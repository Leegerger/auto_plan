"""PLAN_TEST public API evidence in a fresh disposable business namespace."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_plans import OraclePlanStore
from tests.oracle_namespace import disposable_business_namespace
from tests.test_oracle_plans import FaultPool
from tests.test_plan_preview import snapshot
from tests.support import CompanyAuthentication

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Opt-in disposable PLAN_TEST AP_* only')]
NOW=datetime(2026,10,14,tzinfo=timezone.utc)


def test_real_facts_reset_correction_race_and_unknown_save_are_one_guarded_transaction():
    settings=Settings(initial_admin_id='T24ADMIN');assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool,'AP_TEST_'+uuid4().hex[:6].upper()) as namespace:
            faults=FaultPool(namespace);plans=OraclePlanStore(faults);inputs=OracleInputStore(namespace)
            def app(): return create_app(settings,store=access,input_store=inputs,plan_store=plans,authenticator=CompanyAuthentication(),clock=lambda:NOW)
            with TestClient(app(),base_url=settings.public_origin) as client:
                client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert client.post('/auto_plan/api/auth/login',json={'username':'T24ADMIN','password':'company-secret'}).status_code==200
                assert client.put('/auto_plan/api/admin/authorizations/T33EDITOR',json={'roles':['editor']}).status_code==200
                assert client.post('/auto_plan/api/auth/login',json={'username':'T33EDITOR','password':'company-secret'}).status_code==200
                data=snapshot()['inputs'];changes={key:value for section in data.values() for key,value in section.items()}
                assert client.post('/auto_plan/api/inputs/apply',json={'base_version':0,'changes':changes}).status_code==200
                def post(path,payload): return client.post('/auto_plan/api/plans/'+path,json=payload)
                def head():
                    response=client.get('/auto_plan/api/plans/current');assert response.status_code==200,response.text
                    return response.json()
                def body(*operations):
                    current=head()
                    return {'base_revision':current['revision'],'input_version':current['input_version'],'latest_adoption':current['latest_adoption'],'operation_id':str(uuid4()),'pending':True,'operations':list(operations)}
                coverage={'start_date':'2026-10-12','end_date':'2026-10-18','line_codes':['L'],'model_codes':['M','N','P'],'blanks_are_zero':True}
                rows=[{'id':name,'date':day,'line_code':'L','model_code':'M','order':0,'quantity':1000,'hours':'24'} for name,day in [('early','2026-10-15'),('late','2026-10-16')]]
                # Initial-import coverage is an explicit boundary fixture, not a database side channel.
                from auto_plan.plans import InitializePlan, PlanService, PlanningContext
                from auto_plan.plan_models import Plan, ExecutionCoverage, ExecutionContext
                context=PlanningContext(coverage=[ExecutionCoverage.model_validate(coverage)],execution=ExecutionContext(coverage=[ExecutionCoverage.model_validate(coverage|{'end_date':'2026-10-14'})],cutoff=NOW))
                initial=InitializePlan(base_revision=0,input_version=head()['input_version'],latest_adoption=0,operation_id=uuid4(),pending=True,inputs=inputs.read(),context=context,plan=Plan.model_validate({'segments':rows}))
                PlanService(plans).initialize(initial,'T33EDITOR',NOW)
                assert post('adopt',body()).status_code==200
                assert head()['latest_adoption']==1
                def reset(balance,correction=False):
                    return {'kind':'weekly_reset','source_adoption':1,'correction':correction,
                        'snapshot':{'batch_id':'monday','production_week':'2026-10-12','cutoff':'2026-10-12T08:00:00+08:00','models':[{'model_code':m,'wip':0,'net_balance':balance if m=='M' else 0} for m in ['M','N','P']]},
                        'scope':{'start_date':'2026-10-12','end_date':'2027-01-24','line_codes':['L'],'model_codes':[]}}
                before=head();probe=post('preview',{k:v for k,v in body(reset(600)).items() if k not in ('operation_id','pending')})
                assert probe.status_code==200,probe.text
                assert probe.json()['reset_preview']['targets'][0]['target']==1400
                assert head()==before
                first=body(reset(600));saved=post('adopt',first)
                assert saved.status_code==200,saved.text
                assert post('adopt',first).json()==saved.json()
                history=client.get('/auto_plan/api/plans/adoptions/2').json()
                assert sum(r['quantity'] for r in head()['plan']['segments'])==1400
                left=body(reset(500,True));right=body(reset(500,True))
                with ThreadPoolExecutor(2) as pool:
                    competed=list(pool.map(lambda value:post('adopt',value),[left,right]))
                assert sorted(r.status_code for r in competed)==[200,409],[(r.status_code,r.text) for r in competed]
                assert head()['latest_adoption']==3
                assert sum(r['quantity'] for r in head()['plan']['segments'])==1500
                assert client.get('/auto_plan/api/plans/adoptions/2').json()==history
                facts={'kind':'record_execution','rows':[{'date':'2026-10-14','line_code':'L','model_code':'M','quantity':20}],'cutoff':'2026-10-14T08:00:00+08:00','reason':'复核累计执行量'}
                fact_race=body(facts|{'rows':[facts['rows'][0]|{'quantity':10}]});reset_race=body(reset(480,True))
                with ThreadPoolExecutor(2) as pool:
                    competed=list(pool.map(lambda value:post(*value),[('save',fact_race),('adopt',reset_race)]))
                assert sorted(r.status_code for r in competed)==[200,409],[(r.status_code,r.text) for r in competed]
                assert head()['input_version']==fact_race['input_version']+1
                before=head();payload=body(facts,reset(450,True));faults.commit_fault='before'
                failed=post('adopt',payload)
                assert failed.status_code==503 and failed.json()['state']=='unknown',failed.text
                assert head()==before
                assert post('verify',{'operation_id':payload['operation_id']}).json()['status']=='ABORTED_VOID'
                payload=body(facts,reset(450,True));faults.commit_fault='after'
                unknown=post('adopt',payload)
                assert unknown.status_code==503 and unknown.json()['state']=='unknown',unknown.text
                committed=post('verify',{'operation_id':payload['operation_id']}).json()
                assert committed['status']=='COMMITTED' and committed['latest_adoption']==before['latest_adoption']+1
                assert post('adopt',payload).json()==committed
                current=head();assert current['input_version']==before['input_version']+1
                assert current['context']['execution']['facts'][0]['quantity']==20
                assert current['reset_preview']['targets'][0]['target']==1550
                assert current['reset_preview']['targets'][0]['actual']==20
                assert sum(r['quantity'] for r in current['plan']['segments'])==1530
                repeated=post('adopt',body(reset(600)))
                assert repeated.status_code==200 and repeated.json()['already_effective'],repeated.text
                assert head()['latest_adoption']==committed['latest_adoption']
                assert plans.check_consistency() is True
                expected=head()
            with TestClient(app(),base_url=settings.public_origin) as reopened:
                reopened.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert reopened.post('/auto_plan/api/auth/login',json={'username':'T33EDITOR','password':'company-secret'}).status_code==200
                assert reopened.get('/auto_plan/api/plans/current').json()==expected
    finally: access.close()
