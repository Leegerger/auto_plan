"""Opt-in tests at the public API using real Oracle, never a DB substitute.

Require AUTO_PLAN_ORACLE_USER=PLAN_TEST and AUTO_PLAN_TEST_ORACLE=1.
Apply migrations/001_access.sql explicitly first. No production schemas accepted.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.settings import Settings
from tests.support import CompanyAuthentication

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv("AUTO_PLAN_TEST_ORACLE") != "1", reason="Opt-in real PLAN_TEST credentials required")]


@pytest.fixture
def oracle_scenario():
    settings = Settings(initial_admin_id="T24ADMIN")
    assert settings.oracle_user.upper() == "PLAN_TEST", "Refusing non-test Oracle schema"
    store = OracleAccessStore.connect(settings)
    now = [datetime(2026, 10, 14, 0, tzinfo=timezone.utc)]
    application = create_app(settings, store=store, authenticator=CompanyAuthentication(), clock=lambda: now[0])
    headers = {"Origin": settings.public_origin, "X-Auto-Plan-Request": "1"}
    with TestClient(application, base_url=settings.public_origin) as admin:
        assert admin.post('/auto_plan/api/auth/login', json={"username": "T24ADMIN", "password": "company-secret"}, headers=headers).status_code == 200
        yield admin, store, now, headers, settings
    store.close()


def test_oracle_persists_authorization_and_expires_idle_session_at_exact_boundary(oracle_scenario):
    admin, store, now, headers, settings = oracle_scenario
    assert admin.put('/auto_plan/api/admin/authorizations/T24VIEWER', json={"roles": ["viewer"]}, headers=headers).status_code == 200
    viewer = TestClient(admin.app, base_url=settings.public_origin)
    assert viewer.post('/auto_plan/api/auth/login', json={"username": " t24viewer ", "password": "company-secret"}, headers=headers).json() == {"user_id": "T24VIEWER", "roles": ["viewer"]}
    now[0] += timedelta(hours=1, minutes=59)
    assert viewer.get('/auto_plan/api/auth/me').status_code == 200
    now[0] += timedelta(hours=2)
    assert viewer.get('/auto_plan/api/auth/me').status_code == 401
    viewer.close()


def test_oracle_revocation_next_request_and_last_administrator_transaction(oracle_scenario):
    admin, store, now, headers, settings = oracle_scenario
    assert admin.put('/auto_plan/api/admin/authorizations/T24ADMIN', json={"roles": []}, headers=headers).status_code == 409
    assert admin.put('/auto_plan/api/admin/authorizations/T24VIEWER', json={"roles": ["viewer"]}, headers=headers).status_code == 200
    viewer = TestClient(admin.app, base_url=settings.public_origin)
    assert viewer.post('/auto_plan/api/auth/login', json={"username": "T24VIEWER", "password": "company-secret"}, headers=headers).status_code == 200
    assert admin.put('/auto_plan/api/admin/authorizations/T24VIEWER', json={"roles": [], "active": False}, headers=headers).status_code == 200
    assert viewer.get('/auto_plan/api/auth/me').status_code == 401
    assert viewer.post('/auto_plan/api/auth/login', json={"username": "T24VIEWER", "password": "company-secret"}, headers=headers).status_code == 403
    assert any(row['action'] == 'authorize' and row['user_id'] == 'T24VIEWER' for row in admin.get('/auto_plan/api/admin/audit').json())
    viewer.close()


def test_oracle_serializes_competing_last_administrator_removal(oracle_scenario):
    admin, store, now, headers, settings = oracle_scenario
    assert admin.put('/auto_plan/api/admin/authorizations/T24ADMIN2', json={"roles": ["administrator"]}, headers=headers).status_code == 200
    other = TestClient(admin.app, base_url=settings.public_origin)
    assert other.post('/auto_plan/api/auth/login', json={"username": "T24ADMIN2", "password": "company-secret"}, headers=headers).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = [executor.submit(client.put, f'/auto_plan/api/admin/authorizations/{user}', json={"roles": ["viewer"]}, headers=headers) for client, user in [(admin, "T24ADMIN"), (other, "T24ADMIN2")]]
        assert sorted(f.result().status_code for f in requests) == [200, 409]
    survivor = admin if "administrator" in admin.get('/auto_plan/api/auth/me').json()['roles'] else other
    assert survivor.put('/auto_plan/api/admin/authorizations/T24ADMIN', json={"roles": ["administrator"]}, headers=headers).status_code == 200
    assert admin.put('/auto_plan/api/admin/authorizations/T24ADMIN2', json={"roles": [], "active": False}, headers=headers).status_code == 200
    other.close()


def test_oracle_restart_invalidates_session_and_keeps_grants(oracle_scenario):
    admin, store, now, headers, settings = oracle_scenario
    assert admin.put('/auto_plan/api/admin/authorizations/T24KEPT', json={"roles": ["editor"]}, headers=headers).status_code == 200
    old_cookie = admin.cookies.get('auto_plan_session')
    with TestClient(create_app(settings, store=store, authenticator=CompanyAuthentication()), base_url=settings.public_origin) as restarted:
        restarted.cookies.set('auto_plan_session', old_cookie, domain='fsscm.cminl.oa', path='/auto_plan')
        assert restarted.get('/auto_plan/api/auth/me').status_code == 401
        assert restarted.post('/auto_plan/api/auth/login', json={"username": "T24KEPT", "password": "company-secret"}, headers=headers).json() == {"user_id": "T24KEPT", "roles": ["editor"]}


def test_oracle_absolute_lifetime_cannot_be_extended_by_activity(oracle_scenario):
    admin, store, now, headers, settings = oracle_scenario
    for hour in range(7):
        now[0] += timedelta(hours=1)
        assert admin.get('/auto_plan/api/auth/me').status_code == 200
    now[0] += timedelta(hours=1)
    assert admin.get('/auto_plan/api/auth/me').status_code == 401
