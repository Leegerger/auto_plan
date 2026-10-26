"""Explicit late alternatives through the public planning boundary."""
from auto_plan.input_models import InputSnapshot
from tests.test_plan_transactions import shared_client, post
from tests.test_execution_reset import current_command, head, preview
from tests.test_demand_trials import configure, trial
from tests.test_output_preview import confirmation
from tests.line_state_support import confirm_states
from tests.test_inputs import batch


def test_explicit_late_alternative_keeps_on_time_rate_and_partial_shipping_gap(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs, demand=1000, line_yield='100', model_yield='100', capacity=600)
    data['business']['confirmations'] = [confirmation('shipping', '2026-10-12', 200, '2026-10-19')]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    selected = dict(target_output=1000, operations=[confirm_states()], start_date='2026-10-18', end_date='2026-10-19',
                    scope={'start_date':'2026-10-18','end_date':'2026-10-19','line_codes':['L'],'model_codes':['M']})
    original = head(client)
    ordinary = trial(client, **selected)
    assert ordinary.status_code == 200, ordinary.text
    assert (ordinary.json()['suggested_input'], ordinary.json()['scheduled_on_time_output'], ordinary.json()['remaining_output_target']) == (600, 600, 400)

    checked = trial(client, mode='late', **selected)

    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert (result['suggested_input'], result['scheduled_on_time_output'], result['scheduled_late_output'], result['remaining_output_target']) == (1000, 600, 400, 0)
    assert (result['valid_on_time_output'], result['valid_late_output']) == (600, 200)
    row = result['selected_demand']
    assert (row['on_time'], row['late'], row['gap'], row['pending_shipping'], row['on_time_rate']) == (600, 200, 200, 200, '3/5')
    assert [(row['date'],row['quantity']) for row in result['placements']] == [('2026-10-18',600),('2026-10-19',400)]
    assert result['operation']['mode'] == 'late'
    assert head(client) == original
    command = current_command(client, confirm_states(), result['operation'])
    saved = post(client, 'save', command)
    assert saved.status_code == 200, saved.text
    assert post(client, 'adopt', current_command(client)).status_code == 422
    assert head(client)['latest_adoption'] == 0


def test_pending_material_does_not_reserve_shipping_and_confirming_preserves_arrangement(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs, demand=200, week='2026-10-19', line_yield='100', model_yield='100', ct='14', capacity=100)
    data['business']['confirmations'] = [
        confirmation('material','2026-10-19',100,'2026-10-16',batch='MAT'),
        confirmation('shipping','2026-10-19',100,'2026-10-30',batch='SHIP')]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    checked = trial(client, mode='late', demand_week='2026-10-19', target_output=200, operations=[confirm_states()],
                    start_date='2026-10-15', end_date='2026-10-16',
                    scope={'start_date':'2026-10-15','end_date':'2026-10-16','line_codes':['L'],'model_codes':['M']})
    assert checked.status_code == 200, checked.text
    result = checked.json()
    row = result['selected_demand']
    assert (result['suggested_input'], result['scheduled_late_output'], result['valid_late_output'], result['remaining_output_target']) == (200,200,100,0)
    assert (row['on_time'],row['late'],row['pending_material'],row['pending_shipping'],row['gap']) == (0,100,100,0,100)
    assert [(r['kind'],r['pending_output']) for r in result['required_confirmations']] == [('material',100)]
    saved = post(client,'save',current_command(client,confirm_states(),result['operation']))
    assert saved.status_code == 200, saved.text
    arranged = head(client)['plan']
    assert post(client,'adopt',current_command(client)).status_code == 422

    changed = batch(client,{'confirmations':[confirmation('material','2026-10-19',200,'2026-10-15',batch='MAT')]})
    assert changed.status_code == 200, changed.text
    current = head(client)
    row = current['evaluation']['weekly_results'][0]
    assert (row['late'],row['pending_material'],row['pending_shipping'],row['gap']) == (100,0,100,100)
    assert current['plan'] == arranged
    assert post(client,'adopt',current_command(client)).status_code == 422

    changed = batch(client,{'confirmations':[confirmation('shipping','2026-10-19',100,'2026-10-30',batch='SHIP-2')]})
    assert changed.status_code == 200, changed.text
    current = head(client)
    row = current['evaluation']['weekly_results'][0]
    assert (row['on_time'],row['on_time_rate'],row['late'],row['pending_material'],row['pending_shipping'],row['gap']) == (0,'0',200,0,0,0)
    assert current['plan'] == arranged
    assert next(iter(current['lineage']['demand_goals'].values()))['remaining_output_target'] == 0
    assert post(client,'adopt',current_command(client)).status_code == 200
    no_duplicate = trial(client,mode='late',demand_week='2026-10-19',target_output=None)
    assert no_duplicate.status_code == 200, no_duplicate.text
    assert (no_duplicate.json()['suggested_input'],no_duplicate.json()['remaining_output_target'],no_duplicate.json()['operation']) == (0,0,None)


def test_late_continuation_retains_original_goal_and_each_actions_mode(shared_client):
    client, _, inputs = shared_client
    configure(inputs,demand=1000,line_yield='100',model_yield='100',capacity=600)
    early = trial(client,target_output=1000,operations=[confirm_states()],start_date='2026-10-18',end_date='2026-10-18',
                  scope={'start_date':'2026-10-18','end_date':'2026-10-18','line_codes':['L'],'model_codes':['M']})
    assert early.status_code == 200, early.text
    intent = early.json()['operation']['goal']
    assert post(client,'save',current_command(client,confirm_states(),early.json()['operation'])).status_code == 200
    late = trial(client,mode='late',goal_id=intent['id'],target_output=None,start_date='2026-10-19',end_date='2026-10-19',
                 scope={'start_date':'2026-10-19','end_date':'2026-10-19','line_codes':['L'],'model_codes':['M']})
    assert late.status_code == 200, late.text
    result = late.json()
    assert (result['suggested_input'],result['scheduled_on_time_output'],result['scheduled_late_output'],result['goal_remaining_output_target']) == (400,0,400,0)
    assert result['operation']['goal'] == intent
    assert post(client,'save',current_command(client,result['operation'])).status_code == 200
    current = head(client)
    goal, = current['lineage']['demand_goals'].values()
    assert {k:goal[k] for k in intent} == intent
    assert [r['mode'] for r in goal['accepted_actions'].values()] == ['on_time','late']
    assert current['unallocated'] == []
    assert sorted((r['date'],r['quantity']) for r in current['plan']['segments']) == [('2026-10-18',600),('2026-10-19',400)]


def test_explicit_late_keeps_scope_and_bans_override_ordinary_quota(shared_client):
    client, _, inputs = shared_client
    data = configure(inputs,demand=200,line_yield='100',model_yield='100',capacity=100)
    data['business']['confirmations'] = [
        confirmation('shipping','2026-10-12',100,'2026-10-19',batch='EXPIRED'),
        confirmation('shipping','2026-10-12',100,'2026-10-20',batch='VALID'),
        confirmation('shipping','2026-10-12',None,None,batch='BAN',status='forbidden')]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    options = dict(target_output=200,operations=[confirm_states()],start_date='2026-10-20',end_date='2026-10-20',
                   scope={'start_date':'2026-10-20','end_date':'2026-10-20','line_codes':['L'],'model_codes':['M']})
    ordinary = trial(client,**options)
    assert ordinary.status_code == 200, ordinary.text
    assert (ordinary.json()['suggested_input'],ordinary.json()['remaining_output_target']) == (0,200)
    checked = trial(client,mode='late',**options)
    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert (result['suggested_input'],result['scheduled_late_output'],result['valid_late_output'],result['remaining_output_target']) == (100,100,0,100)
    assert {(r['date'],r['line_code']) for r in result['placements']} == {('2026-10-20','L')}
    assert result['required_confirmations'][0]['banned'] is True
    assert post(client,'save',current_command(client,confirm_states(),result['operation'])).status_code == 200
    assert post(client,'adopt',current_command(client)).status_code == 422
    changed = batch(client,{'confirmations':[confirmation('shipping','2026-10-12',None,None,batch='BAN',status='revoked')]})
    assert changed.status_code == 200, changed.text
    row = head(client)['evaluation']['weekly_results'][0]
    assert (row['late'],row['pending_shipping'],row['gap']) == (100,0,100)


def test_valid_continuity_cannot_waive_a_late_shipping_ban(shared_client):
    from tests.test_unfinished_switches import unfinished
    from tests.test_switch_continuity import confirmation as continuous
    client, _, inputs = shared_client
    data = configure(inputs,demand=100,line_yield='100',model_yield='100')
    data['business'] = {'demands':[{'model_code':'P','week':'2026-10-12','quantity':100}],
        'confirmations':[confirmation('shipping','2026-10-12',None,None,status='forbidden') | {'model_code':'P'}]}
    inputs.versions[1] = InputSnapshot.model_validate(data)
    assert post(client,'save',current_command(client,unfinished())).status_code == 200
    checked = trial(client,mode='late',model_code='P',target_output=100,start_date='2026-10-20',end_date='2026-10-20',
                    scope={'start_date':'2026-10-20','end_date':'2026-10-20','line_codes':['L'],'model_codes':['P']})
    assert checked.status_code == 200, checked.text
    result = checked.json()
    dto, = result['preview']['workspace_view']['switch_continuity']
    assert result['operation'] is not None, result['blockers']
    command = current_command(client,result['operation'],continuous(dto))
    checked = preview(client,result['operation'],continuous(dto))
    assert checked.status_code == 200, checked.text
    assert checked.json()['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    assert not any(row['code']=='remaining_switch_continuity' for row in checked.json()['evaluation']['adoption_conditions'])
    assert any(row['code']=='confirmation_forbidden' for row in checked.json()['evaluation']['issues'])
    assert checked.json()['evaluation']['executable'] is False
    assert post(client,'save',command).status_code == 200
    assert post(client,'adopt',current_command(client)).status_code == 422
