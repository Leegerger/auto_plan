"""Audited field state and conditional adoption at the public planning boundary."""
from tests.test_plan_transactions import shared_client, post, row, patch
from tests.test_execution_reset import head, preview, current_command


def confirm(recipe='R', line='L', **extra):
    return {'kind': 'confirm_line_states', 'rows': [dict(line_code=line, effective_at='2026-10-12T08:00:00+08:00', recipe_code=recipe, source='现场点检', **extra)]}


def test_unknown_state_allows_complete_trial_but_requires_field_evidence_for_adoption(shared_client):
    client, _, _ = shared_client
    checked = preview(client, patch(row()))
    assert checked.status_code == 200, checked.text
    evaluation = checked.json()['evaluation']
    assert evaluation['capacity_valid'] is evaluation['computation_complete'] is True
    assert evaluation['executable'] is False
    assert [value['line_code'] for value in evaluation['adoption_conditions']] == ['L']
    assert any(issue['code'] == 'initial_state_unknown' and issue['severity'] == 'warning' for issue in evaluation['issues'])
    assert head(client)['revision'] == 0
    assert post(client, 'adopt', current_command(client, patch(row()))).status_code == 422
    saved = post(client, 'save', current_command(client, patch(row())))
    assert saved.status_code == 200, saved.text
    assert post(client, 'adopt', current_command(client)).status_code == 422
    resolved = preview(client, confirm())
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()['evaluation']['executable'] is True, resolved.json()['evaluation']['issues']
    assert head(client)['context']['line_states']['records'] == []
    adopted = post(client, 'adopt', current_command(client, confirm()))
    assert adopted.status_code == 200, adopted.text
    current = head(client)
    assert current['latest_adoption'] == 1
    record = current['context']['line_states']['records'][0]
    assert (record['recipe_code'], record['source'], record['actor']) == ('R', '现场点检', 'EDITOR')
    assert record['effective_at'] == '2026-10-12T08:00:00+08:00'
    assert record['recorded_at'] == '2026-10-14T00:00:00Z'
    assert len(current['context']['line_states']['audit']) == 1


def test_confirmed_recipe_controls_capacity_sequence_and_a_legacy_hint_cannot_spoof_it(shared_client):
    client, _, _ = shared_client
    checked = preview(client, confirm('S'), patch(row()))
    assert checked.status_code == 200, checked.text
    evaluation = checked.json()['evaluation']
    assert evaluation['capacity_valid'] is False
    assert any(issue['code'] == 'recipe_sequence' for issue in evaluation['issues'])
    assert evaluation['adoption_conditions'] == []
    assert post(client, 'save', current_command(client, confirm('S'))).status_code == 200
    forged = {'kind': 'patch_plan', 'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}]}
    rejected = preview(client, forged, patch(row()))
    assert rejected.status_code in (409, 422), rejected.text
    assert '现场' in rejected.json()['detail']
    replacement = {'kind': 'replace_plan', 'plan': {'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}], 'segments': [row()]}}
    assert preview(client, replacement).status_code in (409, 422)
    assert post(client, 'save', current_command(client, {'kind': 'replace_plan', 'plan': {}})).status_code == 200
    assert head(client)['context']['line_states']['records'][0]['recipe_code'] == 'S'


def test_new_saved_legacy_recipe_hint_remains_provisional_and_does_not_unlock_adoption(shared_client):
    client, _, _ = shared_client
    operation = {'kind': 'replace_plan', 'plan': {'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}], 'segments': [row()]}}
    saved = post(client, 'save', current_command(client, operation))
    assert saved.status_code == 200, saved.text
    assert post(client, 'adopt', current_command(client)).status_code == 422
    checked = preview(client)
    assert checked.json()['evaluation']['capacity_valid'] is True
    assert checked.json()['evaluation']['executable'] is False


def test_current_confirmation_can_resolve_future_work_without_fabricating_a_monday_state(shared_client):
    client, _, _ = shared_client
    day = {'kind': 'confirm_day_capacity', 'confirmations': [{'line_code': 'L', 'remaining_hours': '1', 'current_recipe': 'R'}],
           'observed_at': '2026-10-14T08:00:00+08:00'}
    checked = preview(client, day, patch(row()))
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is True
    monday = next(value for value in checked.json()['workspace_view']['line_weeks'] if value['week'] == '2026-10-12' and value['line_code'] == 'L')
    assert monday['recipe_code'] is None
    assert monday['current_confirmation']['recipe_code'] == 'R'
    assert post(client, 'adopt', current_command(client, day, patch(row()))).status_code == 200
    corrected = {'kind': 'confirm_line_states', 'reason': '复核同一时点剩余工时的 Recipe 登记错误',
                 'rows': [{'line_code': 'L', 'effective_at': '2026-10-14T08:00:00+08:00', 'recipe_code': 'S', 'source': '重新现场核实'}]}
    revised = preview(client, corrected)
    assert revised.status_code == 200, revised.text
    evaluation = revised.json()['evaluation']
    assert evaluation['executable'] is False
    assert any(issue['code'] == 'recipe_sequence' for issue in evaluation['issues'])
    monday = next(value for value in revised.json()['workspace_view']['line_weeks'] if value['week'] == '2026-10-12' and value['line_code'] == 'L')
    assert monday['recipe_code'] is None
    assert monday['current_confirmation']['recipe_code'] == 'S'
    assert monday['current_confirmation']['source'] == '重新现场核实'


def test_correction_needs_current_record_and_reason_preserves_audit_and_original_history(shared_client):
    client, _, _ = shared_client
    assert post(client, 'adopt', current_command(client, confirm(), patch(row()))).status_code == 200
    original = client.get('/auto_plan/api/plans/adoptions/1').json()
    before = head(client)
    identity = before['context']['line_states']['records'][0]['id']
    change = confirm('S', expected_record_id=identity)
    assert preview(client, change).status_code == 422
    assert preview(client, confirm('S', expected_record_id='stale') | {'reason': '现场更正'}).status_code == 422
    corrected = change | {'reason': '现场复核原工艺登记错误'}
    checked = preview(client, corrected)
    assert checked.status_code == 200, checked.text
    assert checked.json()['context_changes']
    assert checked.json()['evaluation']['capacity_valid'] is False
    assert head(client) == before
    command = current_command(client, corrected)
    saved = post(client, 'save', command)
    assert saved.status_code == 200, saved.text
    assert post(client, 'save', command).json() == saved.json()
    after = head(client)
    assert after['input_version'] == before['input_version'] + 1
    assert len(after['context']['line_states']['audit']) == 2
    audit = after['context']['line_states']['audit'][-1]
    assert (audit['reason'], audit['actor']) == ('现场复核原工艺登记错误', 'EDITOR')
    assert audit['changes'][0]['before']['recipe_code'] == 'R'
    assert audit['changes'][0]['after']['recipe_code'] == 'S'
    assert client.get('/auto_plan/api/plans/adoptions/1').json() == original
    restored = preview(client, {'kind': 'restore_history', 'source': {'kind': 'adoption', 'id': 1}})
    assert restored.status_code == 200, restored.text
    assert restored.json()['context']['line_states']['records'][0]['recipe_code'] == 'S'
    assert restored.json()['evaluation']['executable'] is False
    assert post(client, 'adopt', command).status_code == 409


def test_batch_confirmation_rejects_future_or_duplicate_points_and_records_only_changed_rows(shared_client):
    client, _, _ = shared_client
    duplicate = confirm()
    duplicate['rows'] *= 2
    assert preview(client, duplicate).status_code == 422
    future = confirm()
    future['rows'][0]['effective_at'] = '2026-10-15T08:00:00+08:00'
    assert preview(client, future).status_code == 422
    blank = confirm()
    blank['rows'][0]['source'] = ' '
    assert preview(client, blank).status_code == 422
    batch = confirm()
    batch['rows'].append(confirm('S', line='B')['rows'][0])
    saved = post(client, 'save', current_command(client, batch))
    assert saved.status_code == 200, saved.text
    before = head(client)
    assert len(before['context']['line_states']['records']) == 2
    assert len(before['context']['line_states']['audit']) == 1
    repeated = post(client, 'save', current_command(client, batch))
    assert repeated.status_code == 200, repeated.text
    assert head(client)['input_version'] == before['input_version']
    assert len(head(client)['context']['line_states']['audit']) == 1


def test_manual_fill_uses_current_field_recipe_even_when_scope_starts_at_unknown_monday(shared_client):
    client, _, _ = shared_client
    current_state = {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-14T08:00:00+08:00', 'recipe_code': 'S', 'source': '当前现场点检'}]}
    fill = {'kind': 'append_fill', 'model_code': 'M', 'quantity': 1000, 'line_code': 'L', 'start_date': '2026-10-15', 'end_date': '2026-10-15',
            'scope': {'start_date': '2026-10-12', 'end_date': '2026-10-18', 'line_codes': ['L'], 'model_codes': []}}
    checked = preview(client, current_state, fill)
    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert result['evaluation']['capacity_valid'] is True
    assert result['evaluation']['executable'] is True
    assert len(result['plan']['switches']) == 1
    assert (result['plan']['switches'][0]['from_recipe'], result['plan']['switches'][0]['to_recipe']) == ('S', 'R')
    assert result['evaluation']['switches'][0]['total_hours']['exact'] == '2'


def test_old_field_state_does_not_prove_an_elapsed_planned_switch_actually_finished(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    source = {'segments': [row('after-switch', model='P', day='2026-10-15') | {'order': 1}],
        'switches': [{'id': 'planned-past', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
                      'allocations': [{'date': '2026-10-13', 'order': 0, 'hours': '0.95'}]}]}
    saved = post(client, 'save', current_command(client, confirm(), {'kind': 'replace_plan', 'plan': source}))
    assert saved.status_code == 200, saved.text
    client.app.state.clock = lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    current = head(client)
    assert current['evaluation']['capacity_valid'] is True
    assert current['evaluation']['executable'] is False
    assert current['evaluation']['adoption_conditions'][0]['line_code'] == 'L'
    assert post(client, 'adopt', current_command(client)).status_code == 422
    new = {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-14T08:00:00+08:00', 'recipe_code': 'S', 'source': '切机结束现场点检'}]}
    assert preview(client, new).json()['evaluation']['executable'] is True


def test_actual_recipe_can_carry_an_older_field_anchor_through_rollover_and_history_restore(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    source = {'segments': [row('actual-s', model='P', day='2026-10-16'), row('next-s', model='P', day='2026-10-20')],
        'switches': [{'id': 'before-actual', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
                      'allocations': [{'date': '2026-10-15', 'order': 0, 'hours': '0.95'}]}]}
    adopted = post(client, 'adopt', current_command(client, confirm(), {'kind': 'replace_plan', 'plan': source}))
    assert adopted.status_code == 200, adopted.text
    client.app.state.clock = lambda: datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-16', 'line_code': 'L', 'model_code': 'P', 'quantity': 100}],
                 'cutoff': '2026-10-19T08:00:00+08:00'}
    checked = preview(client, execution, {'kind': 'restore_history', 'source': {'kind': 'adoption', 'id': 1}})
    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert result['plan']['initial_states'] == [{'line_code': 'L', 'recipe_code': 'S'}]
    assert result['evaluation']['executable'] is True
    assert result['context']['line_states']['records'][0]['recipe_code'] == 'R'
    assert len(result['context']['line_states']['audit']) == 1


def test_field_observation_and_execution_carry_at_same_boundary_do_not_create_duplicate_events(shared_client):
    client, _, _ = shared_client
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-12', 'line_code': 'L', 'model_code': 'M', 'quantity': 100}],
                 'cutoff': '2026-10-14T08:00:00+08:00'}
    observation = confirm()
    observation['rows'][0]['effective_at'] = '2026-10-13T08:00:00+08:00'
    checked = preview(client, execution, observation, patch(row()))
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is True
    assert not any(issue['code'] == 'duplicate_order' for issue in checked.json()['evaluation']['issues'])
    assert len(checked.json()['context']['line_states']['records']) == 1


def test_confirmed_origin_participates_in_same_base_recipe_dependency_conflicts(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, confirm('S'))).status_code == 200
    baseline = head(client)
    scope = {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L'], 'model_codes': []}
    first = current_command(client, {'kind': 'append_fill', 'model_code': 'M', 'quantity': 100, 'line_code': 'L',
        'start_date': '2026-10-15', 'end_date': '2026-10-15', 'scope': scope})
    stale = current_command(client, patch(row('other-editor', model='P', day='2026-10-16')))
    saved = post(client, 'save', first)
    assert saved.status_code == 200, saved.text
    assert head(client)['input_version'] == baseline['input_version']
    rejected = post(client, 'save', stale)
    assert rejected.status_code == 409, rejected.text
    assert ['CAP', 'L', '2026-10-16'] in rejected.json()['conflicts']


def test_intraday_state_does_not_backdate_unknown_current_day_production_without_a_remaining_witness(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 14, 4, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    observed = {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-14T12:00:00+08:00', 'recipe_code': 'R', 'source': '午间点检'}]}
    production = patch(row(day='2026-10-14'))
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-14', 'line_code': 'L', 'model_code': 'M', 'quantity': 0}], 'cutoff': '2026-10-14T12:00:00+08:00'}
    checked = preview(client, execution, observed, production)
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['capacity_valid'] is True
    assert checked.json()['evaluation']['executable'] is False
    assert checked.json()['evaluation']['adoption_conditions'][0]['line_code'] == 'L'
    remaining = {'kind': 'confirm_day_capacity', 'confirmations': [{'line_code': 'L', 'current_recipe': 'R', 'remaining_hours': '4'}],
                 'observed_at': '2026-10-14T12:00:00+08:00'}
    resolved = preview(client, execution, observed, production, remaining)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()['evaluation']['executable'] is True, resolved.json()['evaluation']['issues']


def test_removing_elapsed_unconfirmed_recipe_changes_keeps_state_review_until_fresh_evidence(shared_client):
    from datetime import datetime, timezone
    client, plans, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    source = {'segments': [row('future', model='P') | {'order': 1}], 'switches': [{'id': 'elapsed', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
              'allocations': [{'date': '2026-10-13', 'order': 0, 'hours': '0.95'}]}]}
    assert post(client, 'save', current_command(client, confirm(), {'kind': 'replace_plan', 'plan': source})).status_code == 200
    client.app.state.clock = lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    replacement = {'kind': 'replace_plan', 'plan': {'segments': [row('future-r')]}}
    checked = preview(client, replacement)
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['capacity_valid'] is True
    assert checked.json()['evaluation']['executable'] is False
    assert any(issue['code'] == 'line_state_review' for issue in checked.json()['evaluation']['adoption_conditions'])
    state=next(value for value in checked.json()['workspace_view']['line_weeks'] if value['line_code']=='L' and value['week']=='2026-10-12')
    assert state['recipe_code']=='R'
    assert state['current_confirmation']['valid'] is False
    assert state['first_production']['duration_state']=='unknown_initial'
    assert state['first_production']['arranged'] is False
    fill={'kind':'append_fill','model_code':'P','quantity':100,'line_code':'L','start_date':'2026-10-15','end_date':'2026-10-15',
          'scope':{'start_date':'2026-10-15','end_date':'2026-10-15','line_codes':['L'],'model_codes':[]}}
    queued=preview(client,{'kind':'replace_plan','plan':{}},fill)
    assert queued.status_code==200,queued.text
    assert queued.json()['evaluation']['capacity_valid'] is True
    assert queued.json()['evaluation']['executable'] is False
    assert queued.json()['plan']['switches']==[]
    assert sum(segment['quantity'] for segment in queued.json()['plan']['segments'])==100
    assert post(client, 'save', current_command(client, replacement)).status_code == 200
    other_recipe=preview(client,patch(row('future-r',model='P')))
    assert other_recipe.status_code==200,other_recipe.text
    assert other_recipe.json()['evaluation']['capacity_valid'] is True
    assert other_recipe.json()['evaluation']['executable'] is False
    assert not any(issue['code']=='recipe_sequence' for issue in other_recipe.json()['evaluation']['issues'])
    from tests.jobs_support import MemoryJobStore
    client.app.state.job_store=MemoryJobStore(plans,client.app.state.access_store)
    source=client.get('/auto_plan/api/jobs/source')
    assert source.status_code==200,source.text
    assert [review['line_code'] for review in source.json()['snapshot']['state_reviews']]==['L']
    frozen=client.post('/auto_plan/api/plans/evaluate',json={'snapshot':source.json()['snapshot']})
    assert frozen.status_code==200,frozen.text
    assert frozen.json()['capacity_valid'] is True
    assert frozen.json()['executable'] is False
    assert any(condition['code']=='line_state_review' for condition in frozen.json()['adoption_conditions'])
    assert post(client, 'adopt', current_command(client)).status_code == 422
    assert preview(client, confirm()).json()['evaluation']['executable'] is False
    fresh = confirm()
    fresh['rows'][0]['effective_at'] = '2026-10-14T08:00:00+08:00'
    assert preview(client, fresh).json()['evaluation']['executable'] is True


def test_empty_line_can_be_adopted_while_retaining_review_for_future_work_and_current_confirmation_can_resolve_it(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    source = {'switches': [{'id': 'elapsed', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
              'allocations': [{'date': '2026-10-13', 'order': 0, 'hours': '0.95'}]}]}
    assert post(client, 'save', current_command(client, confirm(), {'kind': 'replace_plan', 'plan': source})).status_code == 200
    client.app.state.clock = lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    empty = {'kind': 'replace_plan', 'plan': {}}
    adopted = post(client, 'adopt', current_command(client, empty))
    assert adopted.status_code == 200, adopted.text
    assert head(client)['evaluation']['executable'] is True
    assert any(issue['code'] == 'line_state_review' for issue in head(client)['operation_issues'])
    next_work = patch(row())
    checked = preview(client, next_work)
    assert checked.json()['evaluation']['executable'] is False
    assert post(client, 'adopt', current_command(client, next_work)).status_code == 422
    current = {'kind': 'confirm_day_capacity', 'confirmations': [{'line_code': 'L', 'current_recipe': 'R', 'remaining_hours': '1'}],
               'observed_at': '2026-10-14T08:00:00+08:00'}
    assert preview(client, current, next_work).json()['evaluation']['executable'] is True
    assert post(client, 'adopt', current_command(client, current, next_work)).status_code == 200


def test_daily_actual_does_not_override_a_later_same_day_field_observation(shared_client):
    client, _, _ = shared_client
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-13', 'line_code': 'L', 'model_code': 'M', 'quantity': 100}],
                 'cutoff': '2026-10-14T08:00:00+08:00'}
    observed = {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-13T16:00:00+08:00', 'recipe_code': 'S', 'source': '16时现场点检'}]}
    future = patch(row('future-r'))
    control = preview(client, observed, future)
    assert control.status_code == 200, control.text
    assert control.json()['evaluation']['executable'] is False
    checked = preview(client, execution, observed, future)
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is False
    assert post(client, 'adopt', current_command(client, execution, observed, future)).status_code == 422
    assert not any(value['recipe_code'] == 'R' and value['state'] == 'known' for value in checked.json()['evaluation']['initial_states'])


def test_daily_actual_does_not_prove_state_after_a_later_same_day_planned_switch(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    source = {'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}],
        'segments': [row('actual-r', day='2026-10-13'), row('future', model='P')],
        'switches': [{'id': 'later-switch', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
                      'allocations': [{'date': '2026-10-13', 'order': 1, 'hours': '0.95'}]}]}
    assert post(client, 'save', current_command(client, {'kind': 'replace_plan', 'plan': source})).status_code == 200
    client.app.state.clock = lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-13', 'line_code': 'L', 'model_code': 'M', 'quantity': 100}],
                 'cutoff': '2026-10-14T08:00:00+08:00'}
    future = patch(row('future'))
    checked = preview(client, execution, future)
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is False
    assert post(client, 'adopt', current_command(client, execution, future)).status_code == 422


def test_removing_later_switch_does_not_promote_the_same_old_daily_actual_into_fresh_evidence(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    actual = row('actual-r', day='2026-10-13')
    source = {'segments': [actual, row('future', model='P')], 'switches': [{'id': 'later-switch', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
        'allocations': [{'date': '2026-10-13', 'order': 1, 'hours': '0.95'}]}]}
    assert post(client, 'save', current_command(client, confirm(), {'kind': 'replace_plan', 'plan': source})).status_code == 200
    client.app.state.clock = lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    execution = {'kind': 'record_execution', 'rows': [{'date': '2026-10-13', 'line_code': 'L', 'model_code': 'M', 'quantity': 100}],
        'cutoff': '2026-10-14T08:00:00+08:00'}
    changed = {'kind': 'replace_plan', 'plan': {'segments': [actual, row('future')]}}
    checked = preview(client, execution, changed)
    assert checked.status_code == 200, checked.text
    assert any(issue['code'] == 'line_state_review' for issue in checked.json()['operation_issues'])
    assert checked.json()['evaluation']['executable'] is False
    assert post(client, 'adopt', current_command(client, execution, changed)).status_code == 422
    assert post(client, 'save', current_command(client, execution, changed)).status_code == 200
    assert head(client)['evaluation']['executable'] is False
    assert preview(client, confirm()).json()['evaluation']['executable'] is False
    client.app.state.clock = lambda: datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    new_actual = {'kind': 'record_execution', 'rows': [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 100}],
        'cutoff': '2026-10-16T08:00:00+08:00'}
    established = preview(client, new_actual, patch(row('genuinely-later', day='2026-10-17')))
    assert established.status_code == 200, established.text
    assert established.json()['evaluation']['executable'] is True
    assert len(established.json()['context']['line_states']['audit']) == 1


def test_current_capacity_cannot_correct_same_point_field_recipe_without_reasoned_state_correction(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    point = confirm()
    point['rows'][0]['effective_at'] = '2026-10-14T08:30:00+08:00'
    assert post(client, 'save', current_command(client, point)).status_code == 200
    conflicting = {'kind': 'confirm_day_capacity', 'confirmations': [{'line_code': 'L', 'current_recipe': 'S', 'remaining_hours': '1'}],
                   'observed_at': '2026-10-14T08:30:00+08:00'}
    for observed in ('2026-10-14T08:30:00+08:00', '2026-10-14T08:00:00+08:00'):
        operation = conflicting | {'observed_at': observed}
        checked = preview(client, operation, patch(row('future-s', model='P')))
        assert checked.status_code == 422, checked.status_code
        assert '现场' in checked.json()['detail']
        assert post(client, 'adopt', current_command(client, operation, patch(row('future-s', model='P')))).status_code == 422
    assert head(client)['context']['line_states']['records'][0]['recipe_code'] == 'R'
    assert len(head(client)['context']['line_states']['audit']) == 1
    later = conflicting | {'observed_at': '2026-10-14T09:00:00+08:00'}
    checked = preview(client, later, patch(row('future-s', model='P')))
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is True
    assert post(client, 'adopt', current_command(client, later, patch(row('future-s', model='P')))).status_code == 200
    assert len(head(client)['context']['line_states']['audit']) == 1


def test_current_capacity_recipe_cannot_be_silently_overwritten_at_the_same_point(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    day = {'kind': 'confirm_day_capacity', 'confirmations': [{'line_code': 'L', 'current_recipe': 'R', 'remaining_hours': '1'}],
           'observed_at': '2026-10-14T08:30:00+08:00'}
    assert post(client, 'save', current_command(client, day)).status_code == 200
    before = head(client)
    assert before['context']['line_states']['records'] == []
    conflict = day | {'confirmations': [{'line_code': 'L', 'current_recipe': 'S', 'remaining_hours': '1'}]}
    checked = preview(client, conflict, patch(row('future-s', model='P')))
    assert checked.status_code == 422, checked.status_code
    assert '更正' in checked.json()['detail']
    for action in ('save', 'adopt'):
        assert post(client, action, current_command(client, conflict, patch(row('future-s', model='P')))).status_code == 422
    assert head(client) == before
    later = conflict | {'observed_at': '2026-10-14T09:00:00+08:00'}
    assert post(client, 'adopt', current_command(client, later, patch(row('future-s', model='P')))).status_code == 200


def test_new_field_record_correcting_same_point_capacity_requires_reason_and_audits_prior_evidence(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    day = {'kind': 'confirm_day_capacity', 'confirmations': [{'line_code': 'L', 'current_recipe': 'R', 'remaining_hours': '1'}],
           'observed_at': '2026-10-14T08:30:00+08:00'}
    assert post(client, 'save', current_command(client, day)).status_code == 200
    before = head(client)
    corrected = confirm('S')
    corrected['rows'][0]['effective_at'] = '2026-10-14T08:30:00+08:00'
    assert preview(client, corrected).status_code == 422
    for action in ('save', 'adopt'):
        assert post(client, action, current_command(client, corrected)).status_code == 422
    assert head(client) == before
    later = confirm('S')
    later['rows'][0]['effective_at'] = '2026-10-14T09:00:00+08:00'
    checked = preview(client, later, patch(row('later-s', model='P')))
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is True
    corrected['reason'] = '复核发现同一时点的剩余工时 Recipe 登记错误'
    refreshed = day | {'confirmations': [{'line_code': 'L', 'current_recipe': 'S', 'remaining_hours': '1'}]}
    command = current_command(client, corrected, refreshed, patch(row('future-s', model='P')))
    saved = post(client, 'adopt', command)
    assert saved.status_code == 200, saved.text
    assert post(client, 'adopt', command).json() == saved.json()
    current = head(client)
    audit = current['context']['line_states']['audit']
    assert len(audit) == 1
    assert audit[0]['reason'] == corrected['reason']
    assert audit[0]['changes'][0]['before'] is None
    assert audit[0]['changes'][0]['previous_capacity']['current_recipe'] == 'R'
    assert audit[0]['changes'][0]['previous_capacity']['confirmed_at'] == day['observed_at']
    assert audit[0]['changes'][0]['after']['recipe_code'] == 'S'
    assert current['context']['execution']['day_capacities'][0]['current_recipe'] == 'S'


def test_capacity_observation_cannot_backdate_across_any_conflicting_protected_point(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    points = {'kind': 'confirm_line_states', 'rows': [
        {'line_code': 'L', 'effective_at': '2026-10-14T08:00:00+08:00', 'recipe_code': 'R', 'source': '早班点检'},
        {'line_code': 'L', 'effective_at': '2026-10-14T08:30:00+08:00', 'recipe_code': 'S', 'source': '切机后点检'}]}
    assert post(client, 'save', current_command(client, points)).status_code == 200
    before = head(client)
    day = {'kind': 'confirm_day_capacity', 'confirmations': [{'line_code': 'L', 'current_recipe': 'S', 'remaining_hours': '1'}],
           'observed_at': '2026-10-14T08:00:00+08:00'}
    checked = preview(client, day, patch(row('future-s', model='P')))
    assert checked.status_code == 422, checked.status_code
    assert post(client, 'adopt', current_command(client, day, patch(row('future-s', model='P')))).status_code == 422
    assert head(client) == before
    later = day | {'observed_at': '2026-10-14T09:00:00+08:00'}
    assert post(client, 'adopt', current_command(client, later, patch(row('future-s', model='P')))).status_code == 200
