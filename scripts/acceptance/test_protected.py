"""Protected public preparation retains the frozen facts and confirmations."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5
import pytest

from scripts.acceptance.assertions import assert_frozen_snapshot
from scripts.acceptance.compare import assert_reference
from scripts.acceptance.validate import load, public_client

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'docs/acceptance/frozen'


def test_partial_material_preparation_retains_original_and_restored_inputs(tmp_path):
    from scripts.acceptance.protected import prepare_public
    from scripts.acceptance.local_v2 import OutcomeUnknown

    frozen_bytes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in FIXTURES.iterdir() if p.is_file()}
    scenario = next(r for r in json.loads((FIXTURES / 'manifest.json').read_text())['scenarios']
                    if r['id'] == 'combined-a-partial')
    source = load(FIXTURES, scenario['source'])
    output = tmp_path / 'prepared'
    with public_client(source, empty=True) as client:
        requests, lost = [], False

        def call(path, payload=None):
            nonlocal lost
            response = (client.get('/auto_plan/api' + path) if payload is None
                        else client.post('/auto_plan/api' + path, json=payload))
            assert response.status_code == 200, (path, response.status_code, response.text[:400])
            requests.append((path, deepcopy(payload), response.json()))
            if path == '/plans/adopt' and not lost:
                lost = True
                raise OutcomeUnknown
            return response.json()

        config = {'scenario': scenario['id'], 'target': {'kind': 'adapter', 'disposable': True, 'production': False}}
        report = prepare_public(call, FIXTURES, config, output)
        assert report['status'] == 'pass'
        state = json.loads((output / 'state.json').read_text())
        assert_frozen_snapshot(state['snapshot'], source)
        assert state['head']['latest_adoption'] == 1
        assert {(r['date'], r['line_code'], r['model_code'], r['quantity']) for r in state['snapshot']['locks']} == {
            ('2026-10-30', 'L00', 'M00', 800), ('2026-10-30', 'L02', 'M00', 0)}
        adopted = next(r for r in report['operations'] if r['kind'] == 'adopt_protected_plan')
        assert adopted['unknown_outcome_reconciled'] is True
        assert adopted['verification']['status'] == 'COMMITTED'
        assert sum(path == '/plans/adopt' for path, _, _ in requests) == 1
        assert len({r['operation_id'] for r in report['operations'] if 'operation_id' in r}) == 2
        initial_id = report['operations'][0]['operation_id']
        file_id = uuid5(UUID(initial_id), 'demand-file')
        files = call('/demands/files')['files']
        assert len(files) == 1 and files[0]['file_id'] == str(file_id) and files[0]['version'] == 1 and files[0]['effective'] is True
        assert len(files[0]['rows']) == 15
        for i, row in enumerate(files[0]['rows']):
            assert row['row_id'] == str(uuid5(file_id, str(i))) and row['ordinal'] == i and row['source_row'] == i + 2
        assert [r['delta'] for r in files[0]['rows']] == [600, 400] + [0] * 13
        writes = [body for path, body, _ in requests if path == '/inputs/apply']
        assert len(writes) == 2
        changed_rows = [body['changes']['confirmations'] for body in writes]
        assert [next(r['quantity'] for r in rows if r['kind'] == 'material') for rows in changed_rows] == [429, 428]
        versions = [value['input_version'] for path, _, value in requests if path == '/inputs/apply']
        temporary = call('/inputs?version=' + str(versions[0]))
        final = call('/inputs?version=' + str(versions[1]))
        original = call('/inputs?version=1')
        assert next(r['quantity'] for r in temporary['business']['confirmations'] if r['kind'] == 'material') == 429
        assert next(r['quantity'] for r in original['business']['confirmations'] if r['kind'] == 'material') == 428
        assert final == state['snapshot']['inputs']
        assert len([r for r in state['snapshot']['execution']['facts'] if r['quantity'] == 600]) == 1
        assert call('/plans/adoptions/1') == json.loads((output / 'adoption.json').read_text())
        assert_reference(call('/plans/evaluate', {'snapshot': load(FIXTURES, scenario['reference'])}), scenario)
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in FIXTURES.iterdir() if p.is_file()} == frozen_bytes


def test_whole_week_ban_preparation_restores_ban_and_original_wip(tmp_path):
    from scripts.acceptance.protected import prepare_public
    scenario_id = 'combined-b-ban'
    scenario = next(r for r in json.loads((FIXTURES / 'manifest.json').read_text())['scenarios'] if r['id'] == scenario_id)
    source = load(FIXTURES, scenario['source'])
    with public_client(source, empty=True) as client:
        writes = []
        def call(path, payload=None):
            response = (client.get('/auto_plan/api' + path) if payload is None
                        else client.post('/auto_plan/api' + path, json=payload))
            assert response.status_code == 200, (path, response.status_code, response.text[:400])
            if path == '/inputs/apply': writes.append(deepcopy(payload))
            return response.json()
        output = tmp_path / 'ban'
        report = prepare_public(call, FIXTURES, {'scenario': scenario_id, 'target': {'kind': 'adapter', 'disposable': True, 'production': False}}, output)
        assert report['status'] == 'pass'
        state = json.loads((output / 'state.json').read_text())
        assert_frozen_snapshot(state['snapshot'], source)
        assert [r['changes']['confirmations'][0]['status'] for r in writes] == ['revoked', 'forbidden']
        assert all(r['changes']['confirmations'][0].get('quantity') is None for r in writes)
        assert state['snapshot']['reset']['models'][0]['wip'] == 3
        assert state['snapshot']['execution']['associations'] == [{'quantity': 1, 'segment_id': 'combined-L00'}]
        assert [(r['line_code'], r['quantity']) for r in state['snapshot']['locks']] == [('L00', 1), ('L01', 0)]
        current_batch = next(r for r in state['snapshot']['inputs']['business']['confirmations'] if r['kind'] == 'material')
        assert current_batch['batch_id'] == 'whole-week-ban' and current_batch['status'] == 'forbidden'
        assert call('/plans/adoptions/1') == json.loads((output / 'adoption.json').read_text())
        assert all(r['verification']['status'] == 'COMMITTED' for r in report['operations'] if 'operation_id' in r)
        assert_reference(call('/plans/evaluate', {'snapshot': load(FIXTURES, scenario['reference'])}), scenario)
@pytest.mark.parametrize('scenario_id', ['combined-a-confirmed', 'combined-b-confirmed'])
def test_confirmed_protected_sources_adopt_without_temporary_input_changes(tmp_path, scenario_id):
    from scripts.acceptance.protected import prepare_public
    scenario = next(r for r in json.loads((FIXTURES / 'manifest.json').read_text())['scenarios'] if r['id'] == scenario_id)
    source = load(FIXTURES, scenario['source'])
    with public_client(source, empty=True) as client:
        writes = []
        def call(path, payload=None):
            response = (client.get('/auto_plan/api' + path) if payload is None
                        else client.post('/auto_plan/api' + path, json=payload))
            assert response.status_code == 200, (path, response.status_code, response.text[:400])
            if payload is not None: writes.append((path, deepcopy(payload)))
            return response.json()
        output = tmp_path / scenario_id
        report = prepare_public(call, FIXTURES, {'scenario': scenario_id, 'target': {'kind': 'adapter', 'disposable': True, 'production': False}}, output)
        actual = json.loads((output / 'state.json').read_text())
        assert_frozen_snapshot(actual['snapshot'], source)
        assert [r['kind'] for r in report['operations']] == ['initialize_protected_source', 'adopt_protected_plan']
        assert all(r['verification']['status'] == 'COMMITTED' for r in report['operations'])
        assert [path for path, _ in writes].count('/inputs/apply') == 0
        assert actual['head']['latest_adoption'] == 1
        assert actual['snapshot']['locks'] == source['locks']
        assert call('/plans/adoptions/1') == json.loads((output / 'adoption.json').read_text())
        assert_reference(call('/plans/evaluate', {'snapshot': load(FIXTURES, scenario['reference'])}), scenario)
