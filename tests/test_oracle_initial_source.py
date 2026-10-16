"""Actual PLAN_TEST source ownership, with one shared pool connection only."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime,timezone
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.oracle_demands import OracleDemandStore
from auto_plan.settings import Settings
from tests.oracle_namespace import disposable_business_namespace
from tests.support import CompanyAuthentication
from tests.test_initialization import initial_payload
from tests.test_initial_source import initial_workbook,maintained_inputs

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Fresh disposable PLAN_TEST only')]


def test_real_excel_releases_single_pool_connection_before_decode_and_keeps_owned_source(monkeypatch,tmp_path):
    import auto_plan.initialization_tables as tables
    settings=Settings(initial_admin_id='T24ADMIN',oracle_pool_min=1,oracle_pool_max=1)
    assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings);prefix='AP_TEST_'+uuid4().hex[:6].upper()
    metadata={'namespace':prefix,'pool_max':1}
    def objects():
        with access.pool.acquire() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT OBJECT_NAME FROM USER_OBJECTS WHERE SUBSTR(OBJECT_NAME,1,:n)=:prefix',n=len(prefix),prefix=prefix)
                return sorted(row[0] for row in cursor)
    try:
        assert objects()==[]
        with disposable_business_namespace(access.pool,prefix) as namespace:
            plans=OraclePlanStore(namespace);demands=OracleDemandStore(plans)
            app=create_app(settings,store=access,input_store=plans.input_store,plan_store=plans,demand_store=demands,
                authenticator=CompanyAuthentication(),clock=lambda:datetime(2026,10,14,tzinfo=timezone.utc))
            with TestClient(app,base_url=settings.public_origin) as client:
                client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert client.post('/auto_plan/api/auth/login',json={'username':'T24ADMIN','password':'company-secret'}).status_code==200
                assert client.put('/auto_plan/api/admin/authorizations/T29EDITOR',json={'roles':['editor']}).status_code==200
                assert client.post('/auto_plan/api/auth/logout').status_code==200
                assert client.post('/auto_plan/api/auth/login',json={'username':'T29EDITOR','password':'company-secret'}).status_code==200
                original=maintained_inputs(client);payload=initial_payload()|{'input_version':1};workbook=initial_workbook(client,payload)
                endpoint='/auto_plan/api/initialization/excel/preview?base_revision=0&input_version=1&latest_adoption=0'
                baseline=client.post(endpoint,content=workbook);assert baseline.status_code==200 and baseline.json()['valid']
                entered,release=Event(),Event();real=tables.excel_bundle
                def decoding(content,tokens):
                    entered.set();assert release.wait(5),'Preview retained the only Oracle connection during decode.'
                    return real(content,tokens)
                with monkeypatch.context() as patch,ThreadPoolExecutor(max_workers=1) as workers:
                    patch.setattr(tables,'excel_bundle',decoding)
                    future=workers.submit(client.post,endpoint,content=workbook)
                    try:
                        assert entered.wait(5)
                        # The single connection must be free while decoder is held.
                        models=deepcopy(original['masters']['models']);models[0]['name']='Concurrent Oracle edit'
                        changed=client.post('/auto_plan/api/inputs/apply',json={'base_version':1,'changes':{'models':models}})
                        assert changed.status_code==200 and changed.json()['input_version']==2
                    finally:release.set()
                    captured=future.result(timeout=10)
                assert captured.status_code==200,captured.text
                assert captured.json()==baseline.json()
                assert client.get('/auto_plan/api/inputs?version=1').json()==original
                assert client.post(endpoint,content=workbook).status_code==409
                stale=client.post('/auto_plan/api/initialization/apply',json=baseline.json()['payload']|{'operation_id':str(uuid4())})
                assert stale.status_code==409,stale.text
                head=client.get('/auto_plan/api/plans/current').json()
                assert head['revision']==head['latest_adoption']==0 and head['input_version']==2
                assert head['plan']['segments']==[] and head['context']['locks']==[]
                assert client.get('/auto_plan/api/demands/files').json()=={'input_version':2,'enablement_date':None,'files':[]}
                assert plans.check_consistency() and demands.check_consistency()
                (tmp_path/'captured.json').write_text(json.dumps(captured.json()))
                metadata['accepted_source']=1;metadata['current_input_version']=2;metadata['whole_body_baseline_equal']=True
                assert client.post('/auto_plan/api/auth/logout').status_code==200
        metadata['objects_after']=objects();assert metadata['objects_after']==[]
    finally:
        access.close();metadata['pool_closed']=True
        (tmp_path/'namespace-cleanup.json').write_text(json.dumps(metadata,indent=2))
