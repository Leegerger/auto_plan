"""Continuity evidence through the shared public planning boundary."""
from tests.test_plan_transactions import shared_client, post, row, patch
from tests.test_execution_reset import head, preview, current_command
from tests.test_unfinished_switches import unfinished, residual


def arrange(client, *, day='2026-10-15'):
    assert post(client, 'save', current_command(client, unfinished())).status_code == 200
    record = head(client)['context']['line_states']['records'][0]
    plan = patch(row('target', model='P', day=day) | {'order': 1})
    plan['switches'] = [residual(record['id'], day=day)]
    assert post(client, 'save', current_command(client, plan)).status_code == 200
    return head(client)


def confirmation(dto, **changes):
    return {'kind': 'confirm_switch_continuity', 'job_id': dto['job_id'], 'line_code': dto['line_code'],
            'source_record_id': dto['source_record_id'], 'expected_basis_digest': dto['basis_digest'], 'source': '现场核实可连续执行'} | changes


def test_matching_continuity_previews_without_writes_and_adopts_atomically(shared_client):
    client, _, _ = shared_client
    before = arrange(client)
    dto = before['workspace_view']['switch_continuity'][0]
    assert dto['status'] == 'missing'
    assert dto['can_confirm'] is True
    assert before['evaluation']['capacity_valid'] is before['evaluation']['computation_complete'] is True
    assert post(client, 'adopt', current_command(client)).status_code == 422
    checked = preview(client, confirmation(dto))
    assert checked.status_code == 200, checked.text
    assert checked.json()['evaluation']['executable'] is True
    assert head(client) == before
    command = current_command(client, confirmation(dto))
    adopted = post(client, 'adopt', command)
    assert adopted.status_code == 200, adopted.text
    assert post(client, 'adopt', command).json() == adopted.json()
    current = head(client)
    assert current['input_version'] == before['input_version'] + 1
    assert current['workspace_view']['switch_continuity'][0]['status'] == 'valid'
    record = current['context']['switch_continuity']['records'][0]
    assert (record['actor'], record['source'], record['basis_digest']) == ('EDITOR', '现场核实可连续执行', dto['basis_digest'])
    assert len(current['context']['switch_continuity']['audit']) == 1
    assert preview(client).json()['evaluation']['executable'] is True
