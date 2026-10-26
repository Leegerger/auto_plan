"""Qualified-output target trials through the public shared planning API."""
from copy import deepcopy
from uuid import uuid4
from auto_plan.input_models import InputSnapshot
from tests.test_plan_transactions import shared_client, post
from tests.test_execution_reset import current_command, head, preview
from tests.line_state_support import confirm_states
from tests.test_output_preview import confirmation


def configure(inputs, *, demand=72, week='2026-10-12', line_yield='90', model_yield='80', ct='0', capacity=1000):
    data = inputs.read().model_dump(mode='json')
    data['business']['demands'] = [{'model_code': 'M', 'week': week, 'quantity': demand}]
    for category, value in [('line_yields', line_yield), ('model_yields', model_yield)]:
        next(row for row in data['parameters'][category] if row['model_code'] == 'M')['percent'] = value
    next(row for row in data['parameters']['cycle_times'] if row['model_code'] == 'M')['days'] = ct
    next(row for row in data['parameters']['capacities'] if row['recipe_code'] == 'R' and row['line_code'] == 'L')['pieces'] = capacity
    inputs.versions[1] = InputSnapshot.model_validate(data)
    return data


def trial(client, *, operations=None, **changes):
    command = current_command(client, *(operations or []))
    body = {key: value for key, value in command.items() if key not in ('operation_id', 'pending')}
    body.update(action_id=str(uuid4()), model_code='M', demand_week='2026-10-12', target_output=72, mode='on_time',
                line_code='L', start_date='2026-10-15', end_date='2026-10-15',
                scope={'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L'], 'model_codes': []})
    body.update(changes)
    return post(client, 'demand-trial', body)


def test_trial_finds_99_input_for_72_qualified_output_without_using_nominal_floor(shared_client):
    client, plans, inputs = shared_client
    configure(inputs)
    before = head(client)

    checked = trial(client, operations=[confirm_states()])

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert result['status'] == 'complete'
    assert (result['suggested_input'], result['scheduled_on_time_output'], result['valid_on_time_output'], result['remaining_output_target']) == (99, 72, 72, 0)
    assert [row['quantity'] for row in result['placements']] == [99]
    assert result['operation']['kind'] == 'supplement_demand'
    assert result['preview']['evaluation']['capacity_valid'] is True
    assert result['preview']['context']['line_states']['records'][0]['actor'] == 'EDITOR'
    assert head(client) == before


def test_partial_trial_saves_600_input_and_a_400_qualified_output_goal(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=1000, line_yield='100', model_yield='100', capacity=600)

    checked = trial(client, target_output=1000, operations=[confirm_states()])

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert (result['status'], result['suggested_input'], result['scheduled_on_time_output'], result['remaining_output_target']) == ('partial', 600, 600, 400)
    assert {row['date'] for row in result['placements']} == {'2026-10-15'}
    goal_id = result['operation']['goal']['id']
    assert result['preview']['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 400
    assert result['preview']['unallocated'] == []
    body = current_command(client, confirm_states(), result['operation'])
    saved = post(client, 'save', body)
    assert saved.status_code == 200, saved.text
    assert post(client, 'save', body).json() == saved.json()
    assert post(client, 'verify', {'operation_id': body['operation_id']}).json() == saved.json()
    current = head(client)
    goal = current['lineage']['demand_goals'][goal_id]
    assert (goal['requested_output'], goal['desired_attributed'], goal['remaining_output_target']) == (1000, 1000, 400)
    assert current['unallocated'] == []
    assert current['latest_adoption'] == 0
    removed = preview(client, {'kind': 'patch_plan', 'remove_segments': [row['id'] for row in result['placements']]})
    assert removed.status_code == 200, removed.text
    assert removed.json()['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 1000


def test_fifo_trial_arranges_earlier_300_then_selected_500_and_explains_shipping(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs, demand=500, week='2026-10-19', line_yield='100', model_yield='100')
    data['business']['demands'].insert(0, {'model_code': 'M', 'week': '2026-10-12', 'quantity': 300})
    inputs.versions[1] = InputSnapshot.model_validate(data)

    checked = trial(client, demand_week='2026-10-19', target_output=500, start_date='2026-10-20', end_date='2026-10-20',
                    scope={'start_date': '2026-10-20', 'end_date': '2026-10-20', 'line_codes': ['L', 'B'], 'model_codes': []})

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert (result['suggested_input'], result['scheduled_on_time_output'], result['remaining_output_target']) == (800, 500, 0)
    assert result['request']['scope']['line_codes'] == ['L', 'B']
    assert result['operation']['scope']['line_codes'] == ['L']
    earlier, selected = result['week_effects']
    assert (earlier['week'], earlier['physical_late_after'], earlier['after']['pending_shipping']) == ('2026-10-12', 300, 300)
    assert (selected['week'], selected['physical_on_time_after'], selected['after']['on_time']) == ('2026-10-19', 500, 500)
    requirement, = result['required_confirmations']
    assert (requirement['kind'], requirement['week'], requirement['pending_output'], requirement['latest_output_date']) == ('shipping', '2026-10-12', 300, '2026-10-20')
    assert requirement['banned'] is False


def test_split_rounding_uses_one_input_even_when_valid_coverage_is_nonmonotone(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs, demand=2, week='2026-10-19', line_yield='100', model_yield='100', ct='0.5')
    data['business']['confirmations'] = [confirmation('material', '2026-10-19', 1, '2026-10-15')]
    inputs.versions[1] = InputSnapshot.model_validate(data)

    checked = trial(client, demand_week='2026-10-19', target_output=2)

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert (result['suggested_input'], result['scheduled_on_time_output'], result['valid_on_time_output'], result['remaining_output_target']) == (1, 2, 2, 0)
    counterexample = {'inputs': data, 'plan': {'segments': [{'id': 'two', 'date': '2026-10-15', 'line_code': 'L', 'order': 0, 'model_code': 'M', 'quantity': 2, 'hours': '1'}]}}
    evaluated = post(client, 'evaluate', {'snapshot': counterexample})
    assert evaluated.status_code == 200, evaluated.text
    row, = evaluated.json()['weekly_results']
    assert (row['attributed'], row['on_time'], row['pending_material']) == (2, 1, 1)


def test_trial_uses_cross_line_same_date_rounding_and_preserves_other_authorized_line(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=304, line_yield='100', model_yield='100', ct='0.5')
    existing = {'id': 'other-line', 'date': '2026-10-15', 'line_code': 'B', 'order': 5, 'model_code': 'M', 'quantity': 301, 'hours': '8'}

    checked = trial(client, target_output=2, operations=[{'kind': 'patch_plan', 'segments': [existing]}],
                    scope={'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L', 'B'], 'model_codes': ['M']})

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert (result['suggested_input'], result['scheduled_on_time_output']) == (2, 2)
    assert next(row for row in result['preview']['plan']['segments'] if row['id'] == 'other-line') == existing
    source, = result['preview']['evaluation']['output_sources']
    assert (source['quantity'], source['allocated_output']) == (303, 304)


def test_attributed_pending_output_defaults_to_confirmation_without_duplicate_production(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs, demand=1000, week='2026-10-19', line_yield='100', model_yield='100')
    data['business']['confirmations'] = [confirmation('material', '2026-10-19', 400, '2026-10-15')]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    existing = {'id': 'pending', 'date': '2026-10-15', 'line_code': 'B', 'order': 0, 'model_code': 'M', 'quantity': 1000, 'hours': '24'}

    checked = trial(client, demand_week='2026-10-19', target_output=None, operations=[{'kind': 'patch_plan', 'segments': [existing]}])

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert (result['status'], result['request']['target_output'], result['suggested_input'], result['remaining_output_target']) == ('blocked', 0, 0, 0)
    assert result['placements'] == []
    assert result['operation'] is None
    requirement, = result['required_confirmations']
    assert (requirement['kind'], requirement['pending_output'], requirement['required_input']) == ('material', 600, 600)


def test_missing_actual_input_month_is_incomplete_instead_of_zero_recommendation(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs)
    data['parameters']['line_yields'] = [row for row in data['parameters']['line_yields'] if row['model_code'] != 'M']
    inputs.versions[1] = InputSnapshot.model_validate(data)

    checked = trial(client)

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert result['status'] == 'incomplete'
    assert result['suggested_input'] is None
    assert result['remaining_output_target'] is None
    assert result['operation'] is None
    assert any(row['code'] == 'missing_parameters' for row in result['blockers'])


def test_history_restores_source_goal_intent_and_recalculates_with_current_yield(shared_client):
    client, plans, inputs = shared_client
    configure(inputs, demand=1000, line_yield='100', model_yield='100', capacity=600)
    result = trial(client, target_output=1000).json()
    goal_id = result['operation']['goal']['id']
    assert post(client, 'save', current_command(client, result['operation'])).status_code == 200
    original = client.get('/auto_plan/api/plans/revisions/1').json()
    assert post(client, 'save', current_command(client, {'kind': 'replace_plan', 'plan': {}})).status_code == 200
    # External-storage fixture for a legacy head without the new optional
    # metadata. The immutable source remains unchanged and is read via HTTP.
    plans.revisions[2]['lineage'].pop('demand_goals')
    changed = client.post('/auto_plan/api/inputs/apply', json={'base_version': head(client)['input_version'],
        'changes': {'model_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '50'}]}})
    assert changed.status_code == 200, changed.text

    restored = preview(client, {'kind': 'restore_history', 'source': {'kind': 'revision', 'id': 1}})

    assert restored.status_code == 200, restored.text
    goal = restored.json()['lineage']['demand_goals'][goal_id]
    assert (goal['requested_output'], goal['current_attributed'], goal['remaining_output_target']) == (1000, 300, 700)
    assert client.get('/auto_plan/api/plans/revisions/1').json() == original


def test_goal_only_actions_conflict_for_same_demand_and_merge_unrelated_intents(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs)
    data['business']['demands'].append({'model_code': 'N', 'week': '2026-10-12', 'quantity': 50})
    inputs.versions[1] = InputSnapshot.model_validate(data)
    first = trial(client).json()['retain_operation']
    related = trial(client).json()['retain_operation']
    unrelated_response = trial(client, model_code='N', target_output=50, line_code='B',
        scope={'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['B'], 'model_codes': []})
    assert unrelated_response.status_code == 200, unrelated_response.text
    unrelated = unrelated_response.json()['retain_operation']
    a, b, competing = [current_command(client, operation) for operation in (first, unrelated, related)]

    assert post(client, 'save', a).status_code == 200
    merged = post(client, 'save', b)

    assert merged.status_code == 200, merged.text
    conflict = post(client, 'save', competing)
    assert conflict.status_code == 409, conflict.text
    assert ['REQ', 'M', '2026-10-12'] in conflict.json()['conflicts']
    current = head(client)
    assert current['plan']['segments'] == []
    assert current['input_version'] == 1
    goals = current['lineage']['demand_goals']
    assert {goal['model_code']: goal['remaining_output_target'] for goal in goals.values()} == {'M': 72, 'N': 50}


def test_accepted_quantities_do_not_increase_when_an_earlier_queue_action_is_undone(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=1000, line_yield='100', model_yield='100')
    existing = {'kind': 'append_fill', 'model_code': 'M', 'quantity': 100, 'line_code': 'L', 'start_date': '2026-10-15', 'end_date': '2026-10-15',
                'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L'], 'model_codes': []}}
    result = trial(client, target_output=100, operations=[existing]).json()
    assert result['suggested_input'] == 100

    without_earlier = preview(client, result['operation'])

    assert without_earlier.status_code == 200, without_earlier.text
    value = without_earlier.json()
    assert sum(row['quantity'] for row in value['plan']['segments']) == 100
    goal = value['lineage']['demand_goals'][result['operation']['goal']['id']]
    assert (goal['desired_attributed'], goal['current_attributed'], goal['remaining_output_target']) == (200, 100, 100)


def test_on_time_trial_keeps_deadline_and_accepted_action_rechecks_changed_ct(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=72, line_yield='100', model_yield='100')
    date_fields = {'start_date': '2026-10-18', 'end_date': '2026-10-18',
                   'scope': {'start_date': '2026-10-18', 'end_date': '2026-10-18', 'line_codes': ['L'], 'model_codes': []}}
    result = trial(client, **date_fields).json()
    assert (result['status'], result['suggested_input']) == ('complete', 72)
    updated = client.post('/auto_plan/api/inputs/apply', json={'base_version': head(client)['input_version'],
        'changes': {'cycle_times': [{'model_code': 'M', 'days': '0.5'}]}})
    assert updated.status_code == 200, updated.text

    stale_arrangement = preview(client, result['operation'])

    assert stale_arrangement.status_code == 422, stale_arrangement.text
    assert '截止' in stale_arrangement.json()['detail']
    checked = trial(client, **date_fields)
    assert checked.status_code == 200, checked.text
    blocked = checked.json()
    assert (blocked['status'], blocked['remaining_output_target']) == ('blocked', 72)
    assert blocked['operation'] is None
    assert any(row['code'] == 'on_time_deadline' for row in blocked['blockers'])


def test_zero_lock_keeps_goal_and_explains_exact_protection_without_extending_dates(shared_client):
    client, _, inputs = shared_client
    configure(inputs)
    lock = {'kind': 'lock_cells', 'cells': [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 0}]}

    checked = trial(client, operations=[lock])

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert (result['status'], result['suggested_input'], result['remaining_output_target']) == ('blocked', 0, 72)
    assert result['operation'] is None
    assert result['retain_operation']['goal']['requested_output'] == 72
    assert any(row['code'] == 'locked_cell' and row['date'] == '2026-10-15' for row in result['blockers'])
    assert result['preview']['plan']['segments'] == []


def test_actual_job_trial_reserves_four_hours_and_retains_review_and_outside_work(shared_client):
    from tests.test_switch_remaining_certificate import unfinished
    client, plans, inputs = shared_client
    data = configure(inputs, demand=1000, line_yield='100', model_yield='100')
    data['business']['demands'][0]['model_code'] = 'P'
    data['parameters']['switch_durations'][0]['hours'] = '12'
    inputs.versions[1] = InputSnapshot.model_validate(data)
    plans.revisions[0]['operation_issues'] = [{'id': 'retained-review', 'code': 'line_state_review', 'line_code': 'L',
        'review_after': '2026-10-13T08:00:00+08:00', 'reason': '旧计划执行尚待核对', 'blocks_adoption': True}]
    outside = {'id': 'other-line', 'date': '2026-10-16', 'line_code': 'B', 'order': 7, 'model_code': 'M', 'quantity': 100, 'hours': '3'}
    source_ops = [unfinished(), {'kind': 'patch_plan', 'segments': [outside]}]

    checked = trial(client, operations=source_ops, model_code='P', target_output=1000,
                    scope={'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L', 'B'], 'model_codes': []})

    assert checked.status_code == 200, checked.text[:500]
    result = checked.json()
    assert (result['status'], result['suggested_input'], result['remaining_output_target']) == ('partial', 834, 166)
    actual, = result['preview']['plan']['switches']
    record, = result['preview']['context']['line_states']['records']
    assert (actual['id'], actual['residual_record_id'], actual['from_recipe'], actual['to_recipe']) == ('actual-r-s', record['id'], 'R', 'S')
    assert [row['hours'] for row in actual['allocations']] == ['4']
    assert next(row for row in result['preview']['plan']['segments'] if row['id'] == 'other-line') == outside
    assert result['preview']['snapshot']['state_reviews'][0]['id'] == 'retained-review'
    assert {'remaining_switch_continuity', 'line_state_review'} <= {row['code'] for row in result['blockers']}
    week = next(row for row in result['preview']['workspace_view']['line_weeks'] if row['line_code'] == 'L' and row['week'] == '2026-10-12')
    assert (week['new_switch_count'], week['carried_switch_count'], week['switch_hours']['exact']) == (0, 1, '4')
    assert result['preview']['evaluation']['capacity_valid'] is True
    assert result['preview']['evaluation']['executable'] is False


def test_execution_facts_remain_frozen_and_past_dates_are_explained(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=100, line_yield='100', model_yield='100')
    fact = {'date': '2026-10-13', 'line_code': 'L', 'model_code': 'M', 'quantity': 60}
    execution = {'kind': 'record_execution', 'rows': [fact], 'cutoff': '2026-10-14T08:00:00+08:00'}

    checked = trial(client, operations=[execution], target_output=40)

    assert checked.status_code == 200, checked.text[:500]
    result = checked.json()
    assert result['suggested_input'] == 40
    assert result['preview']['snapshot']['execution']['facts'] == [fact]
    assert {row['date'] for row in result['placements']} == {'2026-10-15'}
    blocked = trial(client, operations=[execution], target_output=40, start_date='2026-10-13', end_date='2026-10-13',
                    scope={'start_date': '2026-10-13', 'end_date': '2026-10-13', 'line_codes': ['L'], 'model_codes': []}).json()
    assert (blocked['status'], blocked['remaining_output_target']) == ('blocked', 40)
    assert any(row['code'] == 'past_input' for row in blocked['blockers'])


def test_missing_unused_later_month_does_not_invalidate_completed_actual_date_trial(shared_client):
    client, _, inputs = shared_client
    configure(inputs, week='2026-10-26')

    checked = trial(client, demand_week='2026-10-26', start_date='2026-10-31', end_date='2026-11-01',
                    scope={'start_date': '2026-10-31', 'end_date': '2026-11-01', 'line_codes': ['L'], 'model_codes': []})

    assert checked.status_code == 200, checked.text[:500]
    result = checked.json()
    assert (result['status'], result['suggested_input'], result['remaining_output_target']) == ('complete', 99, 0)
    assert {row['date'] for row in result['placements']} == {'2026-10-31'}


def test_existing_goal_is_refreshed_before_rejecting_a_duplicate_after_input_change(shared_client):
    client, _, inputs = shared_client
    configure(inputs)
    result = trial(client).json()
    assert post(client, 'save', current_command(client, result['operation'])).status_code == 200
    changed = client.post('/auto_plan/api/inputs/apply', json={'base_version': head(client)['input_version'],
        'changes': {'model_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '50'}]}})
    assert changed.status_code == 200, changed.text
    assert head(client)['lineage']['demand_goals'][result['operation']['goal']['id']]['remaining_output_target'] == 27

    duplicate = trial(client, target_output=20)

    assert duplicate.status_code == 422, duplicate.text[:500]
    assert '已有补排目标' in duplicate.json()['detail']


def test_partial_target_does_not_silently_reattribute_existing_late_output_to_selected_week(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs, demand=500, week='2026-10-19', line_yield='100', model_yield='100')
    data['business']['demands'].insert(0, {'model_code': 'M', 'week': '2026-10-12', 'quantity': 300})
    inputs.versions[1] = InputSnapshot.model_validate(data)
    later = {'id': 'existing-late', 'date': '2026-10-27', 'line_code': 'B', 'order': 0, 'model_code': 'M', 'quantity': 100, 'hours': '3'}

    checked = trial(client, demand_week='2026-10-19', target_output=100, operations=[{'kind': 'patch_plan', 'segments': [later]}],
                    start_date='2026-10-20', end_date='2026-10-20',
                    scope={'start_date': '2026-10-20', 'end_date': '2026-10-20', 'line_codes': ['L'], 'model_codes': []})

    assert checked.status_code == 200, checked.text[:500]
    result = checked.json()
    assert (result['status'], result['remaining_output_target']) == ('blocked', 100)
    assert result['operation'] is None
    assert result['preview']['plan']['segments'] == [later]
    assert any(row['code'] == 'on_time_deadline' for row in result['blockers'])


def test_multi_day_fifo_keeps_a_prefix_when_full_range_removes_temporary_selected_late_output(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs, demand=500, week='2026-10-19', line_yield='100', model_yield='100', capacity=500)
    data['business']['demands'].insert(0, {'model_code': 'M', 'week': '2026-10-12', 'quantity': 300})
    inputs.versions[1] = InputSnapshot.model_validate(data)
    later = {'id': 'existing-late', 'date': '2026-10-27', 'line_code': 'B', 'order': 0, 'model_code': 'M', 'quantity': 100, 'hours': '3'}
    source = {'kind': 'patch_plan', 'segments': [later]}
    scope = {'start_date': '2026-10-20', 'end_date': '2026-10-21', 'line_codes': ['L'], 'model_codes': []}
    before = head(client)
    explicit = [{'kind': 'append_fill', 'model_code': 'M', 'line_code': 'L', 'quantity': quantity,
                 'start_date': day, 'end_date': day, 'scope': scope} for day, quantity in [('2026-10-20', 500), ('2026-10-21', 300)]]
    witness = preview(client, source, *explicit)
    assert witness.status_code == 200, witness.text[:500]
    assert witness.json()['evaluation']['capacity_valid'] is True
    assert next(row for row in witness.json()['evaluation']['weekly_results'] if row['week'] == '2026-10-19')['on_time'] == 500

    checked = trial(client, demand_week='2026-10-19', target_output=500, operations=[source],
                    start_date='2026-10-20', end_date='2026-10-21', scope=scope)

    assert checked.status_code == 200, checked.text[:500]
    result = checked.json()
    assert (result['status'], result['suggested_input'], result['scheduled_on_time_output'], result['remaining_output_target']) == ('complete', 800, 500, 0)
    assert [(row['date'], row['line_code'], row['quantity']) for row in result['placements']] == [('2026-10-20', 'L', 500), ('2026-10-21', 'L', 300)]
    selected = next(row for row in result['week_effects'] if row['week'] == '2026-10-19')
    assert (selected['physical_on_time_after'], selected['physical_late_after']) == (500, 0)
    assert next(row for row in result['preview']['plan']['segments'] if row['id'] == 'existing-late') == later
    assert not any(row['code'] == 'on_time_deadline' for row in result['blockers'])
    replayed = preview(client, source, result['operation'])
    assert replayed.status_code == 200, replayed.text[:500]
    assert replayed.json()['plan'] == result['preview']['plan']
    assert head(client) == before


def test_trial_rejects_stale_inputs_and_viewer_requests_without_writing(shared_client):
    client, _, inputs = shared_client
    configure(inputs)
    before = head(client)
    result = trial(client).json()
    request = {key: value for key, value in result['request'].items() if key != 'placement_scope'} | {
        'base_revision': before['revision'], 'input_version': before['input_version'], 'latest_adoption': before['latest_adoption'], 'operations': []}
    updated = client.post('/auto_plan/api/inputs/apply', json={'base_version': before['input_version'],
        'changes': {'model_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '50'}]}})
    assert updated.status_code == 200, updated.text

    stale = post(client, 'demand-trial', request)

    assert stale.status_code == 409, stale.text[:500]
    assert stale.json()['input_diff']
    assert head(client)['revision'] == 0
    client.app.state.access_store.users['VIEWER'] = {'user_id': 'VIEWER', 'roles': ['viewer'], 'active': True}
    assert client.post('/auto_plan/api/auth/login', json={'username': 'VIEWER', 'password': 'company-secret'}).status_code == 200
    denied = trial(client)
    assert denied.status_code == 403, denied.text
    assert head(client)['revision'] == 0


def test_durable_goal_becomes_unavailable_after_its_demand_week_leaves_window(shared_client):
    from datetime import datetime, timezone
    client, _, inputs = shared_client
    configure(inputs)
    result = trial(client).json()
    assert post(client, 'save', current_command(client, result['retain_operation'])).status_code == 200
    client.app.state.clock = lambda: datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200

    goal = head(client)['lineage']['demand_goals'][result['retain_operation']['goal']['id']]

    assert goal['status'] == 'unavailable'
    assert goal['remaining_output_target'] is None


def test_current_day_target_uses_record_bound_budget_and_charges_only_actual_remainder(shared_client):
    from tests.test_switch_remaining_certificate import unfinished
    client, _, inputs = shared_client
    data = configure(inputs, demand=1000, line_yield='100', model_yield='100')
    data['business']['demands'][0]['model_code'] = 'P'
    data['parameters']['switch_durations'][0]['hours'] = '12'
    inputs.versions[1] = InputSnapshot.model_validate(data)
    state = unfinished()
    prepared = preview(client, state)
    assert prepared.status_code == 200, prepared.text[:500]
    record, = prepared.json()['context']['line_states']['records']
    budget = {'kind': 'confirm_day_capacity', 'observed_at': '2026-10-14T08:00:00+08:00',
              'confirmations': [{'line_code': 'L', 'residual_record_id': record['id'], 'remaining_hours': '24'}]}
    dates = {'start_date': '2026-10-14', 'end_date': '2026-10-14',
             'scope': {'start_date': '2026-10-14', 'end_date': '2026-10-14', 'line_codes': ['L'], 'model_codes': []}}
    unknown_execution = trial(client, operations=[state, budget], model_code='P', target_output=1000, **dates)
    assert unknown_execution.status_code == 200, unknown_execution.text[:500]
    assert unknown_execution.json()['status'] == 'incomplete'
    assert unknown_execution.json()['suggested_input'] is None
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-14', 'line_code': 'L', 'model_code': 'P', 'quantity': 0}],
                 'cutoff': '2026-10-14T08:00:00+08:00'}

    checked = trial(client, operations=[state, execution, budget], model_code='P', target_output=1000, **dates)

    assert checked.status_code == 200, checked.text[:500]
    result = checked.json()
    assert (result['suggested_input'], result['remaining_output_target']) == (834, 166), result['blockers']
    witness, = result['preview']['plan']['current_day_witnesses']
    assert witness['prior_switch_hours'] == '0'
    assert witness['prior_switches'] == []
    assert result['preview']['evaluation']['capacity_valid'] is True
    assert not any(row['code'] == 'current_day_witness' for row in result['blockers'])


def test_trial_preserves_unrelated_continuity_evidence_in_its_frozen_snapshot(shared_client):
    from tests.test_switch_continuity import arrange, confirmation as continuity_confirmation
    client, _, inputs = shared_client
    configure(inputs)
    current = arrange(client)
    dto, = current['workspace_view']['switch_continuity']
    assert post(client, 'save', current_command(client, continuity_confirmation(dto))).status_code == 200
    before = head(client)

    checked = trial(client, line_code='B', scope={'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['B'], 'model_codes': []})

    assert checked.status_code == 200, checked.text[:500]
    result = checked.json()
    assert result['preview']['snapshot']['switch_continuity'] == before['context']['switch_continuity']
    assert result['preview']['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    assert not any(row['code'] == 'remaining_switch_continuity' for row in result['blockers'])
