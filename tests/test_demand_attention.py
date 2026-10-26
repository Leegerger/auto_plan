"""Demand handling through the application's public business interfaces."""
from tests.test_plan_preview import preview
from tests.test_output_preview import output_snapshot
from tests.test_inputs import inputs_client


def test_attributed_pending_output_requires_confirmation_without_duplicate_production(inputs_client):
    data = output_snapshot(1000, '2026-10-15', yield_percent='100', ct='0')
    data['inputs']['business'] = {
        'demands': [{'model_code': 'M', 'week': '2026-10-19', 'quantity': 1000}],
        'confirmations': [{'model_code': 'M', 'week': '2026-10-19', 'kind': 'material',
                           'batch_id': 'confirmed-400', 'status': 'allowed',
                           'date': '2026-10-15', 'quantity': 400}],
    }
    result = preview(inputs_client, data)
    attention = result['demand_attention']
    assert len(attention) == 1
    row = attention[0]
    assert (row['demand'], row['attributed'], row['on_time'], row['pending_material']) == (1000, 1000, 400, 600)
    assert row['gap'] == 600
    assert row['unarranged_output'] == 0
    assert row['reasons'] == ['pending_material']
    assert row['planning_complete'] is True
    assert row['deadline'] == '2026-10-26T08:00:00+08:00'


def test_attention_preserves_multiple_reasons_bans_and_confirmed_late_in_deadline_order(inputs_client):
    data = output_snapshot(600, '2026-10-15', yield_percent='100', ct='0')
    data['plan']['segments'] += [
        {'id': 'late-m', 'date': '2026-10-26', 'line_code': 'L', 'order': 0,
         'model_code': 'M', 'quantity': 400, 'hours': '10'},
        {'id': 'late-n', 'date': '2026-10-21', 'line_code': 'L', 'order': 0,
         'model_code': 'N', 'quantity': 100, 'hours': '3'},
    ]
    data['inputs']['business'] = {
        'demands': [{'model_code': 'M', 'week': '2026-10-19', 'quantity': 1000},
                    {'model_code': 'N', 'week': '2026-10-12', 'quantity': 100},
                    {'model_code': 'M', 'week': '2027-02-01', 'quantity': 999}],
        'confirmations': [
            {'model_code': 'M', 'week': '2026-10-19', 'kind': 'material', 'batch_id': 'partial',
             'status': 'allowed', 'date': '2026-10-15', 'quantity': 400},
            {'model_code': 'M', 'week': '2026-10-19', 'kind': 'shipping', 'batch_id': 'ban', 'status': 'forbidden'},
            {'model_code': 'N', 'week': '2026-10-12', 'kind': 'shipping', 'batch_id': 'late-ok',
             'status': 'allowed', 'date': '2026-10-30', 'quantity': 100},
        ],
    }
    attention = preview(inputs_client, data)['demand_attention']
    assert [(row['model_code'], row['week']) for row in attention] == [('N', '2026-10-12'), ('M', '2026-10-19')]
    late, pending = attention
    assert (late['on_time'], late['late'], late['gap'], late['unarranged_output']) == (0, 100, 0, 0)
    assert late['reasons'] == ['confirmed_late']
    assert (pending['on_time'], pending['pending_material'], pending['pending_shipping']) == (400, 200, 400)
    assert pending['unarranged_output'] == 0
    assert set(pending['reasons']) == {'pending_material', 'pending_shipping', 'forbidden_shipping'}


def test_unplanned_model_lists_missing_parameters_without_reporting_a_definite_zero_or_hiding_other_models(inputs_client):
    data = output_snapshot()
    data['plan']['segments'] = []
    parameters = data['inputs']['parameters']
    parameters['line_yields'] = [row for row in parameters['line_yields'] if row['model_code'] != 'M']
    parameters['cycle_times'] = [row for row in parameters['cycle_times'] if row['model_code'] != 'M']
    data['inputs']['business'] = {'demands': [
        {'model_code': 'M', 'week': '2026-10-19', 'quantity': 1000},
        {'model_code': 'N', 'week': '2026-10-19', 'quantity': 500},
    ]}
    result = preview(inputs_client, data)
    missing, ready = result['demand_attention']
    assert missing['unarranged_output'] is None
    assert missing['planning_complete'] is False
    assert missing['reasons'] == ['incomplete']
    assert {(row['category'], row.get('month')) for row in missing['missing_inputs']} == {('line_yields', '2026-10'), ('cycle_times', None)}
    assert all(row['model_code'] == 'M' and row['reason'] for row in missing['missing_inputs'])
    assert (ready['model_code'], ready['unarranged_output'], ready['planning_complete']) == ('N', 500, True)
    assert ready['reasons'] == ['unarranged']
    assert result['computation_complete'] is True


from tests.test_plan_transactions import shared_client, command, post, row


def test_current_preview_and_viewer_read_the_same_authoritative_demand_rows_without_writing(shared_client):
    client, plans, inputs = shared_client
    from auto_plan.input_models import InputSnapshot
    seeded = inputs.read().model_dump(mode='json')
    seeded['business']['demands'] = [{'model_code': 'M', 'week': '2026-10-19', 'quantity': 1000}]
    inputs.versions[1] = InputSnapshot.model_validate(seeded)
    applied = client.post('/auto_plan/api/inputs/apply', json={
        'base_version': 1,
        'changes': {'confirmations': [{'model_code': 'M', 'week': '2026-10-19', 'kind': 'material',
                                       'batch_id': 'some', 'status': 'allowed', 'date': '2026-10-15', 'quantity': 400}]},
    })
    assert applied.status_code == 200, applied.text
    version = applied.json()['input_version']
    source = row(quantity=1000) | {'hours': '24'}
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': [source]}, version=version)).status_code == 200
    before = client.get('/auto_plan/api/plans/current').json()
    current = before['evaluation']['demand_attention'][0]
    assert (current['unarranged_output'], current['pending_material']) == (0, 600)
    current_day = next(day for day in before['workspace_view']['days'] if day['date'] == '2026-10-15' and day['line_code'] == 'L')
    assert current_day['production_hours']['exact'] == '24'
    changed = post(client, 'preview', {key: value for key, value in command(
        {'kind': 'patch_plan', 'segments': [source | {'quantity': 600, 'hours': '15'}]}, base=1, version=version
    ).items() if key not in {'operation_id', 'pending'}})
    assert changed.status_code == 200, changed.text
    proposed = changed.json()['evaluation']['demand_attention'][0]
    assert (proposed['unarranged_output'], proposed['pending_material']) == (400, 200)
    proposed_day = next(day for day in changed.json()['workspace_view']['days'] if day['date'] == '2026-10-15' and day['line_code'] == 'L')
    assert proposed_day['production_hours']['exact'] == '15'
    assert client.get('/auto_plan/api/plans/current').json() == before
    client.app.state.access_store.users['VIEWER'] = {'user_id': 'VIEWER', 'roles': ['viewer'], 'active': True}
    assert client.post('/auto_plan/api/auth/login', json={'username': 'VIEWER', 'password': 'company-secret'}).status_code == 200
    viewed = client.get('/auto_plan/api/plans/current')
    assert viewed.status_code == 200
    assert viewed.json()['evaluation']['demand_attention'] == before['evaluation']['demand_attention']
    assert post(client, 'save', command(version=version, base=1)).status_code == 403
    assert client.get('/auto_plan/api/plans/current').json() == before


def test_existing_output_missing_cycle_time_exposes_unknown_target_and_specific_maintenance(inputs_client):
    data = output_snapshot(1000, '2026-10-15', yield_percent='100', ct='0')
    data['inputs']['parameters']['cycle_times'] = [row for row in data['inputs']['parameters']['cycle_times'] if row['model_code'] != 'M']
    result = preview(inputs_client, data)
    demand = result['demand_attention'][0]
    assert demand['computation_complete'] is False
    assert demand['planning_complete'] is False
    assert demand['unarranged_output'] is None
    assert demand['reasons'] == ['incomplete']
    assert [(row['category'], row['model_code'], row['month']) for row in demand['missing_inputs']] == [('cycle_times', 'M', None)]
    assert result['executable'] is False
