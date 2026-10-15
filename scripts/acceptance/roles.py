"""Check twenty prepared synthetic account role combinations through public APIs.

Private stdin contains {"accounts":[{"username":...,"password":...,"roles":[...],"active":true}, ...]}.
Only role labels, status codes and commit IDs enter evidence. Real-company-account
acceptance remains not_run. --save-probes optionally commits zero-change drafts.
"""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from uuid import uuid4
import httpx


def run(config, output, accounts, save_probes=False):
    origin, target = urlsplit(config['origin']), config['target']
    assert origin.scheme == 'https' and origin.netloc and not origin.username and not origin.path
    assert target['disposable'] is True and target['production'] is False
    assert target['kind'] in ('adapter', 'PLAN_TEST')
    assert target['kind'] != 'PLAN_TEST' or re.fullmatch(r'AP_TEST_[A-F0-9]{6}', target['namespace'])
    assert len(accounts) == 20 and len({r['username'].strip().upper() for r in accounts}) == 20
    assert all(set(row['roles']) <= {'viewer', 'editor', 'administrator'} for row in accounts)
    active_roles = {frozenset(row['roles']) for row in accounts if row.get('active', True)}
    assert {frozenset(['viewer']), frozenset(['editor']), frozenset(['administrator']), frozenset(['editor', 'administrator'])} <= active_roles, 'Include viewer-only, editor-only, administrator-only and mixed roles.'
    assert any(not row.get('active', True) for row in accounts) and any(not row['roles'] for row in accounts), 'Include inactive and ungranted synthetic accounts.'
    output.mkdir(parents=True, exist_ok=False)
    report = {'kind': 'twenty synthetic account public role matrix', 'target': target, 'checks': [],
              'write_probe': 'acknowledged zero-change draft save' if save_probes else 'editor preview plus denied save for noneditors',
              'real_company_accounts': {'status': 'not_run', 'reason': 'Prepared synthetic identities do not establish real company login, company-side stop behavior or twenty real users.'}}
    prefix = config['origin'] + config['base_path'] + '/api'
    verify = config.get('tls_ca_file') or not config.get('allow_test_certificate', False)
    headers = {'Origin': config['origin'], 'X-Auto-Plan-Request': '1'}
    for number, account in enumerate(accounts, 1):
        roles = set(account['roles'])
        row = {'account_index': number, 'expected_roles': sorted(roles), 'active': account.get('active', True), 'requests': [], 'status': 'fail'}
        try:
            with httpx.Client(verify=verify, timeout=180, headers=headers) as client:
                def call(method, path, expected, data=None):
                    response = client.request(method, prefix + path, json=data) if data is not None else client.request(method, prefix + path)
                    row['requests'].append({'method': method, 'path': path, 'expected_http': expected, 'observed_http': response.status_code})
                    assert response.status_code == expected, f'Unexpected HTTP status at {path}'
                    return response.json()
                permitted = bool(roles) and account.get('active', True)
                logged = call('POST', '/auth/login', 200 if permitted else 403, {'username': account['username'], 'password': account['password']})
                account['password'] = ''
                if permitted:
                    assert set(logged['roles']) == roles
                    assert set(call('GET', '/auth/me', 200)['roles']) == roles
                    viewing = bool(roles & {'viewer', 'editor'})
                    head = call('GET', '/plans/current', 200 if viewing else 403)
                    call('GET', '/admin/authorizations', 200 if 'administrator' in roles else 403)
                    tokens = {key: head[name] for key, name in [('base_revision', 'revision'), ('input_version', 'input_version'), ('latest_adoption', 'latest_adoption')]} if viewing else {
                        'base_revision': config['bindings']['source_revision'], 'input_version': config['bindings']['input_version'], 'latest_adoption': config['bindings']['latest_adoption']}
                    command = tokens | {'operations': []}
                    call('POST', '/plans/preview', 200 if 'editor' in roles else 403, command)
                    if save_probes or 'editor' not in roles:
                        operation_id = str(uuid4())
                        row['operation_id'] = operation_id
                        (output / 'report.json').write_text(json.dumps(report | {'pending_check': row}, indent=2) + '\n')
                        saved = call('POST', '/plans/save', 200 if 'editor' in roles else 403, command | {'operation_id': operation_id, 'pending': True})
                        if 'editor' in roles:
                            assert saved['status'] == 'COMMITTED'
                            row['committed'] = saved
                    call('POST', '/auth/logout', 200, {})
                    call('GET', '/auth/me', 401)
                row['status'] = 'pass'
        except Exception as error:
            row['reason'] = str(error) if isinstance(error, AssertionError) else type(error).__name__
        report['checks'].append(row)
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'account_index': number, 'status': row['status']}), flush=True)
    report['synthetic_result'] = 'pass' if all(row['status'] == 'pass' for row in report['checks']) else 'fail'
    report['acceptance'] = 'not_run'
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report['synthetic_result'] == 'pass'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--save-probes', action='store_true')
    args = parser.parse_args()
    try:
        result = run(json.loads(args.config.read_text()), args.output, json.load(sys.stdin)['accounts'], args.save_probes)
    except Exception as error:
        raise SystemExit(str(error) if isinstance(error, AssertionError) else type(error).__name__)
    raise SystemExit(0 if result else 1)
