"""Protected actual switch progress through the shared planning API."""
from tests.test_plan_transactions import shared_client, post, row, patch
from tests.test_execution_reset import head, preview, current_command


def unfinished(**changes):
    job = {'job_id': 'actual-r-s', 'started_at': '2026-10-11T08:00:00+08:00',
           'from_recipe': 'R', 'to_recipe': 'S', 'remaining_hours': '4', 'standard_hours': '12', 'completed_hours': '8'}
    job.update(changes)
    return {'kind': 'confirm_line_states', 'rows': [{'line_code': 'L', 'effective_at': '2026-10-12T08:00:00+08:00',
            'state': 'unfinished_switch', 'recipe_code': None, 'unfinished_switch': job, 'source': '现场交班'}]}


def residual(record_id, *, hours='4', day='2026-10-15', order=0, **changes):
    value = {'id': 'actual-r-s', 'line_code': 'L', 'from_recipe': 'R', 'to_recipe': 'S', 'residual_record_id': record_id,
             'allocations': [{'date': day, 'order': order, 'hours': hours}]}
    return value | changes


def test_unfinished_actual_job_records_observed_progress_without_a_ready_target(shared_client):
    client, _, _ = shared_client
    checked = preview(client, unfinished())
    assert checked.status_code == 200, checked.text
    record = checked.json()['context']['line_states']['records'][0]
    assert record['state'] == 'unfinished_switch'
    assert record['recipe_code'] is None
    assert record['unfinished_switch'] == unfinished()['rows'][0]['unfinished_switch']
    assert head(client)['context']['line_states']['records'] == []
    saved = post(client, 'save', current_command(client, unfinished()))
    assert saved.status_code == 200, saved.text
    current = head(client)
    assert current['context']['line_states']['records'][0]['unfinished_switch']['remaining_hours'] == '4'
    assert len(current['context']['line_states']['audit']) == 1
    assert current['context']['line_states']['records'][0]['actor'] == 'EDITOR'
