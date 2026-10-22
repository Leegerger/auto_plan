"""Prepared acceptance assets exercised at the public business API seam."""
import gzip
import json
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
from tests.scale_support import seeded_app

ROOT = Path(__file__).resolve().parents[2]


def test_prepared_loose_reference_is_complete_and_matches_public_evaluation(tmp_path):
    target = tmp_path / 'frozen'
    subprocess.run([sys.executable, str(ROOT / 'scripts/acceptance/prepare.py'), '--output', str(target)], check=True)
    manifest = json.loads((target / 'manifest.json').read_text())
    scenario = next(row for row in manifest['scenarios'] if row['id'] == 'r10-loose')
    with gzip.open(target / scenario['reference']['path'], 'rt') as source:
        snapshot = json.load(source)
    assert len(snapshot['plan']['segments']) == 4200
    assert len({row['line_code'] for row in snapshot['plan']['segments']}) == 18
    assert len(snapshot['inputs']['parameters']['line_yields']) == 200
    with TestClient(seeded_app(snapshot), base_url='https://testserver') as client:
        client.headers.update({'Origin': 'https://testserver', 'X-Auto-Plan-Request': '1'})
        assert client.post('/auto_plan/api/auth/login', json={'username': 'SCALEEDITOR', 'password': 'company-secret'}).status_code == 200
        response = client.post('/auto_plan/api/plans/evaluate', json={'snapshot': snapshot})
        assert response.status_code == 200, response.text
        actual = response.json()
        assert actual['capacity_valid'] and actual['computation_complete'] and actual['executable']
        assert actual['switch_count'] == 418
        assert {key: actual['output_totals'][key] for key in ('expected', 'on_time', 'late', 'gap', 'pending', 'unallocated', 'outside_window')} == {'expected': 420000, 'on_time': 420000, 'late': 0, 'gap': 0, 'pending': 0, 'unallocated': 0, 'outside_window': 0}
        assert all(row['on_time'] == 700 and row['gap'] == 0 for row in actual['weekly_results'])


def test_boundary_and_rolling_assets_preserve_independent_exact_witnesses(tmp_path):
    target = tmp_path / 'frozen'
    subprocess.run([sys.executable, str(ROOT / 'scripts/acceptance/prepare.py'), '--output', str(target)], check=True)
    manifest = json.loads((target / 'manifest.json').read_text())
    scenarios = {row['id']: row for row in manifest['scenarios']}
    assert scenarios['r40-boundary']['expected']['totals']['expected'] == 422310
    with gzip.open(target / scenarios['r40-boundary']['reference']['path'], 'rt') as source:
        boundary = json.load(source)
    row = next(r for r in boundary['plan']['segments'] if r['date'] == '2026-10-12' and r['model_code'] == 'M00')
    assert (row['quantity'], row['hours']) == (102, '76/75')
    assert scenarios['r20-partial-gap']['expected']['objective'] == ['0'] * 14 + ['40'] + ['0'] * 14 + ['40', '418', '0', '0']
    assert scenarios['r20-rolling']['expected']['objective'] == ['0'] * 30 + ['420', '0', '0']
    assert scenarios['r20-near-increase']['expected']['objective'] == ['0'] * 30 + ['418', '120', '0']
    assert scenarios['r20-far-decrease']['expected']['objective'] == ['0'] * 30 + ['418', '40', '0']
    for count in (10, 20, 40):
        for key_lines in (1, 2):
            assert f'r{count}-key{key_lines}' in scenarios


def test_combined_confirmation_and_execution_reference_matches_public_output(tmp_path):
    target = tmp_path / 'frozen'
    subprocess.run([sys.executable, str(ROOT / 'scripts/acceptance/prepare.py'), '--output', str(target)], check=True)
    manifest = json.loads((target / 'manifest.json').read_text())
    scenario = next(row for row in manifest['scenarios'] if row['id'] == 'combined-a-partial')
    with gzip.open(target / scenario['reference']['path'], 'rt') as source:
        snapshot = json.load(source)
    with TestClient(seeded_app(snapshot), base_url='https://testserver') as client:
        client.headers.update({'Origin': 'https://testserver', 'X-Auto-Plan-Request': '1'})
        assert client.post('/auto_plan/api/auth/login', json={'username': 'SCALEEDITOR', 'password': 'company-secret'}).status_code == 200
        result = client.post('/auto_plan/api/plans/evaluate', json={'snapshot': snapshot})
        assert result.status_code == 200, result.text
        assert {key: result.json()['output_totals'][key] for key in ('expected', 'on_time', 'late', 'gap', 'pending', 'unallocated', 'outside_window')} == {'expected': 1000, 'on_time': 899, 'late': 100, 'gap': 1, 'pending': 1, 'unallocated': 0, 'outside_window': 0}
        assert {r['demand_week']: r['apportioned_input'] for r in result.json()['attributions']} == {'2026-10-26': 643, '2026-11-02': 429}


def test_combined_wip_source_import_preserves_the_frozen_physical_quantity(tmp_path):
    from scripts.acceptance.validate import public_client, load
    target = tmp_path / 'frozen'
    subprocess.run([sys.executable, str(ROOT / 'scripts/acceptance/prepare.py'), '--output', str(target)], check=True)
    manifest = json.loads((target / 'manifest.json').read_text())
    scenario = next(row for row in manifest['scenarios'] if row['id'] == 'combined-b-ban')
    snapshot = load(target, scenario['source'])
    with public_client(snapshot, empty=True) as client:
        response = client.post('/auto_plan/api/initialization/preview', json=load(target, scenario['import']))
        assert response.status_code == 200 and response.json()['valid'] is True
        assert response.json()['context']['reset']['models'] == [{'model_code': 'M00', 'wip': 3, 'net_balance': 0}]
        assert response.json()['evaluation']['output_totals']['expected'] == 4


def test_external_adapter_retains_a_complete_adopted_history_instead_of_masking_the_view():
    from scripts.acceptance.adapter_server import application
    app=application(ROOT/'docs/acceptance/frozen','r20-loose',8776)
    with TestClient(app,base_url='https://localhost:8776') as client:
        client.headers.update({'Origin':'https://localhost:8776','X-Auto-Plan-Request':'1'})
        assert client.post('/planning/api/auth/login',json={'username':'ACCEPTANCE0','password':'company-secret'}).status_code==200
        history=client.get('/planning/api/plans/history')
        assert history.status_code==200
        assert history.json()['items'][0]['adoption']==1
        adoption=client.get('/planning/api/plans/adoptions/1')
        assert adoption.status_code==200
        record=adoption.json()
        assert len(record['snapshot']['plan']['segments'])==4200
        assert record['evaluation']['output_totals']['expected']==420000
        assert record['evaluation']['output_totals']['gap']==0
