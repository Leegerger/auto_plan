"""Real PLAN_TEST evidence, limited to disposable AP_TEST_* business tables."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4
import oracledb
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

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Opt-in PLAN_TEST AP_* only')]
NOW=datetime(2026,10,14,tzinfo=timezone.utc)


def test_real_initial_bundle_rolls_back_or_commits_together_and_demand_replacements_survive_reopen():
    settings=Settings(initial_admin_id='T24ADMIN');assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool,'AP_TEST_'+uuid4().hex[:6].upper()) as namespace:
            faults=FaultPool(namespace);plans=OraclePlanStore(faults);demands=OracleDemandStore(plans);inputs=OracleInputStore(namespace)
            def app():return create_app(settings,store=access,input_store=inputs,plan_store=plans,demand_store=demands,authenticator=CompanyAuthentication(),clock=lambda:NOW)
            with TestClient(app(),base_url=settings.public_origin) as client:
                client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert client.post('/auto_plan/api/auth/login',json={'username':'T24ADMIN','password':'company-secret'}).status_code==200
                assert client.put('/auto_plan/api/admin/authorizations/T26EDITOR',json={'roles':['editor']}).status_code==200
                assert client.post('/auto_plan/api/auth/login',json={'username':'T26EDITOR','password':'company-secret'}).status_code==200
                failed=initial_payload()|{'operation_id':str(uuid4())}
                faults.commit_fault='before'
                response=client.post('/auto_plan/api/initialization/apply',json=failed)
                assert response.status_code==503 and response.json()['state']=='unknown',response.text
                assert client.get('/auto_plan/api/plans/current').json()['revision']==0
                assert client.get('/auto_plan/api/inputs?version=0').json()['masters']['models']==[]
                assert client.get('/auto_plan/api/demands/files').json()['files']==[]
                assert client.post('/auto_plan/api/plans/verify',json={'operation_id':failed['operation_id']}).json()['status']=='ABORTED_VOID'
                payload=initial_payload()|{'operation_id':str(uuid4())}
                faults.commit_fault='after'
                response=client.post('/auto_plan/api/initialization/apply',json=payload)
                assert response.status_code==503 and response.json()['state']=='unknown',response.text
                saved=client.post('/auto_plan/api/plans/verify',json={'operation_id':payload['operation_id']}).json()
                assert (saved['revision'],saved['input_version'],saved['latest_adoption'],saved['pending'])==(1,1,0,True)
                assert client.post('/auto_plan/api/initialization/apply',json=payload).json()==saved
                current=client.get('/auto_plan/api/plans/current').json()
                assert current['plan']['segments'][0]['quantity']==1400
                assert current['lineage']['external_reset']['already_applied'] is True
                assert len(client.get('/auto_plan/api/demands/files').json()['files'][0]['rows'])==15
                # Invalid later data leaves file, input epoch and plan unchanged.
                invalid={'input_version':1,'rows':[{'model_code':'M','week':'2026-10-12','delta':-100},{'model_code':'M','week':'2026-10-12','delta':200}],'operation_id':str(uuid4())}
                assert client.post('/auto_plan/api/demands/apply',json=invalid).status_code==422
                assert client.get('/auto_plan/api/plans/current').json()['input_version']==1
                body={'input_version':1,'rows':[{'model_code':'M','week':'2026-10-12','delta':200}]*2,'operation_id':str(uuid4())}
                faults.commit_fault='after'
                unknown=client.post('/auto_plan/api/demands/apply',json=body)
                assert unknown.status_code==503 and unknown.json()['state']=='unknown'
                committed=client.post('/auto_plan/api/demands/verify',json={'operation_id':body['operation_id']}).json()
                assert committed['status']=='COMMITTED' and committed['input_version']==2
                assert client.post('/auto_plan/api/demands/apply',json=body).json()==committed
                before=client.get('/auto_plan/api/demands/files').json()
                assert len(before['files'])==2 and before['files'][-1]['effective']
                assert len({r['row_id'] for r in before['files'][-1]['rows']})==2
                # Same guarded epoch gives one winner and one stale-input conflict.
                first=body|{'input_version':2,'operation_id':str(uuid4())};second=first|{'operation_id':str(uuid4())}
                with ThreadPoolExecutor(2) as pool:
                    responses=list(pool.map(lambda b:client.post('/auto_plan/api/demands/apply',json=b),[first,second]))
                assert sorted(r.status_code for r in responses)==[200,409],[(r.status_code,r.text) for r in responses]
                saved_files=client.get('/auto_plan/api/demands/files').json()
                assert client.get('/auto_plan/api/plans/current').json()['revision']==1
                old=client.get('/auto_plan/api/inputs?version=1').json()
                assert old['business']['demands'][0]['quantity']==1000
                assert plans.check_consistency() and demands.check_consistency()
                # Oracle immutability is a database boundary, not a memory claim.
                with namespace.acquire() as connection:
                    with connection.cursor() as cursor:
                        with pytest.raises(oracledb.DatabaseError) as error:cursor.execute('DELETE FROM AP_DEMAND_FILE')
                        assert error.value.args[0].code==20001
                    connection.rollback()
            with TestClient(app(),base_url=settings.public_origin) as reopened:
                reopened.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert reopened.post('/auto_plan/api/auth/login',json={'username':'T26EDITOR','password':'company-secret'}).status_code==200
                assert reopened.get('/auto_plan/api/demands/files').json()==saved_files
                assert reopened.get('/auto_plan/api/inputs?version=1').json()==old
                assert reopened.get('/auto_plan/api/plans/current').json()['input_version']==3
    finally:access.close()
