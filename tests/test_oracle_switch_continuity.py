"""Real PLAN_TEST continuity adoption, race, fault and immutable-history evidence."""
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
from tests.test_unfinished_switches import unfinished, residual
from tests.test_switch_continuity import confirmation
from tests.support import CompanyAuthentication

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in disposable PLAN_TEST objects only')]
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def test_real_continuity_adoption_race_commit_faults_history_restore_and_reopen():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool, 'AP_TEST_' + uuid4().hex[:6].upper()) as namespace:
            faults = FaultPool(namespace)
            inputs = OracleInputStore(namespace)
            plans = OraclePlanStore(faults)
            def app():
                return create_app(settings, store=access, input_store=inputs, plan_store=plans, authenticator=CompanyAuthentication(), clock=lambda: NOW)
            with TestClient(app(), base_url=settings.public_origin) as client:
                client.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T24ADMIN', 'password': 'company-secret'}).status_code == 200
                for user, role in [('T33EDITOR', 'editor'), ('T33VIEWER', 'viewer')]:
                    assert client.put('/auto_plan/api/admin/authorizations/' + user, json={'roles': [role]}).status_code == 200
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
                data = snapshot()['inputs']
                assert client.post('/auto_plan/api/inputs/apply', json={'base_version': 0, 'changes': {key: value for section in data.values() for key, value in section.items()}}).status_code == 200
                def post(path, body):
                    return client.post('/auto_plan/api/plans/' + path, json=body)
                def head():
                    answer = client.get('/auto_plan/api/plans/current')
                    assert answer.status_code == 200, answer.text
                    return answer.json()
                def command(*operations):
                    current = head()
                    return {'base_revision': current['revision'], 'input_version': current['input_version'], 'latest_adoption': current['latest_adoption'],
                            'operation_id': str(uuid4()), 'pending': True, 'operations': list(operations)}
                assert post('save', command(unfinished())).status_code == 200
                progress = head()['context']['line_states']['records'][0]
                plan = {'kind': 'patch_plan', 'switches': [residual(progress['id'])], 'segments': [
                    {'id': 'target', 'date': '2026-10-15', 'line_code': 'L', 'order': 1, 'model_code': 'P', 'quantity': 100, 'hours': '3'}]}
                assert post('save', command(plan)).status_code == 200
                before = head()
                old_revision = client.get(f"/auto_plan/api/plans/revisions/{before['revision']}").json()
                assert old_revision['snapshot']['switch_continuity']['records'] == []
                dto = before['workspace_view']['switch_continuity'][0]
                assert post('adopt', command()).status_code == 422
                first = command(confirmation(dto, source='甲班现场确认'))
                second = first | {'operation_id': str(uuid4()), 'operations': [confirmation(dto, source='乙班现场确认')]}
                checked = post('preview', {key: value for key, value in first.items() if key not in ('operation_id', 'pending')})
                assert checked.status_code == 200 and checked.json()['evaluation']['executable'] is True
                assert head() == before
                with ThreadPoolExecutor(2) as pool:
                    answers = list(pool.map(lambda body: post('adopt', body), [first, second]))
                assert sorted(answer.status_code for answer in answers) == [200, 409]
                winner = first if answers[0].status_code == 200 else second
                successful = next(answer.json() for answer in answers if answer.status_code == 200)
                assert post('adopt', winner).json() == successful
                assert post('verify', {'operation_id': winner['operation_id']}).json() == successful
                current = head()
                assert current['latest_adoption'] == 1
                assert current['input_version'] == before['input_version'] + 1
                assert len(current['context']['switch_continuity']['audit']) == 1
                assert current['workspace_view']['switch_continuity'][0]['status'] == 'valid'
                original = client.get('/auto_plan/api/plans/adoptions/1').json()
                stored = current['context']['switch_continuity']['records'][0]
                corrected = confirmation(dto, source='交班现场复核', expected_confirmation_id=stored['id'], reason='补充交班确认来源')
                failed_body = command(corrected)
                faults.commit_fault = 'before'
                failed = post('adopt', failed_body)
                assert failed.status_code == 503, failed.text
                assert post('verify', {'operation_id': failed_body['operation_id']}).json()['status'] == 'ABORTED_VOID'
                assert head() == current
                unknown_body = command(corrected)
                faults.commit_fault = 'after'
                unknown = post('adopt', unknown_body)
                assert unknown.status_code == 503 and unknown.json()['state'] == 'unknown'
                verified = post('verify', {'operation_id': unknown_body['operation_id']})
                assert verified.status_code == 200 and verified.json()['status'] == 'COMMITTED'
                assert post('adopt', unknown_body).json() == verified.json()
                current = head()
                assert current['latest_adoption'] == 2
                assert len(current['context']['switch_continuity']['audit']) == 2
                audit = current['context']['switch_continuity']['audit'][-1]
                assert audit['changes'][0]['before']['id'] == stored['id']
                assert audit['changes'][0]['after']['source'] == '交班现场复核'
                changed = unfinished(remaining_hours='2')
                changed['rows'][0]['expected_record_id'] = progress['id']
                changed['reason'] = '现场复核实际剩余量'
                assert post('save', command(changed)).status_code == 200
                assert post('save', command({'kind': 'restore_history', 'source': {'kind': 'adoption', 'id': 1}})).status_code == 200
                expected = head()
                assert expected['context']['line_states']['records'][0]['unfinished_switch']['remaining_hours'] == '2'
                assert expected['workspace_view']['switch_continuity'][0]['status'] == 'stale'
                assert expected['context']['switch_continuity']['records'][0]['source'] == '交班现场复核'
                assert post('adopt', command()).status_code == 422
                assert client.get('/auto_plan/api/plans/adoptions/1').json() == original
                assert client.get(f"/auto_plan/api/plans/revisions/{before['revision']}").json() == old_revision
                assert plans.check_consistency() is True
            with TestClient(app(), base_url=settings.public_origin) as reopened:
                reopened.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert reopened.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
                assert reopened.get('/auto_plan/api/plans/current').json() == expected
                assert reopened.get('/auto_plan/api/plans/adoptions/1').json() == original
                assert reopened.post('/auto_plan/api/auth/login', json={'username': 'T33VIEWER', 'password': 'company-secret'}).status_code == 200
                assert reopened.get('/auto_plan/api/plans/current').json()['context']['switch_continuity'] == expected['context']['switch_continuity']
                body = {'base_revision': expected['revision'], 'input_version': expected['input_version'], 'latest_adoption': expected['latest_adoption'],
                        'operations': [confirmation(dto)], 'operation_id': str(uuid4()), 'pending': True}
                for path in ('preview', 'save', 'adopt'):
                    value = body if path != 'preview' else {key: value for key, value in body.items() if key not in ('operation_id', 'pending')}
                    assert reopened.post('/auto_plan/api/plans/' + path, json=value).status_code == 403
    finally:
        access.close()
