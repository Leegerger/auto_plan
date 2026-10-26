"""Audited field Recipe evidence and adoption prerequisites."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from typing import Literal
from pydantic import Field, field_validator
from .input_models import InputRow, Code
from .plan_models import LineStateRecord, LineStateAudit, RuleIssue
from .plan_operations import register_operation, ReplayResult
from .production_time import production_date


class StateConfirmation(InputRow):
    line_code: Code
    effective_at: datetime
    recipe_code: Code
    source: str = Field(min_length=1, max_length=500)
    expected_record_id: Code | None = None

    @field_validator('effective_at')
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('线体状态时点必须包含时区')
        return value

    @field_validator('source')
    @classmethod
    def field_source(cls, value):
        if not value.strip():
            raise ValueError('请填写线体状态的现场来源')
        return value.strip()


class ConfirmLineStates(InputRow):
    kind: Literal['confirm_line_states']
    rows: list[StateConfirmation] = Field(min_length=1, max_length=1000)
    reason: str | None = Field(default=None, max_length=2000)


def confirm_line_states(plan, command, context):
    lines = {row.code for row in context.inputs.masters.lines if row.active}
    recipes = {row.code for row in context.inputs.masters.recipes if row.active}
    records = {(row.line_code, row.effective_at): row for row in context.line_states.records}
    identities = [(row.line_code, row.effective_at) for row in command.rows]
    if len(identities) != len(set(identities)):
        raise ValueError('线体状态确认包含重复线体和对应时点')
    changes = []
    for row in command.rows:
        if row.line_code not in lines or row.recipe_code not in recipes:
            raise ValueError('请选择有效线体和 Recipe')
        if row.effective_at > context.time.as_of:
            raise ValueError('不能确认未来时点的现场状态')
        key = row.line_code, row.effective_at
        old = records.get(key)
        if old and (old.recipe_code, old.source) == (row.recipe_code, row.source):
            continue
        if old:
            if row.expected_record_id != old.id:
                raise ValueError('已确认状态已变化，请重新读取并核对更正基准')
            if not command.reason or not command.reason.strip():
                raise ValueError('更正已确认线体状态必须填写原因并预览影响')
        elif row.expected_record_id is not None:
            raise ValueError('更正来源状态不存在，请重新核对')
        identity = 'state-' + hashlib.sha256(f'{context.action_id}:{row.line_code}:{row.effective_at.isoformat()}'.encode()).hexdigest()[:40]
        record = LineStateRecord(id=identity, line_code=row.line_code, effective_at=row.effective_at,
            recipe_code=row.recipe_code, source=row.source, actor=context.actor, recorded_at=context.time.as_of)
        changes.append({'before': old.model_dump(mode='json') if old else None, 'after': record.model_dump(mode='json')})
        records[key] = record
    if not changes:
        return ReplayResult(plan)
    audit = LineStateAudit(action_id=context.action_id, actor=context.actor, recorded_at=context.time.as_of,
                           reason=command.reason.strip() if command.reason else None, changes=changes)
    updated = context.line_states.model_copy(update={'records': list(records.values()), 'audit': context.line_states.audit + [audit]})
    return ReplayResult(plan, context_updates={'line_states': updated})


@dataclass(frozen=True)
class ActualRecipeAnchor:
    id: str
    line_code: str
    effective_at: datetime
    recipe_code: str
    recorded_at: datetime
    priority: int = 0


def calculation_anchors(snapshot):
    """Derive Recipe carry from facts without creating a field confirmation."""
    from collections import defaultdict
    from .production_time import production_cutoff
    records = [record for record in snapshot.line_states.records if record.effective_at <= snapshot.time.as_of]
    cutoff = snapshot.execution.cutoff
    if cutoff is None or cutoff > snapshot.time.as_of:
        return records
    today = production_date(snapshot.time.as_of)
    models = {model.code: model.recipe_code for model in snapshot.inputs.masters.models}
    actual = defaultdict(set)
    for fact in snapshot.execution.facts:
        if fact.quantity and fact.date < today and fact.date <= production_date(cutoff):
            actual[fact.date, fact.line_code].add(models.get(fact.model_code))
    for (day, line), recipes in actual.items():
        if len(recipes) == 1 and None not in recipes:
            recipe = next(iter(recipes))
            records.append(ActualRecipeAnchor('actual-' + hashlib.sha256(f'{day}:{line}:{recipe}'.encode()).hexdigest()[:40],
                line, production_cutoff(day + timedelta(days=1)), recipe, cutoff))
    return records


def record_is_current(snapshot, record):
    """Carry evidence through idle or established work, never elapsed plans alone."""
    from collections import defaultdict
    today = production_date(snapshot.time.as_of)
    first = production_date(record.effective_at)
    models = {row.code: row.recipe_code for row in snapshot.inputs.masters.models}
    planned = defaultdict(set)
    switches = defaultdict(list)
    actual = defaultdict(set)
    for segment in snapshot.plan.segments:
        if segment.line_code == record.line_code and first <= segment.date < today and (segment.quantity or segment.hours != '0'):
            planned[segment.date].add(models.get(segment.model_code))
    for switch in snapshot.plan.switches:
        if switch.line_code != record.line_code:
            continue
        for allocation in switch.allocations:
            if first <= allocation.date < today:
                switches[allocation.date].append(switch)
    cutoff = snapshot.execution.cutoff
    if cutoff is not None and cutoff <= snapshot.time.as_of:
        last = production_date(cutoff)
        for fact in snapshot.execution.facts:
            if fact.line_code == record.line_code and first <= fact.date < today and fact.date <= last and fact.quantity:
                actual[fact.date].add(models.get(fact.model_code))
    recipe, known = record.recipe_code, True
    for day in sorted(planned.keys() | switches.keys() | actual.keys()):
        recipes = actual[day]
        if len(recipes) == 1 and None not in recipes:
            recipe, known = next(iter(recipes)), True
            if any(max(allocation.date for allocation in switch.allocations) > day or switch.to_recipe != recipe for switch in switches[day]):
                known = False
        elif recipes or switches[day] or any(value != recipe for value in planned[day]):
            known = False
    return known


def adoption_conditions(snapshot):
    from .execution_rules import confirmed_day_capacity, executed_by_segment
    today = production_date(snapshot.time.as_of)
    executed = executed_by_segment(snapshot.plan, snapshot.execution)
    affected = {}
    for segment in sorted(snapshot.plan.segments, key=lambda row: (row.date, row.order, row.id)):
        if segment.date >= today and segment.quantity > executed.get(segment.id, 0):
            affected.setdefault(segment.line_code, (segment.date, segment.id))
    for switch in snapshot.plan.switches:
        allocations = [row for row in switch.allocations if row.date >= today]
        if allocations:
            affected.setdefault(switch.line_code, (min(row.date for row in allocations), None))
    conditions = []
    for line, (day, segment) in sorted(affected.items()):
        records = [row for row in calculation_anchors(snapshot) if row.line_code == line]
        latest=max(records,key=lambda record:(record.effective_at,getattr(record,'priority',1),record.recorded_at)) if records else None
        if confirmed_day_capacity(snapshot,line) is not None or latest is not None and record_is_current(snapshot,latest):
            continue
        conditions.append(RuleIssue(code='initial_state_unknown', severity='warning', line_code=line, date=day,
            segment_id=segment, reason=('现场状态后的历史计划切机或生产尚无执行依据，不能推定已经完成，请重新确认当前线体状态。' if latest is not None else '起点未知或仅有未确认的计划值，首次切机尚未计入。请补充线体状态现场确认后重新预览。')))
    return conditions


register_operation('confirm_line_states', ConfirmLineStates, confirm_line_states)


def protect_initial_hints(before, after, snapshot):
    from .production_time import production_cutoff
    anchors=calculation_anchors(snapshot)
    left = {row.line_code: row.recipe_code for row in before.initial_states}
    for row in after.initial_states:
        if row.recipe_code is None or left.get(row.line_code) == row.recipe_code:
            continue
        records = [record for record in anchors if record.line_code == row.line_code
                   and record.effective_at <= production_cutoff(snapshot.time.window_start)]
        if records and max(records, key=lambda record: (record.effective_at,getattr(record,'priority',1),record.recorded_at)).recipe_code != row.recipe_code:
            from .plans import PlanProblem
            raise PlanProblem(409, '普通计划修改不能覆盖线体现场依据，请通过状态更正并预览影响', line_code=row.line_code)
