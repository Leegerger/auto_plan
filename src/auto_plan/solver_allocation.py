"""Integer FIFO attribution and general dated shipping/bans for native routes.

Input-share largest-remainder material quotas share the integer model.
Whole-week bans bind raw allocation even when its apportioned input is zero.
"""
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction


def allocation_objectives(search, plan, rows, indices, variables, constraint, variable, outputs):
    objectives = [dict() for _ in range(33)]
    model_demands = defaultdict(list)
    for demand in search.snapshot.inputs.business.demands:
        if demand.quantity and search.snapshot.time.window_start <= demand.week <= search.snapshot.time.window_end:
            model_demands[demand.model_code].append(demand)
    zero = plan.model_copy(update={'segments': [r for r in plan.segments if r.id not in indices]})
    shipping_uses = defaultdict(list)
    batches = search.snapshot.inputs.business.confirmations
    bans = {(b.model_code, b.week, b.kind) for b in batches if b.status == 'forbidden'}
    sources = {}
    for model in sorted(outputs):
        search.check()
        demands = sorted(model_demands[model], key=lambda r: r.week)
        if not demands:
            continue
        source_dates = {day for day, _, _ in outputs[model]}
        for day in source_dates:
            free = [r for r in rows if r.model_code == model and r.date == day]
            fixed_quantity = sum(r.quantity for r in plan.segments if r.model_code == model and r.date == day and r.id not in indices)
            parameters = search.index.critical_parameters(day.strftime('%Y-%m'), model)
            unit = Fraction(parameters['line_yield']['percent']) * Fraction(parameters['model_yield']['percent']) == 10000 and Fraction(parameters['cycle_time']['days']).denominator == 1
            sources[f'input:{model}:{day}'] = {'model': model, 'input_date': day, 'wip': False,
                'quantity_columns': {indices[r.id]: 1 for r in free}, 'fixed_quantity': fixed_quantity,
                'quantity_upper': fixed_quantity + sum(variables[indices[r.id]]['upper'] for r in free),
                'unit_free': unit and not fixed_quantity, 'allocations': defaultdict(list)}
        events = [(output_day, day, f'input:{model}:{day}', column, 0, False)
                  for day, output_day, column in outputs[model]]
        for source in search.project(zero, model)['output_sources']:
            if source.model_code != model or source.kind == 'input' and source.input_date in source_dates:
                continue
            for portion in source.portions:
                events.append((portion.date, source.input_date, source.id, None, portion.quantity, source.kind == 'wip'))
            sources[source.id] = {'model': model, 'input_date': source.input_date, 'wip': source.kind == 'wip',
                'quantity_columns': {}, 'fixed_quantity': source.quantity, 'quantity_upper': source.quantity,
                'unit_free': False, 'allocations': defaultdict(list)}
        events.sort(key=lambda event: (event[0], not event[5], event[1], event[2]))
        maximum = sum(variables[event[3]]['upper'] if event[3] is not None else event[4] for event in events)
        bound = maximum + sum(d.quantity for d in demands) + 1
        cumulative, constant, previous = {}, 0, {}
        week_allocations, ontime_allocations = defaultdict(list), defaultdict(list)
        for output_day, input_day, source_id, column, quantity, wip in events:
            if column is not None:
                cumulative[column] = cumulative.get(column, 0) + 1
            else:
                constant += quantity
            preceding_demand = 0
            for demand in demands:
                # z is clamp(cumulative_output - preceding_demand,0,D).
                # Three disjoint affine regions encode exact deterministic FIFO.
                z = variable(demand.quantity)
                below, middle, above = variable(1), variable(1), variable(1)
                constraint({below: 1, middle: 1, above: 1}, lower=1, upper=1)
                offset = constant - preceding_demand
                constraint({z: 1, below: demand.quantity}, upper=demand.quantity)
                constraint(cumulative | {below: bound}, upper=bound-offset)
                constraint({z: 1, above: -demand.quantity}, lower=0)
                constraint(cumulative | {above: -bound}, lower=demand.quantity-bound-offset)
                constraint({z: 1, **{key: -value for key, value in cumulative.items()}, middle: bound}, upper=bound+offset)
                constraint({**cumulative, z: -1, middle: bound}, upper=bound-offset)
                allocated = variable(demand.quantity)
                coefficients = {allocated: 1, z: -1}
                if demand.week in previous:
                    coefficients[previous[demand.week]] = 1
                constraint(coefficients, lower=0, upper=0)
                previous[demand.week] = z
                week_allocations[demand.week].append(allocated)
                sources[source_id]['allocations'][demand.week].append(allocated)
                early = not wip and input_day < demand.week
                late = output_day >= demand.week + timedelta(days=7)
                if early and (model, demand.week, 'material') in bans or late and (model, demand.week, 'shipping') in bans:
                    constraint({allocated: 1}, upper=0)
                if late:
                    uses = []
                    for batch in batches:
                        if batch.model_code == model and batch.week == demand.week and batch.kind == 'shipping' and batch.status == 'allowed' and batch.date >= output_day:
                            used = variable(min(demand.quantity, batch.quantity))
                            uses.append(used)
                            shipping_uses[model, demand.week, batch.batch_id].append(used)
                    constraint({**{used: 1 for used in uses}, allocated: -1}, lower=0, upper=0)
                else:
                    ontime_allocations[demand.week].append(allocated)
                preceding_demand += demand.quantity
        for demand in demands:
            number = (demand.week-search.snapshot.time.window_start).days//7
            for final in (False, True):
                gap = variable(demand.quantity)
                allocations = week_allocations[demand.week] if final else ontime_allocations[demand.week]
                constraint({gap: 1, **{column: 1 for column in allocations}}, lower=demand.quantity, upper=demand.quantity)
                objectives[15*final+number][str(gap)] = '1'
    for batch in batches:
        key = batch.model_code, batch.week, batch.batch_id
        if batch.kind == 'shipping' and batch.status == 'allowed' and key in shipping_uses:
            constraint({column: 1 for column in shipping_uses[key]}, upper=batch.quantity)
    from .solver_material import material_constraints
    material_constraints(search, variables, constraint, variable, sources)
    # The common future cells have no executed input. Use the same adopted
    # coverage interpretation as the shared stability module, keeping that
    # baseline fixed even when construction prepared a different starting plan.
    baseline = search.snapshot.baseline
    if baseline:
        from .stability import _cells, _covered
        old = _cells(baseline.plan)
        groups = defaultdict(list)
        fixed = _cells(zero)
        for row in rows:
            groups[row.date, row.line_code, row.model_code].append(row)
        for key, cell_rows in groups.items():
            if key not in old and not _covered(key, baseline.coverage):
                continue
            number = (key[0]-search.snapshot.time.window_start).days//7
            band = 'near' if number < search.snapshot.preferences.near_weeks else 'far'
            weight = getattr(search.snapshot.preferences, band+'_week_weight')
            maximum = sum(variables[indices[r.id]]['upper'] for r in cell_rows)
            difference = variable(maximum + fixed[key] + old[key])
            constraint({difference: 1, **{indices[r.id]: -1 for r in cell_rows}}, lower=fixed[key]-old[key])
            constraint({difference: 1, **{indices[r.id]: 1 for r in cell_rows}}, lower=old[key]-fixed[key])
            objectives[31][str(difference)] = weight
        old_days, new_days = defaultdict(list), defaultdict(list)
        for row in baseline.plan.segments:
            if row.quantity:
                old_days[row.date, row.line_code].append(row)
        for row in plan.segments:
            if row.quantity or row.id in indices:
                new_days[row.date, row.line_code].append(row)
        activities = {}
        for row in rows:
            active = variable(1)
            constraint({indices[row.id]: 1, active: -variables[indices[row.id]]['upper']}, upper=0)
            constraint({indices[row.id]: 1, active: -1}, lower=0)
            activities[row.id] = active
        for key in {(r.date, r.line_code) for r in rows}:
            old_sequence = [r.model_code for r in sorted(old_days[key], key=lambda r: (r.order, r.id))]
            relations = {(a, b) for n, a in enumerate(old_sequence) for b in old_sequence[n+1:] if a != b}
            old_models = set(old_sequence)
            proposed = sorted(new_days[key], key=lambda r: (r.order, r.id))
            terms, constant_pairs = defaultdict(list), set()
            for number, left in enumerate(proposed):
                for right in proposed[number+1:]:
                    a, b = left.model_code, right.model_code
                    if a == b or a not in old_models or b not in old_models or (a, b) in relations:
                        continue
                    pair = tuple(sorted((a, b)))
                    operands = [activities[r.id] for r in (left, right) if r.id in activities]
                    if not operands:
                        constant_pairs.add(pair)
                        continue  # This is constant outside the repaired cells.
                    active = variable(1)
                    for operand in operands:
                        constraint({active: 1, operand: -1}, upper=0)
                    constraint({active: 1, **{operand: -1 for operand in operands}}, lower=1-len(operands))
                    terms[pair].append(active)
            for pair, columns in terms.items():
                if pair in constant_pairs:
                    continue
                reversal = variable(1)
                for column in columns:
                    constraint({reversal: 1, column: -1}, lower=0)
                constraint({reversal: 1, **{column: -1 for column in columns}}, upper=0)
                objectives[32][str(reversal)] = '1'
    return objectives
