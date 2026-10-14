"""Opt-in public business API persistence checks against real PLAN_TEST AP_* objects."""
import os
import uuid
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from auto_plan.oracle_access import OracleAccessStore
from tests.support import CompanyAuthentication

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in real PLAN_TEST required')]


def test_oracle_atomic_inputs_survive_app_reopen_and_keep_old_version():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    headers = {'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'}
    suffix = uuid.uuid4().hex[:12].upper()
    recipe, model, line = 'T25R' + suffix, 'T25M' + suffix, 'T25L' + suffix
    try:
        with TestClient(create_app(settings, store=access, authenticator=CompanyAuthentication()), base_url=settings.public_origin) as client:
            assert client.post('/auto_plan/api/auth/login', json={'username': 'T24ADMIN', 'password': 'company-secret'}, headers=headers).status_code == 200
            assert client.put('/auto_plan/api/admin/authorizations/T25EDITOR', json={'roles': ['editor']}, headers=headers).status_code == 200
            assert client.post('/auto_plan/api/auth/login', json={'username': 'T25EDITOR', 'password': 'company-secret'}, headers=headers).status_code == 200
            before = client.get('/auto_plan/api/inputs').json()
            changes = {'recipes': [{'code': recipe, 'name': '测试工艺', 'active': True}], 'models': [{'code': model, 'name': '测试机种', 'active': True, 'recipe_code': recipe}], 'lines': [{'code': line, 'name': '测试线体', 'active': True}], 'capacities': [{'month': '2026-10', 'recipe_code': recipe, 'line_code': line, 'pieces': 2400}]}
            payload = {'base_version': before['input_version'], 'changes': changes}
            assert client.post('/auto_plan/api/inputs/preview', json=payload, headers=headers).json()['valid'] is True
            result = client.post('/auto_plan/api/inputs/apply', json=payload, headers=headers)
            assert result.status_code == 200
            saved_version = result.json()['input_version']
            invalid = {'base_version': saved_version, 'changes': {'capacities': [{'month': '2026-10', 'recipe_code': recipe, 'line_code': line, 'pieces': 2600}], 'line_yields': [{'month': '2026-10', 'percent': '0'}]}}
            assert client.post('/auto_plan/api/inputs/apply', json=invalid, headers=headers).status_code == 422
            persisted = client.get(f'/auto_plan/api/inputs?version={saved_version}').json()
            assert next(row['pieces'] for row in persisted['parameters']['capacities'] if row['recipe_code'] == recipe) == 2400
            assert client.get(f"/auto_plan/api/inputs?version={before['input_version']}").json() == before
        with TestClient(create_app(settings, store=access, authenticator=CompanyAuthentication()), base_url=settings.public_origin) as reopened:
            assert reopened.post('/auto_plan/api/auth/login', json={'username': 'T25EDITOR', 'password': 'company-secret'}, headers=headers).status_code == 200
            assert reopened.get(f'/auto_plan/api/inputs?version={saved_version}').json() == persisted
    finally:
        access.close()
