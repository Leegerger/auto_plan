"""Qualified-output target trials through the public shared planning API."""
from copy import deepcopy
from uuid import uuid4
from auto_plan.input_models import InputSnapshot
from tests.test_plan_transactions import shared_client, post
from tests.test_execution_reset import current_command, head, preview
from tests.line_state_support import confirm_states


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
    client, _, inputs = shared_client
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
