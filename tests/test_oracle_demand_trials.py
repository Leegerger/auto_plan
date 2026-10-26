"""Target intent and accepted placements in disposable actual PLAN_TEST storage."""
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
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def test_real_target_trial_atomic_goal_replay_concurrency_restore_and_reopen():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    proof = Path('/tmp/auto-plan-spec49/oracle58-evidence')
    proof.mkdir(exist_ok=True)
    prefix = 'AP_TEST_' + uuid4().hex[:6].upper()
    try:
        with disposable_business_namespace(access.pool, prefix) as namespace:
            def app():
                plans = OraclePlanStore(namespace)
                return create_app(settings, store=access, input_store=OracleInputStore(namespace), plan_store=plans, demand_store=OracleDemandStore(plans),
                                  authenticator=CompanyAuthentication(), clock=lambda: NOW)
            with TestClient(app(), base_url=settings.public_origin) as client:
                client.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T24ADMIN', 'password': 'company-secret'}).status_code == 200
                assert client.put('/auto_plan/api/admin/authorizations/T33EDITOR', json={'roles': ['editor']}).status_code == 200
                assert client.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
                data = snapshot()['inputs']
                data['masters']['lines'].append({'code': 'B', 'name': '二线', 'active': True})
                data['parameters']['capacities'].extend([row | {'line_code': 'B'} for row in list(data['parameters']['capacities'])])
                data['parameters']['capacities'][0]['pieces'] = 600
                data['business'] = {'demands': [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1000}, {'model_code': 'N', 'week': '2026-10-12', 'quantity': 50}]}
                initial = initial_payload()
                initial.update(operation_id=str(uuid4()), changes={key: value for section in ('masters', 'parameters') for key, value in data[section].items()}, segments=[])
                initial['demands'] += [row | {'model_code': 'N', 'quantity': 50 if index == 0 else 0} for index, row in enumerate(list(initial['demands']))]
                initial['reset']['models'] = [{'model_code': model, 'wip': 0, 'net_balance': 0} for model in ('M', 'N')]
                for coverage in initial['coverage'] + initial['execution']['coverage']:
                    coverage.update(line_codes=['L', 'B'], model_codes=['M', 'N'])
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
                legacy = client.get('/auto_plan/api/plans/revisions/1').json()
                assert 'demand_goals' not in legacy['lineage']
                before = head()
                body = {key: value for key, value in command().items() if key not in ('operation_id', 'pending')}
                body.update(model_code='M', demand_week='2026-10-12', target_output=1000, mode='on_time', line_code='L',
                            start_date='2026-10-15', end_date='2026-10-15',
                            scope={'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L'], 'model_codes': []})
                trials = [post('demand-trial', body | {'action_id': str(uuid4())}) for _ in range(2)]
                for response in trials:
                    assert response.status_code == 200, response.text
                    assert (response.json()['suggested_input'], response.json()['remaining_output_target']) == (600, 400)
                unrelated = post('demand-trial', body | {'action_id': str(uuid4()), 'model_code': 'N', 'target_output': 50, 'line_code': 'B',
                    'scope': body['scope'] | {'line_codes': ['B']}})
                assert unrelated.status_code == 200, unrelated.text
                requests = [command(response.json()['operation']) for response in trials]
                unrelated_save = command(unrelated.json()['retain_operation'])
                assert head() == before
                with ThreadPoolExecutor(2) as executor:
                    responses = list(executor.map(lambda value: post('save', value), requests))
                assert sorted(response.status_code for response in responses) == [200, 409]
                winner = next(index for index, response in enumerate(responses) if response.status_code == 200)
                saved = responses[winner].json()
                assert post('save', requests[winner]).json() == saved
                assert post('verify', {'operation_id': requests[winner]['operation_id']}).json() == saved
                source = client.get('/auto_plan/api/plans/revisions/2').json()
                goal_id = trials[winner].json()['operation']['goal']['id']
                assert source['operations'][0]['kind'] == 'supplement_demand'
                assert source['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 400
                merged = post('save', unrelated_save)
                assert merged.status_code == 200, merged.text
                current = head()
                assert {goal['model_code'] for goal in current['lineage']['demand_goals'].values()} == {'M', 'N'}
                assert sum(row['quantity'] for row in current['plan']['segments']) == 600
                assert current['latest_adoption'] == 0
                changed = client.post('/auto_plan/api/inputs/apply', json={'base_version': current['input_version'],
                    'changes': {'model_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '50'}]}})
                assert changed.status_code == 200, changed.text
                assert head()['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 700
                restored = post('save', command({'kind': 'restore_history', 'source': {'kind': 'revision', 'id': 2}}))
                assert restored.status_code == 200, restored.text
                expected = head()
                assert expected['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 700
                assert client.get('/auto_plan/api/plans/revisions/2').json() == source
                assert client.get('/auto_plan/api/plans/revisions/1').json() == legacy
                assert client.app.state.plan_store.check_consistency() is True
                for filename, value in [('inputs', data), ('trial_request', body), ('trial_response', trials[winner].json()),
                                        ('save_request', requests[winner]), ('save_result', saved), ('reopened_expected', expected)]:
                    (proof / f'{filename}.json').write_text(json.dumps(value, ensure_ascii=False, sort_keys=True))
            with TestClient(app(), base_url=settings.public_origin) as reopened:
                reopened.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
                assert reopened.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
                assert reopened.get('/auto_plan/api/plans/current').json() == expected
                assert reopened.get('/auto_plan/api/plans/revisions/2').json() == source
        (proof / 'cleanup.json').write_text(json.dumps({'namespace': prefix, 'business_namespace_dropped': True, 'canonical_business_tables_modified': False}))
    finally:
        access.close()
