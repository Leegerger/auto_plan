"""Acceptance persistence parity through the public business HTTP interface."""
from copy import deepcopy
import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from auto_plan.input_models import InputSnapshot
from scripts.acceptance.adapter_server import BrowserPlanStore
from tests.input_support import MemoryInputStore
from tests.jobs_support import MemoryJobStore
from tests.plan_support import MemoryPlanStore
from tests.test_plan_transactions import shared_client, row
from tests.test_plan_history import workbook, records


def transcript(client, inputs, store_type):
    plans = store_type(inputs)
    client.app.state.input_store = inputs
    client.app.state.plan_store = plans
    client.app.state.job_store = MemoryJobStore(plans, client.app.state.access_store)
    answers = []

    def get(path):
        response = client.get('/auto_plan/api/' + path)
        assert response.status_code == 200, response.text
        value = response.json()
        answers.append(deepcopy(value))
        return value

    def command(*operations):
        current = get('plans/current')
        return {'base_revision': current['revision'], 'input_version': current['input_version'],
                'latest_adoption': current['latest_adoption'], 'operations': list(operations),
                'operation_id': str(UUID(int=len(answers))), 'pending': True}

    def post(path, body):
        if path == 'plans/preview':
            body = {key: value for key, value in body.items() if key not in ('operation_id', 'pending')}
        response = client.post('/auto_plan/api/' + path, json=body)
        assert response.status_code == 200, response.text
        value = response.json()
        answers.append(deepcopy(value))
        return value

    initial = {'segments': [row()], 'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}]}
    post('plans/adopt', command({'kind': 'replace_plan', 'plan': initial}))
    original = get('plans/adoptions/1')
    source = get('jobs/source')
    assert source['snapshot']['baseline']['plan']['segments'][0]['quantity'] == 100
    change = {'kind': 'change_quantity', 'segment_id': 'a', 'quantity': 99, 'end_date': '2026-10-15',
              'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L'], 'model_codes': ['M']},
              'action_id': 'same-public-edit'}
    payload = command(change)
    preview = post('plans/preview', payload)
    assert preview['evaluation']['output_totals']['expected'] == 99
    assert preview['evaluation']['output_totals']['on_time'] == 99
    assert preview['evaluation']['output_totals']['gap'] == 1
    # Mutating returned objects cannot become persisted history or cached output.
    expected = deepcopy(preview)
    preview['plan']['segments'].clear()
    preview['evaluation']['output_totals']['expected'] = 999
    source['snapshot']['baseline']['plan']['segments'].clear()
    assert post('plans/preview', payload) == expected
    post('plans/save', payload)
    changes = [
        {'kind': 'set_preferences', 'preferences': {'near_week_weight': '7'}},
        {'kind': 'lock_cells', 'cells': [{'date': '2026-10-16', 'line_code': 'B', 'model_code': 'N', 'quantity': 0}]},
        {'kind': 'record_execution', 'rows': [{'date': '2026-10-14', 'line_code': 'L', 'model_code': 'M', 'quantity': 1}],
         'cutoff': '2026-10-14T08:00:00+08:00'},
    ]
    post('plans/save', command(*changes))
    changed = get('jobs/source')
    assert changed['source_revision'] == 3 and changed['input_version'] == 2 and changed['latest_adoption'] == 1
    assert changed['snapshot']['plan']['segments'][0]['quantity'] == 99
    assert changed['snapshot']['baseline']['plan']['segments'][0]['quantity'] == 100
    assert changed['snapshot']['preferences']['near_week_weight'] == '7'
    assert changed['snapshot']['locks'] == changes[1]['cells']
    assert changed['snapshot']['execution']['facts'] == changes[2]['rows']
    models = deepcopy(changed['snapshot']['inputs']['masters']['models'])
    models[0]['name'] = 'Maintained after the read'
    post('inputs/apply', {'base_version': 2, 'changes': {'models': models}})
    refreshed = get('jobs/source')
    assert refreshed['input_version'] == 3
    assert refreshed['snapshot']['inputs']['masters']['models'] == models
    assert changed['snapshot']['inputs']['masters']['models'] != models
    post('plans/adopt', command())
    latest = get('jobs/source')
    assert latest['latest_adoption'] == 2
    assert latest['snapshot']['baseline']['plan']['segments'][0]['quantity'] == 99
    assert get('plans/adoptions/1') == original
    get('plans/history')
    for identity in range(1, 5):
        selected = get(f'plans/revisions/{identity}')
        assert 'evaluation' in selected and 'inputs' in selected['snapshot']
    exported = workbook(client.get('/auto_plan/api/plans/adoptions/1/export.xlsx'))
    exported_record = json.loads(''.join(part['json_chunk'] for part in records(exported, '_record_json')))
    assert exported_record == original
    answers.append(exported_record)
    return answers


def test_browser_planning_reads_preserve_complete_public_responses_context_history_and_export(shared_client):
    client, _, inputs = shared_client
    initial = inputs.read().model_dump(mode='json')
    initial['business']['demands'] = [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 100}]
    responses = []
    for store_type in (MemoryPlanStore, BrowserPlanStore):
        maintained = MemoryInputStore()
        maintained.version = 1
        maintained.versions[1] = InputSnapshot.model_validate(deepcopy(initial))
        responses.append(transcript(client, maintained, store_type))
    assert responses[0] == responses[1]


@pytest.mark.parametrize('raw_quantity', [True, 1.0], ids=['boolean', 'float'])
@pytest.mark.parametrize('store_type', [MemoryPlanStore, BrowserPlanStore], ids=['full-record', 'planning-record'])
def test_planning_reads_preserve_invalid_raw_adoption_types_after_a_valid_read(shared_client, raw_quantity, store_type):
    client, _, inputs = shared_client
    plans = store_type(inputs)
    client.app.state.plan_store = plans
    client.app.state.job_store = MemoryJobStore(plans, client.app.state.access_store)
    body = {'base_revision': 0, 'input_version': 1, 'latest_adoption': 0,
            'operations': [{'kind': 'patch_plan', 'segments': [row(quantity=1)]}],
            'operation_id': str(UUID(int=1)), 'pending': True}
    response = client.post('/auto_plan/api/plans/adopt', json=body)
    assert response.status_code == 200, response.text
    assert client.get('/auto_plan/api/jobs/source').status_code == 200
    # Legacy persistence can contain types that compare equal to 1 in Python.
    # A subsequent read must validate the raw record, including its types.
    plans.adoptions[1]['snapshot']['plan']['segments'][0]['quantity'] = raw_quantity
    with pytest.raises(ValidationError):
        client.get('/auto_plan/api/jobs/source')
    plans.adoptions[1]['snapshot']['plan']['segments'][0]['quantity'] = 1
    assert client.get('/auto_plan/api/jobs/source').status_code == 200


def test_browser_planning_reads_keep_reset_and_versioned_context(shared_client):
    from tests.test_computation_source import test_computation_source_keeps_complete_adopted_and_versioned_business_context as check
    client, _, inputs = shared_client
    plans = BrowserPlanStore(inputs)
    client.app.state.plan_store = plans
    check((client, plans, inputs))
