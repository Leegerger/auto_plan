"""Atomic manual commands and exact certificates bounded by explicit authority."""
from collections import defaultdict
from datetime import date, timedelta
from fractions import Fraction
import hashlib
from typing import Literal
from pydantic import Field, model_validator
from .input_models import InputRow, Code, Pieces
from .plan_models import Plan, SourceSegment, InitialRecipeState
from .input_lookup import InputIndex
from .recipe_states import analyze_recipe_states
from .certificate import construct_hour_certificate, CertificateError
from .plan_operations import register_operation, ReplayResult
from .editing_scope import EditingScope


class ScopedOperation(InputRow):
    scope: EditingScope


class AppendFill(ScopedOperation):
    kind: Literal['append_fill']
    model_code: Code
    quantity: Pieces
    line_code: Code
    start_date: date
    end_date: date
    continue_id: str | None = None


def derived_id(context,suffix):
    return 'edit-'+hashlib.sha256(f'{context.action_id}:{suffix}'.encode()).hexdigest()[:40]


def _state_before(plan,index,line,day,states=None):
    states=states if states is not None else analyze_recipe_states(plan,index)
    endings=[(production_day,state) for (production_day,production_line),state in states.endings.items()
             if production_line==line and production_day<day]
    return max(endings,key=lambda value:value[0])[1] if endings else states.initial_states.get(line)


class CapacityWitnessError(ValueError):
    pass


def bounded_certificate(plan,context,scope):
    """Rebuild only authorized lines/dates; preserve all outside rows verbatim.

    Crossing switches and outgoing state mismatches require explicit expansion.
    This never rewrites an executed association or silently repairs a boundary.
    """
    if scope.start_date<context.time.window_start or scope.end_date>context.time.window_end:
        raise ValueError('调整范围必须位于当周至第十五周')
    index=InputIndex(context.inputs)
    output=plan.model_copy(deep=True)
    original=context.plan
    original_states=analyze_recipe_states(original,index)
    original_switch_documents={switch.model_dump_json() for switch in original.switches}
    for line in scope.line_codes:
        if line not in index.lines: raise ValueError('线体编码不存在')
        crossing=[s for s in original.switches if s.line_code==line and any(scope.start_date<=a.date<=scope.end_date for a in s.allocations) and any(not scope.start_date<=a.date<=scope.end_date for a in s.allocations)]
        if crossing: raise ValueError('切机跨越调整范围边界，请扩大日期范围后重新预览')
        inside=[r for r in output.segments if r.line_code==line and scope.start_date<=r.date<=scope.end_date]
        incoming=_state_before(original,index,line,scope.start_date,original_states)
        sources=[SourceSegment.model_validate(r.model_dump(exclude={'hours'})) for r in inside]
        try:
            certificate=construct_hour_certificate(context.inputs,sources,[InitialRecipeState(line_code=line,recipe_code=incoming)],window_start=scope.start_date)
        except CertificateError as error:
            # Pending drafts may contain an unrelated impossible day. Try the
            # actual changed block while keeping every other event exact.
            certificate=_local_certificate(output,original,context,index,scope,line,incoming,inside)
            if certificate is None:
                raise CapacityWitnessError('；'.join(f'{i.date or ""} {i.line_code or ""} {i.reason}' for i in error.issues)) from None
        outgoing=_state_before(certificate,index,line,scope.end_date+timedelta(days=1))
        old_outgoing=_state_before(original,index,line,scope.end_date+timedelta(days=1),original_states)
        outside_later=any(r.line_code==line and r.date>scope.end_date and r.quantity for r in original.segments) or any(s.line_code==line and any(a.date>scope.end_date for a in s.allocations) for s in original.switches)
        if outside_later and outgoing!=old_outgoing:
            raise ValueError(f'{scope.end_date} {line} 调整会改变范围外 Recipe 衔接，请扩大范围后重新预览')
        if scope.model_codes:
            fixed_inside=[r for r in original.segments if r.line_code==line and scope.start_date<=r.date<=scope.end_date]
            certificate=_preserve_fixed_models(certificate,fixed_inside,index,scope,line)
        # Unchanged days without switch work keep their original exact witness.
        # A wide authorization range does not itself authorize needless writes.
        preserved_days=set()
        original_inside=[r for r in original.segments if r.line_code==line and scope.start_date<=r.date<=scope.end_date]
        for day in {r.date for r in original_inside}:
            old_rows=sorted([r for r in original_inside if r.date==day],key=lambda r:r.id)
            requested_rows=sorted([r for r in inside if r.date==day],key=lambda r:r.id)
            switch_work=any(a.date==day for s in [*original.switches,*certificate.switches] if s.line_code==line for a in s.allocations)
            if old_rows==requested_rows and not switch_work: preserved_days.add(day)
        certificate=certificate.model_copy(update={'segments':[r for r in certificate.segments if r.date not in preserved_days]+[r for r in original_inside if r.date in preserved_days]})
        segments=[r for r in output.segments if not (r.line_code==line and scope.start_date<=r.date<=scope.end_date)]+certificate.segments
        switches=[s for s in output.switches if not(s.line_code==line and all(scope.start_date<=a.date<=scope.end_date for a in s.allocations))]
        for switch in certificate.switches:
            signature=f'{line}:{switch.from_recipe}:{switch.to_recipe}:'+','.join(f'{a.date}:{a.order}' for a in switch.allocations)
            switches.append(switch if switch.model_dump_json() in original_switch_documents else switch.model_copy(update={'id':derived_id(context,signature)}))
        idle=[r for r in output.ordinary_idle if not(r.line_code==line and scope.start_date<=r.date<=scope.end_date) or (r.line_code==line and r.date in preserved_days)]
        output=output.model_copy(update={'segments':segments,'switches':switches,'ordinary_idle':idle})
    output=output.model_copy(update={'segments':sorted(output.segments,key=lambda r:(r.date,r.line_code,r.order,r.id))})
    _authorize_change(original,output,scope,context)
    return output


def _local_certificate(output,original,context,index,scope,line,incoming,inside):
    old={r.id:r for r in original.segments if r.line_code==line}
    new={r.id:r for r in output.segments if r.line_code==line}
    changed=[row.date for identity in old.keys()|new.keys() if old.get(identity)!=new.get(identity)
             for row in (old.get(identity),new.get(identity)) if row]
    if not changed: return None
    first,last=min(changed),max(changed)
    if first==scope.start_date and last==scope.end_date: return None
    for switch in original.switches:
        if switch.line_code==line and any(first<=a.date<=last for a in switch.allocations) and any(not first<=a.date<=last for a in switch.allocations): return None
    sources=[SourceSegment.model_validate(r.model_dump(exclude={'hours'})) for r in inside if first<=r.date<=last]
    try:
        local=construct_hour_certificate(context.inputs,sources,[InitialRecipeState(line_code=line,recipe_code=_state_before(original,index,line,first))],window_start=first)
    except CertificateError: return None
    preserved=[s for s in original.switches if s.line_code==line and all(scope.start_date<=a.date<=scope.end_date for a in s.allocations) and not any(first<=a.date<=last for a in s.allocations)]
    certificate=local.model_copy(update={'segments':[r for r in inside if not first<=r.date<=last]+local.segments,
        'switches':preserved+local.switches,'initial_states':[InitialRecipeState(line_code=line,recipe_code=incoming)]})
    combined=output.model_copy(update={'segments':[r for r in output.segments if not(r.line_code==line and scope.start_date<=r.date<=scope.end_date)]+certificate.segments,
        'switches':[s for s in output.switches if not(s.line_code==line and all(scope.start_date<=a.date<=scope.end_date for a in s.allocations))]+certificate.switches})
    def recipe_problems(plan):
        problems=set()
        def problem(code,reason,**where):
            row=where.get('segment');switch=where.get('switch')
            problems.add((code,str(where.get('day') or (row.date if row else '')),where.get('line') or (row.line_code if row else switch.line_code if switch else ''),row.id if row else ''))
        analyze_recipe_states(plan,index,problem)
        return problems
    if recipe_problems(combined)-recipe_problems(original): return None
    return certificate


def _preserve_fixed_models(certificate,inside,index,scope,line,fixed_ids=None):
    fixed={r.id:r for r in inside if r.id in fixed_ids} if fixed_ids is not None else {r.id:r for r in inside if r.model_code not in scope.model_codes}
    if not fixed: return certificate
    rows={r.id:r for r in certificate.segments}
    # Preserve the integer event positions of all excluded models. Inserted
    # production and switch events can use only the remaining positions.
    events=defaultdict(list)
    for row in certificate.segments: events[row.date].append((row.order,'segment',row.id,None))
    for switch in certificate.switches:
        for ordinal,allocation in enumerate(switch.allocations): events[allocation.date].append((allocation.order,'switch',switch.id,ordinal))
    switch_orders={}
    for day,daily in events.items():
        following=0
        for _,kind,identity,ordinal in sorted(daily):
            position=fixed[identity].order if kind=='segment' and identity in fixed else following
            if position<following:
                raise ValueError(f'{day} {line} 插入切机或生产需要改动范围外顺序，请扩大机种范围')
            following=position+1
            if kind=='segment': rows[identity]=rows[identity].model_copy(update={'order':position})
            else: switch_orders[(identity,ordinal)]=position
    switches=[s.model_copy(update={'allocations':[a.model_copy(update={'order':switch_orders[(s.id,n)]}) for n,a in enumerate(s.allocations)]}) for s in certificate.switches]
    groups=defaultdict(list)
    for row in rows.values(): groups[(row.date,index.models[row.model_code].recipe_code)].append(row)
    by_day=defaultdict(list)
    for key,daily in groups.items(): by_day[key[0]].append((key,daily))
    for day,daily_groups in by_day.items():
        bounds={}; needs=[]
        for key,daily in daily_groups:
            quantity=sum(r.quantity for r in daily)
            capacity=index.capacity(day.strftime('%Y-%m'),key[1],line)['pieces']
            lower=Fraction(24*(quantity-1),capacity) if quantity else Fraction(0)
            fixed_hours=sum((Fraction(fixed[r.id].hours) for r in daily if r.id in fixed),Fraction(0))
            bounds[key]=max(fixed_hours,lower)
            if quantity and fixed_hours<=lower: needs.append(key)
        switch_hours=sum((Fraction(a.hours) for s in switches for a in s.allocations if a.date==day),Fraction(0))
        spare=Fraction(index.day(day,line)['available_hours'])-switch_hours-sum(bounds.values(),Fraction(0))
        if spare<0 or needs and spare<=0:
            raise CapacityWitnessError(f'{day} {line} 保留范围外工时后容量不足')
        margin=spare/(2*len(needs)) if needs else Fraction(0)
        for key,daily in daily_groups:
            editable=[r for r in daily if r.id not in fixed]
            fixed_hours=sum((Fraction(fixed[r.id].hours) for r in daily if r.id in fixed),Fraction(0))
            remaining=bounds[key]+(margin if key in needs else 0)-fixed_hours
            quantity=sum(r.quantity for r in editable)
            if remaining and not quantity: raise CapacityWitnessError(f'{day} {line} 范围外工时不足以维持投入')
            for row in daily:
                rows[row.id]=fixed[row.id] if row.id in fixed else rows[row.id].model_copy(update={'hours':str(remaining*row.quantity/quantity if quantity else 0)})
    return certificate.model_copy(update={'segments':list(rows.values()),'switches':switches})


def _authorize_change(before,after,scope,context,allow_certificate=False):
    if scope.start_date<context.time.window_start or scope.end_date>context.time.window_end:
        raise ValueError('调整范围必须位于当周至第十五周')
    old,new={r.id:r for r in before.segments},{r.id:r for r in after.segments}
    for identity in old.keys()|new.keys():
        if old.get(identity)==new.get(identity): continue
        for row in (old.get(identity),new.get(identity)):
            if row and not scope.contains(row):
                prior,later=old.get(identity),new.get(identity)
                if allow_certificate and prior and later and scope.start_date<=row.date<=scope.end_date and row.line_code in scope.line_codes and prior.model_dump(exclude={'hours','order'})==later.model_dump(exclude={'hours','order'}):
                    continue
                raise ValueError(f'{row.date} {row.line_code} {row.model_code} 修改超出授权调整范围')


def _ordered_insert(plan,segment,position=None):
    daily=sorted([r for r in plan.segments if (r.date,r.line_code)==(segment.date,segment.line_code) and r.id!=segment.id],key=lambda r:(r.order,r.id))
    index=len(daily) if position is None else min(position,len(daily))
    daily.insert(index,segment)
    ordered={row.id:row.model_copy(update={'order':order}) for order,row in enumerate(daily)}
    return plan.model_copy(update={'segments':[ordered.get(row.id,row) for row in plan.segments]})


def append_fill(plan,command,context):
    index=InputIndex(context.inputs)
    if command.model_code not in index.models or command.line_code not in index.lines:
        raise ValueError('机种或线体不存在')
    if not index.models[command.model_code].active or not index.lines[command.line_code].active or not index.recipes[index.models[command.model_code].recipe_code].active:
        raise ValueError('停用机种或线体不可新增生产段')
    if command.start_date>command.end_date or command.start_date<command.scope.start_date or command.end_date>command.scope.end_date or command.line_code not in command.scope.line_codes:
        raise ValueError('追加日期和线体必须位于调整范围')
    if command.scope.model_codes and command.model_code not in command.scope.model_codes:
        raise ValueError('追加机种不在调整范围')
    remaining=command.quantity
    resolved=[]
    if command.continue_id:
        entry=next((e for e in context.unallocated if e['id']==command.continue_id),None)
        if entry is None or entry.get('model_code')!=command.model_code or entry.get('quantity')!=command.quantity:
            raise ValueError('未分配量已变化，请按原记录继续处理')
        resolved=[command.continue_id]
    result=plan.model_copy(deep=True)
    recipe=index.models[command.model_code].recipe_code
    day=command.start_date
    allowed=False
    while day<=command.end_date and remaining:
        capacity=index.capacity(day.strftime('%Y-%m'),recipe,command.line_code)
        if capacity['state']!='known' or not capacity['pieces']:
            day+=timedelta(days=1); continue
        allowed=True
        if any(lock.date==day and lock.line_code==command.line_code and lock.model_code==command.model_code for lock in context.locks):
            day+=timedelta(days=1); continue
        daily=sorted([r for r in result.segments if r.date==day and r.line_code==command.line_code],key=lambda r:(r.order,r.id))
        neighbors=[n for n,r in enumerate(daily) if index.models[r.model_code].recipe_code==recipe]
        position=neighbors[-1]+1 if neighbors else len(daily)
        identity=derived_id(context,f'append:{day}:{command.line_code}')
        def candidate(quantity):
            working=result.model_copy(deep=True)
            from .plan_models import ProductionSegment
            added=ProductionSegment(id=identity,date=day,line_code=command.line_code,order=0,model_code=command.model_code,quantity=quantity,hours='0')
            working.segments.append(added); working=_ordered_insert(working,added,position)
            _authorize_change(result,working,command.scope,context,allow_certificate=True)
            return bounded_certificate(working,context,command.scope)
        low,high=0,min(remaining,capacity['pieces']+1)
        best=None
        while low<high:
            amount=(low+high+1)//2
            try: proposed=candidate(amount)
            except CapacityWitnessError: high=amount-1
            else: low=amount; best=proposed
        if low:
            result=best if best is not None and next(r.quantity for r in best.segments if r.id==identity)==low else candidate(low)
            remaining-=low
        day+=timedelta(days=1)
    if not allowed: raise ValueError('所选线体在日期范围内没有已知且允许的正产能')
    leftovers=[] if not remaining else [{'id':derived_id(context,'unallocated'),'model_code':command.model_code,'quantity':remaining,'requested':command.quantity,'placed':command.quantity-remaining,'line_code':command.line_code,'start_date':command.start_date.isoformat(),'end_date':command.end_date.isoformat(),'reason':'指定线体及日期范围容量不足，继续选线或明确舍弃'}]
    return ReplayResult(result,unallocated=leftovers,resolve_unallocated=resolved)


register_operation('append_fill',AppendFill,append_fill)


class SwapSegments(ScopedOperation):
    kind: Literal['swap_segments']
    segment_ids: list[Code] = Field(min_length=2,max_length=2)


def selected_segments(plan,identities):
    if not identities or len(set(identities))!=len(identities):
        raise ValueError('请选择完整且不重复的生产段')
    rows={r.id:r for r in plan.segments}
    if set(identities)-rows.keys(): raise ValueError('生产段已变化或不存在，请重新选择')
    return [rows[identity] for identity in identities]


def finish_operation(before,after,command,context):
    index=InputIndex(context.inputs)
    old={r.id:r for r in before.segments}
    for row in after.segments:
        prior=old.get(row.id)
        if prior and (row.date,row.line_code,row.model_code)==(prior.date,prior.line_code,prior.model_code): continue
        if row.line_code not in index.lines or row.model_code not in index.models:
            raise ValueError(f'{row.date} {row.line_code} {row.model_code} 编码不存在')
        if not index.lines[row.line_code].active or not index.models[row.model_code].active or not index.recipes[index.models[row.model_code].recipe_code].active:
            raise ValueError(f'{row.date} {row.line_code} {row.model_code} 停用资料不可新增排程位置')
    _authorize_change(before,after,command.scope,context,allow_certificate=True)
    try:
        return bounded_certificate(after,context,command.scope)
    except CapacityWitnessError:
        # Keep the requested date/position for the authoritative conflict preview.
        # A pending draft may retain capacity/order problems, never facts or locks.
        fixed={r.id:r for r in before.segments if not command.scope.contains(r)}
        after=after.model_copy(update={'segments':[fixed.get(r.id,r) for r in after.segments]})
        _authorize_change(before,after,command.scope,context)
        return after


def swap_segments(plan,command,context):
    left,right=selected_segments(plan,command.segment_ids)
    changes={left.id:left.model_copy(update={'date':right.date,'line_code':right.line_code,'order':right.order}),
             right.id:right.model_copy(update={'date':left.date,'line_code':left.line_code,'order':left.order})}
    result=plan.model_copy(update={'segments':[changes.get(r.id,r) for r in plan.segments]},deep=True)
    return finish_operation(plan,result,command,context)


register_operation('swap_segments',SwapSegments,swap_segments)


class MoveSegment(ScopedOperation):
    kind: Literal['move_segment']
    segment_id: Code
    target_date: date
    target_line: Code
    target_order: Pieces


class SplitSegment(MoveSegment):
    kind: Literal['split_segment']
    quantity: Pieces


def move_segment(plan,command,context):
    row=selected_segments(plan,[command.segment_id])[0]
    moved=row.model_copy(update={'date':command.target_date,'line_code':command.target_line,'order':command.target_order})
    result=plan.model_copy(update={'segments':[moved if r.id==row.id else r for r in plan.segments]},deep=True)
    result=_ordered_insert(result,moved,command.target_order)
    return finish_operation(plan,result,command,context)


def split_segment(plan,command,context):
    row=selected_segments(plan,[command.segment_id])[0]
    if not 0<command.quantity<row.quantity:
        raise ValueError('拆出数量必须大于零并小于原段数量')
    split=row.model_copy(update={'id':derived_id(context,'split'),'date':command.target_date,'line_code':command.target_line,
        'order':command.target_order,'quantity':command.quantity})
    result=plan.model_copy(update={'segments':[r.model_copy(update={'quantity':r.quantity-command.quantity}) if r.id==row.id else r for r in plan.segments]+[split]},deep=True)
    result=_ordered_insert(result,split,command.target_order)
    return finish_operation(plan,result,command,context)


class ClearSegments(ScopedOperation):
    kind: Literal['clear_segments']
    segment_ids: list[Code] = Field(min_length=1)


def clear_segments(plan,command,context):
    selected_segments(plan,command.segment_ids)
    result=plan.model_copy(update={'segments':[r for r in plan.segments if r.id not in command.segment_ids]},deep=True)
    return finish_operation(plan,result,command,context)


class ReorderSegments(ScopedOperation):
    kind: Literal['reorder_segments']
    date: date
    line_code: Code
    segment_ids: list[Code] = Field(min_length=1)


def reorder_segments(plan,command,context):
    selected=selected_segments(plan,command.segment_ids)
    daily=[r for r in plan.segments if (r.date,r.line_code)==(command.date,command.line_code)]
    if {r.id for r in daily}!=set(command.segment_ids):
        raise ValueError('排序必须包含所选日期线体的全部生产段')
    positions={identity:position for position,identity in enumerate(command.segment_ids)}
    result=plan.model_copy(update={'segments':[r.model_copy(update={'order':positions[r.id]}) if r.id in positions else r for r in plan.segments]},deep=True)
    return finish_operation(plan,result,command,context)


register_operation('move_segment',MoveSegment,move_segment)
register_operation('split_segment',SplitSegment,split_segment)
register_operation('clear_segments',ClearSegments,clear_segments)
register_operation('reorder_segments',ReorderSegments,reorder_segments)


class CopySegments(ScopedOperation):
    kind: Literal['copy_segments']
    segment_ids: list[Code] = Field(min_length=1)
    target_start_date: date
    line_mapping: dict[Code,Code]


class OverwriteCopySegments(CopySegments):
    kind: Literal['overwrite_copy_segments']


def copy_segments(plan,command,context):
    rows=sorted(selected_segments(plan,command.segment_ids),key=lambda r:(r.date,r.line_code,r.order,r.id))
    source_lines={r.line_code for r in rows}
    if set(command.line_mapping)!=source_lines:
        raise ValueError('请为每条源线体明确指定一条目标线体')
    first=min(r.date for r in rows)
    copies=[r.model_copy(update={'id':derived_id(context,f'copy:{r.id}'),'date':command.target_start_date+(r.date-first),'line_code':command.line_mapping[r.line_code]}) for r in rows]
    target_cells={(r.date,r.line_code) for r in copies}
    existing=[r for r in plan.segments if command.kind!='overwrite_copy_segments' or (r.date,r.line_code) not in target_cells]
    result=plan.model_copy(update={'segments':existing},deep=True)
    for copied in copies:
        daily=[r for r in result.segments if (r.date,r.line_code)==(copied.date,copied.line_code)]
        copied=copied.model_copy(update={'order':max((r.order for r in daily),default=-1)+1})
        result.segments.append(copied)
    return finish_operation(plan,result,command,context)


register_operation('copy_segments',CopySegments,copy_segments)
register_operation('overwrite_copy_segments',OverwriteCopySegments,copy_segments)


class DiscardUnallocated(ScopedOperation):
    kind: Literal['discard_unallocated']
    result_ids: list[Code] = Field(min_length=1)


def discard_unallocated(plan,command,context):
    if len(set(command.result_ids))!=len(command.result_ids):
        raise ValueError('未分配记录不可重复选择')
    if set(command.result_ids)-{e['id'] for e in context.unallocated}:
        raise ValueError('未分配记录已变化或不存在')
    return ReplayResult(plan,resolve_unallocated=command.result_ids)


register_operation('discard_unallocated',DiscardUnallocated,discard_unallocated)
