"""Attribute an approved read-only import while other users maintain inputs."""
from copy import deepcopy
import hashlib
import json

from .assertions import _assert_workbook_contents
from .compare import assert_reference
from .input_companion import content_digest
from .validate import load

DECISION = 'https://github.com/Leegerger/auto_plan/issues/23#issuecomment-6205040209'


def _canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',', ':'),allow_nan=False)


def _captured(fixtures, pointer, expected):
    path = (fixtures / pointer['path']).resolve()
    assert path.is_relative_to(fixtures.resolve()), 'Public body artifact leaves its evidence directory.'
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pointer['sha256'], 'Public body bytes differ.'
    assert _canonical(json.loads(raw)) == _canonical(expected), 'Public body differs from its complete decoded payload.'


def assert_initial_import_load(fixtures, scenario_id, proof):
    """Check full accepted inputs and every authorized immutable version advance.

    The fixture manifest is trusted configuration. Approval is never taken from
    an assertion embedded only in the observed proof.
    """
    manifest = json.loads((fixtures / 'manifest.json').read_text())
    scenario = next(row for row in manifest['scenarios'] if row['id'] == scenario_id)
    pointer = scenario['initial_import_load']
    assert pointer['encoding'] == 'json', 'Unknown first-import companion encoding.'
    raw = (fixtures / pointer['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pointer['sha256'], 'Approved import companion bytes differ.'
    declaration = json.loads(raw)
    assert declaration['schema_version'] == 1 and declaration['kind'] == 'first_import_full_snapshot_v1'
    assert declaration['scenario'] == scenario_id and declaration['decision'] == DECISION, 'First-import approval differs.'
    parent = scenario['predecessor']; parent_raw = (fixtures / parent['manifest']['path']).read_bytes()
    digest = hashlib.sha256(parent_raw).hexdigest()
    assert digest == parent['manifest']['sha256'] == declaration['source_manifest_sha256'], 'Import predecessor manifest differs.'
    original_manifest = json.loads(parent_raw)
    original = next(row for row in original_manifest['scenarios'] if row['id'] == parent['scenario'])
    assert scenario_id != original['id'], 'Import load requires an explicit successor.'
    assert _canonical({key: value for key, value in manifest.items() if key != 'scenarios'}) == _canonical({key: value for key, value in original_manifest.items() if key != 'scenarios'}), 'Import load changed gates or reference metadata.'
    business = deepcopy(scenario)
    for key in ('id', 'predecessor', 'initial_import_load'): business.pop(key)
    assert _canonical(business) == _canonical({key: value for key, value in original.items() if key != 'id'}), 'Import load changed frozen business assets.'
    source, expected = load(fixtures, scenario['source']), load(fixtures, scenario['import'])
    for key in ('reference', 'action'): load(fixtures, scenario[key])
    live = deepcopy(source['inputs']); live['input_version'] = declaration['live_inputs']['input_version']; live['business']['demands'] = []
    assert _canonical(declaration['live_inputs']) == _canonical(live), 'Declared live inputs changed business quantities or fields.'
    targets = declaration['targets']
    assert sorted(row['editor'] for row in targets) == [1,2,3,4], 'Four assigned input editors are required.'
    assert len({(row['kind'], row['code']) for row in targets}) == 4
    for target in targets:
        assert target['kind'] in ('models', 'lines')
        rows = [row for row in live['masters'][target['kind']] if row['code'] == target['code']]
        assert len(rows) == 1 and target['names'][0] == rows[0]['name']
        assert len(target['names']) == 2 and target['names'][0] != target['names'][1]
        assert all(isinstance(name, str) and name and not any(c in name for c in '\t\r\n') for name in target['names'])
    declared = {row['id']: row['inputs_sha256'] for row in declaration['states']}
    assert len(declared) == len(declaration['states']) == 16
    allowed = set()
    for mask in range(16):
        state = deepcopy(live)
        for bit, target in enumerate(targets): next(row for row in state['masters'][target['kind']] if row['code'] == target['code'])['name'] = target['names'][(mask >> bit) & 1]
        digest = content_digest(state)
        assert declared[str(mask)] == digest, 'Declared complete input state differs.'
        allowed.add(digest)
    before, after, prepared = proof['before'], proof['after'], proof['prepared']
    first, last = live['input_version'], after['head']['input_version']
    assert type(first) is int and type(last) is int and last >= first
    versions = proof['versions']
    assert set(versions) == {str(v) for v in range(first, last+1)}, 'Immutable input version history has a gap.'
    for version in range(first, last+1):
        observed = versions[str(version)]
        _captured(fixtures, observed['artifact'], observed['body'])
        assert all(type(value) is int for value in (observed['status'],observed['version'],observed['body']['input_version']))
        assert observed['status'] == 200 and observed['version'] == observed['body']['input_version'] == version
        assert content_digest(observed['body']) in allowed, 'Immutable inputs changed quantity or undeclared fields.'
    assert _canonical(versions[str(first)]['body']) == _canonical(live), 'Initial immutable inputs differ.'
    history = proof['history']
    assert len(history) == last-first, 'An input version has no unique attributed editor commit.'
    for version, transition in zip(range(first+1, last+1), history, strict=True):
        target = next(row for row in targets if row['editor'] == transition['editor'])
        command = transition['command']; previous = versions[str(version-1)]['body']
        _captured(fixtures, transition['request_artifact'], command)
        _captured(fixtures, transition['response_artifact'], transition['response'])
        assert type(command['base_version']) is int and command['base_version'] == version-1 and set(command['changes']) == {target['kind']}
        rows = command['changes'][target['kind']]
        assert len({row['code'] for row in rows}) == len(rows), 'Submitted input identities repeat.'
        expected_input = deepcopy(previous)
        index = {row['code']: row for row in previous['masters'][target['kind']]}
        changed = []
        for submitted in rows:
            assert submitted['code'] in index, 'An input editor added a master.'
            original_row = index[submitted['code']]
            if _canonical(submitted) != _canonical(original_row):
                assert submitted['code'] == target['code'] and submitted['name'] in target['names']
                assert _canonical({key: value for key, value in submitted.items() if key != 'name'}) == _canonical({key: value for key, value in original_row.items() if key != 'name'}), 'Input edit changed fields outside its assigned name.'
                changed.append(submitted)
        assert len(changed) == 1, 'An editor commit must genuinely change its one declared name.'
        next(row for row in expected_input['masters'][target['kind']] if row['code'] == target['code'])['name'] = changed[0]['name']
        expected_input['input_version'] = version
        assert type(transition['response_status']) is int and transition['response_status'] == 200, 'Unacknowledged input commit needs its own qualified reconciliation proof.'
        assert _canonical(transition['response']) == _canonical(versions[str(version)]['body']) == _canonical(expected_input), 'Attributed immutable input transition differs.'
    from datetime import timedelta
    from auto_plan.plan_models import Plan, EvaluationSnapshot, EvaluationTime
    from auto_plan.plans import PlanningContext
    from auto_plan.production_time import week_label
    from auto_plan.rules import evaluate
    empty_plan = Plan().model_dump(mode='json')
    empty_context = PlanningContext().model_dump(mode='json')
    time = EvaluationTime.model_validate(source['time'])
    weeks = [{'number':week+1,'label':week_label(time.window_start+timedelta(days=week*7)),
        'start_date':(time.window_start+timedelta(days=week*7)).isoformat(),
        'dates':[(time.window_start+timedelta(days=week*7+day)).isoformat() for day in range(7)]}
        for week in range(15)]
    for state in (before, after):
        assert state['head']['revision'] == state['head']['latest_adoption'] == 0, 'Import load initialized a Plan or adoption.'
        version = state['head']['input_version']
        assert all(type(state['head'][key]) is int for key in ('revision','latest_adoption','input_version'))
        inputs = versions[str(version)]['body']
        assert _canonical(state['snapshot']) == _canonical({'inputs':inputs,'plan':empty_plan,'context':empty_context}), 'Live source is not a never-initialized empty Plan/context.'
        expected_head = {'revision':0,'latest_adoption':0,'input_version':version,'pending':False,'kind':'empty',
            'unallocated':[],'operation_issues':[],'lineage':{},'coverage':[],'plan':empty_plan,'context':empty_context,
            'execution_segments':[],'execution_surplus':[],'reset_preview':None,
            'reset_participants':{'current':{'source_adoption':0,'model_codes':sorted(row['code'] for row in inputs['masters']['models'] if row['active'])},'correction':None},
            'time':time.model_dump(mode='json'),'hour_display':{'segments':{},'switches':{}},'weeks':weeks,
            'evaluation':evaluate(EvaluationSnapshot(time=time,inputs=inputs,plan=empty_plan)).model_dump(mode='json')}
        assert _canonical(state['head']) == _canonical(expected_head), 'Live head differs from the complete frozen empty source.'
        assert _canonical(state['demand_files']) == _canonical({'input_version': version, 'enablement_date': None, 'files': []}), 'Import load changed demand history.'
    assert _canonical({key: value for key, value in before['snapshot'].items() if key != 'inputs'}) == _canonical({key: value for key, value in after['snapshot'].items() if key != 'inputs'}), 'Import load changed Plan/context contents.'
    assert _canonical({key: value for key, value in before['head'].items() if key != 'input_version'}) == _canonical({key: value for key, value in after['head'].items() if key != 'input_version'}), 'Import load changed shared state outside input versions.'
    accepted = prepared['input_version']
    _captured(fixtures, proof['prepared_artifact'], prepared)
    assert _canonical(proof['query']) == _canonical({'base_revision': 0, 'input_version': accepted, 'latest_adoption': 0}), 'Submitted query differs from accepted server input pin.'
    assert str(accepted) in versions and prepared['inputs']['input_version'] == prepared['payload']['input_version'] == accepted, 'Prepared inputs differ from accepted server input pin.'
    workbook = declaration['workbook']
    assert hashlib.sha256((fixtures / workbook['path']).read_bytes()).hexdigest() == workbook['sha256'] == proof['workbook_sha256'], 'Incoming workbook bytes differ.'
    _assert_workbook_contents(prepared, expected, source, scenario, {'revision': 0, 'input_version': accepted, 'latest_adoption': 0})
    from datetime import datetime
    from zoneinfo import ZoneInfo
    context = PlanningContext(execution=source['execution'], locks=source.get('locks', []), reset=expected['reset'],
        coverage=expected['coverage'], preferences=source.get('preferences', {}), lineage={
            'enablement_date': datetime.fromisoformat(source['time']['as_of']).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),
            'external_reset': {'production_week': expected['reset']['production_week'], 'cutoff': expected['reset']['cutoff'],
                'batch_id': expected['reset']['batch_id'], 'already_applied': True, 'system_correction_available': False},
            'initial_coverage': expected['coverage']}).model_dump(mode='json')
    assert _canonical(prepared['context']) == _canonical(context), 'Prepared complete context differs from frozen import expectations.'
    assert proof['evaluations'], 'No complete full-snapshot computation was observed.'
    from auto_plan.plan_models import EvaluationResult
    independent = evaluate(EvaluationSnapshot.model_validate(source)).model_dump(mode='json')
    for evaluation in proof['evaluations']:
        _captured(fixtures, evaluation['request_artifact'], evaluation['payload'])
        _captured(fixtures, evaluation['response_artifact'], evaluation['response'])
        assert type(evaluation['status']) is int and evaluation['status'] == 200 and _canonical(evaluation['payload']) == _canonical({'snapshot': source}), 'Background computation used another complete frozen snapshot.'
        response = EvaluationResult.model_validate(evaluation['response']).model_dump(mode='json')
        assert_reference(response, scenario)
        assert len(response['weekly_results']) == len(source['inputs']['business']['demands'])
        assert _canonical(response) == _canonical(independent), 'Complete full-snapshot evaluation ledgers differ from independent rules.'
        assert _canonical(evaluation['response']) == _canonical(independent), 'Raw complete evaluation fields or scalar types differ from independent rules.'
    return {'accepted_input_version': accepted, 'final_input_version': last, 'evaluation_count': len(proof['evaluations']), 'acceptance': 'not_run'}
