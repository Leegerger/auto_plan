"""Exact acceptance comparison. Independent references predate measured candidates."""
from collections import defaultdict
from fractions import Fraction


def compare_vector(observed, reference):
    if len(observed) != 33 or len(reference) != 33:
        raise ValueError('A complete 33-coordinate objective is required.')
    left, right = tuple(map(Fraction, observed)), tuple(map(Fraction, reference))
    return (left > right) - (left < right)


def objective(evaluation):
    weekly = defaultdict(lambda: [0, 0])
    for row in evaluation['weekly_results']:
        weekly[row['week']][0] += row['demand'] - row['on_time']
        weekly[row['week']][1] += row['gap']
    if len(weekly) != 15:
        raise ValueError('All fifteen dated demand weeks are required.')
    weeks = [weekly[key] for key in sorted(weekly)]
    stability = evaluation['stability']
    return [str(row[0]) for row in weeks] + [str(row[1]) for row in weeks] + [
        str(evaluation['switch_count']), str(stability['weighted_cost']), str(stability['order_change_count'])]


def numeric(value):
    if isinstance(value, list):
        return [numeric(item) for item in value]
    if isinstance(value, dict):
        return {key: Fraction(item) if key in ('hours', 'idle_hours') else numeric(item) for key, item in value.items()}
    return value


def assert_scope(source, candidate, scope):
    def allowed(row):
        return scope['start_date'] <= row['date'] <= scope['end_date'] and row['line_code'] in scope['line_codes'] and (
            not scope.get('model_codes') or row.get('model_code') in scope['model_codes'])
    def frozen_segments(plan):
        return {row['id']: numeric(row) for row in plan.get('segments', []) if not allowed(row)}
    assert frozen_segments(source) == frozen_segments(candidate), 'A production segment changed outside the allowed scope.'
    old = {row['id']: row for row in source.get('switches', [])}
    new = {row['id']: row for row in candidate.get('switches', [])}
    for ident in old.keys() | new.keys():
        rows = [row for row in (old.get(ident), new.get(ident)) if row]
        if any(row['line_code'] not in scope['line_codes'] or any(not scope['start_date'] <= part['date'] <= scope['end_date'] for part in row['allocations']) for row in rows):
            assert numeric(old.get(ident)) == numeric(new.get(ident)), 'A switch changed outside the allowed scope.'
    assert sorted(source.get('initial_states', []), key=lambda row: row['line_code']) == sorted(candidate.get('initial_states', []), key=lambda row: row['line_code']), 'Initial Recipe state changed.'
    def frozen_idle(plan):
        return {(row['date'], row['line_code']): numeric(row) for row in plan.get('ordinary_idle', []) if not (
            scope['start_date'] <= row['date'] <= scope['end_date'] and row['line_code'] in scope['line_codes'])}
    assert frozen_idle(source) == frozen_idle(candidate), 'Ordinary idle changed outside the allowed scope.'
    assert numeric(source.get('current_day_witnesses', [])) == numeric(candidate.get('current_day_witnesses', [])), 'Protected current-day witness changed.'


def assert_reference(evaluation, scenario):
    expected = scenario['expected']
    for field in ('capacity_valid', 'computation_complete', 'executable'):
        assert evaluation[field] == expected[field], field
    assert {key: evaluation['output_totals'][key] for key in expected['totals']} == expected['totals'], 'Independent output totals differ.'
    actual_vector = objective(evaluation)
    assert compare_vector(actual_vector, expected['objective']) == 0, (actual_vector, expected['objective'])
    return actual_vector


def assert_protected(source, candidate):
    old = {row['id']: row for row in source['plan']['segments']}
    new = {row['id']: row for row in candidate['segments']}
    associations = {row['segment_id']: row['quantity'] for row in source['execution'].get('associations', [])}
    actual = {(row['date'], row['line_code'], row['model_code']): row['quantity'] for row in source['execution']['facts']}
    frozen = {(row['date'], row['line_code'], row['model_code']) for row in source['execution'].get('association_cells', [])}
    assigned = defaultdict(int)
    for ident, quantity in associations.items():
        if ident in old:
            row = old[ident]
            assigned[row['date'], row['line_code'], row['model_code']] += quantity
    for row in sorted(old.values(), key=lambda item: (item['date'], item['line_code'], item['order'], item['id'])):
        key = row['date'], row['line_code'], row['model_code']
        if row['id'] not in associations:
            quantity = 0 if key in frozen else min(row['quantity'], max(0, actual.get(key, 0) - assigned[key]))
            associations[row['id']] = quantity
            assigned[key] += quantity
    executed_by_cell = defaultdict(list)
    for ident, quantity in associations.items():
        if not quantity or ident not in old:
            continue
        before, after = old[ident], new.get(ident)
        assert after is not None, 'Executed segment was removed.'
        assert all(before[key] == after[key] for key in ('date', 'line_code', 'model_code')), 'Executed segment moved.'
        assert after['quantity'] >= quantity, 'Executed quantity was reduced.'
        executed_by_cell[before['date'], before['line_code']].append(ident)
    for identities in executed_by_cell.values():
        assert sorted(identities, key=lambda ident: (old[ident]['order'], ident)) == sorted(identities, key=lambda ident: (new[ident]['order'], ident)), 'Executed segment order changed.'
    quantities = defaultdict(int)
    for row in new.values():
        quantities[row['date'], row['line_code'], row['model_code']] += row['quantity']
    for lock in source.get('locks', []):
        assert quantities[lock['date'], lock['line_code'], lock['model_code']] == lock['quantity'], 'A positive or zero lock changed.'


def assert_candidate(evaluation, scenario, source, candidate):
    assert evaluation['capacity_valid'] and evaluation['computation_complete'] and evaluation['executable'], 'Candidate is not executable.'
    assert_scope(source['plan'], candidate, scenario['scope'])
    assert_protected(source, candidate)
    actual_vector = objective(evaluation)
    assert compare_vector(actual_vector, scenario['expected']['objective']) <= 0, 'Candidate is worse than the frozen reference.'
    # Equal objective does not permit surplus input retained after a reduction.
    assert evaluation['output_totals']['unallocated'] == 0, 'Candidate retains unnecessary output.'
    return actual_vector
