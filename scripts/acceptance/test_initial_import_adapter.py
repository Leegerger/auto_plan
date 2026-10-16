"""Public source and history contracts for disposable browser preparation."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
from scripts.acceptance.adapter_server import application
from scripts.acceptance.validate import load


def test_never_initialized_adapter_keeps_plan_and_demand_history_empty(tmp_path):
    frozen = Path(__file__).resolve().parents[2] / 'docs/acceptance/frozen'
    parent = json.loads((frozen/'manifest.json').read_text())
    scenario = deepcopy(next(row for row in parent['scenarios'] if row['id'] == 'r20-loose'))
    source = load(frozen, scenario['source'])
    scenario['adapter_preparation'] = {'kind': 'never_initialized_inputs'}
    for name in ('source', 'reference', 'action', 'import'):
        destination = tmp_path/scenario[name]['path']
        if not destination.exists(): shutil.copy2(frozen/scenario[name]['path'], destination)
    (tmp_path/'manifest.json').write_text(json.dumps(parent | {'scenarios': [scenario]}))
    app = application(tmp_path, scenario['id'], 18869)
    with TestClient(app, base_url='https://localhost:18869') as client:
        client.headers.update({'Origin': 'https://localhost:18869', 'X-Auto-Plan-Request': '1'})
        assert client.post('/planning/api/auth/login', json={'username': 'ACCEPTANCE0', 'password': 'company-secret'}).status_code == 200
        response = client.get('/planning/api/plans/current'); assert response.status_code == 200
        head = response.json()
        assert head['revision'] == head['latest_adoption'] == 0, 'Declared never-initialized adapter exposed an initialized shared source.'
        assert head['plan']['segments'] == []
        history = client.get('/planning/api/demands/files'); assert history.status_code == 200
        assert history.json() == {'input_version': 1, 'enablement_date': None, 'files': []}
        inputs = client.get('/planning/api/inputs', params={'version': 1}); assert inputs.status_code == 200
        expected = deepcopy(source['inputs']); expected['input_version'] = 1; expected['business']['demands'] = []
        assert inputs.json() == expected


def test_default_adapter_preserves_complete_adopted_source(tmp_path):
    frozen = Path(__file__).resolve().parents[2] / 'docs/acceptance/frozen'
    parent = json.loads((frozen/'manifest.json').read_text())
    scenario = next(row for row in parent['scenarios'] if row['id'] == 'r20-loose')
    source = load(frozen, scenario['source'])
    app = application(frozen, scenario['id'], 18869)
    with TestClient(app, base_url='https://localhost:18869') as client:
        client.headers.update({'Origin': 'https://localhost:18869', 'X-Auto-Plan-Request': '1'})
        assert client.post('/planning/api/auth/login', json={'username': 'ACCEPTANCE0', 'password': 'company-secret'}).status_code == 200
        response = client.get('/planning/api/plans/current')
        assert response.status_code == 200
        head = response.json()
        assert head['revision'] == head['latest_adoption'] == 1
        assert head['kind'] == 'adopt' and not head['pending']
        assert head['plan'] == source['plan'] | {'current_day_witnesses': []}
        assert head['coverage'] == source['baseline']['coverage']
        response = client.get('/planning/api/inputs', params={'version': 1})
        assert response.status_code == 200
        assert response.json() == source['inputs']
