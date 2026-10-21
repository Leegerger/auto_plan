"""Manual event slots remain stable without erasing real switch/scope changes."""
from copy import deepcopy
from fractions import Fraction
from uuid import uuid4
import json
import pytest

from auto_plan.input_models import InputSnapshot
from tests.test_plan_transactions import shared_client, command, post, row


@pytest.mark.parametrize('with_fixed_neighbor', [False, True])
def test_quantity_decrease_retains_existing_slots_when_no_event_is_inserted(shared_client, with_fixed_neighbor,tmp_path):
    client, plans, inputs = shared_client
    data = inputs.read().model_dump(mode='json')
    models = ('M', 'N') if with_fixed_neighbor else ('M',)
    data['business']['demands'] = [
        {'model_code': model, 'week':'2026-10-12', 'quantity':100} for model in models
    ]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    target = row('target', model='N' if with_fixed_neighbor else 'M') | {
        'order':5 if with_fixed_neighbor else 0,
    }
    fixed = row('fixed') | {'order':3}
    plan = {'segments':([fixed] if with_fixed_neighbor else []) + [target],
            'initial_states':[{'line_code':'L','recipe_code':'R'}]}
    saved = post(client,'save',command({'kind':'replace_plan','plan':plan}))
    assert saved.status_code == 200, saved.text
    source = client.get('/auto_plan/api/plans/current').json()
    operation = {'kind':'change_quantity','segment_id':'target','quantity':99,
                 'end_date':'2026-10-15','action_id':str(uuid4()),
                 'scope':{'start_date':'2026-10-15','end_date':'2026-10-15',
                          'line_codes':['L'],'model_codes':[target['model_code']]}}
    request = {'base_revision':source['revision'],'input_version':source['input_version'],
               'latest_adoption':source['latest_adoption'],'operations':[operation]}
    unchanged = post(client,'preview',request | {'operations':[]})
    assert unchanged.status_code == 200, unchanged.text
    source_snapshot = unchanged.json()['snapshot']
    response = post(client,'preview',request)
    (tmp_path/'source.json').write_text(json.dumps(source,ensure_ascii=False,sort_keys=True))
    (tmp_path/'request.json').write_text(json.dumps(request,ensure_ascii=False,sort_keys=True))
    (tmp_path/'response.json').write_text(response.text)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['evaluation']['capacity_valid'] is True
    assert value['evaluation']['computation_complete'] is True
    assert value['evaluation']['output_totals']['expected'] == (199 if with_fixed_neighbor else 99)
    assert value['evaluation']['output_totals']['gap'] == 1
    actual = next(segment for segment in value['plan']['segments'] if segment['id']=='target')
    expected = deepcopy(target) | {'quantity':99}
    # Rebuilt exact production hours may change. Every other requested field,
    # including the existing integer event slot, must remain untouched here.
    assert {key:actual[key] for key in actual if key!='hours'} == {key:expected[key] for key in expected if key!='hours'}
    # Literal1000pieces/day; fixed same-Recipe neighbor keeps3hours.
    # ceil(1000*(fixed+H)/24)>=quantity gives these strict lower bounds.
    assert Fraction(actual['hours']) > Fraction(219 if with_fixed_neighbor else 294,125)
    assert Fraction(actual['hours']) <= (21 if with_fixed_neighbor else 24)
    if with_fixed_neighbor:
        assert next(segment for segment in value['plan']['segments'] if segment['id']=='fixed') == fixed
    for key in ('switches','initial_states','ordinary_idle','current_day_witnesses'):
        assert value['plan'][key] == source['plan'][key]
    for key in ('inputs','execution','reset','locks','baseline','preferences'):
        assert value['snapshot'][key] == source_snapshot[key]
    assert client.get('/auto_plan/api/plans/current').json() == source


def _request(source,operation):
    return {'base_revision':source['revision'],'input_version':source['input_version'],
            'latest_adoption':source['latest_adoption'],'operations':[operation]}


def test_real_new_switch_has_slots_and_fixed_anchor_requires_expansion(shared_client):
    client, _, _ = shared_client
    initial={'segments':[row('fixed') | {'order':0},row('moving','P',day='2026-10-16') | {'order':1}],
             'initial_states':[{'line_code':'L','recipe_code':'R'}],
             'switches':[{'id':'old-switch','line_code':'L','from_recipe':'R','to_recipe':'S',
                'allocations':[{'date':'2026-10-16','order':0,'hours':'0.95'}]}]}
    assert post(client,'save',command({'kind':'replace_plan','plan':initial})).status_code == 200
    source=client.get('/auto_plan/api/plans/current').json()
    operation={'kind':'move_segment','segment_id':'moving','target_date':'2026-10-15',
               'target_line':'L','target_order':0,'scope':{
                   'start_date':'2026-10-15','end_date':'2026-10-16',
                   'line_codes':['L'],'model_codes':['P']}}
    blocked=post(client,'preview',_request(source,operation))
    assert blocked.status_code == 422,blocked.text
    assert '扩大机种范围' in blocked.json()['detail']
    assert client.get('/auto_plan/api/plans/current').json() == source
    expanded=deepcopy(operation)
    expanded['scope']['model_codes']=['M','P']
    checked=post(client,'preview',_request(source,expanded))
    assert checked.status_code == 200,checked.text
    value=checked.json()
    assert value['evaluation']['capacity_valid'] is True
    assert sum(r['quantity'] for r in value['plan']['segments']) == 200
    events=sorted([(r['order'],r['model_code']) for r in value['plan']['segments']] +
                  [(a['order'],s['from_recipe']+'>'+s['to_recipe'])
                   for s in value['plan']['switches'] for a in s['allocations']])
    assert [name for _,name in events] == ['R>S','P','S>R','M']
    assert len({position for position,_ in events}) == len(events)
    assert all(position>=0 for position,_ in events)
    assert client.get('/auto_plan/api/plans/current').json() == source


def test_cross_day_switch_boundary_rejects_without_rewriting_either_allocation(shared_client):
    client, _, _=shared_client
    initial={'segments':[row('fixed') | {'order':1},row('target','P',day='2026-10-16') | {'order':5}],
        'initial_states':[{'line_code':'L','recipe_code':'R'}],
        'switches':[{'id':'cross','line_code':'L','from_recipe':'R','to_recipe':'S',
            'allocations':[{'date':'2026-10-15','order':3,'hours':'0.4'},
                           {'date':'2026-10-16','order':3,'hours':'0.55'}]}]}
    assert post(client,'save',command({'kind':'replace_plan','plan':initial})).status_code == 200
    source=client.get('/auto_plan/api/plans/current').json()
    operation={'kind':'change_quantity','segment_id':'target','quantity':99,'end_date':'2026-10-16',
        'scope':{'start_date':'2026-10-16','end_date':'2026-10-16','line_codes':['L'],'model_codes':['P']}}
    response=post(client,'preview',_request(source,operation))
    assert response.status_code == 422,response.text
    assert '切机跨越调整范围边界' in response.json()['detail']
    assert client.get('/auto_plan/api/plans/current').json() == source


def test_explicit_reorder_is_preserved_and_stale_effects_still_protect_shared_capacity(shared_client):
    client, _, _=shared_client
    assert post(client,'save',command({'kind':'patch_plan','segments':[
        row('first') | {'order':0},row('target','N') | {'order':5}]})).status_code == 200
    source=client.get('/auto_plan/api/plans/current').json()
    scope={'start_date':'2026-10-15','end_date':'2026-10-15','line_codes':['L'],'model_codes':[]}
    reordered=post(client,'preview',_request(source,{'kind':'reorder_segments',
        'date':'2026-10-15','line_code':'L','segment_ids':['target','first'],'scope':scope}))
    assert reordered.status_code == 200,reordered.text
    assert [r['id'] for r in reordered.json()['plan']['segments']] == ['target','first']
    assert sum(r['quantity'] for r in reordered.json()['plan']['segments']) == 200
    operation={'kind':'change_quantity','segment_id':'target','quantity':99,'end_date':'2026-10-15',
               'scope':scope | {'model_codes':['N']}}
    preview=post(client,'preview',_request(source,operation))
    assert preview.status_code == 200,preview.text
    effects=preview.json()['effects']
    assert ['CAP','L','2026-10-15'] in effects
    assert ['CELL','2026-10-15','L','N'] in effects
    assert not any(effect[0]=='REQ' and effect[1]=='M' for effect in effects)
    assert post(client,'save',command({'kind':'patch_plan','segments':[row('first',quantity=110)]},base=source['revision'])).status_code == 200
    current=client.get('/auto_plan/api/plans/current').json()
    conflict=post(client,'save',_request(source,operation) | {'operation_id':str(uuid4()),'pending':True})
    assert conflict.status_code == 409,conflict.text
    assert ['CAP','L','2026-10-15'] in conflict.json()['conflicts']
    assert client.get('/auto_plan/api/plans/current').json() == current


def test_future_decrease_keeps_current_execution_and_zero_locks_and_rejects_positive_lock(shared_client):
    client, _, _=shared_client
    initial={'segments':[row('executed',day='2026-10-14') | {'order':0},row('target') | {'order':5}],
             'initial_states':[{'line_code':'L','recipe_code':'R'}]}
    assert post(client,'save',command({'kind':'replace_plan','plan':initial})).status_code == 200
    source=client.get('/auto_plan/api/plans/current').json()
    execution={'kind':'record_execution','rows':[{'date':'2026-10-14','line_code':'L','model_code':'M','quantity':50}],
               'cutoff':'2026-10-14T08:00:00+08:00'}
    assert post(client,'save',_request(source,execution) | {'operation_id':str(uuid4()),'pending':True}).status_code == 200
    source=client.get('/auto_plan/api/plans/current').json()
    lock={'date':'2026-10-15','line_code':'B','model_code':'M','quantity':0}
    assert post(client,'save',_request(source,{'kind':'lock_cells','cells':[lock]}) | {'operation_id':str(uuid4()),'pending':True}).status_code == 200
    source=client.get('/auto_plan/api/plans/current').json()
    operation={'kind':'change_quantity','segment_id':'target','quantity':99,'end_date':'2026-10-15',
        'scope':{'start_date':'2026-10-15','end_date':'2026-10-15','line_codes':['L'],'model_codes':['M']}}
    checked=post(client,'preview',_request(source,operation))
    assert checked.status_code == 200,checked.text
    value=checked.json()
    assert value['snapshot']['execution'] == source['context']['execution']
    assert value['snapshot']['locks'] == [lock]
    assert value['plan']['segments'][0] == source['plan']['segments'][0]
    assert value['plan']['current_day_witnesses'] == source['plan']['current_day_witnesses']
    positive=lock | {'line_code':'L','quantity':100}
    assert post(client,'save',_request(source,{'kind':'lock_cells','cells':[positive]}) | {'operation_id':str(uuid4()),'pending':True}).status_code == 200
    source=client.get('/auto_plan/api/plans/current').json()
    blocked=post(client,'preview',_request(source,operation))
    assert blocked.status_code == 409,blocked.text
    assert any(issue['code']=='locked_cell' for issue in blocked.json()['conflicts'])
    assert client.get('/auto_plan/api/plans/current').json() == source


def test_decrease_keeps_an_existing_switch_identity_allocation_and_event_slot(shared_client,tmp_path):
    client, _, _ = shared_client
    switch = {'id':'existing-r-s','line_code':'L','from_recipe':'R','to_recipe':'S',
              'allocations':[{'date':'2026-10-15','order':3,'hours':'0.95'}]}
    initial = {'segments':[row('fixed') | {'order':1}, row('target','P') | {'order':5}],
               'initial_states':[{'line_code':'L','recipe_code':'R'}],'switches':[switch]}
    saved = post(client,'save',command({'kind':'replace_plan','plan':initial}))
    assert saved.status_code == 200, saved.text
    source = client.get('/auto_plan/api/plans/current').json()
    response = post(client,'preview',{'base_revision':source['revision'],
        'input_version':source['input_version'],'latest_adoption':source['latest_adoption'],
        'operations':[{'kind':'change_quantity','segment_id':'target','quantity':99,
            'end_date':'2026-10-15','scope':{'start_date':'2026-10-15','end_date':'2026-10-15',
                'line_codes':['L'],'model_codes':['P']}}]})
    assert response.status_code == 200, response.text
    value = response.json()
    (tmp_path/'source.json').write_text(json.dumps(source,ensure_ascii=False,sort_keys=True))
    (tmp_path/'response.json').write_text(response.text)
    assert value['evaluation']['capacity_valid'] is True
    assert value['plan']['switches'] == source['plan']['switches']
    assert [(r['id'],r['order']) for r in value['plan']['segments']] == [('fixed',1),('target',5)]
    assert next(r for r in value['plan']['segments'] if r['id']=='fixed') == source['plan']['segments'][0]
    assert client.get('/auto_plan/api/plans/current').json() == source
