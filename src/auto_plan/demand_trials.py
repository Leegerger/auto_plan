"""Frozen single-line qualified-output trials using the common plan engines."""
from datetime import date, timedelta
import hashlib
import json
from pydantic import field_validator
from .input_models import Code, Pieces
from .plans import PlanCommand, PlanProblem, protect
from .plan_models import EvaluationSnapshot
from .editing_scope import EditingScope
from .input_lookup import InputIndex
from .demand_operations import place_input, on_time_violation


class DemandTrial(PlanCommand):
    action_id: Code
    model_code: Code
    demand_week: date
    target_output: Pieces | None = None
    mode: str = 'on_time'
    line_code: Code
    start_date: date
    end_date: date
    scope: EditingScope

    @field_validator('demand_week')
    @classmethod
    def monday(cls, value):
        if value.weekday():
            raise ValueError('需求周须为周一')
        return value


def weekly(evaluation, model, week):
    return next((row for row in evaluation['weekly_results'] if row['model_code'] == model and row['week'] == str(week)), None)


def physical(evaluation, model, week, *, late=False):
    cutoff = str(week + timedelta(days=7))
    return sum(row['quantity'] for row in evaluation['attributions'] if row['model_code'] == model
               and row['demand_week'] == str(week) and (row['date'] >= cutoff if late else row['date'] < cutoff))


def confirmation_requirements(evaluation, model, weeks):
    result = []
    for row in evaluation['weekly_results']:
        if row['model_code'] != model or row['week'] not in weeks:
            continue
        portions = [value for value in evaluation['attributions'] if value['model_code'] == model and value['demand_week'] == row['week']]
        for kind in ('material', 'shipping'):
            pending = row['pending_' + kind]
            if not pending:
                continue
            usage = [value for value in evaluation['confirmation_usage'] if value['model_code'] == model and value['week'] == row['week'] and value['kind'] == kind]
            affected = [value for value in portions if value['pending_' + kind]]
            # Input apportionment repeats on each CT portion of one source/week.
            source_inputs = {value['source_id']: value['material_input_pending'] for value in affected}
            result.append({'model_code': model, 'week': row['week'], 'kind': kind, 'pending_output': pending,
                'required_input': sum(source_inputs.values()) if kind == 'material' else None,
                'earliest_input_date': min((value['input_date'] for value in affected), default=None),
                'latest_output_date': max((value['date'] for value in affected), default=None),
                'banned': any(value['banned'] for value in usage) or any(value['forbidden_' + kind] for value in affected), 'usage': usage})
    return result


def trial_in_unit(planner, unit, request, actor, now):
    command = PlanCommand.model_validate(request.model_dump(include=set(PlanCommand.model_fields)))
    prepared = planner._prepare(unit, command, now, actor)
    snapshot = EvaluationSnapshot.model_validate(prepared['snapshot'])
    index = InputIndex(snapshot.inputs)
    model, line = request.model_code, request.line_code
    if model not in index.models or not index.models[model].active or line not in index.lines or not index.lines[line].active:
        raise PlanProblem(422, '请选择有效机种及线体')
    if request.mode != 'on_time':
        raise PlanProblem(422, '请显式使用按时补排模式')
    if not snapshot.time.window_start <= request.demand_week <= snapshot.time.window_end:
        raise PlanProblem(422, '需求周必须位于冻结的十五周窗口')
    if not snapshot.time.window_start <= request.scope.start_date <= request.start_date <= request.end_date <= request.scope.end_date <= snapshot.time.window_end:
        raise PlanProblem(422, '补排投入日期须位于明确授权的窗口范围')
    if line not in request.scope.line_codes or request.scope.model_codes and model not in request.scope.model_codes:
        raise PlanProblem(422, '所选线体或机种不在调整范围')
    before = prepared['evaluation']
    selected = weekly(before, model, request.demand_week)
    if selected is None:
        raise PlanProblem(422, '所选机种需求周不存在')
    for goal in prepared.get('lineage', {}).get('demand_goals', {}).values():
        if (goal['model_code'], goal['demand_week']) == (model, str(request.demand_week)) and goal.get('remaining_output_target') != 0:
            raise PlanProblem(422, '此机种需求周已有补排目标，请保留原目标处理', goal_id=goal['id'])
    unarranged = max(selected['demand'] - selected['attributed'], 0)
    target = unarranged if request.target_output is None else request.target_output
    if target > unarranged:
        raise PlanProblem(422, '补排合格产出目标不可超过当前尚无归属安排的数量', unarranged_output=unarranged)
    scope = request.scope.model_copy(update={'line_codes': [line]})
    echoed = request.model_dump(mode='json', exclude=set(PlanCommand.model_fields)) | {'target_output': target, 'placement_scope': scope.model_dump(mode='json')}
    digest = hashlib.sha256(json.dumps({'snapshot': prepared['snapshot'], 'lineage': prepared['lineage'], 'operations': command.operations},
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    basis = {key: prepared[key] for key in ('revision', 'input_version', 'latest_adoption')} | {
        'as_of': snapshot.time.as_of.isoformat(), 'window_start': str(snapshot.time.window_start), 'digest': digest}
    blockers = []
    def block(code, reason, day=None):
        blockers.append({'code': code, 'reason': reason, 'date': str(day) if day else None, 'line_code': line, 'model_code': model})
    result_plan = snapshot.plan
    result_eval = before
    placements = []
    baseline_on_time = physical(before, model, request.demand_week)
    cutoff = str(request.demand_week + timedelta(days=7))
    incomplete = not selected['computation_complete']
    if incomplete:
        block('incomplete_output', '此机种已有投入的产出计算尚未完成，请先补齐参数')
    elif not target:
        block('already_attributed', '已有产出归属覆盖此需求，请处理确认，无须重复新增生产')
    else:
        from .production_time import production_date
        from .execution_rules import confirmed_day_capacity
        recipe = index.models[model].recipe_code
        today = production_date(snapshot.time.as_of)
        day = request.start_date
        while day <= request.end_date:
            if day < today:
                block('past_input', '已过去的生产日不能新增投入', day)
                day += timedelta(days=1)
                continue
            if day == today and confirmed_day_capacity(snapshot, line) is None:
                block('day_capacity_unconfirmed', '当日补排须先确认适用的剩余工时和当前现场状态依据', day)
                day += timedelta(days=1)
                continue
            if any(row.date == day and row.line_code == line and row.model_code == model for row in snapshot.locks):
                block('locked_cell', '所选日期、线体和机种的投入数量已锁定，须先显式解锁', day)
                day += timedelta(days=1)
                continue
            if not index.day(day, line)['available_hours']:
                block('calendar_capacity', '所选日期受 Holiday、保养或实验占用，没有可用工时', day)
                day += timedelta(days=1)
                continue
            parameters = index.critical_parameters(day.strftime('%Y-%m'), model)
            missing = [kind for kind, value in parameters.items() if value['state'] == 'missing']
            if missing:
                block('missing_parameters', f'{day:%Y-%m} 缺少 ' + '、'.join(missing), day)
                day += timedelta(days=1)
                continue
            capacity = index.capacity(day.strftime('%Y-%m'), recipe, line)
            if capacity['state'] != 'known' or not capacity['pieces']:
                block('recipe_capacity', '所选日期没有已知、允许且大于零的全日产能', day)
                day += timedelta(days=1)
                continue
            prefix = result_plan
            def candidate(quantity):
                placed = place_input(prefix, snapshot, scope, model, line, day, quantity, request.action_id)
                protect(prefix, placed.plan, snapshot.model_copy(update={'plan': prefix}))
                evaluation = planner._evaluation_json(snapshot.model_copy(update={'plan': placed.plan}), prepared['operation_issues'])
                return placed.plan, evaluation
            try:
                maximum, maximum_eval = candidate(capacity['pieces'] + 1)
            except (ValueError, PlanProblem) as error:
                block('placement_blocked', error.detail if isinstance(error, PlanProblem) else str(error), day)
                day += timedelta(days=1)
                continue
            old_ids = {row.id for row in prefix.segments}
            added = [row for row in maximum.segments if row.id not in old_ids]
            maximum_input = sum(row.quantity for row in added)
            if not maximum_input:
                block('no_remaining_capacity', '所选日期受到剩余容量、执行依据或数量锁限制，未新增投入', day)
                day += timedelta(days=1)
                continue
            if on_time_violation(before, maximum_eval, model, request.demand_week, {str(day)}):
                block('on_time_deadline', '所选投入会跨过目标需求截止点，本次按时模式不自动安排迟交', day)
                day += timedelta(days=1)
                continue
            progress = physical(maximum_eval, model, request.demand_week) - baseline_on_time
            needed = min(target, progress)
            def fifo_total(evaluation):
                return sum(row['quantity'] for row in evaluation['attributions'] if row['model_code'] == model
                           and row['demand_week'] <= str(request.demand_week) and row['date'] < cutoff)
            max_fifo = fifo_total(maximum_eval)
            if max_fifo <= fifo_total(result_eval):
                block('no_target_progress', '此日期新增投入不能增加较早或所选需求的归属产出', day)
                day += timedelta(days=1)
                continue
            low, high = 1, maximum_input
            best_plan, best_eval = maximum, maximum_eval
            while low < high:
                amount = (low + high) // 2
                proposed, evaluated = candidate(amount)
                reaches = physical(evaluated, model, request.demand_week) - baseline_on_time >= needed
                if progress < target:
                    reaches = reaches and fifo_total(evaluated) >= max_fifo
                if reaches:
                    high = amount
                    best_plan, best_eval = proposed, evaluated
                else:
                    low = amount + 1
            result_plan, result_eval = best_plan, best_eval
            placements.append({'date': str(day), 'quantity': low})
            if physical(result_eval, model, request.demand_week) - baseline_on_time >= target:
                break
            day += timedelta(days=1)
    incomplete = incomplete or not placements and any(row['code'] == 'missing_parameters' for row in blockers)
    if placements and on_time_violation(before, result_eval, model, request.demand_week, {row['date'] for row in placements}):
        block('on_time_deadline', '此部分目标会使已有较晚产出新归属到目标周，本次按时模式保留目标并要求重新选择')
        placements = []
    goal = {'id': request.action_id, 'model_code': model, 'demand_week': str(request.demand_week), 'requested_output': target,
            'baseline_attributed': selected['attributed'], 'desired_attributed': selected['attributed'] + target,
            'origin_revision': prepared['revision'], 'origin_input_version': prepared['input_version']}
    operation = {'kind': 'supplement_demand', 'action_id': request.action_id, 'model_code': model, 'demand_week': str(request.demand_week),
                 'mode': request.mode, 'line_code': line, 'start_date': str(request.start_date), 'end_date': str(request.end_date),
                 'scope': scope.model_dump(mode='json'), 'placements': placements, 'goal': goal}
    retain = operation | {'placements': []} if target and selected['computation_complete'] else None
    accepted = operation if placements else None
    preview = planner._prepare(unit, command.model_copy(update={'operations': command.operations + [accepted]}), now, actor) if accepted else prepared
    after = preview['evaluation']
    gain = physical(after, model, request.demand_week) - baseline_on_time
    remaining = max(target - gain, 0)
    final_selected = weekly(after, model, request.demand_week)
    changed_weeks = []
    for old in before['weekly_results']:
        new = weekly(after, old['model_code'], date.fromisoformat(old['week']))
        if old != new:
            week = date.fromisoformat(old['week'])
            changed_weeks.append({'model_code': old['model_code'], 'week': old['week'], 'before': old, 'after': new,
                'physical_on_time_before': physical(before, old['model_code'], week), 'physical_on_time_after': physical(after, old['model_code'], week),
                'physical_late_before': physical(before, old['model_code'], week, late=True), 'physical_late_after': physical(after, old['model_code'], week, late=True)})
    new_ids = {row.id for row in snapshot.plan.segments}
    actual = [row for row in preview['plan']['segments'] if row['id'] not in new_ids]
    return {'status': 'incomplete' if incomplete else 'complete' if target and not remaining else 'partial' if actual else 'blocked',
        'basis': basis, 'request': echoed, 'suggested_input': None if incomplete else sum(row['quantity'] for row in actual),
        'scheduled_on_time_output': None if incomplete else gain, 'valid_on_time_output': None if incomplete else final_selected['on_time'] - selected['on_time'],
        'remaining_output_target': None if incomplete else remaining, 'placements': actual,
        'switch_effects': {'before': before['switches'], 'after': after['switches']}, 'week_effects': changed_weeks,
        'required_confirmations': confirmation_requirements(after, model, {str(request.demand_week)} | {row['week'] for row in changed_weeks}),
        'blockers': blockers + after['adoption_conditions'] + [issue for issue in after['issues']
            if issue['severity'] in ('error', 'incomplete') and (issue.get('model_code') == model or issue.get('line_code') == line)],
        'operation': accepted, 'retain_operation': retain, 'preview': preview}
