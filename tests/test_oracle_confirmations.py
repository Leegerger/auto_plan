"""Public API confirmation revision/concurrency checks in real PLAN_TEST AP_* storage."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from auto_plan.oracle_access import OracleAccessStore
from tests.support import CompanyAuthentication

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in real PLAN_TEST required')]


def test_confirmation_revision_atomic_concurrency_and_frozen_history_survive_reopen():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    suffix = uuid.uuid4().hex[:12].upper()
    recipe, model = 'T28R'+suffix, 'T28M'+suffix
    headers = {'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'}
    try:
        with TestClient(create_app(settings, store=access, authenticator=CompanyAuthentication()), base_url=settings.public_origin) as client:
            client.headers.update(headers)
            assert client.post('/auto_plan/api/auth/login', json={'username':'T24ADMIN','password':'company-secret'}).status_code == 200
            assert client.put('/auto_plan/api/admin/authorizations/T28EDITOR', json={'roles':['editor']}).status_code == 200
            assert client.post('/auto_plan/api/auth/login', json={'username':'T28EDITOR','password':'company-secret'}).status_code == 200
            current = client.get('/auto_plan/api/inputs').json()
            row = {'model_code':model,'week':'2026-10-19','kind':'material','batch_id':'MAT','status':'allowed','date':'2026-10-14','quantity':300,'note':'T28 test'}
            seed = {'base_version': current['input_version'], 'changes': {'recipes':[{'code':recipe,'name':'确认测试工艺','active':True}], 'models':[{'code':model,'name':'确认测试机种','active':True,'recipe_code':recipe}], 'confirmations':[row]}}
            first = client.post('/auto_plan/api/inputs/apply', json=seed)
            assert first.status_code == 200, first.text
            original = first.json()
            payloads = [{'base_version':original['input_version'],'changes':{'confirmations':[row|{'quantity':value}]}} for value in (128,129)]
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(lambda payload: client.post('/auto_plan/api/inputs/apply', json=payload), payloads))
            assert sorted(response.status_code for response in responses) == [200,409]
            saved = next(response.json() for response in responses if response.status_code == 200)
            matches = [r for r in saved['business']['confirmations'] if r['model_code'] == model]
            assert len(matches) == 1
            assert matches[0]['quantity'] in (128,129)
            invalid = {'base_version':saved['input_version'],'changes':{'confirmations':[row|{'quantity':0,'status':'forbidden'},row|{'batch_id':'OTHER','quantity':-1}]}}
            assert client.post('/auto_plan/api/inputs/apply', json=invalid).status_code == 422
            assert client.get(f"/auto_plan/api/inputs?version={saved['input_version']}").json() == saved
            assert client.get(f"/auto_plan/api/inputs?version={original['input_version']}").json() == original
        with TestClient(create_app(settings, store=access, authenticator=CompanyAuthentication()), base_url=settings.public_origin) as reopened:
            reopened.headers.update(headers)
            assert reopened.post('/auto_plan/api/auth/login', json={'username':'T28EDITOR','password':'company-secret'}).status_code == 200
            assert reopened.get(f"/auto_plan/api/inputs?version={saved['input_version']}").json() == saved
    finally:
        access.close()
