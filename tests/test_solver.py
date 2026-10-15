"""Candidate business API cases, expected quantities worked independently."""
from tests.test_plan_transactions import shared_client
from auto_plan.input_models import InputSnapshot
import json
from pathlib import Path
import pytest


def setup(inputs, demands, capacity=10, **extra):
    data = inputs.read().model_dump(mode='json')
    for row in data['parameters']['capacities']:
        row['pieces'] = capacity
    data['business']['demands'] = demands
    data['business'].update(extra)
    inputs.versions[1] = InputSnapshot.model_validate(data)


def solve(client, **extra):
    return client.post('/auto_plan/api/plans/candidates/preview', json={
        'base_revision': 0, 'input_version': 1, 'latest_adoption': 0,
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15',
                              'line_codes': ['L']}, **extra}})


def test_empty_draft_creates_absent_model_and_returns_valid_partial_candidate(shared_client):
    client, plans, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 20}])
    response = solve(client)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'feasible'
    candidate = result['candidates'][0]
    assert sum(r['quantity'] for r in candidate['plan']['segments']) == 10
    assert candidate['objective'] == ['10'] + ['0'] * 14 + ['10'] + ['0'] * 17
    assert candidate['evaluation']['executable'] is True
    assert candidate['input_version'] == 1 and candidate['source_revision'] == 0
    assert candidate['snapshot']['plan'] == {'segments': [], 'switches': [], 'initial_states': [], 'ordinary_idle': []}
    assert plans.revision == plans.adoption == 0
    assert len(candidate['stage_bounds']) > 0


def test_demand_reduction_removes_useless_input_even_against_adopted_baseline(shared_client):
    from tests.test_plan_transactions import command, post, row
    client, plans, inputs = shared_client
    setup(inputs, [])
    plan = {'segments': [row(quantity=10) | {'hours': '24'}]}
    assert post(client, 'adopt', command({'kind': 'replace_plan', 'plan': plan}) | {'pending': False}).status_code == 200
    response = client.post('/auto_plan/api/plans/candidates/preview', json={
        'base_revision': 1, 'input_version': 1, 'latest_adoption': 1,
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L']}}})
    assert response.status_code == 200, response.text
    candidate = response.json()['candidates'][0]
    assert candidate['plan']['segments'] == []
    assert candidate['objective'][31] == '30'
    assert candidate['snapshot']['baseline']['plan']['segments'][0]['quantity'] == 10
    assert plans.adoption == 1


def test_all_ontime_layers_precede_late_coverage_and_general_shipping_quota_is_respected(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1},
                   {'model_code': 'N', 'week': '2026-10-19', 'quantity': 1}], capacity=1,
          confirmations=[{'model_code': 'M', 'week': '2026-10-12', 'kind': 'shipping',
                          'batch_id': 'late', 'status': 'allowed', 'date': '2026-10-20', 'quantity': 1}])
    response = solve(client, scope={'start_date': '2026-10-20', 'end_date': '2026-10-20', 'line_codes': ['L']})
    assert response.status_code == 200, response.text
    candidate = response.json()['candidates'][0]
    # Enumerate empty, M1, N1. N1 uniquely minimizes the week-2 on-time gap,
    # even though M1 could close the week-1 final gap instead.
    assert [(r['model_code'], r['quantity']) for r in candidate['plan']['segments']] == [('N', 1)]
    assert candidate['objective'][:2] == ['1', '0']
    assert candidate['objective'][15:17] == ['1', '0']


def test_free_replan_relocates_holiday_input_to_an_authorized_open_line(shared_client):
    from tests.test_plan_transactions import row
    from auto_plan.plan_models import Plan
    client, plans, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 5}])
    data = inputs.read().model_dump(mode='json')
    data['calendar'] = {'holidays': [{'date': '2026-10-15', 'holiday': True}],
                        'opening_exceptions': [{'date': '2026-10-15', 'line_code': 'B', 'enabled': True}]}
    inputs.versions[1] = InputSnapshot.model_validate(data)
    plans.plan = Plan(segments=[row(quantity=5)])
    plans.revisions[0]['snapshot']['plan'] = plans.plan.model_dump(mode='json')
    response = solve(client, mode='all_replan', scope={'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L', 'B']})
    assert response.status_code == 200, response.text
    candidate = response.json()['candidates'][0]
    assert [(r['line_code'], r['quantity']) for r in candidate['plan']['segments']] == [('B', 5)]
    assert candidate['objective'][:30] == ['0'] * 30


def test_locked_aggregate_allows_order_repair_and_lock_zero_forbids_insertion(shared_client):
    from auto_plan.plan_models import Plan
    from auto_plan.plans import PlanningContext
    from tests.test_plan_transactions import row
    client, plans, inputs = shared_client
    setup(inputs, [{'model_code': model, 'week': '2026-10-12', 'quantity': 1} for model in ('M', 'N')])
    old = Plan(segments=[row('n', 'N', quantity=1) | {'hours': '1'}, row('m', 'M', quantity=1) | {'order': 1, 'hours': '2'}])
    adopted = old.model_copy(update={'segments': [r.model_copy(update={'order': 1-r.order}) for r in old.segments]})
    plans.plan = old
    plans.revisions[0]['snapshot']['plan'] = old.model_dump(mode='json')
    plans.adoption = 1
    plans.adoptions[1] = {'snapshot': {'plan': adopted.model_dump(mode='json')}, 'coverage': [{'start_date': '2026-10-12', 'end_date': '2027-01-24', 'line_codes': ['L'], 'model_codes': ['M', 'N'], 'blanks_are_zero': True}]}
    plans.context = PlanningContext(locks=[{'date': '2026-10-15', 'line_code': 'L', 'model_code': model, 'quantity': 1} for model in ('M', 'N')])
    response = client.post('/auto_plan/api/plans/candidates/preview', json={
        'base_revision': 0, 'input_version': 1, 'latest_adoption': 1,
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L']}}})
    assert response.status_code == 200, response.text
    candidate = response.json()['candidates'][0]
    assert candidate['objective'][32] == '0'
    assert [r['model_code'] for r in sorted(candidate['plan']['segments'], key=lambda r: r['order'])] == ['M', 'N']


@pytest.mark.parametrize('strategy', ['hybrid', 'cp_sat', 'highs'])
def test_exact_fractional_hour_witness_and_native_routes_are_real_and_isolated(shared_client, strategy):
    import sys
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': model, 'week': '2026-10-12', 'quantity': 2} for model in ('M', 'P')], capacity=1000)
    data = inputs.read().model_dump(mode='json')
    data['calendar']['maintenance'] = [{'date': '2026-10-15', 'line_code': 'L', 'hours': 23}]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    response = solve(client, strategy=strategy)
    assert response.status_code == 200, response.text
    result = response.json()
    candidate = result['candidates'][0]
    assert candidate['objective'][:30] == ['0'] * 30
    assert candidate['objective'][30] == '1'
    from fractions import Fraction
    assert Fraction('0.998') < Fraction(candidate['evaluation']['days'][0]['occupied_hours']['exact']) <= 1
    assert any('native ' + ('cp_sat' if strategy == 'hybrid' else strategy) + ': optimal_in_table' in stage['detail'] for stage in result['stage_bounds'])
    assert 'highspy' not in sys.modules and 'ortools' not in sys.modules
    evaluation = client.post('/auto_plan/api/plans/evaluate', json={'snapshot': candidate['snapshot'] | {'plan': candidate['plan']}})
    assert evaluation.status_code == 200 and evaluation.json()['executable'] is True


@pytest.mark.parametrize('status,quota,covered', [('allowed', 3, 3), ('forbidden', 10, 0), ('pending', 10, 0)])
def test_general_material_quota_and_whole_week_ban_keep_valid_partial_plans(shared_client, status, quota, covered):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-19', 'quantity': 10}],
          confirmations=[{'model_code': 'M', 'week': '2026-10-19', 'kind': 'material',
                          'batch_id': 'partial', 'status': status, 'date': '2026-10-15', 'quantity': quota}])
    response = solve(client)
    assert response.status_code == 200, response.text
    candidate = response.json()['candidates'][0]
    assert sum(r['quantity'] for r in candidate['plan']['segments']) == covered
    assert candidate['evaluation']['executable'] is True
    assert candidate['objective'][1] == candidate['objective'][16] == str(10-covered)


def test_missing_critical_parameter_is_incomplete_instead_of_a_zero_output_recommendation(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1}])
    data = inputs.read().model_dump(mode='json')
    data['parameters']['cycle_times'] = []
    inputs.versions[1] = InputSnapshot.model_validate(data)
    result = solve(client).json()
    assert result['status'] == 'incomplete'
    assert result['candidates'] == []
    assert any(issue['severity'] == 'incomplete' for issue in result['issues'])


def test_new_week_uses_idle_capacity_and_preserves_existing_rows_and_switches(shared_client):
    from tests.test_plan_transactions import post, command, row
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 2},
                   {'model_code': 'P', 'week': '2026-10-12', 'quantity': 2}], capacity=1000)
    existing = row(quantity=2) | {'hours': '1', 'order': 5}
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': [existing]})).status_code == 200
    response = client.post('/auto_plan/api/plans/candidates/preview', json={
        'base_revision': 1, 'input_version': 1, 'latest_adoption': 0,
        'request': {'mode': 'new_week', 'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-16', 'line_codes': ['L']}}})
    assert response.status_code == 200, response.text
    candidate = response.json()['candidates'][0]
    assert next(r for r in candidate['plan']['segments'] if r['id'] == 'a') == existing
    assert candidate['objective'][:30] == ['0'] * 30
    assert candidate['evaluation']['executable'] is True


def test_fractional_ct_rounding_tail_is_retained_and_nominal_input_is_not_a_floor(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 999}], capacity=2000)
    data = inputs.read().model_dump(mode='json')
    for row in data['parameters']['model_yields']:
        if row['model_code'] == 'M':
            row['percent'] = '93.10'
    for row in data['parameters']['cycle_times']:
        if row['model_code'] == 'M':
            row['days'] = '0.50'
    inputs.versions[1] = InputSnapshot.model_validate(data)
    candidate = solve(client).json()['candidates'][0]
    # 1072 * .931 / 2 = 499.016, each portion rounds to500.
    # 1071 * .931 / 2 = 498.5505, each portion rounds to499.
    assert sum(r['quantity'] for r in candidate['plan']['segments']) == 1072
    assert candidate['evaluation']['output_totals']['unallocated'] == 1
    assert candidate['objective'][:30] == ['0'] * 30


@pytest.mark.parametrize('fixture', json.loads((Path(__file__).parents[1] / 'docs/validation/solver-small-oracle.json').read_text()))
def test_candidate_matches_independent_exhaustive_small_case(shared_client, fixture):
    from fractions import Fraction
    from datetime import date, timedelta
    client, _, inputs = shared_client
    case = fixture['case']
    demands = [{'model_code': model, 'week': week, 'quantity': case['demand'][m][w]}
               for m, model in enumerate(('M', 'N')) for w, week in enumerate(('2026-10-12', '2026-10-19'))]
    confirmations = []
    for model in ('M', 'N'):
        if case.get('early_ban'):
            confirmations.append({'model_code': model, 'week': '2026-10-19', 'kind': 'material',
                                  'batch_id': 'ban', 'status': 'forbidden'})
        if case.get('late_quota'):
            confirmations.append({'model_code': model, 'week': '2026-10-12', 'kind': 'shipping',
                                  'batch_id': 'late', 'status': 'allowed', 'date': '2026-10-20', 'quantity': case['late_quota']})
    setup(inputs, demands, capacity=case['capacity'], confirmations=confirmations)
    data = inputs.read().model_dump(mode='json')
    for row in data['parameters']['model_yields']:
        row['percent'] = str(Fraction(case['yield']) * 100)
    data['calendar']['holidays'] = [{'date': (date(2026, 10, 16) + timedelta(days=n)).isoformat(), 'holiday': True} for n in range(4)]
    inputs.versions[1] = InputSnapshot.model_validate(data)
    response = solve(client, scope={'start_date': '2026-10-15', 'end_date': '2026-10-20',
                                    'line_codes': ['L', 'B'][:case['lines']]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['candidates'][0]['objective'] == fixture['objective']


def test_outside_rows_order_and_zero_duration_switch_are_preserved_exactly(shared_client):
    from tests.test_plan_transactions import post, command, row
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': model, 'week': '2026-10-12', 'quantity': 1} for model in ('M', 'P')], capacity=10)
    data = inputs.read().model_dump(mode='json')
    data['parameters']['switch_durations'][0]['hours'] = '0'
    inputs.versions[1] = InputSnapshot.model_validate(data)
    old = {'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}],
           'segments': [row('fixed', 'P', day='2026-10-16', quantity=1) | {'order': 7, 'hours': '3'}],
           'switches': [{'id': 'zero-outside', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S',
                         'allocations': [{'date': '2026-10-16', 'order': 6, 'hours': '0'}]}]}
    saved = post(client, 'save', command({'kind': 'replace_plan', 'plan': old}))
    assert saved.status_code == 200, saved.text
    source = client.get('/auto_plan/api/plans/current').json()['plan']
    response = client.post('/auto_plan/api/plans/candidates/preview', json={
        'base_revision': 1, 'input_version': 1, 'latest_adoption': 0,
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L'], 'model_codes': ['M']}}})
    assert response.status_code == 200, response.text
    plan = response.json()['candidates'][0]['plan']
    assert next(r for r in plan['segments'] if r['id'] == 'fixed') == source['segments'][0]
    assert plan['switches'] == source['switches']


def test_compute_stop_keeps_the_last_whole_valid_candidate_without_global_claim(shared_client):
    from auto_plan.solver import solve as compute
    from auto_plan.solver_models import FrozenSolveInput
    from auto_plan.plans import PlanService
    from datetime import datetime, timezone
    client, plans, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1}])
    frozen = plans.read('EDITOR', lambda unit: FrozenSolveInput(snapshot=PlanService(plans)._snapshot(unit, unit.plan, datetime(2026, 10, 14, tzinfo=timezone.utc)),
        source_revision=0, input_version=1, latest_adoption=0, request={'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L']}}))
    stop = [False]
    def progress(event):
        if event['kind'] == 'candidate':
            stop[0] = True
    result = compute(frozen, progress=progress, should_stop=lambda: stop[0])
    assert result.status == 'stopped' and len(result.candidates) == 1
    assert result.candidates[0].evaluation.executable and not result.global_optimal
    assert plans.revision == 0 and frozen.snapshot.plan.segments == []


def test_impossible_protected_capacity_has_a_local_proof_distinct_from_demand_shortage(shared_client):
    from auto_plan.plans import PlanningContext
    from auto_plan.plan_models import Plan
    from tests.test_plan_transactions import row
    client, plans, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 20}], capacity=100)
    plans.plan = Plan(segments=[row(quantity=101) | {'hours': '24'}])
    plans.revisions[0]['snapshot']['plan'] = plans.plan.model_dump(mode='json')
    plans.context = PlanningContext(locks=[{'date': '2026-10-15', 'line_code': 'L', 'model_code': 'M', 'quantity': 101}])
    result = solve(client).json()
    assert result['status'] == 'scope_conflict' and result['candidates'] == []
    assert any(stage['status'] == 'proven_protected_conflict' and not stage['global_proof'] for stage in result['stage_bounds'])
    assert result['global_optimal'] is False


@pytest.mark.parametrize('strategy', ['cp_sat', 'highs'])
def test_native_quantity_repair_runs_outside_the_finite_table_and_labels_its_relaxation(shared_client, strategy):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1000}], capacity=2000)
    result = solve(client, strategy=strategy).json()
    assert result['candidates'][0]['objective'][:30] == ['0'] * 30
    stage = next(row for row in result['stage_bounds'] if row['stage'] == 'native_recipe:R')
    assert stage['status'] == 'restricted_relaxation'
    assert 'Optimal' in stage['detail'] or 'OPTIMAL' in stage['detail']
    assert stage['lower_bound'] is None and stage['global_proof'] is False

