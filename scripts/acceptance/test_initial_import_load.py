"""Public first-import replies must separate accepted, live and incoming inputs."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts.acceptance.assertions import assert_workbook
from scripts.acceptance.input_companion import content_digest
from scripts.acceptance.validate import load
from scripts.acceptance.workbook import workbook

FROZEN = Path(__file__).resolve().parents[2] / 'docs/acceptance/frozen'
DECISION = 'https://github.com/Leegerger/auto_plan/issues/23#issuecomment-6205040209'


@pytest.fixture(scope='module')
def public_import(tmp_path_factory):
    from fastapi.testclient import TestClient
    from auto_plan.app import create_app
    from auto_plan.input_models import InputSnapshot
    from auto_plan.settings import Settings
    from tests.input_support import MemoryInputStore
    from tests.plan_support import MemoryPlanStore
    from tests.demand_support import MemoryDemandStore
    from tests.support import MemoryAccessStore, CompanyAuthentication
    directory = tmp_path_factory.mktemp('initial-import-load')
    (directory / 'bodies').mkdir()
    def capture(raw):
        digest = hashlib.sha256(raw).hexdigest()
        path = 'bodies/'+digest+'.json'; (directory/path).write_bytes(raw)
        return {'path': path, 'sha256': digest}
    parent_bytes = (FROZEN / 'manifest.json').read_bytes()
    parent = json.loads(parent_bytes)
    original = next(row for row in parent['scenarios'] if row['id'] == 'r20-loose')
    source, expected = load(FROZEN, original['source']), load(FROZEN, original['import'])
    for name in ('source', 'reference', 'action', 'import'):
        shutil.copy2(FROZEN / original[name]['path'], directory / original[name]['path'])
    (directory / 'predecessor.json').write_bytes(parent_bytes)
    live = deepcopy(source['inputs']); live['input_version'] = 1; live['business']['demands'] = []
    inputs, access = MemoryInputStore(), MemoryAccessStore()
    inputs.version = 1; inputs.versions[1] = InputSnapshot.model_validate(live)
    plans = MemoryPlanStore(inputs)
    for actor in range(5): access.users[f'IMPORT{actor}'] = {'user_id': f'IMPORT{actor}', 'roles': ['editor'], 'active': True}
    app = create_app(Settings(public_origin='https://testserver'), store=access, input_store=inputs,
        plan_store=plans, demand_store=MemoryDemandStore(plans), authenticator=CompanyAuthentication(),
        clock=lambda: datetime.fromisoformat(source['time']['as_of']))
    prefix = '/auto_plan/api'
    def login(client, actor):
        client.headers.update({'Origin': 'https://testserver', 'X-Auto-Plan-Request': '1'})
        assert client.post(prefix+'/auth/login', json={'username': f'IMPORT{actor}', 'password': 'company-secret'}).status_code == 200
    with TestClient(app, base_url='https://testserver') as primary:
        login(primary, 0)
        template = directory / 'template.xlsx'
        template.write_bytes(primary.get(prefix+'/initialization/template.xlsx').content)
        incoming = directory / 'frozen.xlsx'
        workbook(FROZEN, original['id'], template, incoming)
        targets = []
        for editor, code in enumerate(('M02', 'M04', 'M06', 'M08'), 1):
            row = next(row for row in live['masters']['models'] if row['code'] == code)
            targets.append({'editor': editor, 'kind': 'models', 'code': code, 'names': [row['name'], row['name']+' · import editor'+str(editor)]})
        declaration = {'schema_version': 1, 'kind': 'first_import_full_snapshot_v1', 'decision': DECISION,
            'scenario': 'r20-loose-initial-import-load-v1', 'source_manifest_sha256': hashlib.sha256(parent_bytes).hexdigest(),
            'live_inputs': live, 'targets': targets,
            'workbook': {'path': 'frozen.xlsx', 'sha256': hashlib.sha256(incoming.read_bytes()).hexdigest()}, 'states': []}
        for mask in range(16):
            state = deepcopy(live)
            for bit, target in enumerate(targets): next(row for row in state['masters']['models'] if row['code'] == target['code'])['name'] = target['names'][(mask >> bit) & 1]
            declaration['states'].append({'id': str(mask), 'inputs_sha256': content_digest(state)})
        raw = json.dumps(declaration, ensure_ascii=False, separators=(',', ':')).encode()
        (directory / 'companion.json').write_bytes(raw)
        scenario = deepcopy(original) | {'id': declaration['scenario'],
            'predecessor': {'scenario': original['id'], 'manifest': {'path': 'predecessor.json', 'sha256': hashlib.sha256(parent_bytes).hexdigest()}},
            'initial_import_load': {'path': 'companion.json', 'sha256': hashlib.sha256(raw).hexdigest(), 'encoding': 'json'}}
        (directory / 'manifest.json').write_text(json.dumps(parent | {'scenarios': [scenario]}))
        versions, history = {}, []
        def immutable(version):
            response = primary.get(prefix+'/inputs', params={'version': version}); assert response.status_code == 200
            value = response.json(); versions[str(version)] = {'status': 200, 'version': version, 'body': value, 'artifact': capture(response.content)}; return value
        immutable(1)
        def edit(editor):
            target = targets[editor-1]
            before = immutable(max(map(int, versions)))
            row = deepcopy(next(row for row in before['masters']['models'] if row['code'] == target['code'])); row['name'] = target['names'][1]
            command = {'base_version': before['input_version'], 'changes': {'models': [row]}}
            # The primary owns the one application lifespan. A second auth
            # context must not shut that shared app down after its request.
            writer = TestClient(app, base_url='https://testserver')
            try:
                login(writer, editor)
                preview = writer.post(prefix+'/inputs/preview', json=command); assert preview.status_code == 200 and preview.json()['valid']
                response = writer.post(prefix+'/inputs/apply', json=command); assert response.status_code == 200
            finally:
                writer.close()
            body = response.json(); immutable(body['input_version'])
            history.append({'editor': editor, 'command': command, 'response_status': 200, 'response': body,
                'request_artifact': capture(response.request.content), 'response_artifact': capture(response.content)})
        def state():
            head = primary.get(prefix+'/plans/current').json()
            return {'head': head, 'snapshot': {'inputs': immutable(head['input_version']), 'plan': head['plan'], 'context': head['context']},
                'demand_files': primary.get(prefix+'/demands/files').json()}
        edit(1)
        before = state()
        evaluation = primary.post(prefix+'/plans/evaluate', json={'snapshot': source})
        assert evaluation.status_code == 200
        query = {'base_revision': 0, 'input_version': before['head']['input_version'], 'latest_adoption': 0}
        response = primary.post(prefix+'/initialization/excel/preview', params=query, content=incoming.read_bytes(), headers={'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
        assert response.status_code == 200
        prepared = response.json(); assert prepared['valid']
        assert prepared['input_version'] == prepared['inputs']['input_version'] == 2
        assert prepared['inputs']['masters']['models'][2]['name'] != versions['2']['body']['masters']['models'][2]['name']
        edit(2)
        proof = {'prepared': prepared, 'before': before, 'after': state(), 'query': query,
            'versions': versions, 'history': history, 'workbook_sha256': hashlib.sha256(incoming.read_bytes()).hexdigest(),
            'prepared_artifact': capture(response.content),
            'evaluations': [{'status': evaluation.status_code, 'payload': {'snapshot': source}, 'response': evaluation.json(),
                'request_artifact': capture(evaluation.request.content), 'response_artifact': capture(evaluation.content)}]}
        (directory / 'public-proof.json').write_text(json.dumps(proof))
        (directory / 'source.json').write_text(json.dumps(source)); (directory / 'expected.json').write_text(json.dumps(expected))
        yield directory, scenario, source, expected, proof


def test_real_workbook_accepts_its_pin_when_other_authorized_editors_advance_inputs(public_import):
    from scripts.acceptance.initial_import_load import assert_initial_import_load
    directory, scenario, source, expected, proof = public_import
    result = assert_initial_import_load(directory, scenario['id'], proof)
    assert result == {'accepted_input_version': 2, 'final_input_version': 3, 'evaluation_count': 1, 'acceptance': 'not_run'}
    # Solo proof still rejects another writer; only explicit approved attribution
    # authorizes the new workload's input-version changes.
    with pytest.raises(AssertionError, match='shared token'):
        assert_workbook(proof['prepared'], expected, source, scenario, proof['before'], proof['after'])


def test_accepted_public_body_hash_cannot_be_replaced(public_import):
    from scripts.acceptance.initial_import_load import assert_initial_import_load
    directory, scenario, _, _, original = public_import
    proof = deepcopy(original); proof['prepared_artifact']['sha256'] = '0'*64
    with pytest.raises(AssertionError, match='body bytes'):
        assert_initial_import_load(directory, scenario['id'], proof)


@pytest.mark.parametrize('case', ['locks','reset','execution','coverage','preferences','lineage',
    'segments','switches','initial_states','ordinary_idle','current_day_witnesses','kind','pending'])
def test_symmetric_live_state_corruption_cannot_masquerade_as_never_initialized(public_import, case):
    from scripts.acceptance.initial_import_load import assert_initial_import_load
    directory, scenario, source, expected, original = public_import
    proof = deepcopy(original)
    for state in (proof['before'], proof['after']):
        if case in ('kind','pending'):
            state['head'][case] = 'save' if case=='kind' else True
        elif case in ('segments','switches','initial_states','ordinary_idle','current_day_witnesses'):
            value = deepcopy(source['plan'].get(case, [])) or [{'unexpected': 'positive live Plan state'}]
            for target in (state['head']['plan'], state['snapshot']['plan']): target[case] = value
        else:
            value = {'locks':[{'date':'2027-01-24','line_code':'L00','model_code':'M00','quantity':100}],
                'reset':expected['reset'],'execution':source['execution'],'coverage':expected['coverage'],
                'preferences':{'near_weeks':6},'lineage':{'unexpected':'prior history'}}[case]
            for target in (state['head']['context'],state['snapshot']['context']): target[case] = deepcopy(value)
    with pytest.raises(AssertionError):
        assert_initial_import_load(directory,scenario['id'],proof)


def test_rehashed_full_evaluation_cannot_drop_a_nonobjective_ledger(public_import):
    from scripts.acceptance.initial_import_load import assert_initial_import_load
    directory,scenario,_,_,original=public_import
    proof=deepcopy(original)
    entry=proof['evaluations'][0]
    assert entry['response']['output_sources']
    entry['response']['output_sources']=[]
    raw=json.dumps(entry['response']).encode(); digest=hashlib.sha256(raw).hexdigest()
    path='bodies/'+digest+'.json'; (directory/path).write_bytes(raw)
    entry['response_artifact']={'path':path,'sha256':digest}
    with pytest.raises(AssertionError):
        assert_initial_import_load(directory,scenario['id'],proof)


@pytest.mark.parametrize('case', ['wrong_pin', 'missing_version', 'changed_quantity', 'mutated_workbook', 'demand_history', 'incomplete_evaluation', 'context_lock'])
def test_untrusted_import_proof_cannot_substitute_business_or_attribution(public_import, case):
    from scripts.acceptance.initial_import_load import assert_initial_import_load
    directory, scenario, _, _, original = public_import
    proof = deepcopy(original)
    def rebind(value):
        raw = json.dumps(value).encode(); digest = hashlib.sha256(raw).hexdigest()
        path = 'bodies/'+digest+'.json'; (directory/path).write_bytes(raw)
        return {'path': path, 'sha256': digest}
    if case == 'wrong_pin': proof['query']['input_version'] = 3
    elif case == 'missing_version': del proof['versions']['2']
    elif case == 'changed_quantity':
        proof['versions']['3']['body']['parameters']['capacities'][0]['pieces'] = 1
        proof['versions']['3']['artifact'] = rebind(proof['versions']['3']['body'])
    elif case == 'mutated_workbook':
        proof['prepared']['payload']['segments'][0]['quantity'] = 1
        proof['prepared_artifact'] = rebind(proof['prepared'])
    elif case == 'demand_history': proof['after']['demand_files']['files'] = [{'file_id': 'unexpected'}]
    elif case == 'incomplete_evaluation':
        proof['evaluations'][0]['response']['computation_complete'] = False
        proof['evaluations'][0]['response_artifact'] = rebind(proof['evaluations'][0]['response'])
    elif case == 'context_lock':
        proof['prepared']['context']['locks'] = [{'date': '2027-01-24', 'line_code': 'L00', 'model_code': 'M00', 'quantity': 100}]
        proof['prepared_artifact'] = rebind(proof['prepared'])
    with pytest.raises(AssertionError):
        assert_initial_import_load(directory, scenario['id'], proof)


def test_approved_public_import_proof_is_consumed_by_the_real_cli(public_import):
    import subprocess
    import sys
    directory, scenario, _, _, proof = public_import
    evaluation = directory/'evaluation.json'; evaluation.write_text(json.dumps(proof['prepared']['evaluation']))
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('check_result.py')),
        '--fixtures', str(directory), '--scenario', scenario['id'], '--kind', 'workbook',
        '--evaluation', str(evaluation), '--proof', str(directory/'public-proof.json')],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'pass'
    assert len(json.loads(result.stdout)['objective']) == 33
