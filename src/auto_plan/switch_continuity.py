"""One semantic basis for actual remaining-work continuity and its audited operation."""
from datetime import timedelta
from fractions import Fraction
import hashlib
import json
from typing import Literal
from pydantic import Field, field_validator
from .exact import hours
from .input_lookup import InputIndex
from .input_models import InputRow, Code
from .plan_models import SwitchContinuityRecord, SwitchContinuityAudit
from .plan_operations import register_operation, ReplayResult
from .production_time import production_date, production_cutoff


GROUPS = {'job':'原切机作业','progress':'现场进度依据','allocations':'剩余切机安排','calendar':'日历与可用工时',
          'day_budgets':'当日剩余工时','competing_work':'同线生产与切机','execution':'执行事实','locks':'数量锁','applicability':'安排适用范围'}


def _ordered(rows):
    return sorted(rows,key=lambda row:json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')))


def _basis(snapshot, record, index):
    from .line_states import matches_residual_job
    job=record.unfinished_switch
    arranged=[switch for switch in snapshot.plan.switches if switch.id==job.job_id]
    allocations=sorted([row.model_dump(mode='json') for switch in arranged for row in switch.allocations],key=lambda row:(row['date'],row['order']))
    raw=[row for switch in arranged for row in switch.allocations]
    today=production_date(snapshot.time.as_of)
    complete=len(arranged)==1 and matches_residual_job(arranged[0],record) and sum((Fraction(row.hours) for row in raw),Fraction(0))==Fraction(job.remaining_hours)
    positive=sorted({row.date for row in raw if Fraction(row.hours)})
    consecutive=all(b-a==timedelta(days=1) for a,b in zip(positive,positive[1:]))
    unique=len({(row.date,row.order) for row in raw})==len(raw)
    applicable=all(max(today,production_date(record.effective_at),snapshot.time.window_start)<=row.date<=snapshot.time.window_end for row in raw)
    reasons=[]
    if not complete: reasons.append('请先按当前现场依据完整安排实际剩余切机工时。')
    if not applicable: reasons.append('剩余作业不能安排到已过去日期、现场观察之前或排程窗口以外。')
    if not unique: reasons.append('剩余切机分配的同日顺序重复，请先修正。')
    if not consecutive: reasons.append('剩余切机安排之间存在未安排作业的生产日，已知中断不能由连续性确认豁免。')
    eligible=complete and applicable and unique and consecutive
    first=min((row.date for row in raw),default=max(snapshot.time.window_start,production_date(record.effective_at)))
    last=max((row.date for row in raw),default=first)
    days=[]
    day=max(first,snapshot.time.window_start)
    while day<=min(last,snapshot.time.window_end):
        days.append(day);day+=timedelta(days=1)
    work=[]
    production=[row for row in snapshot.plan.segments if row.line_code==record.line_code]
    final=max(((row.date,row.order) for row in raw),default=(first,-1))
    handoff=min((row for row in production if (row.date,row.order)>final and (row.quantity or Fraction(row.hours))),key=lambda row:(row.date,row.order,row.id),default=None)
    def segment(row):
        return row.model_dump(mode='json')|{'recipe_code':index.models[row.model_code].recipe_code if row.model_code in index.models else None}
    for row in production:
        if first<=row.date<=last: work.append({'kind':'production',**segment(row)})
    for switch in snapshot.plan.switches:
        rows=[row for row in switch.allocations if first<=row.date<=last]
        if switch.line_code==record.line_code and switch.id!=job.job_id and rows:
            work.append({'kind':'switch','id':switch.id,'from_recipe':switch.from_recipe,'to_recipe':switch.to_recipe,
                         'allocations':_ordered([row.model_dump(mode='json') for row in rows]),'standard':index.switch(switch.from_recipe,switch.to_recipe)})
    recipe_months={(row.date.strftime('%Y-%m'),index.models[row.model_code].recipe_code) for row in production
                   if first<=row.date<=last and row.model_code in index.models}
    if handoff is not None and handoff.model_code in index.models:
        recipe_months.add((handoff.date.strftime('%Y-%m'),index.models[handoff.model_code].recipe_code))
    calendar={'days':[{'date':day.isoformat(),'holiday':index.holidays.get(day,False),'opening_exception':index.openings.get((day,record.line_code),False),
                      'maintenance':index.maintenance.get((day,record.line_code),0),'experiment':index.experiments.get((day,record.line_code),0),
                      'available_hours':str(index.day(day,record.line_code)['available_hours'])} for day in days],
              'capacities':[{'month':month,'recipe_code':recipe,**index.capacity(month,recipe,record.line_code)} for month,recipe in sorted(recipe_months)]}
    budgets=[]
    for day in days:
        observed=next((row for row in snapshot.execution.day_capacities if row.date==day and row.line_code==record.line_code
                       and row.residual_record_id==record.id and row.confirmed_at>=record.effective_at and row.confirmed_at<=snapshot.time.as_of),None)
        budgets.append({'date':day.isoformat(),'remaining_hours':observed.remaining_hours if observed else str(index.day(day,record.line_code)['available_hours']),
                        'residual_record_id':record.id})
    end=max(last,handoff.date if handoff is not None else last)
    start=production_date(record.effective_at)
    facts=[row.model_dump(mode='json')|{'recipe_code':index.models[row.model_code].recipe_code if row.model_code in index.models else None}
           for row in snapshot.execution.facts if row.line_code==record.line_code and start<=row.date<=end]
    covered=[]
    for row in snapshot.execution.coverage:
        if record.line_code in row.line_codes and row.start_date<=end and row.end_date>=start:
            covered.append({'start_date':max(start,row.start_date).isoformat(),'end_date':min(end,row.end_date).isoformat(),
                            'model_codes':sorted(row.model_codes),'blanks_are_zero':row.blanks_are_zero})
    selected={row.id for row in production if start<=row.date<=end}
    cutoff=snapshot.execution.cutoff
    if cutoff is not None:
        cutoff=min(max(cutoff,production_cutoff(start)),production_cutoff(end+timedelta(days=1))).isoformat()
    basis={'schema':1,'job':{'job_id':job.job_id,'line_code':record.line_code,'from_recipe':job.from_recipe,'to_recipe':job.to_recipe,'started_at':job.started_at.isoformat()},
           'progress':{'source_record_id':record.id,'effective_at':record.effective_at.isoformat(),'remaining_hours':job.remaining_hours,
                       'standard_hours':job.standard_hours,'completed_hours':job.completed_hours},
           'allocations':allocations,'calendar':calendar,'day_budgets':budgets,
           'competing_work':{'events':_ordered(work),'handoff':segment(handoff) if handoff is not None else None},
           'execution':{'facts':_ordered(facts),'coverage':_ordered(covered),'cutoff':cutoff,
                        'associations':_ordered([row.model_dump(mode='json') for row in snapshot.execution.associations if row.segment_id in selected]),
                        'association_cells':_ordered([row.model_dump(mode='json') for row in snapshot.execution.association_cells if row.line_code==record.line_code and start<=row.date<=end])},
           'locks':_ordered([row.model_dump(mode='json') for row in snapshot.locks if row.line_code==record.line_code and first<=row.date<=end]),
           'applicability':{'not_elapsed':applicable,'consecutive_days':consecutive,'unique_orders':unique,'complete':complete}}
    return basis,eligible,reasons


def _evidence(basis):
    job=basis['job'];progress=basis['progress']
    items={'job':[f"作业 {job['job_id']} · {job['from_recipe']} → {job['to_recipe']}",f"实际开始 {job['started_at']}"],
           'progress':[f"状态对应 {progress['effective_at']}",f"现场剩余 {progress['remaining_hours']} 小时"],
           'allocations':[f"{row['date']} · 顺序 {row['order']} · {row['hours']} 小时" for row in basis['allocations']],
           'calendar':[f"{row['date']} · 日历可用 {row['available_hours']} 小时 · 保养 {row['maintenance']} 小时 · 实验 {row['experiment']} 小时" for row in basis['calendar']['days']],
           'day_budgets':[f"{row['date']} · 可用预算 {row['remaining_hours']} 小时" for row in basis['day_budgets']],
           'competing_work':[f"同线安排 {len(basis['competing_work']['events'])} 项，后续生产已关联"],
           'execution':[f"相关累计事实 {len(basis['execution']['facts'])} 项，执行覆盖 {len(basis['execution']['coverage'])} 项"],
           'locks':[f"{row['date']} · {row['model_code']} · 固定投入 {row['quantity']}" for row in basis['locks']],
           'applicability':['已完整安排当前剩余工时' if basis['applicability']['complete'] else '当前剩余工时尚未完整安排']}
    return [{'key':key,'title':title,'items':items[key] or ['无']} for key,title in GROUPS.items()]


def continuity_details(snapshot):
    from .line_states import active_unfinished_jobs
    index=InputIndex(snapshot.inputs)
    prior={(row.line_code,row.job_id):row for row in snapshot.switch_continuity.records}
    result=[]
    for line,record in sorted(active_unfinished_jobs(snapshot).items()):
        job=record.unfinished_switch
        basis,eligible,reasons=_basis(snapshot,record,index)
        digest=hashlib.sha256(json.dumps(basis,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        old=prior.get((line,job.job_id))
        changed=[title for key,title in GROUPS.items() if old is not None and old.basis.get(key)!=basis[key]]
        valid=old is not None and eligible and old.basis_digest==digest
        status='valid' if valid else 'stale' if old is not None else 'missing' if eligible else 'unarranged'
        if valid: reasons=['现场连续性已确认，仍须通过完整计划校核。']
        elif changed: reasons=reasons+['以下依据已变化，请重新核对：'+'、'.join(changed)+'。']
        elif eligible: reasons=['日级工时不能证明连续条件，请核实现场可连续执行。']
        result.append({'job_id':job.job_id,'line_code':line,'source_record_id':record.id,'from_recipe':job.from_recipe,'to_recipe':job.to_recipe,
                       'remaining_hours':hours(job.remaining_hours).model_dump(mode='json'),'allocations':basis['allocations'],'basis_digest':digest,'basis':basis,
                       'status':status,'can_confirm':eligible,'reasons':reasons,'changed_groups':changed,'evidence':_evidence(basis),
                       'confirmation':old.model_dump(mode='json') if old is not None else None})
    return result


class ConfirmSwitchContinuity(InputRow):
    kind: Literal['confirm_switch_continuity']
    job_id: Code
    line_code: Code
    source_record_id: Code
    expected_basis_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    source: str = Field(min_length=1,max_length=500)
    expected_confirmation_id: Code | None = None
    reason: str | None = Field(default=None,max_length=2000)

    @field_validator('source')
    @classmethod
    def nonblank(cls,value):
        if not value.strip(): raise ValueError('请填写连续性确认的现场来源')
        return value.strip()


def confirm_switch_continuity(plan,command,context):
    detail=next((row for row in continuity_details(context.snapshot) if row['job_id']==command.job_id and row['line_code']==command.line_code),None)
    if detail is None or detail['source_record_id']!=command.source_record_id:
        raise ValueError('原切机现场依据已变化，请重新预览核对')
    if detail['basis_digest']!=command.expected_basis_digest:
        raise ValueError('剩余切机安排或现场条件已变化，请重新预览核对')
    if not detail['can_confirm']: raise ValueError('；'.join(detail['reasons']))
    old=next((row for row in context.switch_continuity.records if row.job_id==command.job_id and row.line_code==command.line_code),None)
    if old is not None and (old.basis_digest,old.source)==(command.expected_basis_digest,command.source): return ReplayResult(plan)
    if old is not None and (old.id!=command.expected_confirmation_id or not command.reason or not command.reason.strip()):
        raise ValueError('重新核对连续性须使用当前确认记录并填写原因')
    if old is None and command.expected_confirmation_id is not None: raise ValueError('连续性更正来源不存在，请重新核对')
    identity='continuity-'+hashlib.sha256(f'{context.action_id}:{command.line_code}:{command.job_id}'.encode()).hexdigest()[:40]
    record=SwitchContinuityRecord(id=identity,job_id=command.job_id,line_code=command.line_code,source_record_id=command.source_record_id,
        basis_digest=detail['basis_digest'],basis=detail['basis'],source=command.source,actor=context.actor,recorded_at=context.time.as_of)
    audit=SwitchContinuityAudit(action_id=context.action_id,actor=context.actor,recorded_at=context.time.as_of,reason=command.reason.strip() if command.reason else None,
        changes=[{'before':old.model_dump(mode='json') if old else None,'after':record.model_dump(mode='json')}])
    updated=context.switch_continuity.model_copy(update={'records':[row for row in context.switch_continuity.records if (row.line_code,row.job_id)!=(command.line_code,command.job_id)]+[record],
        'audit':context.switch_continuity.audit+[audit]})
    return ReplayResult(plan,context_updates={'switch_continuity':updated})


register_operation('confirm_switch_continuity',ConfirmSwitchContinuity,confirm_switch_continuity)
