"""The existing public-result CLI reads approvals from a trusted manifest."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from scripts.acceptance.test_assertions import known_evaluation
from scripts.acceptance.validate import load
from scripts.acceptance.input_companion import content_digest as approved_content_digest

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / 'docs/acceptance/frozen'


def decoded_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def content_digest(inputs):
    value = deepcopy(inputs)
    del value['input_version']
    return decoded_digest(value)


def test_cli_accepts_only_the_manifest_approved_complete_input_name_state(tmp_path):
    parent_bytes = (FROZEN / 'manifest.json').read_bytes()
    parent = json.loads(parent_bytes)
    original = next(row for row in parent['scenarios'] if row['id'] == 'r20-loose')
    source, action = load(FROZEN, original['source']), load(FROZEN, original['action'])
    successor_id = 'r20-loose-input-names-v1'
    targets = [{'editor': index + 1, 'kind': 'models', 'code': code,
                'names': [code, f'{code} · editor{index + 1}']}
               for index, code in enumerate(('M02', 'M04', 'M06', 'M08'))]
    states = []
    for mask in range(16):
        inputs = deepcopy(source['inputs'])
        for index, target in enumerate(targets):
            row = next(row for row in inputs['masters']['models'] if row['code'] == target['code'])
            row['name'] = target['names'][(mask >> index) & 1]
        states.append({'id': str(mask), 'inputs': inputs, 'inputs_sha256': content_digest(inputs)})
    companion = {'schema_version': 1, 'kind': 'maintained_input_names', 'digest_algorithm': 'decoded-input-json-v1',
                 'scenario': successor_id, 'source_manifest_sha256': hashlib.sha256(parent_bytes).hexdigest(),
                 'source_fixture_sha256': original['source']['sha256'],
                 'source_inputs_sha256': content_digest(source['inputs']), 'targets': targets, 'states': states}
    companion_bytes = json.dumps(companion, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    (tmp_path / 'input-companion.json').write_bytes(companion_bytes)
    scenario = deepcopy(original)
    scenario['id'] = successor_id
    scenario['predecessor'] = {'scenario': original['id'],
        'manifest': {'path': os.path.relpath(FROZEN / 'manifest.json', tmp_path),
                     'sha256': hashlib.sha256(parent_bytes).hexdigest()}}
    scenario['input_companion'] = {'path': 'input-companion.json', 'encoding': 'json',
                                   'sha256': hashlib.sha256(companion_bytes).hexdigest()}
    # Reference existing immutable payloads; never copy or rewrite their bytes.
    for kind in ('source', 'reference', 'action', 'import'):
        scenario[kind]['path'] = os.path.relpath(FROZEN / original[kind]['path'], tmp_path)
        assert scenario[kind]['sha256'] == original[kind]['sha256']
    manifest = {key: deepcopy(value) for key, value in parent.items() if key != 'scenarios'}
    manifest['scenarios'] = [scenario]
    assert manifest['gates_seconds'] == parent['gates_seconds']
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    accepted = deepcopy(source)
    accepted['inputs'] = deepcopy(states[1]['inputs'])
    accepted['inputs']['input_version'] = 2
    displayed = deepcopy(source)
    displayed['inputs'] = deepcopy(states[3]['inputs'])
    displayed['inputs']['input_version'] = 3
    evaluation = known_evaluation()
    proof = {'candidate': {'source_revision': 7, 'input_version': 2, 'latest_adoption': 1,
                          'request': action['request'], 'snapshot': deepcopy(accepted),
                          'objective': ['0'] * 30 + ['418', '0', '0'], 'evaluation': evaluation},
             'submitted': {'base_revision': 7, 'input_version': 2, 'latest_adoption': 1, 'request': action['request']},
             'source': {'snapshot': deepcopy(accepted), 'head': {'revision': 7, 'input_version': 2, 'latest_adoption': 1}},
             'displayed_reload': {'snapshot': displayed, 'head': {'revision': 7, 'input_version': 3, 'latest_adoption': 1}}}
    files = {}
    for kind, value in [('evaluation', evaluation), ('candidate', source['plan']), ('proof', proof)]:
        files[kind] = tmp_path / (kind + '.json')
        files[kind].write_text(json.dumps(value), encoding='utf-8')
    def check():
        return subprocess.run([sys.executable, str(ROOT / 'scripts/acceptance/check_result.py'),
            '--fixtures', str(tmp_path), '--scenario', successor_id, '--kind', 'candidate',
            '--evaluation', str(files['evaluation']), '--candidate', str(files['candidate']), '--proof', str(files['proof'])],
            cwd=ROOT, env={key: value for key, value in os.environ.items() if not key.startswith('AUTO_PLAN_')} |
                           {'PYTHONPATH': str(ROOT / 'src') + os.pathsep + str(ROOT)},
            text=True, capture_output=True, timeout=30)
    result = check()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['objective'] == ['0'] * 30 + ['418', '0', '0']

    # A later displayed read cannot replace the original accepted snapshot.
    proof['candidate']['snapshot'] = displayed
    files['proof'].write_text(json.dumps(proof), encoding='utf-8')
    result = check()
    assert result.returncode != 0 and 'accepted input pin' in result.stderr
    proof['candidate']['snapshot'] = accepted
    files['proof'].write_text(json.dumps(proof), encoding='utf-8')

    manifest['gates_seconds']['single_cell'] = 2
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    result = check()
    assert result.returncode != 0 and 'changed gates' in result.stderr
    manifest['gates_seconds'] = parent['gates_seconds']
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    # Even a hash-approved declaration may only vary its assigned names.
    companion['states'][1]['inputs']['parameters']['capacities'][0]['pieces'] = 1
    companion['states'][1]['inputs_sha256'] = content_digest(companion['states'][1]['inputs'])
    changed = json.dumps(companion, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    (tmp_path / 'input-companion.json').write_bytes(changed)
    scenario['input_companion']['sha256'] = hashlib.sha256(changed).hexdigest()
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    result = check()
    assert result.returncode != 0 and 'outside its declared names' in result.stderr


def test_python_input_digest_preserves_the_independent_exact_scalar_vector():
    value = {'input_version': 7, 'hours': '1/2', 'days': '5.0', 'percent': '95.00', 'rows': [{'hours': '0.50'}]}
    assert approved_content_digest(value) == '10cfec11b331891ae5270329036026152131be257071fe192af8efe51533851b'
    value['input_version'] = 8
    assert approved_content_digest(value) == '10cfec11b331891ae5270329036026152131be257071fe192af8efe51533851b'
    value['hours'] = '0.5'
    assert approved_content_digest(value) != '10cfec11b331891ae5270329036026152131be257071fe192af8efe51533851b'
