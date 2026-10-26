"""Opt-in positive comparison proof using owned actual PLAN_TEST units.

Timing is observation only. This fixture asserts public business literals and
source isolation, without a new latency threshold or a persistence claim for
the compared alternatives.
"""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter, process_time, sleep
from uuid import uuid4

from fastapi.testclient import TestClient
import oracledb
import pytest

from auto_plan.app import create_app
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_demands import OracleDemandStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.settings import Settings
from tests.oracle_namespace import disposable_business_namespace
from tests.scale_support import NOW, START, full_window
from tests.support import CompanyAuthentication

pytestmark = [
    pytest.mark.oracle,
    pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in disposable PLAN_TEST only'),
    pytest.mark.skipif(os.getenv('AUTO_PLAN_ORACLE_COMPARISON_PROOF') != '1', reason='Opt-in positive comparison diagnostic'),
]


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def optional_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def positive_weekly_initialization():
    data = full_window()
    lines = [row['code'] for row in data['inputs']['masters']['lines']]
    models = [row['code'] for row in data['inputs']['masters']['models']]
    last_week = START + timedelta(weeks=14)
    next(row for row in data['inputs']['business']['demands']
         if row['model_code'] == 'M00' and row['week'] == str(last_week))['quantity'] = 1000
    rows = [{'id': f'ORACLE-W{week:02d}-{line}', 'date': str(START + timedelta(weeks=week, days=6)),
             'line_code': line, 'order': 0, 'model_code': models[number], 'quantity': 700}
            for week in range(15) for number, line in enumerate(lines)]
    coverage = {'start_date': str(START), 'end_date': str(START + timedelta(days=104)),
                'line_codes': lines, 'model_codes': models, 'blanks_are_zero': True}
    initial = {
        'base_revision': 0, 'input_version': 0, 'latest_adoption': 0,
        'changes': {key: value for section in ('masters', 'parameters', 'calendar')
                    for key, value in data['inputs'][section].items()},
        'segments': rows, 'demands': data['inputs']['business']['demands'],
        'complete_weekly_demands': True, 'coverage': [coverage],
        'initial_states': data['plan']['initial_states'],
        'reset': {'batch_id': 'oracle-positive-weekly', 'production_week': str(START), 'cutoff': NOW.isoformat(),
                  'models': [{'model_code': model, 'wip': 0, 'net_balance': 0} for model in models]},
        'execution': data['execution'],
    }
    states = {'kind': 'confirm_line_states', 'rows': [
        {'line_code': line, 'recipe_code': f'R{number // 2:02d}', 'effective_at': NOW.isoformat(),
         'source': 'PLAN_TEST positive comparison fixture'} for number, line in enumerate(lines)]}
    return initial, states


def test_real_positive_18_line_comparison_and_selected_full_preview_keep_source_unchanged():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    prefix = 'AP_TEST_' + uuid4().hex[:6].upper()
    evidence = Path(os.getenv('AUTO_PLAN_ORACLE_COMPARISON_EVIDENCE', '/tmp/auto-plan-spec49/oracle60-proof')) / prefix
    evidence.mkdir(parents=True, exist_ok=True)
    report = {
        'namespace': prefix, 'workload': 'weekly270', 'business_clock': NOW.isoformat(),
        'lines': 18, 'models': 40, 'recipes': 20, 'weeks': 15, 'explicit_demands': 600,
        'source_segments': 270, 'source_quantity': 189000, 'target_qualified_output': 300,
        'expected_selected_total': 189300, 'expected_selected_week_attribution': 1000,
        'source_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'business_source_sha256': {name: hashlib.sha256((Path('src/auto_plan') / name).read_bytes()).hexdigest()
            for name in ('demand_comparison.py', 'demand_trials.py', 'demand_operations.py', 'plans.py', 'rules.py', 'manual_operations.py')},
        'environment': {'python': sys.version, 'platform': platform.system(), 'machine': platform.machine(),
                        'oracledb': oracledb.__version__, 'oracle_user': 'PLAN_TEST', 'cpu_count': os.cpu_count(),
                        'cpu_quota': optional_text('/sys/fs/cgroup/cpu.max'),
                        'cpu_affinity_count': len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None},
        'cpu_scope': 'ASGI/client Python process; Oracle server CPU is not measured',
        'timing_threshold': None, 'candidate_persistence_claim': False,
        'context_difference': 'public initialization regenerates certificates and adds zero reset/import coverage; all lines explicitly confirmed',
        'state': 'seeding',
    }
    def write(name, value):
        (evidence / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True))
    def checkpoint():
        write('report', report)
    def application(namespace):
        plans = OraclePlanStore(namespace)
        return create_app(settings, store=access, input_store=OracleInputStore(namespace), plan_store=plans,
            demand_store=OracleDemandStore(plans), authenticator=CompanyAuthentication(), clock=lambda: NOW)
    def login(client, *, authorize=False):
        client.headers.update({'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
        account = 'T24ADMIN' if authorize else 'T33EDITOR'
        assert client.post('/auto_plan/api/auth/login', json={'username': account, 'password': 'company-secret'}).status_code == 200
        if authorize:
            assert client.put('/auto_plan/api/admin/authorizations/T33EDITOR', json={'roles': ['editor']}).status_code == 200
            assert client.post('/auto_plan/api/auth/login', json={'username': 'T33EDITOR', 'password': 'company-secret'}).status_code == 200
    def current(client):
        response = client.get('/auto_plan/api/plans/current')
        assert response.status_code == 200, response.text
        return response.json()
    def timed_post(client, path, body, name, cache_state):
        write(name + '_request', body)
        began, cpu = perf_counter(), process_time()
        response = client.post('/auto_plan/api/plans/' + path, json=body)
        observation = {'wall_seconds': perf_counter() - began, 'process_cpu_seconds': process_time() - cpu,
                       'http_status': response.status_code, 'response_bytes': len(response.content),
                       'response_sha256': hashlib.sha256(response.content).hexdigest(),
                       'request_canonical_sha256': canonical_sha(body), 'output_cache_state': cache_state}
        (evidence / (name + '_response.json')).write_bytes(response.content)
        report[name] = observation
        checkpoint()
        assert response.status_code == 200, response.text
        return response.json()
    checkpoint()
    setup_began, setup_cpu = perf_counter(), process_time()
    try:
        with disposable_business_namespace(access.pool, prefix) as namespace:
            initial, states = positive_weekly_initialization()
            write('initialization_request', initial)
            with TestClient(application(namespace), base_url=settings.public_origin) as client:
                login(client, authorize=True)
                prepared = client.post('/auto_plan/api/initialization/preview', json=initial)
                assert prepared.status_code == 200, prepared.text
                assert prepared.json()['valid'] is True, prepared.text
                write('initialization_preview', prepared.json())
                initialized = client.post('/auto_plan/api/initialization/apply', json=initial | {'operation_id': str(uuid4())})
                assert initialized.status_code == 200, initialized.text
                head = current(client)
                confirmed = client.post('/auto_plan/api/plans/save', json={
                    'base_revision': head['revision'], 'input_version': head['input_version'], 'latest_adoption': head['latest_adoption'],
                    'operation_id': str(uuid4()), 'pending': True, 'operations': [states]})
                assert confirmed.status_code == 200, confirmed.text
                before = current(client)
                assert len(before['plan']['segments']) == 270
                assert before['plan']['switches'] == []
                assert len(before['context']['line_states']['records']) == 18
                assert len(before['evaluation']['weekly_results']) == 600
                assert before['evaluation']['output_totals']['expected'] == before['evaluation']['output_totals']['on_time'] == 189000
                assert before['evaluation']['computation_complete'] is before['evaluation']['capacity_valid'] is True
                source = {key: before[key] for key in ('revision', 'input_version', 'latest_adoption')}
                history = client.get(f"/auto_plan/api/plans/revisions/{source['revision']}").json()
                inputs = client.get('/auto_plan/api/inputs', params={'version': source['input_version']}).json()
                assert len(inputs['masters']['models']) == 40 and len(inputs['masters']['lines']) == 18
                write('source_head', before)
                write('source_history', history)
                write('source_inputs', inputs)
                report.update(source_pointers=source, source_canonical_sha256=canonical_sha(before),
                              source_history_sha256=canonical_sha(history), input_sha256=canonical_sha(inputs),
                              actual_source_switches=0, state='seeded')
                checkpoint()
            report['setup_outside_measurement'] = {'wall_seconds': perf_counter() - setup_began,
                                                    'process_cpu_seconds': process_time() - setup_cpu}
            checkpoint()
            gate = os.getenv('AUTO_PLAN_ORACLE_COMPARISON_GATE')
            if gate:
                gate = Path(gate)
                gate.with_suffix('.ready.json').write_text(json.dumps({'evidence': str(evidence), 'source': source}))
                began = perf_counter()
                while not gate.exists():
                    assert perf_counter() - began < 600, 'Coordinated timing gate was not released within ten minutes'
                    sleep(.2)
                report['gate_wait_seconds'] = perf_counter() - began
            with TestClient(application(namespace), base_url=settings.public_origin) as client:
                login(client)
                last_week = START + timedelta(weeks=14)
                last_day = last_week + timedelta(days=6)
                body = {'base_revision': source['revision'], 'input_version': source['input_version'], 'latest_adoption': source['latest_adoption'],
                    'operations': [], 'action_id': str(uuid4()), 'model_code': 'M00', 'demand_week': str(last_week),
                    'target_output': 300, 'mode': 'on_time', 'start_date': str(last_day), 'end_date': str(last_day),
                    'scope': {'start_date': str(last_day), 'end_date': str(last_day),
                              'line_codes': [f'L{number:02d}' for number in range(18)], 'model_codes': []}}
                report['state'] = 'measuring'
                checkpoint()
                compared = timed_post(client, 'demand-comparison', body, 'comparison',
                    'fresh OraclePlanStore; no prior planning request; startup/database buffers may be warm')
                assert len(compared['rows']) == 18
                assert [row['line_code'] for row in compared['rows']] == body['scope']['line_codes']
                assert {key: compared['basis'][key] for key in source} == source
                assert compared['basis']['as_of'] == NOW.isoformat()
                assert compared['basis']['window_start'] == str(START)
                assert len(compared['basis']['digest']) == 64
                for row in compared['rows']:
                    assert row['status'] == 'complete'
                    assert (row['suggested_input'], row['scheduled_on_time_output'], row['valid_on_time_output'], row['remaining_output_target']) == (300, 300, 300, 0)
                    assert row['operation']['goal']['baseline_attributed'] == 700
                    assert row['operation']['goal']['desired_attributed'] == 1000
                    assert row['operation']['goal']['origin_revision'] == source['revision']
                    assert row['operation']['goal']['origin_input_version'] == source['input_version']
                    assert row['operation']['scope']['line_codes'] == [row['line_code']]
                command = {key: body[key] for key in ('base_revision', 'input_version', 'latest_adoption')}
                command['operations'] = [compared['rows'][0]['operation']]
                selected = timed_post(client, 'preview', command, 'selected_full_preview',
                    'same OraclePlanStore after comparison; shared checked output reuse may be warm')
                assert selected['evaluation']['computation_complete'] is selected['evaluation']['capacity_valid'] is True
                assert selected['evaluation']['output_totals']['expected'] == selected['evaluation']['output_totals']['on_time'] == 189300
                assert len(selected['evaluation']['weekly_results']) == 600
                target = next(row for row in selected['evaluation']['weekly_results'] if row['model_code'] == 'M00' and row['week'] == str(last_week))
                assert target['attributed'] == target['on_time'] == 1000
                actual = {row['id']: row for row in selected['plan']['segments']}
                assert all(actual[row['id']] == row for row in before['plan']['segments']
                           if (row['date'], row['line_code']) != (str(last_day), 'L00'))
                assert selected['plan']['switches'] == before['plan']['switches']
                assert selected['context']['line_states'] == before['context']['line_states']
                assert selected['context']['execution'] == before['context']['execution']
                old_weeks = {(row['model_code'], row['week']): row for row in before['evaluation']['weekly_results']}
                assert all(row == old_weeks[row['model_code'], row['week']] for row in selected['evaluation']['weekly_results']
                           if (row['model_code'], row['week']) != ('M00', str(last_week)))
                assert current(client) == before
                assert client.get(f"/auto_plan/api/plans/revisions/{source['revision']}").json() == history
                assert client.get('/auto_plan/api/inputs', params={'version': source['input_version']}).json() == inputs
                report.update(state='verified', complete_rows=18, selected_week_attribution=1000,
                              selected_expected_output=189300, source_and_outside_witnesses_unchanged=True)
                checkpoint()
            with TestClient(application(namespace), base_url=settings.public_origin) as client:
                login(client)
                assert current(client) == before
                assert client.get(f"/auto_plan/api/plans/revisions/{source['revision']}").json() == history
                report['fresh_store_source_readback_unchanged'] = True
                checkpoint()
        report.update(state='complete', business_namespace_dropped=True, canonical_business_tables_modified=False)
        checkpoint()
    finally:
        access.close()
