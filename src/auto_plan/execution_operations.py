"""Audited cumulative execution commands in the shared transactional queue."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from typing import Literal
from pydantic import Field
from .input_models import InputRow, Code
from .exact import Exact
from .plan_models import PlanCell, ExecutionCoverage, ExecutionAssociation, ExecutionAudit
from .plan_operations import register_operation, ReplayResult
from .production_time import production_date


class RecordExecution(InputRow):
    kind: Literal['record_execution']
    rows: list[PlanCell] = Field(default_factory=list)
    coverage: list[ExecutionCoverage] | None = None
    cutoff: datetime | None = None
    associations: list[ExecutionAssociation] | None = None
    reason: str | None = None


def record_execution(plan,command,context):
    execution=context.execution.model_copy(deep=True)
    key=lambda r:(r.date,r.line_code,r.model_code)
    rows={key(r):r for r in execution.facts}
    keys=[key(r) for r in command.rows]
    if len(keys)!=len(set(keys)): raise ValueError('累计事实组合重复，不能重复相加')
    lines={r.code for r in context.inputs.masters.lines};models={r.code for r in context.inputs.masters.models}
    today=production_date(context.time.as_of)
    changes=[];correction=False
    for row in command.rows:
        if row.line_code not in lines or row.model_code not in models: raise ValueError('执行事实引用不存在的线体或机种')
        if row.date>today: raise ValueError('不能登记未来生产日的执行事实')
        old=rows.get(key(row))
        if old==row: continue
        correction|=old is not None
        changes.append({'family':'fact','before':old.model_dump(mode='json') if old else None,'after':row.model_dump(mode='json')})
        rows[key(row)]=row
    if command.associations is not None and command.associations!=execution.associations:
        correction=True
        changes.append({'family':'associations','before':[r.model_dump(mode='json') for r in execution.associations],
                        'after':[r.model_dump(mode='json') for r in command.associations]})
        identities=[r.segment_id for r in command.associations]
        segments={r.id:r for r in plan.segments}
        if len(identities)!=len(set(identities)) or set(identities)-segments.keys(): raise ValueError('分段关联重复或生产段已不存在')
        totals=defaultdict(int)
        for association in command.associations:
            segment=segments[association.segment_id]
            if association.quantity>segment.quantity: raise ValueError('分段执行关联超过该段投入数量')
            totals[key(segment)]+=association.quantity
        for cell,total in totals.items():
            expected=min(rows[cell].quantity,sum(r.quantity for r in plan.segments if key(r)==cell)) if cell in rows else -1
            if total!=expected: raise ValueError('分段执行关联必须完整保留累计事实对应的计划内总量，超计划量另列')
        execution=execution.model_copy(update={'associations':command.associations})
    changed_cells={key(row) for row in command.rows if rows[key(row)]!=next((r for r in context.execution.facts if key(r)==key(row)),None)}
    if changed_cells and command.associations is None:
        associations=[r for r in execution.associations if not any(s.id==r.segment_id and key(s) in changed_cells for s in plan.segments)]
        for cell in changed_cells:
            remaining=rows[cell].quantity
            for segment in sorted([r for r in plan.segments if key(r)==cell],key=lambda r:(r.order,r.id)):
                quantity=min(segment.quantity,remaining);remaining-=quantity
                associations.append(ExecutionAssociation(segment_id=segment.id,quantity=quantity))
        execution=execution.model_copy(update={'associations':associations})
    associated={key(r):r for r in execution.association_cells}
    associated.update({cell:rows[cell] for cell in changed_cells})
    if command.associations is not None:
        for association in command.associations:
            segment=next(r for r in plan.segments if r.id==association.segment_id)
            associated[key(segment)]=rows[key(segment)]
    execution=execution.model_copy(update={'association_cells':list(associated.values())})
    if correction and not (command.reason and command.reason.strip()): raise ValueError('纠错必须填写原因')
    execution=execution.model_copy(update={'facts':list(rows.values())})
    for field in ('coverage','cutoff'):
        value=getattr(command,field)
        if value is not None and value!=getattr(execution,field):
            changes.append({'family':field,'before':str(getattr(execution,field)),'after':str(value)})
            execution=execution.model_copy(update={field:value})
    if execution.cutoff is not None:
        if execution.cutoff.tzinfo is None or execution.cutoff.utcoffset() is None or execution.cutoff>context.time.as_of:
            raise ValueError('执行截止时点必须包含时区且不晚于计算时点')
        if any(r.date>production_date(execution.cutoff) for r in execution.facts): raise ValueError('执行事实不在已确认截止时点内')
    for coverage in execution.coverage:
        if set(coverage.line_codes)-lines or set(coverage.model_codes)-models: raise ValueError('执行覆盖引用不存在的编码')
    if not changes: return ReplayResult(plan)
    execution.audit.append(ExecutionAudit(action_id=context.action_id,actor=context.actor,recorded_at=context.time.as_of,reason=command.reason,changes=changes))
    lineage={};issues=[]
    records=deepcopy(context.lineage.get('resets',{}))
    for week,record in records.items():
        if not record.get('effective_adoption'): continue
        affected=[change for change in changes if change.get('family')=='fact' and change.get('after',{}).get('date','9999')<week]
        association_change=any(change.get('family')=='associations' for change in changes)
        earlier_association=association_change and any(r.date.isoformat()<week for r in plan.segments if any(a.segment_id==r.id for a in execution.associations))
        coverage_change=any(change.get('family') in {'coverage','cutoff'} for change in changes) and any(c.start_date.isoformat()<week for c in execution.coverage)
        if affected or earlier_association or coverage_change:
            records[week]=record|{'pending_review':True,'review_action':context.action_id}
            issues.append({'id':'reset-review-'+week,'code':'reset_pending_review','week':week,'reason':'重置截止前的执行事实已纠错，请重新确认同批在制品与最终净余额','blocks_adoption':True})
    if issues:
        lineage={'resets':records}
        current=context.lineage.get('reset_preview')
        if current: lineage['reset_preview']=records.get(current['production_week'],current)
    return ReplayResult(plan,context_updates={'execution':execution},lineage=lineage,issues=issues)


register_operation('record_execution',RecordExecution,record_execution)


class CapacityConfirmation(InputRow):
    line_code: Code
    remaining_hours: Exact
    current_recipe: Code


class ConfirmDayCapacity(InputRow):
    kind: Literal['confirm_day_capacity']
    confirmations: list[CapacityConfirmation] = Field(min_length=1)
    observed_at: datetime


def confirm_capacity(plan,command,context):
    from fractions import Fraction
    from .plan_models import CurrentDayCapacity
    from .execution_rules import capacity_basis
    lines={r.code for r in context.inputs.masters.lines};recipes={r.code for r in context.inputs.masters.recipes}
    if command.observed_at.tzinfo is None or command.observed_at.utcoffset() is None or command.observed_at>context.time.as_of or production_date(command.observed_at)!=production_date(context.time.as_of):
        raise ValueError('剩余工时确认必须属于当前生产日且不晚于计算时点')
    if len({r.line_code for r in command.confirmations})!=len(command.confirmations): raise ValueError('同一线体剩余工时不能重复确认')
    execution=context.execution
    capacities={r.line_code:r for r in execution.day_capacities if r.date==production_date(context.time.as_of)}
    for row in command.confirmations:
        if row.line_code not in lines or row.current_recipe not in recipes: raise ValueError('请确认有效线体和当前 Recipe')
        try: hours=Fraction(row.remaining_hours)
        except (ValueError,ZeroDivisionError): raise ValueError('剩余工时必须为精确非负数') from None
        if not 0<=hours<=24: raise ValueError('剩余可用工时必须在零到24小时之间')
        capacities[row.line_code]=CurrentDayCapacity(date=production_date(context.time.as_of),line_code=row.line_code,remaining_hours=str(hours),
            current_recipe=row.current_recipe,confirmed_at=command.observed_at,basis_digest=capacity_basis(context))
    updated=execution.model_copy(update={'day_capacities':list(capacities.values())})
    from dataclasses import replace
    from .execution_rules import execution_certificate
    from .manual_operations import EditingScope
    revised=replace(context,snapshot=context.snapshot.model_copy(update={'execution':updated}))
    scope=EditingScope(start_date=production_date(context.time.as_of),end_date=production_date(context.time.as_of),line_codes=[r.line_code for r in command.confirmations])
    try: plan=execution_certificate(plan,revised,scope)
    except ValueError as error:
        return ReplayResult(plan,context_updates={'execution':updated},issues=[{'code':'day_capacity_review','reason':str(error),'blocks_adoption':True}])
    return ReplayResult(plan,context_updates={'execution':updated},resolve_issues=[r['id'] for r in context.issues if r.get('code')=='day_capacity_review'])


register_operation('confirm_day_capacity',ConfirmDayCapacity,confirm_capacity)
