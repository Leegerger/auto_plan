"""THROWAWAY: full-size fixed-quantity order neighborhood, not full scheduling.

All date/line/model quantities and existing production segments are fixed.
Only permutations inside authorized date/line cells vary. Each switch is
charged to its target production day; cross-day redistribution is excluded.
All source outputs and confirmation assignments are consequently invariant.
The full 33-coordinate vector is compared, with only K and R variable.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date
from fractions import Fraction as F
from itertools import permutations
from pathlib import Path
from time import perf_counter
import gzip
import hashlib
import json
import math
import os
import resource
import sys

from validate_reference import check

ROOT = Path(__file__).parent


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def relation_cost(original, changed):
    def relations(seq):
        return {(a['model'], b['model']) for i, a in enumerate(seq) for b in seq[i+1:]
                if a['model'] != b['model']}
    before, after = relations(original), relations(changed)
    return len({tuple(sorted(pair)) for pair in after-before})


def transitions(snapshot, previous, rows):
    events = []
    for row in rows:
        target = snapshot['model_recipe'][str(row['model'])]
        if previous is not None and previous != target:
            events.append({'from': previous, 'to': target,
                           'hours': snapshot['switch_hours'][f'{previous}:{target}']})
        previous = target
    return events


def hours_for(snapshot, cell, rows, events):
    quantities = defaultdict(int)
    for row in rows:
        quantities[snapshot['model_recipe'][str(row['model'])]] += row['quantity']
    lower = {}
    for r, quantity in quantities.items():
        c = snapshot['capacity_by_month'][cell['date'][:7]][f"{cell['line']}:{r}"]
        if c <= 0:
            return None
        lower[r] = F(24*(quantity-1), c)
    slack = F(cell['available_hours'])-sum((F(e['hours']) for e in events), F(0))-sum(lower.values(), F(0))
    if slack <= 0:
        return None
    # A rational witness specific to this candidate, not a fixed epsilon.
    return {str(r): str(h+slack/len(lower)) for r, h in lower.items()}


def load_case(name):
    source = 'tail' if name.startswith('tail') else 'normal'
    snapshot = json.loads(gzip.decompress((ROOT/f'{source}-snapshot.json.gz').read_bytes()))
    original_hash = digest(snapshot)
    if name.startswith('tight'):
        by_cell = defaultdict(list)
        for row in snapshot['plan']:
            by_cell[(row['date'], row['line'])].append(row)
        for cell in snapshot['day_lines']:
            rows = by_cell[(cell['date'], cell['line'])]
            quantity = defaultdict(int)
            for row in rows:
                quantity[snapshot['model_recipe'][str(row['model'])]] += row['quantity']
            lower = sum((F(24*(q-1), snapshot['capacity_by_month'][cell['date'][:7]][f"{cell['line']}:{r}"])
                         for r, q in quantity.items()), F(0))
            setup = sum((F(e['hours']) for e in cell['transitions']), F(0))
            # Integer daily hours, with the smallest integer above the exact
            # reference lower bound. The original order remains feasible.
            cell['available_hours'] = math.floor(lower+setup)+1
            cell['maintenance_hours'] = 24-cell['available_hours']
            cell['production_hours_by_recipe'] = hours_for(snapshot, cell, rows, cell['transitions'])
    local = name.endswith('local')
    start = date.fromisoformat(snapshot['start_date'])
    mutable = {(c['date'], c['line']) for c in snapshot['day_lines']
               if not local or (42 <= (date.fromisoformat(c['date'])-start).days < 49 and c['line'] < 3)}
    snapshot['baseline_plan'] = deepcopy(snapshot['plan'])
    if name.startswith('scrambled'):
        grouped = defaultdict(list)
        for row in snapshot['plan']:
            grouped[(row['date'],row['line'])].append(row)
        for line in snapshot['lines']:
            cells = sorted((c for c in snapshot['day_lines'] if c['line']==line),key=lambda c:c['date'])
            previous = None
            for i,cell in enumerate(cells):
                key = (cell['date'],line)
                # Keep the last mutable day's ending state, so scrambling
                # does not itself alter already scheduled outside switches.
                following_mutable = i+1<len(cells) and (cells[i+1]['date'],line) in mutable
                rows = sorted(grouped[key],key=lambda r:r['position'])
                if key in mutable and (not local or following_mutable):
                    rows.reverse()
                    for position,row in enumerate(rows):
                        row['position'] = position
                events = transitions(snapshot,previous,rows)
                if key in mutable:
                    cell['initial_recipe'] = previous
                    cell['transitions'] = events
                    cell['production_hours_by_recipe'] = hours_for(snapshot,cell,rows,events)
                    if cell['production_hours_by_recipe'] is None:
                        raise ValueError('scrambled fixture is not feasible')
                previous = snapshot['model_recipe'][str(rows[-1]['model'])]
    return snapshot, mutable, original_hash


def make_graph(snapshot, mutable):
    grouped = defaultdict(list)
    baseline = defaultdict(list)
    for row in snapshot['baseline_plan']:
        baseline[(row['date'],row['line'])].append(row)
    for row in snapshot['plan']:
        grouped[(row['date'], row['line'])].append(row)
    graph = []
    for line in snapshot['lines']:
        layers = []
        previous_options = [None]
        for cell in sorted((c for c in snapshot['day_lines'] if c['line']==line), key=lambda c:c['date']):
            key = (cell['date'], line)
            original = sorted(grouped[key], key=lambda r:r['position'])
            adopted_order = sorted(baseline[key],key=lambda r:r['position'])
            options = [list(order) for order in permutations(original)] if key in mutable else [original]
            edges = []
            for previous_index, previous_rows in enumerate(previous_options):
                previous = None if previous_rows is None else snapshot['model_recipe'][str(previous_rows[-1]['model'])]
                for selected, rows in enumerate(options):
                    events = transitions(snapshot, previous, rows)
                    if key not in mutable and (events != cell['transitions'] or previous != cell['initial_recipe']):
                        continue
                    hours = hours_for(snapshot, cell, rows, events)
                    if hours is None:
                        continue
                    edges.append({'from': previous_index, 'to': selected, 'k': len(events),
                                  'r': relation_cost(adopted_order, rows), 'events': events,
                                  'initial': previous, 'hours': hours})
            layers.append({'cell': cell, 'options': options, 'edges': edges, 'mutable': key in mutable})
            previous_options = options
        graph.append(layers)
    return graph


def dynamic_optimum(graph):
    # Exact shortest paths for this finite order neighborhood. It says
    # nothing about better quantities, line assignments or production days.
    selected = []
    total = (0, 0)
    for layers in graph:
        states = {0: ((0, 0), [])}
        for layer in layers:
            next_states = {}
            for edge in layer['edges']:
                if edge['from'] not in states:
                    continue
                cost, path = states[edge['from']]
                value = (cost[0]+edge['k'], cost[1]+edge['r'])
                if edge['to'] not in next_states or value < next_states[edge['to']][0]:
                    next_states[edge['to']] = (value, path+[edge['to']])
            states = next_states
        if not states:
            return None, None
        cost, path = min(states.values(), key=lambda value:value[0])
        total = (total[0]+cost[0], total[1]+cost[1])
        selected.append(path)
    return total, selected


def greedy_start(graph):
    # Backward feasibility only, followed by a myopic forward choice. This
    # does not use the dynamic optimum's values as an experimental hint.
    result = []
    for layers in graph:
        reachable = [set() for _ in range(len(layers)+1)]
        reachable[-1] = set(range(len(layers[-1]['options'])))
        for i in range(len(layers)-1, -1, -1):
            reachable[i] = {e['from'] for e in layers[i]['edges'] if e['to'] in reachable[i+1]}
        previous, choices = 0, []
        for i, layer in enumerate(layers):
            options = [e for e in layer['edges'] if e['from']==previous and e['to'] in reachable[i+1]]
            if not options:
                return None
            edge = min(options, key=lambda e:(e['k'],e['r'],e['to']))
            previous = edge['to']
            choices.append(previous)
        result.append(choices)
    return result


def selected_cost(graph, selected):
    k = r = 0
    for layers, choices in zip(graph, selected):
        previous = 0
        for layer, choice in zip(layers, choices):
            edge = next(e for e in layer['edges'] if e['from']==previous and e['to']==choice)
            k += edge['k']
            r += edge['r']
            previous = choice
    return k, r


def solve_cp(graph, deadline, use_hint):
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()
    hint = greedy_start(graph) if use_hint else None
    choices, costs_k, costs_r = [], [], []
    for li, layers in enumerate(graph):
        line_choices = []
        previous = model.new_constant(0)
        for di, layer in enumerate(layers):
            choice = model.new_int_var(0, len(layer['options'])-1, f'x{li}_{di}')
            k = model.new_int_var(0, 4, f'k{li}_{di}')
            r = model.new_int_var(0, 3, f'r{li}_{di}')
            model.add_allowed_assignments([previous,choice,k,r], [(e['from'],e['to'],e['k'],e['r']) for e in layer['edges']])
            if hint:
                model.add_hint(choice,hint[li][di])
            costs_k.append(k)
            costs_r.append(r)
            line_choices.append(choice)
            previous = choice
        choices.append(line_choices)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    stages, solution = [], None
    for name, objective in [('switches',sum(costs_k)),('order_changes',sum(costs_r))]:
        remaining = deadline-perf_counter()
        if remaining <= 0:
            break
        solver.parameters.max_time_in_seconds = remaining
        model.minimize(objective)
        status = solver.solve(model)
        good = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        stages.append({'stage':name,'status':solver.status_name(status),
                       'value':solver.objective_value if good else None,
                       'bound':solver.best_objective_bound,
                       'completed_seconds':perf_counter()})
        if good:
            solution = [[solver.value(x) for x in line] for line in choices]
        if status != cp_model.OPTIMAL:
            break
        model.add(objective==round(solver.objective_value))
    return solution, stages, {'variables':len(model.proto.variables),'constraints':len(model.proto.constraints)}


def solve_highs(graph, deadline):
    import highspy
    model = highspy.Highs()
    for key,value in [('output_flag',False),('threads',1),('random_seed',0),('mip_rel_gap',0.0),('mip_abs_gap',0.0)]:
        model.setOptionValue(key,value)
    rows = []
    objective_k = objective_r = 0
    for layers in graph:
        previous_incoming = {0: [1]}
        line_rows = []
        for layer in layers:
            incoming = defaultdict(list)
            outgoing = defaultdict(list)
            arcs = []
            for edge in layer['edges']:
                variable = model.addVariable(lb=0,ub=1,type=highspy.HighsVarType.kInteger)
                arcs.append((edge,variable))
                outgoing[edge['from']].append(variable)
                incoming[edge['to']].append(variable)
                objective_k += edge['k']*variable
                objective_r += edge['r']*variable
            for vertex in set(previous_incoming) | set(outgoing):
                model.addConstr(sum(previous_incoming.get(vertex,[]))==sum(outgoing.get(vertex,[])))
            previous_incoming = incoming
            line_rows.append(arcs)
        model.addConstr(sum(x for values in previous_incoming.values() for x in values)==1)
        rows.append(line_rows)
    stages, selected = [], None
    for name, objective in [('switches',objective_k),('order_changes',objective_r)]:
        remaining = deadline-perf_counter()
        if remaining <= 0:
            break
        model.setOptionValue('time_limit',remaining)
        model.minimize(objective)
        status = model.getModelStatus()
        info = model.getInfo()
        valid = model.getSolution().value_valid
        bound = info.mip_dual_bound
        stages.append({'stage':name,'status':str(status),'value':model.getObjectiveValue() if valid else None,
                       'bound':bound if math.isfinite(bound) else None,'completed_seconds':perf_counter()})
        if valid:
            selected = [[next(edge['to'] for edge,var in arcs if model.val(var)>0.5) for arcs in line] for line in rows]
        if status != highspy.HighsModelStatus.kOptimal:
            break
        model.addConstr(objective==round(model.getObjectiveValue()))
    return selected, stages, {'variables':model.getNumCol(),'constraints':model.getNumRow()}


def materialize(snapshot, graph, selected):
    candidate = deepcopy(snapshot)
    candidate['plan'] = []
    candidate['day_lines'] = []
    for layers, choices in zip(graph, selected):
        previous = 0
        for layer, choice in zip(layers, choices):
            edge = next(e for e in layer['edges'] if e['from']==previous and e['to']==choice)
            cell = deepcopy(layer['cell'])
            if layer['mutable']:
                cell['initial_recipe'] = edge['initial']
                cell['transitions'] = edge['events']
                cell['production_hours_by_recipe'] = edge['hours']
            candidate['day_lines'].append(cell)
            for position,row in enumerate(layer['options'][choice]):
                candidate['plan'].append(dict(row,position=position))
            previous = choice
    candidate['day_lines'].sort(key=lambda c:(c['date'],c['line']))
    candidate['plan'].sort(key=lambda r:(r['date'],r['line'],r['position']))
    return candidate


def validate_candidate(snapshot, candidate, mutable):
    # Keep business inputs authoritative. This experiment may only replace
    # the two candidate fields below, never yields/demand/capacity/confirmations.
    for key in snapshot:
        if key not in ('plan','day_lines') and candidate[key] != snapshot[key]:
            raise ValueError('candidate changed immutable input '+key)
    def by_cell(plan):
        grouped = defaultdict(list)
        for row in plan:
            grouped[(row['date'],row['line'])].append(row)
        return {key:sorted(rows,key=lambda r:r['position']) for key,rows in grouped.items()}
    old, new = by_cell(snapshot['plan']), by_cell(candidate['plan'])
    adopted = by_cell(snapshot['baseline_plan'])
    if set(old) != set(new):
        raise ValueError('changed date/line membership')
    r = 0
    for key in old:
        if sorted((x['model'],x['quantity']) for x in old[key]) != sorted((x['model'],x['quantity']) for x in new[key]):
            raise ValueError('changed a protected quantity/segment in order neighborhood')
        if key not in mutable and old[key] != new[key]:
            raise ValueError('changed production outside authorized range')
        # Separate pair computation from the model's relation-set helper.
        models = sorted({x['model'] for x in old[key]})
        for i,a in enumerate(models):
            for b in models[i+1:]:
                positions = lambda rows,m:[j for j,x in enumerate(rows) if x['model']==m]
                ob,oa = positions(adopted[key],b),positions(adopted[key],a)
                nb,na = positions(new[key],b),positions(new[key],a)
                introduced = (max(oa)<min(ob) and min(nb)<max(na)) or (max(ob)<min(oa) and min(na)<max(nb))
                r += int(introduced)
    old_cells = {(c['date'],c['line']):c for c in snapshot['day_lines']}
    for cell in candidate['day_lines']:
        key = (cell['date'],cell['line'])
        if key not in mutable and cell != old_cells[key]:
            raise ValueError('changed switch work/time outside authorized range')
        for field in ('available_hours','maintenance_hours'):
            if cell[field] != old_cells[key][field]:
                raise ValueError('candidate changed calendar capacity')
    result = check(candidate)
    gaps = [w['demand']-w['on_time'] for w in result['weeks']]
    final = [w['final_shortage'] for w in result['weeks']]
    result['objective_vector'] = gaps+final+[result['switches'],0,r]
    return result


def atomic_write(path, data):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def run(name, route, output, repeat=5):
    all_runs = []
    for repetition in range(repeat):
        began = perf_counter()
        deadline = began+(30 if name.endswith('local') else 120)
        snapshot, mutable, source_hash = load_case(name)
        input_hash = digest({'snapshot':snapshot,'mutable':sorted(mutable)})
        reference = validate_candidate(snapshot,snapshot,mutable)
        first_valid = perf_counter()-began
        best = snapshot
        best_result = reference
        atomic_write(output.with_suffix('.candidate.json'), {'input_sha256':input_hash,'source':'reference',
                     'validation':reference,'candidate':best})
        graph = make_graph(snapshot,mutable)
        built = perf_counter()-began
        if route.startswith('cp-sat'):
            selected, stages, size = solve_cp(graph,deadline,route.endswith('hint'))
        elif route == 'highs':
            selected, stages, size = solve_highs(graph,deadline)
        else:
            raise ValueError(route)
        returned = perf_counter()-began
        candidate_returned = selected is not None
        if selected is not None:
            proposal = materialize(snapshot,graph,selected)
            validation = validate_candidate(snapshot,proposal,mutable)
            if validation['objective_vector'] <= best_result['objective_vector']:
                best, best_result = proposal, validation
                atomic_write(output.with_suffix('.candidate.json'), {'input_sha256':input_hash,'source':route,
                             'validation':validation,'candidate':best})
        finished = perf_counter()-began
        # Oracle is outside the route's measured pipeline, never used as a
        # hidden optimal solution hint by either solver.
        oracle_cost, _ = dynamic_optimum(graph)
        actual = (best_result['switches'],best_result['objective_vector'][-1])
        for stage in stages:
            stage['completed_seconds'] -= began
        all_runs.append({'case':name,'route':route,'repetition':repetition,'process_first_run':repetition==0,
                         'synthetic_as_of':'2026-10-12T08:00:00+08:00',
                         'source_snapshot_sha256':source_hash,'input_sha256':input_hash,
                         'candidate_sha256':digest(best),'initial_draft_vector':reference['objective_vector'],
                         'candidate_vector':best_result['objective_vector'],'matches_restricted_oracle':actual==oracle_cost,
                         'restricted_oracle':oracle_cost,'solver_returned_candidate':candidate_returned,
                         'seconds':{'first_valid_reference':first_valid,'graph_built':built,
                                    'solver_returned':returned,'validated_candidate_saved':finished},
                         'stages':stages,'model':size,'mutable_cells':len(mutable),
                         'route_peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                         'scope':__doc__,'end_to_end_acceptance':False,
                         'within_compute_target':finished <= (30 if name.endswith('local') else 120)})
        atomic_write(output,{'runs':all_runs})
        print(json.dumps({'case':name,'route':route,'repeat':repetition,'before_k':reference['switches'],
                          'after_k':actual[0],'r':actual[1],'seconds':round(finished,3),'oracle_match':actual==oracle_cost}),flush=True)
    return all_runs


if __name__ == '__main__':
    case, route, output = sys.argv[1:4]
    run(case,route,Path(output),int(sys.argv[4]) if len(sys.argv)>4 else 5)
