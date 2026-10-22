"""Candidate business API cases, expected quantities worked independently."""
from tests.test_plan_transactions import shared_client as shared_plan_client
from tests.job_http_support import calculate
from auto_plan.input_models import InputSnapshot
import json
from pathlib import Path
import pytest


@pytest.fixture
def shared_client(shared_plan_client):
    from tests.test_jobs import install_controller
    client,plans,inputs=shared_plan_client
    controller=install_controller(client,plans)
    try: yield client,plans,inputs
    finally: controller.shutdown()


def setup(inputs, demands, capacity=10, **extra):
    data = inputs.read().model_dump(mode='json')
    for row in data['parameters']['capacities']:
        row['pieces'] = capacity
    data['business']['demands'] = demands
    data['business'].update(extra)
    inputs.versions[1] = InputSnapshot.model_validate(data)


def solve(client, **extra):
    return calculate(client, {
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
    assert candidate['snapshot']['plan']['segments'] == []
    assert candidate['snapshot']['plan']['current_day_witnesses'] == []
    assert plans.revision == plans.adoption == 0
    assert len(candidate['stage_bounds']) > 0


def test_demand_reduction_removes_useless_input_even_against_adopted_baseline(shared_client):
    from tests.test_plan_transactions import command, post, row
    client, plans, inputs = shared_client
    setup(inputs, [])
    plan = {'segments': [row(quantity=10) | {'hours': '24'}]}
    assert post(client, 'adopt', command({'kind': 'replace_plan', 'plan': plan}) | {'pending': False}).status_code == 200
    response = calculate(client, {
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
    response = calculate(client, {
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


def test_solver_stops_for_missing_model_month_without_global_or_cross_model_fallback(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1000}], capacity=2000)
    data = inputs.read().model_dump(mode='json')
    data['parameters']['line_yields'] = [
        {'month': '2026-10', 'model_code': 'N', 'percent': '80'},
        {'month': '2026-10', 'model_code': 'P', 'percent': '70'},
        {'month': '2026-10', 'percent': '100'},
    ]
    data['parameters']['model_yields'] = [
        {'month': '2026-10', 'model_code': 'M', 'percent': '90'},
        {'month': '2026-10', 'model_code': 'N', 'percent': '90'},
        {'month': '2026-10', 'model_code': 'P', 'percent': '90'},
    ]
    inputs.versions[1] = InputSnapshot.model_validate(data)

    result = solve(client).json()

    assert result['status'] == 'incomplete'
    assert result['candidates'] == []
    missing = next(issue for issue in result['issues'] if issue['code'] == 'missing_solver_parameter')
    assert (missing['model_code'], missing['yield_month']) == ('M', '2026-10')
    assert any(stage['stage'] == 'context_check' and stage['status'] == 'incomplete'
               for stage in result['stage_bounds'])


def test_zero_demand_input_can_be_removed_without_inventing_missing_yield(shared_client):
    from auto_plan.plan_models import Plan
    from tests.test_plan_transactions import row
    client, plans, inputs = shared_client
    setup(inputs, [])
    data = inputs.read().model_dump(mode='json')
    data['parameters']['model_yields'] = []
    inputs.versions[1] = InputSnapshot.model_validate(data)
    plans.plan = Plan(segments=[row(quantity=10) | {'hours': '24'}])
    plans.revisions[0]['snapshot']['plan'] = plans.plan.model_dump(mode='json')
    result = solve(client).json()
    assert result['candidates'][0]['plan']['segments'] == []
    assert result['candidates'][0]['evaluation']['executable'] is True


def test_new_week_uses_idle_capacity_and_preserves_existing_rows_and_switches(shared_client):
    from tests.test_plan_transactions import post, command, row
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 2},
                   {'model_code': 'P', 'week': '2026-10-12', 'quantity': 2}], capacity=1000)
    existing = row(quantity=2) | {'hours': '1', 'order': 5}
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': [existing]})).status_code == 200
    response = calculate(client, {
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


def test_solver_quantity_and_final_candidate_use_each_models_month_yields(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [
        {'model_code': 'M', 'week': '2026-10-12', 'quantity': 855},
        {'model_code': 'N', 'week': '2026-10-12', 'quantity': 720},
    ], capacity=2000)
    data = inputs.read().model_dump(mode='json')
    data['parameters']['line_yields'] = [
        {'month': '2026-10', 'model_code': 'M', 'percent': '95'},
        {'month': '2026-10', 'model_code': 'N', 'percent': '80'},
        {'month': '2026-10', 'model_code': 'P', 'percent': '100'},
    ]
    data['parameters']['model_yields'] = [
        {'month': '2026-10', 'model_code': 'M', 'percent': '90'},
        {'month': '2026-10', 'model_code': 'N', 'percent': '90'},
        {'month': '2026-10', 'model_code': 'P', 'percent': '100'},
    ]
    inputs.versions[1] = InputSnapshot.model_validate(data)

    result = solve(client, scope={'start_date': '2026-10-15', 'end_date': '2026-10-15',
                                  'line_codes': ['L', 'B']}).json()

    candidate = result['candidates'][0]
    assert result['status'] == 'feasible'
    assert sum(row['quantity'] for row in candidate['plan']['segments'] if row['model_code'] == 'M') > 0
    assert sum(row['quantity'] for row in candidate['plan']['segments'] if row['model_code'] == 'N') > 0
    assert {row['model_code']: row['on_time'] for row in candidate['evaluation']['weekly_results']} == {
        'M': 855, 'N': 720}
    sources = {row['model_code']: row for row in candidate['evaluation']['output_sources']}
    assert (sources['M']['line_yield'], sources['M']['model_yield']) == ('95', '90')
    assert (sources['N']['line_yield'], sources['N']['model_yield']) == ('80', '90')
    checked = client.post('/auto_plan/api/plans/evaluate', json={
        'snapshot': candidate['snapshot'] | {'plan': candidate['plan']}})
    assert checked.status_code == 200, checked.text
    assert checked.json() == candidate['evaluation']


def test_parameter_change_rejects_stale_candidate_and_new_task_uses_new_yield(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 855}], capacity=2000)
    data = inputs.read().model_dump(mode='json')
    data['parameters']['line_yields'][0]['percent'] = '95'
    data['parameters']['model_yields'][0]['percent'] = '90'
    data['parameters']['cycle_times'][0]['days'] = '0'
    inputs.versions[1] = InputSnapshot.model_validate(data)
    old_source = {'base_revision': 0, 'input_version': 1, 'latest_adoption': 0}
    old = calculate(client, old_source | {
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L']}}}).json()['candidates'][0]
    assert old['evaluation']['output_sources'][0]['line_yield'] == '95'

    changed = client.post('/auto_plan/api/inputs/apply', json={
        'base_version': 1,
        'changes': {'line_yields': [{'month': '2026-10', 'model_code': 'M', 'percent': '96'}]},
    })
    assert changed.status_code == 200, changed.text
    stale = client.post('/auto_plan/api/plans/preview', json=old_source | {
        'operations': old['server_reference']['operations']})
    assert stale.status_code == 409 and stale.json()['input_version'] > 1

    current = client.get('/auto_plan/api/plans/current').json()
    source = {name: current[name] for name in ('input_version', 'latest_adoption')} | {'base_revision': current['revision']}
    fresh = calculate(client, source | {
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L']}}}).json()['candidates'][0]
    assert fresh['input_version'] > old['input_version']
    assert fresh['evaluation']['output_sources'][0]['line_yield'] == '96'
    assert fresh['id'] != old['id']


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
    response = calculate(client, {
        'base_revision': 1, 'input_version': 1, 'latest_adoption': 0,
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L'], 'model_codes': ['M']}}})
    assert response.status_code == 200, response.text
    plan = response.json()['candidates'][0]['plan']
    assert next(r for r in plan['segments'] if r['id'] == 'fixed') == source['segments'][0]
    assert plan['switches'] == source['switches']


def test_unknown_initial_recipe_allows_matching_outside_work_without_changing_it(shared_client):
    from tests.test_plan_transactions import post, command, row
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1000},
                   {'model_code': 'N', 'week': '2026-10-12', 'quantity': 1}], capacity=2000)
    outside = row('outside', 'N', day='2026-10-16', quantity=1) | {'order': 7}
    assert post(client, 'save', command({'kind': 'patch_plan', 'segments': [outside]})).status_code == 200
    response = calculate(client, {
        'base_revision': 1, 'input_version': 1, 'latest_adoption': 0,
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L'], 'model_codes': ['M']}}})
    assert response.status_code == 200, response.text
    candidate = response.json()['candidates'][0]
    assert candidate['objective'][:30] == ['0'] * 30
    assert next(r for r in candidate['plan']['segments'] if r['id'] == 'outside') == outside
    assert candidate['plan']['switches'] == []


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
def test_native_quantity_repair_runs_outside_the_finite_table_and_labels_its_scope(shared_client, strategy):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1000}], capacity=2000)
    result = solve(client, strategy=strategy).json()
    assert result['candidates'][0]['objective'][:30] == ['0'] * 30
    stage = next(row for row in result['stage_bounds'] if row['stage'] == 'native_local')
    assert stage['status'] == 'restricted_model'
    assert 'Optimal' in stage['detail'] or 'OPTIMAL' in stage['detail']
    assert stage['lower_bound'] is None and stage['global_proof'] is False
    assert len(stage['native_layers']) == 33 and stage['route'] == strategy


@pytest.mark.parametrize('strategy', ['cp_sat', 'highs'])
def test_native_general_largest_remainder_quota_matches_independently_enumerated_input(shared_client, strategy):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 600},
                   {'model_code': 'M', 'week': '2026-10-19', 'quantity': 400}], capacity=2000,
          confirmations=[{'model_code': 'M', 'week': '2026-10-19', 'kind': 'material', 'batch_id': 'early',
                          'status': 'allowed', 'date': '2026-10-18', 'quantity': 428},
                         {'model_code': 'M', 'week': '2026-10-12', 'kind': 'shipping', 'batch_id': 'late',
                          'status': 'allowed', 'date': '2026-10-19', 'quantity': 1000}])
    data = inputs.read().model_dump(mode='json')
    data['parameters']['model_yields'][0]['percent'] = '93.10'
    data['parameters']['cycle_times'][0]['days'] = '0.50'
    inputs.versions[1] = InputSnapshot.model_validate(data)
    result = solve(client, strategy=strategy, scope={'start_date': '2026-10-18', 'end_date': '2026-10-18', 'line_codes': ['L']}).json()
    candidate = result['candidates'][0]
    # Independent q=0..2000 enumeration gives vector(101,2,0,2), attained
    # by q1070/1071. q1071 is removable input; q1072 needs material share429.
    assert sum(r['quantity'] for r in candidate['plan']['segments']) == 1070
    assert candidate['objective'][:2] == ['101', '2']
    assert candidate['objective'][15:17] == ['0', '2']
    assert any(row['stage'] == 'native_local' and 'native ' + strategy in row['detail'] for row in result['stage_bounds'])


def test_current_day_candidate_declares_exact_residual_proof_and_preserves_actual_association(shared_client):
    from tests.test_execution_reset import current_command, fact
    from tests.test_plan_transactions import post, row
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 800}], capacity=4800)
    recorded = post(client, 'save', current_command(client,
        {'kind': 'patch_plan', 'segments': [row(day='2026-10-14', quantity=600) | {'hours': '3'}]},
        {'kind': 'record_execution', 'rows': [fact(600)], 'cutoff': '2026-10-14T08:00:00+08:00'}))
    assert recorded.status_code == 200, recorded.text
    confirmation = {'kind': 'confirm_day_capacity', 'confirmations': [
        {'line_code': 'L', 'remaining_hours': '1', 'current_recipe': 'R'}], 'observed_at': '2026-10-14T08:00:00+08:00'}
    confirmed = post(client, 'save', current_command(client, confirmation))
    assert confirmed.status_code == 200, confirmed.text
    current = client.get('/auto_plan/api/plans/current').json()
    response = calculate(client, {
        'base_revision': current['revision'], 'input_version': current['input_version'], 'latest_adoption': 0,
        'request': {'scope': {'start_date': '2026-10-14', 'end_date': '2026-10-14', 'line_codes': ['L']}}})
    assert response.status_code == 200, response.text
    candidate = response.json()['candidates'][0]
    assert sum(r['quantity'] for r in candidate['plan']['segments']) == 800
    assert next(r for r in candidate['plan']['segments'] if r['id'] == 'a')['quantity'] == 600
    assert candidate['objective'][:30] == ['0'] * 30
    assert candidate['operation']['current_day_witnesses'] == candidate['plan']['current_day_witnesses']
    assert len(candidate['plan']['current_day_witnesses']) == 1
    checked = client.post('/auto_plan/api/plans/evaluate', json={'snapshot': candidate['snapshot'] | {'plan': candidate['plan']}})
    assert checked.status_code == 200 and checked.json()['executable'], checked.text
    saved = post(client, 'save', current_command(client, candidate['operation']) | {'pending': False})
    assert saved.status_code == 200, saved.text
    assert client.get('/auto_plan/api/plans/current').json()['execution_segments'][0]['executed'] == 600


def test_zero_input_share_cannot_bypass_a_material_ban(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': week, 'quantity': 1} for week in ('2026-10-12', '2026-10-19')], capacity=1,
          confirmations=[{'model_code': 'M', 'week': '2026-10-19', 'kind': 'material', 'batch_id': 'ban', 'status': 'forbidden'}])
    data = inputs.read().model_dump(mode='json')
    data['parameters']['cycle_times'][0]['days'] = '0.50'
    inputs.versions[1] = InputSnapshot.model_validate(data)
    result = solve(client, scope={'start_date': '2026-10-18', 'end_date': '2026-10-18', 'line_codes': ['L']}).json()
    # Q1 would round to1+1 across the week boundary, giving the second week
    # input share0. The ban still rejects it; the empty partial plan is valid.
    assert result['candidates'][0]['plan']['segments'] == []
    assert result['candidates'][0]['objective'][:2] == ['1', '1']


def test_output_rounds_across_lines_once_and_removable_extra_input_is_ineligible(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 2}], capacity=2)
    data = inputs.read().model_dump(mode='json')
    data['parameters']['model_yields'][0]['percent'] = '50'
    inputs.versions[1] = InputSnapshot.model_validate(data)
    candidate = solve(client, scope={'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L', 'B']}).json()['candidates'][0]
    assert sum(r['quantity'] for r in candidate['plan']['segments']) == 3
    assert candidate['evaluation']['output_totals']['expected'] == 2
    assert candidate['objective'][:30] == ['0'] * 30


def test_window_external_output_repairs_final_gap_without_scheduling_external_input(shared_client):
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': 'M', 'week': '2027-01-18', 'quantity': 2}], capacity=1,
          confirmations=[{'model_code': 'M', 'week': '2027-01-18', 'kind': 'shipping', 'batch_id': 'tail',
                          'status': 'allowed', 'date': '2027-01-26', 'quantity': 2}])
    data = inputs.read().model_dump(mode='json')
    data['parameters']['capacities'].append({'month': '2027-01', 'recipe_code': 'R', 'line_code': 'L', 'pieces': 1})
    data['parameters']['line_yields'].append({'month': '2027-01', 'model_code': 'M', 'percent': '100'})
    data['parameters']['model_yields'].append({'month': '2027-01', 'model_code': 'M', 'percent': '100'})
    data['parameters']['cycle_times'][0]['days'] = '1.50'
    inputs.versions[1] = InputSnapshot.model_validate(data)
    candidate = solve(client, scope={'start_date': '2027-01-24', 'end_date': '2027-01-24', 'line_codes': ['L']}).json()['candidates'][0]
    assert [(r['date'], r['quantity']) for r in candidate['plan']['segments']] == [('2027-01-24', 1)]
    assert candidate['objective'][14] == '2' and candidate['objective'][29] == '0'
    assert candidate['evaluation']['output_totals']['outside_window'] == 2



@pytest.mark.parametrize('strategy', ['cp_sat', 'highs'])
def test_future_native_quantity_repair_preserves_confirmed_today_proof(shared_client, strategy):
    from tests.test_execution_reset import current_command, fact
    from tests.test_plan_transactions import post, row
    client, _, inputs = shared_client
    setup(inputs, [{'model_code': model, 'week': '2026-10-12', 'quantity': 1500} for model in ('M', 'N')], capacity=2000)
    original = [row('actual', day='2026-10-14', quantity=200) | {'hours': '3'},
                row('m', quantity=500) | {'hours': '6'},
                row('n', 'N', quantity=1500) | {'order': 1, 'hours': '18'}]
    recorded = post(client, 'save', current_command(client, {'kind': 'patch_plan', 'segments': original},
        {'kind': 'record_execution', 'rows': [fact(200)], 'cutoff': '2026-10-14T08:00:00+08:00'}))
    assert recorded.status_code == 200, recorded.text
    confirmed = post(client, 'save', current_command(client, {'kind': 'confirm_day_capacity', 'confirmations': [
        {'line_code': 'L', 'remaining_hours': '3', 'current_recipe': 'R'}], 'observed_at': '2026-10-14T08:00:00+08:00'}))
    assert confirmed.status_code == 200, confirmed.text
    locked = post(client, 'save', current_command(client, {'kind': 'lock_cells', 'cells': [
        {'date': '2026-10-14', 'line_code': 'L', 'model_code': 'M', 'quantity': 200},
        {'date': '2026-10-14', 'line_code': 'L', 'model_code': 'N', 'quantity': 0}]}))
    assert locked.status_code == 200, locked.text
    adopted = post(client, 'adopt', current_command(client) | {'pending': False})
    assert adopted.status_code == 200, adopted.text
    changed = post(client, 'save', current_command(client, {'kind': 'patch_plan', 'segments': [
        row('m', quantity=1300) | {'hours': '78/5'}, row('n', 'N', quantity=700) | {'order': 1, 'hours': '42/5'}]}) | {'pending': False})
    assert changed.status_code == 200, changed.text
    source = client.get('/auto_plan/api/plans/current').json()
    response = calculate(client, {
        'base_revision': source['revision'], 'input_version': source['input_version'], 'latest_adoption': source['latest_adoption'],
        'request': {'strategy': strategy, 'scope': {'start_date': '2026-10-14', 'end_date': '2026-10-15', 'line_codes': ['L']}}})
    assert response.status_code == 200, response.text
    candidate = response.json()['candidates'][0]
    # Both future arrangements cover2200 pieces across the week, leaving800.
    # The adopted future quantities500/1500 uniquely give stability cost0.
    assert candidate['objective'][0] == candidate['objective'][15] == '800'
    assert candidate['objective'][31] == '0'
    assert candidate['plan']['current_day_witnesses'] == source['plan']['current_day_witnesses']
    assert next(r for r in candidate['plan']['segments'] if r['id'] == 'actual') == next(r for r in source['plan']['segments'] if r['id'] == 'actual')
    assert candidate['evaluation']['executable']
