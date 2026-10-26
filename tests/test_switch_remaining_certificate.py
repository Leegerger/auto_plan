"""Remaining actual switch work at the public manual planning boundary."""
import pytest
from auto_plan.input_models import InputSnapshot
from tests.test_plan_transactions import shared_client, post
from tests.test_execution_reset import current_command, preview, head


def unfinished():
    return {'kind': 'confirm_line_states', 'rows': [{
        'line_code': 'L', 'state': 'unfinished_switch', 'recipe_code': None,
        'effective_at': '2026-10-12T08:00:00+08:00', 'source': '上周切机交班',
        'unfinished_switch': {'job_id': 'actual-r-s', 'started_at': '2026-10-11T20:00:00+08:00',
                              'from_recipe': 'R', 'to_recipe': 'S', 'standard_hours': '12',
                              'completed_hours': '8', 'remaining_hours': '4'}}]}


def fill(model='P', quantity=1000, day='2026-10-15', **scope_changes):
    scope = {'start_date': day, 'end_date': day, 'line_codes': ['L'], 'model_codes': []} | scope_changes
    return {'kind': 'append_fill', 'model_code': model, 'quantity': quantity, 'line_code': 'L',
            'start_date': day, 'end_date': day, 'scope': scope}


def standard_twelve(inputs):
    data = inputs.read().model_dump(mode='json')
    data['parameters']['switch_durations'][0]['hours'] = '12'
    inputs.versions[1] = InputSnapshot.model_validate(data)


def test_append_reserves_only_four_remaining_hours_and_preserves_actual_job(shared_client):
    client, _, inputs = shared_client
    standard_twelve(inputs)

    checked = preview(client, unfinished(), fill())

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert sum(row['quantity'] for row in result['plan']['segments']) == 834
    assert result['unallocated'][0]['quantity'] == 166
    record = result['context']['line_states']['records'][0]
    switch, = result['plan']['switches']
    assert (switch['id'], switch['residual_record_id'], switch['from_recipe'], switch['to_recipe']) == ('actual-r-s', record['id'], 'R', 'S')
    assert [row['hours'] for row in switch['allocations']] == ['4']
    assert result['evaluation']['capacity_valid'] is True
    assert result['evaluation']['executable'] is False
    assert any(row['code'] == 'remaining_switch_continuity' for row in result['evaluation']['adoption_conditions'])


@pytest.mark.parametrize('budget_time', ['2026-10-14T08:30:00+08:00', '2026-10-14T09:00:00+08:00'])
def test_current_day_budget_is_tied_to_the_actual_progress_without_claiming_ready_target(shared_client, budget_time):
    from datetime import datetime, timezone
    client, _, inputs = shared_client
    standard_twelve(inputs)
    client.app.state.clock = lambda: datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
    observed = unfinished()
    observed['rows'][0]['effective_at'] = '2026-10-14T08:30:00+08:00'
    assert post(client, 'save', current_command(client, observed)).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    budget = {'kind': 'confirm_day_capacity', 'observed_at': budget_time,
              'confirmations': [{'line_code': 'L', 'current_recipe': None, 'residual_record_id': record['id'], 'remaining_hours': '24'}]}

    checked = preview(client, budget, fill(day='2026-10-14'))

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert sum(row['quantity'] for row in result['plan']['segments']) == 834
    switch, = result['plan']['switches']
    assert (switch['id'], switch['residual_record_id']) == ('actual-r-s', record['id'])
    assert [row['hours'] for row in switch['allocations']] == ['4']
    witness, = result['plan']['current_day_witnesses']
    assert witness['prior_switches'] == []
    assert witness['prior_switch_hours'] == '0'
    assert result['context']['execution']['day_capacities'][0]['current_recipe'] is None
    assert not any(issue['code'] == 'current_day_witness' for issue in result['evaluation']['issues'])


def test_current_day_budget_cannot_predate_progress_or_replace_it_with_ready_recipe(shared_client):
    from datetime import datetime, timezone
    client, _, _ = shared_client
    client.app.state.clock = lambda: datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
    observed = unfinished()
    observed['rows'][0]['effective_at'] = '2026-10-14T08:30:00+08:00'
    assert post(client, 'save', current_command(client, observed)).status_code == 200
    before = head(client)
    record = before['context']['line_states']['records'][0]
    budget = {'kind': 'confirm_day_capacity', 'observed_at': '2026-10-14T08:00:00+08:00',
              'confirmations': [{'line_code': 'L', 'residual_record_id': record['id'], 'remaining_hours': '20'}]}
    rejected = preview(client, budget, fill(day='2026-10-14'))
    assert rejected.status_code == 422, rejected.text
    assert '早于' in rejected.json()['detail']
    for recipe in ('R', 'S'):
        ready = budget | {'observed_at': '2026-10-14T09:00:00+08:00',
                          'confirmations': [{'line_code': 'L', 'current_recipe': recipe, 'remaining_hours': '20'}]}
        rejected = preview(client, ready, fill(day='2026-10-14'))
        assert rejected.status_code == 422, rejected.text
        assert '未完成切机' in rejected.json()['detail']
    assert head(client) == before


def test_current_budget_can_arrange_only_the_remainder_without_production(shared_client):
    client, _, inputs = shared_client
    standard_twelve(inputs)
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    budget = {'kind': 'confirm_day_capacity', 'observed_at': '2026-10-14T08:00:00+08:00',
              'confirmations': [{'line_code': 'L', 'residual_record_id': record['id'], 'remaining_hours': '4'}]}

    checked = preview(client, budget)

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert result['plan']['segments'] == []
    switch, = result['plan']['switches']
    assert (switch['id'], switch['residual_record_id']) == ('actual-r-s', record['id'])
    assert switch['allocations'] == [{'date': '2026-10-14', 'order': 0, 'hours': '4', 'idle_hours': '0'}]


def test_origin_recipe_production_requires_finishing_actual_target_then_a_return_switch(shared_client):
    client, _, inputs = shared_client
    standard_twelve(inputs)

    checked = preview(client, unfinished(), fill(model='M'))

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert sum(row['quantity'] for row in result['plan']['segments']) == 750
    first, second = result['plan']['switches']
    assert (first['id'], first['from_recipe'], first['to_recipe']) == ('actual-r-s', 'R', 'S')
    assert [row['hours'] for row in first['allocations']] == ['4']
    assert (second['from_recipe'], second['to_recipe']) == ('S', 'R')
    assert [row['hours'] for row in second['allocations']] == ['2']
    assert first['allocations'][0]['order'] < second['allocations'][0]['order'] < result['plan']['segments'][0]['order']


def test_source_certificate_keeps_residual_only_work_and_does_not_allocate_elapsed_days(shared_client):
    client, _, inputs = shared_client
    standard_twelve(inputs)
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    source = {'inputs': inputs.read().model_dump(mode='json'), 'plan': {},
              'line_states': head(client)['context']['line_states']}

    checked = client.post('/auto_plan/api/plans/certificate/preview', json={'snapshot': source})

    assert checked.status_code == 200, checked.text
    result = checked.json()
    switch, = result['plan']['switches']
    assert switch['id'] == 'actual-r-s'
    assert switch['allocations'] == [{'date': '2026-10-15', 'order': 0, 'hours': '4', 'idle_hours': '0'}]


def test_a_completed_future_prefix_keeps_one_original_job_for_later_scoped_work(shared_client):
    client, _, inputs = shared_client
    standard_twelve(inputs)
    body = current_command(client, unfinished(), fill(quantity=100), fill(model='M', quantity=100, day='2026-10-16'))

    saved = post(client, 'save', body)

    assert saved.status_code == 200, saved.text
    assert post(client, 'save', body).json() == saved.json()
    result = head(client)
    first, second = result['plan']['switches']
    assert (first['id'], first['from_recipe'], first['to_recipe']) == ('actual-r-s', 'R', 'S')
    assert [row['hours'] for row in first['allocations']] == ['4']
    assert (second['from_recipe'], second['to_recipe']) == ('S', 'R')
    assert second.get('residual_record_id') is None
    assert result['evaluation']['capacity_valid'] is True


def test_incomplete_or_elapsed_outside_allocation_requires_expanding_scope(shared_client):
    from datetime import datetime, timezone
    client, _, inputs = shared_client
    standard_twelve(inputs)
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    residual = {'kind': 'patch_plan', 'switches': [{
        'id': 'actual-r-s', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S', 'residual_record_id': record['id'],
        'allocations': [{'date': '2026-10-15', 'order': 0, 'hours': '2'}]}]}
    assert post(client, 'save', current_command(client, residual)).status_code == 200
    before = head(client)

    checked = preview(client, fill(quantity=100, day='2026-10-16'))

    assert checked.status_code == 422, checked.text
    assert '扩大' in checked.json()['detail']
    assert head(client) == before
    client.app.state.clock = lambda: datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    checked = preview(client, fill(quantity=100, day='2026-10-17'))
    assert checked.status_code == 422, checked.text
    assert '扩大' in checked.json()['detail']


def test_standalone_current_day_remainder_cannot_omit_its_declared_witness(shared_client):
    client, _, inputs = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    budget = {'kind': 'confirm_day_capacity', 'observed_at': '2026-10-14T08:00:00+08:00',
              'confirmations': [{'line_code': 'L', 'residual_record_id': record['id'], 'remaining_hours': '4'}]}
    assert post(client, 'save', current_command(client, budget)).status_code == 200
    context = head(client)['context']
    source = {'inputs': inputs.read().model_dump(mode='json'), 'plan': {},
              'line_states': context['line_states'], 'execution': context['execution']}

    checked = client.post('/auto_plan/api/plans/certificate/preview', json={'snapshot': source})

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert result['plan']['switches'][0]['allocations'][0]['date'] == '2026-10-14'
    assert result['evaluation']['capacity_valid'] is False
    assert any(issue['code'] == 'current_day_witness' for issue in result['evaluation']['issues'])


def test_progress_correction_does_not_duplicate_stale_actual_job_outside_scope(shared_client):
    client, _, _ = shared_client
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    residual = {'kind': 'patch_plan', 'switches': [{
        'id': 'actual-r-s', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S', 'residual_record_id': record['id'],
        'allocations': [{'date': '2026-10-15', 'order': 0, 'hours': '4'}]}]}
    assert post(client, 'save', current_command(client, residual)).status_code == 200
    correction = unfinished()
    correction['rows'][0].update(expected_record_id=record['id'], source='交班来源更正')
    correction['reason'] = '复核登记来源'
    assert post(client, 'save', current_command(client, correction)).status_code == 200
    before = head(client)

    checked = preview(client, fill(quantity=100, day='2026-10-16'))

    assert checked.status_code == 422, checked.text
    assert '扩大' in checked.json()['detail']
    assert head(client) == before


def test_append_preserves_the_actual_job_and_later_switch_outside_scope(shared_client):
    client, _, inputs = shared_client
    standard_twelve(inputs)
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    residual = {'id': 'actual-r-s', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S', 'residual_record_id': record['id'],
                'allocations': [{'date': '2026-10-15', 'order': 0, 'hours': '4'}]}
    later = {'id': 'later-return', 'line_code': 'L', 'from_recipe': 'S', 'to_recipe': 'R',
             'allocations': [{'date': '2026-10-16', 'order': 0, 'hours': '2'}]}
    outside = {'id': 'outside', 'date': '2026-10-17', 'line_code': 'L', 'order': 0, 'model_code': 'M', 'quantity': 100, 'hours': '3'}
    initial = {'kind': 'replace_plan', 'plan': {'switches': [residual, later], 'segments': [outside]}}
    assert post(client, 'save', current_command(client, initial)).status_code == 200
    before = head(client)

    checked = preview(client, fill(quantity=100))

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert next(s for s in result['plan']['switches'] if s['id'] == 'later-return') == next(s for s in before['plan']['switches'] if s['id'] == 'later-return')
    assert next(s for s in result['plan']['segments'] if s['id'] == 'outside') == outside
    actual, = [s for s in result['plan']['switches'] if s.get('residual_record_id') == record['id']]
    assert actual['id'] == 'actual-r-s'
    assert [allocation['hours'] for allocation in actual['allocations']] == ['4']
    assert result['evaluation']['capacity_valid'] is True
