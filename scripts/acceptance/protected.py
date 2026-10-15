"""Prepare protected frozen sources through public import, locks and adoption."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from scripts.acceptance.assertions import assert_frozen_snapshot
from scripts.acceptance.local_v2 import request_json
from scripts.acceptance.public_preparation import PublicPreparation, validate_target
from scripts.acceptance.validate import load

ROOT = Path(__file__).resolve().parents[2]
V1_MANIFEST = '33e765b2cc22a28116d0416f04cc05cd628f5239183160be32a143035d29b0f4'


def prepare_public(call, fixtures, config, output):
    """Prepare a synthetic protected source; retain each real public operation."""
    fixtures = Path(fixtures)
    manifest_bytes = (fixtures / 'manifest.json').read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == V1_MANIFEST, 'Immutable V1 manifest differs.'
    assert config['scenario'] in ('combined-a-partial', 'combined-a-confirmed', 'combined-b-confirmed', 'combined-b-ban'), 'This preparation recipe is not qualified for that scenario.'
    scenario = next(r for r in json.loads(manifest_bytes)['scenarios'] if r['id'] == config['scenario'])
    source, imported = (load(fixtures, scenario[key]) for key in ('source', 'import'))
    preparation = PublicPreparation(call, config, output, manifest_sha256=V1_MANIFEST,
                                    source_sha256=scenario['source']['sha256'])
    try:
        preparation.phase('empty_target')
        before = preparation.state()
        preparation.retain('initial-state.json', before)
        head = before['head']
        assert head['revision'] == head['latest_adoption'] == 0, 'Preparation requires an empty target.'
        assert head['time']['as_of'] == source['time']['as_of'], 'Controlled source clock differs.'
        assert call('/demands/files')['files'] == [], 'Fresh target already has demand history.'

        preparation.phase('initialize_protected_source')
        imported.update(base_revision=head['revision'], input_version=head['input_version'], latest_adoption=head['latest_adoption'])
        preview = call('/initialization/preview', imported)
        preparation.retain('initial-preview.json', preview)
        assert preview['valid'], 'Frozen protected first import is invalid.'
        preparation.commit('/initialization/apply', imported, 'initialize_protected_source')
        initial = preparation.state()
        preparation.retain('imported-state.json', initial)
        files = call('/demands/files')
        preparation.retain('initial-demand-files.json', files)
        assert len(files['files']) == 1 and files['files'][0]['effective'], 'Original complete demand file is missing.'
        file = files['files'][0]
        identity = uuid5(UUID(preparation.report['operations'][0]['operation_id']), 'demand-file')
        rows = [{'model_code': row['model_code'], 'week': row['week'], 'delta': row['quantity'], 'source_row': i + 2,
                 'week_label': None, 'row_id': str(uuid5(identity, str(i))), 'ordinal': i}
                for i, row in enumerate(imported['demands'])]
        assert file['file_id'] == str(identity) and file['version'] == 1 and file['rows'] == rows, 'Original immutable demand file identity/complete rows differ.'
        assert initial['head']['lineage']['demand_initial_file'] == str(identity), 'Original demand lineage is missing.'

        material = None
        if scenario['id'] == 'combined-a-partial':
            preparation.phase('temporary_confirmation_for_baseline')
            material = next(row for row in source['inputs']['business']['confirmations'] if row['batch_id'] == 'partial-material')
            assert material['kind'] == 'material' and material['quantity'] == 428 and material['status'] == 'allowed'
            temporary = material | {'quantity': 429}
            preparation.inputs([temporary], 'temporary_confirmation_for_baseline')
        elif scenario['id'] == 'combined-b-ban':
            preparation.phase('temporary_confirmation_for_baseline')
            material = next(row for row in source['inputs']['business']['confirmations'] if row['batch_id'] == 'whole-week-ban')
            assert material['kind'] == 'material' and material['status'] == 'forbidden' and material.get('quantity') is None
            preparation.inputs([material | {'status': 'revoked'}], 'temporary_confirmation_for_baseline')

        preparation.phase('adopt_protected_plan')
        head = call('/plans/current')
        command = {'base_revision': head['revision'], 'input_version': head['input_version'], 'latest_adoption': head['latest_adoption'],
                   'operations': [{'kind': 'replace_plan', 'plan': source['plan']}, {'kind': 'lock_cells', 'cells': source['locks']}]}
        preview = call('/plans/preview', command)
        preparation.retain('protected-adoption-preview.json', preview)
        assert all(preview['evaluation'][name] for name in ('capacity_valid', 'computation_complete', 'executable')) and not preview['adoption_blocked'], 'Protected witness cannot establish its baseline.'
        adopted = preparation.commit('/plans/adopt', command | {'pending': False}, 'adopt_protected_plan')
        adoption = call('/plans/adoptions/' + str(adopted['latest_adoption']))
        preparation.retain('adoption.json', adoption)

        if material is not None:
            preparation.phase('restore_frozen_confirmation')
            preparation.inputs([material], 'restore_frozen_confirmation')
        preparation.phase('fresh_complete_source_and_history')
        actual = preparation.state()
        preparation.retain('state.json', actual)
        assert_frozen_snapshot(actual['snapshot'], source)
        assert call('/plans/adoptions/' + str(adopted['latest_adoption'])) == adoption, 'Immutable protected adoption changed.'
        final_files = call('/demands/files')
        assert final_files['files'] == files['files'], 'Original demand file/history changed.'
        assert final_files['input_version'] == actual['head']['input_version'], 'Demand history response is not pinned to current inputs.'
        assert call('/inputs?version=' + str(initial['head']['input_version'])) == initial['snapshot']['inputs'], 'Original immutable input revision changed.'
        preparation.retain('demand-files.json', final_files)
        prepared = deepcopy(config)
        prepared['fixtures'] = str(fixtures.resolve())
        prepared['bindings'] = {'source_revision': actual['head']['revision'], 'input_version': actual['head']['input_version'], 'latest_adoption': actual['head']['latest_adoption']}
        preparation.retain('run-config.json', prepared)
        preparation.retain('head.json', actual['head'])
        preparation.report.update(status='pass', phase='complete', source_assertion='pass', actual_tokens=prepared['bindings'])
        preparation.persist()
        return preparation.report
    except Exception as error:
        preparation.failed(error)
        raise


def prepare_external(config, fixtures, output, credentials):
    import httpx
    validate_target(config)
    origin = urlsplit(config['origin'])
    assert origin.scheme == 'https' and origin.netloc and not any((origin.username, origin.password, origin.path, origin.query, origin.fragment))
    assert config['base_path'].startswith('/') and not config['base_path'].endswith('/')
    prefix = config['origin'] + config['base_path'] + '/api'
    verify = config.get('tls_ca_file') or not config.get('allow_test_certificate', False)
    with httpx.Client(verify=verify, timeout=180, headers={'Origin': config['origin'], 'X-Auto-Plan-Request': '1'}) as client:
        account = credentials['accounts'][0]
        response = client.post(prefix + '/auth/login', json={'username': account['username'], 'password': account['password']})
        assert response.status_code == 200, 'Public login failed.'
        account['password'] = ''
        return prepare_public(lambda path, payload=None: request_json(client, prefix, path, payload), fixtures, config, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'docs/acceptance/frozen')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare_external(json.loads(args.config.read_text()), args.fixtures, args.output, json.load(sys.stdin))
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
