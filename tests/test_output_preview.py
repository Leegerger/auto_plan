from tests.test_inputs import inputs_client
from tests.test_plan_preview import snapshot, segment, preview


def output_snapshot(quantity=1000, day='2026-10-14', yield_percent='93.10', ct='0.50'):
    data = snapshot()
    data['time'] = {'as_of': '2026-10-14T09:00:00+08:00', 'window_start': '2026-10-12'}
    data['inputs']['parameters']['model_yields'][0]['percent'] = yield_percent
    data['inputs']['parameters']['cycle_times'][0]['days'] = ct
    data['plan']['segments'] = [segment('a', 0, 'M', quantity, '24', day)]
    data['inputs']['parameters']['capacities'][0]['pieces'] = 10000
    data['execution'] = {'cutoff': '2026-10-14T09:00:00+08:00', 'coverage': [{'start_date': '2026-10-12', 'end_date': '2026-10-14', 'line_codes': ['L'], 'model_codes': ['M'], 'blanks_are_zero': True}]}
    data['inputs']['business'] = {'demands': [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1000}]}
    return data


def test_raw_legacy_global_line_yield_snapshot_remains_evaluable(inputs_client):
    data = output_snapshot(1000, yield_percent='90', ct='0')
    data['inputs']['parameters']['line_yields'] = [{'month': '2026-10', 'percent': '95'}]
    result = preview(inputs_client, data)
    assert result['output_sources'][0]['line_yield'] == '95'
    assert result['output_totals']['expected'] == 855


def test_same_recipe_models_keep_independent_model_month_line_yields(inputs_client):
    data = snapshot()
    data['time'] = {'as_of': '2026-10-14T09:00:00+08:00', 'window_start': '2026-10-12'}
    data['inputs']['parameters']['line_yields'] = [
        {'month': '2026-10', 'model_code': 'M', 'percent': '95'},
        {'month': '2026-10', 'model_code': 'N', 'percent': '80'},
    ]
    data['inputs']['parameters']['model_yields'] = [
        {'month': '2026-10', 'model_code': 'M', 'percent': '90'},
        {'month': '2026-10', 'model_code': 'N', 'percent': '90'},
    ]
    data['inputs']['parameters']['cycle_times'] = [
        {'model_code': 'M', 'days': '0'}, {'model_code': 'N', 'days': '0'}]
    data['inputs']['parameters']['capacities'] = [
        {'month': '2026-10', 'recipe_code': 'R', 'line_code': 'L', 'pieces': 10000}]
    data['plan']['segments'] = [
        segment('a', 0, 'M', 1000, '12'),
        segment('b', 1, 'N', 1000, '12'),
    ]
    data['execution'] = {
        'cutoff': '2026-10-14T09:00:00+08:00',
        'coverage': [{'start_date': '2026-10-12', 'end_date': '2026-10-14',
                      'line_codes': ['L'], 'model_codes': ['M', 'N'], 'blanks_are_zero': True}],
    }
    result = preview(inputs_client, data)
    assert {source['model_code']: source['portions'][0]['quantity']
            for source in result['output_sources']} == {'M': 855, 'N': 720}, result['issues']

    data['inputs']['parameters']['line_yields'][0]['percent'] = '96'
    result = preview(inputs_client, data)
    assert {source['model_code']: source['portions'][0]['quantity']
            for source in result['output_sources']} == {'M': 864, 'N': 720}


def test_exact_ct_portions_and_cross_line_aggregation(inputs_client):
    data = output_snapshot()
    result = preview(inputs_client, data)
    assert [(row['date'], row['quantity']) for row in result['output_sources'][0]['portions']] == [('2026-10-14', 466), ('2026-10-15', 466)]
    assert result['weekly_results'][0]['on_time'] == 932
    assert result['weekly_results'][0]['gap'] == 68
    assert result['executable'] is True
    data['inputs']['masters']['lines'].append({'code': 'L2', 'name': '二线', 'active': True})
    data['inputs']['parameters']['capacities'].append({'month': '2026-10', 'recipe_code': 'R', 'line_code': 'L2', 'pieces': 10000})
    data['plan']['segments'][0]['quantity'] = 600
    second = segment('b', 0, 'M', 400, '24'); second['line_code'] = 'L2'
    data['plan']['segments'].append(second)
    data['execution']['coverage'][0]['line_codes'].append('L2')
    result = preview(inputs_client, data)
    assert len(result['output_sources']) == 1
    assert [p['quantity'] for p in result['output_sources'][0]['portions']] == [466, 466]


def confirmation(kind, week, quantity, date, batch='B', status='allowed'):
    return {'model_code': 'M', 'week': week, 'kind': kind, 'batch_id': batch,
            'status': status, 'date': date, 'quantity': quantity, 'note': ''}


def test_largest_remainder_partial_material_and_no_nominal_minimum(inputs_client):
    data = output_snapshot(1072)
    data['inputs']['business']['demands'] = [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 600}, {'model_code': 'M', 'week': '2026-10-19', 'quantity': 400}]
    data['inputs']['business']['confirmations'] = [confirmation('material', '2026-10-19', 428, '2026-10-14')]
    result = preview(inputs_client, data)
    assert result['output_totals']['expected'] == 1000
    second = [a for a in result['attributions'] if a['demand_week'] == '2026-10-19']
    assert {a['apportioned_input'] for a in second} == {429}
    assert sum(a['material_satisfied'] for a in second) == 399
    assert result['weekly_results'][1]['pending'] == 1
    assert result['weekly_results'][1]['on_time'] == 399
    assert result['executable'] is False
    data['inputs']['business']['confirmations'][0]['quantity'] = 429
    result = preview(inputs_client, data)
    assert result['executable'] is True
    assert result['weekly_results'][1]['on_time'] == 400


def test_facts_replace_plan_expired_is_not_output_and_wip_is_separate(inputs_client):
    data = output_snapshot(500, '2026-10-12', '100', '0')
    data['plan']['segments'].append(segment('b', 0, 'M', 500, '24', '2026-10-15'))
    data['execution']['facts'] = [{'date': '2026-10-12', 'line_code': 'L', 'model_code': 'M', 'quantity': 300}]
    data['reset'] = {'batch_id': 'RESET', 'production_week': '2026-10-12', 'cutoff': '2026-10-12T08:00:00+08:00', 'models': [{'model_code': 'M', 'wip': 100, 'net_balance': 0}]}
    result = preview(inputs_client, data)
    assert result['output_totals']['expected'] == 900
    assert result['reconciled_cells'][0]['actual'] == 300
    assert result['reconciled_cells'][0]['expired'] == 200
    assert result['output_sources'][0]['kind'] == 'wip'
    assert result['attributions'][0]['source_id'].startswith('wip:')
    data['execution']['facts'][0]['quantity'] = 600
    assert preview(inputs_client, data)['output_totals']['expected'] == 1200
    data['execution']['facts'] = []
    data['execution']['coverage'][0]['blanks_are_zero'] = False
    result = preview(inputs_client, data)
    assert result['computation_complete'] is False
    assert any(i['code'] == 'execution_unknown' for i in result['issues'])


def test_plan_execution_facts_and_wip_apply_the_same_two_yields(inputs_client):
    data = output_snapshot(300, '2026-10-12', '98', '0')
    data['inputs']['parameters']['line_yields'][0]['percent'] = '95'
    data['execution']['facts'] = [{'date': '2026-10-12', 'line_code': 'L', 'model_code': 'M', 'quantity': 300}]
    data['reset'] = {'batch_id': 'RESET', 'production_week': '2026-10-12', 'cutoff': '2026-10-12T08:00:00+08:00', 'models': [{'model_code': 'M', 'wip': 100, 'net_balance': 0}]}
    result = preview(inputs_client, data)
    sources = {row['kind']: row for row in result['output_sources']}
    assert (sources['input']['yield_month'], sources['input']['line_yield'], sources['input']['model_yield']) == ('2026-10', '95', '98')
    assert (sources['wip']['yield_month'], sources['wip']['line_yield'], sources['wip']['model_yield']) == ('2026-10', '95', '98')
    assert [row['quantity'] for row in sources['input']['portions']] == [280]
    assert [row['quantity'] for row in sources['wip']['portions']] == [94]
    assert result['output_totals']['expected'] == 374


def test_confirmation_upserts_share_input_version_and_keep_history(inputs_client):
    from tests.test_inputs import masters
    masters(inputs_client)
    saved = inputs_client.get('/auto_plan/api/inputs').json()
    model = saved['masters']['models'][0]['code']
    batch = confirmation('material', '2026-10-19', 300, '2026-10-14'); batch['model_code'] = model
    result = inputs_client.post('/auto_plan/api/inputs/apply', json={'base_version': saved['input_version'], 'changes': {'confirmations': [batch]}})
    assert result.status_code == 200, result.text
    old = result.json()
    batch['quantity'] = 128
    revised = inputs_client.post('/auto_plan/api/inputs/apply', json={'base_version': old['input_version'], 'changes': {'confirmations': [batch]}})
    assert revised.status_code == 200
    assert len(revised.json()['business']['confirmations']) == 1
    assert revised.json()['business']['confirmations'][0]['quantity'] == 128
    assert inputs_client.get(f"/auto_plan/api/inputs?version={old['input_version']}").json()['business']['confirmations'][0]['quantity'] == 300
    assert inputs_client.post('/auto_plan/api/inputs/apply', json={'base_version': old['input_version'], 'changes': {'confirmations': [batch]}}).status_code == 409
    invalid = inputs_client.post('/auto_plan/api/inputs/preview', json={'base_version': revised.json()['input_version'], 'changes': {'confirmations': [batch, batch]}})
    assert invalid.json()['valid'] is False


def test_holiday_keeps_two_roundings_on_same_day_and_opening_exception_only_affects_input(inputs_client):
    data = output_snapshot(1000, '2026-10-18')
    data['inputs']['calendar'] = {'holidays': [{'date': '2026-10-18', 'holiday': True}], 'opening_exceptions': [{'date': '2026-10-18', 'line_code': 'L', 'enabled': True}]}
    data['inputs']['business']['confirmations'] = [confirmation('shipping', '2026-10-12', 932, '2026-10-19')]
    result = preview(inputs_client, data)
    assert [(p['date'], p['quantity']) for p in result['output_sources'][0]['portions']] == [('2026-10-19', 466), ('2026-10-19', 466)]
    assert result['weekly_results'][0]['on_time'] == 0
    assert result['weekly_results'][0]['late'] == 932
    assert result['weekly_results'][0]['gap'] == 68
    assert result['weekly_results'][0]['on_time_rate'] == '0'


def test_necessary_tail_does_not_waive_input_and_zero_share_cannot_bypass_ban(inputs_client):
    data = output_snapshot(1072)
    data['inputs']['business']['demands'] = [{'model_code': 'M', 'week': '2026-10-19', 'quantity': 999}]
    data['inputs']['business']['confirmations'] = [confirmation('material', '2026-10-19', 1071, '2026-10-14')]
    result = preview(inputs_client, data)
    assert result['output_totals']['unallocated'] == 1
    assert {a['apportioned_input'] for a in result['attributions']} == {1072}
    assert result['weekly_results'][0]['on_time'] == 998
    data = output_snapshot(1, yield_percent='100')
    data['inputs']['business']['demands'] = [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1}, {'model_code': 'M', 'week': '2026-10-19', 'quantity': 1}]
    result = preview(inputs_client, data)
    assert result['attributions'][1]['apportioned_input'] == 0
    assert result['weekly_results'][1]['on_time'] == 1
    data['inputs']['business']['confirmations'] = [confirmation('material', '2026-10-19', 0, '2026-10-14', status='forbidden')]
    result = preview(inputs_client, data)
    assert result['weekly_results'][1]['on_time'] == 0
    assert result['attributions'][1]['forbidden_material'] is True
    assert any(i['code'] == 'confirmation_forbidden' and i['demand_week'] == '2026-10-19' for i in result['issues'])


def test_fifo_pending_early_week_never_skips_to_later_and_shipping_dates_stay_per_batch(inputs_client):
    data = output_snapshot(120, '2026-10-22', '100', '0')
    data['inputs']['business']['demands'] = [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 120}, {'model_code': 'M', 'week': '2026-10-19', 'quantity': 120}]
    data['inputs']['business']['confirmations'] = [confirmation('shipping', '2026-10-12', 100, '2026-10-20', 'EARLY'), confirmation('shipping', '2026-10-12', 100, '2026-10-23', 'LATE')]
    result = preview(inputs_client, data)
    assert result['weekly_results'][0]['late'] == 100
    assert result['weekly_results'][0]['pending_shipping'] == 20
    assert result['weekly_results'][1]['attributed'] == 0
    assert [u['used'] for u in result['confirmation_usage']] == [0, 100]


def test_pending_material_does_not_reserve_shipping_and_revocation_rematches_only_affected_part(inputs_client):
    data = output_snapshot(100, '2026-10-14', '100', '14')
    data['plan']['segments'].append(segment('b', 0, 'M', 100, '24', '2026-10-15'))
    data['inputs']['business']['demands'] = [{'model_code': 'M', 'week': '2026-10-19', 'quantity': 200}]
    data['inputs']['business']['confirmations'] = [confirmation('material', '2026-10-19', 100, '2026-10-15', 'MAT'), confirmation('shipping', '2026-10-19', 100, '2026-10-30', 'SHIP')]
    result = preview(inputs_client, data)
    assert result['weekly_results'][0]['pending_material'] == 100
    assert result['weekly_results'][0]['late'] == 100
    assert result['attributions'][0]['shipping_batches'] == []
    assert result['attributions'][1]['shipping_batches'] == [{'batch_id': 'SHIP', 'quantity': 100}]
    data['inputs']['business']['confirmations'][0]['date'] = '2026-10-14'
    result = preview(inputs_client, data)
    assert result['attributions'][0]['late'] == 100
    assert result['attributions'][1]['pending_material'] == 100
    data['inputs']['business']['confirmations'][0]['quantity'] = 200
    result = preview(inputs_client, data)
    assert result['attributions'][1]['pending_shipping'] == 100
    assert result['weekly_results'][0]['pending'] == 100


def test_cross_month_sources_keep_yield_month_and_window_tail_can_cover_original_week(inputs_client):
    data = output_snapshot(100, '2026-10-31', '90', '0.5')
    data['plan']['segments'].append(segment('b', 0, 'M', 100, '24', '2026-11-01'))
    data['inputs']['parameters']['line_yields'].append({'month': '2026-11', 'model_code': 'M', 'percent': '100'})
    data['inputs']['parameters']['model_yields'].append({'month': '2026-11', 'model_code': 'M', 'percent': '100'})
    data['inputs']['parameters']['capacities'].append({'month': '2026-11', 'recipe_code': 'R', 'line_code': 'L', 'pieces': 10000})
    data['inputs']['business']['demands'] = [{'model_code': 'M', 'week': '2026-10-26', 'quantity': 100}, {'model_code': 'M', 'week': '2026-11-02', 'quantity': 100}]
    data['inputs']['business']['confirmations'] = [confirmation('material', '2026-11-02', 90, '2026-11-01')]
    result = preview(inputs_client, data)
    assert [w['on_time'] for w in result['weekly_results']] == [100, 90]
    assert [a['apportioned_input'] for a in result['attributions'] if a['source_id'].endswith('2026-11-01')] == [10, 90, 90]
    data = output_snapshot(200, '2027-01-24', '100', '7')
    for kind in ['line_yields', 'model_yields', 'capacities']:
        for row in data['inputs']['parameters'][kind]: row['month'] = '2027-01'
    data['inputs']['business']['demands'] = [{'model_code': 'M', 'week': '2027-01-18', 'quantity': 200}]
    data['inputs']['business']['confirmations'] = [confirmation('shipping', '2027-01-18', 200, '2027-01-31')]
    result = preview(inputs_client, data)
    assert result['output_totals']['outside_window'] == 200
    assert result['weekly_results'][0]['late'] == 200
    assert result['weekly_results'][0]['gap'] == 0


def test_reset_requires_exact_monday_0800_cutoff_and_explicit_fact_scope(inputs_client):
    data = output_snapshot(100, yield_percent='100', ct='0')
    data['reset'] = {'batch_id': 'R', 'production_week': '2026-10-12', 'cutoff': '2026-10-12T20:00:00+08:00', 'models': [{'model_code': 'M', 'wip': 10, 'net_balance': 0}]}
    result = preview(inputs_client, data)
    assert result['executable'] is False
    assert any(i['code'] == 'reset_context' for i in result['issues'])
    data['reset']['cutoff'] = '2026-10-12T00:00:00+00:00'
    assert not any(i['code'] == 'reset_context' for i in preview(inputs_client, data)['issues'])
    data['execution']['facts'] = [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 20}]
    assert any(i['code'] == 'execution_cutoff' for i in preview(inputs_client, data)['issues'])


def test_unplanned_facts_keep_identity_validation_and_current_day_rounds_once(inputs_client):
    data = output_snapshot(1000)
    data['execution']['facts'] = [{'date': '2026-10-14', 'line_code': 'L', 'model_code': 'M', 'quantity': 600}]
    result = preview(inputs_client, data)
    assert result['output_totals']['expected'] == 932
    assert result['reconciled_cells'][0]['quantity'] == 1000
    assert result['reconciled_cells'][0]['actual'] == 600
    assert result['reconciled_cells'][0]['remaining'] == 400
    data['execution']['facts'][0]['line_code'] = 'UNKNOWN'
    assert any(i['code'] == 'unknown_master' and i['line_code'] == 'UNKNOWN' for i in preview(inputs_client, data)['issues'])


def test_ct_beyond_representable_output_calendar_is_incomplete_without_server_failure(inputs_client):
    data = output_snapshot(ct='4000000')
    result = preview(inputs_client, data)
    assert result['computation_complete'] is False
    assert any(i['code'] == 'output_date_range' for i in result['issues'])


def test_rolling_window_retains_demand_history_without_reusing_full_old_demand(inputs_client):
    data = output_snapshot(100, '2026-10-19', '100', '0')
    data['time'] = {'as_of': '2026-10-19T09:00:00+08:00', 'window_start': '2026-10-19'}
    data['execution'] = {'cutoff': '2026-10-19T09:00:00+08:00', 'coverage': [{'start_date':'2026-10-19','end_date':'2026-10-19','line_codes':['L'],'model_codes':['M'],'blanks_are_zero':True}]}
    data['inputs']['business']['demands'] = [{'model_code':'M','week':'2026-10-12','quantity':100}, {'model_code':'M','week':'2026-10-19','quantity':100}]
    result = preview(inputs_client, data)
    assert result['executable'] is True
    assert [w['week'] for w in result['weekly_results']] == ['2026-10-19']
    assert result['weekly_results'][0]['on_time'] == 100
    assert result['excluded_demands'] == [{'model_code':'M','week':'2026-10-12','quantity':100}]
    assert len(data['inputs']['business']['demands']) == 2
    assert any(i['code'] == 'demand_outside_window' and i['severity'] == 'warning' for i in result['issues'])


def test_two_yields_batch_priority_partial_revision_and_replacement_quota(inputs_client):
    data = output_snapshot(1072)
    data['inputs']['parameters']['line_yields'][0]['percent'] = '95'
    data['inputs']['parameters']['model_yields'][0]['percent'] = '98'
    data['inputs']['business']['demands'] = [{'model_code':'M','week':'2026-10-12','quantity':600}, {'model_code':'M','week':'2026-10-19','quantity':400}]
    data['inputs']['business']['confirmations'] = [confirmation('material','2026-10-19',300,'2026-10-14','A'), confirmation('material','2026-10-19',129,'2026-10-14','B')]
    result = preview(inputs_client, data)
    assert result['output_totals']['expected'] == 1000
    assert [u['used'] for u in result['confirmation_usage']] == [300,129]
    data['inputs']['business']['confirmations'][1]['quantity'] = 128
    result = preview(inputs_client, data)
    assert result['weekly_results'][1]['on_time'] == 399
    assert result['weekly_results'][1]['pending_material'] == 1
    assert result['issues'][0]['related_batch_ids'] == ['A','B']
    data['inputs']['business']['confirmations'].append(confirmation('material','2026-10-19',1,'2026-10-14','C'))
    assert preview(inputs_client, data)['weekly_results'][1]['on_time'] == 400
    data['inputs']['business']['confirmations'].append(confirmation('material','2026-10-19',0,'2026-10-14','BAN','forbidden'))
    result = preview(inputs_client, data)
    assert result['weekly_results'][1]['on_time'] == 0
    assert all(u['used'] == 0 for u in result['confirmation_usage'])
    data['inputs']['business']['confirmations'][-1]['status'] = 'revoked'
    assert preview(inputs_client, data)['weekly_results'][1]['on_time'] == 400


def test_whole_week_ban_needs_no_meaningless_date_or_quota(inputs_client):
    from tests.test_inputs import masters
    masters(inputs_client)
    current = inputs_client.get('/auto_plan/api/inputs').json()
    ban = {'model_code':'M','week':'2026-10-19','kind':'material','batch_id':'BAN','status':'forbidden','note':'整周禁令'}
    payload = {'base_version':current['input_version'],'changes':{'confirmations':[ban]}}
    result = inputs_client.post('/auto_plan/api/inputs/apply', json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['business']['confirmations'][0]['quantity'] is None
    ban['status'] = 'allowed'
    payload['base_version'] = result.json()['input_version']
    invalid = inputs_client.post('/auto_plan/api/inputs/preview', json=payload).json()
    assert invalid['valid'] is False
    assert {e['field'] for e in invalid['errors']} == {'date','quantity'}


def test_missing_critical_input_marks_affected_week_rate_incomplete(inputs_client):
    data = output_snapshot()
    data['inputs']['parameters']['cycle_times'] = []
    result = preview(inputs_client, data)
    assert result['computation_complete'] is False
    assert result['weekly_results'][0]['computation_complete'] is False
    assert result['weekly_results'][0]['on_time_rate'] is None
    assert result['weekly_results'][0]['on_time_rate_display'] == '计算未完成'
    assert result['output_totals']['computation_complete'] is False
