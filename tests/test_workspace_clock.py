"""Workspace refresh across a production-day boundary at public HTTP seams."""
from datetime import datetime
from uuid import uuid4

from tests.test_plan_transactions import shared_client


def test_daily_current_head_changes_without_mutating_versions_or_frozen_history(shared_client):
    client, _, _ = shared_client
    now = [datetime.fromisoformat('2026-10-14T07:59:00+08:00')]
    client.app.state.clock = lambda: now[0]
    client.cookies.clear()
    assert client.post('/auto_plan/api/auth/login', json={
        'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    saved = client.post('/auto_plan/api/plans/save', json={
        'base_revision': 0, 'input_version': 1, 'latest_adoption': 0,
        'operation_id': str(uuid4()), 'pending': True, 'operations': [
            {'kind': 'replace_plan', 'plan': {
                'initial_states': [{'line_code': 'L', 'recipe_code': 'R'}],
                'segments': [{'id': 'future-s', 'date': '2026-10-14',
                    'line_code': 'L', 'order': 0, 'model_code': 'P',
                    'quantity': 100, 'hours': '3'}]}},
            {'kind': 'confirm_day_capacity', 'observed_at': now[0].isoformat(),
                'confirmations': [{'line_code': 'L', 'current_recipe': 'S',
                    'remaining_hours': '1/60'}]}]})
    assert saved.status_code == 200, saved.text
    before = client.get('/auto_plan/api/plans/current').json()
    history = client.get('/auto_plan/api/plans/revisions/1').json()
    assert before['time']['production_date'] == '2026-10-13'
    assert 'production_date' not in history['snapshot']['time']
    assert before['evaluation']['capacity_valid'] is True
    week = lambda head: next(row for row in head['workspace_view']['line_weeks']
        if row['line_code'] == 'L' and row['week'] == '2026-10-12')
    assert week(before)['current_confirmation']['valid'] is True

    now[0] = datetime.fromisoformat('2026-10-14T08:01:00+08:00')
    after = client.get('/auto_plan/api/plans/current').json()

    assert after['time']['production_date'] == '2026-10-14'
    assert after['time']['window_start'] == before['time']['window_start'] == '2026-10-12'
    for key in ('revision', 'input_version', 'latest_adoption', 'plan', 'context'):
        assert after[key] == before[key]
    assert after['evaluation']['capacity_valid'] is False
    assert any(row['code'] == 'recipe_sequence' for row in after['evaluation']['issues'])
    assert week(after)['current_confirmation'] is None
    assert client.get('/auto_plan/api/plans/revisions/1').json() == history
    checked = client.post('/auto_plan/api/plans/preview', json={
        'base_revision': after['revision'], 'input_version': after['input_version'],
        'latest_adoption': after['latest_adoption'], 'operations': []})
    assert checked.status_code == 200, checked.text
    assert 'production_date' not in checked.json()['snapshot']['time']
