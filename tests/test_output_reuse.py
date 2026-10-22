"""Public planning results remain identical when pure output parts are reused."""
from copy import deepcopy
from uuid import uuid4
import pytest
from auto_plan.input_models import InputSnapshot
from auto_plan.plan_models import EvaluationSnapshot
from auto_plan.rules import evaluate
from tests.test_plan_transactions import shared_client, command, post, row


@pytest.mark.parametrize('opening_wip', [None, 7])
def test_public_quantity_preview_reuses_unchanged_output_with_owned_complete_results(shared_client, opening_wip):
    from auto_plan.output_reuse import OutputReuse
    client, plans, inputs = shared_client
    source = inputs.read().model_dump(mode='json')
    source['business']['demands'] = [
        {'model_code': model, 'week': '2026-10-12', 'quantity': 100}
        for model in ('M', 'N')
    ]
    inputs.versions[1] = InputSnapshot.model_validate(source)
    if opening_wip is not None:
        from auto_plan.plan_models import ResetContext
        plans.context = plans.context.model_copy(update={'reset': ResetContext.model_validate({
            'batch_id': 'OBSERVED', 'production_week': '2026-10-12',
            'cutoff': '2026-10-12T08:00:00+08:00', 'models': [
                {'model_code': 'M', 'wip': 7, 'net_balance': 0},
                {'model_code': 'N', 'wip': 3, 'net_balance': 0}]})})
    plan = {'segments': [row('m'), row('n', 'N', '2026-10-16')],
            'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}]}
    saved = post(client, 'save', command({'kind': 'replace_plan', 'plan': plan}))
    assert saved.status_code == 200, saved.text
    reuse = plans.output_cache = OutputReuse()
    # Loading the actual current source is the natural first computation.
    before = client.get('/auto_plan/api/plans/current').json()
    assert {key: before['evaluation']['output_totals'][key] for key in ('expected', 'on_time', 'gap')} == {
        'expected': 200 if opening_wip is None else 210, 'on_time': 200, 'gap': 0}
    request = {'base_revision': before['revision'], 'input_version': before['input_version'],
               'latest_adoption': before['latest_adoption'], 'operations': [{
                   'kind': 'change_quantity', 'segment_id': 'm', 'quantity': 99,
                   'end_date': '2026-10-15', 'action_id': str(uuid4()),
                   'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15',
                             'line_codes': ['L'], 'model_codes': ['M']}}]}
    response = post(client, 'preview', request)
    assert response.status_code == 200, response.text
    actual = response.json()
    assert {key: actual['evaluation']['output_totals'][key] for key in ('expected', 'on_time', 'gap')} == {
        'expected': 199 if opening_wip is None else 209,
        'on_time': 199 if opening_wip is None else 200, 'gap': 1 if opening_wip is None else 0}
    assert {value['model_code']: (value['on_time'], value['gap'])
            for value in actual['evaluation']['weekly_results']} == {'M': (99, 1) if opening_wip is None else (100, 0), 'N': (100, 0)}
    assert reuse.stats()['hits'] >= 1, 'The unchanged N output must be reused.'
    assert actual['evaluation'] == evaluate(EvaluationSnapshot.model_validate(actual['snapshot'])).model_dump(mode='json')
    expected = deepcopy(actual)
    plans.output_cache = None
    assert post(client, 'preview', request).json() == expected
    plans.output_cache = reuse
    actual['evaluation']['output_sources'][0]['portions'][0]['quantity'] = 999999
    assert post(client, 'preview', request).json() == expected
    assert client.get('/auto_plan/api/plans/current').json() == before


def reuse_source():
    from tests.test_plan_preview import snapshot
    data = snapshot()
    data['time'] = {'as_of': '2026-10-14T09:00:00+08:00', 'window_start': '2026-10-12'}
    data['plan'] = {'segments': [row('m'), row('n', 'N') | {'order': 1}],
                    'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}]}
    data['inputs']['business'] = {'demands': [
        {'model_code': model, 'week': '2026-10-12', 'quantity': 100} for model in ('M', 'N')]}
    for parameter in data['inputs']['parameters']['line_yields']:
        if parameter['model_code'] == 'M':
            parameter['percent'] = '95'
        elif parameter['model_code'] == 'N':
            parameter['percent'] = '80'
    for parameter in data['inputs']['parameters']['model_yields']:
        if parameter['model_code'] == 'M':
            parameter['percent'] = '98'
        elif parameter['model_code'] == 'N':
            parameter['percent'] = '90'
    for parameter in data['inputs']['parameters']['cycle_times']:
        parameter['days'] = '0.5'
    return data


@pytest.mark.parametrize('change', ['quantity', 'yield', 'line_yield', 'ct', 'holiday', 'demand',
                                   'same_day', 'day_boundary', 'facts', 'adoption', 'reset',
                                   'duplicate_demand', 'duplicate_fact', 'missing_ct', 'confirmation',
                                   'invalid_reset', 'duplicate_reset', 'partial_missing_ct',
                                   'week_boundary', 'tied_portions'])
def test_reuse_matches_complete_default_evaluator_after_dependency_or_invalid_state_change(change):
    from auto_plan.output_reuse import OutputReuse
    from tests.test_output_preview import confirmation
    raw = reuse_source()
    cache = OutputReuse()
    original = EvaluationSnapshot.model_validate(raw)
    assert evaluate(original, output_cache=cache) == evaluate(original)
    assert evaluate(original, output_cache=cache) == evaluate(original)
    if change == 'quantity': raw['plan']['segments'][0]['quantity'] = 99
    elif change == 'yield': raw['inputs']['parameters']['model_yields'][0]['percent'] = '93.1'
    elif change == 'line_yield':
        next(row for row in raw['inputs']['parameters']['line_yields'] if row['model_code'] == 'N')['percent'] = '85'
    elif change == 'ct': raw['inputs']['parameters']['cycle_times'][0]['days'] = '1.25'
    elif change == 'holiday': raw['inputs']['calendar'] = {'holidays': [{'date': '2026-10-16', 'holiday': True}]}
    elif change == 'demand': raw['inputs']['business']['demands'][0]['quantity'] = 101
    elif change == 'same_day': raw['time']['as_of'] = '2026-10-14T10:00:00+08:00'
    elif change == 'day_boundary': raw['time']['as_of'] = '2026-10-15T09:00:00+08:00'
    elif change in ('facts', 'duplicate_fact'):
        raw['time']['as_of'] = '2026-10-15T09:00:00+08:00'
        fact = {'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 20}
        raw['execution'] = {'cutoff': raw['time']['as_of'], 'facts': [fact] * (2 if change == 'duplicate_fact' else 1),
                            'coverage': [{'start_date': '2026-10-12', 'end_date': '2026-10-15',
                                          'line_codes': ['L'], 'model_codes': ['M', 'N'], 'blanks_are_zero': True}]}
    elif change == 'adoption':
        raw['baseline'] = {'version': 1, 'plan': deepcopy(raw['plan']), 'coverage': []}
    elif change == 'duplicate_demand': raw['inputs']['business']['demands'].append(deepcopy(raw['inputs']['business']['demands'][0]))
    elif change == 'missing_ct': raw['inputs']['parameters']['cycle_times'] = []
    elif change == 'partial_missing_ct': raw['inputs']['parameters']['cycle_times'] = [r for r in raw['inputs']['parameters']['cycle_times'] if r['model_code'] != 'M']
    elif change == 'week_boundary':
        raw['time'] = {'as_of': '2026-10-19T09:00:00+08:00', 'window_start': '2026-10-19'}
        for segment in raw['plan']['segments']: segment['date'] = '2026-10-22'
        for demand in raw['inputs']['business']['demands']: demand['week'] = '2026-10-19'
    elif change == 'tied_portions':
        raw['inputs']['calendar'] = {'holidays': [{'date': '2026-10-15', 'holiday': True}],
                                    'opening_exceptions': [{'date': '2026-10-15', 'line_code': 'L', 'enabled': True}]}
    elif change == 'confirmation': raw['inputs']['business']['confirmations'] = [confirmation('material', '2026-10-12', 20, '2026-10-15')]
    elif change in ('reset', 'invalid_reset', 'duplicate_reset'):
        model = {'model_code': 'M', 'wip': 7, 'net_balance': 0}
        raw['reset'] = {'batch_id': 'OBSERVED', 'production_week': '2026-10-12',
                        'cutoff': '2026-10-13T08:00:00+08:00' if change == 'invalid_reset' else '2026-10-12T08:00:00+08:00',
                        'models': [model] * (2 if change == 'duplicate_reset' else 1)}
    changed = EvaluationSnapshot.model_validate(raw)
    expected = evaluate(changed).model_dump(mode='json')
    if change == 'line_yield':
        assert cache.stats()['hits'] >= 1
    from auto_plan.rules import _evaluation_json
    owned_json = _evaluation_json(changed, output_cache=cache)
    assert owned_json == expected
    if owned_json['output_sources']:
        owned_json['output_sources'][0]['portions'].clear()
    assert _evaluation_json(changed, output_cache=cache) == expected
    first = evaluate(changed, output_cache=cache)
    assert first.model_dump(mode='json') == expected
    if first.output_sources:
        first.output_sources[0].portions.clear()
    assert evaluate(changed, output_cache=cache).model_dump(mode='json') == expected


# The eviction case must admit one complete immutable object graph. Its
# accounted memory includes rows/keys/containers, beyond serialized JSON bytes.
@pytest.mark.parametrize('limits', [(1, 100000, 100000), (10, 64, 64), (0, 10000, 10000)])
def test_reuse_bounds_entries_and_bytes_without_changing_results_and_owns_concurrent_values(limits):
    from concurrent.futures import ThreadPoolExecutor
    from auto_plan.output_reuse import OutputReuse
    count, size, entry = limits
    cache = OutputReuse(max_entries=count, max_bytes=size, max_entry_bytes=entry)
    sources = []
    for quantity in (99, 100, 101, 102):
        raw = reuse_source()
        raw['plan']['segments'][0]['quantity'] = quantity
        sources.append(EvaluationSnapshot.model_validate(raw))
    def run(source):
        result = evaluate(source, output_cache=cache)
        assert result == evaluate(source)
        if result.output_sources:
            result.output_sources[0].portions.clear()
        return evaluate(source, output_cache=cache).model_dump(mode='json')
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(run, sources))
    assert results == [evaluate(source).model_dump(mode='json') for source in sources]
    assert cache.stats()['entries'] <= count and cache.stats()['bytes'] <= size
    if count == 1:
        assert cache.stats()['evictions'] > 0
    else:
        assert cache.stats()['entries'] == cache.stats()['bytes'] == 0


@pytest.mark.parametrize('change', ['batch', 'wip', 'net', 'holiday', 'yield'])
def test_warm_explicit_wip_parts_track_reset_identity_quantities_calendar_and_yields(change):
    from auto_plan.output_reuse import OutputReuse
    raw = reuse_source()
    raw['reset'] = {'batch_id': 'OBSERVED', 'production_week': '2026-10-12',
                    'cutoff': '2026-10-12T08:00:00+08:00',
                    'models': [{'model_code': model, 'wip': 7, 'net_balance': 0} for model in ('M', 'N')]}
    cache = OutputReuse()
    source = EvaluationSnapshot.model_validate(raw)
    assert evaluate(source, output_cache=cache) == evaluate(source)
    assert evaluate(source, output_cache=cache) == evaluate(source)
    if change == 'batch': raw['reset']['batch_id'] = 'NEXT'
    elif change == 'wip': raw['reset']['models'][0]['wip'] = 8
    elif change == 'net': raw['reset']['models'][0]['net_balance'] = -3
    elif change == 'holiday': raw['inputs']['calendar'] = {'holidays': [{'date': '2026-10-12', 'holiday': True}]}
    elif change == 'yield': raw['inputs']['parameters']['model_yields'][0]['percent'] = '93.1'
    changed = EvaluationSnapshot.model_validate(raw)
    expected = evaluate(changed)
    assert evaluate(changed, output_cache=cache) == expected
    assert evaluate(changed, output_cache=cache) == expected
