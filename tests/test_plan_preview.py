from copy import deepcopy
import pytest
from tests.test_inputs import inputs_client


def snapshot():
    return {'inputs': {'masters': {
        'recipes': [{'code': 'R', 'name': '工艺一', 'active': True}, {'code': 'S', 'name': '工艺二', 'active': True}],
        'lines': [{'code': 'L', 'name': '一线', 'active': True}],
        'models': [{'code': 'M', 'name': '机种一', 'active': True, 'recipe_code': 'R'},
                   {'code': 'N', 'name': '机种二', 'active': True, 'recipe_code': 'R'},
                   {'code': 'P', 'name': '机种三', 'active': True, 'recipe_code': 'S'}]},
        'parameters': {'capacities': [{'month': '2026-10', 'recipe_code': r, 'line_code': 'L', 'pieces': 1000} for r in ('R', 'S')],
        'line_yields': [{'month': '2026-10', 'model_code': m, 'percent': '100'} for m in ('M', 'N', 'P')],
        'model_yields': [{'month': '2026-10', 'model_code': m, 'percent': '100'} for m in ('M', 'N', 'P')],
        'cycle_times': [{'model_code': m, 'days': '0'} for m in ('M', 'N', 'P')],
        'switch_durations': [{'from_recipe': 'R', 'to_recipe': 'S', 'hours': '0.95'},
                             {'from_recipe': 'S', 'to_recipe': 'R', 'hours': '2'}]}},
        'plan': {'segments': [segment('a', 0, 'M', 200, '5'), segment('b', 1, 'N', 217, '5')]}}


def segment(id, order, model, quantity, hours, day='2026-10-14'):
    return {'id': id, 'date': day, 'line_code': 'L', 'order': order, 'model_code': model, 'quantity': quantity, 'hours': hours}


def preview(client, data):
    response = client.post('/auto_plan/api/plans/evaluate', json={'snapshot': data})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('path', [
    '/auto_plan/api/plans/evaluate',
    '/auto_plan/api/plans/certificate/preview',
])
def test_current_plan_previews_reject_legacy_line_yield_rows(path, inputs_client):
    data = snapshot()
    data['inputs']['parameters']['line_yields'] = [
        {'month': '2026-10', 'percent': '95'}]

    response = inputs_client.post(path, json={'snapshot': data})

    assert response.status_code == 422, response.text


def test_daily_recipe_capacity_rounds_once_across_models_and_splits_without_writes(inputs_client):
    client = inputs_client
    before = client.get('/auto_plan/api/inputs?version=0').json()
    data = snapshot()
    result = preview(client, data)
    assert result['capacity_valid'] is True
    assert result['days'][0]['recipes'][0]['limit'] == 417
    assert result['days'][0]['recipes'][0]['quantity'] == 417
    assert result['days'][0]['production_hours']['exact'] == '10'
    assert result['initial_states'][0]['state'] == 'unknown_initial'
    data['plan']['segments'][1]['quantity'] = 218
    bad = preview(client, data)
    assert bad['capacity_valid'] is False
    assert any(issue['code'] == 'recipe_capacity' and issue['date'] == '2026-10-14' and issue['line_code'] == 'L' and issue['model_code'] == 'N' for issue in bad['issues'])
    assert client.get('/auto_plan/api/inputs?version=0').json() == before


def switching(id='change', allocations=None, reverse=False):
    return {'id': id, 'line_code': 'L', 'from_recipe': 'S' if reverse else 'R', 'to_recipe': 'R' if reverse else 'S',
            'allocations': allocations or [{'date': '2026-10-14', 'order': 1, 'hours': '0.95'}]}


def test_exact_open_capacity_boundary_and_tiny_positive_hour_display(inputs_client):
    data = snapshot()
    data['plan']['segments'] = [segment('a', 0, 'M', 2, '0.024')]
    assert preview(inputs_client, data)['capacity_valid'] is False
    data['plan']['segments'][0]['hours'] = '0.024001'
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is True
    assert result['days'][0]['recipes'][0]['hours'] == {'exact': '24001/1000000', 'display': '0.0240'}
    data['plan']['segments'][0].update(quantity=1, hours='1/1000000000000000000000000')
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is True
    assert result['days'][0]['production_hours'] == {'exact': '1/1000000000000000000000000', 'display': '<0.0001'}
    data['plan']['segments'][0]['hours'] = '0'
    assert preview(inputs_client, data)['capacity_valid'] is False
    data['plan']['segments'][0]['hours'] = 0.024001
    assert inputs_client.post('/auto_plan/api/plans/evaluate', json={'snapshot': data}).status_code == 422


def test_two_recipes_share_one_hour_and_switch_extra_is_not_double_deducted(inputs_client):
    data = snapshot()
    data['inputs']['calendar'] = {'maintenance': [{'date': '2026-10-14', 'line_code': 'L', 'hours': 23}]}
    data['plan'] = {'segments': [segment('a', 0, 'M', 2, '0.025'), segment('b', 2, 'P', 2, '0.025')],
                    'switches': [switching()]}
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is True
    assert result['switch_count'] == 1
    assert result['days'][0]['occupied_hours']['exact'] == '1'
    assert result['switches'][0]['extra_hours']['exact'] == '19/20'
    data['plan']['segments'][1]['hours'] = '0.02500000000000000001'
    assert preview(inputs_client, data)['capacity_valid'] is False


def test_zero_day_and_explicit_forbidden_or_zero_capacity_reject_positive_allocations(inputs_client):
    data = snapshot()
    data['inputs']['calendar'] = {'maintenance': [{'date': '2026-10-14', 'line_code': 'L', 'hours': 20}],
                                'experiments': [{'date': '2026-10-14', 'line_code': 'L', 'hours': 10}]}
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is False
    assert result['days'][0]['available_hours'] == 0
    data['plan']['segments'] = []
    assert preview(inputs_client, data)['capacity_valid'] is True
    data = snapshot()
    data['inputs']['parameters']['capacities'][0]['pieces'] = 0
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is False
    assert result['days'][0]['recipes'][0]['capacity_state'] == 'zero'
    data['inputs']['masters']['capabilities'] = [{'recipe_code': 'R', 'line_code': 'L', 'allowed': False}]
    assert preview(inputs_client, data)['days'][0]['recipes'][0]['capacity_state'] == 'forbidden'


def test_cross_day_switch_preserves_recipe_over_holiday_and_counts_once(inputs_client):
    data = snapshot()
    data['inputs']['calendar'] = {'holidays': [{'date': '2026-10-15', 'holiday': True}],
                                 'maintenance': [{'date': '2026-10-16', 'line_code': 'L', 'hours': 24}]}
    data['plan'] = {'segments': [segment('a', 0, 'M', 2, '0.025'), segment('b', 1, 'P', 2, '0.025', '2026-10-17')],
        'switches': [switching(allocations=[{'date': '2026-10-14', 'order': 1, 'hours': '0.425'},
                                            {'date': '2026-10-17', 'order': 0, 'hours': '0.525'}])]}
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is True
    assert result['switch_count'] == 1
    assert result['switches'][0]['allocated_hours']['exact'] == '19/20'
    assert result['days'][-1]['ending_recipe'] == 'S'
    data['plan']['switches'][0]['allocations'][1]['hours'] = '0.5249999999'
    bad = preview(inputs_client, data)
    assert any(i['code'] == 'switch_total' for i in bad['issues'])


def test_sequence_checks_boundaries_and_does_not_let_idle_or_switch_events_overlap(inputs_client):
    data = snapshot()
    data['plan'] = {'segments': [segment('a', 0, 'M', 1, '1'), segment('b', 2, 'P', 1, '1')],
                    'initial_states': [{'line_code': 'L', 'recipe_code': 'S'}], 'switches': [switching()]}
    assert any(i['code'] == 'recipe_sequence' for i in preview(inputs_client, data)['issues'])
    data['plan']['initial_states'] = []
    data['plan']['switches'][0]['allocations'][0]['order'] = 3
    assert any(i['code'] == 'recipe_sequence' for i in preview(inputs_client, data)['issues'])
    data['plan']['switches'][0]['allocations'][0]['order'] = 0
    assert any(i['code'] == 'duplicate_order' for i in preview(inputs_client, data)['issues'])
    data['plan']['switches'][0]['allocations'] = [{'date': '2026-10-14', 'order': 1, 'hours': '0.425'},
                                               {'date': '2026-10-15', 'order': 0, 'hours': '0.525'}]
    assert any(i['code'] == 'unfinished_switch' for i in preview(inputs_client, data)['issues'])


def test_missing_parameters_mark_incomplete_default_twelve_but_unknown_initial_is_free(inputs_client):
    data = snapshot()
    data['inputs']['parameters']['cycle_times'] = []
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is True
    assert result['computation_complete'] is False
    assert result['executable'] is False
    data = snapshot()
    data['inputs']['parameters']['switch_durations'] = []
    data['plan'] = {'segments': [segment('a', 0, 'M', 1, '1'), segment('b', 2, 'P', 1, '1')],
                    'switches': [switching(allocations=[{'date': '2026-10-14', 'order': 1, 'hours': '12'}])]}
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is True
    assert result['switches'][0]['duration_state'] == 'default_missing'
    assert result['switches'][0]['total_hours']['exact'] == '12'
    assert result['initial_states'][0]['state'] == 'unknown_initial'


def test_zero_duration_and_all_idle_switches_still_count_once_and_idle_cannot_be_reused(inputs_client):
    data = snapshot()
    data['inputs']['parameters']['switch_durations'][0]['hours'] = '0'
    data['plan'] = {'segments': [segment('a', 0, 'M', 1, '1'), segment('b', 2, 'P', 1, '1')],
                    'switches': [switching(allocations=[{'date': '2026-10-14', 'order': 1, 'hours': '0'}])]}
    assert preview(inputs_client, data)['switch_count'] == 1
    data['inputs']['parameters']['switch_durations'][0]['hours'] = '0.95'
    data['plan']['switches'][0]['allocations'][0].update(hours='0.95', idle_hours='0.95')
    data['plan']['ordinary_idle'] = [{'date': '2026-10-14', 'line_code': 'L', 'hours': '0.95'}]
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is True
    assert result['switch_count'] == 1
    assert result['switches'][0]['extra_hours']['exact'] == '0'
    data['plan']['ordinary_idle'][0]['hours'] = '0.94'
    assert any(i['code'] == 'idle_reuse' for i in preview(inputs_client, data)['issues'])
    data['plan']['ordinary_idle'][0]['hours'] = '23'
    assert any(i['code'] == 'idle_production_overlap' for i in preview(inputs_client, data)['issues'])


def test_standard_quantities_can_generate_exact_certificate_without_a_minimum_hour_step(inputs_client):
    data = snapshot()
    data['inputs']['calendar'] = {'maintenance': [{'date': '2026-10-14', 'line_code': 'L', 'hours': 23}]}
    data['plan']['segments'] = [segment('a', 0, 'M', 2, '0'), segment('b', 1, 'P', 2, '0')]
    for row in data['plan']['segments']:
        del row['hours']
    response = inputs_client.post('/auto_plan/api/plans/certificate/preview', json={'snapshot': data})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['evaluation']['capacity_valid'] is True
    assert result['evaluation']['switch_count'] == 1
    assert result['evaluation']['days'][0]['occupied_hours']['exact'] == '999/1000'
    assert result['plan']['segments'][0]['hours'] == '49/2000'
    assert result['plan']['segments'][1]['hours'] == '49/2000'
    replay = deepcopy(data)
    replay['plan'] = result['plan']
    assert preview(inputs_client, replay)['capacity_valid'] is True


def test_certificate_preserves_exact_witness_with_a_large_capacity_denominator(inputs_client):
    # One two-piece source needs more than 24/capacity hours. Half of the
    # remaining day gives the independently worked witness 12 + 12/capacity.
    capacity=10**200+1
    data=snapshot()
    for row in data['inputs']['parameters']['capacities']:
        if row['recipe_code']=='R' and row['line_code']=='L': row['pieces']=capacity
    data['plan']={'segments':[{'id':'large','date':'2026-10-15','line_code':'L','model_code':'M','order':0,'quantity':2}],
        'initial_states':[{'line_code':'L','recipe_code':'R'}]}
    response=inputs_client.post('/auto_plan/api/plans/certificate/preview',json={'snapshot':data})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['evaluation']['capacity_valid'] and result['evaluation']['computation_complete']
    assert result['plan']['segments'][0]['hours']==f'{12*(capacity+1)}/{capacity}'
    assert result['plan']['switches']==[]


def test_certificate_cross_day_switch_uses_exact_idle_days_and_reports_true_open_boundary_failure(inputs_client):
    data = snapshot()
    data['inputs']['calendar'] = {'maintenance': [{'date': day, 'line_code': 'L', 'hours': 23} for day in ('2026-10-14', '2026-10-17')],
        'holidays': [{'date': '2026-10-15', 'holiday': True}], 'experiments': [{'date': '2026-10-16', 'line_code': 'L', 'hours': 24}]}
    data['plan']['segments'] = [segment('a', 0, 'M', 20, '0'), segment('b', 0, 'P', 20, '0', '2026-10-17')]
    for row in data['plan']['segments']:
        del row['hours']
    result = inputs_client.post('/auto_plan/api/plans/certificate/preview', json={'snapshot': data}).json()
    assert result['evaluation']['capacity_valid'] is True
    assert [row['hours'] for row in result['plan']['switches'][0]['allocations']] == ['1019/2000', '881/2000']
    for row in data['plan']['segments']:
        row['quantity'] = 23
    result = inputs_client.post('/auto_plan/api/plans/certificate/preview', json={'snapshot': data}).json()
    assert result['plan'] is None
    assert result['issues'][0]['code'] == 'certificate_capacity'


def test_evaluation_freezes_beijing_production_time_and_rejects_outside_window(inputs_client):
    data = snapshot()
    result = preview(inputs_client, data)
    assert result['time']['as_of'] == '2026-10-14T00:00:00Z'
    assert result['time']['window_start'] == '2026-10-12'
    assert result['time']['weeks'] == 15
    data['time'] = {'as_of': '2026-10-12T07:00:00+08:00', 'window_start': '2026-10-05', 'weeks': 15}
    assert preview(inputs_client, data)['time']['window_start'] == '2026-10-05'
    data['time']['as_of'] = '2026-10-12T07:00:00'
    assert inputs_client.post('/auto_plan/api/plans/evaluate', json={'snapshot': data}).status_code == 422
    del data['time']
    data['plan']['segments'][0]['date'] = '2027-01-25'
    assert any(i['code'] == 'outside_window' for i in preview(inputs_client, data)['issues'])


def test_directed_reverse_switch_and_same_recipe_production_keep_persistent_state(inputs_client):
    data = snapshot()
    data['plan'] = {'initial_states': [{'line_code': 'L', 'recipe_code': 'S'}],
        'segments': [segment('a', 0, 'P', 1, '1'), segment('b', 1, 'M', 1, '1', '2026-10-17'),
                     segment('c', 2, 'N', 1, '1', '2026-10-17')],
        'switches': [switching(reverse=True, allocations=[{'date': '2026-10-17', 'order': 0, 'hours': '2'}])]}
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is True
    assert result['switch_count'] == 1
    assert result['switches'][0]['total_hours']['exact'] == '2'
    assert result['days'][-1]['ending_recipe'] == 'R'
    data['plan']['switches'][0]['allocations'][0]['hours'] = '0.95'
    assert any(i['code'] == 'switch_total' for i in preview(inputs_client, data)['issues'])


def test_switch_can_finish_in_idle_without_production_and_reserved_idle_is_shared(inputs_client):
    data = snapshot()
    data['plan'] = {'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}], 'segments': [],
        'switches': [switching(allocations=[{'date': '2026-10-14', 'order': 0, 'hours': '0.95', 'idle_hours': '0.95'}]),
                      switching(id='return', reverse=True, allocations=[{'date': '2026-10-14', 'order': 1, 'hours': '2', 'idle_hours': '2'}])],
        'ordinary_idle': [{'date': '2026-10-14', 'line_code': 'L', 'hours': '2.95'}]}
    result = preview(inputs_client, data)
    assert result['capacity_valid'] is True
    assert result['switch_count'] == 2
    assert [row['extra_hours']['exact'] for row in result['switches']] == ['0', '0']
    assert result['days'][0]['occupied_hours']['exact'] == '59/20'
    assert result['days'][0]['ending_recipe'] == 'R'
    data['plan']['ordinary_idle'][0]['hours'] = '2'
    assert any(i['code'] == 'idle_reuse' for i in preview(inputs_client, data)['issues'])


def test_preview_remains_editor_only_and_critical_missing_yields_are_localized(inputs_client):
    data = snapshot()
    data['inputs']['parameters']['line_yields'] = []
    data['inputs']['parameters']['model_yields'] = []
    result = preview(inputs_client, data)
    assert result['computation_complete'] is False
    missing_yields = [row for row in result['issues'] if row['code'] in {'missing_line_yield', 'missing_model_yield'}]
    assert {row['code'] for row in missing_yields} == {'missing_line_yield', 'missing_model_yield'}
    assert all(row['date'] == '2026-10-14' and row['line_code'] == 'L' and row['model_code'] in {'M', 'N'} for row in missing_yields)
    assert {(row['code'], row['line_code'], row['date'], row['severity']) for row in result['adoption_conditions']} == {
        ('initial_state_unknown', 'L', '2026-10-14', 'warning')}
    assert result['executable'] is False
    inputs_client.post('/auto_plan/api/auth/logout')
    assert inputs_client.post('/auto_plan/api/plans/evaluate', json={'snapshot': data}).status_code == 401


def test_certificate_keeps_valid_long_master_codes_without_inventing_invalid_switch_ids(inputs_client):
    data = snapshot()
    long_line = 'L' * 64
    data['inputs']['masters']['lines'][0]['code'] = long_line
    for row in data['inputs']['parameters']['capacities']:
        row['line_code'] = long_line
    data['plan']['segments'] = [segment('a', 0, 'M', 2, '0'), segment('b', 1, 'P', 2, '0')]
    for row in data['plan']['segments']:
        del row['hours']
        row['line_code'] = long_line
    response = inputs_client.post('/auto_plan/api/plans/certificate/preview', json={'snapshot': data})
    assert response.status_code == 200
    assert response.json()['evaluation']['capacity_valid'] is True
