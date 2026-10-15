"""Scalable fixed-switch integer quantities; independent exact acceptance follows.

Integer FIFO, confirmation quotas, input shares and dated stability constrain
the fixed-position neighborhood. Exact oracle cuts handle Recipe/event and
eligibility constraints; full validation still owns candidate acceptance.
"""
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction
from math import lcm
import time
from .solver_process import optimize
from .solver_witness import fixed_switch_certificate


def repair(search, plan, recipe, route, seconds):
    variables, constraints = [], []
    rows, indices = [], {}
    day_groups = defaultdict(list)
    for row in plan.segments:
        row_recipe = search.index.models[row.model_code].recipe_code
        if search.mutable(row) and row.date > search.today and not search.executed.get(row.id) and (recipe is None or row_recipe == recipe) and (search.request.mode != 'new_week' or row.id not in search.original_ids):
            capacity = search.index.capacity(row.date.strftime('%Y-%m'), row_recipe, row.line_code)
            if capacity['state'] != 'known':
                continue
            index = len(variables)
            variables.append({'lower': 0, 'upper': capacity['pieces']})
            rows.append(row)
            indices[row.id] = index
            day_groups[row.date, row.line_code, row_recipe].append(row)
    if not rows:
        return None, {'status': 'no_free_quantities'}

    def constraint(coefficients, lower=None, upper=None):
        if len(constraints) >= 500000:
            raise ValueError('Native neighborhood constraint budget exceeded')
        if len(constraints) % 256 == 0:
            search.check()
        constraints.append({'coefficients': {str(k): str(v) for k, v in coefficients.items()},
            'lower': str(lower) if lower is not None else None, 'upper': str(upper) if upper is not None else None})

    def variable(upper):
        if len(variables) >= 100000:
            raise ValueError('Native neighborhood variable budget exceeded')
        if len(variables) % 128 == 0:
            search.check()
        identity = len(variables)
        variables.append({'lower': 0, 'upper': max(0, upper)})
        return identity

    switch_hours = defaultdict(Fraction)
    for switch in plan.switches:
        for allocation in switch.allocations:
            switch_hours[allocation.date, switch.line_code] += Fraction(allocation.hours)
    idle = {(r.date, r.line_code): Fraction(r.hours) for r in plan.ordinary_idle}
    occupied = lambda day, line: max(switch_hours[day, line], idle.get((day, line), Fraction(0)))
    day_costs = defaultdict(list)
    for (day, line, group_recipe), daily in day_groups.items():
        fixed = [r for r in plan.segments if r.date == day and r.line_code == line and r.id not in indices]
        other_hours = sum((Fraction(r.hours) for r in fixed if search.index.models[r.model_code].recipe_code != group_recipe), Fraction(0))
        fixed_quantity = sum(r.quantity for r in fixed if search.index.models[r.model_code].recipe_code == group_recipe)
        budget = Fraction(search.index.day(day, line)['available_hours']) - other_hours - occupied(day, line)
        capacity = search.index.capacity(day.strftime('%Y-%m'), group_recipe, line)['pieces']
        exact = capacity * budget / 24
        maximum = -(-exact.numerator // exact.denominator) if budget > 0 else 0
        if recipe is not None:
            constraint({indices[r.id]: 1 for r in daily}, upper=maximum - fixed_quantity)
        else:
            # A local monolithic model repairs all free Recipe quantities. The
            # integer costs represent exact rational lower bounds, not an hour
            # grid. The final witness selects arbitrary rational positive slack.
            scale = lcm(*(search.index.capacity(day.strftime('%Y-%m'), code, line)['pieces']
                for d, production_line, code in day_groups if d == day and production_line == line),
                *(Fraction(r.hours).denominator for r in fixed), occupied(day, line).denominator)
            if scale >= 2**45:
                return None, {'status': 'native_exact_coefficient_range_exceeded'}
            active = variable(1)
            cost = variable(scale * 24)
            coefficients = {indices[r.id]: 1 for r in daily}
            constraint(coefficients | {active: -1}, lower=-fixed_quantity)
            constraint(coefficients | {active: -capacity}, upper=-fixed_quantity)
            # fixed_quantity forces activation through the second inequality.
            unit = Fraction(24 * scale, capacity)
            constraint({cost: 1, **{indices[r.id]: -unit for r in daily}, active: unit}, lower=unit * fixed_quantity)
            fixed_hours = sum((Fraction(r.hours) for r in fixed if search.index.models[r.model_code].recipe_code == group_recipe), Fraction(0))
            constraint({cost: 1}, lower=fixed_hours * scale)
            raw_need, strict_need = variable(1), variable(1)
            bound = scale * 48 + fixed_quantity * unit + fixed_hours * scale + 1
            difference = {indices[r.id]: unit for r in daily} | {active: -unit, raw_need: -bound}
            offset = fixed_quantity * unit - fixed_hours * scale
            constraint(difference, lower=-bound-offset, upper=-1-offset)
            constraint({strict_need: 1, raw_need: -1}, upper=0)
            constraint({strict_need: 1, active: -1}, upper=0)
            constraint({strict_need: 1, raw_need: -1, active: -1}, lower=-1)
            day_costs[day, line].append((cost, scale, strict_need))
    for (day, line), costs in day_costs.items():
        scale = costs[0][1]
        variable_recipes = {code for d, production_line, code in day_groups if d == day and production_line == line}
        fixed_hours = sum((Fraction(r.hours) for r in plan.segments if r.date == day and r.line_code == line
            and search.index.models[r.model_code].recipe_code not in variable_recipes), Fraction(0))
        budget = (Fraction(search.index.day(day, line)['available_hours']) - occupied(day, line) - fixed_hours) * scale
        any_strict = variable(1)
        for _, _, need in costs:
            constraint({any_strict: 1, need: -1}, lower=0)
        constraint({any_strict: 1, **{need: -1 for _, _, need in costs}}, upper=0)
        constraint({column: 1 for column, _, _ in costs} | {any_strict: 1}, upper=budget)

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
    from .solver_allocation import allocation_objectives
    objectives = allocation_objectives(search, plan, rows, indices, variables, constraint, variable, outputs)
    deadline, cuts = time.monotonic() + seconds, 0
    native = {'status': 'time_limit_in_exact_separation'}
    affected = {row.model_code for row in rows}
    while time.monotonic() < deadline and cuts < 64:
        search.check()
        native = optimize(route, {'variables': variables, 'constraints': constraints,
            'objectives': objectives}, max(0.05, deadline - time.monotonic()), search.request.native_threads, search.should_stop)
        values = native.get('values')
        native['exact_cuts'] = cuts
        if not isinstance(values, list) or len(values) != len(variables) or any(not isinstance(v, int) or not row['lower'] <= v <= row['upper'] for v, row in zip(values, variables)):
            return None, native
        for constraint_row in constraints:
            value = sum(Fraction(coefficient) * values[int(index)] for index, coefficient in constraint_row['coefficients'].items())
            if constraint_row['lower'] is not None and value < Fraction(constraint_row['lower']) or constraint_row['upper'] is not None and value > Fraction(constraint_row['upper']):
                return None, native | {'status': 'exact_native_constraint_rejected'}
        changed = {row.id: values[indices[row.id]] for row in rows}
        if all(changed[row.id] == row.quantity for row in rows):
            return plan, native
        trial = plan.model_copy(update={'segments': [row.model_copy(update={'quantity': changed[row.id]}) if row.id in changed else row
            for row in plan.segments if row.id not in changed or changed[row.id]]})
        try:
            trial = fixed_switch_certificate(trial, search.context, search.scope, preserve_existing=search.request.mode == 'new_week', recipe_code=recipe)
            search.authorize(trial)
            valid = all(not any(issue.severity in ('error', 'incomplete') for issue in search.project(trial, model)['issues']) for model in affected)
        except ValueError:
            valid = False
        if valid and search.eligible(trial):
            return trial, native
        # Exact oracle separation encodes arbitrary FIFO/quota/ban/rounding
        # outcomes, including zero input shares. Exclude only this whole free
        # input tuple; do not assume monotonicity of largest-remainder shares.
        # Auxiliary variables are not part of the tuple, so another auxiliary
        # assignment cannot bypass a rejected business arrangement.
        branches = {}
        for row in rows:
            column = indices[row.id]
            value, bounds = values[column], variables[column]
            if value > bounds['lower']:
                flag = variable(1)
                bound = bounds['upper'] - value + 1
                constraint({column: 1, flag: bound}, upper=value - 1 + bound)
                branches[flag] = 1
            if value < bounds['upper']:
                flag = variable(1)
                bound = value + 1 - bounds['lower']
                constraint({column: 1, flag: -bound}, lower=value + 1 - bound)
                branches[flag] = 1
        if not branches:
            return None, native | {'status': 'no_witness_in_fixed_quantity_domain'}
        constraint(branches, lower=1)
        cuts += 1
    return None, native | {'status': 'time_limit_in_exact_separation', 'exact_cuts': cuts}
