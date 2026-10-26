"""Audited field Recipe evidence and adoption prerequisites."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from typing import Literal
from pydantic import Field, field_validator
from .input_models import InputRow, Code
from .plan_models import LineStateRecord, LineStateValue, LineStateAudit, LineStateReview, RuleIssue
from .plan_operations import register_operation, ReplayResult
from .production_time import production_date


class StateConfirmation(LineStateValue):
    line_code: Code
    effective_at: datetime
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
        endpoints = [row.recipe_code] if row.state == 'retained_recipe' else [row.unfinished_switch.from_recipe, row.unfinished_switch.to_recipe]
        if row.line_code not in lines or any(recipe not in recipes for recipe in endpoints):
            raise ValueError('请选择有效线体和 Recipe')
        if row.effective_at > context.time.as_of:
            raise ValueError('不能确认未来时点的现场状态')
        if row.unfinished_switch is not None and row.unfinished_switch.started_at > row.effective_at:
            raise ValueError('原切机开始时点不能晚于状态对应时点')
        if row.unfinished_switch is not None and any(record.line_code != row.line_code and record.unfinished_switch is not None
                and record.unfinished_switch.job_id == row.unfinished_switch.job_id for record in records.values()):
            raise ValueError('原切机作业不能改到另一线体，请核对实际作业关联')
        key = row.line_code, row.effective_at
        old = records.get(key)
        if old and (old.state, old.recipe_code, old.unfinished_switch, old.source) == (row.state, row.recipe_code, row.unfinished_switch, row.source):
            continue
        prior_capacity = next((value for value in context.execution.day_capacities
            if value.line_code == row.line_code and value.confirmed_at == row.effective_at and value.current_recipe != row.recipe_code), None)
        if prior_capacity is not None and (not command.reason or not command.reason.strip()):
            raise ValueError('更正同一时点已确认的剩余工时 Recipe 必须填写现场状态更正原因')
        previous = max((record for record in records.values() if record.line_code == row.line_code and record.effective_at <= row.effective_at),
                       key=lambda record: (record.effective_at, record.recorded_at), default=None)
        previous_job = previous.unfinished_switch if previous is not None else None
        next_job = row.unfinished_switch
        reconcile = previous_job is not None and (next_job is None or
            (previous_job.job_id, previous_job.started_at, previous_job.from_recipe, previous_job.to_recipe) !=
            (next_job.job_id, next_job.started_at, next_job.from_recipe, next_job.to_recipe))
        if reconcile and (not command.reason or not command.reason.strip()):
            raise ValueError('已经开始的实际切机不能自动取消或改目标，请填写现场核对及更正原因')
        if old:
            if row.expected_record_id != old.id:
                raise ValueError('已确认状态已变化，请重新读取并核对更正基准')
            if not command.reason or not command.reason.strip():
                raise ValueError('更正已确认线体状态必须填写原因并预览影响')
        elif row.expected_record_id is not None:
            raise ValueError('更正来源状态不存在，请重新核对')
        identity = 'state-' + hashlib.sha256(f'{context.action_id}:{row.line_code}:{row.effective_at.isoformat()}'.encode()).hexdigest()[:40]
        record = LineStateRecord(id=identity, line_code=row.line_code, effective_at=row.effective_at,
            state=row.state, recipe_code=row.recipe_code, unfinished_switch=row.unfinished_switch,
            source=row.source, actor=context.actor, recorded_at=context.time.as_of)
        change = {'before': old.model_dump(mode='json') if old else None, 'after': record.model_dump(mode='json')}
        if prior_capacity is not None:
            change['previous_capacity'] = prior_capacity.model_dump(mode='json')
        if reconcile:
            change['previous_unfinished'] = previous.model_dump(mode='json')
        changes.append(change)
        records[key] = record
    resolved=resolved_reviews(records.values(),context.issues)
    if not changes:
        return ReplayResult(plan,resolve_issues=resolved)
    audit = LineStateAudit(action_id=context.action_id, actor=context.actor, recorded_at=context.time.as_of,
                           reason=command.reason.strip() if command.reason else None, changes=changes)
    updated = context.line_states.model_copy(update={'records': list(records.values()), 'audit': context.line_states.audit + [audit]})
    return ReplayResult(plan, context_updates={'line_states': updated},resolve_issues=resolved)


@dataclass(frozen=True)
class RecipeAnchor:
    id: str
    line_code: str
    effective_at: datetime
    recipe_code: str | None
    recorded_at: datetime
    priority: int = 0


def unfinished_job_history(snapshot):
    """Observed jobs and the day by which later actual evidence resolves them."""
    from .production_time import production_cutoff
    jobs = {}
    records = sorted((row for row in snapshot.line_states.records if row.effective_at <= snapshot.time.as_of),
                     key=lambda row: (row.effective_at, row.recorded_at, row.id))
    for record in records:
        if record.unfinished_switch is not None:
            jobs[record.unfinished_switch.job_id] = record
    result = {}
    cutoff = snapshot.execution.cutoff
    models = {row.code: row.recipe_code for row in snapshot.inputs.masters.models}
    today = production_date(snapshot.time.as_of)
    for identity, record in jobs.items():
        job = record.unfinished_switch
        resolved = [production_date(row.effective_at) for row in records if row.line_code == record.line_code
                    and row.effective_at >= record.effective_at and row.id != record.id
                    and (row.unfinished_switch is None or row.unfinished_switch.job_id != identity)]
        if cutoff is not None and cutoff <= snapshot.time.as_of:
            first = production_date(record.effective_at)
            days = {fact.date for fact in snapshot.execution.facts if fact.line_code == record.line_code and fact.quantity
                    and (fact.date > first or fact.date == first and record.effective_at == production_cutoff(first))
                    and fact.date < today and fact.date <= production_date(cutoff)}
            resolved.extend(day for day in days if {models.get(fact.model_code) for fact in snapshot.execution.facts
                if fact.line_code == record.line_code and fact.date == day and fact.quantity} == {job.to_recipe})
        result[identity] = record, min(resolved) if resolved else None
    return result


def active_unfinished_jobs(snapshot):
    """Resolve current actual prerequisites without treating elapsed plans as facts."""
    latest = {}
    for record in sorted(snapshot.line_states.records, key=lambda row: (row.effective_at, row.recorded_at, row.id)):
        if record.effective_at <= snapshot.time.as_of:
            latest[record.line_code] = record
    history = unfinished_job_history(snapshot)
    return {line:record for line,record in latest.items() if record.unfinished_switch is not None
            and history[record.unfinished_switch.job_id][1] is None}


def _known_anchors(snapshot):
    """Derive Recipe carry from facts without creating a field confirmation."""
    from collections import defaultdict
    from .production_time import production_cutoff
    records = [record for record in snapshot.line_states.records if record.effective_at <= snapshot.time.as_of]
    pending = active_unfinished_jobs(snapshot)
    cutoff = snapshot.execution.cutoff
    if cutoff is None or cutoff > snapshot.time.as_of:
        return records
    today = production_date(snapshot.time.as_of)
    models = {model.code: model.recipe_code for model in snapshot.inputs.masters.models}
    actual = defaultdict(set)
    for fact in snapshot.execution.facts:
        if fact.quantity and fact.date < today and fact.date <= production_date(cutoff):
            actual[fact.date, fact.line_code].add(models.get(fact.model_code))
    if not actual:
        return records
    from .execution_rules import executed_by_segment
    executed=executed_by_segment(snapshot.plan,snapshot.execution)
    intraday={(production_date(record.effective_at),record.line_code) for record in records
              if record.effective_at>production_cutoff(production_date(record.effective_at))}
    for (day, line), recipes in actual.items():
        if line in pending and day >= production_date(pending[line].effective_at):
            continue
        if (day,line) in intraday:
            continue
        if len(recipes) == 1 and None not in recipes:
            recipe = next(iter(recipes))
            orders=[segment.order for segment in snapshot.plan.segments if segment.date==day and segment.line_code==line
                    and executed.get(segment.id,0) and models.get(segment.model_code)==recipe]
            last=max(orders) if orders else None
            later_production=any(segment.date==day and segment.line_code==line and (segment.quantity or getattr(segment,'hours','0')!='0')
                and (last is None or segment.order>last) and models.get(segment.model_code)!=recipe for segment in snapshot.plan.segments)
            later_switch=any(switch.line_code==line and (switch.to_recipe!=recipe or max(row.date for row in switch.allocations)>day)
                and any(row.date==day and (last is None or row.order>last) for row in switch.allocations) for switch in snapshot.plan.switches)
            if later_production or later_switch:
                continue
            records.append(RecipeAnchor('actual-' + hashlib.sha256(f'{day}:{line}:{recipe}'.encode()).hexdigest()[:40],
                line, production_cutoff(day + timedelta(days=1)), recipe, cutoff))
    return records


def review_records(issues):
    fields=set(LineStateReview.model_fields)
    return [LineStateReview.model_validate({key:value for key,value in issue.items() if key in fields})
            for issue in issues if issue.get('code')=='line_state_review']


def active_state_reviews(snapshot):
    from .execution_rules import confirmed_day_capacity
    anchors=_known_anchors(snapshot)
    active=[]
    for review in snapshot.state_reviews:
        capacity=confirmed_day_capacity(snapshot,review.line_code)
        if capacity is not None and capacity.confirmed_at>=review.review_after:
            continue
        fresh=[record for record in anchors if record.line_code==review.line_code
               and (record.effective_at>review.review_after if isinstance(record,RecipeAnchor) else record.effective_at>=review.review_after)]
        if fresh and record_is_current(snapshot,max(fresh,key=lambda row:(row.effective_at,getattr(row,'priority',1),row.recorded_at))):
            continue
        active.append(review)
    return active


def calculation_anchors(snapshot):
    from .production_time import production_cutoff
    anchors=_known_anchors(snapshot)
    pending=active_unfinished_jobs(snapshot)
    for review in active_state_reviews(snapshot):
        if review.line_code in pending:
            continue
        point=max(review.review_after,production_cutoff(production_date(snapshot.time.as_of)))
        anchors.append(RecipeAnchor(review.id,review.line_code,point,None,snapshot.time.as_of,priority=2))
    return anchors


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
        if segment.line_code == record.line_code and first <= segment.date < today and (segment.quantity or getattr(segment,'hours','0') != '0'):
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


def affected_events(snapshot):
    from .execution_rules import executed_by_segment
    today=production_date(snapshot.time.as_of)
    executed=executed_by_segment(snapshot.plan,snapshot.execution)
    events=[(row.date,row.order,row.line_code,row.id) for row in snapshot.plan.segments
            if row.date>=today and row.quantity>executed.get(row.id,0)]
    events.extend((row.date,row.order,switch.line_code,None) for switch in snapshot.plan.switches
                  for row in switch.allocations if row.date>=today)
    affected={}
    for day,order,line,segment in sorted(events,key=lambda value:(value[0],value[1],value[2],value[3] or '')):
        affected.setdefault(line,(day,segment))
    return affected


def adoption_conditions(snapshot):
    from .execution_rules import confirmed_day_capacity
    from .production_time import production_cutoff
    today=production_date(snapshot.time.as_of)
    affected=affected_events(snapshot)
    reviews={review.line_code:review for review in active_state_reviews(snapshot)}
    conditions=[]
    anchors=calculation_anchors(snapshot)
    pending=active_unfinished_jobs(snapshot)
    for line,(day,segment) in sorted(affected.items()):
        if line in pending:
            job=pending[line].unfinished_switch
            conditions.append(RuleIssue(code='remaining_switch_continuity',severity='warning',line_code=line,date=day,
                segment_id=segment,reason=f'原切机 {job.job_id} 尚余 {job.remaining_hours} 小时，日级容量不能证明连续条件，须核对后才能采用。'))
            continue
        if line in reviews:
            conditions.append(RuleIssue(code='line_state_review',severity='warning',line_code=line,date=day,
                segment_id=segment,reason=reviews[line].reason+' 当前起点待核对，首次切机尚未计入。'))
            continue
        records=[record for record in anchors if record.line_code==line]
        latest=max(records,key=lambda record:(record.effective_at,getattr(record,'priority',1),record.recorded_at)) if records else None
        current_capacity=confirmed_day_capacity(snapshot,line)
        intraday=latest is not None and day==today and latest.effective_at>production_cutoff(today)
        if current_capacity is not None or latest is not None and latest.recipe_code is not None and not intraday and record_is_current(snapshot,latest):
            continue
        reason=('当日现场状态时点不能替代剩余工时和顺序依据，请确认当前 Recipe 与剩余可用工时后重新预览。' if intraday else
                '现场状态后的历史计划切机或生产尚无执行依据，不能推定已经完成，请重新确认当前线体状态。' if latest is not None else
                '起点未知或仅有未确认的计划值，首次切机尚未计入。请补充线体状态现场确认后重新预览。')
        conditions.append(RuleIssue(code='initial_state_unknown',severity='warning',line_code=line,date=day,segment_id=segment,reason=reason))
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


def protect_unfinished_jobs(before, after, snapshot):
    originals = {row.id: row for row in before.switches}
    for record in active_unfinished_jobs(snapshot).values():
        job = record.unfinished_switch
        for switch in after.switches:
            if originals.get(switch.id) == switch:
                continue
            if switch.id != job.job_id and switch.residual_record_id != record.id:
                continue
            if (switch.id, switch.line_code, switch.from_recipe, switch.to_recipe, switch.residual_record_id) != (job.job_id, record.line_code, job.from_recipe, job.to_recipe, record.id):
                from .plans import PlanProblem
                raise PlanProblem(409, '普通计划修改不能重写原切机作业及目标，请通过现场状态更正并预览影响', line_code=record.line_code)


def resolved_reviews(records, issues):
    return [issue['id'] for issue in issues if issue.get('code') == 'line_state_review'
            and any(record.line_code == issue['line_code'] and record.effective_at >= datetime.fromisoformat(issue['review_after']) for record in records)]


def retained_state_reviews(before, after, snapshot):
    from .production_time import production_cutoff
    original = snapshot.model_copy(update={'plan': before})
    changed = snapshot.model_copy(update={'plan': after})
    latest = {}
    for record in sorted(calculation_anchors(snapshot), key=lambda row: (row.effective_at,getattr(row,'priority',1),row.recorded_at)):
        latest[record.line_code] = record
    reviews = []
    for line, record in latest.items():
        if record.recipe_code is None:
            continue
        if not record_is_current(original, record) and record_is_current(changed, record):
            since = production_cutoff(production_date(snapshot.time.as_of)).isoformat()
            identity = 'state-review-' + hashlib.sha256(f'{line}:{since}'.encode()).hexdigest()[:40]
            reviews.append({'id': identity, 'code': 'line_state_review', 'line_code': line,
                'review_after': since, 'blocks_adoption': False,
                'reason': '旧计划中的历史 Recipe 变化尚无执行依据。修改或移除计划不能代替现场确认，请核实当前线体状态。'})
    return reviews
