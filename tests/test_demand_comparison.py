"""Independent line alternatives through the public demand-comparison API."""
from uuid import uuid4
from datetime import datetime

from auto_plan.input_models import InputSnapshot
from auto_plan.plan_models import ResetContext
from tests.test_demand_trials import configure
from tests.test_plan_transactions import shared_client, post
from tests.test_execution_reset import current_command, head
from tests.line_state_support import confirm_states


def comparison(client, *, operations=None, **changes):
    body = {key: value for key, value in current_command(client, *(operations or [])).items()
            if key not in ('operation_id', 'pending')}
    body.update(action_id=str(uuid4()), model_code='M', demand_week='2026-10-12', target_output=1000,
                mode='on_time', start_date='2026-10-15', end_date='2026-10-15',
                scope={'start_date': '2026-10-15', 'end_date': '2026-10-15',
                       'line_codes': ['L', 'B'], 'model_codes': []})
    body.update(changes)
    return post(client, 'demand-comparison', body)


def test_comparison_keeps_each_line_independent_and_only_selected_full_arrangement_is_saved(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=1000, line_yield='100', model_yield='100', capacity=600)
    states = confirm_states(('L', 'R'), ('B', 'R'))
    before = head(client)

    checked = comparison(client, operations=[states])

    assert checked.status_code == 200, checked.text
    result = checked.json()
    first, second = result['rows']
    assert [row['line_code'] for row in result['rows']] == ['L', 'B']
    assert (first['suggested_input'], first['scheduled_on_time_output'], first['remaining_output_target']) == (600, 600, 400)
    assert (second['suggested_input'], second['scheduled_on_time_output'], second['remaining_output_target']) == (1000, 1000, 0)
    assert first['request']['target_output'] == second['request']['target_output'] == 1000
    assert first['operation']['action_id'] != second['operation']['action_id']
    assert result['basis']['input_version'] == 1
    assert first['switch_totals']['after']['total_hours']['exact'] == '0'
    assert all('preview' not in row and 'basis' not in row for row in result['rows'])
    assert head(client) == before

    selected = current_command(client, states, second['operation'])
    preview = post(client, 'preview', {key: value for key, value in selected.items()
                                       if key not in ('operation_id', 'pending')})
    assert preview.status_code == 200, preview.text
    assert [(row['line_code'], row['quantity']) for row in preview.json()['plan']['segments']] == [('B', 1000)]
    assert head(client) == before
    saved = post(client, 'save', current_command(client, states, second['operation']))
    assert saved.status_code == 200, saved.text
    assert [(row['line_code'], row['quantity']) for row in head(client)['plan']['segments']] == [('B', 1000)]
    assert head(client)['latest_adoption'] == 0


def test_comparison_retains_no_room_zero_missing_and_forbidden_rows_with_distinct_reasons(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs, demand=1000, line_yield='100', model_yield='100', capacity=600)
    data['masters']['lines'] += [{'code': code, 'name': code, 'active': True} for code in ('C', 'D', 'E')]
    originals = list(data['parameters']['capacities'])
    data['parameters']['capacities'] += [row | {'line_code': code} for code in ('D', 'E') for row in originals if row['line_code'] == 'B']
    for row in data['parameters']['capacities']:
        if row['line_code'] == 'B':
            row['pieces'] = 0
    data['masters']['capabilities'].append({'recipe_code': 'R', 'line_code': 'D', 'allowed': False})
    inputs.versions[1] = InputSnapshot.model_validate(data)
    lines = ['L', 'B', 'C', 'D', 'E']
    occupied = {'kind': 'patch_plan', 'segments': [{'id': 'full', 'date': '2026-10-15', 'line_code': 'L',
                'order': 0, 'model_code': 'N', 'quantity': 600, 'hours': '24'}]}

    checked = comparison(client, operations=[confirm_states(*[(line, 'R') for line in lines]), occupied],
                         scope={'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': lines, 'model_codes': []})

    assert checked.status_code == 200, checked.text
    rows = {row['line_code']: row for row in checked.json()['rows']}
    assert set(rows) == set(lines)
    assert [(rows[line]['status'], rows[line]['suggested_input']) for line in lines] == [
        ('blocked', 0), ('blocked', 0), ('incomplete', None), ('blocked', 0), ('complete', 1000)]
    assert 'no_remaining_capacity' in {row['code'] for row in rows['L']['blockers']}
    assert 'zero_capacity' in {row['code'] for row in rows['B']['blockers']}
    assert 'missing_capacity' in {row['code'] for row in rows['C']['blockers']}
    assert 'recipe_forbidden' in {row['code'] for row in rows['D']['blockers']}
    assert rows['C']['scheduled_on_time_output'] is rows['C']['remaining_output_target'] is rows['C']['switch_totals'] is None
    assert rows['E']['request']['target_output'] == 1000


def test_comparison_reports_exact_switch_work_without_deducting_another_lines_candidate(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs, demand=1000, line_yield='100', model_yield='100', capacity=600)
    next(row for row in data['parameters']['switch_durations'] if row['from_recipe'] == 'S')['hours'] = '12'
    inputs.versions[1] = InputSnapshot.model_validate(data)

    checked = comparison(client, operations=[confirm_states(('L', 'R'), ('B', 'S'))])

    assert checked.status_code == 200, checked.text
    first, second = checked.json()['rows']
    assert (first['suggested_input'], first['remaining_output_target']) == (600, 400)
    assert (second['suggested_input'], second['remaining_output_target']) == (500, 500)
    assert second['switch_totals']['before']['total_hours']['exact'] == '0'
    assert second['switch_totals']['after']['total_hours']['exact'] == '12'
    assert second['switch_totals']['after']['extra_hours']['exact'] == '12'
    switch, = second['switch_effects']['after']
    assert (switch['from_recipe'], switch['to_recipe'], switch['total_hours']['exact']) == ('S', 'R', '12')
    selected = current_command(client, confirm_states(('L', 'R'), ('B', 'S')), second['operation'])
    checked = post(client, 'preview', {key: value for key, value in selected.items() if key not in ('operation_id', 'pending')})
    assert checked.status_code == 200, checked.text
    assert checked.json()['plan']['segments'] == second['placements']
    assert checked.json()['evaluation']['switches'] == second['switch_effects']['after']


def test_independent_rounding_candidates_share_original_source_and_preserve_outside_work(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=304, line_yield='100', model_yield='100', ct='0.5')
    existing = {'id': 'outside', 'date': '2026-10-15', 'line_code': 'B', 'order': 5,
                'model_code': 'M', 'quantity': 300, 'hours': '8'}
    later = {'id': 'later', 'date': '2026-10-20', 'line_code': 'L', 'order': 0,
             'model_code': 'N', 'quantity': 20, 'hours': '1'}
    operations = [confirm_states(('L', 'R'), ('B', 'R')), {'kind': 'patch_plan', 'segments': [existing, later]}]

    checked = comparison(client, target_output=2, operations=operations)

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert [(row['suggested_input'], row['scheduled_on_time_output']) for row in result['rows']] == [(1, 2), (1, 2)]
    for row in result['rows']:
        selected = current_command(client, *operations, row['operation'])
        preview = post(client, 'preview', {key: value for key, value in selected.items() if key not in ('operation_id', 'pending')})
        assert preview.status_code == 200, preview.text
        segments = {segment['id']: segment for segment in preview.json()['plan']['segments']}
        assert {key: segments['outside'][key] for key in ('date', 'line_code', 'model_code', 'quantity')} == {
            key: existing[key] for key in ('date', 'line_code', 'model_code', 'quantity')}
        if row['line_code'] == 'L':
            assert segments['outside'] == existing
        assert segments['later'] == later
        assert preview.json()['evaluation']['output_totals']['expected'] == 322


def test_comparison_rejects_old_input_basis_and_recalculates_all_lines_under_current_parameters(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=1000, line_yield='100', model_yield='100', capacity=600)
    first = comparison(client)
    assert first.status_code == 200, first.text
    original = first.json()
    changed = client.post('/auto_plan/api/inputs/apply', json={'base_version': 1,
        'changes': {'model_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '50'}]}})
    assert changed.status_code == 200, changed.text

    stale = comparison(client, input_version=1)

    assert stale.status_code == 409, stale.text
    refreshed = comparison(client)
    assert refreshed.status_code == 200, refreshed.text
    result = refreshed.json()
    assert result['basis']['input_version'] > original['basis']['input_version']
    assert result['basis']['digest'] != original['basis']['digest']
    assert [(row['suggested_input'], row['scheduled_on_time_output'], row['remaining_output_target'])
            for row in result['rows']] == [(599, 300, 700), (999, 500, 500)]
    assert head(client)['plan']['segments'] == []


def test_comparison_requires_editor_and_explicit_valid_line_authority(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=1000, line_yield='100', model_yield='100')
    invalid = comparison(client, scope={'start_date': '2026-10-15', 'end_date': '2026-10-15',
                                      'line_codes': ['L', 'UNKNOWN'], 'model_codes': []})
    assert invalid.status_code == 422, invalid.text
    assert '有效线体' in invalid.json()['detail']
    client.app.state.access_store.users['VIEWER'] = {'user_id': 'VIEWER', 'roles': ['viewer'], 'active': True}
    assert client.post('/auto_plan/api/auth/login', json={'username': 'VIEWER', 'password': 'company-secret'}).status_code == 200
    denied = comparison(client)
    assert denied.status_code == 403


def test_comparison_does_not_enable_explicit_late_mode(shared_client):
    client, _, inputs = shared_client
    configure(inputs, demand=1000, line_yield='100', model_yield='100')
    assert comparison(client, mode='late').status_code == 422


def test_comparison_continues_the_same_goal_with_independent_current_qualified_balances(shared_client):
    from tests.test_demand_continuation import cross_month, october_trial
    client, _, inputs = shared_client
    cross_month(inputs)
    first = october_trial(client)
    original = first['operation']['goal']
    assert post(client, 'save', current_command(client, first['operation'])).status_code == 200
    before = head(client)

    checked = comparison(client, goal_id=original['id'], demand_week='2026-11-02', target_output=200,
                         start_date='2026-11-03', end_date='2026-11-03',
                         scope={'start_date': '2026-11-03', 'end_date': '2026-11-03', 'line_codes': ['L', 'B'], 'model_codes': ['M']})

    assert checked.status_code == 200, checked.text
    for row in checked.json()['rows']:
        assert row['request']['goal_id'] == original['id']
        assert (row['suggested_input'], row['scheduled_on_time_output'], row['remaining_output_target'], row['goal_remaining_output_target']) == (249, 200, 0, 200)
        assert row['operation']['goal'] == original
    assert head(client) == before


def assert_single_trial_parity(client, result, operations):
    for row in result['rows']:
        body = {key: value for key, value in current_command(client, *operations).items()
                if key not in ('operation_id', 'pending')}
        body.update({key: value for key, value in row['request'].items() if key != 'placement_scope'})
        checked = post(client, 'demand-trial', body)
        assert checked.status_code == 200, checked.text
        ordinary = checked.json()
        assert ordinary['basis'] == result['basis']
        for key in ordinary.keys() - {'basis', 'preview', 'switch_effects'}:
            assert row[key] == ordinary[key], (row['line_code'], key)


def test_projected_probes_preserve_wip_yields_and_full_public_trial_results(shared_client):
    client, plans, inputs = shared_client
    configure(inputs, demand=100)
    plans.context = plans.context.model_copy(update={'reset': ResetContext(batch_id='WIP', production_week='2026-10-12',
        cutoff='2026-10-12T08:00:00+08:00', models=[{'model_code': 'M', 'wip': 10, 'net_balance': 0}])})
    operations = [confirm_states(('L', 'R'), ('B', 'R'))]
    before = head(client)

    checked = comparison(client, target_output=72, operations=operations)

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert [(row['suggested_input'], row['scheduled_on_time_output']) for row in result['rows']] == [(99, 72), (99, 72)]
    assert_single_trial_parity(client, result, operations)
    assert head(client) == before


def test_projected_probes_keep_all_cross_line_sources_fifo_weeks_and_confirmation_quotas(shared_client):
    from tests.test_output_preview import confirmation
    client, _, inputs = shared_client
    data = configure(inputs, demand=1000, week='2026-10-19', line_yield='100', model_yield='100', ct='0.5')
    data['business']['demands'].insert(0, {'model_code': 'M', 'week': '2026-10-12', 'quantity': 300})
    data['business']['confirmations'] = [confirmation('material', '2026-10-19', 1, '2026-10-15')]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    source = {'id': 'shared-source', 'date': '2026-10-15', 'line_code': 'B', 'order': 0,
              'model_code': 'M', 'quantity': 300, 'hours': '8'}
    operations = [confirm_states(('L', 'R'), ('B', 'R')), {'kind': 'patch_plan', 'segments': [source]}]

    checked = comparison(client, demand_week='2026-10-19', target_output=2, operations=operations)

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert [(row['suggested_input'], row['scheduled_on_time_output'], row['valid_on_time_output'])
            for row in result['rows']] == [(1, 2, 1), (1, 2, 1)]
    assert_single_trial_parity(client, result, operations)


def test_comparison_keeps_existing_actual_remainder_and_continuity_condition_in_its_row(shared_client):
    from tests.test_unfinished_switches import unfinished, residual
    client, _, inputs = shared_client
    data = configure(inputs, demand=1000, line_yield='100', model_yield='100')
    data['business']['demands'] = [{'model_code': 'P', 'week': '2026-10-12', 'quantity': 1000}]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    assert post(client, 'save', current_command(client, unfinished(), confirm_states(('B', 'R')))).status_code == 200
    record = next(row for row in head(client)['context']['line_states']['records'] if row['state'] == 'unfinished_switch')
    assert post(client, 'save', current_command(client, {'kind': 'patch_plan', 'switches': [residual(record['id'])]})).status_code == 200
    before = head(client)

    checked = comparison(client, model_code='P')

    assert checked.status_code == 200, checked.text
    result = checked.json()
    first = result['rows'][0]
    assert (first['suggested_input'], first['scheduled_on_time_output']) == (834, 834)
    assert first['switch_totals']['before']['total_hours']['exact'] == '4'
    assert first['switch_totals']['after']['total_hours']['exact'] == '4'
    switch, = first['switch_effects']['after']
    assert (switch['id'], switch['kind'], switch['completed_hours']['exact']) == ('actual-r-s', 'residual', '8')
    assert 'remaining_switch_continuity' in {issue['code'] for issue in first['blockers']}
    assert_single_trial_parity(client, result, [])
    assert post(client, 'adopt', current_command(client, first['operation'])).status_code == 422
    assert head(client) == before


def test_comparison_retains_latent_actual_review_when_queued_work_moves_an_elapsed_proposal(shared_client):
    from tests.test_unfinished_switches import unfinished, residual
    client, _, inputs = shared_client
    data = configure(inputs, demand=1000, line_yield='100', model_yield='100')
    data['business']['demands'] = [{'model_code': 'P', 'week': '2026-10-12', 'quantity': 1000}]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    client.app.state.clock = lambda: datetime.fromisoformat('2026-10-14T10:00:00+08:00')
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    progress = unfinished()
    progress['rows'][0]['effective_at'] = '2026-10-14T08:30:00+08:00'
    assert post(client, 'save', current_command(client, progress, confirm_states(('B', 'R')))).status_code == 200
    record = next(row for row in head(client)['context']['line_states']['records'] if row['state'] == 'unfinished_switch')
    old = {'kind': 'patch_plan', 'switches': [residual(record['id'], day='2026-10-14')],
           'segments': [{'id': 'future', 'date': '2026-10-16', 'line_code': 'L', 'order': 1,
                         'model_code': 'P', 'quantity': 10, 'hours': '1'}]}
    assert post(client, 'save', current_command(client, old)).status_code == 200
    client.app.state.clock = lambda: datetime.fromisoformat('2026-10-15T10:00:00+08:00')
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    before = head(client)
    assert not any(issue['code'] == 'line_state_review' for issue in before['operation_issues'])
    move = {'kind': 'patch_plan', 'switches': [residual(record['id'], day='2026-10-16')]}

    checked = comparison(client, model_code='P', target_output=100, operations=[move],
                         start_date='2026-10-16', end_date='2026-10-16',
                         scope={'start_date': '2026-10-14', 'end_date': '2026-10-16', 'line_codes': ['L', 'B'], 'model_codes': []})

    assert checked.status_code == 200, checked.text
    result = checked.json()
    first = result['rows'][0]
    assert (first['suggested_input'], first['scheduled_on_time_output']) == (100, 100)
    assert {'line_state_review', 'remaining_switch_continuity'} <= {issue['code'] for issue in first['blockers']}
    assert_single_trial_parity(client, result, [move])
    assert post(client, 'adopt', current_command(client, move, first['operation'])).status_code == 422
    assert head(client) == before
