"""Frozen field evidence through durable computation and candidate application."""
from uuid import uuid4
from datetime import datetime
import pytest
from tests.test_solver import shared_client, shared_plan_client, setup
from tests.job_http_support import calculate
from tests.test_plan_transactions import post
from tests.test_execution_reset import head, current_command
from tests.test_unfinished_switches import unfinished, residual
from tests.line_state_support import assert_unconfirmed_trial
from tests.test_switch_continuity import confirmation


def automatic(client, *, start='2026-10-15', end='2026-10-15'):
    source = client.get('/auto_plan/api/jobs/source').json()
    response = calculate(client, {
        **{key: source[key] for key in ('input_version', 'latest_adoption')},
        'base_revision': source['source_revision'],
        'request': {'scope': {'start_date': start, 'end_date': end, 'line_codes': ['L']},
                    'time_limit_seconds': 10}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['candidates'], result
    return source, result['candidates'][0]


def test_actual_job_candidate_reserves_only_remaining_hours_and_applies_as_pending(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'P', 'week': '2026-10-12', 'quantity': 1000}], capacity=1000)
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    before = head(client)
    record = before['context']['line_states']['records'][0]
    source, candidate = automatic(client)
    assert candidate['snapshot'] == source['snapshot']
    assert [(switch['id'], switch['residual_record_id'], switch['from_recipe'], switch['to_recipe'])
            for switch in candidate['plan']['switches']] == [('actual-r-s', record['id'], 'R', 'S')]
    assert sum(int(row['quantity']) for row in candidate['plan']['segments']) == 834
    assert candidate['evaluation']['days'][0]['switch_hours']['exact'] == '4'
    assert candidate['evaluation']['switch_count'] == 1
    assert candidate['evaluation']['capacity_valid'] is True
    assert candidate['evaluation']['computation_complete'] is True
    assert candidate['eligibility'] == 'pending_confirmation'
    assert {row['code'] for row in candidate['evaluation']['adoption_conditions']} == {'remaining_switch_continuity'}
    proposal = candidate['server_reference']
    checked = post(client, 'preview', proposal)
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['adoption_conditions'] == candidate['evaluation']['adoption_conditions']
    assert head(client) == before
    saved = post(client, 'save', proposal | {'operation_id': str(uuid4()), 'pending': True})
    assert saved.status_code == 200, saved.text
    assert post(client, 'adopt', current_command(client)).status_code == 422
    assert head(client)['context']['line_states'] == before['context']['line_states']
    dto = head(client)['workspace_view']['switch_continuity'][0]
    assert post(client, 'save', current_command(client, confirmation(dto))).status_code == 200
    _, rechecked = automatic(client)
    assert rechecked['evaluation']['executable'] is True
    assert rechecked['snapshot']['switch_continuity'] == head(client)['context']['switch_continuity']
    adopted = post(client, 'adopt', rechecked['server_reference'] | {'operation_id': str(uuid4())})
    assert adopted.status_code == 200, adopted.text


@pytest.mark.parametrize('newer_observation', [False, True])
def test_elapsed_actual_job_source_remains_conditional_when_candidate_moves_its_work(shared_client, newer_observation):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'P', 'week': '2026-10-12', 'quantity': 10}], capacity=1000)
    client.app.state.clock = lambda: datetime.fromisoformat('2026-10-14T10:00:00+08:00')
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    progress = unfinished()
    progress['rows'][0]['effective_at'] = '2026-10-14T08:30:00+08:00'
    assert post(client, 'save', current_command(client, progress)).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    old = {'kind': 'patch_plan', 'switches': [residual(record['id'], day='2026-10-14')]}
    assert post(client, 'save', current_command(client, old)).status_code == 200
    if newer_observation:
        newer = unfinished(remaining_hours='2', completed_hours='10')
        newer['rows'][0]['effective_at'] = '2026-10-14T09:00:00+08:00'
        assert post(client, 'save', current_command(client, newer)).status_code == 200
        record = head(client)['context']['line_states']['records'][-1]
    client.app.state.clock = lambda: datetime.fromisoformat('2026-10-15T10:00:00+08:00')
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    source, candidate = automatic(client, start='2026-10-14', end='2026-10-16')
    assert candidate['snapshot'] == source['snapshot']
    assert candidate['plan']['segments']
    switch = candidate['plan']['switches'][0]
    assert switch['id'] == 'actual-r-s' and switch['residual_record_id'] == record['id']
    assert all(row['date'] == '2026-10-16' for row in switch['allocations'])
    assert {row['code'] for row in candidate['evaluation']['adoption_conditions']} == {
        'line_state_review', 'remaining_switch_continuity'}
    checked = post(client, 'preview', candidate['server_reference'])
    assert checked.status_code == 200, checked.text
    assert {row['code'] for row in checked.json()['evaluation']['adoption_conditions']} == {
        'line_state_review', 'remaining_switch_continuity'}
    # The same normalization must not move a proposal outside the user's scope.
    unauthorized = calculate(client, {
        'base_revision': source['source_revision'], 'input_version': source['input_version'],
        'latest_adoption': source['latest_adoption'],
        'request': {'scope': {'start_date': '2026-10-16', 'end_date': '2026-10-16', 'line_codes': ['L']},
                    'time_limit_seconds': 10}})
    assert unauthorized.status_code == 200, unauthorized.text
    assert unauthorized.json()['candidates'] == []
    assert any(issue['code'] == 'scope_expansion_required' for issue in unauthorized.json()['issues'])
    assert head(client)['plan'] == source['snapshot']['plan']


def test_unknown_candidate_can_be_saved_and_current_confirmation_requires_a_fresh_source(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 10}], capacity=1000)
    source, unknown = automatic(client)
    assert_unconfirmed_trial(unknown['evaluation'])
    saved = post(client, 'save', unknown['server_reference'] | {'operation_id': str(uuid4()), 'pending': True})
    assert saved.status_code == 200, saved.text
    assert post(client, 'adopt', current_command(client)).status_code == 422
    budget = {'kind': 'confirm_day_capacity', 'observed_at': '2026-10-14T08:00:00+08:00',
              'confirmations': [{'line_code': 'L', 'current_recipe': 'R', 'remaining_hours': '1'}]}
    assert post(client, 'save', current_command(client, budget)).status_code == 200
    stale = post(client, 'preview', unknown['server_reference'])
    assert stale.status_code == 409
    fresh_source, fresh = automatic(client)
    assert fresh_source['input_version'] > source['input_version']
    assert fresh['eligibility'] == 'executable'
    assert fresh['evaluation']['executable'] is True
    assert fresh['evaluation']['adoption_conditions'] == []
    checked = post(client, 'preview', fresh['server_reference'])
    assert checked.status_code == 200, checked.text
    monday = next(row for row in checked.json()['workspace_view']['line_weeks'] if row['line_code'] == 'L')
    assert monday['state'] == 'unknown'
    assert monday['current_confirmation']['valid'] is True
    adopted = post(client, 'adopt', fresh['server_reference'] | {'operation_id': str(uuid4())})
    assert adopted.status_code == 200, adopted.text


def test_large_constructor_counts_the_confirmed_origin_switch_in_its_capacity_bound(shared_client):
    from datetime import date, timedelta
    from auto_plan.input_models import InputSnapshot
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': (date(2026, 10, 12) + timedelta(weeks=week)).isoformat(), 'quantity': 1000}
                   for week in range(6)], capacity=1000)
    data = inputs.read().model_dump(mode='json')
    for kind in ('capacities', 'line_yields', 'model_yields'):
        data['parameters'][kind] += [row | {'month': month} for row in list(data['parameters'][kind])
                                     for month in ('2026-11', '2026-12', '2027-01')]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    state = {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-14T08:00:00+08:00',
              'recipe_code': 'S', 'source': '自动排程前点检'}]}
    assert post(client, 'save', current_command(client, state)).status_code == 200
    _, candidate = automatic(client, end='2027-01-24')
    assert candidate['objective'][:30] == ['0'] * 30
    assert candidate['evaluation']['executable'] is True
    assert candidate['evaluation']['switch_count'] == 1
    assert candidate['plan']['switches'][0]['from_recipe'] == 'S'
    assert candidate['evaluation']['switches'][0]['total_hours']['exact'] == '2'
