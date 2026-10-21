"""Prepare a disposable frozen shared source solely through public business APIs.

Supply one authorized test account as {"accounts":[{"username":...,"password":...}]}
through private stdin. Passwords and session cookies are never written to evidence.
"""
import argparse
import json
from pathlib import Path
import re
import sys
from uuid import uuid4
from urllib.parse import urlsplit
import httpx
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.acceptance.validate import load


def prepare(config, fixtures, output, credentials):
    origin = urlsplit(config['origin'])
    assert origin.scheme == 'https' and origin.netloc and not origin.username and not origin.password and not origin.path
    target = config['target']
    assert target['disposable'] is True and target['production'] is False
    assert target['kind'] in ('PLAN_TEST', 'adapter')
    assert target['kind'] != 'PLAN_TEST' or re.fullmatch(r'AP_TEST_[A-F0-9]{6}', target['namespace'])
    scenario = next(row for row in json.loads((fixtures / 'manifest.json').read_text())['scenarios'] if row['id'] == config['scenario'])
    assert scenario['kind'] != 'rolling', 'Rolling preparation needs the existing fourteen-week history and controlled clock advance.'
    source = load(fixtures, scenario['source'])
    assert not source['locks'], 'Protected companion preparation requires its explicit public context actions.'
    output.mkdir(parents=True, exist_ok=False)
    report = {'kind': 'public API preparation, not timed acceptance', 'scenario': scenario['id'], 'target': target,
              'source_sha256': scenario['source']['sha256'], 'operations': []}
    prefix = config['origin'] + config['base_path'] + '/api'
    headers = {'Origin': config['origin'], 'X-Auto-Plan-Request': '1'}
    verify = config.get('tls_ca_file') or not config.get('allow_test_certificate', False)
    account = credentials['accounts'][0]
    with httpx.Client(verify=verify, timeout=180, headers=headers) as client:
        def persist(): (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        def call(path, data=None):
            report['phase'] = path; persist()
            response = client.get(prefix + path) if data is None else client.post(prefix + path, json=data)
            assert response.status_code == 200, f'{path} returned HTTP {response.status_code}'
            return response.json()
        call('/auth/login', {'username': account['username'], 'password': account['password']})
        account['password'] = ''
        head = call('/plans/current')
        assert head['revision'] == head['latest_adoption'] == 0, 'Preparation requires an empty disposable shared plan.'
        assert head['time']['as_of'] == source['time']['as_of'], 'Controlled environment clock differs from the frozen source.'
        payload = load(fixtures, scenario['import'])
        payload.update(input_version=head['input_version'])
        preview = call('/initialization/preview', payload)
        assert preview['valid'], 'Frozen source import did not validate.'
        operation_id = str(uuid4())
        report['operations'].append({'kind': 'initialization', 'operation_id': operation_id, 'status': 'prepared'})
        persist()
        try:
            imported = call('/initialization/apply', payload | {'operation_id': operation_id})
        except httpx.TransportError:
            imported = call('/plans/verify', {'operation_id': operation_id})
        assert imported['status'] == 'COMMITTED', 'Import result requires operator resolution.'
        report['operations'][-1].update(imported); persist()
        head = call('/plans/current')
        command = {'base_revision': head['revision'], 'input_version': head['input_version'], 'latest_adoption': head['latest_adoption'],
                   'operations': [{'kind': 'replace_plan', 'plan': source['plan']}]}
        checked = call('/plans/preview', command)
        assert checked['evaluation']['capacity_valid'] and checked['evaluation']['computation_complete'] and checked['evaluation']['executable']
        operation_id = str(uuid4())
        report['operations'].append({'kind': 'adopt_exact_source_witness', 'operation_id': operation_id, 'status': 'prepared'})
        persist()
        try:
            adopted = call('/plans/adopt', command | {'operation_id': operation_id, 'pending': False})
        except httpx.TransportError:
            adopted = call('/plans/verify', {'operation_id': operation_id})
        assert adopted['status'] == 'COMMITTED', 'Adoption result requires operator resolution.'
        report['operations'][-1].update(adopted); persist()
        head = call('/plans/current')
        config['bindings'] = {'source_revision': head['revision'], 'input_version': head['input_version'], 'latest_adoption': head['latest_adoption']}
        (output / 'run-config.json').write_text(json.dumps(config, indent=2) + '\n')
        (output / 'head.json').write_text(json.dumps(head))
    report['status'] = 'pass'
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'docs/acceptance/frozen')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(json.loads(args.config.read_text()), args.fixtures, args.output, json.load(sys.stdin))
    except Exception as error:
        # HTTP/auth payloads and headers may contain secrets. Emit only the class.
        if args.output.exists():
            (args.output / 'failure.json').write_text(json.dumps({'status': 'fail', 'reason': str(error) if isinstance(error, AssertionError) else type(error).__name__}) + '\n')
        raise SystemExit(type(error).__name__)
