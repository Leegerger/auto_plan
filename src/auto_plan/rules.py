"""Authoritative exact business evaluation, independent of any solver model."""
from collections import defaultdict
from fractions import Fraction
from .exact import hours
from .input_lookup import InputIndex
from .recipe_states import analyze_recipe_states
from .plan_models import (EvaluationSnapshot, EvaluationResult, RuleIssue, RecipeCapacity,
                          DayEvaluation, InitialStateEvaluation, SwitchEvaluation)


def evaluate(snapshot: EvaluationSnapshot) -> EvaluationResult:
    index = InputIndex(snapshot.inputs)
    issues: list[RuleIssue] = []

    def problem(code, reason, *, segment=None, switch=None, day=None, line=None, severity='error', recipe=None):
        issues.append(RuleIssue(code=code, reason=reason, severity=severity,
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
            key = tuple(getattr(row, field) for field in fields)
            if key in seen:
                problem('input_duplicate', f'输入快照{kind}存在重复识别组合')
            seen.add(key)
            for field, known in [('model_code', index.models), ('recipe_code', index.recipes), ('line_code', index.lines), ('from_recipe', index.recipes), ('to_recipe', index.recipes)]:
                if hasattr(row, field) and getattr(row, field) not in known:
                    problem('unknown_master', f'输入快照{kind}引用不存在的编码')

    groups = defaultdict(lambda: defaultdict(list))
    daily_switch = defaultdict(Fraction)
    daily_idle_use = defaultdict(Fraction)
    idle_budget = {}
    initial_states = {}
    identifiers = set()
    for initial in snapshot.plan.initial_states:
        if initial.line_code in initial_states:
            problem('duplicate_initial_state', '线体起始 Recipe 重复', line=initial.line_code)
        initial_states[initial.line_code] = initial.recipe_code
        if initial.line_code not in index.lines or initial.recipe_code is not None and initial.recipe_code not in index.recipes:
            problem('unknown_master', '起始状态引用不存在的线体或 Recipe', line=initial.line_code)
    for idle in snapshot.plan.ordinary_idle:
        key = (idle.date, idle.line_code)
        if key in idle_budget:
            problem('duplicate_idle_budget', '同日线体普通空闲重复，不能重复抵扣', day=idle.date, line=idle.line_code)
        idle_budget[key] = Fraction(idle.hours)
        if idle.line_code not in index.lines:
            problem('unknown_master', '普通空闲引用不存在的线体', day=idle.date, line=idle.line_code)
    for segment in snapshot.plan.segments:
        if segment.id in identifiers:
            problem('duplicate_id', '生产段或切机标识重复', segment=segment)
        identifiers.add(segment.id)
        if segment.model_code not in index.models or segment.line_code not in index.lines:
            problem('unknown_master', '机种或线体编码不存在', segment=segment)
            continue
        recipe = index.models[segment.model_code].recipe_code
        groups[(segment.date, segment.line_code)][recipe].append(segment)
    switch_results = []
    for switch in snapshot.plan.switches:
        if switch.id in identifiers:
            problem('duplicate_id', '生产段或切机标识重复', switch=switch)
        identifiers.add(switch.id)
        if switch.line_code not in index.lines or switch.from_recipe not in index.recipes or switch.to_recipe not in index.recipes:
            problem('unknown_master', '切机引用不存在的线体或 Recipe', switch=switch)
        duration = index.switch(switch.from_recipe, switch.to_recipe)
        total = Fraction(duration['hours'])
        allocated = sum((Fraction(a.hours) for a in switch.allocations), Fraction(0))
        idle = sum((Fraction(a.idle_hours) for a in switch.allocations), Fraction(0))
        if duration['state'] == 'same_recipe':
            problem('same_recipe_switch', '同 Recipe 无需切机，请移除切机事件', switch=switch)
        if allocated != total:
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
            total_hours=hours(total), allocated_hours=hours(allocated), idle_hours=hours(idle), extra_hours=hours(max(total - idle, 0))))

    states = analyze_recipe_states(snapshot.plan, index, problem)
    endings, all_lines = states.endings, states.lines

    result_days = []
    all_days = set(groups) | set(daily_switch) | set(idle_budget)
    for day, line in sorted(all_days):
        availability = index.day(day, line)
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
                            problem('missing_' + kind, '缺少关键良率或 Cycle Time，相关产出尚未计算完成', segment=segment, recipe=recipe, severity='incomplete')
        switch_hours = daily_switch[(day, line)]
        used_idle = daily_idle_use[(day, line)]
        reserved_idle = idle_budget.get((day, line), Fraction(0))
        if total_hours + switch_hours > availability['available_hours']:
            problem('day_capacity', '生产与切机占用超过日可用工时', day=day, line=line)
        if used_idle > reserved_idle:
            problem('idle_reuse', '切机使用的普通空闲超过该日唯一空闲预算，不能重复使用', day=day, line=line)
        if total_hours + reserved_idle > availability['available_hours']:
            problem('idle_production_overlap', '普通空闲不能重复用于生产或来自不可用时段', day=day, line=line)
        result_days.append(DayEvaluation(date=day, line_code=line, **availability, production_hours=hours(total_hours),
            switch_hours=hours(switch_hours), ordinary_idle_hours=hours(reserved_idle), used_idle_hours=hours(used_idle),
            occupied_hours=hours(total_hours + switch_hours), recipes=recipes, ending_recipe=endings.get((day, line))))
    initial = [InitialStateEvaluation(line_code=line, recipe_code=initial_states.get(line), state='known' if initial_states.get(line) else 'unknown_initial')
               for line in sorted(all_lines)]
    valid = not any(i.severity == 'error' for i in issues)
    from .output_rules import output_evaluation
    output = output_evaluation(snapshot, index)
    issues.extend(output.pop('issues'))
    complete = not any(i.severity == 'incomplete' for i in issues)
    return EvaluationResult(time=snapshot.time, capacity_valid=valid, computation_complete=complete, executable=not any(i.severity == 'error' for i in issues) and complete,
        issues=issues, days=result_days, switches=switch_results, initial_states=initial,
        switch_count=sum(s.from_recipe != s.to_recipe for s in snapshot.plan.switches), **output)
