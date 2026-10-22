"""Selected immutable history in a disposable real PLAN_TEST namespace."""
import os
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
import oracledb
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_plans import OraclePlanStore
from tests.oracle_namespace import disposable_business_namespace
from tests.support import CompanyAuthentication
from tests.test_plan_preview import snapshot
from tests.test_plan_history import workbook, records

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Opt-in PLAN_TEST only')]
NOW=datetime(2026,10,14,tzinfo=timezone.utc)


def test_real_selected_history_recovery_boundary_export_restart_and_immutable_audit():
    settings=Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    moment=[NOW-timedelta(days=30)]
    try:
        with disposable_business_namespace(access.pool,'AP_TEST_'+uuid4().hex[:6].upper()) as namespace:
            inputs=OracleInputStore(namespace);plans=OraclePlanStore(namespace)
            def app():
                return create_app(settings,store=access,input_store=inputs,plan_store=plans,
                                  authenticator=CompanyAuthentication(),clock=lambda:moment[0])
            def login(client,actor):
                client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                response=client.post('/auto_plan/api/auth/login',json={'username':actor,'password':'company-secret'})
                assert response.status_code==200,response.text
            with TestClient(app(),base_url=settings.public_origin) as client:
                login(client,'T24ADMIN')
                assert client.put('/auto_plan/api/admin/authorizations/T34EDITOR',json={'roles':['editor']}).status_code==200
                assert client.put('/auto_plan/api/admin/authorizations/T34VIEWER',json={'roles':['viewer']}).status_code==200
                login(client,'T34EDITOR')
                changes={key:value for section in snapshot()['inputs'].values() for key,value in section.items()}
                seeded=client.post('/auto_plan/api/inputs/apply',json={'base_version':0,'changes':changes})
                assert seeded.status_code==200,seeded.text
                seeded_input=client.get('/auto_plan/api/inputs?version=1').json()
                assert seeded_input['parameters']['line_yields']==[
                    {'month':'2026-10','model_code':m,'percent':'100'} for m in ('M','N','P')]
                def head():
                    response=client.get('/auto_plan/api/plans/current');assert response.status_code==200,response.text
                    return response.json()
                def command(*operations):
                    current=head()
                    return {'base_revision':current['revision'],'input_version':current['input_version'],
                            'latest_adoption':current['latest_adoption'],'operation_id':str(uuid4()),'pending':True,'operations':list(operations)}
                def post(kind,body):
                    if kind=='preview':body={key:value for key,value in body.items() if key not in {'operation_id','pending'}}
                    return client.post('/auto_plan/api/plans/'+kind,json=body)
                row={'id':'a','date':'2026-10-15','line_code':'L','model_code':'M','quantity':100,'order':0,'hours':'60001/20000'}
                assert post('save',command({'kind':'patch_plan','segments':[row]})).status_code==200
                adopted=post('adopt',command())
                assert adopted.status_code==200,adopted.text
                original=client.get('/auto_plan/api/plans/adoptions/1').json()
                assert post('save',command({'kind':'patch_plan','segments':[row|{'quantity':125}]})).status_code==200
                moment[0]=NOW;login(client,'T34EDITOR')
                listing=client.get('/auto_plan/api/plans/history?limit=2').json()
                assert [(r['revision'],r['adoption']) for r in listing['items']]==[(3,None),(2,1)]
                assert listing['next_before']==2
                assert len(listing['items'][0]['operation_id'])==36
                restore={'kind':'restore_history','source':{'kind':'revision','id':1}}
                request=command(restore)
                assert post('preview',request).status_code==200
                saved=post('save',request)
                assert saved.status_code==200,saved.text
                assert (saved.json()['revision'],saved.json()['latest_adoption'])==(4,1)
                assert post('save',request).json()==saved.json()
                moment[0]=NOW+timedelta(microseconds=1)
                expired=post('preview',command(restore))
                assert expired.status_code==410,expired.text
                permanent={'kind':'restore_history','source':{'kind':'adoption','id':1}}
                assert post('preview',command(permanent)).status_code==200
                changed=client.post('/auto_plan/api/inputs/apply',json={'base_version':1,'changes':{'capacities':[{'month':'2026-10','recipe_code':'R','line_code':'L','pieces':2000}],'line_yields':[{'month':'2026-10','model_code':'M','percent':'94'}]}})
                assert changed.status_code==200,changed.text
                assert client.get('/auto_plan/api/inputs?version=1').json()==seeded_input
                assert client.get('/auto_plan/api/plans/adoptions/1').json()==original
                book=workbook(client.get('/auto_plan/api/plans/adoptions/1/export.xlsx'))
                assert records(book,'production')[0]['hours_exact']=='60001/20000'
                raw=''.join(row['json_chunk'] for row in records(book,'_record_json'))
                assert json.loads(raw)==original
                assert post('save',command({'kind':'lock_cells','cells':[{'date':'2026-10-15','line_code':'L','model_code':'M','quantity':100}]})).status_code==200
                blocked=post('preview',command({'kind':'restore_history','source':{'kind':'revision','id':3}}))
                # Expired references are rejected before historical content can bypass a lock.
                assert blocked.status_code==410,blocked.text
                assert plans.check_consistency() is True
                expected=head()
                login(client,'T34VIEWER')
                assert client.get('/auto_plan/api/plans/adoptions/1/export.xlsx').status_code==200
                assert post('preview',command(permanent)).status_code==403
            # Verify physical permanence and triggers directly at the agreed
            # real-persistence seam, never weaken or delete canonical records.
            with namespace.acquire() as connection:
                with connection.cursor() as cursor:
                    for table in ('AP_PLAN_ADOPTION','AP_PLAN_AUDIT','AP_PLAN_REVISION','AP_PLAN_SNAPSHOT','AP_PLAN_EFFECT'):
                        with pytest.raises(oracledb.DatabaseError) as rejected:
                            cursor.execute('DELETE FROM '+table)
                        assert rejected.value.args[0].code==20029
                    connection.rollback()
            with TestClient(app(),base_url=settings.public_origin) as reopened:
                login(reopened,'T34EDITOR')
                assert reopened.get('/auto_plan/api/plans/current').json()==expected
                assert reopened.get('/auto_plan/api/plans/adoptions/1').json()==original
                assert reopened.get('/auto_plan/api/inputs?version=1').json()==seeded_input
                assert plans.check_consistency() is True
    finally:
        access.close()
