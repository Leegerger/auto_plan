"""Full canonical private edits follow real shared-save epochs through public APIs."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.input_models import InputSnapshot
from auto_plan.plan_models import EvaluationSnapshot, Plan
from auto_plan.plans import PlanningContext
from auto_plan.rules import evaluate
from auto_plan.settings import Settings
from scripts.acceptance.adapter_server import BrowserPlanStore
from scripts.acceptance.compare import assert_protected, numeric, objective
from tests.input_support import MemoryInputStore
from tests.jobs_support import MemoryJobStore
from tests.support import CompanyAuthentication, MemoryAccessStore


def assert_complete_plan(actual, expected):
    """Switch identity owns a record; allocation order remains business data."""
    actual, expected = deepcopy(actual), deepcopy(expected)
    left, right = actual.pop('switches'), expected.pop('switches')
    assert len(left) == len(right), 'The complete switch collection changed length.'
    left_ids, right_ids = [row['id'] for row in left], [row['id'] for row in right]
    assert all(isinstance(identity, str) and identity for identity in left_ids + right_ids)
    assert len(set(left_ids)) == len(left_ids), 'Duplicate actual switch identity.'
    assert len(set(right_ids)) == len(right_ids), 'Duplicate expected switch identity.'
    assert set(left_ids) == set(right_ids), 'The complete switch identity set changed.'
    assert sorted(left, key=lambda row: row['id']) == sorted(right, key=lambda row: row['id']), 'A complete switch record changed.'
    assert numeric(actual) == numeric(expected), 'A full Plan field outside the declared legal witness changed.'


def test_switch_collection_comparison_preserves_complete_records_and_other_arrays():
    plan = {'segments': [{'id': 'S1'}, {'id': 'S2'}], 'switches': [
        {'id': 'X1', 'line_code': 'L1', 'from_recipe': 'R0', 'to_recipe': 'R1', 'allocations': [{'date': '2027-01-23', 'order': 2, 'hours': '1'}, {'date': '2027-01-24', 'order': 0, 'hours': '2'}]},
        {'id': 'X2', 'line_code': 'L2', 'from_recipe': 'R1', 'to_recipe': 'R2', 'allocations': [{'date': '2027-01-24', 'order': 3, 'hours': '1'}]},
    ], 'ordinary_idle': []}
    permuted = deepcopy(plan); permuted['switches'].reverse()
    assert_complete_plan(permuted, plan)
    invalid = []
    duplicate = deepcopy(plan); duplicate['switches'][1] = deepcopy(duplicate['switches'][0]); invalid.append(duplicate)
    missing = deepcopy(plan); missing['switches'].pop(); invalid.append(missing)
    allocations = deepcopy(plan); allocations['switches'][0]['allocations'].reverse(); invalid.append(allocations)
    for path, value in [('order', 9), ('hours', '3'), ('date', '2027-01-25')]:
        changed = deepcopy(plan); changed['switches'][0]['allocations'][0][path] = value; invalid.append(changed)
    for path, value in [('line_code', 'L9'), ('from_recipe', 'R9'), ('to_recipe', 'R9'), ('id', 'X9')]:
        changed = deepcopy(plan); changed['switches'][0][path] = value; invalid.append(changed)
    other_order = deepcopy(plan); other_order['segments'].reverse(); invalid.append(other_order)
    for changed in invalid:
        with pytest.raises(AssertionError):
            assert_complete_plan(changed, plan)


def test_full_r20_private_edits_follow_original_save_and_restore_epochs(tmp_path):
    root = Path(__file__).resolve().parents[2]
    frozen = root / 'docs/acceptance/frozen'
    manifest = (frozen / 'manifest.json').read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == '33e765b2cc22a28116d0416f04cc05cd628f5239183160be32a143035d29b0f4'
    scenario = next(row for row in json.loads(manifest)['scenarios'] if row['id'] == 'r20-loose')
    assert scenario['source']['sha256'] == scenario['reference']['sha256'] == 'd019627a21ca1507f9145aac1575c55fa03c8be52838d0f47c85088a8703d909'
    assert scenario['action']['sha256'] == '7f524927f3aa5d0df789c60c938557530b26322addb90ad5b4c14bf8a7ae2f4c'
    raw = gzip.decompress((frozen / scenario['source']['path']).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == scenario['source']['sha256']
    source = EvaluationSnapshot.model_validate(json.loads(raw)).model_dump(mode='json')
    assert len(source['plan']['segments']) == 4200 and len(source['plan']['switches']) == 418
    assert len(source['inputs']['business']['demands']) == 600
    rows = {model: next(row for row in source['plan']['segments'] if row['id'] == 'S-2027-01-24-' + model) for model in ('M00', 'M39')}
    for model, line, order in [('M00', 'L00', 1), ('M39', 'L01', 5)]:
        assert rows[model] == {'id': 'S-2027-01-24-' + model, 'date': '2027-01-24', 'line_code': line, 'model_code': model, 'order': order, 'quantity': 100, 'hours': '1'}
    inputs = MemoryInputStore()
    inputs.versions[1] = InputSnapshot.model_validate(source['inputs']); inputs.version = 1
    plans = BrowserPlanStore(inputs)
    plans.plan = Plan.model_validate(source['plan'])
    plans.context = PlanningContext(execution=source['execution'], reset=source['reset'], locks=source['locks'], preferences=source['preferences'], coverage=source['baseline']['coverage'])
    plans.revision = plans.adoption = 1
    plans.revisions[1] = {'snapshot': deepcopy(source), 'coverage': source['baseline']['coverage'], 'lineage': {}, 'operations': [], 'effects': [], 'pending': False, 'kind': 'adopt', 'actor': 'ACCEPTANCE0', 'revision': 1, 'recorded_at': source['time']['as_of'], 'input_version': 1, 'latest_adoption': 1, 'evaluation': evaluate(EvaluationSnapshot.model_validate(source)).model_dump(mode='json')}
    plans.adoptions[1] = deepcopy(plans.revisions[1])
    access = MemoryAccessStore()
    for actor in ('ACCEPTANCE0', 'ACCEPTANCE1'):
        access.users[actor] = {'user_id': actor, 'roles': ['editor'], 'active': True}
    jobs = MemoryJobStore(plans, access)
    app = create_app(Settings(public_origin='https://testserver', base_path='/planning'), store=access, input_store=inputs, plan_store=plans, job_store=jobs, authenticator=CompanyAuthentication(), clock=lambda: datetime.fromisoformat(source['time']['as_of']))
    weeks = [(date(2026, 10, 12) + timedelta(days=7*i)).isoformat() for i in range(15)]
    wire = []
    def retain(name, value):
        (tmp_path / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    with TestClient(app, base_url='https://testserver') as client:
        client.headers.update({'Origin': 'https://testserver', 'X-Auto-Plan-Request': '1'})
        def login(actor):
            assert client.post('/planning/api/auth/login', json={'username': actor, 'password': 'company-secret'}).status_code == 200
        def call(path, payload=None, status=200):
            response = client.get('/planning/api' + path) if payload is None else client.post('/planning/api' + path, json=payload)
            index = len(wire)
            request, reply = response.request.content, response.content
            (tmp_path / f'{index:02d}-request.json').write_bytes(request)
            (tmp_path / f'{index:02d}-response.json').write_bytes(reply)
            wire.append({'path': path, 'status': response.status_code, 'request_sha256': hashlib.sha256(request).hexdigest(), 'response_sha256': hashlib.sha256(reply).hexdigest()})
            retain('wire.json', wire)
            assert response.status_code == status, (path, response.status_code, response.text[:200])
            return response.json()
        def command(model):
            row = rows[model]
            return {'base_revision': 1, 'input_version': 1, 'latest_adoption': 1, 'operations': [{'kind': 'change_quantity', 'segment_id': row['id'], 'quantity': 99, 'end_date': row['date'], 'action_id': str(uuid4()), 'scope': {'start_date': row['date'], 'end_date': row['date'], 'line_codes': [row['line_code']], 'model_codes': [model]}}]}
        def checked(value, parent_plan, model, gap_models, parent, base):
            assert (value['parent_revision'], value['base_revision'], value['revision']) == (parent, base, parent)
            assert value['merged'] is (parent != base)
            assert value['input_version'] == value['latest_adoption'] == 1
            wanted, actual = deepcopy(parent_plan), deepcopy(value['plan'])
            if model:
                selected = next(row for row in actual['segments'] if row['id'] == rows[model]['id'])
                expected = next(row for row in wanted['segments'] if row['id'] == rows[model]['id'])
                expected['quantity'] = 99
                assert Fraction(49, 50) < Fraction(selected['hours']) <= 18
                del selected['hours']; del expected['hours']
            assert_complete_plan(actual, wanted)
            assert value['snapshot'] == source | {'plan': value['plan']}
            assert value['context'] == before_records['/plans/current']['context']
            assert value['context_changes'] == [] and value['unallocated'] == value['operation_issues'] == []
            assert value['adoption_blocked'] is False
            effects = [] if not model else [['CAP', rows[model]['line_code'], '2027-01-24']] + [['CELL', '2027-01-24', f'L{i:02d}', model] for i in range(18)] + [['REQ', model, week] for week in weeks]
            assert sorted(value['effects']) == sorted(effects)
            assert len(effects) == (34 if model else 0)
            result = value['evaluation']; gap = len(gap_models)
            assert all(result[key] for key in ('capacity_valid', 'computation_complete', 'executable'))
            assert {key: result['output_totals'][key] for key in ('expected', 'on_time', 'late', 'gap', 'pending', 'unallocated', 'outside_window')} == {'expected': 420000-gap, 'on_time': 420000-gap, 'late': 0, 'gap': gap, 'pending': 0, 'unallocated': 0, 'outside_window': 0}
            weekly = result['weekly_results']
            assert len(weekly) == 600 and {(row['model_code'], row['week']) for row in weekly} == {(f'M{i:02d}', week) for i in range(40) for week in weeks}
            for row in weekly:
                affected = row['model_code'] in gap_models and row['week'] == '2027-01-18'
                assert (row['demand'], row['on_time'], row['gap'], row['late'], row['pending']) == (700, 699 if affected else 700, int(affected), 0, 0)
            vector = ['0']*30 + ['418', str(gap), '0']; vector[14] = vector[29] = str(gap)
            assert objective(result) == vector
            assert call('/plans/evaluate', {'snapshot': value['snapshot']}) == result
            assert_protected(source, value['plan'])
        login('ACCEPTANCE0')
        before = call('/jobs/source'); retain('before-source.json', before)
        assert before['snapshot'] == source
        before_records = {path: call(path) for path in ('/plans/current', '/plans/history', '/plans/revisions/1', '/plans/adoptions/1', '/inputs?version=1')}
        a, b = command('M00'), command('M39')
        retain('commands.json', {'primary': a, 'background': b})
        login('ACCEPTANCE1')
        background = call('/plans/preview', b); retain('background-r1.json', background)
        checked(background, source['plan'], 'M39', {'M39'}, 1, 1)
        login('ACCEPTANCE0')
        primary = call('/plans/preview', a); retain('primary-r1.json', primary)
        checked(primary, source['plan'], 'M00', {'M00'}, 1, 1)
        s0 = a | {'operation_id': str(uuid4()), 'pending': False}
        # Real public commit; injector truth stays separate from reconciliation.
        injector = call('/plans/save', s0); retain('injector-only.json', injector)
        def reconcile(original_id):
            return call('/plans/verify', {'operation_id': original_id})
        saved = reconcile(s0['operation_id'])
        assert saved == {'status': 'COMMITTED', 'revision': 2, 'input_version': 1, 'latest_adoption': 1, 'pending': False}
        revision2, epoch1 = call('/plans/revisions/2'), call('/jobs/source')
        assert revision2['snapshot'] == source | {'plan': primary['plan']}
        assert revision2['operations'] == a['operations']
        assert epoch1 == before | {'source_revision': 2, 'snapshot': source | {'plan': primary['plan']}}
        retain('saved-source.json', epoch1)
        login('ACCEPTANCE1')
        merged = call('/plans/preview', b); retain('background-r2.json', merged)
        checked(merged, primary['plan'], 'M39', {'M00', 'M39'}, 2, 1)
        undo2 = call('/plans/preview', b | {'operations': []}); retain('undo-r2.json', undo2)
        checked(undo2, primary['plan'], None, {'M00'}, 2, 1)
        call('/plans/preview', a, status=409)
        assert call('/plans/revisions/1') == before_records['/plans/revisions/1']
        login('ACCEPTANCE0')
        t0 = {'base_revision': 2, 'input_version': 1, 'latest_adoption': 1, 'operation_id': str(uuid4()), 'pending': False, 'operations': [{'kind': 'replace_plan', 'plan': source['plan']}]}
        restored = call('/plans/save', t0)
        assert restored['status'] == 'COMMITTED' and restored['revision'] == 3
        assert reconcile(t0['operation_id']) == restored
        assert reconcile(s0['operation_id']) == saved
        assert call('/plans/save', s0) == saved
        assert s0['operation_id'] != t0['operation_id']
        revision3 = call('/plans/revisions/3')
        assert revision3['snapshot'] == source and revision3['operations'] == t0['operations']
        login('ACCEPTANCE1')
        undo3 = call('/plans/preview', b | {'operations': []}); retain('undo-r3.json', undo3)
        checked(undo3, source['plan'], None, set(), 3, 1)
        assert call('/jobs/source') == before | {'source_revision': 3}
        assert call('/plans/revisions/1') == before_records['/plans/revisions/1']
        assert call('/plans/revisions/2') == revision2
        assert call('/plans/adoptions/1') == before_records['/plans/adoptions/1']
        assert call('/inputs?version=1') == before_records['/inputs?version=1']
        current = call('/plans/current')
        assert current['context'] == before_records['/plans/current']['context']
        assert current['plan'] == source['plan']
        assert [row['revision'] for row in call('/plans/history')['items']] == [3, 2, 1]
        assert plans.revision == 3 and plans.adoption == inputs.version == 1
        assert set(plans.keys) == {s0['operation_id'], t0['operation_id']} and jobs.jobs == {}
        assert app.state.job_controller is None
        retain('closure.json', {'status': 'pass', 'source': before, 'save_uuid': s0['operation_id'], 'restore_uuid': t0['operation_id'], 'revision': 3, 'input_version': 1, 'latest_adoption': 1, 'jobs': [], 'acceptance': 'not_run', 'repeats': []})
        assert client.post('/planning/api/auth/logout').status_code == 200
