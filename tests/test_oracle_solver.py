"""Frozen shared candidate and replay checks in fresh PLAN_TEST AP_TEST tables."""
import os
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.oracle_jobs import OracleJobStore
from auto_plan.job_process import JobController
from tests.job_http_support import calculate
from tests.oracle_namespace import disposable_business_namespace
from tests.support import CompanyAuthentication
from tests.test_initialization import initial_payload
from tests.line_state_support import confirm_states

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in PLAN_TEST AP_* only')]
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def test_real_shared_freeze_candidate_patch_and_changed_context_guard():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool, 'AP_TEST_' + uuid4().hex[:6].upper()) as namespace:
            plans, inputs = OraclePlanStore(namespace), OracleInputStore(namespace)
            jobs=OracleJobStore(namespace,worker_limit=1)
            controller=JobController(jobs,workers=1,clock=lambda:NOW)
            app = create_app(settings, store=access, plan_store=plans, input_store=inputs, job_store=jobs,job_controller=controller,
                authenticator=CompanyAuthentication(), clock=lambda: NOW)
            with TestClient(app, base_url=settings.public_origin) as client:
                client.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T24ADMIN', 'password': 'company-secret'}).status_code == 200
                assert client.put('/auto_plan/api/admin/authorizations/T36EDITOR', json={'roles': ['editor']}).status_code == 200
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T36EDITOR', 'password': 'company-secret'}).status_code == 200
                initial = initial_payload()
                initial['segments'] = []
                initial['demands'][0]['quantity'] = 5
                initial['reset']['models'][0]['net_balance'] = 0
                seeded = client.post('/auto_plan/api/initialization/apply', json=initial | {'operation_id': str(uuid4())})
                assert seeded.status_code == 200, seeded.text
                def head():
                    response = client.get('/auto_plan/api/plans/current')
                    assert response.status_code == 200, response.text
                    return response.json()
                def guard():
                    current = head()
                    return {name: current[name] for name in ('input_version', 'latest_adoption')} | {'base_revision': current['revision']}
                confirmed = client.post('/auto_plan/api/plans/save', json=guard() | {
                    'operation_id':str(uuid4()),'pending':True,'operations':[confirm_states()]})
                assert confirmed.status_code == 200, confirmed.text
                source = guard()
                response = calculate(client, source | {
                    'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L']}}})
                assert response.status_code == 200, response.text
                candidate = response.json()['candidates'][0]
                assert candidate['objective'][:30] == ['0'] * 30
                assert candidate['input_version'] == source['input_version']
                assert head()['revision'] == source['base_revision'] and head()['latest_adoption'] == 0
                # A patch replays on the server; it is not persisted by preview.
                saved = client.post('/auto_plan/api/plans/save', json=source | {
                    'operation_id': str(uuid4()), 'pending': False, 'operations': candidate['server_reference']['operations']})
                assert saved.status_code == 200, saved.text
                current = head()
                assert current['revision'] == source['base_revision'] + 1 and current['latest_adoption'] == 0
                assert current['plan'] == candidate['plan']
                locked = client.post('/auto_plan/api/plans/save', json=guard() | {'operation_id': str(uuid4()), 'pending': False,
                    'operations': [{'kind': 'lock_cells', 'cells': [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 5}]}]})
                assert locked.status_code == 200, locked.text
                stale = client.post('/auto_plan/api/plans/preview', json=source | {'operations': candidate['server_reference']['operations']})
                assert stale.status_code == 409 and stale.json()['input_version'] > source['input_version']
                assert plans.check_consistency()
            controller.shutdown()
    finally:
        access.close()
