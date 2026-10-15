"""Separate operation namespaces share the same public Oracle recovery contract."""
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
from auto_plan.oracle_demands import OracleDemandStore
from tests.oracle_namespace import disposable_business_namespace
from tests.test_initialization import initial_payload
from tests.test_oracle_plans import FaultPool
from tests.support import CompanyAuthentication

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Opt-in disposable PLAN_TEST only')]


def test_plan_and_demand_keys_keep_independent_replay_void_races_and_unknown_resolution():
    settings=Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool,'AP_TEST_'+uuid4().hex[:6].upper()) as namespace:
            faults=FaultPool(namespace)
            plans=OraclePlanStore(faults)
            app=create_app(settings,store=access,input_store=OracleInputStore(namespace),plan_store=plans,
                demand_store=OracleDemandStore(plans),authenticator=CompanyAuthentication(),clock=lambda:datetime(2026,10,14,tzinfo=timezone.utc))
            with TestClient(app,base_url=settings.public_origin) as client:
                client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert client.post('/auto_plan/api/auth/login',json={'username':'T24ADMIN','password':'company-secret'}).status_code==200
                assert client.put('/auto_plan/api/admin/authorizations/T26EDITOR',json={'roles':['editor']}).status_code==200
                assert client.post('/auto_plan/api/auth/login',json={'username':'T26EDITOR','password':'company-secret'}).status_code==200
                assert client.post('/auto_plan/api/initialization/apply',json=initial_payload()|{'operation_id':str(uuid4())}).status_code==200
                shared_id=str(uuid4())
                for family in ('plans','demands'):
                    def command(key):
                        head=client.get('/auto_plan/api/plans/current').json()
                        if family=='plans':
                            return {k:head[k] for k in ('input_version','latest_adoption')}|{'base_revision':head['revision'],'operations':[],'pending':True,'operation_id':key}
                        return {'input_version':head['input_version'],'attribution_date':'2026-10-14','rows':[{'model_code':'M','week':'2026-10-12','delta':1}],'operation_id':key}
                    submit='/auto_plan/api/'+family+('/save' if family=='plans' else '/apply')
                    verify='/auto_plan/api/'+family+'/verify'
                    payload=command(shared_id)
                    faults.commit_fault='after'
                    unknown=client.post(submit,json=payload)
                    assert unknown.status_code==503 and unknown.json()['state']=='unknown'
                    committed=client.post(verify,json={'operation_id':shared_id}).json()
                    assert committed['status']=='COMMITTED'
                    assert client.post(submit,json=payload).json()==committed
                    voided=str(uuid4())
                    late=command(voided)
                    assert client.post(verify,json={'operation_id':voided}).json()['status']=='ABORTED_VOID'
                    assert client.post(submit,json=late).status_code==409
                    rolled=command(str(uuid4()))
                    faults.commit_fault='before'
                    assert client.post(submit,json=rolled).json()['state']=='unknown'
                    assert client.post(verify,json={'operation_id':rolled['operation_id']}).json()['status']=='ABORTED_VOID'
                    raced=command(str(uuid4()))
                    with ThreadPoolExecutor(2) as workers:
                        answers=list(workers.map(lambda _:client.post(submit,json=raced),range(2)))
                    assert [r.status_code for r in answers]==[200,200]
                    assert answers[0].json()==answers[1].json()
                    assert client.post(verify,json={'operation_id':raced['operation_id']}).json()==answers[0].json()
    finally:
        access.close()
