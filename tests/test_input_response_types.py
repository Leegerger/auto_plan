"""Complete public input responses retain their contract with checked serialization."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from tests.test_inputs import inputs_client


BIG = 2**53 + 123
NOTE = '确认\n制表\t空字符\x00与引号"\\'
CHANGES = {
    'recipes': [{'code': 'R', 'name': '工艺 Ω', 'active': True}],
    'lines': [{'code': 'L', 'name': '一线', 'active': True}],
    'models': [{'code': 'M', 'name': '机种 🧪', 'active': True, 'recipe_code': 'R'}],
    'line_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '95.50'}],
    'model_yields': [{'month': '2026-10', 'percent': '98', 'model_code': 'M'}],
    'capacities': [{'month': '2026-10', 'recipe_code': 'R', 'line_code': 'L', 'pieces': BIG}],
    'cycle_times': [{'model_code': 'M', 'days': '0.50'}],
    'holidays': [{'date': '2026-10-14', 'holiday': True}],
    'maintenance': [{'date': '2026-10-14', 'line_code': 'L', 'hours': 25}],
    'confirmations': [{'model_code': 'M', 'week': '2026-10-12', 'kind': 'material',
                       'batch_id': 'B', 'status': 'pending', 'date': None, 'quantity': None, 'note': NOTE}],
}
EMPTY = {'schema_version': 1, 'input_version': 0,
         'masters': {'models': [], 'recipes': [], 'lines': [], 'capabilities': []},
         'parameters': {'line_yields': [], 'model_yields': [], 'capacities': [], 'cycle_times': [], 'switch_durations': []},
         'calendar': {'holidays': [], 'opening_exceptions': [], 'maintenance': [], 'experiments': []},
         'business': {'demands': [], 'confirmations': []}}


def test_public_input_responses_preserve_complete_contract_and_ownership(inputs_client):
    client, wires = inputs_client, []
    def request(method, path, payload=None):
        result = client.request(method, '/auto_plan/api' + path, **({'json': payload} if payload is not None else {}))
        wires.append({'method': method, 'path': path, 'status': result.status_code,
                      'headers': dict(result.headers), 'body_hex': result.content.hex()})
        return result
    assert request('GET', '/inputs').json() == EMPTY
    seed = deepcopy(EMPTY)
    for section in ('masters', 'parameters', 'calendar', 'business'):
        for kind in seed[section]:
            if kind in CHANGES:
                seed[section][kind] = deepcopy(CHANGES[kind])
    command = {'base_version': 0, 'changes': CHANGES}
    checked = request('POST', '/inputs/preview', command)
    assert checked.status_code == 200
    assert checked.json() == {'valid': True, 'base_version': 0, 'errors': [], 'changes': CHANGES, 'snapshot': seed}
    assert request('GET', '/inputs?version=0').json() == EMPTY
    seed['input_version'] = 1
    assert request('POST', '/inputs/apply', command).json() == seed
    assert request('GET', '/inputs?version=1').json() == seed
    current = deepcopy(seed); current['input_version'] = 2
    for kind in ('line_yields', 'model_yields', 'capacities'):
        current['parameters'][kind] = [dict(CHANGES[kind][0], month=month) for month in ('2026-10', '2026-11', '2026-12', '2027-01', '2027-02')]
    assert request('GET', '/inputs').json() == current
    assert request('GET', '/inputs').json() == current
    changed = deepcopy(current); changed['masters']['models'][0]['name'] = '更名 🧪'; changed['input_version'] = 3
    rename = {'base_version': 2, 'changes': {'models': [changed['masters']['models'][0]]}}
    proposal = deepcopy(changed); proposal['input_version'] = 2
    assert request('POST', '/inputs/preview', rename).json() == {'valid': True, 'base_version': 2, 'errors': [], 'changes': rename['changes'], 'snapshot': proposal}
    assert request('POST', '/inputs/apply', rename).json() == changed
    stale = request('POST', '/inputs/apply', rename)
    assert stale.status_code == 409 and stale.json() == {'detail': '资料已更新，请重新读取差异并预览'}
    noop = {'base_version': 3, 'changes': rename['changes']}
    assert request('POST', '/inputs/apply', noop).json() == changed
    invalid = {'base_version': 3, 'changes': {'unknown': [], 'cycle_times': [{'model_code': 'M', 'days': '-1'}]}}
    bad = request('POST', '/inputs/preview', invalid)
    assert bad.status_code == 200
    assert bad.json() == {'valid': False, 'base_version': 3, 'errors': [
        {'sheet': 'unknown', 'row': 1, 'field': 'sheet', 'reason': '未知资料类别'},
        {'sheet': 'cycle_times', 'row': 2, 'field': 'days', 'reason': '必须填写非负十进制数，最多两位小数'}],
        'changes': {'cycle_times': []}, 'snapshot': None}
    rejected = request('POST', '/inputs/apply', invalid)
    assert rejected.status_code == 422 and rejected.json()['detail'] == {'message': '整批未提交，请修正错误', 'errors': bad.json()['errors']}
    assert request('GET', '/inputs?version=99').status_code == 404
    assert request('POST', '/inputs/preview', {'base_version': True, 'changes': {}}).status_code == 422
    assert request('GET', '/inputs?version=0').json() == EMPTY
    assert request('GET', '/inputs?version=1').json() == seed
    assert request('GET', '/inputs?version=2').json() == current
    assert request('GET', '/inputs?version=3').json() == changed
    checked.json()['snapshot']['business']['confirmations'][0]['note'] = 'client only'
    assert request('GET', '/inputs?version=1').json() == seed
    access = client.app.state.access_store
    access.users['EDITOR']['roles'] = ['viewer']
    assert request('GET', '/inputs?version=3').json() == changed
    assert request('POST', '/inputs/preview', noop).status_code == 403
    assert request('POST', '/inputs/apply', noop).status_code == 403
    access.users['EDITOR']['roles'] = ['editor']
    client.cookies.clear()
    assert request('GET', '/inputs').status_code == 401
    if os.environ.get('INPUT_RESPONSE_EXPECTED_WIRE'):
        assert wires == json.loads(Path(os.environ['INPUT_RESPONSE_EXPECTED_WIRE']).read_text())
    if os.environ.get('INPUT_RESPONSE_CAPTURE_WIRE'):
        Path(os.environ['INPUT_RESPONSE_CAPTURE_WIRE']).write_text(json.dumps(wires, ensure_ascii=False, indent=2) + '\n')


def test_complete_input_responses_use_checked_default_serialization(inputs_client, monkeypatch):
    import fastapi.routing
    seen, original = [], fastapi.routing.serialize_response
    async def observe(**kwargs):
        seen.append({'field': kwargs['field'], 'dump_json': kwargs['dump_json'], 'is_coroutine': kwargs['is_coroutine']})
        return await original(**kwargs)
    monkeypatch.setattr(fastapi.routing, 'serialize_response', observe)
    client = inputs_client
    assert client.get('/auto_plan/api/inputs').status_code == 200
    command = {'base_version': 0, 'changes': {}}
    assert client.post('/auto_plan/api/inputs/preview', json=command).status_code == 200
    assert client.post('/auto_plan/api/inputs/apply', json=command).status_code == 200
    assert len(seen) == 3
    assert all(row['field'] is not None and row['dump_json'] is True and row['is_coroutine'] is False for row in seen)


def test_input_source_unavailable_retains_priority_over_stale_batch(inputs_client, monkeypatch):
    from auto_plan.access import StoreUnavailable
    store = inputs_client.app.state.input_store
    def unavailable(*args, **kwargs):
        raise StoreUnavailable
    monkeypatch.setattr(store, 'read', unavailable)
    for method, path, payload in [('GET', '/inputs?version=0', None), ('GET', '/inputs', None),
                                  ('POST', '/inputs/preview', {'base_version': 999, 'changes': {}}),
                                  ('POST', '/inputs/apply', {'base_version': 999, 'changes': {}})]:
        response = inputs_client.request(method, '/auto_plan/api' + path, **({'json': payload} if payload else {}))
        assert response.status_code == 503
