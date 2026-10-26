"""Authoritative exact business evaluation, independent of any solver model."""
from collections import defaultdict
from fractions import Fraction
from .exact import hours
from .input_lookup import InputIndex
from .recipe_states import analyze_recipe_states
from .plan_models import (EvaluationSnapshot, EvaluationResult, RuleIssue, RecipeCapacity,
                          DayEvaluation, InitialStateEvaluation, SwitchEvaluation)


def evaluate(snapshot: EvaluationSnapshot, *, output_cache=None) -> EvaluationResult:
    return EvaluationResult(**_evaluate_fields(snapshot, output_cache=output_cache))


def _evaluation_json(snapshot, *, output_cache=None):
    """Private service assembly; every returned JSON container is owned."""
    values = _evaluate_fields(snapshot, output_cache=output_cache, json_mode=True)
    fields = ('output_sources', 'attributions', 'weekly_results')
    derived = {field: values.pop(field) for field in fields}
    result = EvaluationResult(**values).model_dump(mode='json')
    return result | derived


def _evaluate_fields(snapshot, *, output_cache=None, json_mode=False):
    index = InputIndex(snapshot.inputs)
    issues: list[RuleIssue] = []

    def problem(code, reason, *, segment=None, switch=None, day=None, line=None, severity='error', recipe=None, yield_month=None):
        issues.append(RuleIssue(code=code, reason=reason, severity=severity,
            yield_month=yield_month,
            date=segment.date if segment else day, line_code=segment.line_code if segment else switch.line_code if switch else line,
            model_code=segment.model_code if segment else None, recipe_code=recipe,
            segment_id=segment.id if segment else None, switch_id=switch.id if switch else None))

    for row in [*snapshot.plan.segments, *snapshot.plan.ordinary_idle]:
        if not snapshot.time.window_start <= row.date <= snapshot.time.window_end:
            problem('outside_window', '投入或空闲安排超出冻结的十五周排程窗口', day=row.date, line=row.line_code)
    for switch in snapshot.plan.switches:
        for row in switch.allocations:
            if not snapshot.time.window_start <= row.date <= snapshot.time.window_end:
                problem('outside_window', '切机日分配超出冻结的十五周排程窗口', switch=switch, day=row.date)

    # Supplied immutable contexts must not gain meaning from last-row-wins lookup.
    from .inputs import KEYS, SECTIONS
    for kind, fields in KEYS.items():
        seen = set()
        for row in getattr(getattr(snapshot.inputs, SECTIONS[kind]), kind):
            key = tuple(getattr(row, field, None) for field in fields)
            if key in seen:
                problem('input_duplicate', f'输入快照{kind}存在重复识别组合')
            seen.add(key)
            for field, known in [('model_code', index.models), ('recipe_code', index.recipes), ('line_code', index.lines), ('from_recipe', index.recipes), ('to_recipe', index.recipes)]:
                if hasattr(row, field) and getattr(row, field) not in known:
                    problem('unknown_master', f'输入快照{kind}引用不存在的编码')

    planned=defaultdict(int)
    for row in snapshot.plan.segments:
        planned[row.date,row.line_code,row.model_code]+=row.quantity
    locked=set()
    for lock in snapshot.locks:
        key=lock.date,lock.line_code,lock.model_code
        if key in locked:
            issues.append(RuleIssue(code='duplicate_lock',reason='数量锁存在重复日期、线体、机种组合',date=lock.date,line_code=lock.line_code,model_code=lock.model_code))
        locked.add(key)
        if lock.line_code not in index.lines or lock.model_code not in index.models:
            issues.append(RuleIssue(code='unknown_master',reason='数量锁引用不存在的线体或机种',date=lock.date,line_code=lock.line_code,model_code=lock.model_code))
        if snapshot.time.window_start<=lock.date<=snapshot.time.window_end and planned[key]!=lock.quantity:
            issues.append(RuleIssue(code='locked_cell',reason=f'锁定汇总数量{lock.quantity}片，当前安排{planned[key]}片，须先显式解锁',date=lock.date,line_code=lock.line_code,model_code=lock.model_code))

    from .execution_rules import capacity_plan as execution_capacity_plan, confirmed_day_capacity
    from .production_time import production_date
    capacity_plan=execution_capacity_plan(snapshot,issues)
    groups = defaultdict(lambda: defaultdict(list))
    daily_switch = defaultdict(Fraction)
    daily_idle_use = defaultdict(Fraction)
    idle_budget = {}
    initial_states = {}
    identifiers = set()
    for initial in capacity_plan.initial_states:
        if initial.line_code in initial_states:
            problem('duplicate_initial_state', '线体起始 Recipe 重复', line=initial.line_code)
        initial_states[initial.line_code] = initial.recipe_code
        if initial.line_code not in index.lines or initial.recipe_code is not None and initial.recipe_code not in index.recipes:
            problem('unknown_master', '起始状态引用不存在的线体或 Recipe', line=initial.line_code)
    for idle in capacity_plan.ordinary_idle:
        key = (idle.date, idle.line_code)
        if key in idle_budget:
            problem('duplicate_idle_budget', '同日线体普通空闲重复，不能重复抵扣', day=idle.date, line=idle.line_code)
        idle_budget[key] = Fraction(idle.hours)
        if idle.line_code not in index.lines:
            problem('unknown_master', '普通空闲引用不存在的线体', day=idle.date, line=idle.line_code)
    for segment in capacity_plan.segments:
        if segment.id in identifiers:
            problem('duplicate_id', '生产段或切机标识重复', segment=segment)
        identifiers.add(segment.id)
        if segment.model_code not in index.models or segment.line_code not in index.lines:
            problem('unknown_master', '机种或线体编码不存在', segment=segment)
            continue
        recipe = index.models[segment.model_code].recipe_code
        groups[(segment.date, segment.line_code)][recipe].append(segment)
    switch_results = []
    from .line_states import active_unfinished_jobs, unfinished_job_history
    unfinished = active_unfinished_jobs(snapshot)
    actual_jobs = {identity for identity,(record,resolved) in unfinished_job_history(snapshot).items()
                   if production_date(record.unfinished_switch.started_at)<=snapshot.time.window_end and (resolved is None or resolved>=snapshot.time.window_start)}
    for switch in capacity_plan.switches:
        if switch.id in identifiers:
            problem('duplicate_id', '生产段或切机标识重复', switch=switch)
        identifiers.add(switch.id)
        if switch.line_code not in index.lines or switch.from_recipe not in index.recipes or switch.to_recipe not in index.recipes:
            problem('unknown_master', '切机引用不存在的线体或 Recipe', switch=switch)
        duration = index.switch(switch.from_recipe, switch.to_recipe)
        total = Fraction(duration['hours'])
        actual = unfinished.get(switch.line_code)
        residual = switch.residual_record_id is not None
        verified = residual and actual is not None and actual.id == switch.residual_record_id and actual.unfinished_switch.job_id == switch.id and (actual.unfinished_switch.from_recipe,actual.unfinished_switch.to_recipe)==(switch.from_recipe,switch.to_recipe)
        if residual:
            if not verified:
                problem('residual_reference', '剩余切机须关联当前现场原作业，不能改变线体、原目标 Recipe 或使用过期依据', switch=switch)
            else:
                total = Fraction(actual.unfinished_switch.remaining_hours)
        allocated = sum((Fraction(a.hours) for a in switch.allocations), Fraction(0))
        idle = sum((Fraction(a.idle_hours) for a in switch.allocations), Fraction(0))
        if duration['state'] == 'same_recipe':
            problem('same_recipe_switch', '同 Recipe 无需切机，请移除切机事件', switch=switch)
        if residual and verified and allocated > total:
            problem('remaining_switch_total', '剩余切机分配不能超过现场确认的实际剩余工时', switch=switch, day=switch.allocations[0].date)
        elif not residual and allocated != total:
            problem('switch_total', '切机日分配合计必须精确等于有向切机总时间', switch=switch, day=switch.allocations[0].date)
        if duration['state'] == 'default_missing':
            problem('missing_switch_duration', '已知不同 Recipe 缺切机参数，按12小时计算，待补充', switch=switch, severity='warning')
        ordered = sorted(switch.allocations, key=lambda a: (a.date, a.order))
        for allocation in ordered:
            key = (allocation.date, switch.line_code)
            daily_switch[key] += Fraction(allocation.hours)
            daily_idle_use[key] += Fraction(allocation.idle_hours)
            if Fraction(allocation.idle_hours) > Fraction(allocation.hours):
                problem('idle_exceeds_allocation', '普通空闲抵扣不得超过该日实际切机占用', switch=switch, day=allocation.date)
        switch_results.append(SwitchEvaluation(id=switch.id, line_code=switch.line_code,
            from_recipe=switch.from_recipe, to_recipe=switch.to_recipe, duration_state=duration['state'],
            total_hours=hours(total), allocated_hours=hours(allocated), idle_hours=hours(idle), extra_hours=hours(max(total - idle, 0)),
            kind='residual' if residual else 'planned',job_id=switch.id if residual else None,residual_record_id=switch.residual_record_id,
            standard_hours=hours(actual.unfinished_switch.standard_hours) if verified and actual.unfinished_switch.standard_hours is not None else None,
            completed_hours=hours(actual.unfinished_switch.completed_hours) if verified and actual.unfinished_switch.completed_hours is not None else None))

    from .line_states import calculation_anchors
    confirmed = [row for row in calculation_anchors(snapshot) if row.effective_at <= snapshot.time.as_of
                 and (getattr(row,'unfinished_switch',None) is not None or confirmed_day_capacity(snapshot,row.line_code) is None)]
    states = analyze_recipe_states(capacity_plan, index, problem, confirmed_states=confirmed, window_start=snapshot.time.window_start, as_of=snapshot.time.as_of)
    initial_states = states.initial_states
    endings, all_lines = states.endings, states.lines

    result_days = []
    all_days = set(groups) | set(daily_switch) | set(idle_budget)
    for day, line in sorted(all_days):
        availability = index.day(day, line)
        confirmation=confirmed_day_capacity(snapshot,line) if day==production_date(snapshot.time.as_of) else None
        if confirmation is not None:
            availability=availability|{'available_hours':confirmation.remaining_hours}
        recipes = []
        total_hours = Fraction(0)
        for recipe, segments in sorted(groups[(day, line)].items()):
            allocated = sum((Fraction(s.hours) for s in segments), Fraction(0))
            quantity = sum(s.quantity for s in segments)
            capacity = index.capacity(day.strftime('%Y-%m'), recipe, line)
            exact_capacity = capacity['pieces'] * allocated / 24
            limit = -(-exact_capacity.numerator // exact_capacity.denominator)
            if capacity['state'] == 'forbidden':
                limit = 0
            recipes.append(RecipeCapacity(recipe_code=recipe, quantity=quantity, hours=hours(allocated),
                capacity_state=capacity['state'], full_day_capacity=capacity['pieces'], limit=limit,
                model_codes=sorted({s.model_code for s in segments})))
            if quantity > limit:
                for segment in segments:
                    problem('recipe_capacity', f'Recipe 汇总投入{quantity}超过精确工时上限{limit}', segment=segment, recipe=recipe)
            if capacity['state'] == 'missing' and quantity:
                problem('missing_capacity', '缺少全日产能，按零计算，待补充', segment=segments[0], recipe=recipe, severity='incomplete')
            total_hours += allocated
            for segment in segments:
                if segment.quantity:
                    for kind, parameter in index.critical_parameters(day.strftime('%Y-%m'), segment.model_code).items():
                        if parameter['state'] == 'missing':
                            problem('missing_' + kind, '缺少关键良率或 Cycle Time，相关产出尚未计算完成', segment=segment, recipe=recipe, severity='incomplete',
                                    yield_month=day.strftime('%Y-%m'))
        switch_hours = daily_switch[(day, line)]
        used_idle = daily_idle_use[(day, line)]
        reserved_idle = idle_budget.get((day, line), Fraction(0))
        if total_hours + switch_hours > Fraction(availability['available_hours']):
            problem('day_capacity', '生产与切机占用超过日可用工时', day=day, line=line)
        if used_idle > reserved_idle:
            problem('idle_reuse', '切机使用的普通空闲超过该日唯一空闲预算，不能重复使用', day=day, line=line)
        if total_hours + reserved_idle > Fraction(availability['available_hours']):
            problem('idle_production_overlap', '普通空闲不能重复用于生产或来自不可用时段', day=day, line=line)
        result_days.append(DayEvaluation(date=day, line_code=line, **availability, production_hours=hours(total_hours),
            switch_hours=hours(switch_hours), ordinary_idle_hours=hours(reserved_idle), used_idle_hours=hours(used_idle),
            occupied_hours=hours(total_hours + switch_hours), recipes=recipes, ending_recipe=endings.get((day, line))))
    for proof in snapshot.plan.current_day_witnesses:
        if proof.date!=production_date(snapshot.time.as_of) or confirmed_day_capacity(snapshot,proof.line_code) is None: continue
        for switch in proof.prior_switches:
            duration=index.switch(switch.from_recipe,switch.to_recipe)
            allocated=sum((Fraction(a.hours) for a in switch.allocations),Fraction(0))
            switch_results.append(SwitchEvaluation(id=switch.id,line_code=switch.line_code,from_recipe=switch.from_recipe,to_recipe=switch.to_recipe,
                duration_state=duration['state'],total_hours=hours(duration['hours']),allocated_hours=hours(allocated),idle_hours=hours(0),extra_hours=hours(allocated)))
    initial = [InitialStateEvaluation(line_code=line, recipe_code=initial_states.get(line), state='known' if initial_states.get(line) else 'unknown_initial')
               for line in sorted(all_lines)]
    valid = not any(i.severity == 'error' for i in issues)
    from .output_rules import output_evaluation
    output = output_evaluation(snapshot, index, output_cache=output_cache if not issues else None, json_mode=json_mode)
    issues.extend(output.pop('issues'))
    from .stability import compare_stability
    stability=compare_stability(snapshot)
    for week in stability.weeks:
        if week.exceeds_tolerance:
            issues.append(RuleIssue(code='stability_tolerance',reason=f'第{week.number}周变化率{week.rate_display}超过容忍度{week.tolerance_percent}%，可核对后明确采用',severity='warning',demand_week=week.week))
        elif week.state=='zero_baseline':
            issues.append(RuleIssue(code='stability_zero_addition',reason=f'原计划为零，新增 {week.added} 片，请核对',severity='warning',demand_week=week.week))
    from .line_states import adoption_conditions
    conditions = adoption_conditions(snapshot)
    issues.extend(conditions)
    complete = not any(i.severity == 'incomplete' for i in issues)
    from .demand_attention import demand_attention
    return dict(time=snapshot.time, capacity_valid=valid, computation_complete=complete, executable=not any(i.severity == 'error' for i in issues) and complete and not conditions, adoption_conditions=conditions,
        issues=issues, days=result_days, switches=switch_results, initial_states=initial,
        switch_count=len({('actual',s.id) if s.residual_record_id else ('planned',s.id) for s in snapshot.plan.switches if s.from_recipe != s.to_recipe}
                         | {('actual',identity) for identity in actual_jobs}), stability=stability,
        demand_attention=demand_attention(snapshot,output,issues,index), **output)
