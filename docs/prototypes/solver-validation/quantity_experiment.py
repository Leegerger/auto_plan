"""Throwaway quantity-repair neighborhood, preserving existing segment order.

This case has ample confirmations, no WIP/executed/locked quantities, and
all output inside the window. Only existing positive segment quantities
vary. It does not encode arbitrary confirmation ratios inside the solver.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date
from fractions import Fraction as F
from math import lcm, ceil
from pathlib import Path
from time import perf_counter
import gzip
import json
import math
import resource
import sys
from threading import Event, Thread

from generate_snapshot import outputs
from order_experiment import digest, hours_for, atomic_write
from validate_reference import check
from surplus_audit import trim

ROOT = Path(__file__).parent


def output_amounts(snapshot,source_totals):
    slots = []
    for (day,m),quantity in sorted(source_totals.items()):
        factor = F(snapshot['line_yield_by_month'][day[:7]])*F(snapshot['model_yield_by_month'][day[:7]][str(m)])
        for slot in outputs(date.fromisoformat(day),quantity,factor,F(snapshot['ct'][str(m)])):
            slots.append({'input_date':day,'model':m,**slot})
    return slots


def make_case(local):
    snapshot = json.loads(gzip.decompress((ROOT/'normal-snapshot.json.gz').read_bytes()))
    baseline = deepcopy(snapshot['plan'])
    snapshot['baseline_plan'] = baseline
    target = deepcopy(snapshot)
    start = date.fromisoformat(snapshot['start_date'])
    mutable = {i for i,row in enumerate(snapshot['plan']) if not local or (
        42 <= (date.fromisoformat(row['date'])-start).days < 49 and row['line']<3)}
    for i,row in enumerate(target['plan']):
        if i in mutable:
            row['quantity'] = ceil(F(105,100)*row['quantity'])
    totals = defaultdict(int)
    for row in target['plan']:
        totals[(row['date'],row['model'])] += row['quantity']
    requirements = defaultdict(int)
    slots = output_amounts(target,totals)
    for slot in slots:
        week = (date.fromisoformat(slot['date'])-start).days//7
        if week >= 15:
            raise ValueError('normal growth case unexpectedly produces outside window')
        requirements[(slot['model'],week)] += slot['quantity']
    snapshot['demands'] = [{'model':m,'week':w,'quantity':q} for (m,w),q in sorted(requirements.items())]
    # Explicit generous synthetic authorizations isolate quantity/rounding
    # from the separate shortage/ban boundary cases. These are not real approvals.
    snapshot['confirmation_batches'] = [
        {'id':f'{kind}-{m}-{w}','type':kind,'model':m,'demand_week':w,
         'quantity':10_000_000,'status':'allowed','date':snapshot['start_date'] if kind=='material' else '2027-02-28'}
        for m,w in requirements for kind in ('material','shipping')]
    target['demands'] = deepcopy(snapshot['demands'])
    target['confirmation_batches'] = deepcopy(snapshot['confirmation_batches'])
    target = materialize(target,[row['quantity'] for row in target['plan']],mutable)
    return snapshot,target,mutable


def materialize(snapshot, quantities, mutable):
    result = deepcopy(snapshot)
    groups = defaultdict(list)
    for i,row in enumerate(result['plan']):
        row['quantity'] = quantities[i]
        groups[(row['date'],row['line'])].append(row)
    touched = {(snapshot['plan'][i]['date'],snapshot['plan'][i]['line']) for i in mutable}
    for cell in result['day_lines']:
        if (cell['date'],cell['line']) in touched:
            rows = groups[(cell['date'],cell['line'])]
            cell['production_hours_by_recipe'] = hours_for(result,cell,rows,cell['transitions'])
            if cell['production_hours_by_recipe'] is None:
                raise ValueError('exact daily capacity rejected quantity proposal')
    return result


def quantities(snapshot):
    return [row['quantity'] for row in snapshot['plan']]


def evaluate(snapshot, candidate, mutable):
    for field in snapshot:
        if field not in ('plan','day_lines') and candidate[field]!=snapshot[field]:
            raise ValueError('changed immutable input '+field)
    if len(snapshot['plan'])!=len(candidate['plan']):
        raise ValueError('changed fixed segment count')
    baseline = defaultdict(int)
    actual = defaultdict(int)
    start = date.fromisoformat(snapshot['start_date'])
    for i,(old,new) in enumerate(zip(snapshot['plan'],candidate['plan'])):
        for key in ('date','line','model','position'):
            if old[key]!=new[key]:
                raise ValueError('quantity neighborhood changed position or identity')
        if i not in mutable and old!=new:
            raise ValueError('quantity outside range changed')
        key = (new['date'],new['line'],new['model'])
        actual[key] += new['quantity']
    for row in snapshot['baseline_plan']:
        baseline[(row['date'],row['line'],row['model'])] += row['quantity']
    change = sum(abs(q-baseline.get(key,0))*(3 if (date.fromisoformat(key[0])-start).days<28 else 1)
                 for key,q in actual.items())
    touched = {(snapshot['plan'][i]['date'],snapshot['plan'][i]['line']) for i in mutable}
    for old,new in zip(snapshot['day_lines'],candidate['day_lines']):
        if (old['date'],old['line']) not in touched and old!=new:
            raise ValueError('outside switch or time changed')
        for key in ('date','line','available_hours','maintenance_hours','initial_recipe','transitions'):
            if old[key]!=new[key]:
                raise ValueError('quantity neighborhood changed fixed calendar/switches')
    result = check(candidate)
    result['vector'] = [w['demand']-w['on_time'] for w in result['weeks']]+[w['final_shortage'] for w in result['weeks']]+[result['switches'],change,0]
    return result


def model_data(snapshot,mutable):
    cells = {(c['date'],c['line']):c for c in snapshot['day_lines']}
    groups = defaultdict(lambda:defaultdict(list))
    source_indices = defaultdict(list)
    upper = []
    for i,row in enumerate(snapshot['plan']):
        key = (row['date'],row['line'])
        r = snapshot['model_recipe'][str(row['model'])]
        groups[key][r].append(i)
        source_indices[(row['date'],row['model'])].append(i)
        c = snapshot['capacity_by_month'][row['date'][:7]][f"{row['line']}:{r}"]
        upper.append(ceil(F(c)*cells[key]['available_hours']/24))
    capacities = []
    for key,recipes in groups.items():
        cell = cells[key]
        remaining = F(cell['available_hours'])-sum((F(e['hours']) for e in cell['transitions']),F(0))
        cs = {r:snapshot['capacity_by_month'][key[0][:7]][f'{key[1]}:{r}'] for r in recipes}
        denominator = lcm(remaining.denominator,*cs.values())
        coefficient = {i:24*denominator//cs[r] for r,indices in recipes.items() for i in indices}
        bound = int(remaining*denominator)-1+sum(24*denominator//c for c in cs.values())
        capacities.append((coefficient,bound))
    slot_data = []
    for (day,m),indices in sorted(source_indices.items()):
        ct = F(snapshot['ct'][str(m)])
        frac = ct%1
        factor = F(snapshot['line_yield_by_month'][day[:7]])*F(snapshot['model_yield_by_month'][day[:7]][str(m)])
        predicted = outputs(date.fromisoformat(day),1,F(1),ct)
        parts = [1-frac]+([frac] if frac else [])
        for slot,part in zip(predicted,parts):
            ratio = factor*part
            slot_data.append({'source':(day,m),'date':slot['date'],'indices':indices,
                              'numerator':ratio.numerator,'denominator':ratio.denominator,
                              'upper':ceil(ratio*sum(upper[i] for i in indices))})
    needs = {(d['model'],d['week']):d['quantity'] for d in snapshot['demands']}
    deadlines = []
    start = date.fromisoformat(snapshot['start_date'])
    for m in snapshot['models']:
        required = 0
        for w in range(15):
            required += needs.get((m,w),0)
            slots = [j for j,s in enumerate(slot_data) if s['source'][1]==m and (date.fromisoformat(s['date'])-start).days//7<=w]
            deadlines.append((slots,required))
    weights = [3 if (date.fromisoformat(row['date'])-start).days<28 else 1 for row in snapshot['plan']]
    return upper,capacities,slot_data,deadlines,weights


def solve(snapshot,mutable,route,deadline,publish,phase,stop_path):
    upper,capacities,slots,deadlines,weights = model_data(snapshot,mutable)
    base = quantities(snapshot)
    hint = None
    if route.endswith('hint'):
        # Generic proportional repair using only current quantities and new
        # requirements, not the independently generated reference solution.
        for percent in (101,102,104,108,116,132):
            proposal = [ceil(F(percent,100)*q) if i in mutable else q for i,q in enumerate(base)]
            try:
                possible = materialize(snapshot,proposal,mutable)
                result = evaluate(snapshot,possible,mutable)
            except (ValueError,AssertionError):
                continue
            if not any(result['vector'][:30]):
                hint,_ = trim(snapshot,possible,mutable)
                publish(hint,'constructor')
                break
    if route.startswith('cp-sat'):
        from ortools.sat.python import cp_model
        model = cp_model.CpModel()
        q = [model.new_int_var(1 if i in mutable else value,upper[i] if i in mutable else value,f'q{i}') for i,value in enumerate(base)]
        for coefficients,bound in capacities:
            model.add(sum(a*q[i] for i,a in coefficients.items())<=bound)
        y = []
        for j,slot in enumerate(slots):
            variable = model.new_int_var(0,slot['upper'],f'y{j}')
            n,b = slot['numerator'],slot['denominator']
            numerator = n*sum(q[i] for i in slot['indices'])
            model.add(b*variable-b+1<=numerator)
            model.add(numerator<=b*variable)
            y.append(variable)
        for indices,required in deadlines:
            model.add(sum(y[j] for j in indices)>=required)
        dev = []
        for i,value in enumerate(base):
            d = model.new_int_var(0,max(upper[i],value),f'd{i}')
            model.add_abs_equality(d,q[i]-value)
            dev.append(d)
            if hint:
                model.add_hint(q[i],hint[i])
        model.minimize(sum(w*d for w,d in zip(weights,dev)))
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers=1
        solver.parameters.random_seed=0
        solver.parameters.max_time_in_seconds=max(0.001,deadline-perf_counter())
        class FirstCandidate(cp_model.CpSolverSolutionCallback):
            saved=False
            def on_solution_callback(self):
                if not self.saved:
                    publish([self.value(x) for x in q],'solver_first')
                    self.saved=True
                if stop_path.exists():
                    self.stop_search()
        finished=Event()
        def watch_stop():
            while not finished.wait(.05):
                if stop_path.exists():
                    solver.stop_search()
        watcher=Thread(target=watch_stop,daemon=True)
        watcher.start()
        phase('solving')
        try:
            status=solver.solve(model,FirstCandidate())
        finally:
            finished.set()
            watcher.join()
        valid=status in (cp_model.FEASIBLE,cp_model.OPTIMAL)
        result = [solver.value(x) for x in q] if valid else hint
        return result,{
            'status':solver.status_name(status),'value':solver.objective_value if valid else None,
            'bound':solver.best_objective_bound,'variables':len(model.proto.variables),'constraints':len(model.proto.constraints),
            'constructive_candidate_available':hint is not None,'candidate_from_constructor_only':not valid and hint is not None}
    import highspy
    model=highspy.Highs()
    for key,value in [('output_flag',False),('threads',1),('random_seed',0),('mip_rel_gap',0.0),('mip_abs_gap',0.0)]:
        model.setOptionValue(key,value)
    q=[model.addVariable(lb=1 if i in mutable else value,ub=upper[i] if i in mutable else value,type=highspy.HighsVarType.kInteger) for i,value in enumerate(base)]
    for coefficients,bound in capacities:
        model.addConstr(sum(a*q[i] for i,a in coefficients.items())<=bound)
    y=[]
    for slot in slots:
        variable=model.addVariable(lb=0,ub=slot['upper'],type=highspy.HighsVarType.kInteger)
        n,b=slot['numerator'],slot['denominator']
        numerator=n*sum(q[i] for i in slot['indices'])
        model.addConstr(b*variable-b+1<=numerator)
        model.addConstr(numerator<=b*variable)
        y.append(variable)
    for indices,required in deadlines:
        if indices:
            model.addConstr(sum(y[j] for j in indices)>=required)
        elif required:
            raise ValueError('positive demand without output slots in restricted model')
    deviations=[]
    for i,value in enumerate(base):
        d=model.addVariable(lb=0)
        model.addConstr(d>=q[i]-value)
        model.addConstr(d>=value-q[i])
        deviations.append(d)
    model.setOptionValue('time_limit',max(0.001,deadline-perf_counter()))
    saved=False
    def incumbent(event):
        nonlocal saved
        if not saved:
            publish([round(event.val(x)) for x in q],'solver_first')
            saved=True
        if stop_path.exists():
            event.interrupt()
    model.cbMipSolution.subscribe(incumbent)
    def interrupt(event):
        if stop_path.exists():
            event.interrupt()
    model.cbMipInterrupt.subscribe(interrupt)
    phase('solving')
    model.minimize(sum(w*d for w,d in zip(weights,deviations)))
    status=model.getModelStatus()
    valid=model.getSolution().value_valid and model.getInfo().primal_solution_status==2
    info=model.getInfo()
    return ([round(model.val(x)) for x in q] if valid else None),{
        'status':str(status),'value':model.getObjectiveValue() if valid else None,
        'bound':info.mip_dual_bound if math.isfinite(info.mip_dual_bound) else None,
        'variables':model.getNumCol(),'constraints':model.getNumRow()}


def run(local,route,path,budget):
    began=perf_counter()
    snapshot,reference,mutable=make_case(local)
    reference_result=evaluate(snapshot,reference,mutable if local else set(range(len(snapshot['plan']))))
    initial=evaluate(snapshot,snapshot,mutable)
    input_hash=digest({'snapshot':snapshot,'mutable':sorted(mutable)})
    saved={}
    errors=[]
    def phase(value):
        atomic_write(path.with_suffix('.phase.json'),{'phase':value,'seconds':perf_counter()-began})
    def publish(q,source):
        try:
            candidate=materialize(snapshot,q,mutable)
            validation=evaluate(snapshot,candidate,mutable)
            if any(validation['vector'][:30]):
                raise ValueError('solver zero-shortage claim failed exact output/confirmation check')
            cleaned,surplus=trim(snapshot,candidate,mutable)
            candidate=materialize(snapshot,cleaned,mutable)
            validation=evaluate(snapshot,candidate,mutable)
            if any(validation['vector'][:30]):
                raise ValueError('deletion audit lost demand coverage')
            if saved and validation['vector']>=saved['validation']['vector']:
                return
            first=saved.get('first_validated_seconds',perf_counter()-began)
            atomic_write(path.with_suffix('.candidate.json'),{
                'input':snapshot,'mutable':sorted(mutable),'candidate':candidate,
                'input_sha256':input_hash,'vector':validation['vector'],'source':source,
                'validated_seconds':perf_counter()-began,'first_validated_seconds':first,
                'certification_scope':'restricted quantity fixture; not full business certificate'})
            saved.update(candidate=candidate,validation=validation,surplus=surplus,first_validated_seconds=first)
        except (ValueError,AssertionError) as failure:
            errors.append({'source':source,'error':repr(failure)})
    # Reserve two seconds for exact final validation and result publication.
    q,model=solve(snapshot,mutable,route,began+budget-2,publish,phase,path.with_suffix('.stop'))
    if q is not None:
        publish(q,'solver_final')
    validation=saved.get('validation')
    surplus=saved.get('surplus')
    error=errors or None
    report={'route':route,'case':'growth-local' if local else 'growth-full','budget_seconds':budget,
            'input_sha256':input_hash,'initial_vector':initial['vector'],'reference_vector':reference_result['vector'],
            'candidate_vector':validation['vector'] if validation else None,'exact_check_error':error,
            'model':model,'seconds':perf_counter()-began,'scope':__doc__,
            'first_validated_seconds':saved.get('first_validated_seconds'),
            'stop_requested':path.with_suffix('.stop').exists(),
            'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'surplus_audit':surplus,
            'surplus_policy_check':'deletion-only within positive fixed segments; not a full business certificate',
            'reference_quality_met':validation['vector']<=reference_result['vector'] if validation and not error else False,
            'zero_shortage_feasibility_scope':'fixed orders and active segments; infeasible is not business infeasibility',
            'end_to_end_acceptance':False}
    atomic_write(path,report)
    phase('finished')
    print(json.dumps({key:report[key] for key in ['route','case','model','seconds','exact_check_error','candidate_vector']}),flush=True)


if __name__=='__main__':
    case,route,path=sys.argv[1:4]
    run(case=='local',route,Path(path),float(sys.argv[4]) if len(sys.argv)>4 else (30 if case=='local' else 120))
