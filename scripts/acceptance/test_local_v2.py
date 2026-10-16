"""Local companions preserve frozen business cases through public preparation."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from fastapi.testclient import TestClient

from scripts.acceptance.assertions import assert_frozen_snapshot
from scripts.acceptance.compare import assert_reference
from scripts.acceptance.validate import load

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / 'docs/acceptance/frozen'


@pytest.fixture(scope='module')
def companions(tmp_path_factory):
    from scripts.acceptance.local_v2 import write_assets
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in V1.iterdir() if p.is_file()}
    output = tmp_path_factory.mktemp('local-parent') / 'local-v2'
    write_assets(V1, output)
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in V1.iterdir() if p.is_file()} == before
    return output


def test_all_twelve_companions_keep_original_business_actions_and_v1_bytes(companions):
    from scripts.acceptance.local_v2 import write_assets
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in V1.iterdir() if p.is_file()}
    original = json.loads((V1 / 'manifest.json').read_text())
    frozen = json.loads((companions / 'manifest.json').read_text())
    expected = {f'r{count}-{band}-{sign}-v2' for count in (10, 20, 40)
                for band in ('near', 'far') for sign in ('increase', 'decrease')}
    assert {row['id'] for row in frozen['scenarios']} == expected
    assert frozen['gates_seconds'] == original['gates_seconds']
    assert frozen['gates_seconds']['local'] == 60
    assert frozen['repeats_per_mode'] == 5 and frozen['include_first_repeat'] is True
    for row in frozen['scenarios']:
        predecessor = next(r for r in original['scenarios'] if r['id'] == row['predecessor']['scenario'])
        assert row['expected'] == predecessor['expected']
        assert len(row['expected']['objective']) == 33
        assert row['action'] == predecessor['action']
        old = load(V1, predecessor['source'])
        source = load(companions, row['source'])
        assert len(source['plan']['segments']) == 4200
        assert all(r['quantity'] == 100 for r in source['plan']['segments'])
        assert source['baseline'] == old['baseline'] and source['plan'] == old['plan']
        assert source['inputs']['business']['demands'][0] == {'model_code': 'M00', 'week': '2026-10-12', 'quantity': 700}
        assert source['inputs']['business']['demands'][1]['model_code'] == 'M00'
        week = '2026-10-19' if '-near-' in row['id'] else '2026-11-30'
        quantity = 740 if '-increase-' in row['id'] else 660
        assert next(r['quantity'] for r in source['inputs']['business']['demands'] if (r['model_code'], r['week']) == ('M00', week)) == quantity
        reverted = deepcopy(source)
        reverted['inputs']['business']['demands'] = old['inputs']['business']['demands']
        assert reverted == old
        assert row['predecessor']['source_sha256'] == predecessor['source']['sha256']
        imported = load(companions, row['import'])
        assert len(imported['demands']) == 600 and all(r['quantity'] == 700 for r in imported['demands'])
        replacement = load(companions, row['demand_replacement'])
        assert replacement['attribution_date'] == '2026-10-12' and len(replacement['rows']) == 600
        expected_total = 420040 if quantity == 740 else 419960
        assert sum(r['delta'] for r in replacement['rows']) == expected_total
        assert row['source']['sha256'] != predecessor['source']['sha256']
        reference = load(companions, row['reference'])
        old_reference = load(V1, predecessor['reference'])
        reference['inputs']['business']['demands'] = old_reference['inputs']['business']['demands']
        assert reference == old_reference
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in V1.iterdir() if p.is_file()} == before
    with pytest.raises(FileExistsError):
        write_assets(V1, companions)


@pytest.mark.parametrize('scenario_id, lose_demand_reply', [
    ('r20-near-decrease-v2', True),
    ('r10-far-increase-v2', False),
])
def test_public_recipe_preserves_original_adoption_and_both_same_day_files(companions, tmp_path, scenario_id, lose_demand_reply):
    from auto_plan.app import create_app
    from auto_plan.settings import Settings
    from tests.support import MemoryAccessStore, CompanyAuthentication
    from tests.input_support import MemoryInputStore
    from tests.plan_support import MemoryPlanStore
    from tests.demand_support import MemoryDemandStore
    from scripts.acceptance.local_v2 import OutcomeUnknown, prepare_public

    scenario = next(r for r in json.loads((companions / 'manifest.json').read_text())['scenarios'] if r['id'] == scenario_id)
    source = load(companions, scenario['source'])
    inputs, access = MemoryInputStore(), MemoryAccessStore()
    plans = MemoryPlanStore(inputs)
    access.users['ACCEPTANCE0'] = {'user_id': 'ACCEPTANCE0', 'roles': ['editor'], 'active': True}
    app = create_app(Settings(public_origin='https://testserver'), store=access, input_store=inputs,
                     plan_store=plans, demand_store=MemoryDemandStore(plans),
                     authenticator=CompanyAuthentication(), clock=lambda: datetime.fromisoformat(source['time']['as_of']))
    output = tmp_path / 'prepared'
    with TestClient(app, base_url='https://testserver') as client:
        client.headers.update({'Origin': 'https://testserver', 'X-Auto-Plan-Request': '1'})
        assert client.post('/auto_plan/api/auth/login', json={'username': 'ACCEPTANCE0', 'password': 'company-secret'}).status_code == 200
        requests, lost = [], False

        def call(path, payload=None):
            nonlocal lost
            response = client.get('/auto_plan/api' + path) if payload is None else client.post('/auto_plan/api' + path, json=payload)
            assert response.status_code == 200, (path, response.status_code)
            requests.append((path, deepcopy(payload)))
            if path == '/demands/apply' and lose_demand_reply and not lost:
                lost = True
                raise OutcomeUnknown
            return response.json()

        config = {'scenario': scenario_id, 'target': {'kind': 'adapter', 'disposable': True, 'production': False}}
        report = prepare_public(call, companions, config, output)
        assert report['status'] == 'pass' and report['kind'] == 'public API preparation; not timed acceptance'
        assert [r['kind'] for r in report['operations']] == ['initialize_original_demand', 'adopt_exact_original_plan', 'replace_complete_same_day_demand_file']
        assert all(r['verification']['status'] == 'COMMITTED' for r in report['operations'])
        assert len({r['operation_id'] for r in report['operations']}) == 3
        assert report['operations'][-1]['unknown_outcome_reconciled'] is lose_demand_reply
        assert sum(path == '/demands/apply' for path, _ in requests) == 1
        assert all(path != '/inputs/apply' for path, _ in requests)
        before = json.loads((output / 'before-demand.json').read_text())
        actual = json.loads((output / 'state.json').read_text())
        assert before['head']['revision'] == actual['head']['revision'] == 2
        assert before['head']['latest_adoption'] == actual['head']['latest_adoption'] == 1
        assert actual['head']['input_version'] == before['head']['input_version'] + 1
        assert_frozen_snapshot(actual['snapshot'], source)
        files = call('/demands/files')['files']
        assert [(r['version'], r['effective'], len(r['rows'])) for r in files] == [(1, False, 600), (2, True, 600)]
        for record, operation in zip(files, (report['operations'][0], report['operations'][2])):
            identity = uuid5(UUID(operation['operation_id']), 'demand-file')
            assert record['file_id'] == str(identity)
            assert record['rows'][0]['row_id'] == str(uuid5(identity, '0'))
        assert actual['head']['lineage']['demand_initial_file'] == files[0]['file_id']
        assert json.loads((output / 'adoption.json').read_text()) == call('/plans/adoptions/1')
        wrong = call('/demands/preview', {'input_version': actual['head']['input_version'], 'attribution_date': '2026-10-12',
                                         'rows': [{'model_code': 'M00', 'week': '2026-10-19', 'delta': -40}]})
        assert wrong['valid'] is False
        assert call('/demands/files')['files'] == files
        prepared_config = json.loads((output / 'run-config.json').read_text())
        assert prepared_config['fixtures'] == str(companions.resolve())
        assert prepared_config['scenario'] == scenario_id
        reference = load(companions, scenario['reference'])
        assert_reference(call('/plans/evaluate', {'snapshot': reference}), scenario)
        with pytest.raises(AssertionError, match='empty'):
            prepare_public(call, companions, config, tmp_path / 'refuse-reuse')
        assert json.loads((tmp_path / 'refuse-reuse/failure.json').read_text())['status'] == 'fail'


@pytest.mark.parametrize('status', [408, 500, 502, 503, 504])
def test_proxy_write_reply_is_unknown_until_original_uuid_verification(status):
    import httpx
    from scripts.acceptance.local_v2 import OutcomeUnknown, request_json
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text='<html>proxy reply</html>'))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(OutcomeUnknown):
            request_json(client, 'https://testserver/auto_plan/api', '/demands/apply',
                         {'operation_id': '00000000-0000-4000-8000-000000000123'})
