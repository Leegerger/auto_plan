"""Continue qualified-output goals through the public planning API."""
from copy import deepcopy
import pytest
from auto_plan.input_models import InputSnapshot
from tests.test_plan_transactions import shared_client, post
from tests.test_execution_reset import current_command, head, preview
from tests.test_demand_trials import configure, trial


def cross_month(inputs):
    data = configure(inputs, demand=1000, week='2026-11-02', line_yield='100', model_yield='100', capacity=600)
    for category in ('capacities', 'line_yields', 'model_yields'):
        data['parameters'][category].extend(row | {'month': '2026-11'} for row in list(data['parameters'][category]))
    for row in data['parameters']['line_yields']:
        if row['model_code'] == 'M' and row['month'] == '2026-11':
            row['percent'] = '80'
    inputs.versions[1] = InputSnapshot.model_validate(data)


def october_trial(client):
    response = trial(client, demand_week='2026-11-02', target_output=1000, start_date='2026-10-30', end_date='2026-10-30',
        scope={'start_date': '2026-10-30', 'end_date': '2026-10-30', 'line_codes': ['L'], 'model_codes': ['M']})
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result['suggested_input'], result['scheduled_on_time_output'], result['remaining_output_target']) == (600, 600, 400)
    return result


def continuation(client, goal_id, **changes):
    fields = dict(goal_id=goal_id, demand_week='2026-11-02', target_output=None, line_code='B',
        start_date='2026-11-03', end_date='2026-11-03',
        scope={'start_date': '2026-11-03', 'end_date': '2026-11-03', 'line_codes': ['B'], 'model_codes': ['M']})
    return trial(client, **(fields | changes))


def test_continuation_recalculates_400_qualified_output_as_499_november_input_on_next_line(shared_client):
    client, _, inputs = shared_client
    cross_month(inputs)
    first = october_trial(client)
    original = deepcopy(first['operation']['goal'])
    assert post(client, 'save', current_command(client, first['operation'])).status_code == 200
    before = head(client)

    response = continuation(client, original['id'])

    assert response.status_code == 200, response.text
    result = response.json()
    assert (result['status'], result['request']['goal_id'], result['request']['target_output']) == ('complete', original['id'], 400)
    assert (result['suggested_input'], result['scheduled_on_time_output'], result['remaining_output_target'], result['goal_remaining_output_target']) == (499, 400, 0, 0)
    assert result['operation']['goal'] == original
    assert result['operation']['action_id'] != first['operation']['action_id']
    assert result['operation']['placements'] == [{'date': '2026-11-03', 'quantity': 499}]
    goal = result['preview']['lineage']['demand_goals'][original['id']]
    assert (goal['requested_output'], goal['desired_attributed'], goal['current_attributed'], goal['pending_material']) == (1000, 1000, 1000, 600)
    assert len(goal['accepted_actions']) == 2
    assert result['preview']['unallocated'] == []
    assert [(row['line_code'], row['date'], row['quantity']) for row in result['preview']['plan']['segments']] == [('L', '2026-10-30', 600), ('B', '2026-11-03', 499)]
    assert head(client) == before


def test_smaller_continuation_completes_its_200_target_but_keeps_the_other_200_in_original_goal(shared_client):
    client, _, inputs = shared_client
    cross_month(inputs)
    first = october_trial(client)
    original = first['operation']['goal']

    response = continuation(client, original['id'], operations=[first['operation']], target_output=200)

    assert response.status_code == 200, response.text
    result = response.json()
    assert (result['status'], result['request']['target_output'], result['suggested_input'], result['scheduled_on_time_output']) == ('complete', 200, 249, 200)
    assert (result['remaining_output_target'], result['goal_remaining_output_target']) == (0, 200)
    assert result['operation']['goal'] == original
    assert result['preview']['lineage']['demand_goals'][original['id']]['remaining_output_target'] == 200


@pytest.mark.parametrize('changes', [
    {'goal_id': 'missing'}, {'model_code': 'N'}, {'demand_week': '2026-11-09'}, {'target_output': 401},
])
def test_continuation_requires_matching_existing_goal_and_current_qualified_balance(shared_client, changes):
    client, _, inputs = shared_client
    cross_month(inputs)
    first = october_trial(client)
    assert post(client, 'save', current_command(client, first['operation'])).status_code == 200
    before = head(client)
    fields = {'goal_id': first['operation']['goal']['id']} | changes

    response = continuation(client, **fields)

    assert response.status_code == 422, response.text
    assert head(client) == before


def test_continuation_undo_and_current_data_refresh_keep_original_goal_and_fixed_accepted_inputs(shared_client):
    client, _, inputs = shared_client
    cross_month(inputs)
    first = october_trial(client)
    goal_id = first['operation']['goal']['id']
    second = continuation(client, goal_id, operations=[first['operation']]).json()['operation']

    undone = preview(client, second)

    assert undone.status_code == 200, undone.text
    assert [(row['line_code'], row['quantity']) for row in undone.json()['plan']['segments']] == [('B', 499)]
    assert undone.json()['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 600
    assert undone.json()['lineage']['demand_goals'][goal_id]['requested_output'] == 1000
    changed = client.post('/auto_plan/api/inputs/apply', json={'base_version': head(client)['input_version'],
        'changes': {'model_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '50'}]}})
    assert changed.status_code == 200, changed.text
    checked = preview(client, first['operation'], second)
    assert checked.status_code == 200, checked.text
    assert [(row['line_code'], row['quantity']) for row in checked.json()['plan']['segments']] == [('L', 600), ('B', 499)]
    assert checked.json()['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 300
    assert checked.json()['unallocated'] == []
    next_trial = continuation(client, goal_id, operations=[first['operation'], second],
        start_date='2026-11-04', end_date='2026-11-04',
        scope={'start_date': '2026-11-04', 'end_date': '2026-11-04', 'line_codes': ['B'], 'model_codes': ['M']})
    assert next_trial.status_code == 200, next_trial.text
    assert (next_trial.json()['request']['target_output'], next_trial.json()['suggested_input']) == (300, 374)


def test_pending_physical_goal_completion_prompts_confirmation_without_another_continuation(shared_client):
    client, _, inputs = shared_client
    cross_month(inputs)
    first = october_trial(client)
    goal_id = first['operation']['goal']['id']
    second = continuation(client, goal_id, operations=[first['operation']]).json()['operation']

    response = continuation(client, goal_id, operations=[first['operation'], second])

    assert response.status_code == 200, response.text
    result = response.json()
    assert (result['status'], result['request']['target_output'], result['suggested_input'], result['goal_remaining_output_target']) == ('blocked', 0, 0, 0)
    assert result['operation'] is None and result['retain_operation'] is None
    assert [(row['kind'], row['pending_output']) for row in result['required_confirmations']] == [('material', 600)]
    reused = continuation(client, goal_id, operations=[first['operation']], action_id=first['operation']['action_id'])
    assert reused.status_code == 422, reused.text


def test_direct_input_remainder_is_saved_continued_or_explicitly_discarded_separately_from_output_goal(shared_client):
    client, _, inputs = shared_client
    cross_month(inputs)
    first = october_trial(client)
    fill = {'kind': 'append_fill', 'model_code': 'N', 'quantity': 700, 'line_code': 'L', 'start_date': '2026-10-15', 'end_date': '2026-10-15',
        'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L'], 'model_codes': ['N']}}
    saved = post(client, 'save', current_command(client, first['operation'], fill))
    assert saved.status_code == 200, saved.text
    current = head(client)
    remainder, = current['unallocated']
    goal_id = first['operation']['goal']['id']
    assert (remainder['model_code'], remainder['quantity'], current['lineage']['demand_goals'][goal_id]['remaining_output_target']) == ('N', 100, 400)
    next_input = fill | {'continue_id': remainder['id'], 'quantity': 100, 'line_code': 'B', 'start_date': '2026-11-03', 'end_date': '2026-11-03',
        'scope': {'start_date': '2026-11-03', 'end_date': '2026-11-03', 'line_codes': ['B'], 'model_codes': ['N']}}

    continued = preview(client, next_input)

    assert continued.status_code == 200, continued.text
    assert continued.json()['unallocated'] == []
    assert next(row for row in continued.json()['plan']['segments'] if row['line_code'] == 'B')['quantity'] == 100
    assert continued.json()['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 400
    discarded = post(client, 'save', current_command(client, {'kind': 'discard_unallocated', 'result_ids': [remainder['id']], 'scope': fill['scope']}))
    assert discarded.status_code == 200, discarded.text
    reopened = head(client)
    assert reopened['unallocated'] == []
    assert reopened['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 400
    assert len(reopened['plan']['segments']) == 2


def test_continued_goal_history_uses_current_yield_without_mutating_original_actions(shared_client):
    client, _, inputs = shared_client
    cross_month(inputs)
    first = october_trial(client)
    goal_id = first['operation']['goal']['id']
    assert post(client, 'save', current_command(client, first['operation'])).status_code == 200
    second = continuation(client, goal_id).json()['operation']
    assert post(client, 'save', current_command(client, second)).status_code == 200
    source = client.get('/auto_plan/api/plans/revisions/2').json()
    original_goal = deepcopy(source['lineage']['demand_goals'][goal_id])
    assert post(client, 'save', current_command(client, {'kind': 'replace_plan', 'plan': {}})).status_code == 200
    changed = client.post('/auto_plan/api/inputs/apply', json={'base_version': head(client)['input_version'],
        'changes': {'model_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '50'}]}})
    assert changed.status_code == 200, changed.text

    restored = post(client, 'save', current_command(client, {'kind': 'restore_history', 'source': {'kind': 'revision', 'id': 2}}))

    assert restored.status_code == 200, restored.text
    current = head(client)
    goal = current['lineage']['demand_goals'][goal_id]
    assert (goal['requested_output'], goal['current_attributed'], goal['remaining_output_target']) == (1000, 700, 300)
    assert goal['accepted_actions'] == original_goal['accepted_actions']
    assert [row['quantity'] for row in current['plan']['segments']] == [600, 499]
    assert client.get('/auto_plan/api/plans/revisions/2').json() == source


def test_incomplete_current_goal_cannot_be_treated_as_zero_or_retried_with_old_balance(shared_client):
    client, _, inputs = shared_client
    cross_month(inputs)
    first = october_trial(client)
    goal_id = first['operation']['goal']['id']
    assert post(client, 'save', current_command(client, first['operation'])).status_code == 200
    data = inputs.read().model_dump(mode='json')
    data['parameters']['line_yields'] = [row for row in data['parameters']['line_yields'] if not (row['model_code'] == 'M' and row['month'] == '2026-10')]
    # External master source adapter represents a newly unavailable historic input month.
    inputs.versions[1] = InputSnapshot.model_validate(data)
    assert head(client)['lineage']['demand_goals'][goal_id]['remaining_output_target'] is None

    response = continuation(client, goal_id)

    assert response.status_code == 422, response.text
    assert '核算' in response.json()['detail']


def test_continuation_keeps_earlier_fifo_coverage_and_its_still_required_confirmation_visible(shared_client):
    client, _, inputs = shared_client
    cross_month(inputs)
    data = inputs.read().model_dump(mode='json')
    data['business']['demands'] = [
        {'model_code': 'M', 'week': '2026-10-19', 'quantity': 300},
        {'model_code': 'M', 'week': '2026-11-02', 'quantity': 500}]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    first_response = trial(client, demand_week='2026-11-02', target_output=500, start_date='2026-10-30', end_date='2026-10-30',
        scope={'start_date': '2026-10-30', 'end_date': '2026-10-30', 'line_codes': ['L'], 'model_codes': ['M']})
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    assert (first['scheduled_on_time_output'], first['remaining_output_target']) == (300, 200)

    response = continuation(client, first['operation']['goal']['id'], operations=[first['operation']])

    assert response.status_code == 200, response.text
    result = response.json()
    assert (result['suggested_input'], result['scheduled_on_time_output'], result['goal_remaining_output_target']) == (249, 200, 0)
    earlier = next(row for row in result['week_effects'] if row['week'] == '2026-10-19')
    assert (earlier['physical_late_before'], earlier['physical_late_after'], earlier['after']['pending_shipping']) == (300, 300, 300)
    assert {row['week']: (row['kind'], row['pending_output']) for row in result['required_confirmations']} == {
        '2026-10-19': ('shipping', 300), '2026-11-02': ('material', 300)}


def test_explicit_goal_only_retention_can_later_continue_on_a_user_selected_line_and_date(shared_client):
    client, _, inputs = shared_client
    cross_month(inputs)
    data = inputs.read().model_dump(mode='json')
    for row in data['parameters']['capacities']:
        if row['line_code'] == 'L' and row['month'] == '2026-10':
            row['pieces'] = 0
    inputs.versions[1] = InputSnapshot.model_validate(data)
    initial = trial(client, demand_week='2026-11-02', target_output=1000, start_date='2026-10-30', end_date='2026-10-30',
        scope={'start_date': '2026-10-30', 'end_date': '2026-10-30', 'line_codes': ['L'], 'model_codes': ['M']})
    assert initial.status_code == 200, initial.text
    retained = initial.json()['retain_operation']
    assert retained['placements'] == []
    assert post(client, 'save', current_command(client, retained)).status_code == 200

    response = continuation(client, retained['goal']['id'], start_date='2026-10-31', end_date='2026-10-31',
        scope={'start_date': '2026-10-31', 'end_date': '2026-10-31', 'line_codes': ['B'], 'model_codes': ['M']})

    assert response.status_code == 200, response.text
    result = response.json()
    assert (result['suggested_input'], result['scheduled_on_time_output'], result['remaining_output_target']) == (1000, 1000, 0)
    assert result['operation']['goal'] == retained['goal']
    goal = result['preview']['lineage']['demand_goals'][retained['goal']['id']]
    assert len(goal['accepted_actions']) == 2
    assert goal['accepted_actions'][retained['action_id']]['suggested_input'] == 0
    assert result['preview']['unallocated'] == []


def test_existing_historical_goals_reactivated_by_yield_change_can_continue_without_double_production(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=1000, week='2026-11-02', line_yield='100', model_yield='100', capacity=10000)
    first = trial(client, demand_week='2026-11-02', target_output=200).json()['operation']
    assert post(client, 'save', current_command(client, first)).status_code == 200
    second_response = trial(client, demand_week='2026-11-02', target_output=800, line_code='B', start_date='2026-10-16', end_date='2026-10-16',
        scope={'start_date': '2026-10-16', 'end_date': '2026-10-16', 'line_codes': ['B'], 'model_codes': ['M']})
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()['operation']
    assert post(client, 'save', current_command(client, second)).status_code == 200
    changed = client.post('/auto_plan/api/inputs/apply', json={'base_version': head(client)['input_version'],
        'changes': {'model_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '10'}]}})
    assert changed.status_code == 200, changed.text
    before = head(client)
    first_id, second_id = first['goal']['id'], second['goal']['id']
    assert {identity: goal['remaining_output_target'] for identity, goal in before['lineage']['demand_goals'].items()} == {first_id: 100, second_id: 900}
    fields = dict(demand_week='2026-11-02', target_output=None, line_code='L', start_date='2026-10-17', end_date='2026-10-17',
        scope={'start_date': '2026-10-17', 'end_date': '2026-10-17', 'line_codes': ['L'], 'model_codes': ['M']})
    assert trial(client, **fields).status_code == 422

    response = trial(client, goal_id=second_id, **fields)

    assert response.status_code == 200, response.text
    result = response.json()
    assert (result['request']['target_output'], result['suggested_input'], result['scheduled_on_time_output']) == (900, 8991, 900)
    assert result['operation']['goal'] == second['goal']
    goals = result['preview']['lineage']['demand_goals']
    assert {identity: goal['remaining_output_target'] for identity, goal in goals.items()} == {first_id: 0, second_id: 0}
    assert (len(goals[first_id]['accepted_actions']), len(goals[second_id]['accepted_actions'])) == (1, 2)
    selected, = result['preview']['evaluation']['weekly_results']
    assert (selected['attributed'], selected['pending_material']) == (1000, 1000)
    assert sorted(row['quantity'] for row in result['preview']['plan']['segments']) == [200, 800, 8991]
    assert post(client, 'save', current_command(client, result['operation'])).status_code == 200
    repeated = trial(client, goal_id=second_id, **fields)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()['operation'] is None
    assert repeated.json()['suggested_input'] == 0
