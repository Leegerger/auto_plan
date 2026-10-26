"""Frozen field evidence through durable computation and candidate application."""
from uuid import uuid4
from datetime import datetime
import pytest
from tests.test_solver import shared_client, shared_plan_client, setup
from tests.job_http_support import calculate
from tests.test_plan_transactions import post
from tests.test_execution_reset import head, current_command, preview
from tests.test_unfinished_switches import unfinished, residual
from tests.line_state_support import assert_unconfirmed_trial
from tests.test_switch_continuity import confirmation
from tests.test_jobs import wait_job


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


@pytest.mark.parametrize('model,quantity,switch_hours,switch_count', [('P',834,'4',1),('M',750,'6',2)])
def test_actual_job_candidate_reserves_only_remaining_hours_and_applies_as_pending(shared_client,model,quantity,switch_hours,switch_count):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': model, 'week': '2026-10-12', 'quantity': 1000}], capacity=1000)
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    before = head(client)
    record = before['context']['line_states']['records'][0]
    source, candidate = automatic(client)
    assert candidate['snapshot'] == source['snapshot']
    assert [(switch['id'], switch['residual_record_id'], switch['from_recipe'], switch['to_recipe'])
            for switch in candidate['plan']['switches'] if switch.get('residual_record_id')] == [('actual-r-s', record['id'], 'R', 'S')]
    assert sum(int(row['quantity']) for row in candidate['plan']['segments']) == quantity
    assert candidate['evaluation']['days'][0]['switch_hours']['exact'] == switch_hours
    assert candidate['evaluation']['switch_count'] == switch_count
    assert candidate['evaluation']['capacity_valid'] is True
    assert candidate['evaluation']['computation_complete'] is True
    assert candidate['eligibility'] == 'pending_confirmation'
    assert {row['code'] for row in candidate['evaluation']['adoption_conditions']} == {'remaining_switch_continuity'}
    proposal = candidate['server_reference']
    checked = post(client, 'preview', proposal)
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['adoption_conditions'] == candidate['evaluation']['adoption_conditions']
    assert head(client) == before
    partial = proposal | {'operations': [proposal['operations'][0] | {
        'selection': ['segments:' + candidate['plan']['segments'][0]['id']]}]}
    selected = post(client, 'preview', partial)
    assert selected.status_code == 200, selected.text
    assert selected.json()['plan']['switches'][0]['residual_record_id'] == record['id']
    assert selected.json()['evaluation']['days'][0]['switch_hours']['exact'] == switch_hours
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
    # External stored-job fixture predating derived source-review freezing.
    # Its public rerun must interpret the old evidence without rewriting it.
    from auto_plan.solver_models import FrozenSolveInput
    identity = candidate['server_reference']['operations'][0]['job_id']
    store = client.app.state.job_store
    legacy = store.jobs[identity]['frozen'].model_dump(mode='json')
    legacy['snapshot'].pop('state_reviews')
    store.jobs[identity]['frozen'] = FrozenSolveInput.model_validate(legacy)
    accepted = client.post(f'/auto_plan/api/jobs/{identity}/rerun',json={'submission_id':str(uuid4())})
    assert accepted.status_code == 200, accepted.text
    rerun = wait_job(client,accepted.json()['job_id'])
    assert rerun['state'] == 'completed', rerun
    reference = rerun['candidates'][0]
    viewed = client.get(f"/auto_plan/api/jobs/{rerun['job_id']}/candidates/{reference['candidate_id']}")
    assert viewed.status_code == 200, viewed.text
    assert {row['code'] for row in viewed.json()['candidate']['evaluation']['adoption_conditions']} == {
        'line_state_review', 'remaining_switch_continuity'}
    assert viewed.json()['candidate']['snapshot']['state_reviews'] == []
    assert viewed.json()['candidate']['snapshot']['plan'] == source['snapshot']['plan']


def test_split_elapsed_proposal_does_not_count_unconfirmed_planned_progress_as_completed(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'P', 'week': '2026-10-12', 'quantity': 10}], capacity=1000)
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    split = residual(record['id'], day='2026-10-13', hours='2')
    split['allocations'].append({'date':'2026-10-16','order':0,'hours':'2'})
    assert post(client, 'save', current_command(client, {'kind':'patch_plan','switches':[split]})).status_code == 200
    _, candidate = automatic(client, start='2026-10-13', end='2026-10-16')
    assert candidate['plan']['switches'][0]['allocations'] == [{'date':'2026-10-15','order':0,'hours':'4','idle_hours':'0'}]
    assert candidate['evaluation']['switches'][0]['total_hours']['exact'] == '4'
    assert {row['code'] for row in candidate['evaluation']['adoption_conditions']} == {'line_state_review','remaining_switch_continuity'}


def test_unknown_candidate_can_be_saved_and_current_confirmation_requires_a_fresh_source(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 10}], capacity=1000)
    source, unknown = automatic(client)
    assert_unconfirmed_trial(unknown['evaluation'])
    saved = post(client, 'save', unknown['server_reference'] | {'operation_id': str(uuid4()), 'pending': True})
    assert saved.status_code == 200, saved.text
    assert post(client, 'adopt', current_command(client)).status_code == 422
    budget = {'kind': 'confirm_day_capacity', 'observed_at': '2026-10-14T08:00:00+08:00',
              'confirmations': [{'line_code': 'L', 'current_recipe': 'S', 'remaining_hours': '1'}]}
    assert post(client, 'save', current_command(client, budget)).status_code == 200
    stale = post(client, 'preview', unknown['server_reference'])
    assert stale.status_code == 409
    fresh_source, fresh = automatic(client)
    assert fresh_source['input_version'] > source['input_version']
    assert fresh['eligibility'] == 'executable'
    assert fresh['evaluation']['executable'] is True
    assert fresh['evaluation']['adoption_conditions'] == []
    assert sum(row['quantity'] for row in fresh['plan']['segments']) == 10
    assert [(row['from_recipe'],row['to_recipe']) for row in fresh['plan']['switches']] == [('S','R')]
    assert fresh['evaluation']['switches'][0]['total_hours']['exact'] == '2'
    checked = post(client, 'preview', fresh['server_reference'])
    assert checked.status_code == 200, checked.text
    monday = next(row for row in checked.json()['workspace_view']['line_weeks'] if row['line_code'] == 'L')
    assert monday['state'] == 'unknown'
    assert monday['current_confirmation']['valid'] is True
    assert monday['first_production']['from_recipe'] == 'S'
    assert monday['first_production']['arranged'] is True
    adopted = post(client, 'adopt', fresh['server_reference'] | {'operation_id': str(uuid4())})
    assert adopted.status_code == 200, adopted.text


def test_current_capacity_recipe_is_used_by_manual_future_certificate_too(shared_client):
    client, _, _ = shared_client
    budget = {'kind':'confirm_day_capacity','observed_at':'2026-10-14T08:00:00+08:00',
              'confirmations':[{'line_code':'L','current_recipe':'S','remaining_hours':'1'}]}
    fill = {'kind':'append_fill','model_code':'M','quantity':10,'line_code':'L','start_date':'2026-10-15','end_date':'2026-10-15',
            'scope':{'start_date':'2026-10-15','end_date':'2026-10-15','line_codes':['L']}}
    checked = preview(client, budget, fill)
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is True
    assert checked.json()['evaluation']['switch_count'] == 1
    assert checked.json()['plan']['switches'][0]['from_recipe'] == 'S'
    assert checked.json()['evaluation']['switches'][0]['total_hours']['exact'] == '2'
    monday = next(row for row in checked.json()['workspace_view']['line_weeks'] if row['line_code']=='L')
    assert monday['state'] == 'unknown'
    assert monday['first_production']['from_recipe'] == 'S'
    assert monday['first_production']['arranged'] is True
    assert post(client,'save',current_command(client,budget)).status_code == 200
    source = client.get('/auto_plan/api/jobs/source').json()['snapshot']
    from tests.test_plan_transactions import row
    source_row = row(quantity=10)
    source_row.pop('hours')
    source = source | {'plan':{'segments':[source_row]}}
    certificate = client.post('/auto_plan/api/plans/certificate/preview',json={'snapshot':source})
    assert certificate.status_code == 200, certificate.text
    proposed = certificate.json()
    assert [(switch['from_recipe'],switch['to_recipe']) for switch in proposed['plan']['switches']] == [('S','R')]
    from fractions import Fraction
    assert sum(Fraction(row['hours']) for row in proposed['plan']['switches'][0]['allocations']) == 2
    assert proposed['evaluation']['executable'] is False
    assert any(issue['code']=='current_day_witness' for issue in proposed['evaluation']['issues'])


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


def test_wide_scope_order_uses_field_state_before_first_locked_production_day(shared_client):
    from auto_plan.input_models import InputSnapshot
    from tests.test_plan_transactions import row
    client, _, inputs = shared_client
    codes = ['M', 'N', 'P', 'R3', 'R4', 'R5', 'R6']
    setup(inputs, [{'model_code': code, 'week': '2026-10-12', 'quantity': 1} for code in codes], capacity=1000)
    data = inputs.read().model_dump(mode='json')
    data['masters']['models'] += [{'code':code,'name':code,'recipe_code':'R','active':True} for code in codes[3:]]
    for kind in ('line_yields', 'model_yields', 'cycle_times'):
        template = data['parameters'][kind][0]
        data['parameters'][kind] += [template | {'model_code':code} for code in codes[3:]]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    state = {'kind':'confirm_line_states','rows':[{'line_code':'L','recipe_code':'S',
             'effective_at':'2026-10-14T08:00:00+08:00','source':'开工点检'}]}
    ordered = ['M', 'N', 'R3', 'R4', 'R5', 'R6', 'P']
    rows = [row(code, model=code, quantity=1) | {'order':position,'hours':'1'} for position,code in enumerate(ordered)]
    locks = {'kind':'lock_cells','cells':[{'date':'2026-10-15','line_code':'L','model_code':code,'quantity':1} for code in codes]}
    assert post(client, 'save', current_command(client, state, {'kind':'patch_plan','segments':rows}, locks)).status_code == 200
    _, candidate = automatic(client, start='2026-10-12')
    assert candidate['objective'][:30] == ['0'] * 30
    assert candidate['evaluation']['switch_count'] == 1
    assert min(candidate['plan']['segments'], key=lambda row:row['order'])['model_code'] == 'P'
    assert {row['model_code']:row['quantity'] for row in candidate['plan']['segments']} == dict.fromkeys(codes, 1)


def test_confirmed_origin_can_repair_switch_proof_without_changing_locked_quantity(shared_client):
    from tests.test_plan_transactions import row
    client, _, inputs = shared_client
    setup(inputs, [{'model_code':'M','week':'2026-10-12','quantity':10}], capacity=1000)
    state = {'kind':'confirm_line_states','rows':[{'line_code':'L','recipe_code':'S',
             'effective_at':'2026-10-14T08:00:00+08:00','source':'开工点检'}]}
    locks = {'kind':'lock_cells','cells':[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':10}]}
    assert post(client, 'save', current_command(client, state, {'kind':'patch_plan','segments':[row(quantity=10)|{'order':1}]}, locks)).status_code == 200
    _, candidate = automatic(client)
    assert [(row['model_code'],row['quantity']) for row in candidate['plan']['segments']] == [('M',10)]
    assert candidate['evaluation']['executable'] is True
    assert candidate['evaluation']['switch_count'] == 1
    assert candidate['evaluation']['switches'][0]['total_hours']['exact'] == '2'
