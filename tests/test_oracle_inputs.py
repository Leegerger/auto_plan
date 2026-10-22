"""Opt-in public business API persistence checks against real PLAN_TEST AP_* objects."""
import os
import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.input_models import InputSnapshot
from auto_plan.settings import Settings
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_inputs import OracleInputStore
from tests.oracle_legacy import seed_legacy_input_revision
from tests.oracle_namespace import disposable_business_namespace
from tests.support import CompanyAuthentication

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in real PLAN_TEST required')]
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


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
            invalid = {'base_version': saved_version, 'changes': {'capacities': [{'month': '2026-10', 'recipe_code': recipe, 'line_code': line, 'pieces': 2600}], 'line_yields': [{'month': '2026-10', 'model_code': model, 'percent': '0'}]}}
            assert client.post('/auto_plan/api/inputs/apply', json=invalid, headers=headers).status_code == 422
            persisted = client.get(f'/auto_plan/api/inputs?version={saved_version}').json()
            assert next(row['pieces'] for row in persisted['parameters']['capacities'] if row['recipe_code'] == recipe) == 2400
            assert client.get(f"/auto_plan/api/inputs?version={before['input_version']}").json() == before
        with TestClient(create_app(settings, store=access, authenticator=CompanyAuthentication()), base_url=settings.public_origin) as reopened:
            assert reopened.post('/auto_plan/api/auth/login', json={'username': 'T25EDITOR', 'password': 'company-secret'}, headers=headers).status_code == 200
            assert reopened.get(f'/auto_plan/api/inputs?version={saved_version}').json() == persisted
    finally:
        access.close()


def test_oracle_legacy_line_yields_convert_once_and_survive_reopen():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    headers = {'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'}
    try:
        with disposable_business_namespace(
                access.pool, 'AP_TEST_' + uuid.uuid4().hex[:6].upper()) as namespace:
            store = OracleInputStore(namespace)
            legacy = InputSnapshot.model_validate({
                'schema_version': 1,
                'input_version': 1,
                'masters': {
                    'recipes': [{'code': 'R', 'name': '工艺', 'active': True}],
                    'lines': [{'code': 'L', 'name': '一线', 'active': True}],
                    'models': [
                        {'code': 'A', 'name': '机种 A', 'recipe_code': 'R', 'active': True},
                        {'code': 'B', 'name': '机种 B', 'recipe_code': 'R', 'active': True},
                        {'code': 'C', 'name': '停用保留', 'recipe_code': 'R', 'active': False},
                    ],
                },
                'parameters': {'line_yields': [{'month': '2026-10', 'percent': '95'}]},
            })
            seed_legacy_input_revision(store, legacy, NOW)

            def app():
                return create_app(settings, store=access, input_store=store,
                                  authenticator=CompanyAuthentication(), clock=lambda: NOW)

            with TestClient(app(), base_url=settings.public_origin) as client:
                assert client.post('/auto_plan/api/auth/login',
                                   json={'username': 'T24ADMIN', 'password': 'company-secret'},
                                   headers=headers).status_code == 200
                assert client.put('/auto_plan/api/admin/authorizations/T29EDITOR',
                                  json={'roles': ['editor']}, headers=headers).status_code == 200
                assert client.post('/auto_plan/api/auth/login',
                                   json={'username': 'T29EDITOR', 'password': 'company-secret'},
                                   headers=headers).status_code == 200
                converted = client.get('/auto_plan/api/inputs', headers=headers)
                assert converted.status_code == 200, converted.text
                converted = converted.json()
                assert converted['schema_version'] == 1 and converted['input_version'] == 2
                assert {(row['month'], row['model_code']) for row in converted['parameters']['line_yields']
                        if row['month'] == '2026-10'} == {
                            ('2026-10', 'A'), ('2026-10', 'B'), ('2026-10', 'C')}
                assert client.get('/auto_plan/api/inputs?version=1', headers=headers).json() == legacy.model_dump(mode='json')
                assert client.get('/auto_plan/api/inputs', headers=headers).json() == converted
                edited = client.post('/auto_plan/api/inputs/apply', headers=headers, json={
                    'base_version': converted['input_version'],
                    'changes': {
                        'models': [{'code': 'D', 'name': '新机种', 'recipe_code': 'R', 'active': True}],
                        'line_yields': [{'month': '2026-10', 'model_code': 'A', 'percent': '96'}],
                    },
                })
                assert edited.status_code == 200, edited.text
                persisted = client.get('/auto_plan/api/inputs', headers=headers).json()
                persisted_values = {(row['month'], row['model_code']): row['percent']
                                    for row in persisted['parameters']['line_yields']}
                assert persisted_values['2026-10', 'A'] == '96'
                assert persisted_values['2026-10', 'B'] == '95'
                assert ('2026-10', 'D') not in persisted_values
                assert store.check_consistency() is True

            with TestClient(app(), base_url=settings.public_origin) as reopened:
                assert reopened.post('/auto_plan/api/auth/login',
                                     json={'username': 'T29EDITOR', 'password': 'company-secret'},
                                     headers=headers).status_code == 200
                assert reopened.get('/auto_plan/api/inputs', headers=headers).json() == persisted
                assert reopened.get('/auto_plan/api/inputs?version=1', headers=headers).json() == legacy.model_dump(mode='json')
                assert store.check_consistency() is True
    finally:
        access.close()
