"""Complete week-matrix reads through the shared public planning API."""
from tests.test_plan_transactions import shared_client, post
from tests.test_execution_reset import head, current_command, preview


def test_empty_active_lines_have_complete_calendar_days_without_changing_sparse_evaluation(shared_client):
    client, _, _ = shared_client
    before = head(client)
    matrix = before['workspace_view']
    assert len(matrix['days']) == 210
    assert len(matrix['line_weeks']) == 30
    assert len({(row['date'], row['line_code']) for row in matrix['days']}) == 210
    assert before['evaluation']['days'] == []
    first = next(row for row in matrix['days'] if row['date'] == '2026-10-12' and row['line_code'] == 'B')
    assert first['available_hours']['exact'] == first['remaining_hours']['exact'] == '24'
    assert first['occupied_hours']['exact'] == '0'
    assert first['basis'] == 'calendar'
    unknown = next(row for row in matrix['line_weeks'] if row['week'] == '2026-10-12' and row['line_code'] == 'B')
    assert (unknown['state'], unknown['recipe_code'], unknown['source']) == ('unknown', None, 'window_plan_start')
    assert unknown['first_production'] is None
    assert preview(client).json()['workspace_view'] == matrix
    assert head(client) == before


def test_matrix_calendar_reasons_locks_and_recipe_capacity_states_remain_distinct(shared_client):
    client, _, _ = shared_client
    current = client.get('/auto_plan/api/inputs').json()
    changed = client.post('/auto_plan/api/inputs/apply', json={'base_version': current['input_version'], 'changes': {
        'recipes': [{'code': 'T', 'name': '未设产能工艺', 'active': True}],
        'holidays': [{'date': '2026-10-15', 'holiday': True}],
        'opening_exceptions': [{'date': '2026-10-15', 'line_code': 'L', 'enabled': True}],
        'maintenance': [{'date': '2026-10-16', 'line_code': 'L', 'hours': 5}],
        'experiments': [{'date': '2026-10-16', 'line_code': 'L', 'hours': 3}],
        'capacities': [row | {'pieces': 0} for row in current['parameters']['capacities'] if row['recipe_code'] == 'R'],
        'capabilities': [{'recipe_code': 'S', 'line_code': 'B', 'allowed': False}],
    }})
    assert changed.status_code == 200, changed.text
    operation = {'kind': 'lock_cells', 'cells': [{'date': '2026-10-16', 'line_code': 'B', 'model_code': 'N', 'quantity': 0}]}
    checked = preview(client, operation)
    assert checked.status_code == 200, checked.text
    matrix = checked.json()['workspace_view']
    days = {(row['date'], row['line_code']): row for row in matrix['days']}
    assert days['2026-10-15', 'L']['available_hours']['exact'] == '24'
    assert days['2026-10-15', 'B']['available_hours']['exact'] == '0'
    friday = days['2026-10-16', 'L']
    assert (friday['available_hours']['exact'], friday['maintenance_hours'], friday['experiment_hours']) == ('16', 5, 3)
    assert days['2026-10-16', 'B']['locks'] == [{'model_code': 'N', 'quantity': 0}]
    capacities = {(row['month'], row['line_code'], row['recipe_code']): row['state'] for row in matrix['capacities']}
    assert capacities['2026-10', 'L', 'R'] == 'zero'
    assert capacities['2026-10', 'L', 'S'] == 'known'
    assert capacities['2026-10', 'B', 'S'] == 'forbidden'
    assert capacities['2026-10', 'L', 'T'] == 'missing'
    assert head(client)['revision'] == 0


def test_monday_state_and_first_switch_link_follow_original_plan_across_idle_weeks(shared_client):
    client, _, _ = shared_client
    source = {'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}], 'segments': [
        {'id': 'first-s', 'date': '2026-10-18', 'line_code': 'L', 'order': 1, 'model_code': 'P', 'quantity': 10, 'hours': '1'}],
        'switches': [{'id': 'r-to-s', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
                      'allocations': [{'date': '2026-10-18', 'order': 0, 'hours': '0.95'}]}]}
    checked = preview(client, {'kind': 'replace_plan', 'plan': source})
    assert checked.status_code == 200, checked.text
    result = checked.json()
    weeks = {(row['week'], row['line_code']): row for row in result['workspace_view']['line_weeks']}
    monday = weeks['2026-10-12', 'L']
    assert (monday['recipe_code'], monday['source']) == ('R', 'window_plan_start')
    link = monday['first_production']
    assert (link['from_recipe'], link['recipe_code'], link['arranged']) == ('R', 'S', True)
    assert link['required_hours']['exact'] == '19/20'
    assert link['planned_switch_ids'] == ['r-to-s']
    assert weeks['2026-10-19', 'L']['recipe_code'] == 'S'
    assert weeks['2026-10-19', 'L']['source'] == 'plan_projection'
    assert weeks['2027-01-18', 'L']['recipe_code'] == 'S'
    assert weeks['2026-10-19', 'B']['state'] == 'unknown'
    assert result['evaluation']['switch_count'] == 1
    assert len(result['evaluation']['days']) == 1
    assert head(client)['revision'] == 0


def test_unknown_same_recipe_and_missing_directed_switch_have_explicit_first_link_states(shared_client):
    client, _, _ = shared_client
    segment = {'id': 'first', 'date': '2026-10-15', 'line_code': 'L', 'order': 1, 'model_code': 'M', 'quantity': 10, 'hours': '1'}
    def first_link(plan):
        checked = preview(client, {'kind': 'replace_plan', 'plan': plan})
        assert checked.status_code == 200, checked.text
        return next(row for row in checked.json()['workspace_view']['line_weeks'] if row['week'] == '2026-10-12' and row['line_code'] == 'L')['first_production']
    unknown = first_link({'segments': [segment]})
    assert (unknown['duration_state'], unknown['arranged'], unknown['required_hours']['exact']) == ('unknown_initial', False, '0')
    initial = [{'line_code': 'L', 'recipe_code': 'R'}]
    same = first_link({'initial_states': initial, 'segments': [segment]})
    assert (same['duration_state'], same['arranged']) == ('same_recipe', True)
    missing = first_link({'initial_states': initial, 'segments': [segment | {'model_code': 'P'}]})
    assert missing['arranged'] is False
    assert 'recipe_sequence' in missing['issues']
    reverse = first_link({'initial_states': [{'line_code': 'L', 'recipe_code': 'S'}], 'segments': [segment]})
    assert (reverse['duration_state'], reverse['required_hours']['exact']) == ('known', '2')
    inputs = client.get('/auto_plan/api/inputs').json()
    applied = client.post('/auto_plan/api/inputs/apply', json={'base_version': inputs['input_version'], 'changes': {
        'recipes': [{'code': 'T', 'name': '缺切机参数工艺', 'active': True}]}})
    assert applied.status_code == 200, applied.text
    default = first_link({'initial_states': [{'line_code': 'L', 'recipe_code': 'T'}], 'segments': [segment]})
    assert (default['duration_state'], default['required_hours']['exact'], default['arranged']) == ('default_missing', '12', False)


def test_current_recipe_confirmation_is_separate_from_original_monday_and_stales_with_inputs(shared_client):
    client, _, _ = shared_client
    operation = {'kind': 'replace_plan', 'plan': {'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}]}}
    confirmation = {'kind': 'confirm_day_capacity', 'confirmations': [{'line_code': 'L', 'remaining_hours': '1', 'current_recipe': 'S'}],
                    'observed_at': '2026-10-14T08:00:00+08:00'}
    checked = preview(client, operation, confirmation)
    assert checked.status_code == 200, checked.text
    matrix = checked.json()['workspace_view']
    monday = next(row for row in matrix['line_weeks'] if row['week'] == '2026-10-12' and row['line_code'] == 'L')
    assert monday['recipe_code'] == 'R'
    assert monday['current_confirmation'] == {'observed_at': '2026-10-14T08:00:00+08:00', 'recipe_code': 'S', 'valid': True,
        'state': 'retained_recipe', 'unfinished_switch': None}
    today = next(row for row in matrix['days'] if row['date'] == '2026-10-14' and row['line_code'] == 'L')
    assert (today['calendar_hours']['exact'], today['available_hours']['exact'], today['basis']) == ('24', '1', 'confirmed_remaining_day')
    saved = post(client, 'save', current_command(client, operation, confirmation))
    assert saved.status_code == 200, saved.text
    inputs = client.get('/auto_plan/api/inputs').json()
    changed = client.post('/auto_plan/api/inputs/apply', json={'base_version': inputs['input_version'], 'changes': {
        'maintenance': [{'date': '2026-10-14', 'line_code': 'L', 'hours': 2}]}})
    assert changed.status_code == 200, changed.text
    stale = head(client)['workspace_view']
    monday = next(row for row in stale['line_weeks'] if row['week'] == '2026-10-12' and row['line_code'] == 'L')
    assert monday['current_confirmation']['valid'] is False
    today = next(row for row in stale['days'] if row['date'] == '2026-10-14' and row['line_code'] == 'L')
    assert (today['available_hours']['exact'], today['basis']) == ('22', 'calendar')


def test_cross_week_planned_switch_keeps_target_unready_at_monday_boundary(shared_client):
    client, _, _ = shared_client
    source = {'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}],
        'segments': [{'id': 'after', 'date': '2026-10-19', 'line_code': 'L', 'order': 1, 'model_code': 'P', 'quantity': 10, 'hours': '1'}],
        'switches': [{'id': 'cross-week', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S', 'allocations': [
            {'date': '2026-10-18', 'order': 0, 'hours': '1/2'}, {'date': '2026-10-19', 'order': 0, 'hours': '9/20'}]}]}
    checked = preview(client, {'kind': 'replace_plan', 'plan': source})
    assert checked.status_code == 200, checked.text
    result = checked.json()
    monday = next(row for row in result['workspace_view']['line_weeks'] if row['week'] == '2026-10-19' and row['line_code'] == 'L')
    assert (monday['state'], monday['recipe_code']) == ('planned_switch', 'R')
    assert monday['planned_switch'] == {'id': 'cross-week', 'from_recipe': 'R', 'to_recipe': 'S'}
    assert monday['first_production']['planned_switch_ids'] == ['cross-week']
    assert monday['first_production']['arranged'] is True
    assert result['evaluation']['switch_count'] == 1
    assert len(result['evaluation']['days']) == 2


def test_invalid_switch_total_does_not_present_a_ready_future_recipe(shared_client):
    client, _, _ = shared_client
    source = {'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}],
        'switches': [{'id': 'too-short', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
                      'allocations': [{'date': '2026-10-18', 'order': 0, 'hours': '0.5'}]}]}
    checked = preview(client, {'kind': 'replace_plan', 'plan': source})
    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert any(issue['code'] == 'switch_total' for issue in result['evaluation']['issues'])
    following = next(row for row in result['workspace_view']['line_weeks'] if row['week'] == '2026-10-19' and row['line_code'] == 'L')
    assert (following['state'], following['recipe_code']) == ('unknown', None)


def test_residual_day_totals_keep_exact_split_work_and_line_identity_in_invalid_drafts(shared_client):
    from copy import deepcopy
    from tests.test_unfinished_switches import unfinished, residual

    client, _, _ = shared_client
    state = unfinished()
    other = deepcopy(state['rows'][0])
    other['line_code'] = 'B'
    other['unfinished_switch']['job_id'] = 'actual-b-r-s'
    state['rows'].append(other)
    observed = preview(client, state)
    assert observed.status_code == 200, observed.text
    records = {row['line_code']: row for row in observed.json()['context']['line_states']['records']}
    left = residual(records['L']['id'])
    left['allocations'] = [
        {'date': '2026-10-15', 'order': 4, 'hours': '1/3'},
        {'date': '2026-10-15', 'order': 0, 'hours': '2/3'},
        {'date': '2026-10-16', 'order': 0, 'hours': '3'}]
    right = residual(records['B']['id'], line_code='B', id='actual-b-r-s')
    ordinary = {'id': 'ordinary-return', 'line_code': 'L', 'from_recipe': 'S', 'to_recipe': 'R',
                'allocations': [{'date': '2026-10-16', 'order': 1, 'hours': '2'}]}
    before = head(client)
    for invalid in (False, True):
        switches = deepcopy([right, ordinary, left])
        if invalid:
            switches[-1]['allocations'][0]['order'] = 0
        checked = preview(client, state, {'kind': 'patch_plan', 'switches': switches})
        assert checked.status_code == 200, checked.text
        result = checked.json()
        assert result['evaluation']['capacity_valid'] is (not invalid)
        assert ('duplicate_order' in {row['code'] for row in result['evaluation']['issues']}) is invalid
        days = {(row['date'], row['line_code']): row for row in result['workspace_view']['days']}
        expected = {('2026-10-15', 'L'): [('actual-r-s', '1')],
                    ('2026-10-16', 'L'): [('actual-r-s', '3')],
                    ('2026-10-15', 'B'): [('actual-b-r-s', '4')],
                    ('2026-10-16', 'B'): []}
        for key, entries in expected.items():
            assert [(row['id'], row['allocated_hours']['exact'])
                    for row in days[key]['residual_switches']] == entries
        assert result['plan']['switches'][0]['id'] == 'actual-b-r-s'
        assert result['context']['line_states']['records'] == observed.json()['context']['line_states']['records']
        assert head(client) == before
