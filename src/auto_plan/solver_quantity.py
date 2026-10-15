"""Scalable fixed-switch integer quantities; independent exact acceptance follows.

Cumulative output constraints guide a bounded neighborhood. General FIFO,
confirmation quotas, stability and Recipe/event validity remain authoritative
in the full evaluator. Solver bounds refer only to this stated relaxation.
"""
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction
from .solver_process import optimize
from .solver_witness import fixed_switch_certificate


def repair(search, plan, recipe, route, seconds):
    variables, constraints, objectives = [], [], [dict() for _ in range(33)]
    rows, indices = [], {}
    day_groups = defaultdict(list)
    for row in plan.segments:
        if search.mutable(row) and row.date > search.today and not search.executed.get(row.id) and search.index.models[row.model_code].recipe_code == recipe:
            capacity = search.index.capacity(row.date.strftime('%Y-%m'), recipe, row.line_code)
            if capacity['state'] != 'known':
                continue
            index = len(variables)
            variables.append({'lower': 0, 'upper': capacity['pieces']})
            rows.append(row)
            indices[row.id] = index
            day_groups[row.date, row.line_code].append(row)
    if not rows:
        return None, {'status': 'no_free_quantities'}

    def constraint(coefficients, lower=None, upper=None):
        constraints.append({'coefficients': {str(k): str(v) for k, v in coefficients.items()},
            'lower': str(lower) if lower is not None else None, 'upper': str(upper) if upper is not None else None})

    def variable(upper):
        identity = len(variables)
        variables.append({'lower': 0, 'upper': max(0, upper)})
        return identity

    switch_hours = defaultdict(Fraction)
    for switch in plan.switches:
        for allocation in switch.allocations:
            switch_hours[allocation.date, switch.line_code] += Fraction(allocation.hours)
    for (day, line), daily in day_groups.items():
        fixed = [r for r in plan.segments if r.date == day and r.line_code == line and r.id not in indices]
        other_hours = sum((Fraction(r.hours) for r in fixed if search.index.models[r.model_code].recipe_code != recipe), Fraction(0))
        fixed_quantity = sum(r.quantity for r in fixed if search.index.models[r.model_code].recipe_code == recipe)
        budget = Fraction(search.index.day(day, line)['available_hours']) - other_hours - switch_hours[day, line]
        capacity = search.index.capacity(day.strftime('%Y-%m'), recipe, line)['pieces']
        exact = capacity * budget / 24
        maximum = -(-exact.numerator // exact.denominator) if budget > 0 else 0
        constraint({indices[r.id]: 1 for r in daily}, upper=maximum - fixed_quantity)

    source_rows = defaultdict(list)
    for row in rows:
        source_rows[row.date, row.model_code].append(row)
    outputs = defaultdict(list)
    for (day, model), daily in source_rows.items():
        parameters = search.index.critical_parameters(day.strftime('%Y-%m'), model)
        if any(value['state'] == 'missing' for value in parameters.values()):
            continue
        yield_rate = Fraction(parameters['line_yield']['percent']) * Fraction(parameters['model_yield']['percent']) / 10000
        ct = Fraction(parameters['cycle_time']['days'])
        integer, tail = ct.numerator // ct.denominator, ct % 1
        for offset, share in ((integer, 1-tail), (integer+1, tail)):
            if not share:
                continue
            output_day, left = day, offset
            while left:
                output_day += timedelta(days=1)
                if not search.index.holidays.get(output_day, False):
                    left -= 1
            while search.index.holidays.get(output_day, False):
                output_day += timedelta(days=1)
            ratio = yield_rate * share
            # Other protected lines for the same model/input day participate in
            # the same rounded source, with no per-line rounding duplication.
            fixed = sum(r.quantity for r in plan.segments if r.date == day and r.model_code == model and r.id not in indices)
            maximum = sum(variables[indices[r.id]]['upper'] for r in daily) + fixed
            bound = -(-(maximum * ratio).numerator // (maximum * ratio).denominator)
            output = variable(bound)
            coefficients = {indices[r.id]: -ratio.numerator for r in daily}
            coefficients[output] = ratio.denominator
            constraint(coefficients, lower=fixed * ratio.numerator, upper=fixed * ratio.numerator + ratio.denominator - 1)
            outputs[model].append((day, output_day, output))
    demands = defaultdict(list)
    for demand in search.snapshot.inputs.business.demands:
        if search.snapshot.time.window_start <= demand.week <= search.snapshot.time.window_end:
            demands[demand.model_code].append(demand)
    for model in sorted(outputs):
        total = 0
        for demand in sorted(demands[model], key=lambda r: r.week):
            total += demand.quantity
            week = (demand.week - search.snapshot.time.window_start).days // 7
            for final in (False, True):
                gap = variable(total)
                coefficients = {gap: 1}
                for input_day, output_day, column in outputs[model]:
                    # These are generous cumulative constraints. The general
                    # confirmation ledger filters the resulting whole plan.
                    if final or output_day < demand.week + timedelta(days=7):
                        coefficients[column] = 1
                constraint(coefficients, lower=total)
                objectives[15 * final + week][str(gap)] = '1'
    # A tie-break guides quantity removal; it cannot become a 34th published
    # objective or override adopted-baseline comparison in acceptance.
    for row in rows:
        difference = variable(variables[indices[row.id]]['upper'] + row.quantity)
        constraint({difference: 1, indices[row.id]: -1}, lower=-row.quantity)
        constraint({difference: 1, indices[row.id]: 1}, lower=row.quantity)
        objectives[31][str(difference)] = '1'
    native = optimize(route, {'variables': variables, 'constraints': constraints,
        'objectives': objectives}, seconds, search.request.native_threads, search.should_stop)
    values = native.get('values')
    if not isinstance(values, list) or len(values) != len(variables) or any(not isinstance(v, int) or not row['lower'] <= v <= row['upper'] for v, row in zip(values, variables)):
        return None, native
    for constraint_row in constraints:
        value = sum(Fraction(coefficient) * values[int(index)] for index, coefficient in constraint_row['coefficients'].items())
        if constraint_row['lower'] is not None and value < Fraction(constraint_row['lower']) or constraint_row['upper'] is not None and value > Fraction(constraint_row['upper']):
            return None, native | {'status': 'exact_native_constraint_rejected'}
    changed = {row.id: values[indices[row.id]] for row in rows}
    trial = plan.model_copy(update={'segments': [row.model_copy(update={'quantity': changed[row.id]}) if row.id in changed else row
        for row in plan.segments if row.id not in changed or changed[row.id]]})
    return fixed_switch_certificate(trial, search.context, search.scope, recipe_code=recipe), native
