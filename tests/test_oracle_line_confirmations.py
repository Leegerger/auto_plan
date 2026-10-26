"""Real PLAN_TEST state evidence, atomic corrections and historical snapshots."""
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

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in disposable PLAN_TEST objects only')]
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def test_real_line_evidence_correction_race_retries_unknown_commit_and_reopen():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool, 'AP_TEST_' + uuid4().hex[:6].upper()) as namespace:
            faults = FaultPool(namespace)
            inputs = OracleInputStore(namespace)
            plans = OraclePlanStore(faults)
            def app():
                return create_app(settings, store=access, input_store=inputs, plan_store=OraclePlanStore(faults), authenticator=CompanyAuthentication(), clock=lambda: NOW)
            with TestClient(app(), base_url=settings.public_origin) as client:
                client.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T24ADMIN', 'password': 'company-secret'}).status_code == 200
                for name, role in [('T33EDITOR', 'editor'), ('T33VIEWER', 'viewer')]:
                    assert client.put('/auto_plan/api/admin/authorizations/' + name, json={'roles': [role]}).status_code == 200
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
                data = snapshot()['inputs']
                assert client.post('/auto_plan/api/inputs/apply', json={'base_version': 0, 'changes': {key: value for section in data.values() for key, value in section.items()}}).status_code == 200
                def post(path, payload):
                    return client.post('/auto_plan/api/plans/' + path, json=payload)
                def head():
                    response = client.get('/auto_plan/api/plans/current')
                    assert response.status_code == 200, response.text
                    return response.json()
                def command(*operations):
                    current = head()
                    return {'base_revision': current['revision'], 'input_version': current['input_version'], 'latest_adoption': current['latest_adoption'],
                            'operation_id': str(uuid4()), 'pending': True, 'operations': list(operations)}
                def confirm(source='现场点检', expected=None):
                    return {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-12T08:00:00+08:00',
                            'recipe_code': 'R', 'source': source, 'expected_record_id': expected}], 'reason': '点检记录更正' if expected else None}
                production = {'kind': 'patch_plan', 'segments': [{'id': 'future-r', 'date': '2026-10-15', 'line_code': 'L', 'order': 0, 'model_code': 'M', 'quantity': 100, 'hours': '3'}]}
                before = head()
                assert post('adopt', command(production)).status_code == 422
                pending = post('save', command(production))
                assert pending.status_code == 200, pending.text
                unresolved = head()
                checked = post('preview', {key: value for key, value in command(confirm()).items() if key not in ('operation_id', 'pending')})
                assert checked.status_code == 200, checked.text
                assert checked.json()['evaluation']['executable'] is True
                assert head() == unresolved
                adopted = post('adopt', command(confirm()))
                assert adopted.status_code == 200, adopted.text
                original = client.get('/auto_plan/api/plans/adoptions/1').json()
                confirmed = head()
                assert confirmed['input_version'] == before['input_version'] + 1
                record = confirmed['context']['line_states']['records'][0]
                assert (record['actor'], record['source'], record['recipe_code']) == ('T33EDITOR', '现场点检', 'R')
                assert len(confirmed['context']['line_states']['audit']) == 1
                first = command(confirm('班组甲复核', record['id']))
                second = first | {'operation_id': str(uuid4()), 'operations': [confirm('班组乙复核', record['id'])]}
                with ThreadPoolExecutor(2) as pool:
                    results = list(pool.map(lambda payload: post('save', payload), [first, second]))
                assert sorted(response.status_code for response in results) == [200, 409]
                winning = first if results[0].status_code == 200 else second
                result = next(response.json() for response in results if response.status_code == 200)
                assert post('save', winning).json() == result
                assert post('verify', {'operation_id': winning['operation_id']}).json() == result
                current = head()
                assert len(current['context']['line_states']['audit']) == 2
                assert client.get('/auto_plan/api/plans/adoptions/1').json() == original
                corrected = current['context']['line_states']['records'][0]
                unknown_body = command(confirm('交接最终复核', corrected['id']))
                faults.commit_fault = 'after'
                unknown = post('save', unknown_body)
                assert unknown.status_code == 503 and unknown.json()['state'] == 'unknown', unknown.text
                verified = post('verify', {'operation_id': unknown_body['operation_id']})
                assert verified.status_code == 200, verified.text
                assert verified.json()['status'] == 'COMMITTED'
                assert post('save', unknown_body).json() == verified.json()
                expected = head()
                assert len(expected['context']['line_states']['audit']) == 3
                assert expected['context']['line_states']['records'][0]['source'] == '交接最终复核'
                assert client.get('/auto_plan/api/plans/adoptions/1').json() == original
                assert plans.check_consistency() is True
            with TestClient(app(), base_url=settings.public_origin) as reopened:
                reopened.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert reopened.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
                assert reopened.get('/auto_plan/api/plans/current').json() == expected
                assert reopened.get('/auto_plan/api/plans/adoptions/1').json() == original
                assert reopened.post('/auto_plan/api/auth/login', json={'username': 'T33VIEWER', 'password': 'company-secret'}).status_code == 200
                assert reopened.get('/auto_plan/api/plans/current').json()['context']['line_states'] == expected['context']['line_states']
                forbidden = {'base_revision': expected['revision'], 'input_version': expected['input_version'], 'latest_adoption': expected['latest_adoption'],
                             'operations': [confirm()], 'operation_id': str(uuid4()), 'pending': True}
                for path in ('preview', 'save', 'adopt'):
                    body = forbidden if path != 'preview' else {key: value for key, value in forbidden.items() if key not in ('operation_id', 'pending')}
                    assert reopened.post('/auto_plan/api/plans/' + path, json=body).status_code == 403
    finally:
        access.close()
