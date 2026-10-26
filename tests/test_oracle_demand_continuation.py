"""Saved continuation goals in disposable actual PLAN_TEST storage."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.oracle_demands import OracleDemandStore
from auto_plan.settings import Settings
from tests.oracle_namespace import disposable_business_namespace
from tests.support import CompanyAuthentication
from tests.test_plan_preview import snapshot
from tests.test_initialization import initial_payload

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in disposable PLAN_TEST objects only')]


def test_real_continuation_competes_atomically_keeps_original_goal_and_reopens_current_balance():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    proof = Path('/tmp/auto-plan-spec49/oracle59-evidence')
    proof.mkdir(exist_ok=True)
    prefix = 'AP_TEST_' + uuid4().hex[:6].upper()
    try:
        with disposable_business_namespace(access.pool, prefix) as namespace:
            def app():
                plans = OraclePlanStore(namespace)
                return create_app(settings, store=access, input_store=OracleInputStore(namespace), plan_store=plans, demand_store=OracleDemandStore(plans),
                                  authenticator=CompanyAuthentication(), clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc))
            with TestClient(app(), base_url=settings.public_origin) as client:
                client.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T24ADMIN', 'password': 'company-secret'}).status_code == 200
                assert client.put('/auto_plan/api/admin/authorizations/T33EDITOR', json={'roles': ['editor']}).status_code == 200
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
                data = snapshot()['inputs']
                data['masters']['lines'].append({'code': 'B', 'name': '继续补排线', 'active': True})
                data['parameters']['capacities'] = [row | {'pieces': 600} for row in data['parameters']['capacities']]
                data['parameters']['capacities'].extend(row | {'line_code': 'B'} for row in list(data['parameters']['capacities']))
                for category in ('capacities', 'line_yields', 'model_yields'):
                    data['parameters'][category].extend(row | {'month': '2026-11'} for row in list(data['parameters'][category]))
                for row in data['parameters']['line_yields']:
                    if row['model_code'] == 'M' and row['month'] == '2026-11':
                        row['percent'] = '80'
                initial = initial_payload()
                initial.update(operation_id=str(uuid4()), changes={key: value for section in ('masters', 'parameters') for key, value in data[section].items()}, segments=[])
                for row in initial['demands']:
                    row['quantity'] = 1000 if row['week'] == '2026-11-02' else 0
                initial['reset']['models'] = [{'model_code': 'M', 'wip': 0, 'net_balance': 0}]
                for coverage in initial['coverage'] + initial['execution']['coverage']:
                    coverage['line_codes'] = ['L', 'B']
                seeded = client.post('/auto_plan/api/initialization/apply', json=initial)
                assert seeded.status_code == 200, seeded.text
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
                def calculate(line, day, goal_id=None):
                    body = {key: value for key, value in command().items() if key not in ('operation_id', 'pending')}
                    body.update(action_id=str(uuid4()), model_code='M', demand_week='2026-11-02', target_output=None, mode='on_time', goal_id=goal_id,
                        line_code=line, start_date=day, end_date=day, scope={'start_date': day, 'end_date': day, 'line_codes': [line], 'model_codes': ['M']})
                    response = post('demand-trial', body)
                    assert response.status_code == 200, response.text
                    return response.json()
                first = calculate('L', '2026-10-30')
                assert (first['suggested_input'], first['remaining_output_target']) == (600, 400)
                original = first['operation']['goal']
                goal_id = original['id']
                assert post('save', command(first['operation'])).status_code == 200
                partial = client.get('/auto_plan/api/plans/revisions/2').json()
                before = head()
                alternatives = [calculate('B', '2026-11-03', goal_id), calculate('L', '2026-11-04', goal_id)]
                for result in alternatives:
                    assert (result['suggested_input'], result['scheduled_on_time_output'], result['goal_remaining_output_target']) == (499, 400, 0)
                    assert result['operation']['goal'] == original
                requests = [command(result['operation']) for result in alternatives]
                assert head() == before
                with ThreadPoolExecutor(2) as executor:
                    responses = list(executor.map(lambda body: post('save', body), requests))
                assert sorted(response.status_code for response in responses) == [200, 409]
                winner = next(index for index, response in enumerate(responses) if response.status_code == 200)
                result = responses[winner].json()
                assert post('save', requests[winner]).json() == result
                assert post('verify', {'operation_id': requests[winner]['operation_id']}).json() == result
                current = head()
                assert len(current['lineage']['demand_goals']) == 1
                goal = current['lineage']['demand_goals'][goal_id]
                assert (goal['requested_output'], goal['desired_attributed'], goal['remaining_output_target'], len(goal['accepted_actions'])) == (1000, 1000, 0, 2)
                source = client.get('/auto_plan/api/plans/revisions/3').json()
                assert post('save', command({'kind': 'replace_plan', 'plan': {}})).status_code == 200
                changed = client.post('/auto_plan/api/inputs/apply', json={'base_version': head()['input_version'],
                    'changes': {'model_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '50'}]}})
                assert changed.status_code == 200, changed.text
                restored = post('save', command({'kind': 'restore_history', 'source': {'kind': 'revision', 'id': 3}}))
                assert restored.status_code == 200, restored.text
                expected = head()
                assert expected['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 300
                assert [row['quantity'] for row in expected['plan']['segments']] == [600, 499]
                assert client.get('/auto_plan/api/plans/revisions/2').json() == partial
                assert client.get('/auto_plan/api/plans/revisions/3').json() == source
                assert client.app.state.plan_store.check_consistency() is True
                for filename, value in [('first_trial', first), ('alternatives', alternatives), ('save_requests', requests), ('winning_result', result), ('reopened_expected', expected)]:
                    (proof / f'{filename}.json').write_text(json.dumps(value, ensure_ascii=False, sort_keys=True))
            with TestClient(app(), base_url=settings.public_origin) as reopened:
                reopened.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert reopened.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
                assert reopened.get('/auto_plan/api/plans/current').json() == expected
                assert reopened.get('/auto_plan/api/plans/revisions/3').json() == source
        (proof / 'cleanup.json').write_text(json.dumps({'namespace': prefix, 'business_namespace_dropped': True, 'canonical_business_tables_modified': False}))
    finally:
        access.close()
