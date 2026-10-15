"""Original-source weekly targets and reviewed reset corrections."""
from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import hashlib
import json
from typing import Literal
from pydantic import Field
from .input_models import InputRow, Pieces
from .plan_models import Plan, ResetContext, ProductionSegment, ExecutionCoverage
from .plan_operations import register_operation, ReplayResult
from .manual_operations import EditingScope, derived_id, _ordered_insert, CapacityWitnessError
from .execution_rules import active_plan, executed_by_segment, execution_certificate, confirmed_day_capacity
from .production_time import production_date, production_week, production_cutoff
from .rules import evaluate
from .input_lookup import InputIndex


class ResetChoice(InputRow):
    revision: Pieces
    retain: bool = Field(strict=True)


class WeeklyReset(InputRow):
    kind: Literal['weekly_reset']
    source_adoption: Pieces
    snapshot: ResetContext
    correction: bool = False
    choices: list[ResetChoice] = Field(default_factory=list)
    scope: EditingScope


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _known(execution,day,line,model,now):
    cutoff=execution.cutoff
    if cutoff is None or cutoff>now or day>production_date(cutoff): return False
    return any((r.date,r.line_code,r.model_code)==(day,line,model) for r in execution.facts) or any(
        c.blanks_are_zero and c.start_date<=day<=c.end_date and line in c.line_codes and model in c.model_codes for c in execution.coverage)


def _actual_known(execution,model,week,lines,now):
    last=min(production_date(now),week+timedelta(days=6))
    day=week
    while day<=last:
        if not all(_known(execution,day,line,model,now) for line in lines): return False
        day+=timedelta(days=1)
    return True


def refresh_targets(record,plan,execution,time,lines):
    record=deepcopy(record);today=production_date(time.as_of)
    if record.get('production_week')!=time.window_start.isoformat(): return record
    for target in record.get('targets',[]):
        model=target['model_code'];week=date.fromisoformat(target['week'])
        known=_actual_known(execution,model,week,lines,time.as_of)
        placed=_valid_remaining(plan,execution,model,week,today)
        if not known:
            target.update(actual=None,actual_known=False,remaining_target=None,planned_remaining=placed,
                actual_over_target=None,unmet_increase=None,unmet_reduction=None)
            continue
        actual=sum(r.quantity for r in execution.facts if r.model_code==model and production_week(r.date)==week)
        remaining=max(0,target['target']-actual)
        target.update(actual=actual,actual_known=True,remaining_target=remaining,planned_remaining=placed,
            actual_over_target=max(0,actual-target['target']),unmet_increase=max(0,remaining-placed),unmet_reduction=max(0,placed-remaining))
    return record


def refresh_lineage(lineage,plan,snapshot):
    lineage=deepcopy(lineage)
    record=lineage.get('resets',{}).get(snapshot.time.window_start.isoformat())
    if record:
        record=refresh_targets(record,plan,snapshot.execution,snapshot.time,{r.code for r in snapshot.inputs.masters.lines})
        lineage['resets'][record['production_week']]=record
        lineage['reset_preview']=record
    return lineage


def _week_covered(source,coverage,model,week,lines):
    return all(any(c.blanks_are_zero and c.start_date<=week+timedelta(days=n)<=c.end_date and line in c.line_codes and model in c.model_codes for c in coverage)
               for n in range(7) for line in lines)


def _snapshot(context,plan,reset):
    return context.snapshot.model_copy(update={'plan':plan,'reset':reset})


def _score(context,plan,reset):
    result=evaluate(_snapshot(context,plan,reset))
    weeks=sorted({r.week for r in result.weekly_results})
    gaps=tuple(sum(r.demand-r.on_time for r in result.weekly_results if r.week==week) for week in weeks)
    final=tuple(sum(r.gap for r in result.weekly_results if r.week==week) for week in weeks)
    return gaps,final,result.switch_count,Fraction(result.stability.weighted_cost),result.stability.order_change_count


def _witness(before,candidate,context,scope,reset):
    from dataclasses import replace
    local=replace(context,snapshot=context.snapshot.model_copy(update={'plan':before,'reset':reset}))
    result=execution_certificate(candidate,local,scope)
    from .plans import protect
    protect(before,result,local.snapshot)
    if not evaluate(_snapshot(context,result,reset)).capacity_valid: raise CapacityWitnessError('调整后的共享工时、Recipe 或锁定约束不满足')
    return result


def _valid_remaining(plan,execution,model,week,today):
    assigned=executed_by_segment(plan,execution)
    return sum(max(0,r.quantity-(assigned.get(r.id,0) if r.date==today else 0)) for r in plan.segments
               if r.model_code==model and production_week(r.date)==week and r.date>=today)


def _reduce(plan,amount,model,week,command,context):
    from .plans import PlanProblem
    today=production_date(context.time.as_of)
    while amount:
        assigned=executed_by_segment(plan,context.execution)
        locked={(r.date,r.line_code,r.model_code) for r in context.locks}
        candidates=[]
        for row in plan.segments:
            if row.model_code!=model or production_week(row.date)!=week or row.date<today or not command.scope.contains(row) or (row.date,row.line_code,row.model_code) in locked: continue
            reducible=row.quantity-assigned.get(row.id,0)
            take=min(amount,reducible)
            if not take: continue
            candidate=plan.model_copy(update={'segments':[r.model_copy(update={'quantity':r.quantity-take}) if r.id==row.id else r for r in plan.segments if r.id!=row.id or r.quantity>take]},deep=True)
            local=command.scope.model_copy(update={'start_date':max(today,week,command.scope.start_date),'end_date':min(week+timedelta(days=6),command.scope.end_date),'line_codes':[row.line_code]})
            try: candidate=_witness(plan,candidate,context,local,command.snapshot)
            except (ValueError,PlanProblem): continue
            score=_score(context,candidate,command.snapshot)
            candidates.append(((score[0],-row.date.toordinal(),score[2],1,row.id),candidate,take))
        if not candidates: break
        _,plan,take=min(candidates,key=lambda item:item[0]);amount-=take
    return plan


def _increase(plan,amount,model,week,command,context):
    from .plans import PlanProblem
    index=InputIndex(context.inputs);today=production_date(context.time.as_of)
    recipe=index.models[model].recipe_code
    locked={(r.date,r.line_code,r.model_code) for r in context.locks}
    while amount:
        candidates=[]
        day=max(week,today,command.scope.start_date)
        while day<=min(week+timedelta(days=6),command.scope.end_date):
            for line in command.scope.line_codes:
                if (day,line,model) in locked or day==today and confirmed_day_capacity(context,line) is None: continue
                if not index.lines[line].active or not index.models[model].active or not index.recipes[recipe].active: continue
                capacity=index.capacity(day.strftime('%Y-%m'),recipe,line)
                if capacity['state']!='known' or not capacity['pieces']: continue
                daily=sorted([r for r in plan.segments if r.date==day and r.line_code==line],key=lambda r:(r.order,r.id))
                positions=range(len(daily)+1)
                for position in positions:
                    identity=derived_id(context,f'reset:{model}:{week}:{day}:{line}:{position}')
                    local=command.scope.model_copy(update={'start_date':max(today,week,command.scope.start_date),'end_date':min(week+timedelta(days=6),command.scope.end_date),'line_codes':[line]})
                    def proposal(quantity):
                        existing=next((r for r in plan.segments if r.id==identity),None)
                        if existing:
                            working=plan.model_copy(update={'segments':[r.model_copy(update={'quantity':r.quantity+quantity}) if r.id==identity else r for r in plan.segments]},deep=True)
                        else:
                            added=ProductionSegment(id=identity,date=day,line_code=line,order=0,model_code=model,quantity=quantity,hours='0')
                            working=plan.model_copy(deep=True);working.segments.append(added);working=_ordered_insert(working,added,position)
                        return _witness(plan,working,context,local,command.snapshot)
                    low,high,best=0,min(amount,capacity['pieces']+1),None
                    while low<high:
                        take=(low+high+1)//2
                        try: candidate=proposal(take)
                        except (ValueError,PlanProblem): high=take-1
                        else: low,best=take,candidate
                    if low:
                        candidate=best if best is not None else proposal(low)
                        candidates.append((_score(context,candidate,command.snapshot)+(day,line,position),candidate,low))
            day+=timedelta(days=1)
        if not candidates: break
        _,plan,take=min(candidates,key=lambda item:item[0]);amount-=take
    return plan


def _adjustments(context,start_revision):
    changes=[]
    for number in range(start_revision+1,context.current_revision+1):
        record=context.history.get_revision(number)
        if any(r.get('kind')=='weekly_reset' for r in record.get('operations',[])) or record.get('kind')=='reset_reference': continue
        parent=context.history.get_revision(record.get('parent_revision',number-1))
        if parent['snapshot']['plan']!=record['snapshot']['plan']:
            changes.append({'revision':number,'operations':record.get('operations',[]),
                'before':parent['snapshot']['plan'],'after':record['snapshot']['plan']})
    return changes


def _apply_delta(plan,change,scope,context):
    before=Plan.model_validate(change['before']);after=Plan.model_validate(change['after'])
    rows={r.id:r for r in plan.segments};old={r.id:r for r in before.segments};new={r.id:r for r in after.segments}
    transfer=bool(change.get('operations')) and all(r.get('kind') in {'split_segment','move_segment','swap_segments'} for r in change['operations'])
    represented=defaultdict(int)
    if transfer:
        for identity in new.keys()-old.keys():
            current=rows.get(identity);target=new[identity]
            if current and (current.date,current.line_code,current.model_code)==(target.date,target.line_code,target.model_code):
                represented[target.model_code]+=min(current.quantity,target.quantity)
    for identity in old.keys()|new.keys():
        a,b=old.get(identity),new.get(identity)
        if a==b: continue
        if any(row and not scope.contains(row) for row in (a,b)): continue
        current=rows.get(identity)
        if a is None:
            if current is not None:
                if (current.date,current.line_code,current.model_code)!=(b.date,b.line_code,b.model_code):
                    raise ValueError('保留调整的生产段识别号与受保护位置冲突，请重新核对')
                rows[identity]=current.model_copy(update={'quantity':max(current.quantity,b.quantity) if transfer else current.quantity+b.quantity})
            else: rows[identity]=b
        elif b is None:
            if current is not None: rows.pop(identity)
        elif current is None:
            if b.quantity<a.quantity and transfer and represented[a.model_code]>=a.quantity-b.quantity:
                represented[a.model_code]-=a.quantity-b.quantity
                continue
            if b.quantity<a.quantity: raise ValueError('保留完整调整所需的原位置待执行量不足，不能拆散或补造该动作')
            if b.quantity-a.quantity>0: rows[identity]=b.model_copy(update={'quantity':b.quantity-a.quantity})
            elif (a.date,a.line_code,a.model_code)!=(b.date,b.line_code,b.model_code): rows[identity]=b
        else:
            delta=b.quantity-a.quantity
            if transfer and delta<0:
                covered=min(-delta,represented[a.model_code]);represented[a.model_code]-=covered;delta+=covered
            quantity=current.quantity+delta
            if quantity<0: raise ValueError('撤回或保留的调整超过更正后可用数量，请重新核对')
            rows[identity]=current.model_copy(update={'quantity':quantity,'date':b.date,'line_code':b.line_code,'model_code':b.model_code,'order':b.order})
    return plan.model_copy(update={'segments':list(rows.values())})


def _apply_explicit_metadata(plan,change,scope,context,*,retract=False):
    """Retain declared patch metadata too, rather than silently dropping half an action."""
    before=Plan.model_validate(change['before']);after=Plan.model_validate(change['after'])
    operations=change.get('operations',[])
    fields=set()
    for operation in operations:
        if operation.get('kind')=='replace_plan': fields.update({'switches','ordinary_idle','initial_states'})
        elif operation.get('kind')=='patch_plan':
            fields.update(field for field in ('switches','ordinary_idle','initial_states') if field in operation or 'remove_'+('idle' if field=='ordinary_idle' else field) in operation)
    for field in fields:
        identity=(lambda r:r.id) if field=='switches' else (lambda r:(r.date,r.line_code)) if field=='ordinary_idle' else (lambda r:r.line_code)
        old={identity(r):r for r in getattr(before,field)};new={identity(r):r for r in getattr(after,field)}
        current={identity(r):r for r in getattr(plan,field)}
        def inside(row):
            if row.line_code not in scope.line_codes: return False
            if field=='initial_states': return scope.start_date==context.time.window_start
            dates=[a.date for a in row.allocations] if field=='switches' else [row.date]
            return all(scope.start_date<=day<=scope.end_date for day in dates)
        for key in old.keys()|new.keys():
            a,b=old.get(key),new.get(key)
            if a==b: continue
            if not all(inside(row) for row in (a,b) if row):
                if retract: raise ValueError('撤回完整调整涉及范围外工时或 Recipe 状态，请扩大范围后重新预览')
                continue
            target=a if retract else b
            if target is None: current.pop(key,None)
            else: current[key]=target
        plan=plan.model_copy(update={field:list(current.values())})
    return plan


def weekly_reset(plan,command,context):
    from .plans import PlanProblem, protect
    week=context.time.window_start;key=week.isoformat();reset=command.snapshot
    if reset.production_week!=week or reset.cutoff!=production_cutoff(week): raise ValueError('重置快照必须为当前生产周周一08:00的同批在制品与最终净余额')
    if command.scope.start_date<context.time.window_start or command.scope.end_date>context.time.window_end: raise ValueError('重置调整范围必须位于当前十五周窗口')
    index=InputIndex(context.inputs)
    if set(command.scope.line_codes)-index.lines.keys() or set(command.scope.model_codes)-index.models.keys(): raise ValueError('重置调整范围引用不存在的编码')
    external=context.lineage.get('external_reset')
    if external and external.get('production_week')==key and external.get('already_applied'):
        raise ValueError('首次接入周已在外部重置，不能再次系统重置或更正；请明确手动调整草稿')
    records=deepcopy(context.lineage.get('resets',{}));existing=records.get(key)
    if existing and existing.get('effective_adoption') and not command.correction:
        return ReplayResult(plan,lineage={'reset_reference':{'effective_adoption':existing['effective_adoption'],'effective_revision':existing['effective_revision']}})
    if command.correction and not (existing and existing.get('effective_adoption')): raise ValueError('本周尚无生效的系统重置，不能更正')
    source_number=existing['source_adoption'] if command.correction else context.latest_adoption
    if not source_number or command.source_adoption!=source_number:
        raise PlanProblem(409,'重置来源采用版已变化，请重新核对来源与快照',source_adoption=source_number,latest_adoption=context.latest_adoption)
    source_record=context.history.get_adoption(source_number)
    source=Plan.model_validate(source_record['snapshot']['plan'])
    participating={r.code for r in index.models.values() if r.active}|{r.model_code for r in source.segments}|{r.model_code for r in context.execution.facts if r.date>=week}
    codes=[r.model_code for r in reset.models]
    if len(codes)!=len(set(codes)) or set(codes)!=participating: raise ValueError('同批快照须逐机种完整填写在制品与最终净余额，包含零值')
    snapshot_data=reset.model_dump(mode='json');snapshot_digest=digest(snapshot_data)
    batches=deepcopy(context.lineage.get('reset_batches',{}));versions=batches.setdefault(reset.batch_id,[])
    batch_revision=next((n+1 for n,r in enumerate(versions) if r['digest']==snapshot_digest),None)
    if batch_revision is None:
        versions.append({'digest':snapshot_digest,'snapshot':snapshot_data});batch_revision=len(versions)
    result=active_plan(source,context.time)
    projected_current=active_plan(plan,context.time)
    inside_switch=lambda s:s.line_code in command.scope.line_codes and all(command.scope.start_date<=a.date<=command.scope.end_date for a in s.allocations)
    result=result.model_copy(update={
        'segments':[r for r in result.segments if command.scope.contains(r)]+[r for r in plan.segments if context.time.window_start<=r.date<=context.time.window_end and not command.scope.contains(r)],
        'switches':[r for r in result.switches if inside_switch(r)]+[r for r in projected_current.switches if not inside_switch(r)],
        'ordinary_idle':[r for r in result.ordinary_idle if r.line_code in command.scope.line_codes and command.scope.start_date<=r.date<=command.scope.end_date]+[r for r in plan.ordinary_idle if context.time.window_start<=r.date<=context.time.window_end and not(r.line_code in command.scope.line_codes and command.scope.start_date<=r.date<=command.scope.end_date)],
        'initial_states':projected_current.initial_states,'current_day_witnesses':projected_current.current_day_witnesses})
    today=production_date(context.time.as_of)
    current_executed=executed_by_segment(plan,context.execution)
    source_executed=executed_by_segment(result,context.execution)
    locked={(r.date,r.line_code,r.model_code) for r in context.locks if week<=r.date<=context.time.window_end}
    current_cells=defaultdict(list)
    for row in plan.segments: current_cells[row.date,row.line_code,row.model_code].append(row)
    # Current locks and executed identities win over their old source placements.
    result=result.model_copy(update={'segments':[r for r in result.segments if (r.date,r.line_code,r.model_code) not in locked]+[
        r for cell,rows in current_cells.items() if cell in locked for r in rows if week<=r.date<=context.time.window_end]})
    by_id={r.id:r for r in result.segments}
    for row in plan.segments:
        executed=current_executed.get(row.id,0)
        if not executed or not week<=row.date<=context.time.window_end: continue
        old=by_id.get(row.id)
        by_id[row.id]=(old if old and (old.date,old.line_code,old.model_code)==(row.date,row.line_code,row.model_code) and old.quantity>=executed else row.model_copy(update={'quantity':executed}))
    expired=[];segments=[];issues=[]
    for row in by_id.values():
        if row.date<today:
            known=_known(context.execution,row.date,row.line_code,row.model_code,context.time.as_of)
            if not known: issues.append({'code':'reset_execution_unknown','reason':'过去计划的执行事实或覆盖截止尚未确认','date':row.date.isoformat(),'line_code':row.line_code,'model_code':row.model_code,'blocks_adoption':True})
            executed=current_executed.get(row.id,source_executed.get(row.id,0))
            expired.append({'segment_id':row.id,'date':row.date.isoformat(),'model_code':row.model_code,'quantity':max(0,row.quantity-executed) if known else None,'destination_week':production_week(row.date).isoformat(),'destination':'原周待重新安排，不计预计产出'})
            if known and command.scope.contains(row) and (row.date,row.line_code,row.model_code) not in locked:
                row=row.model_copy(update={'quantity':executed})
        if row.quantity: segments.append(row)
    result=result.model_copy(update={'segments':segments})
    targets=[];residual=[];model_rows={r.model_code:r for r in reset.models}
    lines=set(index.lines)
    for model in sorted(model_rows):
        balance=model_rows[model].net_balance;stopped=False
        for ordinal in range(15):
            target_week=week+timedelta(days=ordinal*7)
            covered=_week_covered(source,[ExecutionCoverage.model_validate(r) for r in source_record.get('coverage',[])],model,target_week,lines)
            if stopped or not covered:
                if not stopped: residual.append({'model_code':model,'week':target_week.isoformat(),'quantity':max(0,balance),'state':'来源覆盖不完整，新周另行核对排程'})
                stopped=True;continue
            source_input=sum(r.quantity for r in source.segments if r.model_code==model and production_week(r.date)==target_week)
            target=max(0,source_input-balance);carry=max(0,balance-source_input)
            actual=sum(r.quantity for r in context.execution.facts if r.model_code==model and production_week(r.date)==target_week)
            actual_known=_actual_known(context.execution,model,target_week,lines,context.time.as_of)
            remaining=max(0,target-actual)
            if not actual_known:
                issues.append({'code':'reset_execution_unknown','reason':'该机种本周执行覆盖或截止尚未确认，实际量不能按零计算','model_code':model,'week':target_week.isoformat(),'blocks_adoption':True})
            current=_valid_remaining(result,context.execution,model,target_week,today)
            if actual_known and current>remaining: result=_reduce(result,current-remaining,model,target_week,command,context)
            elif actual_known and current<remaining: result=_increase(result,remaining-current,model,target_week,command,context)
            placed=_valid_remaining(result,context.execution,model,target_week,today)
            targets.append({'model_code':model,'week':target_week.isoformat(),'source_input':source_input,'incoming_balance':balance,'target':target,'actual':actual,
                'remaining_target':remaining,'planned_remaining':placed,'actual_over_target':max(0,actual-target),'natural_carry':carry,
                'unmet_increase':max(0,remaining-placed),'unmet_reduction':max(0,placed-remaining)})
            balance=carry
    changes=_adjustments(context,existing['first_effective_revision'] if existing and existing.get('first_effective_revision') else source_record['revision'])
    choices={r.revision:r.retain for r in command.choices}
    if len(choices)!=len(command.choices) or set(choices)-{r['revision'] for r in changes}: raise ValueError('调整处理项重复或已变化，请重新核对')
    review=[{'revision':r['revision'],'operations':r['operations'],'decision':choices.get(r['revision'])} for r in changes]
    if set(choices)!={r['revision'] for r in changes}:
        issues.append({'code':'reset_choices_pending','reason':'首次重置后的每项完整调整都须明确保留或撤回','blocks_adoption':True})
    for change in changes:
        if choices.get(change['revision']) is False:
            old_plan=Plan.model_validate(change['before']);new_plan=Plan.model_validate(change['after'])
            old_rows={r.id:r for r in old_plan.segments};new_rows={r.id:r for r in new_plan.segments}
            current_rows={r.id:r for r in plan.segments};conflicts=[]
            for identity in old_rows.keys()|new_rows.keys():
                a,b=old_rows.get(identity),new_rows.get(identity)
                if a==b: continue
                current=current_rows.get(identity);executed=current_executed.get(identity,0)
                if executed and (a is None or (a.date,a.line_code,a.model_code)!=(current.date,current.line_code,current.model_code) or a.quantity<executed):
                    conflicts.append({'code':'reset_protected_adjustment','revision':change['revision'],'segment_id':identity,'reason':'撤回会改变后来已执行的完整调整'})
            def quantities(value):
                totals=defaultdict(int)
                for row in value.segments: totals[row.date,row.line_code,row.model_code]+=row.quantity
                return totals
            a_cells,b_cells=quantities(old_plan),quantities(new_plan)
            for cell in locked:
                if a_cells[cell]!=b_cells[cell]: conflicts.append({'code':'reset_protected_adjustment','revision':change['revision'],'date':cell[0].isoformat(),'line_code':cell[1],'model_code':cell[2],'reason':'撤回会改变当前锁定数量'})
            if conflicts: raise PlanProblem(409,'此完整调整已受执行事实或当前锁定保护，不能撤回',conflicts=conflicts)
            before={r['id']:r for r in change['before'].get('segments',[])}
            after={r['id']:r for r in change['after'].get('segments',[])}
            if any(not command.scope.contains(ProductionSegment.model_validate(row)) for identity in before.keys()|after.keys() if before.get(identity)!=after.get(identity) for row in (before.get(identity),after.get(identity)) if row):
                raise ValueError('撤回调整涉及授权范围外的安排，请扩大范围后重新预览')
        if choices.get(change['revision']) is True:
            candidate=_apply_delta(result,change,command.scope,context)
            result=_witness(result,candidate,context,command.scope,reset)
    for change in changes:
        if choices.get(change['revision']) is not None:
            result=_apply_explicit_metadata(result,change,command.scope,context,retract=not choices[change['revision']])
    if choices and not evaluate(_snapshot(context,result,reset)).capacity_valid:
        raise ValueError('完整调整的投入、工时或 Recipe 衔接与更正目标冲突，请扩大范围或改选处理方式')
    # Retaining an adjustment does not rewrite the underlying target.
    for target in targets:
        placed=_valid_remaining(result,context.execution,target['model_code'],date.fromisoformat(target['week']),today)
        target.update(planned_remaining=placed,unmet_increase=max(0,target['remaining_target']-placed),unmet_reduction=max(0,placed-target['remaining_target']))
    protect(plan,result,context.snapshot)
    record={'production_week':key,'source_adoption':source_number,'comparison_adoption':context.latest_adoption,'snapshot':snapshot_data,'snapshot_digest':snapshot_digest,
        'batch_revision':batch_revision,'historical_inputs':[r.model_dump(mode='json')|{'destination':'由本周最终净余额与在制品接续，不复制投入'} for r in source.segments if r.date<week],
        'crossing_switches':[r.model_dump(mode='json')|{'state':'跨窗口进度待复核，不推定已执行'} for r in source.switches if any(a.date<week for a in r.allocations) and any(a.date>=week for a in r.allocations)],'targets':targets,'residual_carry':residual,'expired':expired,'adjustments':review,'pending_review':False,
        'effective_adoption':existing.get('effective_adoption') if existing else None,'effective_revision':existing.get('effective_revision') if existing else None,
        'first_effective_revision':existing.get('first_effective_revision') if existing else None,'draft_action':context.action_id,'correction':command.correction}
    record=refresh_targets(record,result,context.execution,context.time,lines)
    records[key]=record
    lineage={'resets':records,'reset_batches':batches,'reset_preview':record,'reset_reference':None}
    return ReplayResult(result,issues=issues,context_updates={'reset':reset},lineage=lineage,
        resolve_issues=[r['id'] for r in context.issues if r.get('code') in {'reset_choices_pending','reset_execution_unknown','reset_pending_review'}])


register_operation('weekly_reset',WeeklyReset,weekly_reset)
