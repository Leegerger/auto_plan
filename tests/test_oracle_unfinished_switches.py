"""Actual remaining-switch state in disposable real PLAN_TEST storage."""
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
from tests.test_plan_preview import snapshot
from tests.support import CompanyAuthentication

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in disposable PLAN_TEST objects only')]
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def test_real_unfinished_job_atomic_progress_correction_history_restore_and_reopen():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool, 'AP_TEST_' + uuid4().hex[:6].upper()) as namespace:
            inputs = OracleInputStore(namespace)
            plans = OraclePlanStore(namespace)
            def app():
                return create_app(settings, store=access, input_store=inputs, plan_store=plans, authenticator=CompanyAuthentication(), clock=lambda: NOW)
            with TestClient(app(), base_url=settings.public_origin) as client:
                client.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T24ADMIN', 'password': 'company-secret'}).status_code == 200
                assert client.put('/auto_plan/api/admin/authorizations/T33EDITOR', json={'roles': ['editor']}).status_code == 200
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
                data = snapshot()['inputs']
                assert client.post('/auto_plan/api/inputs/apply', json={'base_version': 0, 'changes': {key: value for section in data.values() for key, value in section.items()}}).status_code == 200
                def head():
                    response = client.get('/auto_plan/api/plans/current')
                    assert response.status_code == 200, response.text
                    return response.json()
                def command(*operations):
                    current = head()
                    return {'base_revision': current['revision'], 'input_version': current['input_version'], 'latest_adoption': current['latest_adoption'],
                            'operation_id': str(uuid4()), 'pending': True, 'operations': list(operations)}
                def post(path, body):
                    return client.post('/auto_plan/api/plans/' + path, json=body)
                ready = {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-09T08:00:00+08:00', 'recipe_code': 'R', 'source': '原现场点检'}]}
                original_production = {'id': 'old-r', 'date': '2026-10-15', 'line_code': 'L', 'order': 0, 'model_code': 'M', 'quantity': 100, 'hours': '3'}
                assert post('adopt', command(ready, {'kind': 'patch_plan', 'segments': [original_production]})).status_code == 200
                original = client.get('/auto_plan/api/plans/adoptions/1').json()
                progress = {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-12T08:00:00+08:00', 'state': 'unfinished_switch',
                    'recipe_code': None, 'source': '现场交班', 'unfinished_switch': {'job_id': 'actual-r-s', 'started_at': '2026-10-11T08:00:00+08:00',
                    'from_recipe': 'R', 'to_recipe': 'S', 'remaining_hours': '4', 'standard_hours': '12', 'completed_hours': '8'}}]}
                before = head()
                checked = post('preview', {key: value for key, value in command(progress).items() if key not in ('operation_id', 'pending')})
                assert checked.status_code == 200, checked.text
                record = next(row for row in checked.json()['context']['line_states']['records'] if row['state'] == 'unfinished_switch')
                assert head() == before
                allocation = {'id': 'actual-r-s', 'residual_record_id': record['id'], 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
                              'allocations': [{'date': '2026-10-15', 'order': 0, 'hours': '4'}]}
                production = original_production | {'id': 'future-s', 'order': 1, 'model_code': 'P', 'quantity': 834, 'hours': '20'}
                save = command(progress, {'kind': 'replace_plan', 'plan': {'segments': [production], 'switches': [allocation]}})
                result = post('save', save)
                assert result.status_code == 200, result.text
                assert post('save', save).json() == result.json()
                assert post('verify', {'operation_id': save['operation_id']}).json() == result.json()
                current = head()
                assert current['input_version'] == before['input_version'] + 1
                assert current['evaluation']['capacity_valid'] is True
                assert current['evaluation']['executable'] is False
                assert current['evaluation']['days'][0]['switch_hours']['exact'] == '4'
                assert current['plan']['switches'][0]['residual_record_id'] == record['id']
                assert post('adopt', command()).status_code == 422
                first = progress | {'reason': '甲班复核实际剩余工时', 'rows': [progress['rows'][0] | {'expected_record_id': record['id'],
                    'unfinished_switch': progress['rows'][0]['unfinished_switch'] | {'remaining_hours': '2'}}]}
                second = first | {'reason': '乙班复核实际剩余工时', 'rows': [first['rows'][0] | {'unfinished_switch': first['rows'][0]['unfinished_switch'] | {'remaining_hours': '3'}}]}
                a = command(first)
                b = a | {'operation_id': str(uuid4()), 'operations': [second]}
                with ThreadPoolExecutor(2) as pool:
                    responses = list(pool.map(lambda body: post('save', body), [a, b]))
                assert sorted(value.status_code for value in responses) == [200, 409]
                winner = a if responses[0].status_code == 200 else b
                successful = next(value.json() for value in responses if value.status_code == 200)
                assert post('save', winner).json() == successful
                current = head()
                audit = current['context']['line_states']['audit'][-1]
                assert audit['reason'] == winner['operations'][0]['reason']
                assert audit['changes'][0]['before']['unfinished_switch']['remaining_hours'] == '4'
                assert any(issue['code'] == 'residual_reference' for issue in current['evaluation']['issues'])
                assert client.get('/auto_plan/api/plans/adoptions/1').json() == original
                restored = post('save', command({'kind': 'restore_history', 'source': {'kind': 'adoption', 'id': 1}}))
                assert restored.status_code == 200, restored.text
                expected = head()
                latest = next(row for row in expected['context']['line_states']['records'] if row['state'] == 'unfinished_switch')
                assert latest['unfinished_switch']['remaining_hours'] in ('2', '3')
                assert expected['evaluation']['executable'] is False
                assert any(issue['code'] == 'unfinished_switch' for issue in expected['evaluation']['issues'])
                assert plans.check_consistency() is True
            with TestClient(app(), base_url=settings.public_origin) as reopened:
                reopened.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert reopened.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
                assert reopened.get('/auto_plan/api/plans/current').json() == expected
                assert reopened.get('/auto_plan/api/plans/adoptions/1').json() == original
    finally:
        access.close()
