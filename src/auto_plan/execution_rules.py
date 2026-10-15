"""Shared execution projection and current production-day capacity rules."""
from collections import defaultdict
import hashlib
import json
from .production_time import production_date


def freeze_associations(plan,execution):
    """Bind legacy/imported cumulative facts to a stable original plan, including empty cells."""
    from .plan_models import ExecutionAssociation
    frozen={(r.date,r.line_code,r.model_code) for r in execution.association_cells}
    pending=[r for r in execution.facts if (r.date,r.line_code,r.model_code) not in frozen]
    if not pending: return execution
    assigned=executed_by_segment(plan,execution)
    pending_keys={(r.date,r.line_code,r.model_code) for r in pending}
    identities={r.segment_id for r in execution.associations}
    associations=execution.associations+[ExecutionAssociation(segment_id=r.id,quantity=assigned[r.id])
        for r in sorted(plan.segments,key=lambda r:(r.date,r.line_code,r.order,r.id))
        if (r.date,r.line_code,r.model_code) in pending_keys and r.id not in identities]
    return execution.model_copy(update={'associations':associations,'association_cells':execution.association_cells+pending})


def executed_by_segment(plan,execution):
    facts={(r.date,r.line_code,r.model_code):r.quantity for r in execution.facts}
    rows={r.id:r for r in plan.segments}
    explicit={r.segment_id:r.quantity for r in execution.associations}
    frozen={(r.date,r.line_code,r.model_code) for r in execution.association_cells}
    assigned=defaultdict(int)
    result={}
    for identity,quantity in explicit.items():
        row=rows.get(identity)
        if row is not None:
            result[identity]=quantity
            assigned[row.date,row.line_code,row.model_code]+=quantity
    for row in sorted(plan.segments,key=lambda r:(r.date,r.line_code,r.order,r.id)):
        if row.id in explicit: continue
        key=row.date,row.line_code,row.model_code
        quantity=0 if key in frozen else min(row.quantity,max(0,facts.get(key,0)-assigned[key]))
        assigned[key]+=quantity;result[row.id]=quantity
    return result


def capacity_basis(snapshot):
    inputs=snapshot.inputs.model_dump(mode='json',exclude={'input_version'})
    execution=snapshot.execution.model_dump(mode='json',exclude={'audit','day_capacities'})
    raw=json.dumps({'inputs':inputs,'execution':execution},ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def confirmed_day_capacity(snapshot,line_code):
    today=production_date(snapshot.time.as_of)
    basis=capacity_basis(snapshot)
    return next((r for r in snapshot.execution.day_capacities if r.date==today and r.line_code==line_code
                 and r.basis_digest==basis and r.confirmed_at<=snapshot.time.as_of),None)


def active_plan(plan,time):
    from .plan_models import Plan
    inside=lambda day:time.window_start<=day<=time.window_end
    switches=[]
    for row in plan.switches:
        allocations=[a for a in row.allocations if inside(a.date)]
        if allocations: switches.append(row.model_copy(update={'allocations':allocations}))
    from .plan_models import InitialRecipeState
    expired_lines={r.line_code for r in plan.segments if r.date<time.window_start}|{r.line_code for r in plan.switches if any(a.date<time.window_start for a in r.allocations)}
    initial=[r for r in plan.initial_states if r.line_code not in expired_lines]
    initial.extend(InitialRecipeState(line_code=line,recipe_code=None) for line in sorted(expired_lines))
    return Plan(current_day_witnesses=[r for r in plan.current_day_witnesses if inside(r.date)],segments=[r for r in plan.segments if inside(r.date)],switches=switches,
        initial_states=initial,ordinary_idle=[r for r in plan.ordinary_idle if inside(r.date)])


def day_plan_digest(plan,day,line):
    content={'segments':[r.model_dump(mode='json') for r in sorted(plan.segments,key=lambda r:(r.order,r.id)) if r.date==day and r.line_code==line],
        'switches':[r.model_dump(mode='json') for r in sorted(plan.switches,key=lambda r:r.id) if r.line_code==line and any(a.date==day for a in r.allocations)],
        'ordinary_idle':[r.model_dump(mode='json') for r in plan.ordinary_idle if r.date==day and r.line_code==line]}
    return hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _prior_switch_pairs(plan,snapshot,line,current_recipe):
    """An existential ordered path, not measured historical switch progress."""
    from fractions import Fraction
    from .input_lookup import InputIndex
    index=InputIndex(snapshot.inputs);today=production_date(snapshot.time.as_of)
    assigned=executed_by_segment(plan,snapshot.execution)
    ordered=sorted(plan.segments,key=lambda r:(r.order,r.id))
    sequence=[index.models[r.model_code].recipe_code for r in ordered if r.date==today and r.line_code==line and assigned.get(r.id,0) and r.model_code in index.models]
    represented={r.model_code for r in ordered if r.date==today and r.line_code==line and assigned.get(r.id,0)}
    sequence.extend(index.models[r.model_code].recipe_code for r in snapshot.execution.facts
        if r.date==today and r.line_code==line and r.quantity and r.model_code not in represented and r.model_code in index.models)
    if not sequence: return []
    initial=next((r.recipe_code for r in plan.initial_states if r.line_code==line),None) if today==snapshot.time.window_start else None
    if initial is not None: sequence.insert(0,initial)
    sequence.append(current_recipe)
    return [(a,b) for a,b in zip(sequence,sequence[1:]) if a!=b]


def _prior_switch_hours(plan,snapshot,line,current_recipe):
    from fractions import Fraction
    from .input_lookup import InputIndex
    index=InputIndex(snapshot.inputs)
    return sum((Fraction(index.switch(a,b)['hours']) for a,b in _prior_switch_pairs(plan,snapshot,line,current_recipe)),Fraction(0))


def _quantity_groups(plan,snapshot,line):
    from .input_lookup import InputIndex
    index=InputIndex(snapshot.inputs);today=production_date(snapshot.time.as_of)
    actual=defaultdict(int);remaining=defaultdict(int)
    assigned=executed_by_segment(plan,snapshot.execution)
    for row in snapshot.execution.facts:
        if row.date==today and row.line_code==line and row.model_code in index.models:
            actual[index.models[row.model_code].recipe_code]+=row.quantity
    for row in plan.segments:
        if row.date==today and row.line_code==line and row.model_code in index.models:
            remaining[index.models[row.model_code].recipe_code]+=max(0,row.quantity-assigned.get(row.id,0))
    return actual,remaining


def capacity_plan(snapshot,issues=None):
    """Verify and project a declared proof. Evaluation never repairs the candidate."""
    from fractions import Fraction
    from .plan_models import InitialRecipeState, RuleIssue
    from .input_lookup import InputIndex
    index=InputIndex(snapshot.inputs);today=production_date(snapshot.time.as_of)
    confirmations={r.code:confirmed_day_capacity(snapshot,r.code) for r in snapshot.inputs.masters.lines}
    confirmations={line:value for line,value in confirmations.items() if value is not None}
    stale={r.line_code for r in snapshot.execution.day_capacities if r.date==today}-set(confirmations)
    if issues is not None:
        for line in stale:
            if any(r.date==today and r.line_code==line and r.quantity for r in snapshot.plan.segments):
                issues.append(RuleIssue(code='day_capacity_stale',reason='当日剩余工时与当前事实或资料版本不一致，请重新确认',date=today,line_code=line))
    if not confirmations: return snapshot.plan
    segments=[r for r in snapshot.plan.segments if r.line_code not in confirmations or r.date>today]
    switches=[r for r in snapshot.plan.switches if r.line_code not in confirmations or all(a.date>today for a in r.allocations)]
    for line,confirmation in confirmations.items():
        errors=[]
        witnesses=[r for r in snapshot.plan.current_day_witnesses if r.date==today and r.line_code==line]
        witness=witnesses[0] if len(witnesses)==1 else None
        expected=[];assigned=executed_by_segment(snapshot.plan,snapshot.execution)
        for row in snapshot.plan.segments:
            if row.date==today and row.line_code==line and row.quantity>assigned.get(row.id,0):
                expected.append(row.model_dump(include={'id','date','line_code','order','model_code'})|{'quantity':row.quantity-assigned.get(row.id,0)})
        if witness is None:
            if expected or any(r.date==today and r.line_code==line and r.quantity for r in snapshot.execution.facts):
                errors.append('当日缺少明确的剩余工时依据，请重新确认并预览')
        else:
            if witness.basis_digest!=capacity_basis(snapshot) or witness.plan_digest!=day_plan_digest(snapshot.plan,today,line):
                errors.append('当日工时依据与计划或事实参数已不一致，请重新预览')
            supplied=[r.model_dump(exclude={'hours'}) for r in witness.segments]
            if sorted(supplied,key=lambda r:r['id'])!=sorted(expected,key=lambda r:r['id']):
                errors.append('当日剩余投入依据与累计事实、分段关联不一致')
            declared=sorted([r for r in snapshot.plan.switches if r.line_code==line and any(a.date==today for a in r.allocations)],key=lambda r:r.id)
            if declared!=sorted(witness.switches+witness.prior_switches,key=lambda r:r.id): errors.append('当日声明的切机与剩余工时依据不一致')
            actual,remaining=_quantity_groups(snapshot.plan,snapshot,line)
            production=defaultdict(Fraction);full=defaultdict(Fraction)
            for row in witness.segments:
                if row.model_code in index.models: production[index.models[row.model_code].recipe_code]+=Fraction(row.hours)
            for row in snapshot.plan.segments:
                if row.date==today and row.line_code==line and row.model_code in index.models:
                    full[index.models[row.model_code].recipe_code]+=Fraction(row.hours)
            prior={r.recipe_code:Fraction(r.hours) for r in witness.prior_recipe_hours}
            if len(prior)!=len(witness.prior_recipe_hours): errors.append('执行部分 Recipe 工时依据重复')
            prior_switch=_prior_switch_hours(snapshot.plan,snapshot,line,confirmation.current_recipe)
            pairs=_prior_switch_pairs(snapshot.plan,snapshot,line,confirmation.current_recipe)
            if [(r.from_recipe,r.to_recipe) for r in witness.prior_switches]!=pairs:
                errors.append('执行部分切机路径与原执行顺序或已知起始 Recipe 不一致')
            for row in witness.prior_switches:
                duration=Fraction(index.switch(row.from_recipe,row.to_recipe)['hours'])
                if row.line_code!=line or any(a.date!=today or Fraction(a.idle_hours) for a in row.allocations) or sum((Fraction(a.hours) for a in row.allocations),Fraction(0))!=duration:
                    errors.append('执行部分声明切机的日期、总工时或占用不一致')
            if Fraction(witness.prior_switch_hours)!=prior_switch: errors.append('执行部分 Recipe 衔接依据不一致')
            budget=Fraction(index.day(today,line)['available_hours'])-Fraction(confirmation.remaining_hours)
            if sum(prior.values(),Fraction(0))+prior_switch>budget:
                errors.append('执行与剩余工时不存在共同日历容量依据')
            for recipe in set(actual)|set(remaining)|set(prior):
                capacity=index.capacity(today.strftime('%Y-%m'),recipe,line)
                pieces=capacity['pieces'];past=prior.get(recipe,Fraction(0));future=production[recipe]
                if actual[recipe] and (capacity['state']!='known' or not pieces or pieces*past<=24*(actual[recipe]-1)):
                    errors.append('执行数量没有对应的同一日 Recipe 工时依据')
                total=actual[recipe]+remaining[recipe]
                if total and (capacity['state']!='known' or not pieces or pieces*(past+future)<=24*(total-1)):
                    errors.append('执行与待执行合计不满足同一日 Recipe 取整边界')
                if not actual[recipe] and past: errors.append('没有执行事实的 Recipe 不能补造执行工时')
                represented=any(r.date==today and r.line_code==line and r.model_code in index.models and index.models[r.model_code].recipe_code==recipe for r in snapshot.plan.segments)
                if represented and full[recipe]!=past+future:
                    errors.append('计划声明的全日工时与共同执行及剩余依据不一致')
            segments.extend(witness.segments);switches.extend(witness.switches)
        if errors and issues is not None:
            issues.extend(RuleIssue(code='current_day_witness',reason=reason,date=today,line_code=line) for reason in dict.fromkeys(errors))
    initial=[r for r in snapshot.plan.initial_states if r.line_code not in confirmations]
    initial.extend(InitialRecipeState(line_code=line,recipe_code=value.current_recipe) for line,value in confirmations.items())
    return snapshot.plan.model_copy(update={'segments':segments,'switches':switches,'initial_states':initial,
        'ordinary_idle':[r for r in snapshot.plan.ordinary_idle if r.line_code not in confirmations or r.date>today]})


def remaining_day_hours(plan,snapshot,line_code):
    from fractions import Fraction
    today=production_date(snapshot.time.as_of)
    projected=capacity_plan(snapshot.model_copy(update={'plan':plan}) if hasattr(snapshot,'model_copy') else snapshot.snapshot.model_copy(update={'plan':plan}))
    return sum((Fraction(r.hours) for r in projected.segments if r.date==today and r.line_code==line_code),Fraction(0))+sum((Fraction(a.hours) for r in projected.switches if r.line_code==line_code for a in r.allocations if a.date==today),Fraction(0))


def _unconfirmed_certificate(plan,context,scope,line):
    from dataclasses import replace
    from datetime import timedelta
    from .manual_operations import _bounded_certificate, _authorize_change, CapacityWitnessError
    today=production_date(context.time.as_of)
    if not scope.start_date<=today<=scope.end_date: return _bounded_certificate(plan,context,scope)
    before=defaultdict(int);after=defaultdict(int)
    for row in context.plan.segments:
        if row.date==today and row.line_code==line: before[row.model_code]+=row.quantity
    for row in plan.segments:
        if row.date==today and row.line_code==line: after[row.model_code]+=row.quantity
    if any(quantity>before[model] for model,quantity in after.items()):
        raise CapacityWitnessError('当日自动补投须先确认剩余可用工时及当前 Recipe')
    old_switch=[r for r in context.plan.switches if r.line_code==line and any(a.date==today for a in r.allocations)]
    new_switch=[r for r in plan.switches if r.line_code==line and any(a.date==today for a in r.allocations)]
    if old_switch!=new_switch: raise CapacityWitnessError('当日新增或改变切机须先确认剩余工时及当前 Recipe')
    if scope.end_date<=today:
        _authorize_change(context.plan,plan,scope,context)
        return plan
    # Keep already arranged current-day work exact. Future construction cannot
    # borrow an unconfirmed current-day gap for a new switch.
    prefix=context.plan.model_copy(update={'segments':[r for r in context.plan.segments if not(r.line_code==line and r.date<=today)]+[r for r in plan.segments if r.line_code==line and r.date<=today]})
    local=replace(context,snapshot=context.snapshot.model_copy(update={'plan':prefix}))
    result=_bounded_certificate(plan,local,scope.model_copy(update={'start_date':today+timedelta(days=1)}))
    _authorize_change(context.plan,result,scope,context)
    return result


def execution_certificate(plan,context,scope):
    """Declare a joint exact whole-day and residual proof while preserving facts."""
    from fractions import Fraction
    from datetime import timedelta
    from .manual_operations import _bounded_certificate, _authorize_change, CapacityWitnessError, derived_id, _state_before, _outgoing_compatible
    from .certificate import construct_hour_certificate, CertificateError
    from .plan_models import SourceSegment, InitialRecipeState, CurrentDayWitness, RecipeHours, RecipeSwitch, SwitchAllocation
    from .input_lookup import InputIndex
    today=production_date(context.time.as_of);index=InputIndex(context.inputs)
    confirmed={line:confirmed_day_capacity(context,line) for line in scope.line_codes}
    confirmed={line:value for line,value in confirmed.items() if value is not None and scope.start_date<=today<=scope.end_date}
    if not confirmed and not scope.start_date<=today<=scope.end_date: return _bounded_certificate(plan,context,scope)
    result=plan.model_copy(deep=True)
    from dataclasses import replace
    for line in scope.line_codes:
        line_original=context.plan.model_copy(update={'segments':[r for r in context.plan.segments if r.line_code==line]+[r for r in result.segments if r.line_code!=line]})
        line_context=replace(context,snapshot=context.snapshot.model_copy(update={'plan':line_original}))
        confirmation=confirmed.get(line);single=scope.model_copy(update={'line_codes':[line]})
        if confirmation is None:
            result=_unconfirmed_certificate(result,line_context,single,line);continue
        if any(s.line_code==line and any(a.date<today for a in s.allocations) and any(a.date>=today for a in s.allocations) for s in context.plan.switches):
            raise ValueError('切机跨越当前执行边界，须先复核实际切机进度')
        if any(s.line_code==line and any(today<=a.date<=scope.end_date for a in s.allocations) and any(a.date>scope.end_date for a in s.allocations) for s in context.plan.switches):
            raise ValueError('切机跨越调整范围边界，请扩大范围')
        assigned=executed_by_segment(result,context.execution)
        rows=[r for r in result.segments if r.line_code==line and today<=r.date<=scope.end_date]
        sources=[]
        for row in rows:
            amount=row.quantity-(assigned.get(row.id,0) if row.date==today else 0)
            if amount: sources.append(SourceSegment.model_validate(row.model_dump(exclude={'hours'})|{'quantity':amount}))
        try:
            witness=construct_hour_certificate(context.inputs,sources,[InitialRecipeState(line_code=line,recipe_code=confirmation.current_recipe)],window_start=today,
                day_limits={(today,line):Fraction(confirmation.remaining_hours)})
        except CertificateError as error:
            raise CapacityWitnessError('；'.join(i.reason for i in error.issues)) from None
        ending=_state_before(witness,index,line,scope.end_date+timedelta(days=1))
        old_ending=_state_before(context.plan,index,line,scope.end_date+timedelta(days=1))
        if not _outgoing_compatible(context.plan,index,line,scope.end_date,ending,old_ending):
            raise ValueError('调整会改变范围外 Recipe 衔接，请扩大范围')
        switches=[s for s in result.switches if not(s.line_code==line and all(today<=a.date<=scope.end_date for a in s.allocations))]
        generated=[s.model_copy(update={'id':derived_id(context,f'residual:{s.id}:{line}')}) for s in witness.switches]
        switches.extend(generated)
        old={r.id:r for r in rows};new=[]
        for row in witness.segments:
            executed=assigned.get(row.id,0) if row.date==today else 0
            new.append(row.model_copy(update={'quantity':row.quantity+executed}))
        new.extend(r for r in rows if r.date==today and assigned.get(r.id,0)==r.quantity)
        result=result.model_copy(update={'segments':[r for r in result.segments if not(r.line_code==line and today<=r.date<=scope.end_date)]+new,'switches':switches,
            'ordinary_idle':[r for r in result.ordinary_idle if not(r.line_code==line and today<=r.date<=scope.end_date)]})
        actual,remaining=_quantity_groups(result,context,line)
        production=defaultdict(Fraction)
        for row in witness.segments:
            if row.date==today: production[index.models[row.model_code].recipe_code]+=Fraction(row.hours)
        prior_switch=_prior_switch_hours(result,context,line,confirmation.current_recipe)
        prior_budget=Fraction(index.day(today,line)['available_hours'])-Fraction(confirmation.remaining_hours)-prior_switch
        lower={}
        for recipe in set(actual)|set(remaining):
            capacity=index.capacity(today.strftime('%Y-%m'),recipe,line)['pieces']
            if not capacity and actual[recipe]+remaining[recipe]: raise CapacityWitnessError('当日事实缺少允许的全日产能依据')
            lower[recipe]=max(Fraction(24*(actual[recipe]-1),capacity) if actual[recipe] else Fraction(0),
                Fraction(24*(actual[recipe]+remaining[recipe]-1),capacity)-production[recipe] if actual[recipe] else Fraction(0),Fraction(0))
        needs=[recipe for recipe in lower if actual[recipe]]
        slack=prior_budget-sum(lower.values(),Fraction(0))
        if slack<0 or needs and slack<=0:
            raise CapacityWitnessError('已知事实和剩余预算尚无共同的全日取整工时依据，请复核剩余工时')
        prior={recipe:bound+(slack/(2*len(needs)) if recipe in needs else 0) for recipe,bound in lower.items()}
        # These are declared existential allocations, never observed elapsed time.
        totals=defaultdict(int)
        for row in result.segments:
            if row.date==today and row.line_code==line: totals[index.models[row.model_code].recipe_code]+=row.quantity
        full=[]
        for row in result.segments:
            if row.date==today and row.line_code==line:
                recipe=index.models[row.model_code].recipe_code
                row=row.model_copy(update={'hours':str((prior.get(recipe,Fraction(0))+production[recipe])*row.quantity/totals[recipe])})
            full.append(row)
        prior_switches=[RecipeSwitch(id=derived_id(context,f'prior-switch:{line}:{n}'),line_code=line,from_recipe=a,to_recipe=b,
            allocations=[SwitchAllocation(date=today,order=n*2,hours=index.switch(a,b)['hours'])])
            for n,(a,b) in enumerate(_prior_switch_pairs(result,context,line,confirmation.current_recipe))]
        result=result.model_copy(update={'segments':full,'switches':result.switches+prior_switches})
        residual=CurrentDayWitness(date=today,line_code=line,basis_digest=capacity_basis(context),plan_digest=day_plan_digest(result,today,line),
            segments=[r for r in witness.segments if r.date==today],switches=[r for r in generated if any(a.date==today for a in r.allocations)],
            prior_recipe_hours=[RecipeHours(recipe_code=recipe,hours=str(value)) for recipe,value in sorted(prior.items())],prior_switch_hours=str(prior_switch),prior_switches=prior_switches)
        result=result.model_copy(update={'current_day_witnesses':[r for r in result.current_day_witnesses if r.line_code!=line or r.date!=today]+[residual]})
        _authorize_change(line_context.plan,result,single,line_context)
    return result
