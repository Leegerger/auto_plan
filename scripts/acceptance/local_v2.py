"""Freeze local companions and prepare their authentic history through public APIs."""
import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
V1_MANIFEST_SHA256 = '33e765b2cc22a28116d0416f04cc05cd628f5239183160be32a143035d29b0f4'
LOCAL_IDS = tuple(f'r{count}-{band}-{sign}' for count in (10, 20, 40)
                  for band in ('near', 'far') for sign in ('increase', 'decrease'))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def asset(root, pointer):
    data = gzip.decompress((root / pointer['path']).read_bytes())
    assert hashlib.sha256(data).hexdigest() == pointer['sha256'], 'Frozen asset hash mismatch.'
    return json.loads(data)


def write_assets(v1, output):
    """Write a new family; V1 assets and existing output are never overwritten."""
    v1, output = Path(v1), Path(output)
    assert v1.resolve() != output.resolve() and v1.resolve() not in output.resolve().parents, 'Output must be separate from V1.'
    raw = (v1 / 'manifest.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == V1_MANIFEST_SHA256, 'Unreviewed V1 manifest.'
    original = json.loads(raw)
    scenarios = {r['id']: r for r in original['scenarios']}
    manifest = {key: deepcopy(value) for key, value in original.items() if key != 'scenarios'}
    manifest.update(fixture_family='local-v2', predecessor_manifest_sha256=V1_MANIFEST_SHA256, scenarios=[])
    output.mkdir(parents=True, exist_ok=False)

    def freeze(value):
        data = canonical(value)
        digest = hashlib.sha256(data).hexdigest()
        path = digest + '.json.gz'
        (output / path).write_bytes(gzip.compress(data, mtime=0))
        return {'path': path, 'sha256': digest, 'canonical_bytes': len(data)}

    for ident in LOCAL_IDS:
        predecessor = scenarios[ident]
        loose = scenarios[ident.split('-')[0] + '-loose']
        source, reference = (asset(v1, predecessor[name]) for name in ('source', 'reference'))
        original_source = asset(v1, loose['source'])
        changed = deepcopy(source)
        changed['inputs']['business']['demands'] = original_source['inputs']['business']['demands']
        assert changed == original_source, 'Local predecessor changes more than current demands.'
        assert len(source['inputs']['business']['demands']) == 600
        replacement = {'input_version': 1, 'attribution_date': source['time']['window_start'],
                       'rows': [{'model_code': row['model_code'], 'week': row['week'], 'delta': row['quantity'], 'source_row': i + 2}
                                for i, row in enumerate(source['inputs']['business']['demands'])]}
        for snapshot in (source, reference):
            snapshot['inputs']['business']['demands'].sort(key=lambda row: (row['model_code'], row['week']))
        row = deepcopy(predecessor)
        row.update(id=ident + '-v2', source=freeze(source), reference=freeze(reference),
                   action=freeze(asset(v1, predecessor['action'])))
        row['import'] = freeze(asset(v1, loose['import']))
        row['preparation_source'] = freeze(original_source)
        row['demand_replacement'] = freeze(replacement)
        row['predecessor'] = {'scenario': ident, 'manifest_sha256': V1_MANIFEST_SHA256,
                              **{name + '_sha256': predecessor[name]['sha256'] for name in ('source', 'reference', 'action', 'import')}}
        row['public_preparation'] = {
            'schema_version': 1, 'initial_scenario': loose['id'],
            'sequence': ['initialize_original_demand', 'adopt_exact_original_plan', 'replace_complete_same_day_demand_file'],
            'materialized_demand_order': ['model_code', 'week'],
            'requires_original_and_replacement_file_history': True,
            'status': 'not_run',
        }
        row['notes'] += ['V2 retains V1 business values, scope, reference objective and gates.',
                         'Public original-demand adoption precedes the complete same-day replacement; immutable file history is required.',
                         'Only materialized demand row ordering changes in the source/reference snapshots. V1 is preserved.']
        manifest['scenarios'].append(row)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


class OutcomeUnknown(Exception):
    """A public write may have committed; reconcile its original UUID."""


def prepare_public(call, fixtures, config, output):
    """Use an authenticated public JSON caller; return passed prep only after readback.

    The caller implements call(path, payload=None). It must raise OutcomeUnknown
    for lost transport replies or the public unknown-outcome response. It must
    never persist login bodies, cookies or headers. This is not timed acceptance.
    """
    from scripts.acceptance.assertions import assert_frozen_snapshot
    from scripts.acceptance.compare import numeric
    from auto_plan.plan_models import Plan
    fixtures, output = Path(fixtures), Path(output)
    manifest_bytes = (fixtures / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest.get('fixture_family') == 'local-v2' and manifest.get('predecessor_manifest_sha256') == V1_MANIFEST_SHA256, 'Wrong companion family.'
    scenario = next(r for r in manifest['scenarios'] if r['id'] == config['scenario'])
    assert scenario['predecessor']['scenario'] in LOCAL_IDS, 'Unknown local predecessor.'
    source, original, imported, replacement = (asset(fixtures, scenario[name]) for name in ('source', 'preparation_source', 'import', 'demand_replacement'))
    original_plan = Plan.model_validate(original['plan']).model_dump(mode='json')
    target = config['target']
    assert target['disposable'] is True and target['production'] is False and target['kind'] in ('adapter', 'PLAN_TEST'), 'Disposable test target required.'
    if target['kind'] == 'PLAN_TEST':
        assert re.fullmatch(r'AP_TEST_[A-F0-9]{6}', target['namespace']), 'Invalid disposable namespace.'
    output.mkdir(parents=True, exist_ok=False)
    report = {'kind': 'public API preparation; not timed acceptance', 'scenario': scenario['id'], 'target': target,
              'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(), 'source_sha256': scenario['source']['sha256'],
              'predecessor': scenario['predecessor'], 'public_api_only': True, 'status': 'in_progress', 'operations': []}

    def retain(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + '\n')

    def persist():
        retain('report.json', report)

    def phase(name):
        report['phase'] = name
        persist()

    def commit(path, verification, payload, kind):
        key = str(uuid4())
        command = payload | {'operation_id': key}
        operation = {'kind': kind, 'operation_id': key, 'request': command, 'request_sha256': fingerprint(command),
                     'status': 'prepared', 'unknown_outcome_reconciled': False}
        report['operations'].append(operation)
        persist()
        try:
            result = call(path, command)
        except OutcomeUnknown:
            operation['status'] = 'unknown'
            persist()
            result = call(verification, {'operation_id': key})
            operation['unknown_outcome_reconciled'] = True
        operation['result'] = result
        persist()
        assert result['status'] == 'COMMITTED', 'Original operation did not commit; preserve and reconcile before retry.'
        verified = call(verification, {'operation_id': key})
        operation['verification'] = verified
        assert verified == result, 'Original UUID verification differs from acknowledged commit.'
        operation['status'] = 'COMMITTED'
        persist()
        return result

    def state(head=None):
        head = call('/plans/current') if head is None else head
        inputs = call('/inputs?version=' + str(head['input_version']))
        baseline = None
        if head['latest_adoption']:
            adopted = call('/plans/adoptions/' + str(head['latest_adoption']))
            baseline = {'version': head['latest_adoption'], 'plan': adopted['snapshot']['plan'], 'coverage': adopted['coverage']}
        context = head['context']
        return {'head': head, 'snapshot': {'time': head['time'], 'inputs': inputs, 'plan': head['plan'],
                'execution': context['execution'], 'reset': context['reset'], 'locks': context['locks'],
                'baseline': baseline, 'preferences': context['preferences']}}

    def assert_file(record, rows, operation_id, version):
        identity = uuid5(UUID(operation_id), 'demand-file')
        assert record['file_id'] == str(identity) and record['version'] == version, 'Demand file identity/version differs.'
        assert record['attribution_date'] == replacement['attribution_date'], 'Demand file attribution differs.'
        expected = [dict(row, week_label=None, row_id=str(uuid5(identity, str(i))), ordinal=i) for i, row in enumerate(rows)]
        assert record['rows'] == expected, 'Complete immutable demand file rows differ.'

    persist()
    try:
        phase('empty_target')
        before = state()
        retain('initial-state.json', before)
        head = before['head']
        assert head['revision'] == head['latest_adoption'] == 0, 'Preparation requires an empty target.'
        assert head['time'] == original['time'], 'Controlled clock differs from the frozen source.'
        assert call('/demands/files')['files'] == [], 'Fresh target already has demand history.'
        phase('initialize_original_demand')
        imported.update(base_revision=head['revision'], input_version=head['input_version'], latest_adoption=head['latest_adoption'])
        preview = call('/initialization/preview', imported)
        retain('initial-preview.json', preview)
        assert preview['valid'] and preview['evaluation']['executable'], 'Original-demand initialization is not executable.'
        commit('/initialization/apply', '/plans/verify', imported, 'initialize_original_demand')
        phase('adopt_exact_original_plan')
        head = call('/plans/current')
        command = {'base_revision': head['revision'], 'input_version': head['input_version'], 'latest_adoption': head['latest_adoption'],
                   'operations': [{'kind': 'replace_plan', 'plan': original['plan']}]}
        preview = call('/plans/preview', command)
        retain('exact-original-preview.json', preview)
        assert all(preview['evaluation'][field] for field in ('capacity_valid', 'computation_complete', 'executable')) and not preview['adoption_blocked'], 'Exact original Plan is not adoptable.'
        adopted = commit('/plans/adopt', '/plans/verify', command | {'pending': False}, 'adopt_exact_original_plan')
        before = state()
        retain('before-demand.json', before)
        assert_frozen_snapshot(before['snapshot'], original)
        baseline = call('/plans/adoptions/' + str(adopted['latest_adoption']))
        retain('adoption.json', baseline)
        assert numeric(baseline['snapshot']['plan']) == numeric(original_plan), 'Adopted original Plan differs.'
        assert baseline['coverage'] == original['baseline']['coverage'], 'Adopted original coverage differs.'
        initial_files = call('/demands/files')
        retain('initial-demand-files.json', initial_files)
        assert len(initial_files['files']) == 1, 'Initial demand history is incomplete.'
        initial_rows = [{'model_code': row['model_code'], 'week': row['week'], 'delta': row['quantity'], 'source_row': i + 2}
                        for i, row in enumerate(imported['demands'])]
        assert_file(initial_files['files'][0], initial_rows, report['operations'][0]['operation_id'], 1)
        phase('replace_complete_same_day_demand_file')
        replacement['input_version'] = before['head']['input_version']
        assert len(replacement['rows']) == 600, 'Replacement must contain all 600 demand rows.'
        preview = call('/demands/preview', replacement)
        retain('demand-preview.json', preview)
        assert preview['valid'], 'Complete same-day demand replacement is invalid.'
        assert preview['demands'] == source['inputs']['business']['demands'], 'Materialized demand order/values differ.'
        assert numeric(preview['plan']) == numeric(original_plan), 'Demand preview changed the original Plan.'
        changed = commit('/demands/apply', '/demands/verify', replacement, 'replace_complete_same_day_demand_file')
        phase('fresh_complete_source_and_history')
        actual = state()
        retain('state.json', actual)
        assert actual['head']['revision'] == before['head']['revision'] and actual['head']['latest_adoption'] == before['head']['latest_adoption'], 'Demand replacement changed the original Plan/adoption.'
        assert actual['head']['input_version'] == changed['input_version'] == before['head']['input_version'] + 1, 'Demand replacement input version differs.'
        assert_frozen_snapshot(actual['snapshot'], source)
        assert call('/plans/adoptions/' + str(adopted['latest_adoption'])) == baseline, 'Original immutable adoption changed.'
        files = call('/demands/files')
        retain('demand-files.json', files)
        assert len(files['files']) == 2 and files['input_version'] == changed['input_version'], 'Complete two-file history is missing.'
        first, second = files['files']
        assert_file(first, initial_rows, report['operations'][0]['operation_id'], 1)
        assert_file(second, replacement['rows'], report['operations'][2]['operation_id'], 2)
        assert first['effective'] is False and second['effective'] is True, 'Same-day whole-file replacement did not supersede only the original file.'
        assert actual['head']['lineage']['demand_initial_file'] == first['file_id'], 'Original demand lineage was lost.'
        prepared = deepcopy(config)
        prepared['fixtures'] = str(fixtures.resolve())
        prepared['bindings'] = {'source_revision': actual['head']['revision'], 'input_version': actual['head']['input_version'], 'latest_adoption': actual['head']['latest_adoption']}
        retain('run-config.json', prepared)
        retain('head.json', actual['head'])
        report.update(status='pass', source_assertion='pass', actual_tokens=prepared['bindings'], phase='complete')
        persist()
        return report
    except Exception as error:
        report.update(status='fail', failure_type=type(error).__name__)
        persist()
        retain('failure.json', {'status': 'fail', 'phase': report['phase'], 'reason': type(error).__name__})
        raise


def request_json(client, prefix, path, payload=None):
    """Call the public HTTP boundary without logging private payloads."""
    import httpx
    try:
        response = client.get(prefix + path) if payload is None else client.post(prefix + path, json=payload)
    except httpx.TransportError:
        raise OutcomeUnknown from None
    if response.status_code == 408 or 500 <= response.status_code <= 599:
        raise OutcomeUnknown
    assert response.status_code == 200, 'Public business request failed.'
    return response.json()


def prepare_external(config, fixtures, output, credentials):
    import httpx
    origin = urlsplit(config['origin'])
    assert origin.scheme == 'https' and origin.netloc and not origin.username and not origin.password and not origin.path and not origin.query and not origin.fragment
    assert config['base_path'].startswith('/') and not config['base_path'].endswith('/'), 'Invalid public application prefix.'
    prefix = config['origin'] + config['base_path'] + '/api'
    verify = config.get('tls_ca_file') or not config.get('allow_test_certificate', False)
    with httpx.Client(verify=verify, timeout=180, headers={'Origin': config['origin'], 'X-Auto-Plan-Request': '1'}) as client:
        account = credentials['accounts'][0]
        response = client.post(prefix + '/auth/login', json={'username': account['username'], 'password': account['password']})
        assert response.status_code == 200, 'Public login failed.'
        account['password'] = ''

        def call(path, payload=None):
            return request_json(client, prefix, path, payload)

        return prepare_public(call, fixtures, config, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    freeze = commands.add_parser('freeze')
    freeze.add_argument('--v1', type=Path, default=ROOT / 'docs/acceptance/frozen')
    freeze.add_argument('--output', type=Path, required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--config', type=Path, required=True)
    prepare.add_argument('--fixtures', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'freeze':
            write_assets(args.v1, args.output)
        else:
            prepare_external(json.loads(args.config.read_text()), args.fixtures, args.output, json.load(sys.stdin))
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
