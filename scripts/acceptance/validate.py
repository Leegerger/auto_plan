"""Check frozen references through the public API using explicit test adapters.

This establishes preparation correctness only, never Oracle or deployed timing.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.acceptance.compare import assert_reference


def load(root, pointer):
    raw = gzip.decompress((root / pointer['path']).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == pointer['sha256'], 'Frozen asset hash mismatch.'
    return json.loads(raw)


@contextmanager
def public_client(snapshot, empty=False):
    from fastapi.testclient import TestClient
    from tests.scale_support import seeded_app
    if empty:
        from auto_plan.app import create_app
        from auto_plan.settings import Settings
        from tests.input_support import MemoryInputStore
        from tests.plan_support import MemoryPlanStore
        from tests.support import MemoryAccessStore, CompanyAuthentication
        inputs, access = MemoryInputStore(), MemoryAccessStore()
        access.users['SCALEEDITOR'] = {'user_id': 'SCALEEDITOR', 'roles': ['editor'], 'active': True}
        app = create_app(Settings(public_origin='https://testserver'), store=access, input_store=inputs,
                         plan_store=MemoryPlanStore(inputs), authenticator=CompanyAuthentication(),
                         clock=lambda: datetime.fromisoformat(snapshot['time']['as_of']))
    else:
        app = seeded_app(snapshot)
    with TestClient(app, base_url='https://testserver') as client:
        client.headers.update({'Origin': 'https://testserver', 'X-Auto-Plan-Request': '1'})
        assert client.post('/auto_plan/api/auth/login', json={'username': 'SCALEEDITOR', 'password': 'company-secret'}).status_code == 200
        yield client


def validate(root, selected, output):
    output.mkdir(parents=True, exist_ok=False)
    raw = (root / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    report = {'kind': 'adapter preparation check; excludes Oracle, browser, queue, deployed resources and user confirmation',
              'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'results': []}
    for scenario in manifest['scenarios']:
        if selected != 'all' and scenario['id'] != selected:
            continue
        began = perf_counter()
        row = {'scenario': scenario['id'], 'reference_sha256': scenario['reference']['sha256']}
        try:
            for pointer in ('source', 'reference', 'action', 'import'):
                load(root, scenario[pointer])
            snapshot = load(root, scenario['reference'])
            with public_client(snapshot) as client:
                response = client.post('/auto_plan/api/plans/evaluate', json={'snapshot': snapshot})
                assert response.status_code == 200, f'Public evaluation returned {response.status_code}'
                evaluation = response.json()
                (output / (scenario['id'] + '.json.gz')).write_bytes(gzip.compress(response.content, mtime=0))
                row['observed_objective'] = assert_reference(evaluation, scenario)
                row['observed_totals'] = evaluation['output_totals']
            if scenario['kind'] == 'rolling':
                row['import_validation'] = {'status': 'not_run', 'reason': 'A true rolling state retains old demand history and an existing adopted plan; it cannot be a fresh first import.'}
            else:
                with public_client(snapshot, empty=True) as client:
                    response = client.post('/auto_plan/api/initialization/preview', json=load(root, scenario['import']))
                    result = response.json()
                    row['import_validation'] = {'status': 'pass' if response.status_code == 200 and result.get('valid') else 'fail',
                                                'errors': result.get('errors', []), 'http_status': response.status_code}
                    assert row['import_validation']['status'] == 'pass', 'Public source import validation failed.'
            row['status'] = 'pass'
        except Exception as error:
            row.update(status='fail', reason=str(error))
        row['preparation_seconds'] = perf_counter() - began
        report['results'].append(row)
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'scenario': row['scenario'], 'status': row['status'], 'preparation_seconds': row['preparation_seconds']}), flush=True)
    if not report['results']:
        raise ValueError('No selected scenario exists.')
    return all(row['status'] == 'pass' for row in report['results'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'docs/acceptance/frozen')
    parser.add_argument('--scenario', default='all')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if validate(args.fixtures, args.scenario, args.output) else 1)
