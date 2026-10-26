"""One frozen field source through manual target and real automatic adoption."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from auto_plan.input_models import InputSnapshot
from tests.job_http_support import calculate
from tests.line_state_support import confirm_states
from tests.test_execution_reset import current_command, head
from tests.test_plan_transactions import post
from tests.test_solver import shared_client, shared_plan_client, setup
from tests.test_switch_continuity import confirmation
from tests.test_switch_remaining_certificate import unfinished


@pytest.mark.parametrize('route', ['manual', 'automatic'])
@pytest.mark.parametrize('state,model,quantity,switch_hours,switch_count', [
    ('known', 'M', 1000, '0', 0),
    ('unknown', 'M', 1000, '0', 0),
    ('unfinished', 'P', 834, '4', 1),
    ('return', 'M', 750, '6', 2),
])
def test_manual_target_and_automatic_candidate_share_protected_adoption_result(
        shared_client, route, state, model, quantity, switch_hours, switch_count):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': model, 'week': '2026-10-12', 'quantity': 1000},
                   {'model_code': 'N', 'week': '2026-10-12', 'quantity': 7}], capacity=1000)
    data = inputs.read().model_dump(mode='json')
    data['parameters']['switch_durations'][0]['hours'] = '12'
    inputs.versions[1] = InputSnapshot.model_validate(data)
    outside = {'id': 'outside', 'date': '2026-10-16', 'line_code': 'B',
               'model_code': 'N', 'order': 0, 'quantity': 7, 'hours': '1'}
    state_operation = (unfinished() if state in ('unfinished', 'return') else
                       confirm_states(('L', 'R')) if state == 'known' else None)
    operations = [confirm_states(('B', 'R')),
        {'kind': 'replace_plan', 'plan': {'segments': [outside]}},
        {'kind': 'lock_cells', 'cells': [{'date': '2026-10-16', 'line_code': 'B',
                                         'model_code': 'N', 'quantity': 7}]}]
    if state_operation:
        operations.insert(0, state_operation)
    seeded = post(client, 'save', current_command(client, *operations))
    assert seeded.status_code == 200, seeded.text
    before = head(client)
    source_response = client.get('/auto_plan/api/jobs/source')
    assert source_response.status_code == 200, source_response.text
    source = source_response.json()
    basis = {'base_revision': before['revision'], 'input_version': before['input_version'],
             'latest_adoption': before['latest_adoption'], 'operations': []}
    scope = {'start_date': '2026-10-15', 'end_date': '2026-10-15',
             'line_codes': ['L'], 'model_codes': [model]}
    response = post(client, 'demand-trial', basis | {
        'action_id': str(uuid4()), 'model_code': model, 'demand_week': '2026-10-12',
        'target_output': 1000, 'mode': 'on_time', 'line_code': 'L',
        'start_date': '2026-10-15', 'end_date': '2026-10-15', 'scope': scope})
    assert response.status_code == 200, response.text
    manual = response.json()
    assert (manual['suggested_input'], manual['scheduled_on_time_output'],
            manual['remaining_output_target']) == (quantity, quantity, 1000 - quantity)
    generated = calculate(client, {
        'base_revision': source['source_revision'], 'input_version': source['input_version'],
        'latest_adoption': source['latest_adoption'],
        'request': {'scope': scope, 'time_limit_seconds': 10}})
    assert generated.status_code == 200, generated.text
    candidate, = generated.json()['candidates']
    assert candidate['snapshot'] == source['snapshot']
    for evaluated in (manual['preview'], candidate):
        result = evaluated['evaluation']
        weekly = next(row for row in result['weekly_results']
                      if row['model_code'] == model and row['week'] == '2026-10-12')
        assert (weekly['attributed'], weekly['gap']) == (quantity, 1000 - quantity)
        day = next(row for row in result['days']
                   if row['date'] == '2026-10-15' and row['line_code'] == 'L')
        assert day['switch_hours']['exact'] == switch_hours
        assert result['switch_count'] == switch_count
        assert result['capacity_valid'] is result['computation_complete'] is True
        assert next(row for row in evaluated['plan']['segments'] if row['id'] == 'outside') == outside
    assert head(client) == before
    chosen = basis | {'operations': [manual['operation']]} if route == 'manual' else candidate['server_reference']
    saved = post(client, 'save', chosen | {'operation_id': str(uuid4()), 'pending': True})
    assert saved.status_code == 200, saved.text
    after_pending = head(client)
    if state != 'known':
        assert post(client, 'adopt', current_command(client)).status_code == 422
    if state == 'unknown':
        evidence = {'kind': 'confirm_day_capacity', 'observed_at': '2026-10-14T08:00:00+08:00',
                    'confirmations': [{'line_code': 'L', 'current_recipe': 'R', 'remaining_hours': '1'}]}
        assert post(client, 'save', current_command(client, evidence)).status_code == 200
        evidence_operations = []
    elif state in ('unfinished', 'return'):
        evidence_operations = [confirmation(after_pending['workspace_view']['switch_continuity'][0])]
    else:
        evidence_operations = []
    checked = post(client, 'preview', {key: value for key, value in
        current_command(client, *evidence_operations).items() if key not in ('operation_id', 'pending')})
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is True
    adopted = post(client, 'adopt', current_command(client, *evidence_operations))
    assert adopted.status_code == 200, adopted.text
    final = head(client)
    assert final['latest_adoption'] == 1
    assert sum(row['quantity'] for row in final['plan']['segments'] if row['model_code'] == model) == quantity
    assert next(row for row in final['plan']['segments'] if row['id'] == 'outside') == outside
    assert final['context']['locks'] == before['context']['locks']
    if state in ('unfinished', 'return'):
        assert final['context']['line_states'] == before['context']['line_states']
        assert final['context']['line_states']['records'][0]['unfinished_switch']['remaining_hours'] == '4'
    if os.getenv('AUTO_PLAN_SPEC49_EVIDENCE'):
        directory = Path(os.environ['AUTO_PLAN_SPEC49_EVIDENCE']) / 'state-composition'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'{state}-{route}.json').write_text(json.dumps({
            'source': source, 'manual': manual, 'candidate': candidate, 'final': final},
            ensure_ascii=False, indent=2) + '\n')
