from uuid import uuid4
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from tests.support import MemoryAccessStore, CompanyAuthentication
from tests.input_support import MemoryInputStore
from tests.test_plan_preview import snapshot


def test_shared_preview_does_not_write_save_keeps_baseline_and_explicit_adoption_advances_it():
    from tests.plan_support import MemoryPlanStore
    inputs = MemoryInputStore()
    from auto_plan.input_models import InputSnapshot
    inputs.versions[1] = InputSnapshot.model_validate(snapshot()['inputs'] | {'input_version': 1})
    inputs.version = 1
    access = MemoryAccessStore()
    from datetime import datetime, timezone
    clock = lambda: datetime(2026,10,14,tzinfo=timezone.utc)
    plans = MemoryPlanStore(inputs)
    app = create_app(Settings(initial_admin_id='ADMIN'), store=access, input_store=inputs, plan_store=plans, authenticator=CompanyAuthentication(), clock=clock)
    with TestClient(app, base_url='https://fsscm.cminl.oa') as client:
        client.headers.update({'Origin':'https://fsscm.cminl.oa','X-Auto-Plan-Request':'1'})
        assert client.post('/auto_plan/api/auth/login', json={'username':'ADMIN','password':'company-secret'}).status_code == 200
        assert client.put('/auto_plan/api/admin/authorizations/EDITOR',json={'roles':['editor']}).status_code == 200
        client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'})
        plan = snapshot()['plan']
        for row in plan['segments']: row['date'] = '2026-10-15'
        command = {'base_revision':0,'input_version':1,'latest_adoption':0,'operations':[{'kind':'replace_plan','plan':plan}]}
        response = client.post('/auto_plan/api/plans/preview',json=command)
        assert response.status_code == 200, response.text
        assert response.json()['evaluation']['capacity_valid'] is True
        assert client.get('/auto_plan/api/plans/current').json()['revision'] == 0
        saved = client.post('/auto_plan/api/plans/save',json=command|{'operation_id':str(uuid4()),'pending':True})
        assert saved.status_code == 200, saved.text
        assert saved.json()['revision'] == 1
        assert saved.json()['latest_adoption'] == 0
        adopted = client.post('/auto_plan/api/plans/adopt',json=command|{'base_revision':1,'operations':[],'operation_id':str(uuid4())})
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()['latest_adoption'] == 1


import pytest
from datetime import datetime, timezone
from tests.plan_support import MemoryPlanStore
from auto_plan.input_models import InputSnapshot
from auto_plan.plans import PlanningContext


@pytest.fixture
def shared_client():
    inputs=MemoryInputStore()
    data=snapshot()['inputs']
    data['masters']['lines'].append({'code':'B','name':'二线','active':True})
    for row in list(data['parameters']['capacities']): data['parameters']['capacities'].append(row|{'line_code':'B'})
    inputs.versions[1]=InputSnapshot.model_validate(data|{'input_version':1}); inputs.version=1
    access=MemoryAccessStore(); access.users['EDITOR']={'user_id':'EDITOR','roles':['editor'],'active':True}
    plans=MemoryPlanStore(inputs)
    app=create_app(Settings(public_origin='https://testserver'),store=access,input_store=inputs,plan_store=plans,authenticator=CompanyAuthentication(),clock=lambda:datetime(2026,10,14,tzinfo=timezone.utc))
    with TestClient(app,base_url='https://testserver') as client:
        client.headers.update({'Origin':'https://testserver','X-Auto-Plan-Request':'1'})
        client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'})
        yield client,plans,inputs


def patch(row): return {'kind':'patch_plan','segments':[row]}
def row(identity='a',model='M',day='2026-10-15',line='L',quantity=100):
    return {'id':identity,'date':day,'line_code':line,'order':0,'model_code':model,'quantity':quantity,'hours':'3'}
def command(*operations,base=0,version=1,adoption=0):
    return {'base_revision':base,'input_version':version,'latest_adoption':adoption,'operations':list(operations),'operation_id':str(uuid4()),'pending':True}
def post(client,path,body): return client.post('/auto_plan/api/plans/'+path,json=body)


def test_stale_independent_same_line_day_sources_replay_without_erasing_other_editor(shared_client):
    client,_,_=shared_client
    assert post(client,'save',command(patch(row()))).status_code==200
    second=post(client,'save',command(patch(row('b','N','2026-10-16'))))
    assert second.status_code==200,second.text
    current=client.get('/auto_plan/api/plans/current').json()
    assert {(r['id'],r['quantity']) for r in current['plan']['segments']}=={('a',100),('b',100)}


def test_different_lines_same_model_input_date_conflict_on_shared_rounding_source(shared_client):
    client,_,_=shared_client
    assert post(client,'save',command(patch(row()))).status_code==200
    response=post(client,'save',command(patch(row('b',line='B'))))
    assert response.status_code==409
    assert ['CELL','2026-10-15','B','M'] in response.json()['conflicts']


def test_confirmation_quota_and_fifo_conflict_across_dates_and_lines(shared_client):
    client,plans,inputs=shared_client
    current=inputs.read().model_dump(mode='json'); current['business']['demands']=[{'model_code':'M','week':'2026-10-19','quantity':500}]
    inputs.versions[1]=InputSnapshot.model_validate(current)
    assert post(client,'save',command(patch(row()))).status_code==200
    response=post(client,'save',command(patch(row('b',day='2026-10-16',line='B'))))
    assert response.status_code==409
    assert ['REQ','M','2026-10-19'] in response.json()['conflicts']


def test_same_key_returns_original_result_and_void_prevents_late_old_request(shared_client):
    client,_,_=shared_client
    body=command(patch(row()))
    first=post(client,'save',body)
    assert first.status_code==200
    assert post(client,'save',body).json()==first.json()
    assert post(client,'verify',{'operation_id':body['operation_id']}).json()==first.json()
    late=command(patch(row('b','N','2026-10-16')),base=1)
    assert post(client,'verify',{'operation_id':late['operation_id']}).json()['status']=='ABORTED_VOID'
    rejected=post(client,'save',late)
    assert rejected.status_code==409
    assert client.get('/auto_plan/api/plans/current').json()['revision']==1


def test_input_changes_require_difference_review_and_current_token_repreview(shared_client):
    client,_,inputs=shared_client
    body=command(patch(row()))
    inputs.update('EDITOR',datetime.now(timezone.utc),lambda current:current.model_copy(update={'input_version':2}))
    response=post(client,'save',body)
    assert response.status_code==409
    assert response.json()['input_diff']==[{'path':['input_version'],'before':1,'after':2}]
    assert post(client,'preview',{k:v for k,v in body.items() if k not in ('operation_id','pending')}|{'input_version':2}).status_code==200
    assert post(client,'save',body|{'input_version':2}).status_code==200


def test_pending_draft_does_not_bypass_executed_segment_or_zero_lock_protection(shared_client):
    client,plans,_=shared_client
    assert post(client,'save',command(patch(row()))).status_code==200
    plans.context=PlanningContext.model_validate({'execution':{'facts':[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':60}],'cutoff':'2026-10-15T09:00:00+08:00'},'locks':[{'date':'2026-10-16','line_code':'B','model_code':'N','quantity':0}]})
    moving=command(patch(row(day='2026-10-16')),base=1)
    assert post(client,'save',moving).status_code==409
    assert post(client,'save',command(patch(row('b','N','2026-10-16','B')),base=1)).status_code==409
    retained=post(client,'save',command(patch(row(quantity=80)),base=1))
    assert retained.status_code==200,retained.text


def test_adoption_competition_uses_latest_adoption_and_baseline_is_immutable(shared_client):
    client,_,_=shared_client
    first=post(client,'adopt',command(patch(row())))
    assert first.status_code==200,first.text
    loser=post(client,'adopt',command(base=1))
    assert loser.status_code==409
    history=client.get('/auto_plan/api/plans/adoptions/1').json()
    assert post(client,'save',command(patch(row(quantity=200)),base=1,adoption=1)).status_code==200
    assert client.get('/auto_plan/api/plans/current').json()['latest_adoption']==1
    assert client.get('/auto_plan/api/plans/adoptions/1').json()==history


def test_duplicate_patch_ids_are_rejected_and_reordering_cannot_reassign_executed_pieces(shared_client):
    client,plans,_=shared_client
    duplicate=command({'kind':'patch_plan','segments':[row(),row(quantity=200)]})
    assert post(client,'save',duplicate).status_code==422
    initial=command({'kind':'patch_plan','segments':[row(quantity=500)|{'hours':'12'},row('b',quantity=500)|{'order':1,'hours':'12'}]})
    assert post(client,'save',initial).status_code==200
    plans.context=PlanningContext.model_validate({'execution':{'facts':[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':600}],'cutoff':'2026-10-15T09:00:00+08:00'}})
    reordered=command({'kind':'patch_plan','segments':[row(quantity=500)|{'order':1,'hours':'12'},row('b',quantity=500)|{'order':0,'hours':'12'}]},base=1)
    response=post(client,'save',reordered)
    assert response.status_code==409
    assert response.json()['conflicts'][0]['code']=='executed_order'


def test_boundary_recipe_scope_stops_after_state_converges(shared_client):
    client,_,_=shared_client
    initial={'kind':'replace_plan','plan':{'initial_states':[{'line_code':'L','recipe_code':'R'}],
        'segments':[row(),row('later','N','2026-10-18')]}}
    assert post(client,'save',command(initial)).status_code==200
    switch_out={'id':'out','line_code':'L','from_recipe':'R','to_recipe':'S','allocations':[{'date':'2026-10-15','order':0,'hours':'0.95'}]}
    switch_back={'id':'back','line_code':'L','from_recipe':'S','to_recipe':'R','allocations':[{'date':'2026-10-16','order':0,'hours':'2'}]}
    changed=command({'kind':'patch_plan','segments':[row(model='P')|{'order':1}],'switches':[switch_out,switch_back]},base=1)
    preview=post(client,'preview',{k:v for k,v in changed.items() if k not in ('operation_id','pending')})
    assert preview.status_code==200,preview.text
    assert ['CAP','L','2026-10-16'] in preview.json()['effects']
    assert ['CAP','L','2026-10-18'] not in preview.json()['effects']
    assert post(client,'save',changed).status_code==200
    independent=post(client,'save',command(patch(row('later','N','2026-10-18',quantity=110)),base=1))
    assert independent.status_code==200,independent.text


def test_same_day_line_capacity_is_shared_even_for_different_models(shared_client):
    client,_,_=shared_client
    assert post(client,'save',command(patch(row()))).status_code==200
    response=post(client,'save',command(patch(row('b','N')|{'order':1})))
    assert response.status_code==409
    assert ['CAP','L','2026-10-15'] in response.json()['conflicts']


def test_saved_unallocated_input_and_operation_results_carry_until_explicitly_resolved(shared_client):
    from auto_plan.plan_operations import register_operation, ReplayResult
    from auto_plan.input_models import InputRow
    from typing import Literal
    class LeaveInput(InputRow):
        kind:Literal['test_leave_input']
    class ResolveInput(InputRow):
        kind:Literal['test_resolve_input']
        result_id:str
    from auto_plan.plan_operations import OPERATIONS
    if 'test_leave_input' not in OPERATIONS:
        register_operation('test_leave_input',LeaveInput,lambda plan,operation,context:ReplayResult(plan,unallocated=[{'model_code':'M','quantity':4000,'kind':'unallocated_input'}]))
        register_operation('test_resolve_input',ResolveInput,lambda plan,operation,context:ReplayResult(plan,resolve_unallocated=[operation.result_id]))
    client,_,_=shared_client
    saved=post(client,'save',command({'kind':'test_leave_input','action_id':'append-intent'}))
    assert saved.status_code==200,saved.text
    first=client.get('/auto_plan/api/plans/current').json()['unallocated']
    assert first==[{'model_code':'M','quantity':4000,'kind':'unallocated_input','action_id':'append-intent','id':'append-intent:unallocated:0'}]
    assert post(client,'save',command(base=1)).status_code==200
    assert client.get('/auto_plan/api/plans/current').json()['unallocated']==first
    assert post(client,'save',command(patch(row()),base=2)).status_code==200
    assert client.get('/auto_plan/api/plans/current').json()['unallocated']==first
    resolved=post(client,'save',command({'kind':'test_resolve_input','result_id':first[0]['id']},base=3))
    assert resolved.status_code==200,resolved.text
    assert client.get('/auto_plan/api/plans/current').json()['unallocated']==[]


def test_input_maintenance_invalidates_adoption_and_repreview_uses_current_yields(shared_client):
    client, _, _ = shared_client
    operation = {'kind':'patch_plan','segments':[row()],
                 'initial_states':[{'line_code':'L','recipe_code':'R'}]}
    body = command(operation)
    preview_body = {key:value for key,value in body.items() if key not in ('operation_id','pending')}
    preview = post(client, 'preview', preview_body)
    assert preview.status_code == 200, preview.text
    assert preview.json()['evaluation']['output_totals']['expected'] == 100
    assert client.get('/auto_plan/api/plans/current').json()['revision'] == 0

    applied = client.post('/auto_plan/api/inputs/apply', json={
        'base_version':1,
        'changes':{'line_yields':[{'month':'2026-10','model_code':'M','percent':'50'}]},
    })
    assert applied.status_code == 200, applied.text
    current_version = applied.json()['input_version']
    rejected = post(client, 'adopt', body)
    assert rejected.status_code == 409
    assert rejected.json()['input_version'] == current_version
    assert client.get('/auto_plan/api/plans/current').json()['latest_adoption'] == 0

    current_preview = post(client, 'preview', preview_body | {'input_version':current_version})
    assert current_preview.status_code == 200, current_preview.text
    assert current_preview.json()['evaluation']['output_totals']['expected'] == 50
    assert client.get('/auto_plan/api/plans/current').json()['revision'] == 0
    adopted = post(client, 'adopt', body | {'input_version':current_version,'operation_id':str(uuid4())})
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()['latest_adoption'] == 1
