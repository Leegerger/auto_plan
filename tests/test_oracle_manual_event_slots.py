"""Actual Oracle public manual recertification in fresh disposable business tables."""
from tests.line_state_support import confirm_states
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.settings import Settings
from tests.oracle_namespace import disposable_business_namespace
from tests.support import CompanyAuthentication
from tests.test_plan_preview import snapshot

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Opt-in disposable PLAN_TEST only')]
NOW=datetime(2026,10,14,tzinfo=timezone.utc)


@pytest.fixture
def oracle_manual(tmp_path):
    settings=Settings(initial_admin_id='T24ADMIN',oracle_pool_max=2)
    assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    prefix='AP_TEST_'+uuid4().hex[:6].upper()
    metadata={'namespace':prefix,'pool_max':2,'source_sha256':hashlib.sha256(
        (Path(__file__).parents[1]/'src/auto_plan/manual_operations.py').read_bytes()).hexdigest()}
    def objects():
        with access.pool.acquire() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT OBJECT_NAME,OBJECT_TYPE FROM USER_OBJECTS WHERE SUBSTR(OBJECT_NAME,1,:n)=:prefix ORDER BY OBJECT_NAME',n=len(prefix),prefix=prefix)
                return [list(row) for row in cursor]
    try:
        metadata['objects_before']=objects()
        assert metadata['objects_before']==[]
        with disposable_business_namespace(access.pool,prefix) as namespace:
            metadata['objects_installed']=objects()
            plans=OraclePlanStore(namespace)
            app=create_app(settings,store=access,input_store=plans.input_store,plan_store=plans,
                authenticator=CompanyAuthentication(),clock=lambda:NOW)
            with TestClient(app,base_url=settings.public_origin) as client:
                client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert client.post('/auto_plan/api/auth/login',json={'username':'T24ADMIN','password':'company-secret'}).status_code==200
                assert client.put('/auto_plan/api/admin/authorizations/T29EDITOR',json={'roles':['editor']}).status_code==200
                assert client.post('/auto_plan/api/auth/logout').status_code==200
                assert client.post('/auto_plan/api/auth/login',json={'username':'T29EDITOR','password':'company-secret'}).status_code==200
                before=client.get('/auto_plan/api/plans/current').json()
                assert (before['revision'],before['input_version'],before['latest_adoption'])==(0,0,0)
                assert before['plan']['segments']==[] and before['context']['locks']==[]
                changes={key:value for section in snapshot()['inputs'].values() for key,value in section.items()}
                seeded=client.post('/auto_plan/api/inputs/apply',json={'base_version':0,'changes':changes})
                assert seeded.status_code==200,seeded.text
                try: yield client,plans,tmp_path
                finally: assert client.post('/auto_plan/api/auth/logout').status_code==200
        metadata['objects_after']=objects()
        assert metadata['objects_after']==[]
    finally:
        access.close()
        metadata['pool_closed']=True
        (tmp_path/'namespace-cleanup.json').write_text(json.dumps(metadata,indent=2))


@pytest.mark.parametrize('case',['single','fixed','switch'])
def test_actual_oracle_manual_quantity_slots_switches_and_protection(oracle_manual,case):
    client,plans,proof=oracle_manual
    def current():
        response=client.get('/auto_plan/api/plans/current')
        assert response.status_code==200,response.text
        return response.json()
    def command(*operations):
        head=current()
        return {'base_revision':head['revision'],'input_version':head['input_version'],
            'latest_adoption':head['latest_adoption'],'operations':list(operations),
            'operation_id':str(uuid4()),'pending':True}
    def post(path,payload):
        if path=='preview': payload={key:value for key,value in payload.items() if key not in {'operation_id','pending'}}
        return client.post('/auto_plan/api/plans/'+path,json=payload)
    def row(identity,model,order):
        return {'id':identity,'date':'2026-10-15','line_code':'L','model_code':model,
                'quantity':100,'order':order,'hours':'3'}
    target=row('target','P' if case=='switch' else 'N' if case=='fixed' else 'M',0 if case=='single' else 5)
    fixed=row('fixed','M',1 if case=='switch' else 3)
    initial={'segments':([] if case=='single' else [fixed])+[target],
             'initial_states':[{'line_code':'L','recipe_code':'R'}],'switches':[]}
    if case=='switch':
        initial['switches']=[{'id':'existing-r-s','line_code':'L','from_recipe':'R','to_recipe':'S',
            'allocations':[{'date':'2026-10-15','order':3,'hours':'0.95'}]}]
    saved=post('save',command({'kind':'replace_plan','plan':initial}))
    assert saved.status_code==200,saved.text
    adopted=post('adopt',command(confirm_states()))
    assert adopted.status_code==200,adopted.text
    source=current()
    adoption=client.get('/auto_plan/api/plans/adoptions/1').json()
    history=client.get('/auto_plan/api/plans/history').json()
    revision=client.get(f"/auto_plan/api/plans/revisions/{source['revision']}").json()
    operation={'kind':'change_quantity','segment_id':'target','quantity':99,'end_date':'2026-10-15',
        'action_id':str(uuid4()),'scope':{'start_date':'2026-10-15','end_date':'2026-10-15',
            'line_codes':['L'],'model_codes':[target['model_code']]}}
    request=command(operation)
    response=post('preview',request)
    (proof/'source.json').write_text(json.dumps(source,sort_keys=True))
    (proof/'request.json').write_text(json.dumps(request,sort_keys=True))
    (proof/'response.json').write_text(response.text)
    assert response.status_code==200,response.text
    value=response.json()
    assert value['evaluation']['capacity_valid'] is True
    assert value['evaluation']['computation_complete'] is True
    assert value['evaluation']['output_totals']['expected']==(99 if case=='single' else 199)
    changed=next(r for r in value['plan']['segments'] if r['id']=='target')
    assert changed['quantity']==99 and changed['order']==target['order']
    expected=source['plan'].copy()
    expected['segments']=[r | {'quantity':99,'hours':changed['hours']} if r['id']=='target' else r for r in source['plan']['segments']]
    assert value['plan']==expected
    assert value['snapshot']['baseline']['plan']==adoption['snapshot']['plan']
    assert ['CAP','L','2026-10-15'] in value['effects']
    assert ['CELL','2026-10-15','L',target['model_code']] in value['effects']
    assert current()==source
    assert client.get('/auto_plan/api/plans/history').json()==history
    assert client.get(f"/auto_plan/api/plans/revisions/{source['revision']}").json()==revision
    assert client.get('/auto_plan/api/plans/adoptions/1').json()==adoption
    locked=post('save',command({'kind':'lock_cells','cells':[{'date':'2026-10-15','line_code':'L',
        'model_code':target['model_code'],'quantity':100}]}))
    assert locked.status_code==200,locked.text
    before=current()
    rejected=post('preview',command(operation))
    assert rejected.status_code==409,rejected.text
    assert any(row['code']=='locked_cell' for row in rejected.json()['conflicts'])
    assert current()==before
    assert client.get('/auto_plan/api/plans/adoptions/1').json()==adoption
    assert plans.check_consistency() is True
