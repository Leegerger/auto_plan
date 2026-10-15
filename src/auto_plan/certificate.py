"""Construct an exact hour witness for ordered standard-source quantities.

Each productive day has an open lower time bound. Interval capacity checks find
an exact positive shared margin before allocating directed switch durations.
No minimum hour grid or fixed epsilon participates in the construction.
"""
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction
from typing import Sequence
from .input_lookup import InputIndex
from .input_models import InputSnapshot
from .plan_models import (SourceSegment, InitialRecipeState, Plan, ProductionSegment,
                          RecipeSwitch, SwitchAllocation, RuleIssue)


class CertificateError(Exception):
    def __init__(self, issues: list[RuleIssue]):
        self.issues = issues


def construct_hour_certificate(inputs: InputSnapshot, segments: list[SourceSegment],
                               initial_states: Sequence[InitialRecipeState] = (), *, window_start=None, day_limits=None) -> Plan:
    """Return a lossless certificate; callers still evaluate the full snapshot."""
    index = InputIndex(inputs)
    initial = {row.line_code: row.recipe_code for row in initial_states}
    ordered_lines = defaultdict(list)
    groups = defaultdict(list)
    errors = []
    seen_ids, seen_orders = set(), set()
    for row in segments:
        if row.model_code not in index.models or row.line_code not in index.lines:
            errors.append(RuleIssue(code='unknown_master', reason='机种或线体编码不存在', date=row.date, line_code=row.line_code, model_code=row.model_code, segment_id=row.id))
            continue
        if row.id in seen_ids or (row.date, row.line_code, row.order) in seen_orders:
            errors.append(RuleIssue(code='duplicate_order', reason='生产段标识或同日顺序重复', date=row.date, line_code=row.line_code, model_code=row.model_code, segment_id=row.id))
        seen_ids.add(row.id)
        seen_orders.add((row.date, row.line_code, row.order))
        recipe = index.models[row.model_code].recipe_code
        ordered_lines[row.line_code].append(row)
        groups[(row.date, row.line_code, recipe)].append(row)
    if errors:
        raise CertificateError(errors)
    if not segments:
        return Plan(initial_states=list(initial_states))
    first_date = window_start or min(row.date for row in segments)
    if first_date > min(row.date for row in segments):
        raise CertificateError([RuleIssue(code='certificate_range', reason='工时依据起日不能晚于已排投入')])
    if (max(row.date for row in segments) - first_date).days >= 105:
        raise CertificateError([RuleIssue(code='certificate_range', reason='工时依据仅构造冻结的十五周排程窗口')])
    lower, quantities = {}, {}
    for key, rows in groups.items():
        day, line, recipe = key
        quantity = sum(row.quantity for row in rows)
        quantities[key] = quantity
        capacity = index.capacity(day.strftime('%Y-%m'), recipe, line)
        if quantity and (capacity['state'] != 'known' or capacity['pieces'] == 0):
            errors.append(RuleIssue(code='recipe_capacity', reason='正投入需要已知、允许且大于零的全日产能', date=day, line_code=line, recipe_code=recipe, model_code=rows[0].model_code))
        lower[key] = Fraction(24 * (quantity - 1), capacity['pieces']) if quantity and capacity['pieces'] else Fraction(0)
    if errors:
        raise CertificateError(errors)
    group_hours = {}
    switch_rows = []
    switch_sequence = 0
    final_orders = {}
    for line, rows in sorted(ordered_lines.items()):
        rows.sort(key=lambda row: (row.date, row.order))
        by_day = defaultdict(list)
        for row in rows:
            by_day[row.date].append(row)
        for day, daily_rows in by_day.items():
            for position, row in enumerate(daily_rows):
                final_orders[row.id] = position * 2 + 1
        dates = []
        current = first_date
        while current <= rows[-1].date:
            dates.append(current)
            current += timedelta(days=1)
        positions = {day: position for position, day in enumerate(dates)}
        bounds = []
        productive = []
        for day in dates:
            keys = [key for key in groups if key[0] == day and key[1] == line]
            bounds.append(Fraction((day_limits or {}).get((day,line),index.day(day, line)['available_hours'])) - sum((lower[key] for key in keys), Fraction(0)))
            productive.append(any(quantities[key] for key in keys))
        jobs = []
        state = initial.get(line)
        previous = None
        for row in rows:
            if not row.quantity:
                continue
            recipe = index.models[row.model_code].recipe_code
            if state is not None and state != recipe:
                switch = index.switch(state, recipe)
                jobs.append({'source': previous, 'target': row, 'from': state, 'to': recipe,
                             'start': positions[previous.date if previous else first_date], 'end': positions[row.date],
                             'total': Fraction(switch['hours'])})
            state = recipe
            previous = row
        # Hall's interval inequalities are exact for jobs with consecutive day
        # eligibility. Reserve half the tightest positive margin per productive
        # day, giving every Recipe a strict (not epsilon-based) lower bound.
        # Sum job requirements by eligibility endpoints once. For each
        # interval [start,end], suffix_start[prefix_end] gives the duration
        # forced inside it. This keeps the certificate check quadratic in
        # the 105-day window, rather than multiplying by every production gap.
        required_in_interval = [[Fraction(0) for _ in dates] for _ in dates]
        for job in jobs:
            required_in_interval[job['start']][job['end']] += job['total']
        for start in range(len(dates)):
            for end in range(1, len(dates)):
                required_in_interval[start][end] += required_in_interval[start][end - 1]
        for start in range(len(dates) - 2, -1, -1):
            for end in range(len(dates)):
                required_in_interval[start][end] += required_in_interval[start + 1][end]
        margin = None
        for start in range(len(dates)):
            capacity_sum = Fraction(0)
            positive_days = 0
            for end in range(start, len(dates)):
                capacity_sum += bounds[end]
                positive_days += productive[end]
                required = required_in_interval[start][end]
                slack = capacity_sum - required
                if slack < 0 or positive_days and slack <= 0:
                    raise CertificateError([RuleIssue(code='certificate_capacity', reason='此顺序和投入量没有满足严格容量边界的工时依据', date=dates[end], line_code=line)])
                if positive_days:
                    candidate = slack / positive_days
                    margin = candidate if margin is None else min(margin, candidate)
        reserve = (margin / 2) if margin is not None else Fraction(0)
        remaining = [bound - (reserve if positive else 0) for bound, positive in zip(bounds, productive)]
        for day in dates:
            keys = [key for key in groups if key[0] == day and key[1] == line and quantities[key]]
            for key in keys:
                group_hours[key] = lower[key] + reserve / len(keys)
        for job in jobs:
            pending = job['total']
            allocations = []
            for day_position in range(job['start'], job['end'] + 1):
                amount = min(pending, remaining[day_position])
                if amount or not job['total'] and day_position == job['end']:
                    day = dates[day_position]
                    if day == job['target'].date:
                        order = final_orders[job['target'].id] - 1
                    elif job['source'] and day == job['source'].date:
                        order = final_orders[job['source'].id] + 1
                    else:
                        order = 0
                    allocations.append(SwitchAllocation(date=day, order=order, hours=str(amount)))
                    remaining[day_position] -= amount
                    pending -= amount
                if not pending and allocations:
                    break
            if pending:
                raise CertificateError([RuleIssue(code='certificate_capacity', reason='切机未能在相邻生产之间分配完成', date=job['target'].date, line_code=line)])
            switch_id = f'certificate-switch-{switch_sequence}'
            while switch_id in seen_ids:
                switch_sequence += 1
                switch_id = f'certificate-switch-{switch_sequence}'
            seen_ids.add(switch_id)
            switch_sequence += 1
            switch_rows.append(RecipeSwitch(id=switch_id, line_code=line, from_recipe=job['from'], to_recipe=job['to'], allocations=allocations))
    production = []
    for row in segments:
        key = (row.date, row.line_code, index.models[row.model_code].recipe_code)
        allocation = group_hours.get(key, Fraction(0)) * row.quantity / quantities[key] if quantities[key] else Fraction(0)
        production.append(ProductionSegment(**(row.model_dump() | {'order': final_orders[row.id], 'hours': str(allocation)})))
    return Plan(segments=production, switches=switch_rows, initial_states=list(initial_states))
