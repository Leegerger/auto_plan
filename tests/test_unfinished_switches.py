"""Protected actual switch progress through the shared planning API."""
from tests.test_plan_transactions import shared_client, post, row, patch
from tests.test_execution_reset import head, preview, current_command


def unfinished(**changes):
    job = {'job_id': 'actual-r-s', 'started_at': '2026-10-11T08:00:00+08:00',
           'from_recipe': 'R', 'to_recipe': 'S', 'remaining_hours': '4', 'standard_hours': '12', 'completed_hours': '8'}
    job.update(changes)
    return {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-12T08:00:00+08:00',
            'state': 'unfinished_switch', 'recipe_code': None, 'unfinished_switch': job, 'source': '现场交班'}]}


def residual(record_id, *, hours='4', day='2026-10-15', order=0, **changes):
    value = {'id': 'actual-r-s', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S', 'residual_record_id': record_id,
             'allocations': [{'date': day, 'order': order, 'hours': hours}]}
    return value | changes


def test_unfinished_actual_job_records_observed_progress_without_a_ready_target(shared_client):
    client, _, _ = shared_client
    checked = preview(client, unfinished())
    assert checked.status_code == 200, checked.text
    record = checked.json()['context']['line_states']['records'][0]
    assert record['state'] == 'unfinished_switch'
    assert record['recipe_code'] is None
    assert record['unfinished_switch'] == unfinished()['rows'][0]['unfinished_switch']
    assert head(client)['context']['line_states']['records'] == []
    saved = post(client, 'save', current_command(client, unfinished()))
    assert saved.status_code == 200, saved.text
    current = head(client)
    assert current['context']['line_states']['records'][0]['unfinished_switch']['remaining_hours'] == '4'
    assert len(current['context']['line_states']['audit']) == 1
    assert current['context']['line_states']['records'][0]['actor'] == 'EDITOR'


def test_verified_remainder_reserves_only_observed_hours_and_stays_conditional(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    plan = patch(row('target', model='P', quantity=834) | {'hours': '20'})
    plan['segments'][0]['order'] = 1
    plan['switches'] = [residual(record['id'])]
    checked = preview(client, plan)
    assert checked.status_code == 200, checked.text
    evaluation = checked.json()['evaluation']
    assert evaluation['capacity_valid'] is True, evaluation['issues']
    assert evaluation['computation_complete'] is True
    assert evaluation['executable'] is False
    assert [condition['code'] for condition in evaluation['adoption_conditions']] == ['remaining_switch_continuity']
    day = next(value for value in evaluation['days'] if value['date'] == '2026-10-15' and value['line_code'] == 'L')
    assert day['switch_hours']['exact'] == '4'
    assert day['occupied_hours']['exact'] == '24'
    switch = evaluation['switches'][0]
    assert (switch['kind'], switch['total_hours']['exact'], switch['completed_hours']['exact']) == ('residual', '4', '8')
    assert evaluation['switch_count'] == 1
    assert post(client, 'save', current_command(client, plan)).status_code == 200
    assert post(client, 'adopt', current_command(client)).status_code == 422


def test_matrix_distinguishes_actual_pending_from_future_projection_and_counts_one_carried_job(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    switch = residual(record['id'], hours='2')
    switch['allocations'].append({'date': '2026-10-19', 'order': 0, 'hours': '2'})
    plan = patch(row('target', model='P', day='2026-10-19') | {'order': 1})
    plan['switches'] = [switch]
    checked = preview(client, plan)
    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert result['evaluation']['capacity_valid'] is True, result['evaluation']['issues']
    weeks = {value['week']: value for value in result['workspace_view']['line_weeks'] if value['line_code'] == 'L'}
    first = weeks['2026-10-12']
    assert first['state'] == 'unfinished_switch'
    assert first['recipe_code'] is None
    assert first['unfinished_switch']['job_id'] == 'actual-r-s'
    assert (first['new_switch_count'], first['carried_switch_count'], first['switch_hours']['exact']) == (0, 1, '2')
    second = weeks['2026-10-19']
    assert second['state'] == 'unfinished_switch'
    assert (second['new_switch_count'], second['carried_switch_count'], second['switch_hours']['exact']) == (0, 1, '2')
    assert second['first_production']['required_hours']['exact'] == '4'
    assert second['first_production']['arranged'] is True
    third = weeks['2026-10-26']
    assert (third['recipe_code'], third['source'], third['carried_switch_count']) == ('S', 'plan_projection', 0)
    assert third['current_confirmation']['state'] == 'unfinished_switch'
    days = {(value['date'], value['line_code']): value for value in result['workspace_view']['days']}
    assert days['2026-10-15', 'L']['residual_switches'][0]['allocated_hours']['exact'] == '2'
    assert days['2026-10-15', 'L']['unfinished_switch']['remaining_hours'] == '4'
    assert result['evaluation']['switch_count'] == 1


def test_missing_or_partial_remainder_never_projects_target_ready_after_premature_production(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    for switches in ([], [residual(record['id'], hours='2')], [residual('forged-record', id='forged-job')]):
        plan = patch(row('too-soon', model='P') | {'order': 1})
        plan['switches'] = switches
        checked = preview(client, plan)
        assert checked.status_code == 200, checked.text
        assert checked.json()['evaluation']['capacity_valid'] is False
        assert any(issue['code'] == 'unfinished_switch' for issue in checked.json()['evaluation']['issues'])
        future = next(value for value in checked.json()['workspace_view']['line_weeks']
                      if value['line_code'] == 'L' and value['week'] == '2026-10-19')
        assert future['recipe_code'] is None
        assert future['state'] == 'unfinished_switch'
        assert future['unfinished_switch']['job_id'] == 'actual-r-s'


def test_actual_job_reconciliation_requires_reason_and_progress_correction_keeps_audit(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    before = head(client)
    original = before['context']['line_states']['records'][0]
    ready = {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-14T08:00:00+08:00',
             'recipe_code': 'S', 'source': '现场完成点检'}]}
    assert preview(client, ready).status_code == 422
    changed_target = unfinished(job_id='replacement-job', from_recipe='S', to_recipe='R')
    changed_target['rows'][0]['effective_at'] = '2026-10-14T08:00:00+08:00'
    assert preview(client, changed_target).status_code == 422
    assert head(client) == before
    corrected = unfinished(remaining_hours='2')
    corrected['rows'][0]['expected_record_id'] = original['id']
    corrected['reason'] = '复核剩余作业登记错误'
    assert post(client, 'save', current_command(client, corrected)).status_code == 200
    current = head(client)
    audit = current['context']['line_states']['audit'][-1]
    assert audit['changes'][0]['before']['unfinished_switch']['remaining_hours'] == '4'
    assert audit['changes'][0]['after']['unfinished_switch']['remaining_hours'] == '2'
    assert audit['reason'] == corrected['reason']
    assert client.get(f"/auto_plan/api/plans/revisions/{before['revision']}").json()['snapshot']['line_states']['records'][0]['unfinished_switch']['remaining_hours'] == '4'
    ready['reason'] = '现场确认原切机已经实际完成'
    assert post(client, 'save', current_command(client, ready)).status_code == 200
    audit = head(client)['context']['line_states']['audit'][-1]
    assert audit['changes'][0]['previous_unfinished']['unfinished_switch']['job_id'] == 'actual-r-s'
    assert preview(client, patch(row('ready', model='P'))).json()['evaluation']['executable'] is True


def test_plan_replacement_cannot_relabel_the_original_actual_job(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    forged = residual(record['id'], from_recipe='S', to_recipe='R')
    operation = {'kind': 'replace_plan', 'plan': {'switches': [forged]}}
    checked = preview(client, operation)
    assert checked.status_code == 409, checked.status_code
    assert '原切机' in checked.json()['detail']
    ordinary = residual(record['id'])
    ordinary.pop('residual_record_id')
    assert preview(client, {'kind': 'replace_plan', 'plan': {'switches': [ordinary]}}).status_code == 409
    assert post(client, 'save', current_command(client, {'kind': 'replace_plan', 'plan': {}})).status_code == 200
    current = head(client)
    assert current['context']['line_states']['records'][0]['unfinished_switch']['to_recipe'] == 'S'
    assert current['evaluation']['switch_count'] == 1
    assert preview(client, patch(row('unarranged', model='P'))).json()['evaluation']['capacity_valid'] is False


def test_elapsed_planned_remainder_does_not_become_actual_completion_after_rollover_or_restore(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    plan = patch(row('last-week-target', model='P') | {'order': 1})
    plan['switches'] = [residual(record['id'])]
    assert post(client, 'save', current_command(client, plan)).status_code == 200
    old = head(client)
    client.app.state.clock = lambda: datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    current = head(client)
    first = next(value for value in current['workspace_view']['line_weeks'] if value['line_code'] == 'L' and value['week'] == '2026-10-19')
    assert first['state'] == 'unfinished_switch'
    assert first['recipe_code'] is None
    assert first['unfinished_switch']['remaining_hours'] == '4'
    restored = preview(client, {'kind': 'restore_history', 'source': {'kind': 'revision', 'id': old['revision']}})
    assert restored.status_code == 200, restored.text
    first = next(value for value in restored.json()['workspace_view']['line_weeks'] if value['line_code'] == 'L' and value['week'] == '2026-10-19')
    assert first['state'] == 'unfinished_switch'
    assert first['recipe_code'] is None
    assert preview(client, patch(row('unproven-completion', model='P', day='2026-10-20'))).json()['evaluation']['capacity_valid'] is False


def test_residual_reference_does_not_relax_full_switch_duration_or_allow_extra_work(shared_client):
    client, _, _ = shared_client
    inputs = client.get('/auto_plan/api/inputs').json()
    assert client.post('/auto_plan/api/inputs/apply', json={'base_version': inputs['input_version'], 'changes': {
        'switch_durations': [{'from_recipe': 'R', 'to_recipe': 'S', 'hours': '12'}]}}).status_code == 200
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    ordinary = residual(record['id'], id='ordinary-full-job')
    ordinary.pop('residual_record_id')
    checked = preview(client, {'kind': 'patch_plan', 'switches': [ordinary]})
    assert checked.status_code == 200, checked.text
    assert any(issue['code'] == 'switch_total' for issue in checked.json()['evaluation']['issues'])
    assert checked.json()['evaluation']['switches'][0]['total_hours']['exact'] == '12'
    checked = preview(client, {'kind': 'patch_plan', 'switches': [residual(record['id'], hours='5')]})
    assert checked.status_code == 200, checked.text
    assert any(issue['code'] == 'remaining_switch_total' for issue in checked.json()['evaluation']['issues'])
    partial = preview(client, {'kind': 'patch_plan', 'switches': [residual(record['id'], hours='2')]})
    assert partial.json()['evaluation']['capacity_valid'] is True
    assert partial.json()['evaluation']['executable'] is False
    original_production = patch(row('origin-before-completion') | {'order': 1})
    original_production['switches'] = [residual(record['id'], hours='2')]
    assert any(issue['code'] == 'unfinished_switch' for issue in preview(client, original_production).json()['evaluation']['issues'])


def test_progress_correction_exposes_stale_allocation_without_rewriting_the_plan(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    old = head(client)['context']['line_states']['records'][0]
    assert post(client, 'save', current_command(client, {'kind': 'patch_plan', 'switches': [residual(old['id'])]})).status_code == 200
    correction = unfinished(remaining_hours='2')
    correction['rows'][0]['expected_record_id'] = old['id']
    correction['reason'] = '现场重新核对剩余作业'
    assert post(client, 'save', current_command(client, correction)).status_code == 200
    current = head(client)
    assert current['plan']['switches'][0]['residual_record_id'] == old['id']
    assert any(issue['code'] == 'residual_reference' for issue in current['evaluation']['issues'])
    latest = current['context']['line_states']['records'][0]
    assert latest['id'] != old['id']
    repaired = preview(client, {'kind': 'patch_plan', 'switches': [residual(latest['id'], hours='2')]})
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()['evaluation']['capacity_valid'] is True
    assert repaired.json()['evaluation']['switches'][0]['total_hours']['exact'] == '2'


def test_ordinary_switch_round_trip_and_legacy_ready_snapshot_keep_their_shape(shared_client):
    client, _, _ = shared_client
    ready = {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-12T08:00:00+08:00', 'recipe_code': 'R', 'source': '现场点检'}]}
    ordinary = {'id': 'ordinary', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
                'allocations': [{'date': '2026-10-15', 'order': 0, 'hours': '19/20', 'idle_hours': '0'}]}
    plan = patch(row('ordinary-target', model='P') | {'order': 1})
    plan['switches'] = [ordinary]
    assert post(client, 'save', current_command(client, ready, plan)).status_code == 200
    current = head(client)
    assert current['plan']['switches'] == [ordinary]
    history = client.get(f"/auto_plan/api/plans/revisions/{current['revision']}").json()
    assert history['snapshot']['plan']['switches'] == [ordinary]
    legacy = history['snapshot']
    for record in legacy['line_states']['records']:
        record.pop('state')
        record.pop('unfinished_switch')
    checked = client.post('/auto_plan/api/plans/evaluate', json={'snapshot': legacy})
    assert checked.status_code == 200, checked.text
    assert checked.json()['capacity_valid'] is True
    assert checked.json()['executable'] is True


def test_later_actual_target_production_resolves_current_job_but_preserves_carried_history(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    client.app.state.clock = lambda: datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'P', 'quantity': 100}],
                 'cutoff': '2026-10-16T08:00:00+08:00'}
    checked = preview(client, execution, patch(row('established-target', model='P', day='2026-10-17')))
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is True, checked.json()['evaluation']['issues']
    first = next(value for value in checked.json()['workspace_view']['line_weeks'] if value['line_code'] == 'L' and value['week'] == '2026-10-12')
    assert first['state'] == 'unfinished_switch'
    assert first['unfinished_switch']['job_id'] == 'actual-r-s'
    assert (first['new_switch_count'], first['carried_switch_count']) == (0, 1)
    assert checked.json()['evaluation']['switch_count'] == 1
    future = next(value for value in checked.json()['workspace_view']['line_weeks'] if value['line_code'] == 'L' and value['week'] == '2026-10-19')
    assert (future['recipe_code'], future['carried_switch_count']) == ('S', 0)
    assert len(checked.json()['context']['line_states']['audit']) == 1
    assert checked.json()['context']['line_states']['records'][0]['state'] == 'unfinished_switch'


def test_later_record_bound_day_budget_keeps_pending_progress_in_current_confirmation(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    progress = unfinished()
    progress['rows'][0]['effective_at'] = '2026-10-14T08:00:00+08:00'
    assert post(client, 'save', current_command(client, progress)).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    budget = {'kind': 'confirm_day_capacity', 'observed_at': '2026-10-14T09:00:00+08:00',
              'confirmations': [{'line_code': 'L', 'current_recipe': None, 'residual_record_id': record['id'], 'remaining_hours': '24'}]}
    checked = preview(client, budget)
    assert checked.status_code == 200, checked.text
    current = next(value for value in checked.json()['workspace_view']['line_weeks'] if value['line_code'] == 'L')['current_confirmation']
    assert current['state'] == 'unfinished_switch'
    assert current['recipe_code'] is None
    assert current['unfinished_switch']['job_id'] == 'actual-r-s'
    assert current['unfinished_switch']['remaining_hours'] == '4'
    assert current['observed_at'] == budget['observed_at']
    assert current['effective_at'] == progress['rows'][0]['effective_at']
    assert current['record_id'] == record['id']
    assert current['valid'] is True
