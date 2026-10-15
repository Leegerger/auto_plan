"""One recoverable quantity change and its ordered, same-line overflow chain."""
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Literal

from .input_models import Code, Pieces
from .input_lookup import InputIndex
from .plan_models import ProductionSegment
from .manual_operations import (
    ScopedOperation, CapacityWitnessError, bounded_certificate, derived_id,
    finish_operation, selected_segments,
)
from .plan_operations import ReplayResult, register_operation


class ChangeQuantity(ScopedOperation):
    kind: Literal['change_quantity']
    segment_id: Code
    quantity: Pieces
    end_date: date


@dataclass
class PendingSegment:
    row: ProductionSegment
    requested: int
    remaining: int


def _executed_minima(plan, execution):
    from .execution_rules import executed_by_segment
    return executed_by_segment(plan,execution)


def change_quantity(plan, command, context):
    selected = selected_segments(plan, [command.segment_id])[0]
    scope = command.scope
    if not scope.contains(selected) or not selected.date <= command.end_date <= scope.end_date:
        raise ValueError('当前段和同线截止日必须位于授权调整范围')
    if command.quantity == selected.quantity:
        return plan
    if command.quantity < selected.quantity:
        changed = selected.model_copy(update={'quantity': command.quantity})
        result = plan.model_copy(update={'segments': [changed if r.id == selected.id else r for r in plan.segments]}, deep=True)
        local_scope = scope.model_copy(update={'start_date': selected.date, 'end_date': selected.date,
                                               'line_codes': [selected.line_code]})
        return finish_operation(plan, result, command.model_copy(update={'scope': local_scope}), context)

    locked = {(r.date, r.line_code, r.model_code) for r in context.locks}
    if (selected.date, selected.line_code, selected.model_code) in locked:
        from .plans import PlanProblem
        raise PlanProblem(409, '当前生产段的汇总数量已锁定，请先显式解锁', conflicts=[{
            'code': 'locked_cell', 'date': selected.date.isoformat(),
            'line_code': selected.line_code, 'model_code': selected.model_code}])
    index = InputIndex(context.inputs)
    model = index.models[selected.model_code]
    if not model.active or not index.lines[selected.line_code].active or not index.recipes[model.recipe_code].active:
        raise ValueError('停用机种、Recipe 或线体不可增加投入')
    minima = _executed_minima(plan, context.execution)
    planned = defaultdict(int)
    for row in plan.segments:
        planned[row.date, row.line_code, row.model_code] += row.quantity
    overplan = {(r.date, r.line_code, r.model_code) for r in context.execution.facts
                if r.quantity > planned[r.date, r.line_code, r.model_code]}
    last_executed = {}
    for row in plan.segments:
        if minima[row.id]:
            key = row.date, row.line_code, row.model_code
            last_executed[key] = max(last_executed.get(key, (-1, '')), (row.order, row.id))
    tasks = []
    reserved = []
    for row in sorted(plan.segments, key=lambda r: (r.date, r.line_code, r.order, r.id)):
        follows = row.date > selected.date or (row.order, row.id) >= (selected.order, selected.id)
        eligible = row.line_code == selected.line_code and selected.date <= row.date <= command.end_date and follows and scope.contains(row)
        if not eligible or (row.date, row.line_code, row.model_code) in locked:
            reserved.append(row)
            continue
        quantity = command.quantity if row.id == selected.id else row.quantity
        executed = minima[row.id]
        if executed:
            reserved.append(row.model_copy(update={'quantity': executed}))
        tasks.append(PendingSegment(row, quantity, quantity - executed))
    result = plan.model_copy(update={'segments': reserved}, deep=True)
    probe_scope = scope.model_copy(update={'start_date': selected.date, 'end_date': command.end_date,
                                          'line_codes': [selected.line_code]})
    earlier_ids = {r.id for r in plan.segments if r.line_code == selected.line_code and r.date == selected.date
                   and (r.order, r.id) < (selected.order, selected.id)}
    # Intermediate capacity witnesses may not yet contain the later queued
    # Recipe. Check the real outgoing boundary once the complete chain exists.
    capacity_original = plan.model_copy(update={
        'segments': [r for r in plan.segments if r.line_code != selected.line_code or r.date <= command.end_date],
        'switches': [s for s in plan.switches if s.line_code != selected.line_code or all(a.date <= command.end_date for a in s.allocations)]})
    witness_context = replace(context, snapshot=context.snapshot.model_copy(update={'plan': capacity_original}))
    placed_ids = defaultdict(set)
    day = selected.date
    while day <= command.end_date:
        for task in tasks:
            if not task.remaining or task.row.date > day:
                continue
            if (day, selected.line_code, task.row.model_code) in locked:
                continue
            recipe = index.models[task.row.model_code].recipe_code
            capacity = index.capacity(day.strftime('%Y-%m'), recipe, selected.line_code)
            if capacity['state'] != 'known' or not capacity['pieces']:
                continue
            cell = day, selected.line_code, task.row.model_code
            separate = day != task.row.date or (task.row.order, task.row.id) < last_executed.get(cell, (-1, '')) or (cell in overplan and minima[task.row.id])
            identity = derived_id(context, f'overflow:{task.row.id}:{day}') if separate else task.row.id

            def candidate(quantity):
                working = result.model_copy(deep=True)
                existing = next((r for r in working.segments if r.id == identity), None)
                if existing:
                    working = working.model_copy(update={'segments': [r.model_copy(update={'quantity': r.quantity + quantity}) if r.id == identity else r for r in working.segments]})
                else:
                    row = task.row.model_copy(update={'id': identity, 'date': day, 'quantity': quantity, 'hours': '0'})
                    daily = sorted([r for r in working.segments if (r.date, r.line_code) == (day, selected.line_code)], key=lambda r: (r.order, r.id))
                    # A carried segment precedes that day's later unprotected
                    # work. Earlier source-day rows remain before the edited row.
                    position = max((n + 1 for n, r in enumerate(daily) if r.id in placed_ids[day] or r.id in earlier_ids or
                                    (r.model_code == task.row.model_code and minima.get(r.id, 0))), default=0)
                    daily.insert(position, row)
                    orders = {r.id: r.model_copy(update={'order': n}) for n, r in enumerate(daily)}
                    working = working.model_copy(update={'segments': [orders.get(r.id, r) for r in working.segments] + [orders[row.id]]})
                outside_rows = [r for r in working.segments if r.line_code == selected.line_code and r.date > command.end_date]
                outside_switches = [s for s in working.switches if s.line_code == selected.line_code and any(a.date > command.end_date for a in s.allocations)]
                probe = working.model_copy(update={
                    'segments': [r for r in working.segments if r not in outside_rows],
                    'switches': [s for s in working.switches if s not in outside_switches]})
                witness = bounded_certificate(probe, witness_context, probe_scope)
                return witness.model_copy(update={'segments': witness.segments + outside_rows,
                                                  'switches': witness.switches + outside_switches})

            low, high, best = 0, min(task.remaining, capacity['pieces'] + 1), None
            while low < high:
                amount = (low + high + 1) // 2
                try:
                    proposed = candidate(amount)
                except CapacityWitnessError:
                    high = amount - 1
                else:
                    low, best = amount, proposed
            if low:
                result = best if best is not None else candidate(low)
                task.remaining -= low
                placed_ids[day].add(identity)
            if task.remaining:
                # Later unprotected work must follow this remainder. It cannot
                # take capacity first merely because its Recipe fits better.
                break
        if not any(task.remaining and task.row.date <= day for task in tasks):
            # No overflow reaches tomorrow. Its existing work is independent of
            # this action and retains its original exact witness and position.
            restored = {task.row.id: task.row for task in tasks if task.row.date > day}
            result = result.model_copy(update={'segments': [r for r in result.segments if r.id not in restored] + list(restored.values())})
            for task in tasks:
                if task.row.id in restored:
                    task.remaining = 0
            break
        day += timedelta(days=1)
    changed_scope = scope.model_copy(update={'start_date': selected.date, 'end_date': min(day, command.end_date),
                                            'line_codes': [selected.line_code]})
    inside = lambda row: row.line_code == selected.line_code and changed_scope.start_date <= row.date <= changed_scope.end_date
    inside_switch = lambda event: event.line_code == selected.line_code and all(changed_scope.start_date <= a.date <= changed_scope.end_date for a in event.allocations)
    result = result.model_copy(update={
        'segments': [r for r in result.segments if inside(r)] + [r for r in plan.segments if not inside(r)],
        'switches': [s for s in result.switches if inside_switch(s)] + [s for s in plan.switches if not inside_switch(s)],
        'ordinary_idle': [r for r in result.ordinary_idle if inside(r)] + [r for r in plan.ordinary_idle if not inside(r)]})
    result = finish_operation(plan, result, command.model_copy(update={'scope': changed_scope}), context)
    leftovers = [{'id': derived_id(context, f'unallocated:{task.row.id}'),
                  'model_code': task.row.model_code, 'quantity': task.remaining,
                  'source_segment_id': task.row.id, 'requested': task.requested,
                  'placed': task.requested - task.remaining, 'line_code': selected.line_code,
                  'start_date': task.row.date.isoformat(), 'end_date': command.end_date.isoformat(),
                  'reason': '改量顺延达到指定同线截止日，保留余额供继续选线或明确舍弃'}
                 for task in tasks if task.remaining]
    return ReplayResult(result, unallocated=leftovers)


register_operation('change_quantity', ChangeQuantity, change_quantity)
