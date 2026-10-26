"""Continuity evidence through the shared public planning boundary."""
import pytest
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


def test_other_line_execution_does_not_stale_a_continuity_with_no_related_execution_evidence(shared_client):
    client, _, _ = shared_client
    before = arrange(client)
    dto = before['workspace_view']['switch_continuity'][0]
    assert post(client, 'adopt', current_command(client, confirmation(dto))).status_code == 200
    unrelated = {'kind': 'record_execution', 'rows': [{'date': '2026-10-14', 'line_code': 'B', 'model_code': 'M', 'quantity': 0}],
                 'cutoff': '2026-10-14T08:00:00+08:00'}
    checked = preview(client, unrelated)
    assert checked.status_code == 200, checked.text
    assert checked.json()['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    assert checked.json()['workspace_view']['switch_continuity'][0]['basis_digest'] == dto['basis_digest']


@pytest.mark.parametrize('changes,feasible', [
    ({'maintenance': [{'date': '2026-10-15', 'line_code': 'L', 'hours': 2}]}, True),
    ({'experiments': [{'date': '2026-10-15', 'line_code': 'L', 'hours': 23}]}, False),
    ({'holidays': [{'date': '2026-10-15', 'holiday': True}]}, False),
    ({'capacities': [{'month': '2026-10', 'recipe_code': 'S', 'line_code': 'L', 'pieces': 900}]}, True),
    ({'capabilities': [{'recipe_code': 'S', 'line_code': 'L', 'allowed': False}]}, False),
])
def test_related_physical_input_changes_stale_continuity_and_recheck_never_waives_conflicts(shared_client, changes, feasible):
    client, _, _ = shared_client
    before = arrange(client)
    dto = before['workspace_view']['switch_continuity'][0]
    assert post(client, 'adopt', current_command(client, confirmation(dto))).status_code == 200
    original = client.get('/auto_plan/api/plans/adoptions/1').json()
    inputs = client.get('/auto_plan/api/inputs').json()
    assert client.post('/auto_plan/api/inputs/apply', json={'base_version': inputs['input_version'], 'changes': changes}).status_code == 200
    current = head(client)
    dto = current['workspace_view']['switch_continuity'][0]
    assert dto['status'] == 'stale'
    assert '日历与可用工时' in dto['changed_groups']
    recheck = confirmation(dto, expected_confirmation_id=dto['confirmation']['id'], reason='相关现场安排改变，重新核对连续条件')
    checked = preview(client, recheck)
    assert checked.status_code == 200, checked.text
    assert checked.json()['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    assert checked.json()['evaluation']['capacity_valid'] is feasible
    assert checked.json()['evaluation']['executable'] is feasible
    assert post(client, 'save', current_command(client, recheck)).status_code == 200
    assert post(client, 'adopt', current_command(client)).status_code == (200 if feasible else 422)
    assert client.get('/auto_plan/api/plans/adoptions/1').json() == original


def test_semantic_equivalence_and_unrelated_calendar_rows_keep_the_confirmation(shared_client):
    client, _, _ = shared_client
    before = arrange(client)
    dto = before['workspace_view']['switch_continuity'][0]
    assert post(client, 'adopt', current_command(client, confirmation(dto))).status_code == 200
    current = head(client)
    operation = {'kind': 'replace_plan', 'plan': current['plan']}
    operation['plan']['switches'][0]['allocations'][0]['hours'] = '4/1'
    operation['plan']['segments'][0]['hours'] = '3.000'
    equivalent = preview(client, operation)
    assert equivalent.json()['workspace_view']['switch_continuity'][0]['basis_digest'] == dto['basis_digest']
    inputs = client.get('/auto_plan/api/inputs').json()
    assert client.post('/auto_plan/api/inputs/apply', json={'base_version': inputs['input_version'], 'changes': {
        'maintenance': [{'date': '2026-10-15', 'line_code': 'L', 'hours': 0}, {'date': '2026-10-15', 'line_code': 'B', 'hours': 20}, {'date': '2026-10-20', 'line_code': 'L', 'hours': 20}],
        'experiments': [{'date': '2026-10-15', 'line_code': 'L', 'hours': 0}],
        'cycle_times': [{'model_code': 'P', 'days': '1'}]}}).status_code == 200
    after = head(client)
    assert after['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    assert after['workspace_view']['switch_continuity'][0]['basis_digest'] == dto['basis_digest']
    same = confirmation(after['workspace_view']['switch_continuity'][0])
    assert post(client, 'save', current_command(client, same)).status_code == 200
    assert head(client)['context']['switch_continuity'] == current['context']['switch_continuity']


def test_queue_order_and_forged_basis_cannot_bless_later_arrangement_changes(shared_client):
    client, _, _ = shared_client
    before = arrange(client)
    dto = before['workspace_view']['switch_continuity'][0]
    for forged in (confirmation(dto, expected_basis_digest='0'*64), confirmation(dto, source_record_id='forged-source'), confirmation(dto, job_id='forged-job')):
        assert preview(client, forged).status_code == 422
    changed = patch(row('target', model='P') | {'order': 2})
    stale = preview(client, confirmation(dto), changed)
    assert stale.status_code == 200, stale.text
    assert stale.json()['workspace_view']['switch_continuity'][0]['status'] == 'stale'
    assert stale.json()['evaluation']['executable'] is False
    assert post(client, 'adopt', current_command(client, confirmation(dto), changed)).status_code == 422
    assert head(client)['context']['switch_continuity']['records'] == []
    assert preview(client, changed, confirmation(dto)).status_code == 422
    newer = preview(client, changed).json()['workspace_view']['switch_continuity'][0]
    assert post(client, 'adopt', current_command(client, changed, confirmation(newer))).status_code == 200


def test_reconfirmation_requires_current_identity_and_reason_and_keeps_audit(shared_client):
    client, _, _ = shared_client
    before = arrange(client)
    dto = before['workspace_view']['switch_continuity'][0]
    assert post(client, 'adopt', current_command(client, confirmation(dto))).status_code == 200
    old = head(client)['context']['switch_continuity']['records'][0]
    for changes in ({'source': '交班复核'}, {'source': '交班复核', 'expected_confirmation_id': 'stale', 'reason': '现场复核'}):
        assert preview(client, confirmation(dto, **changes)).status_code == 422
    update = confirmation(dto, source='交班复核', expected_confirmation_id=old['id'], reason='补充交班现场来源')
    assert post(client, 'adopt', current_command(client, update)).status_code == 200
    current = head(client)
    assert current['workspace_view']['switch_continuity'][0]['basis_digest'] == dto['basis_digest']
    audit = current['context']['switch_continuity']['audit']
    assert len(audit) == 2
    assert audit[-1]['changes'][0]['before']['id'] == old['id']
    assert audit[-1]['changes'][0]['after']['source'] == '交班复核'
    assert audit[-1]['reason'] == update['reason']


def test_progress_replacement_and_quantity_protection_require_recheck_without_unlocking(shared_client):
    client, _, _ = shared_client
    before = arrange(client)
    dto = before['workspace_view']['switch_continuity'][0]
    assert post(client, 'adopt', current_command(client, confirmation(dto))).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    changed = unfinished(remaining_hours='2')
    changed['rows'][0]['expected_record_id'] = record['id']
    changed['reason'] = '现场复核剩余工时'
    local = preview(client, changed)
    newer = local.json()['context']['line_states']['records'][0]
    repair = {'kind': 'patch_plan', 'switches': [residual(newer['id'], hours='2')]}
    dto = preview(client, changed, repair).json()['workspace_view']['switch_continuity'][0]
    assert dto['status'] == 'stale'
    assert dto['source_record_id'] == newer['id']
    assert '现场进度依据' in dto['changed_groups']
    update = confirmation(dto, expected_confirmation_id=dto['confirmation']['id'], reason='按新进度重新核对')
    assert post(client, 'adopt', current_command(client, changed, repair, update)).status_code == 200
    lock = {'kind': 'lock_cells', 'cells': [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'P', 'quantity': 100}]}
    dto = preview(client, lock).json()['workspace_view']['switch_continuity'][0]
    assert dto['status'] == 'stale'
    assert '数量锁' in dto['changed_groups']
    update = confirmation(dto, expected_confirmation_id=dto['confirmation']['id'], reason='保持锁定数量重新核对')
    assert post(client, 'adopt', current_command(client, lock, update)).status_code == 200
    cannot_change = patch(row('target', model='P', quantity=101) | {'order': 1})
    assert preview(client, cannot_change).status_code == 409
    assert head(client)['context']['locks'][0]['quantity'] == 100


def test_frozen_confirmation_survives_source_reads_and_cannot_hide_missing_computation(shared_client):
    from copy import deepcopy
    from tests.jobs_support import MemoryJobStore
    client, plans, _ = shared_client
    before = arrange(client)
    dto = before['workspace_view']['switch_continuity'][0]
    assert post(client, 'adopt', current_command(client, confirmation(dto))).status_code == 200
    client.app.state.job_store = MemoryJobStore(plans, client.app.state.access_store)
    source = client.get('/auto_plan/api/jobs/source')
    assert source.status_code == 200, source.text
    frozen = source.json()['snapshot']
    assert frozen['switch_continuity'] == head(client)['context']['switch_continuity']
    checked = client.post('/auto_plan/api/plans/evaluate', json={'snapshot': frozen})
    assert checked.status_code == 200 and checked.json()['executable'] is True
    incomplete = deepcopy(frozen)
    incomplete['inputs']['parameters']['cycle_times'] = [row for row in incomplete['inputs']['parameters']['cycle_times'] if row['model_code'] != 'P']
    checked = client.post('/auto_plan/api/plans/evaluate', json={'snapshot': incomplete})
    assert checked.status_code == 200, checked.text
    assert checked.json()['capacity_valid'] is True
    assert checked.json()['computation_complete'] is checked.json()['executable'] is False
    assert checked.json()['adoption_conditions'] == []
    assert any(issue['code'] == 'missing_cycle_time' for issue in checked.json()['issues'])
    legacy = deepcopy(frozen)
    legacy.pop('switch_continuity')
    checked = client.post('/auto_plan/api/plans/evaluate', json={'snapshot': legacy})
    assert checked.status_code == 200, checked.text
    assert checked.json()['capacity_valid'] is True
    assert checked.json()['computation_complete'] is True
    assert checked.json()['executable'] is False
    assert {issue['code'] for issue in checked.json()['adoption_conditions']} == {'remaining_switch_continuity'}


@pytest.mark.parametrize('newer_observation',[False,True],ids=['same-progress-reference','newer-progress-stale-reference'])
def test_intraday_remaining_work_needs_fresh_progress_after_it_elapses_even_if_moved_or_deleted(shared_client,newer_observation):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    progress = unfinished()
    progress['rows'][0]['effective_at'] = '2026-10-14T08:30:00+08:00'
    assert post(client, 'save', current_command(client, progress)).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-14', 'line_code': 'L', 'model_code': 'P', 'quantity': 0}],
                 'cutoff': '2026-10-14T09:00:00+08:00'}
    budget = {'kind': 'confirm_day_capacity', 'observed_at': '2026-10-14T09:00:00+08:00',
              'confirmations': [{'line_code': 'L', 'residual_record_id': record['id'], 'remaining_hours': '4'}]}
    assert post(client, 'save', current_command(client, execution, budget)).status_code == 200
    dto = head(client)['workspace_view']['switch_continuity'][0]
    assert post(client, 'adopt', current_command(client, confirmation(dto))).status_code == 200
    amount='2' if newer_observation else '4'
    if newer_observation:
        newer=unfinished(remaining_hours='2',completed_hours='10')
        newer['rows'][0]['effective_at']='2026-10-14T09:00:00+08:00'
        assert post(client,'save',current_command(client,newer)).status_code==200
        current=head(client)
        active_id=current['workspace_view']['switch_continuity'][0]['source_record_id']
        record=next(row for row in current['context']['line_states']['records'] if row['id']==active_id)
    client.app.state.clock = lambda: datetime(2026, 10, 15, 2, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    current = head(client)
    assert next(row for row in current['workspace_view']['line_weeks'] if row['line_code'] == 'L')['current_confirmation']['valid'] is False
    moved = patch(row('future-target', model='P', day='2026-10-16') | {'order': 1})
    moved['switches'] = [residual(record['id'], hours=amount,day='2026-10-16')]
    dto = preview(client, moved).json()['workspace_view']['switch_continuity'][0]
    recheck = confirmation(dto, expected_confirmation_id=dto['confirmation']['id'], reason='仅核对周五连续时段，实际进度未核实')
    checked = preview(client, moved, recheck)
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is False
    assert {issue['code'] for issue in checked.json()['evaluation']['adoption_conditions']} == {'line_state_review'}
    assert post(client, 'adopt', current_command(client, moved, recheck)).status_code == 422
    assert post(client, 'adopt', current_command(client, {'kind': 'patch_plan', 'remove_switches': ['actual-r-s']})).status_code == 200
    dto = preview(client, moved).json()['workspace_view']['switch_continuity'][0]
    recheck = confirmation(dto, expected_confirmation_id=dto['confirmation']['id'], reason='删除过期安排后仅核对连续时段')
    assert preview(client, moved, recheck).json()['evaluation']['executable'] is False
    fresh = unfinished(remaining_hours=amount,completed_hours='10' if newer_observation else '8')
    fresh['rows'][0]['effective_at'] = '2026-10-15T10:00:00+08:00'
    fresh_record = preview(client, fresh).json()['workspace_view']['switch_continuity'][0]['source_record_id']
    moved['switches'][0]['residual_record_id'] = fresh_record
    dto = preview(client, fresh, moved).json()['workspace_view']['switch_continuity'][0]
    recheck = confirmation(dto, expected_confirmation_id=dto['confirmation']['id'], reason='按新实际进度重新核对连续条件')
    assert post(client, 'adopt', current_command(client, fresh, moved, recheck)).status_code == 200


def test_earlier_distinct_job_and_ready_point_do_not_make_later_pending_progress_uncertain(shared_client):
    from datetime import datetime, timezone
    client, _, _=shared_client
    client.app.state.clock=lambda:datetime(2026,10,14,2,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    earlier=unfinished(job_id='earlier-job',remaining_hours='1/4',completed_hours='47/4')
    earlier['rows'][0]['effective_at']='2026-10-14T08:00:00+08:00'
    assert post(client,'save',current_command(client,earlier)).status_code==200
    old=head(client)['context']['line_states']['records'][0]
    old_plan={'kind':'patch_plan','switches':[residual(old['id'],id='earlier-job',hours='1/4',day='2026-10-14')]}
    assert post(client,'save',current_command(client,old_plan)).status_code==200
    ready={'kind':'confirm_line_states','reason':'现场确认前一作业已经完成','rows':[
        {'line_code':'L','effective_at':'2026-10-14T08:30:00+08:00','recipe_code':'S','source':'完成点检'}]}
    later=unfinished(job_id='later-job',started_at='2026-10-14T08:45:00+08:00',from_recipe='S',to_recipe='R',
                     remaining_hours='2',standard_hours='9/4',completed_hours='1/4')
    later['rows'][0]['effective_at']='2026-10-14T09:00:00+08:00'
    assert post(client,'save',current_command(client,ready,later)).status_code==200
    client.app.state.clock=lambda:datetime(2026,10,15,2,tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code==200
    current=head(client)
    assert next(row for row in current['workspace_view']['line_weeks'] if row['line_code']=='L')['current_confirmation']['valid'] is True
    active_id=current['workspace_view']['switch_continuity'][0]['source_record_id']
    new_plan=patch(row('later-target',model='M',day='2026-10-16')|{'order':1})
    new_plan['remove_switches']=['earlier-job']
    new_plan['switches']=[residual(active_id,id='later-job',from_recipe='S',to_recipe='R',hours='2',day='2026-10-16')]
    dto=preview(client,new_plan).json()['workspace_view']['switch_continuity'][0]
    checked=preview(client,new_plan,confirmation(dto))
    assert checked.status_code==200,checked.text
    assert checked.json()['evaluation']['adoption_conditions']==[]
    assert post(client,'adopt',current_command(client,new_plan,confirmation(dto))).status_code==200
