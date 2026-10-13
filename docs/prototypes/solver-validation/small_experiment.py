"""THROWAWAY. A scoped solver comparison, not the Bonding scheduler.

One day/line, two models with distinct Recipes, unknown initial Recipe,
CT=0, yields=1, no cross-week flow, no locks and no executed quantities.
Production may be split, but in this scope a repeated Recipe cannot improve
capacity or any objective: removing a return saves nonnegative setup time
and a switch. Baselines contain each positive model once. Enumerating the
two possible orders therefore covers this deliberately small domain.
"""
from fractions import Fraction as F
from itertools import product, permutations
from math import lcm, isfinite
from pathlib import Path
from time import perf_counter
import hashlib
import json
import platform
import random
import resource
import sys
import subprocess
from importlib.metadata import version


def orders(q):
    return permutations(i for i, value in enumerate(q) if value)


def setup(case, order):
    return sum((F(case['setup'][f'{a}{b}']) for a, b in zip(order, order[1:])), F(0))


def witness(case, q, order):
    """Independent rational checker; it never uses solver matrix coefficients."""
    if sorted(order) != [i for i, value in enumerate(q) if value]:
        return None
    if any(not isinstance(x, int) or x < 0 or x > case['demand'][i] for i, x in enumerate(q)):
        return None
    spent = setup(case, order)
    available = F(case['available'])
    if spent > available:
        return None
    if not order:
        return []
    if any(case['capacity'][i] <= 0 for i in order):
        return None
    lower = {i: F(24 * (q[i] - 1), case['capacity'][i]) for i in order}
    slack = available - spent - sum(lower.values(), F(0))
    if slack <= 0:
        return None
    hours = [F(0), F(0)]
    for i in order:
        hours[i] = lower[i] + slack / len(order)
        if case['capacity'][i] * hours[i] <= 24 * (q[i] - 1):
            return None
    if sum(hours) + spent > available:
        return None
    return [str(h) for h in hours]


def vector(case, q, order):
    shortage = sum(case['demand']) - sum(q)
    # Only W1 has demand, so the other 14 coordinates in each group are zero.
    gaps = [shortage] + [0] * 14
    switches = max(0, len(order) - 1)
    change = 3 * sum(abs(q[i] - case['baseline'][i]) for i in range(2))
    common = [i for i in order if case['baseline'][i] > 0]
    reversal = int(len(common) == 2 and common != case['baseline_order'])
    return gaps + gaps + [switches, change, reversal]


def oracle(case):
    candidates = []
    for q in product(*(range(d + 1) for d in case['demand'])):
        for order in orders(q):
            h = witness(case, q, order)
            if h is not None:
                candidates.append({'q': list(q), 'order': list(order), 'hours': h,
                                   'vector': vector(case, q, order)})
    return min(candidates, key=lambda c: c['vector'])


def matrix(case, order):
    # Eliminate continuous H only for this daily subproblem:
    # sum 24(q_i-1)/C_i < available-setup.
    # Both sides are rational with denominator dividing D. Strict < is
    # exactly <= after subtracting ONE INTEGER numerator unit, not epsilon.
    remaining = F(case['available']) - setup(case, order)
    denominator = lcm(remaining.denominator, *(case['capacity'][i] for i in order))
    coeff = {i: 24 * denominator // case['capacity'][i] for i in order}
    rhs = int(remaining * denominator) - 1 + sum(coeff.values())
    return coeff, rhs


def solve_branch(case, order, route):
    coeff, rhs = matrix(case, order)
    if route.startswith('cp-sat'):
        from ortools.sat.python import cp_model
        model = cp_model.CpModel()
        q = [model.new_int_var(1 if i in order else 0, case['demand'][i] if i in order else 0, f'q{i}') for i in range(2)]
        model.add(sum(coeff[i] * q[i] for i in order) <= rhs)
        dev = [model.new_int_var(0, max(case['demand'][i], case['baseline'][i]), f'dev{i}') for i in range(2)]
        for i in range(2):
            model.add_abs_equality(dev[i], q[i] - case['baseline'][i])
        if route.endswith('hint'):
            # Tiny constructive start, not an exhaustive-oracle hint.
            proposed = [int(i in order) for i in range(2)]
            if witness(case, proposed, order) is not None:
                for i in order:
                    while proposed[i] < case['demand'][i]:
                        proposed[i] += 1
                        if witness(case, proposed, order) is None:
                            proposed[i] -= 1
                            break
                for i in range(2):
                    model.add_hint(q[i], proposed[i])
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        solver.parameters.max_time_in_seconds = 2
        model.maximize(sum(q))
        status = solver.solve(model)
        log = [{'stage': 'coverage', 'status': solver.status_name(status),
                'bound': solver.best_objective_bound if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None}]
        if status == cp_model.INFEASIBLE:
            return None, log
        if status != cp_model.OPTIMAL:
            raise RuntimeError(f'small branch did not finish: {log}')
        total = sum(solver.value(x) for x in q)
        model.add(sum(q) == total)
        model.minimize(sum(dev))
        status = solver.solve(model)
        log.append({'stage': 'quantity_change', 'status': solver.status_name(status), 'bound': solver.best_objective_bound})
        if status != cp_model.OPTIMAL:
            raise RuntimeError(f'small second stage did not finish: {log}')
        return [solver.value(x) for x in q], log
    import highspy
    model = highspy.Highs()
    model.setOptionValue('output_flag', False)
    model.setOptionValue('threads', 1)
    model.setOptionValue('random_seed', 0)
    model.setOptionValue('time_limit', 2.0)
    model.setOptionValue('mip_rel_gap', 0.0)
    model.setOptionValue('mip_abs_gap', 0.0)
    q = [model.addVariable(lb=1 if i in order else 0, ub=case['demand'][i] if i in order else 0, type=highspy.HighsVarType.kInteger) for i in range(2)]
    model.addConstr(sum(coeff[i] * q[i] for i in order) <= rhs)
    dev = [model.addVariable(lb=0) for _ in range(2)]
    for i in range(2):
        model.addConstr(dev[i] >= q[i] - case['baseline'][i])
        model.addConstr(dev[i] >= case['baseline'][i] - q[i])
    model.maximize(sum(q))
    status = model.getModelStatus()
    bound = model.getInfo().mip_dual_bound
    log = [{'stage': 'coverage', 'status': str(status), 'bound': bound if isfinite(bound) else None}]
    if status == highspy.HighsModelStatus.kInfeasible:
        return None, log
    if status != highspy.HighsModelStatus.kOptimal:
        raise RuntimeError(f'small MIP branch did not finish: {log}')
    total = round(sum(model.val(x) for x in q))
    model.addConstr(sum(q) == total)
    model.minimize(sum(dev))
    status = model.getModelStatus()
    log.append({'stage': 'quantity_change', 'status': str(status), 'bound': model.getInfo().mip_dual_bound})
    if status != highspy.HighsModelStatus.kOptimal:
        raise RuntimeError(f'small MIP second stage did not finish: {log}')
    # Rounded values are only proposals. The independent checker decides
    # whether they actually satisfy the exact business subset.
    return [round(model.val(x)) for x in q], log


def run_case(case, route):
    start = perf_counter()
    candidates = [{'q': [0, 0], 'order': [], 'hours': [], 'vector': vector(case, [0, 0], [])}]
    branches = []
    for active in ((0,), (1,), (0, 1), (1, 0)):
        if any(case['capacity'][i] <= 0 or case['demand'][i] == 0 for i in active):
            continue
        q, stages = solve_branch(case, active, route)
        branches.append({'order': active, 'stages': stages})
        if q is None:
            continue
        h = witness(case, q, active)
        if h is None:
            raise RuntimeError(f'exact validator rejected {route}: {case}, {q}, {active}')
        candidates.append({'q': q, 'order': list(active), 'hours': h, 'vector': vector(case, q, active)})
    best = min(candidates, key=lambda c: c['vector'])
    return {'route': route, 'candidate': best, 'branches': branches,
            'seconds_model_solve_validate': perf_counter() - start,
            'proof_scope': 'enumerated one-day two-model subset; MIP numeric status is not an exact proof'}


def fixtures():
    common = {'capacity': [1000, 1000], 'demand': [2, 2], 'baseline': [2, 2], 'baseline_order': [0, 1], 'setup': {'01': '0.95', '10': '0.95'}}
    cases = [dict(common, name='continuous_0025', available='1'),
             dict(common, name='strict_equality', available='0.998'),
             dict(common, name='zero_time', available='0'),
             dict(common, name='zero_capacity', available='1', capacity=[0, 1000])]
    rng = random.Random(160013)
    for i in range(100):
        demand = [rng.randrange(1, 5), rng.randrange(1, 5)]
        baseline = [rng.randrange(d + 1) for d in demand]
        order = [j for j, q in enumerate(baseline) if q]
        rng.shuffle(order)
        cases.append({'name': f'random_{i:03}', 'capacity': [rng.choice([24, 48, 100, 1000]) for _ in range(2)],
                      'demand': demand, 'baseline': baseline, 'baseline_order': order,
                      'available': str(F(rng.randrange(0, 401), 100)),
                      'setup': {'01': str(F(rng.randrange(0, 151), 100)), '10': str(F(rng.randrange(0, 151), 100))}})
    return cases


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else 'small-results.json')
    routes = ('cp-sat', 'highs', 'cp-sat-hint')
    if len(sys.argv) < 3:
        # Native libraries bundled with OR-Tools and highspy collided when
        # loaded into one process. Keep one solver route per child process.
        children = []
        for route in routes:
            child_path = output.with_name(output.stem + '-' + route + '.json')
            started = perf_counter()
            process = subprocess.run([sys.executable, __file__, str(child_path), route], capture_output=True, text=True)
            if not child_path.exists():
                raise RuntimeError(f'{route}: {process.stderr}')
            child = json.loads(child_path.read_text())
            child['route_process_seconds'] = perf_counter() - started
            child['returncode'] = process.returncode
            children.append(child)
        output.write_text(json.dumps({'isolated_processes': children}, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
        print(json.dumps([{'route': child['route'], 'runs': len(child['runs']), 'failures': len(child['failures'])} for child in children]))
        return int(any(child['failures'] or child['returncode'] for child in children))
    selected_route = sys.argv[2]
    if selected_route not in routes:
        raise ValueError(selected_route)
    cases = fixtures()
    encoded = json.dumps(cases, sort_keys=True).encode()
    report = {'scope': __doc__, 'route': selected_route, 'seed': 160013, 'input_sha256': hashlib.sha256(encoded).hexdigest(),
              'environment': {'python': sys.version, 'platform': platform.platform(),
                              'ortools': version('ortools'), 'highspy': version('highspy'), 'threads': 1},
              'runs': [], 'failures': []}
    for case in cases:
        expected = oracle(case)
        for route in (selected_route,):
            try:
                result = run_case(case, route)
                result.update(case=case, oracle=expected)
                result['matches_oracle'] = result['candidate']['vector'] == expected['vector']
                report['runs'].append(result)
                if not result['matches_oracle']:
                    report['failures'].append({'case': case['name'], 'route': route, 'error': 'objective mismatch'})
            except Exception as error:
                report['failures'].append({'case': case['name'], 'route': route, 'error': repr(error)})
    report['process_peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    print(json.dumps({'runs': len(report['runs']), 'failures': len(report['failures']), 'path': str(output)}, ensure_ascii=False))
    return int(bool(report['failures']))


if __name__ == '__main__':
    raise SystemExit(main())
