"""Exact production-hour repair while every global switch remains fixed."""
from collections import defaultdict
from fractions import Fraction
from .input_lookup import InputIndex
from .production_time import production_date


def preserve_existing(plan, context, scope):
    """Preserve old event positions/hours while using authorized idle space."""
    from .manual_operations import _preserve_fixed_models
    index = InputIndex(context.inputs)
    original = context.plan
    output = plan
    for line in scope.line_codes:
        inside = lambda row: row.line_code == line and scope.start_date <= row.date <= scope.end_date
        switch_inside = lambda switch: switch.line_code == line and all(scope.start_date <= a.date <= scope.end_date for a in switch.allocations)
        old_rows = [r for r in original.segments if inside(r)]
        subset = plan.model_copy(update={'segments': [r for r in output.segments if inside(r)],
            'switches': [s for s in output.switches if switch_inside(s)]})
        subset = _preserve_fixed_models(subset, old_rows, index, scope, line, fixed_ids={r.id for r in old_rows})
        documents = {s.model_dump_json(exclude={'id'}): s for s in original.switches if switch_inside(s)}
        subset = subset.model_copy(update={'switches': [documents.get(s.model_dump_json(exclude={'id'}), s) for s in subset.switches]})
        output = output.model_copy(update={'segments': [r for r in output.segments if not inside(r)] + subset.segments,
            'switches': [s for s in output.switches if not switch_inside(s)] + subset.switches})
    return output


def fixed_switch_certificate(plan, context, scope, preserve_existing=False, recipe_code=None):
    index = InputIndex(context.inputs)
    original = {r.id: r for r in context.plan.segments}
    groups = defaultdict(list)
    for row in plan.segments:
        if row.date <= production_date(context.time.as_of) and original.get(row.id) != row:
            # Current-day reconstruction belongs to execution_certificate; its
            # residual current-Recipe switch model cannot be replaced here.
            raise ValueError('本阶段固定切机，仅修复未来数量；当天请使用完整执行工时构造')
        groups[row.date, row.line_code, index.models[row.model_code].recipe_code].append(row)
    switches = defaultdict(Fraction)
    for switch in plan.switches:
        for allocation in switch.allocations:
            switches[allocation.date, switch.line_code] += Fraction(allocation.hours)
    daily = defaultdict(list)
    for key, rows in groups.items():
        daily[key[:2]].append((key, rows))
    updates = {}
    for (day, line), items in daily.items():
        bounds, editable, needs = {}, {}, []
        for key, rows in items:
            free = [r for r in rows if scope.contains(r) and not (preserve_existing and r.id in original)
                    and (recipe_code is None or key[2] == recipe_code)]
            editable[key] = free
            fixed = [r for r in rows if r not in free]
            capacity = index.capacity(day.strftime('%Y-%m'), key[2], line)
            quantity = sum(r.quantity for r in rows)
            if quantity and capacity['state'] != 'known':
                raise ValueError('正投入需要已知允许产能')
            lower = Fraction(24 * (quantity - 1), capacity['pieces']) if quantity else Fraction(0)
            fixed_hours = sum((Fraction(r.hours) for r in fixed), Fraction(0))
            bounds[key] = max(fixed_hours, lower)
            if quantity and fixed_hours <= lower:
                if not free:
                    raise ValueError('固定安排无法满足严格容量边界')
                needs.append(key)
        spare = Fraction(index.day(day, line)['available_hours']) - switches[day, line] - sum(bounds.values(), Fraction(0))
        if spare < 0 or needs and spare <= 0:
            raise ValueError('固定切机及其他 Recipe 后没有精确容量依据')
        margin = spare / (2 * len(needs)) if needs else Fraction(0)
        for key, rows in items:
            free = editable[key]
            fixed_hours = sum((Fraction(r.hours) for r in rows if r not in free), Fraction(0))
            hours = bounds[key] + (margin if key in needs else 0) - fixed_hours
            quantity = sum(r.quantity for r in free)
            for row in free:
                updates[row.id] = row.model_copy(update={'hours': str(hours * row.quantity / quantity if quantity else 0)})
    return plan.model_copy(update={'segments': [updates.get(r.id, r) for r in plan.segments]})
