"""Explicit field-confirmation operations for unrelated adoption fixtures."""


def confirm_states(*line_recipes, effective_at='2026-10-12T08:00:00+08:00'):
    return {'kind':'confirm_line_states','rows':[
        {'line_code':line,'recipe_code':recipe,'effective_at':effective_at,'source':'冻结测试现场点检'}
        for line,recipe in (line_recipes or [('L','R')])]}


def assert_unconfirmed_trial(result, lines=('L',)):
    """A valid trial is still blocked from adoption by its explicit unknown origins."""
    assert result['capacity_valid'] is True
    assert result['computation_complete'] is True
    assert result['executable'] is False
    assert {row['code'] for row in result['adoption_conditions']} == {'initial_state_unknown'}
    assert {row['line_code'] for row in result['adoption_conditions']} == set(lines)
    assert all(row['severity'] == 'warning' for row in result['adoption_conditions'])
