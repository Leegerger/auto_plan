"""Versioned rolling evidence must be reproducible through public business APIs."""
from datetime import datetime
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from tests.support import MemoryAccessStore,CompanyAuthentication
from tests.input_support import MemoryInputStore
from tests.plan_support import MemoryPlanStore
from tests.demand_support import MemoryDemandStore
from scripts.acceptance.assertions import assert_frozen_snapshot
from scripts.acceptance.validate import load


def test_all_rolling_companions_preserve_their_own_business_case_and_gates(tmp_path):
    from scripts.acceptance.rolling_v2 import write_assets
    v1=Path(__file__).resolve().parents[2]/'docs/acceptance/frozen'
    original=json.loads((v1/'manifest.json').read_text())
    target=tmp_path/'all-rolling'
    write_assets(v1,target)
    manifest=json.loads((target/'manifest.json').read_text())
    assert {r['id'] for r in manifest['scenarios']}=={'r10-rolling-v2','r20-rolling-v2','r40-rolling-v2'}
    assert manifest['gates_seconds']==original['gates_seconds']
    assert manifest['gates_seconds']['new_week']==120
    assert manifest['include_first_repeat'] and manifest['repeats_per_mode']==5
    for scenario in manifest['scenarios']:
        prior=next(r for r in original['scenarios'] if r['id']==scenario['predecessor']['scenario'])
        assert scenario['expected']==prior['expected'] and len(scenario['expected']['objective'])==33
        assert scenario['action']==prior['action']
        for name in ('source','reference'):
            old=load(v1,prior[name]);changed=load(target,scenario[name])
            assert changed['execution']['audit'][0]['action_id']==scenario['id']+'-execution'
            assert changed['reset']['batch_id']=='synthetic-'+scenario['id']+'-zero-wip'
            changed['execution'].pop('audit')
            changed['reset']=old['reset']
            changed['inputs']['business']['demands']=old['inputs']['business']['demands']
            assert changed==old
        assert len(load(target,scenario['source'])['plan']['segments'])==3920
        assert len(load(target,scenario['reference'])['plan']['segments'])==4200


def test_rolling_v2_preserves_v1_and_matches_a_public_prior_adoption_and_week_advance(tmp_path):
    from scripts.acceptance.rolling_v2 import write_assets,preparation_operations
    v1=Path(__file__).resolve().parents[2]/'docs/acceptance/frozen'
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in v1.iterdir() if p.is_file()}
    target=tmp_path/'rolling-v2'
    write_assets(v1,target)
    manifest=json.loads((target/'manifest.json').read_text())
    assert manifest['fixture_version']==2
    scenario=manifest['scenarios'][0]
    assert scenario['id']=='r20-rolling-v2'
    source=load(target,scenario['source'])
    prior_manifest=json.loads((v1/'manifest.json').read_text())
    predecessor=next(r for r in prior_manifest['scenarios'] if r['id']=='r20-rolling')
    assert manifest['gates_seconds']==prior_manifest['gates_seconds']
    assert manifest['repeats_per_mode']==5 and manifest['include_first_repeat']
    assert scenario['expected']==predecessor['expected']
    assert scenario['expected']['objective']==['0']*30+['420','0','0']
    assert scenario['action']==predecessor['action']
    for key in ('source','reference'):
        old_rolling=load(v1,predecessor[key])
        new_rolling=load(target,scenario[key])
        restored=deepcopy(new_rolling)
        restored['execution'].pop('audit')
        restored['reset']=old_rolling['reset']
        restored['inputs']['business']['demands']=old_rolling['inputs']['business']['demands']
        assert restored==old_rolling
    assert len(load(target,scenario['reference'])['plan']['segments'])==4200
    assert len(load(target,scenario['reference'])['plan']['switches'])==420
    assert source['execution']['audit'][0]['action_id']=='r20-rolling-v2-execution'
    assert source['execution']['audit'][0]['actor']=='ACCEPTANCE0'
    assert source['execution']['audit'][0]['recorded_at']=='2026-10-19T08:00:00+08:00'
    prior=next(r for r in prior_manifest['scenarios'] if r['id']=='r20-loose')
    old=load(v1,prior['source'])
    inputs=MemoryInputStore();plans=MemoryPlanStore(inputs);access=MemoryAccessStore()
    access.users['ACCEPTANCE0']={'user_id':'ACCEPTANCE0','roles':['editor'],'active':True}
    now=[datetime.fromisoformat(old['time']['as_of'])]
    app=create_app(Settings(public_origin='https://testserver'),store=access,input_store=inputs,plan_store=plans,
        demand_store=MemoryDemandStore(plans),authenticator=CompanyAuthentication(),clock=lambda:now[0])
    with TestClient(app,base_url='https://testserver') as client:
        client.headers.update({'Origin':'https://testserver','X-Auto-Plan-Request':'1'})
        assert client.post('/auto_plan/api/auth/login',json={'username':'ACCEPTANCE0','password':'company-secret'}).status_code==200
        def call(path,payload=None):
            response=client.get('/auto_plan/api'+path) if payload is None else client.post('/auto_plan/api'+path,json=payload)
            assert response.status_code==200,response.text[:1000]
            return response.json()
        call('/initialization/apply',load(v1,prior['import'])|{'operation_id':str(uuid4())})
        head=call('/plans/current')
        adopted=call('/plans/adopt',{'base_revision':head['revision'],'input_version':head['input_version'],'latest_adoption':head['latest_adoption'],
            'operation_id':str(uuid4()),'operations':[{'kind':'replace_plan','plan':old['plan']}]})
        original=call(f"/plans/adoptions/{adopted['latest_adoption']}")
        assert original['coverage']==old['baseline']['coverage']
        initial_files=call('/demands/files')['files']
        assert len(initial_files)==1
        assert [(row['model_code'],row['week'],row['delta']) for row in initial_files[0]['rows']]==[
            (row['model_code'],row['week'],row['quantity']) for row in old['inputs']['business']['demands']]
        now[0]=datetime.fromisoformat(source['time']['as_of'])
        assert client.post('/auto_plan/api/auth/login',json={'username':'ACCEPTANCE0','password':'company-secret'}).status_code==200
        call('/demands/apply',{'operation_id':str(uuid4()),'input_version':head['input_version'],'attribution_date':'2026-10-19',
            'rows':[{'model_code':f'M{i:02d}','week':'2027-01-25','delta':700} for i in range(40)]})
        head=call('/plans/current')
        command={'base_revision':head['revision'],'input_version':head['input_version'],'latest_adoption':head['latest_adoption'],
            'operations':preparation_operations(adopted['latest_adoption'],source)}
        checked=call('/plans/preview',command)
        assert checked['evaluation']['capacity_valid'] and checked['evaluation']['computation_complete']
        assert_frozen_snapshot(checked['snapshot'],source)
        operation_id=str(uuid4())
        saved=call('/plans/save',command|{'operation_id':operation_id,'pending':False})
        assert call('/plans/verify',{'operation_id':operation_id})==saved
        frozen=call(f"/plans/revisions/{saved['revision']}")
        assert_frozen_snapshot(frozen['snapshot'],source)
        assert call(f"/plans/adoptions/{adopted['latest_adoption']}")==original
        current=call('/plans/current')
        assert current['latest_adoption']==adopted['latest_adoption']
        assert current['context']['reset']==source['reset']
        assert current['context']['execution']['audit']==source['execution']['audit']
        assert current['context']['execution']['facts']==[]
        record=current['lineage']['resets']['2026-10-19']
        assert record['source_adoption']==adopted['latest_adoption']
        assert record['snapshot']==source['reset']
        assert len(record['historical_inputs'])==280
        assert sum(row['quantity'] for row in record['historical_inputs'])==28000
        files=call('/demands/files')['files']
        assert len(files)==2
        assert files[0]==initial_files[0]
        assert [(row['attribution_date'],len(row['rows'])) for row in files]==[('2026-10-12',600),('2026-10-19',40)]
        assert [(row['model_code'],row['week'],row['delta']) for row in files[1]['rows']]==[(f'M{i:02d}','2027-01-25',700) for i in range(40)]
        totals=checked['evaluation']['output_totals']
        assert (totals['expected'],totals['on_time'],totals['gap'])==(392000,392000,28000)
        assert len(source['plan']['segments'])==3920 and len(source['plan']['switches'])==392
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in v1.iterdir() if p.is_file()}==before


def test_two_phase_public_preparer_retains_original_uuid_after_unknown_new_week_demand_reply(tmp_path):
    from scripts.acceptance.rolling_v2 import write_assets,prepare_public,OutcomeUnknown
    fixtures=tmp_path/'fixtures'
    write_assets(Path(__file__).resolve().parents[2]/'docs/acceptance/frozen',fixtures)
    scenario=next(r for r in json.loads((fixtures/'manifest.json').read_text())['scenarios'] if r['id']=='r10-rolling-v2')
    source=load(fixtures,scenario['source']);prior=load(fixtures,scenario['prior_source'])
    inputs=MemoryInputStore();plans=MemoryPlanStore(inputs);access=MemoryAccessStore()
    access.users['ACCEPTANCE0']={'user_id':'ACCEPTANCE0','roles':['editor'],'active':True}
    clock=[datetime.fromisoformat(prior['time']['as_of'])]
    app=create_app(Settings(public_origin='https://testserver'),store=access,input_store=inputs,plan_store=plans,
        demand_store=MemoryDemandStore(plans),authenticator=CompanyAuthentication(),clock=lambda:clock[0])
    output=tmp_path/'prepared'
    config={'scenario':scenario['id'],'target':{'kind':'adapter','disposable':True,'production':False}}
    with TestClient(app,base_url='https://testserver') as client:
        client.headers.update({'Origin':'https://testserver','X-Auto-Plan-Request':'1'})
        def login():
            assert client.post('/auto_plan/api/auth/login',json={'username':'ACCEPTANCE0','password':'company-secret'}).status_code==200
        login()
        lost=[]
        def call(path,payload=None):
            response=client.get('/auto_plan/api'+path) if payload is None else client.post('/auto_plan/api'+path,json=payload)
            assert response.status_code==200,response.text[:1000]
            if path=='/demands/apply' and not lost:
                lost.append(payload['operation_id'])
                raise OutcomeUnknown
            return response.json()
        initial=prepare_public(call,fixtures,config,output,phase='initial')
        assert initial['status']=='awaiting_clock_transition'
        old_adoption=initial['actual_tokens']['latest_adoption']
        original=call(f'/plans/adoptions/{old_adoption}')
        clock[0]=datetime.fromisoformat(source['time']['as_of']);login()
        complete=prepare_public(call,fixtures,config,output,phase='rolling')
        assert complete['status']=='pass' and complete['public_api_only']
        assert complete['actual_tokens']['latest_adoption']==old_adoption
        assert [r['kind'] for r in complete['operations']]==['initialize_prior_week','adopt_prior_week','append_new_week_demands','save_rolling_source']
        changed=complete['operations'][2]
        assert changed['operation_id']==lost[0] and changed['unknown_outcome_reconciled']
        assert changed['verification']==changed['result'] and changed['status']=='COMMITTED'
        assert len({r['operation_id'] for r in complete['operations']})==4
        assert call(f'/plans/adoptions/{old_adoption}')==original
        observed=json.loads((output/'state.json').read_text())
        assert_frozen_snapshot(observed['snapshot'],source)
        from auto_plan.plan_models import ExecutionContext
        assert observed['head']['context']['execution']==ExecutionContext.model_validate(source['execution']).model_dump(mode='json')
        assert observed['head']['context']['reset']==source['reset']
        assert len(json.loads((output/'demand-files.json').read_text())['files'])==2
        run_config=json.loads((output/'run-config.json').read_text())
        assert run_config['scenario']==scenario['id'] and run_config['fixtures']==str(fixtures.resolve())
