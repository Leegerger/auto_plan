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
    if command.cutoff is not None and (command.cutoff.tzinfo is None or command.cutoff.utcoffset() is None):
        raise ValueError('执行截止时点必须包含时区')
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
    changed_cells={key(row) for row in command.rows if rows[key(row)]!=next((r for r in context.execution.facts if key(r)==key(row)),None)}
    associations=execution.associations
    if command.associations is not None:
        identities=[r.segment_id for r in command.associations]
        segments={r.id:r for r in plan.segments}
        historical={r.segment_id:r for r in execution.associations if r.segment_id not in segments}
        if len(identities)!=len(set(identities)) or any(r.segment_id not in segments and historical.get(r.segment_id)!=r for r in command.associations):
            raise ValueError('分段关联重复或生产段已不存在')
        totals=defaultdict(int)
        for association in command.associations:
            if association.segment_id not in segments: continue
            segment=segments[association.segment_id]
            if association.quantity>segment.quantity: raise ValueError('分段执行关联超过该段投入数量')
            totals[key(segment)]+=association.quantity
        from .execution_rules import executed_by_segment
        assigned=executed_by_segment(plan,context.execution)
        prior=defaultdict(int);planned=defaultdict(int)
        for segment in plan.segments:
            prior[key(segment)]+=assigned.get(segment.id,0)
            planned[key(segment)]+=segment.quantity
        old_cells={key(r) for r in context.execution.facts}
        for cell in rows.keys()|totals.keys():
            actual=rows[cell].quantity if cell in rows else 0
            minimum=min(actual,prior[cell] if cell in old_cells else planned[cell])
            if not minimum<=totals[cell]<=min(actual,planned[cell]):
                raise ValueError('分段执行关联必须保留已确认计划内总量，且不能超过累计事实或分段投入总量；超计划量另列')
        associations=command.associations+[r for identity,r in historical.items() if identity not in identities]
        correction|=sorted(associations,key=lambda r:r.segment_id)!=sorted(execution.associations,key=lambda r:r.segment_id)
    elif changed_cells:
        associations=[r for r in execution.associations if not any(s.id==r.segment_id and key(s) in changed_cells for s in plan.segments)]
        for cell in sorted(changed_cells):
            remaining=rows[cell].quantity
            for segment in sorted([r for r in plan.segments if key(r)==cell],key=lambda r:(r.order,r.id)):
                quantity=min(segment.quantity,remaining);remaining-=quantity
                associations.append(ExecutionAssociation(segment_id=segment.id,quantity=quantity))
    associations=sorted(associations,key=lambda r:r.segment_id)
    if associations!=sorted(execution.associations,key=lambda r:r.segment_id):
        changes.append({'family':'associations','before':[r.model_dump(mode='json') for r in execution.associations],
                        'after':[r.model_dump(mode='json') for r in associations]})
    execution=execution.model_copy(update={'associations':associations})
    associated={key(r):r for r in execution.association_cells}
    associated.update({cell:rows[cell] for cell in changed_cells})
    if command.associations is not None:
        for association in command.associations:
            segment=next((r for r in plan.segments if r.id==association.segment_id),None)
            if segment is None: continue
            if key(segment) not in rows: raise ValueError('分段执行关联须对应已登记的累计事实')
            associated[key(segment)]=rows[key(segment)]
    execution=execution.model_copy(update={'association_cells':list(associated.values())})
    execution=execution.model_copy(update={'facts':list(rows.values())})
    for field in ('coverage','cutoff'):
        value=getattr(command,field)
        if value is not None and value!=getattr(execution,field):
            old=getattr(execution,field)
            if field=='cutoff': correction|=old is not None and value<old
            elif old:
                def covered(day,line,model,coverages):
                    return any(r.blanks_are_zero and r.start_date<=day<=r.end_date and line in r.line_codes and model in r.model_codes for r in coverages)
                from datetime import timedelta
                correction|=any(not covered(r.start_date+timedelta(days=n),line,model,value)
                    for r in old if r.blanks_are_zero for n in range((r.end_date-r.start_date).days+1) for line in r.line_codes for model in r.model_codes)
            serialized=lambda v:[r.model_dump(mode='json') for r in v] if isinstance(v,list) else v.isoformat() if v else None
            changes.append({'family':field,'before':serialized(old),'after':serialized(value)})
            execution=execution.model_copy(update={field:value})
    if correction and not (command.reason and command.reason.strip()): raise ValueError('纠错必须填写原因')
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
        prior_associations={r.segment_id:r.quantity for r in context.execution.associations}
        new_associations={r.segment_id:r.quantity for r in execution.associations}
        changed_associations={identity for identity in prior_associations.keys()|new_associations.keys() if prior_associations.get(identity)!=new_associations.get(identity)}
        earlier_association=any(r.date.isoformat()<week for r in plan.segments if r.id in changed_associations)
        coverage_change=any(change.get('family') in {'coverage','cutoff'} for change in changes) and any(c.start_date.isoformat()<week for c in execution.coverage)
        if affected or earlier_association or coverage_change:
            records[week]=record|{'pending_review':True,'review_action':context.action_id}
            issues.append({'id':'reset-review-'+week,'code':'reset_pending_review','week':week,'reason':'重置截止前的执行事实已纠错，请重新确认同批在制品与最终净余额','blocks_adoption':True})
    if records:
        from .reset_operations import refresh_targets
        records={week:refresh_targets(record,plan,execution,context.time,lines) for week,record in records.items()}
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
