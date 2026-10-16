"""Freeze a distinct first-import browser companion before actor activity.

The bootstrap and final approved manifests remain separate artifacts.
This prepares an opt-in functional probe, not a qualified timing workload.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil


def prepare(repo, output, template=None):
    from scripts.acceptance.validate import load
    from scripts.acceptance.input_companion import content_digest
    frozen = repo/'docs/acceptance/frozen'
    parent_bytes = (frozen/'manifest.json').read_bytes()
    assert hashlib.sha256(parent_bytes).hexdigest() == '986bffaad672f68add9b40ad76a15972b8354d5a9d0d9cf66593de0980ae94db'
    parent = json.loads(parent_bytes)
    original = next(row for row in parent['scenarios'] if row['id'] == 'r20-loose')
    output.mkdir(parents=True, exist_ok=False)
    for name in ('source', 'reference', 'action', 'import'):
        destination = output/original[name]['path']
        if not destination.exists(): shutil.copy2(frozen/original[name]['path'], destination)
    if template is None:
        bootstrap = deepcopy(original)
        bootstrap['adapter_preparation'] = {'kind': 'never_initialized_inputs'}
        (output/'manifest.json').write_text(json.dumps(parent | {'scenarios': [bootstrap]}))
        return
    from scripts.acceptance.workbook import workbook
    incoming = output/'frozen.xlsx'
    workbook(frozen, original['id'], template, incoming)
    (output/'predecessor.json').write_bytes(parent_bytes)
    source = load(frozen, original['source'])
    live = deepcopy(source['inputs']); live['input_version'] = 1; live['business']['demands'] = []
    targets = []
    for editor, code in enumerate(('M02', 'M04', 'M06', 'M08'), 1):
        row = next(row for row in live['masters']['models'] if row['code'] == code)
        targets.append({'editor': editor, 'kind': 'models', 'code': code, 'names': [row['name'], row['name']+' · import editor'+str(editor)]})
    identifier = 'r20-loose-initial-import-browser-v1'
    declaration = {'schema_version': 1, 'kind': 'first_import_full_snapshot_v1',
        'decision': 'https://github.com/Leegerger/auto_plan/issues/23#issuecomment-6205040209',
        'scenario': identifier, 'source_manifest_sha256': hashlib.sha256(parent_bytes).hexdigest(),
        'live_inputs': live, 'targets': targets,
        'workbook': {'path': 'frozen.xlsx', 'sha256': hashlib.sha256(incoming.read_bytes()).hexdigest()}, 'states': []}
    for mask in range(16):
        inputs = deepcopy(live)
        for bit, target in enumerate(targets):
            next(row for row in inputs['masters'][target['kind']] if row['code'] == target['code'])['name'] = target['names'][(mask >> bit) & 1]
        declaration['states'].append({'id': str(mask), 'inputs_sha256': content_digest(inputs)})
    raw = json.dumps(declaration, ensure_ascii=False, separators=(',', ':')).encode()
    (output/'companion.json').write_bytes(raw)
    (output/'public-template-provenance.json').write_text(json.dumps({'sha256':hashlib.sha256(template.read_bytes()).hexdigest(),'workbook_provenance':json.loads(incoming.with_name(incoming.name+'.acceptance.json').read_text())}))
    scenario = deepcopy(original) | {'id': identifier,
        'predecessor': {'scenario': original['id'], 'manifest': {'path': 'predecessor.json', 'sha256': hashlib.sha256(parent_bytes).hexdigest()}},
        'initial_import_load': {'path': 'companion.json', 'sha256': hashlib.sha256(raw).hexdigest(), 'encoding': 'json'}}
    (output/'manifest.json').write_text(json.dumps(parent | {'scenarios': [scenario]}))
    (output/'frozen-computation.json').write_text(json.dumps({'snapshot': source}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--template', type=Path)
    args = parser.parse_args()
    prepare(args.repo, args.output, args.template)
