"""Immutable output reuse preserves complete results and caller ownership."""
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from uuid import uuid4
import sys
from collections import OrderedDict
import pytest

from auto_plan.input_models import InputSnapshot
from auto_plan.output_reuse import OutputReuse
from auto_plan.plan_models import BatchUse, EvaluationSnapshot
from auto_plan.plans import PlanCommand, PlanService
from auto_plan.rules import evaluate
from tests.test_output_preview import confirmation
from tests.test_plan_transactions import shared_client, command, post, row


def _assert_immutable_json_graph(value):
    """Path qualification only, including nested and empty containers."""
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is tuple:
        for child in value:
            _assert_immutable_json_graph(child)
        return
    if is_dataclass(value):
        assert value.__dataclass_params__.frozen
        assert not hasattr(value, '__dict__')
        for field in fields(value):
            _assert_immutable_json_graph(getattr(value, field.name))
        return
    raise AssertionError(
        f'Expected immutable derived JSON, found {type(value).__name__}'
    )


def test_public_wip_quantity_preview_has_complete_owned_immutable_reuse(shared_client):
    client, plans, inputs = shared_client
    data = inputs.read().model_dump(mode='json')
    data['business']['demands'] = [
        {'model_code': model, 'week': '2026-10-12', 'quantity': 100}
        for model in ('M', 'N')
    ]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    from auto_plan.plan_models import ResetContext
    plans.context = plans.context.model_copy(update={
        'reset': ResetContext.model_validate({
            'batch_id': 'OBSERVED', 'production_week': '2026-10-12',
            'cutoff': '2026-10-12T08:00:00+08:00', 'models': [
                {'model_code': 'M', 'wip': 7, 'net_balance': 0},
                {'model_code': 'N', 'wip': 3, 'net_balance': 0},
            ],
        }),
    })
    saved = post(client, 'save', command({'kind': 'replace_plan', 'plan': {
        'segments': [row('m'), row('n', 'N', '2026-10-16')],
        'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}],
    }}))
    assert saved.status_code == 200, saved.text
    source_inputs = inputs.read().model_dump(mode='json')
    source_context = plans.context.model_copy(deep=True)
    cache = plans.output_cache = OutputReuse()
    current_response = client.get('/auto_plan/api/plans/current')
    assert current_response.status_code == 200, current_response.text
    before = current_response.json()
    assert before['evaluation']['output_totals']['expected'] == 210
    assert before['evaluation']['output_totals']['on_time'] == 200
    assert before['evaluation']['output_totals']['gap'] == 0
    request = {
        'base_revision': before['revision'],
        'input_version': before['input_version'],
        'latest_adoption': before['latest_adoption'],
        'operations': [{
            'kind': 'change_quantity', 'segment_id': 'm', 'quantity': 99,
            'end_date': '2026-10-15', 'action_id': str(uuid4()),
            'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15',
                      'line_codes': ['L'], 'model_codes': ['M']},
        }],
    }

    def complete_preview():
        response = post(client, 'preview', request)
        assert response.status_code == 200, response.text
        body = response.json()
        independent = client.post('/auto_plan/api/plans/evaluate', json={
            'snapshot': body['snapshot'],
        })
        assert independent.status_code == 200, independent.text
        assert body['evaluation'] == independent.json()
        assert body['evaluation'] == evaluate(
            EvaluationSnapshot.model_validate(body['snapshot'])
        ).model_dump(mode='json')
        plans.output_cache = None
        try:
            fresh = post(client, 'preview', request)
            assert fresh.status_code == 200, fresh.text
            assert fresh.json() == body
        finally:
            plans.output_cache = cache
        return body

    expected = complete_preview()
    totals = expected['evaluation']['output_totals']
    assert (totals['expected'], totals['on_time'], totals['gap']) == (209, 200, 0)
    assert {value['model_code']: (value['on_time'], value['gap'])
            for value in expected['evaluation']['weekly_results']} == {
                'M': (100, 0), 'N': (100, 0),
            }
    assert cache.stats()['hits'] > 0  # Exercises reuse, not a latency assertion.

    # HTTP JSON is owned by its caller. The direct service check below also
    # detects aliases that network serialization would otherwise hide.
    public_value = complete_preview()
    public_value['evaluation']['output_sources'][0]['portions'].clear()
    public_value['evaluation']['attributions'][0]['material_batches'].append({
        'batch_id': 'POISON', 'quantity': 999999,
    })
    service = PlanService(plans)
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    service_value = service.preview(PlanCommand.model_validate(request), 'EDITOR', now)
    assert service_value == expected
    source = next(item for item in service_value['evaluation']['output_sources'] if item['lines'])
    source['lines'].clear()
    source['portions'].clear()
    service_value['evaluation']['attributions'][0]['shipping_batches'].append({
        'batch_id': 'POISON', 'quantity': 999999,
    })
    service_value['evaluation']['weekly_results'].clear()
    assert service.preview(PlanCommand.model_validate(request), 'EDITOR', now) == expected

    snapshot = EvaluationSnapshot.model_validate(expected['snapshot'])
    typed = evaluate(snapshot, output_cache=cache)
    assert typed.model_dump(mode='json') == expected['evaluation']
    source = next(item for item in typed.output_sources if item.lines)
    source.lines.clear()
    source.portions.clear()
    typed.attributions[0].material_batches.append(BatchUse(batch_id='POISON', quantity=999999))
    typed.weekly_results.clear()
    assert evaluate(snapshot).model_dump(mode='json') == expected['evaluation']
    assert complete_preview() == expected
    assert client.get('/auto_plan/api/plans/current').json() == before
    assert inputs.read().model_dump(mode='json') == source_inputs
    assert plans.context == source_context

    # Already-warm entries must never hide an unsupported or invalid context.
    # Each case retains whole-body parity with both independent fresh paths.
    try:
        confirmed = deepcopy(source_inputs)
        confirmed['business']['confirmations'] = [
            confirmation('material', '2026-10-12', 1, '2026-10-15', 'VALIDATION0'),
        ]
        inputs.versions[1] = InputSnapshot.model_validate(confirmed)
        body = complete_preview()
        assert any(value['batch_id'] == 'VALIDATION0'
                   for value in body['evaluation']['confirmation_usage'])
        duplicated = deepcopy(source_inputs)
        duplicated['business']['demands'].append(deepcopy(duplicated['business']['demands'][0]))
        inputs.versions[1] = InputSnapshot.model_validate(duplicated)
        body = complete_preview()
        assert 'demand_duplicate' in {
            issue['code'] for issue in body['evaluation']['issues']
        }
        assert body['evaluation']['executable'] is False
        inputs.versions[1] = InputSnapshot.model_validate(source_inputs)
        plans.context = source_context.model_copy(update={
            'reset': source_context.reset.model_copy(update={
                'cutoff': datetime(2026, 10, 13, 8, tzinfo=timezone.utc),
            }),
        })
        body = complete_preview()
        assert 'reset_context' in {issue['code'] for issue in body['evaluation']['issues']}
        assert body['evaluation']['executable'] is False
    finally:
        inputs.versions[1] = InputSnapshot.model_validate(source_inputs)
        plans.context = source_context
    assert client.get('/auto_plan/api/plans/current').json() == before

    # Audit the retained representation independently from public behavior.
    assert cache._entries
    for retained in cache._entries.values():
        _assert_immutable_json_graph(retained)


def test_retained_memory_accounts_for_graph_keys_metadata_and_variable_container_storage():
    from tests.test_output_reuse import reuse_source
    cache = OutputReuse()
    for quantity in (99, 100, 101):
        raw = reuse_source()
        raw['plan']['segments'][0]['quantity'] = quantity
        snapshot = EvaluationSnapshot.model_validate(raw)
        assert evaluate(snapshot, output_cache=cache) == evaluate(snapshot)
    def size(value, seen):
        if id(value) in seen:
            return 0
        seen.add(id(value))
        children = [getattr(value, f.name) for f in fields(value)] if is_dataclass(value) else value if type(value) is tuple else ()
        return sys.getsizeof(value) + sum(size(child, seen) for child in children)
    required = sys.getsizeof(cache._entries) - sys.getsizeof(OrderedDict())
    for key, retained in cache._entries.items():
        # A producer is already a live module function. Its reference storage
        # is counted in the key tuple; its module globals are not cache payload.
        required += sys.getsizeof(key) + sys.getsizeof(key[1]) + size(retained, set())
    assert len(cache._entries) >= 4
    assert cache.stats()['bytes'] >= required
    assert cache.stats()['bytes'] <= cache.max_bytes


@pytest.mark.parametrize('limits', [
    {'max_nodes': 1}, {'max_entry_nodes': 1},
    {'max_bytes': 1}, {'max_entry_bytes': 1}, {'max_entries': 0},
])
def test_cold_oversized_or_disabled_json_reuse_has_complete_fresh_parity(limits):
    from auto_plan.rules import _evaluation_json
    from tests.test_output_reuse import reuse_source
    snapshot = EvaluationSnapshot.model_validate(reuse_source())
    cache = OutputReuse(**limits)
    for _ in range(2):
        assert _evaluation_json(snapshot, output_cache=cache) == evaluate(snapshot).model_dump(mode='json')
    assert cache.stats()['entries'] == cache.stats()['bytes'] == cache.stats()['nodes'] == 0


@pytest.mark.parametrize('field,bad', [
    ('quantity', -1), ('model_code', ''), ('input_date', 'not-a-date'),
    ('portions', [{'date': '2026-10-15', 'fraction': '1', 'before_rounding': '1', 'quantity': -1}]),
    ('lines', [{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': -1}]),
])
def test_admission_validates_all_updated_derived_fields_before_retaining(field, bad):
    from tests.test_output_reuse import reuse_source
    from auto_plan.output_rules import _output_from_cells
    snapshot = EvaluationSnapshot.model_validate(reuse_source())
    result = evaluate(snapshot)
    part = {name: getattr(result, name) for name in ('output_sources', 'attributions', 'weekly_results')}
    part['output_sources'] = [part['output_sources'][0].model_copy(update={field: bad})]
    cache = OutputReuse()
    assert cache._put((_output_from_cells, b'0' * 32), part) is None
    assert cache.stats()['entries'] == cache.stats()['bytes'] == 0


@pytest.mark.parametrize('collection,field,bad', [
    ('attributions', 'quantity', -1),
    ('attributions', 'material_batches', [{'batch_id': 'B', 'quantity': -1}]),
    ('attributions', 'shipping_batches', [{'batch_id': '', 'quantity': 1}]),
    ('weekly_results', 'demand', -1),
    ('weekly_results', 'week', 'not-a-date'),
])
def test_admission_validates_attributions_batches_and_weekly_values(collection, field, bad):
    from tests.test_output_reuse import reuse_source
    from auto_plan.output_rules import _output_from_cells
    result = evaluate(EvaluationSnapshot.model_validate(reuse_source()))
    part = {name: list(getattr(result, name)) for name in ('output_sources', 'attributions', 'weekly_results')}
    part[collection][0] = part[collection][0].model_copy(update={field: bad})
    cache = OutputReuse()
    assert cache._put((_output_from_cells, b'0' * 32), part) is None
    assert cache.stats()['entries'] == cache.stats()['bytes'] == 0


def test_node_eviction_and_empty_map_release_keep_complete_owned_json():
    from auto_plan.rules import _evaluation_json
    from tests.test_output_reuse import reuse_source
    snapshot = EvaluationSnapshot.model_validate(reuse_source())
    expected = evaluate(snapshot).model_dump(mode='json')
    measured = OutputReuse()
    assert _evaluation_json(snapshot, output_cache=measured) == expected
    node_cap = max(value[2] for value in measured._entries.values())
    weight_cap = max(value[1] for value in measured._entries.values())
    limited = OutputReuse(max_nodes=node_cap)
    for _ in range(2):
        assert _evaluation_json(snapshot, output_cache=limited) == expected
    assert limited.stats()['nodes'] <= node_cap
    assert limited.stats()['evictions'] > 0
    # Payload fits, but variable OrderedDict storage also has to fit. If it
    # cannot, self-eviction must release that variable allocation entirely.
    empty = OutputReuse(max_bytes=weight_cap)
    assert _evaluation_json(snapshot, output_cache=empty) == expected
    assert empty.stats()['entries'] == empty.stats()['bytes'] == empty.stats()['nodes'] == 0
    assert empty.stats()['evictions'] > 0
