"""Continue qualified-output goals through the public planning API."""
from copy import deepcopy
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
    return trial(client, goal_id=goal_id, demand_week='2026-11-02', target_output=None, line_code='B',
        start_date='2026-11-03', end_date='2026-11-03',
        scope={'start_date': '2026-11-03', 'end_date': '2026-11-03', 'line_codes': ['B'], 'model_codes': ['M']}, **changes)


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
