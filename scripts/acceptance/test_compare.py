"""Acceptance comparison rejects a worse lexicographic result and scope escapes."""
import pytest
from scripts.acceptance.compare import compare_vector, assert_scope


def test_full_objective_uses_numeric_rationals_and_first_different_coordinate():
    assert compare_vector(['0'] * 30 + ['2/2', '100', '0'], ['0'] * 30 + ['1', '101', '0']) == -1
    assert compare_vector(['1'] + ['0'] * 32, ['0'] * 30 + ['999999', '0', '0']) == 1
    with pytest.raises(ValueError):
        compare_vector(['0'], ['0'])


def test_equivalent_hours_do_not_hide_outside_scope_changes():
    source = {'segments': [{'id': 'a', 'date': '2026-10-20', 'line_code': 'L00', 'model_code': 'M00', 'quantity': 100, 'hours': '1', 'order': 1}], 'switches': [], 'initial_states': []}
    same = {'segments': [source['segments'][0] | {'hours': '2/2'}], 'switches': [], 'initial_states': []}
    scope = {'start_date': '2026-10-21', 'end_date': '2026-10-21', 'line_codes': ['L00'], 'model_codes': ['M00']}
    assert_scope(source, same, scope)
    with pytest.raises(AssertionError, match='outside'):
        assert_scope(source, {'segments': [same['segments'][0] | {'quantity': 101}]}, scope)


def test_candidate_cannot_move_an_executed_segment_inside_an_otherwise_allowed_scope():
    from scripts.acceptance.compare import assert_protected
    source = {'plan': {'segments': [{'id': 'executed', 'date': '2026-10-20', 'line_code': 'L00', 'model_code': 'M00', 'quantity': 800, 'hours': '4/5', 'order': 0}]},
              'execution': {'facts': [{'date': '2026-10-20', 'line_code': 'L00', 'model_code': 'M00', 'quantity': 600}], 'associations': [{'segment_id': 'executed', 'quantity': 600}]}, 'locks': []}
    changed = {'segments': [source['plan']['segments'][0] | {'line_code': 'L01'}]}
    with pytest.raises(AssertionError, match='Executed'):
        assert_protected(source, changed)
