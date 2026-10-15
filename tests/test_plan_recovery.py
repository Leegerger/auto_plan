"""Recovery comparison is a read-only public business boundary."""
from tests.test_plan_transactions import shared_client, post, command, patch, row


def test_recovery_comparison_shows_current_shared_and_input_changes_without_rebasing(shared_client):
    client, _, inputs = shared_client
    assert post(client, 'save', command(patch(row()))).status_code == 200
    assert post(client, 'save', command(patch(row(quantity=125)), base=1)).status_code == 200
    from datetime import datetime, timezone
    inputs.update('EDITOR', datetime.now(timezone.utc), lambda current: current.model_copy(update={'input_version': 2}))
    result = client.get('/auto_plan/api/plans/compare', params={'base_revision': 1, 'input_version': 1})
    assert result.status_code == 200, result.text
    compared = result.json()
    assert compared['base_revision'] == 1
    assert compared['revision'] == 2
    assert compared['input_version'] == 2
    assert [(change['field'], change['before'], change['after']) for change in compared['plan_changes']] == [('投入数量', 100, 125)]
    assert compared['input_changes'] == [{'path': ['input_version'], 'before': 1, 'after': 2}]
    assert client.get('/auto_plan/api/plans/current').json()['revision'] == 2
    assert client.get('/auto_plan/api/plans/compare', params={'base_revision': -1, 'input_version': 1}).status_code == 422
