"""Continuity evidence through the shared public planning boundary."""
from tests.test_plan_transactions import shared_client, post, row, patch
from tests.test_execution_reset import head, preview, current_command
from tests.test_unfinished_switches import unfinished, residual


def arrange(client, *, day='2026-10-15'):
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    plan = patch(row('target', model='P', day=day) | {'order': 1})
    plan['switches'] = [residual(record['id'], day=day)]
    assert post(client, 'save', current_command(client, plan)).status_code == 200
    return head(client)


def confirmation(dto, **changes):
    return {'kind': 'confirm_switch_continuity', 'job_id': dto['job_id'], 'line_code': dto['line_code'],
            'source_record_id': dto['source_record_id'], 'expected_basis_digest': dto['basis_digest'], 'source': '现场核实可连续执行'} | changes


def test_matching_continuity_previews_without_writes_and_adopts_atomically(shared_client):
    client, _, _ = shared_client
    before = arrange(client)
    dto = before['workspace_view']['switch_continuity'][0]
    assert dto['status'] == 'missing'
    assert dto['can_confirm'] is True
    assert before['evaluation']['capacity_valid'] is before['evaluation']['computation_complete'] is True
    assert post(client, 'adopt', current_command(client)).status_code == 422
    checked = preview(client, confirmation(dto))
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is True
    assert head(client) == before
    command = current_command(client, confirmation(dto))
    adopted = post(client, 'adopt', command)
    assert adopted.status_code == 200, adopted.text
    assert post(client, 'adopt', command).json() == adopted.json()
    current = head(client)
    assert current['input_version'] == before['input_version'] + 1
    assert current['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    record = current['context']['switch_continuity']['records'][0]
    assert (record['actor'], record['source'], record['basis_digest']) == ('EDITOR', '现场核实可连续执行', dto['basis_digest'])
    assert len(current['context']['switch_continuity']['audit']) == 1
    assert preview(client).json()['evaluation']['executable'] is True


def test_unrelated_input_change_and_matching_budget_refresh_need_no_new_continuity_audit(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    budget = {'kind': 'confirm_day_capacity', 'observed_at': '2026-10-14T08:00:00+08:00',
              'confirmations': [{'line_code': 'L', 'residual_record_id': record['id'], 'remaining_hours': '24'}]}
    fill = {'kind': 'append_fill', 'model_code': 'P', 'quantity': 100, 'line_code': 'L', 'start_date': '2026-10-14', 'end_date': '2026-10-14',
            'scope': {'start_date': '2026-10-14', 'end_date': '2026-10-14', 'line_codes': ['L']}}
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-14', 'line_code': 'L', 'model_code': 'P', 'quantity': 0}],
                 'cutoff': '2026-10-14T08:00:00+08:00'}
    assert post(client, 'save', current_command(client, execution, budget, fill)).status_code == 200
    dto = head(client)['workspace_view']['switch_continuity'][0]
    adopted = post(client, 'adopt', current_command(client, confirmation(dto)))
    assert adopted.status_code == 200, (adopted.json().get('detail'), adopted.json().get('evaluation', {}).get('issues'))
    before = head(client)
    assert before['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    inputs = client.get('/auto_plan/api/inputs').json()
    assert client.post('/auto_plan/api/inputs/apply', json={'base_version': inputs['input_version'], 'changes': {
        'line_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '90'}]}}).status_code == 200
    stale_budget = head(client)
    assert stale_budget['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    assert any(issue['code'] == 'day_capacity_stale' for issue in stale_budget['evaluation']['issues'])
    client.app.state.clock = lambda: datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    refreshed = budget | {'observed_at': '2026-10-14T10:00:00+08:00'}
    checked = preview(client, refreshed)
    assert checked.status_code == 200, checked.text
    assert checked.json()['workspace_view']['switch_continuity'][0]['basis_digest'] == dto['basis_digest']
    assert checked.json()['evaluation']['executable'] is True, checked.json()['evaluation']['issues']
    assert post(client, 'adopt', current_command(client, refreshed)).status_code == 200
    current = head(client)
    assert current['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    assert current['context']['switch_continuity'] == before['context']['switch_continuity']


def test_tiny_positive_interior_bucket_cannot_hide_a_known_interruption(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    switch = residual(record['id'], hours='2')
    switch['allocations'] += [{'date': '2026-10-16', 'order': 0, 'hours': '1'}, {'date': '2026-10-17', 'order': 0, 'hours': '1'}]
    plan = {'kind': 'patch_plan', 'switches': [switch]}
    checked = preview(client, plan)
    assert checked.status_code == 200, checked.text
    dto = checked.json()['workspace_view']['switch_continuity'][0]
    assert dto['can_confirm'] is False
    assert any('完整生产日' in reason for reason in dto['reasons'])
    assert preview(client, plan, confirmation(dto)).status_code == 422


def test_full_day_interior_is_confirmable_but_its_known_maintenance_cannot_be_waived(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished(remaining_hours='28', standard_hours='40', completed_hours='12'))).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    switch = residual(record['id'], hours='2')
    switch['allocations'] += [{'date': '2026-10-16', 'order': 0, 'hours': '24'}, {'date': '2026-10-17', 'order': 0, 'hours': '2'}]
    assert post(client, 'save', current_command(client, {'kind': 'patch_plan', 'switches': [switch]})).status_code == 200
    dto = head(client)['workspace_view']['switch_continuity'][0]
    assert dto['can_confirm'] is True
    assert post(client, 'adopt', current_command(client, confirmation(dto))).status_code == 200
    inputs = client.get('/auto_plan/api/inputs').json()
    assert client.post('/auto_plan/api/inputs/apply', json={'base_version': inputs['input_version'], 'changes': {
        'maintenance': [{'date': '2026-10-16', 'line_code': 'L', 'hours': 1}]}}).status_code == 200
    current = head(client)
    dto = current['workspace_view']['switch_continuity'][0]
    assert (dto['status'], dto['can_confirm']) == ('stale', False)
    assert any('保养' in reason for reason in dto['reasons'])
    assert current['evaluation']['capacity_valid'] is False
    recheck = confirmation(dto, expected_confirmation_id=dto['confirmation']['id'], reason='重新核对现场安排')
    assert preview(client, recheck).status_code == 422
    assert post(client, 'adopt', current_command(client)).status_code == 422


def test_skipped_arranged_day_is_not_waived_but_adjacent_boundary_days_can_be_confirmed(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    switch = residual(record['id'], hours='2')
    switch['allocations'].append({'date': '2026-10-17', 'order': 0, 'hours': '2'})
    plan = {'kind': 'patch_plan', 'switches': [switch]}
    dto = preview(client, plan).json()['workspace_view']['switch_continuity'][0]
    assert dto['can_confirm'] is False
    assert preview(client, plan, confirmation(dto)).status_code == 422
    switch['allocations'][1]['date'] = '2026-10-16'
    dto = preview(client, plan).json()['workspace_view']['switch_continuity'][0]
    assert dto['can_confirm'] is True
    assert post(client, 'adopt', current_command(client, plan, confirmation(dto))).status_code == 200


def test_continuity_cannot_replace_state_reconciliation_after_intervening_actual_production(shared_client):
    client, _, _ = shared_client
    before = arrange(client)
    dto = before['workspace_view']['switch_continuity'][0]
    assert post(client, 'adopt', current_command(client, confirmation(dto))).status_code == 200
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-13', 'line_code': 'L', 'model_code': 'M', 'quantity': 100}],
                 'cutoff': '2026-10-14T08:00:00+08:00'}
    changed = preview(client, execution)
    assert changed.status_code == 200, changed.text
    stale = changed.json()['workspace_view']['switch_continuity'][0]
    recheck = confirmation(stale, expected_confirmation_id=stale['confirmation']['id'], reason='只核对连续时段，尚未核实实际切机进度')
    checked = preview(client, execution, recheck)
    assert checked.status_code == 200, checked.text
    assert checked.json()['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    assert checked.json()['evaluation']['executable'] is False
    assert any(issue['code'] == 'line_state_review' for issue in checked.json()['evaluation']['adoption_conditions'])
    assert post(client, 'adopt', current_command(client, execution, recheck)).status_code == 422


def test_editing_away_elapsed_residual_keeps_state_uncertainty_separate_from_continuity(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    old = patch(row('target', model='P') | {'order': 1})
    old['switches'] = [residual(record['id'], day='2026-10-13')]
    assert post(client, 'save', current_command(client, old)).status_code == 200
    client.app.state.clock = lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    changed = {'kind': 'patch_plan', 'switches': [residual(record['id'])]}
    dto = preview(client, changed).json()['workspace_view']['switch_continuity'][0]
    checked = preview(client, changed, confirmation(dto))
    assert checked.status_code == 200, checked.text
    assert checked.json()['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    assert checked.json()['evaluation']['executable'] is False
    assert {issue['code'] for issue in checked.json()['evaluation']['adoption_conditions']} == {'line_state_review'}
    assert post(client, 'save', current_command(client, changed, confirmation(dto))).status_code == 200
    assert post(client, 'adopt', current_command(client)).status_code == 422
    assert preview(client, {'kind': 'replace_plan', 'plan': {}}, patch(row('later-target', model='P'))).json()['evaluation']['executable'] is False
