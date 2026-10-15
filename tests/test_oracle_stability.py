"""Real PLAN_TEST context guard and key evidence in fresh AP_TEST_* tables only."""
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

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Opt-in PLAN_TEST AP_* only')]
NOW=datetime(2026,10,14,tzinfo=timezone.utc)


def test_real_lock_preferences_context_and_revision_commit_or_rollback_together():
    settings=Settings(initial_admin_id='T24ADMIN');assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool,'AP_TEST_'+uuid4().hex[:6].upper()) as namespace:
            faults=FaultPool(namespace);plans=OraclePlanStore(faults);inputs=OracleInputStore(namespace)
            def app():return create_app(settings,store=access,input_store=inputs,plan_store=plans,authenticator=CompanyAuthentication(),clock=lambda:NOW)
            with TestClient(app(),base_url=settings.public_origin) as client:
                client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert client.post('/auto_plan/api/auth/login',json={'username':'T24ADMIN','password':'company-secret'}).status_code==200
                assert client.put('/auto_plan/api/admin/authorizations/T32EDITOR',json={'roles':['editor']}).status_code==200
                assert client.post('/auto_plan/api/auth/login',json={'username':'T32EDITOR','password':'company-secret'}).status_code==200
                data=snapshot()['inputs'];changes={key:value for section in data.values() for key,value in section.items()}
                seeded=client.post('/auto_plan/api/inputs/apply',json={'base_version':0,'changes':changes})
                assert seeded.status_code==200,seeded.text
                def head():
                    response=client.get('/auto_plan/api/plans/current');assert response.status_code==200,response.text
                    return response.json()
                def body(operations):
                    current=head()
                    return {'base_revision':current['revision'],'input_version':current['input_version'],'latest_adoption':current['latest_adoption'],'operation_id':str(uuid4()),'pending':True,'operations':operations}
                def post(path,payload):return client.post('/auto_plan/api/plans/'+path,json=payload)
                row={'id':'a','date':'2026-10-15','line_code':'L','model_code':'M','order':0,'quantity':100,'hours':'3'}
                assert post('save',body([{'kind':'patch_plan','segments':[row]}])).status_code==200
                locks=[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':100},
                       {'date':'2026-10-15','line_code':'L','model_code':'N','quantity':0}]
                action={'kind':'lock_cells','cells':locks}
                before=head();payload=body([action])
                faults.commit_fault='before'
                failed=post('save',payload)
                assert failed.status_code==503 and failed.json()['state']=='unknown',failed.text
                assert head()==before
                assert post('verify',{'operation_id':payload['operation_id']}).json()['status']=='ABORTED_VOID'
                payload=body([action]);faults.commit_fault='after'
                unknown=post('save',payload)
                assert unknown.status_code==503 and unknown.json()['state']=='unknown',unknown.text
                saved=post('verify',{'operation_id':payload['operation_id']}).json()
                assert (saved['status'],saved['revision'],saved['input_version'])==('COMMITTED',2,2)
                assert post('save',payload).json()==saved
                current=head();assert current['context']['locks']==locks
                assert current['input_version']==2
                old=client.get('/auto_plan/api/plans/revisions/1').json()
                assert old['snapshot']['locks']==[]
                assert old['snapshot']['inputs']['input_version']==1
                assert client.get('/auto_plan/api/inputs?version=1').json()['input_version']==1
                stale=post('save',payload|{'operation_id':str(uuid4())})
                assert stale.status_code==409 and stale.json()['input_version']==2
                assert any(diff['path'][:2]==['context','locks'] for diff in stale.json()['input_diff'])
                # Both requests use the same complete guard; one context write wins.
                prefs={'kind':'set_preferences','preferences':{'near_weeks':15,'near_tolerance_percent':'20','far_tolerance_percent':'50','near_week_weight':'5','far_week_weight':'1'}}
                first=body([prefs]);second=body([{'kind':'unlock_cells','cells':[{'date':'2026-10-15','line_code':'L','model_code':'M'}]}])
                with ThreadPoolExecutor(2) as pool:
                    responses=list(pool.map(lambda request:post('save',request),[first,second]))
                assert sorted(r.status_code for r in responses)==[200,409],[(r.status_code,r.text) for r in responses]
                final=head();assert (final['revision'],final['input_version'])==(3,3)
                assert final['latest_adoption']==0
                assert plans.check_consistency() is True
                expected=final['context']
            with TestClient(app(),base_url=settings.public_origin) as reopened:
                reopened.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert reopened.post('/auto_plan/api/auth/login',json={'username':'T32EDITOR','password':'company-secret'}).status_code==200
                result=reopened.get('/auto_plan/api/plans/current').json()
                assert result['context']==expected
                assert result['input_version']==3
    finally:access.close()
