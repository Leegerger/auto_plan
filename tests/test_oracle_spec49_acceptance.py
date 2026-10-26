"""Composite protected-switch/qualified-goal acceptance in disposable PLAN_TEST."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter, process_time
from uuid import uuid4

import oracledb
import pytest
from fastapi.testclient import TestClient

from auto_plan.app import create_app
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_demands import OracleDemandStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.settings import Settings
from tests.oracle_namespace import disposable_business_namespace
from tests.support import CompanyAuthentication
from tests.test_initialization import initial_payload
from tests.test_plan_preview import snapshot
from tests.test_switch_continuity import confirmation

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(
    os.getenv('AUTO_PLAN_TEST_ORACLE') != '1',
    reason='Opt-in disposable PLAN_TEST objects only')]
NOW = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)


def test_actual_remaining_switch_continues_one_qualified_goal_atomically_and_reopens():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    prefix = 'AP_TEST_' + uuid4().hex[:6].upper()
    proof = Path(os.getenv('AUTO_PLAN_ORACLE62_EVIDENCE',
                           '/tmp/auto-plan-spec49/oracle62-evidence')) / prefix
    proof.mkdir(parents=True)
    timings = {}

    def record(name, value):
        (proof / (name + '.json')).write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))

    record('environment', {
        'clock': NOW.isoformat(), 'python': sys.version, 'platform': platform.platform(),
        'oracledb': oracledb.__version__, 'database_account': 'PLAN_TEST',
        'namespace': prefix, 'source_sha': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'test_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'timing_claim': 'Correctness run only; no quiet CPU window or SLA assertion',
    })
    record('expected', {
        'actual_job': {'from_recipe': 'R', 'to_recipe': 'S', 'standard_hours': '12',
                       'completed_hours': '8', 'remaining_hours': '4'},
        'line_L_835': {'input': 834, 'qualified_remainder': 1},
        'line_L_1000': {'input': 834, 'qualified_goal': 166},
        'line_B_continuation': {'input': 166, 'qualified_goal': 0},
        'target_input': 1000, 'target_output': 1000, 'outside_input': 50,
        'current_half_yield': {'target_output': 500, 'qualified_goal': 500},
    })
    access = OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool, prefix) as namespace:
            def app():
                plans = OraclePlanStore(namespace)
                return create_app(
                    settings, store=access, input_store=OracleInputStore(namespace),
                    plan_store=plans, demand_store=OracleDemandStore(plans),
                    authenticator=CompanyAuthentication(), clock=lambda: NOW)

            def login(client, username):
                client.headers.update({'Origin': settings.public_origin,
                                       'X-Auto-Plan-Request': '1'})
                response = client.post('/auto_plan/api/auth/login', json={
                    'username': username, 'password': 'company-secret'})
                assert response.status_code == 200, response.status_code

            def call(client, label, method, path, body=None, expected=200):
                record(label + '_request', {'method': method, 'path': path, 'body': body})
                wall, cpu = perf_counter(), process_time()
                response = client.request(method, '/auto_plan/api/' + path, json=body)
                timings[label] = {'wall_seconds': perf_counter() - wall,
                                  'process_cpu_seconds': process_time() - cpu,
                                  'status': response.status_code,
                                  'response_bytes': len(response.content)}
                record('timings', timings)
                record(label + '_response', response.json())
                if expected is not None:
                    assert response.status_code == expected, response.text
                return response.json()

            with TestClient(app(), base_url=settings.public_origin) as client:
                login(client, 'T24ADMIN')
                for user, role in [('T33EDITOR', 'editor'), ('T33VIEWER', 'viewer')]:
                    assert client.put('/auto_plan/api/admin/authorizations/' + user,
                                      json={'roles': [role]}).status_code == 200
                login(client, 'T33EDITOR')

                data = snapshot()['inputs']
                data['masters']['lines'] += [
                    {'code': 'B', 'name': '续排线', 'active': True},
                    {'code': 'C', 'name': '范围外锁定线', 'active': True}]
                data['parameters']['capacities'] += [
                    row | {'line_code': line} for line in ('B', 'C')
                    for row in list(data['parameters']['capacities'])]
                data['parameters']['switch_durations'][0]['hours'] = '12'
                initial = initial_payload()
                initial.update(operation_id=str(uuid4()), changes={
                    key: value for section in ('masters', 'parameters')
                    for key, value in data[section].items()}, segments=[{
                        'id': 'outside-C-N', 'date': '2026-10-16', 'line_code': 'C',
                        'model_code': 'N', 'order': 7, 'quantity': 50}])
                initial['demands'] = [
                    row | {'model_code': model, 'quantity': quantity if index == 0 else 0}
                    for model, quantity in [('M', 0), ('N', 50), ('P', 1000)]
                    for index, row in enumerate(initial['demands'])]
                initial['reset']['models'] = [
                    {'model_code': model, 'wip': 0, 'net_balance': 0}
                    for model in ('M', 'N', 'P')]
                for coverage in initial['coverage'] + initial['execution']['coverage']:
                    coverage.update(line_codes=['L', 'B', 'C'], model_codes=['M', 'N', 'P'])
                imported = call(client, 'initialization_preview', 'POST',
                                'initialization/preview', {key: value for key, value in initial.items()
                                                           if key != 'operation_id'})
                assert imported['valid'] is True, imported
                call(client, 'initialization_apply', 'POST', 'initialization/apply', initial)

                def head():
                    response = client.get('/auto_plan/api/plans/current')
                    assert response.status_code == 200, response.text
                    return response.json()

                def command(*operations):
                    current = head()
                    return {'base_revision': current['revision'],
                            'input_version': current['input_version'],
                            'latest_adoption': current['latest_adoption'],
                            'operation_id': str(uuid4()), 'pending': True,
                            'operations': list(operations)}

                def preview_body(*operations):
                    return {key: value for key, value in command(*operations).items()
                            if key not in ('operation_id', 'pending')}

                state = {'kind': 'confirm_line_states', 'action_id': str(uuid4()), 'rows': [
                    {'line_code': 'L', 'effective_at': '2026-10-14T08:30:00+08:00',
                     'state': 'unfinished_switch', 'recipe_code': None,
                     'source': '周三交班实测', 'unfinished_switch': {
                         'job_id': 'actual-r-s', 'started_at': '2026-10-11T20:00:00+08:00',
                         'from_recipe': 'R', 'to_recipe': 'S', 'standard_hours': '12',
                         'completed_hours': '8', 'remaining_hours': '4'}},
                    *[{'line_code': line, 'effective_at': '2026-10-12T08:00:00+08:00',
                       'recipe_code': recipe, 'source': '周初现场点检'}
                      for line, recipe in [('B', 'S'), ('C', 'R')]]]}
                lock = {'kind': 'lock_cells', 'action_id': str(uuid4()), 'cells': [{
                    'date': '2026-10-16', 'line_code': 'C', 'model_code': 'N', 'quantity': 50}]}
                source = head()
                outside = source['plan']['segments'][0]
                record('initialized_source', source)

                def trial(label, line, target, *, goal_id=None, operations=()):
                    body = preview_body(*operations) | {
                        'action_id': str(uuid4()), 'model_code': 'P',
                        'demand_week': '2026-10-12', 'target_output': target,
                        'goal_id': goal_id, 'mode': 'on_time', 'line_code': line,
                        'start_date': '2026-10-15', 'end_date': '2026-10-15',
                        'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15',
                                  'line_codes': [line], 'model_codes': ['P']}}
                    return call(client, label, 'POST', 'plans/demand-trial', body)

                boundary = trial('readonly_835', 'L', 835, operations=[state, lock])
                assert (boundary['status'], boundary['suggested_input'],
                        boundary['scheduled_on_time_output'],
                        boundary['remaining_output_target']) == ('partial', 834, 834, 1)
                assert head() == source
                first = trial('target_1000', 'L', 1000, operations=[state, lock])
                assert (first['suggested_input'], first['remaining_output_target']) == (834, 166)
                assert first['preview']['unallocated'] == []
                assert first['preview']['evaluation']['capacity_valid'] is True
                assert first['preview']['evaluation']['executable'] is False
                assert {issue['code'] for issue in first['blockers']} == {'remaining_switch_continuity'}
                actual = next(row for row in first['preview']['context']['line_states']['records']
                              if row['line_code'] == 'L')
                switch, = first['preview']['plan']['switches']
                assert (switch['id'], switch['residual_record_id'], switch['from_recipe'],
                        switch['to_recipe']) == ('actual-r-s', actual['id'], 'R', 'S')
                assert [row['hours'] for row in switch['allocations']] == ['4']
                assert next(row for row in first['preview']['plan']['segments']
                            if row['id'] == outside['id']) == outside
                assert head() == source
                goal = first['operation']['goal']
                goal_id = goal['id']
                dto, = first['preview']['workspace_view']['switch_continuity']
                assert dto['status'] == 'missing' and dto['can_confirm'] is True
                queued = [state, lock, first['operation']]
                call(client, 'missing_continuity_adoption', 'POST', 'plans/adopt',
                     command(*queued), expected=422)
                queued.append(confirmation(dto, source='剩余四小时现场连续作业核实'))
                checked = call(client, 'atomic_queue_preview', 'POST', 'plans/preview',
                               preview_body(*queued))
                assert checked['evaluation']['executable'] is True
                assert head() == source
                save = command(*queued)
                call(client, 'atomic_pending_save', 'POST', 'plans/save', save)
                partial = head()
                assert partial['pending'] is True and partial['latest_adoption'] == 0
                assert len(partial['context']['line_states']['audit']) == 1
                assert len(partial['context']['switch_continuity']['audit']) == 1
                assert partial['context']['locks'] == lock['cells']
                assert partial['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 166
                partial_revision = call(client, 'partial_revision', 'GET',
                                        f"plans/revisions/{partial['revision']}")

                alternatives = [trial(f'continuation_{index}', 'B', None, goal_id=goal_id)
                                for index in range(2)]
                for alternative in alternatives:
                    assert (alternative['suggested_input'], alternative['scheduled_on_time_output'],
                            alternative['goal_remaining_output_target']) == (166, 166, 0)
                    assert alternative['operation']['goal'] == goal
                requests = [command(alternative['operation']) for alternative in alternatives]
                assert requests[0]['base_revision'] == requests[1]['base_revision']
                assert head() == partial
                with ThreadPoolExecutor(2) as executor:
                    responses = list(executor.map(lambda pair: call(
                        client, f'racing_save_{pair[0]}', 'POST', 'plans/save', pair[1],
                        expected=None), enumerate(requests)))
                # Each response body is retained, including the losing conflict.
                statuses = [timings[f'racing_save_{index}']['status'] for index in range(2)]
                assert sorted(statuses) == [200, 409]
                winner = statuses.index(200)
                successful = responses[winner]
                assert call(client, 'winner_retry', 'POST', 'plans/save', requests[winner]) == successful
                assert call(client, 'winner_verify', 'POST', 'plans/verify', {
                    'operation_id': requests[winner]['operation_id']}) == successful
                complete = head()
                assert sorted(row['quantity'] for row in complete['plan']['segments']
                              if row['model_code'] == 'P') == [166, 834]
                assert complete['plan']['switches'] == partial['plan']['switches']
                assert next(row for row in complete['plan']['segments']
                            if row['id'] == outside['id']) == outside
                saved_goal = complete['lineage']['demand_goals'][goal_id]
                assert {key: saved_goal[key] for key in goal} == goal
                assert (saved_goal['remaining_output_target'], len(saved_goal['accepted_actions'])) == (0, 2)
                p_week = next(row for row in complete['evaluation']['weekly_results']
                              if row['model_code'] == 'P' and row['week'] == '2026-10-12')
                assert (p_week['attributed'], p_week['on_time']) == (1000, 1000)
                final_preview = call(client, 'full_current_preview', 'POST', 'plans/preview', preview_body())
                assert final_preview['evaluation']['executable'] is True
                assert final_preview['evaluation']['switch_count'] == 1
                assert final_preview['evaluation']['output_totals']['expected'] == 1050
                week = next(row for row in final_preview['workspace_view']['line_weeks']
                            if row['line_code'] == 'L' and row['week'] == '2026-10-12')
                assert (week['new_switch_count'], week['carried_switch_count'],
                        week['switch_hours']['exact']) == (0, 1, '4')
                call(client, 'adoption', 'POST', 'plans/adopt', command())
                adopted = head()
                assert adopted['latest_adoption'] == 1
                actual_after = next(row for row in adopted['context']['line_states']['records']
                                    if row['line_code'] == 'L')
                assert actual_after == actual
                assert actual_after['state'] == 'unfinished_switch'
                original = call(client, 'immutable_adoption', 'GET', 'plans/adoptions/1')
                assert call(client, 'unchanged_partial_history', 'GET',
                            f"plans/revisions/{partial['revision']}") == partial_revision

                call(client, 'remove_target_plan', 'POST', 'plans/save', command({
                    'kind': 'replace_plan', 'plan': {'segments': [outside]}}))
                stale_source = preview_body()
                call(client, 'current_half_yield', 'POST', 'inputs/apply', {
                    'base_version': head()['input_version'], 'changes': {
                        'model_yields': [{'month': '2026-10', 'model_code': 'P', 'percent': '50'}]}})
                call(client, 'stale_source_preview', 'POST', 'plans/preview',
                     stale_source, expected=409)
                restore = {'kind': 'restore_history', 'source': {'kind': 'adoption', 'id': 1}}
                restored_preview = call(client, 'restore_current_rules_preview', 'POST',
                                        'plans/preview', preview_body(restore))
                assert restored_preview['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 500
                call(client, 'restore_current_rules_save', 'POST', 'plans/save', command(restore))
                expected = head()
                assert expected['plan'] == adopted['plan']
                assert expected['context']['locks'] == lock['cells']
                assert expected['context']['line_states'] == adopted['context']['line_states']
                assert expected['lineage']['demand_goals'][goal_id]['remaining_output_target'] == 500
                p_week = next(row for row in expected['evaluation']['weekly_results']
                              if row['model_code'] == 'P' and row['week'] == '2026-10-12')
                assert (p_week['attributed'], p_week['on_time']) == (500, 500)
                assert expected['evaluation']['output_totals']['expected'] == 550
                assert call(client, 'history_after_maintenance', 'GET', 'plans/adoptions/1') == original
                assert call(client, 'partial_after_maintenance', 'GET',
                            f"plans/revisions/{partial['revision']}") == partial_revision
                record('reopened_expected', expected)
                assert client.app.state.plan_store.check_consistency() is True

            with TestClient(app(), base_url=settings.public_origin) as reopened:
                login(reopened, 'T33EDITOR')
                assert call(reopened, 'fresh_store_current', 'GET', 'plans/current') == expected
                assert call(reopened, 'fresh_store_history', 'GET', 'plans/adoptions/1') == original
                login(reopened, 'T33VIEWER')
                assert call(reopened, 'viewer_current', 'GET', 'plans/current') == expected
                body = {'base_revision': expected['revision'], 'input_version': expected['input_version'],
                        'latest_adoption': expected['latest_adoption'], 'operations': [restore]}
                for path in ('preview', 'save', 'adopt'):
                    request = body if path == 'preview' else body | {
                        'operation_id': str(uuid4()), 'pending': True}
                    call(reopened, 'viewer_denied_' + path, 'POST', 'plans/' + path,
                         request, expected=403)
        record('cleanup', {'namespace': prefix, 'business_namespace_dropped': True,
                           'canonical_business_tables_modified': False})
    finally:
        access.close()
