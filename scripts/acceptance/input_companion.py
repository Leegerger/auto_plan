"""Select complete name-only inputs approved by an explicit successor manifest."""
from copy import deepcopy
import hashlib
import json


def decoded_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def input_content(inputs):
    value = deepcopy(inputs)
    del value['input_version']
    return value


def content_digest(inputs):
    return decoded_digest(input_content(inputs))


def approved_source(root, manifest, scenario, source, actual_inputs):
    """Keep the frozen snapshot, replacing only a declared complete input state."""
    if 'input_companion' not in scenario:
        return source
    predecessor = scenario['predecessor']
    pointer = predecessor['manifest']
    parent_bytes = (root / pointer['path']).read_bytes()
    parent_sha = hashlib.sha256(parent_bytes).hexdigest()
    assert parent_sha == pointer['sha256'], 'Predecessor manifest bytes differ.'
    parent = json.loads(parent_bytes)
    original = next(row for row in parent['scenarios'] if row['id'] == predecessor['scenario'])
    assert scenario['id'] != original['id'], 'A companion needs an explicit successor identity.'
    assert {key: value for key, value in manifest.items() if key != 'scenarios'} == {
        key: value for key, value in parent.items() if key != 'scenarios'
    }, 'Successor manifest changed gates or reference metadata.'
    def business(row):
        value = deepcopy(row)
        for key in ('id', 'predecessor', 'input_companion'):
            value.pop(key, None)
        for key in ('source', 'reference', 'action', 'import'):
            if key in value:
                value[key].pop('path', None)
        return value
    assert business(scenario) == business(original), 'Successor changed frozen business inputs, action or reference.'
    pointer = scenario['input_companion']
    assert pointer['encoding'] == 'json', 'Unknown companion encoding.'
    raw = (root / pointer['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pointer['sha256'], 'Approved companion bytes differ.'
    declaration = json.loads(raw)
    assert declaration['scenario'] == scenario['id'], 'Companion names another scenario.'
    assert declaration['source_manifest_sha256'] == parent_sha, 'Companion names another predecessor manifest.'
    assert declaration['source_fixture_sha256'] == original['source']['sha256'], 'Companion names another frozen source.'
    assert declaration['schema_version'] == 1 and declaration['kind'] == 'maintained_input_names', 'Unknown companion schema or kind.'
    assert declaration['digest_algorithm'] == 'decoded-input-json-v1', 'Unknown companion digest algorithm.'
    initial = source['inputs']
    assert declaration['source_inputs_sha256'] == content_digest(initial), 'Companion predecessor complete inputs differ.'
    targets = declaration['targets']
    assert len(targets) == 4 and sorted(target['editor'] for target in targets) == [1, 2, 3, 4], 'Four distinct assigned editors are required.'
    assert len({(target['kind'], target['code']) for target in targets}) == 4, 'Name-edit targets repeat.'
    for target in targets:
        assert target['kind'] in ('models', 'lines'), 'Only existing model or line names may change.'
        rows = [row for row in initial['masters'][target['kind']] if row['code'] == target['code']]
        assert len(rows) == 1, 'An assigned existing master is missing or duplicated.'
        names = target['names']
        assert len(names) == 2 and all(isinstance(name, str) and name and not any(char in name for char in '\t\r\n') for name in names), 'Declared names must be nonempty table cells.'
        assert names[0] == rows[0]['name'] and names[0] != names[1], 'Declared original or changed name differs.'
    states = declaration['states']
    assert len(states) == 16, 'All sixteen complete states are required.'
    by_id = {state['id']: state for state in states}
    assert len(by_id) == 16, 'Declared state identities repeat.'
    by_digest = {}
    for mask in range(16):
        state = by_id[str(mask)]
        expected = deepcopy(initial)
        for index, target in enumerate(targets):
            row = next(row for row in expected['masters'][target['kind']] if row['code'] == target['code'])
            row['name'] = target['names'][(mask >> index) & 1]
        assert input_content(state['inputs']) == input_content(expected), 'A companion changed fields outside its declared names.'
        digest = content_digest(state['inputs'])
        assert digest == state['inputs_sha256'], 'Complete declared input-state digest differs.'
        assert digest not in by_digest, 'Declared complete states repeat.'
        by_digest[digest] = state['id']
    version = actual_inputs['input_version']
    assert type(version) is int and version >= initial['input_version'], 'Immutable input version moved backwards.'
    actual_digest = content_digest(actual_inputs)
    assert actual_digest in by_digest, 'Actual complete inputs are outside approved companion states.'
    if version == initial['input_version']:
        assert by_digest[actual_digest] == '0', 'An immutable input version changed contents.'
    result = deepcopy(source)
    result['inputs'] = deepcopy(actual_inputs)
    return result
