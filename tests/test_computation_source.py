"""Complete reviewed computation sources through the public HTTP interface."""
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4
import pytest
from tests.test_plan_transactions import shared_client
from tests.jobs_support import MemoryJobStore


def legacy_command(identity):
    # Literal pre-reviewed_as_of canonical shape, including old request defaults.
    return {'submission_id': identity, 'base_revision': 0, 'input_version': 1, 'latest_adoption': 0,
            'request': {'scope': {'start_date': '2026-10-20', 'end_date': '2026-10-20',
                                  'line_codes': ['L'], 'model_codes': []},
                        'mode': 'local', 'strategy': 'hybrid', 'time_limit_seconds': 1,
                        'max_candidates': 1, 'native_threads': 1}}


@pytest.mark.parametrize('before_text,after_text,before_window', [
    ('2026-10-19T07:59:59+08:00', '2026-10-19T08:00:00+08:00', '2026-10-12'),
    ('2026-10-20T07:59:59+08:00', '2026-10-20T08:00:00+08:00', '2026-10-19'),
], ids=['new-production-week', 'new-production-day'])
def test_reviewed_source_crossing_business_time_is_rejected_without_changing_legacy_uuid_results(
        shared_client, before_text, after_text, before_window):
    client, plans, _ = shared_client
    now = [datetime.fromisoformat(before_text)]
    client.app.state.clock = lambda: now[0]
    jobs = MemoryJobStore(plans, client.app.state.access_store)
    client.app.state.job_store = jobs

    def login():
        client.cookies.clear()
        answer = client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'})
        assert answer.status_code == 200

    # Historical accepted data predates the optional review field. The public
    # retry must match these literal old bytes, rather than a new None field.
    original_id, old_job = str(uuid4()), str(uuid4())
    old_command = legacy_command(original_id)
    old_result = {'status': 'ACCEPTED', 'submission_id': original_id, 'job_id': old_job}
    jobs.keys[original_id] = 'EDITOR', deepcopy(old_command), deepcopy(old_result)
    jobs.jobs[old_job] = {'job_id': old_job, 'actor': 'EDITOR', 'state': 'stopped', 'fifo': 0,
        'source_revision': 0, 'input_version': 1, 'latest_adoption': 0,
        'created_at': before_text, 'candidates': []}
    login()
    assert client.post('/auto_plan/api/jobs', json=old_command).json() == old_result

    previous_head = client.get('/auto_plan/api/plans/current').json()
    response = client.get('/auto_plan/api/jobs/source')
    assert response.status_code == 200, response.text
    source = response.json()
    assert {key: source[key] for key in ('source_revision', 'input_version', 'latest_adoption')} == {
        'source_revision': 0, 'input_version': 1, 'latest_adoption': 0}
    snapshot = source['snapshot']
    assert set(snapshot) == {'time', 'inputs', 'plan', 'execution', 'reset', 'locks', 'baseline', 'preferences', 'line_states', 'state_reviews'}
    assert snapshot['time'] == {'as_of': before_text, 'window_start': before_window, 'weeks': 15}
    assert snapshot['inputs'] == client.get('/auto_plan/api/inputs', params={'version': 1}).json()
    assert snapshot['plan'] == previous_head['plan']
    for key in ('execution', 'reset', 'locks', 'preferences', 'line_states'):
        assert snapshot[key] == previous_head['context'][key]
    assert snapshot['baseline'] is None
    assert snapshot['state_reviews'] == []
    assert 'evaluation' not in source
    assert client.get('/auto_plan/api/plans/current').json() == previous_head

    now[0] = datetime.fromisoformat(after_text)
    stale = legacy_command(str(uuid4())) | {'reviewed_as_of': snapshot['time']['as_of']}
    rejected = client.post('/auto_plan/api/jobs', json=stale)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()['review_required'] is True
    assert client.post('/auto_plan/api/jobs/verify', json={'submission_id': stale['submission_id']}).json()['status'] == 'ABORTED_VOID'
    # The read snapshot stays immutable although R/V/A have not changed.
    assert snapshot['time']['as_of'] == before_text
    refreshed = client.get('/auto_plan/api/jobs/source').json()
    assert {key: refreshed[key] for key in ('source_revision', 'input_version', 'latest_adoption')} == {
        'source_revision': 0, 'input_version': 1, 'latest_adoption': 0}
    assert refreshed['snapshot']['time'] == {'as_of': after_text, 'window_start': '2026-10-19', 'weeks': 15}
    fresh = legacy_command(str(uuid4())) | {'reviewed_as_of': after_text}
    accepted = client.post('/auto_plan/api/jobs', json=fresh)
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()
    assert client.get('/auto_plan/api/jobs/' + result['job_id']).json()['created_at'] == after_text
    assert client.post('/auto_plan/api/jobs/' + result['job_id'] + '/stop', json={}).status_code == 200

    # A date transition must not override an already accepted original UUID.
    now[0] += timedelta(days=1)
    login()
    assert client.post('/auto_plan/api/jobs', json=fresh).json() == result
    assert client.get('/auto_plan/api/jobs/' + result['job_id']).json()['created_at'] == after_text
    assert client.post('/auto_plan/api/jobs', json=old_command).json() == old_result
    assert client.post('/auto_plan/api/jobs', json=old_command | {'reviewed_as_of': None}).json() == old_result
    # A non-None review instant is part of new UUID command identity.
    assert client.post('/auto_plan/api/jobs', json=fresh | {'reviewed_as_of': before_text}).status_code == 409


def test_computation_source_keeps_complete_adopted_and_versioned_business_context(shared_client):
    from tests.test_execution_reset import seed_reset_source, reset_operation, current_command, fact, head
    from tests.test_plan_transactions import post
    client, plans, _ = shared_client
    client.app.state.job_store = MemoryJobStore(plans, client.app.state.access_store)
    seed_reset_source(client, plans)
    assert post(client, 'save', current_command(client, reset_operation())).status_code == 200
    changes = [
        {'kind': 'record_execution', 'rows': [fact(0, 'P')], 'cutoff': '2026-10-14T08:00:00+08:00'},
        {'kind': 'lock_cells', 'cells': [fact(0, 'N', '2026-10-15', 'B')]},
        {'kind': 'set_preferences', 'preferences': {'near_week_weight': '7'}},
    ]
    saved = post(client, 'save', current_command(client, *changes))
    assert saved.status_code == 200, saved.text
    previous = head(client)
    adopted = client.get('/auto_plan/api/plans/adoptions/1').json()
    response = client.get('/auto_plan/api/jobs/source')
    assert response.status_code == 200, response.text
    source = response.json()
    snapshot = source['snapshot']
    assert source['source_revision'] == previous['revision']
    assert source['input_version'] == previous['input_version']
    assert source['latest_adoption'] == 1
    assert snapshot['inputs'] == client.get('/auto_plan/api/inputs', params={'version': source['input_version']}).json()
    assert snapshot['plan'] == previous['plan']
    for key in ('execution', 'reset', 'locks', 'preferences', 'line_states'):
        assert snapshot[key] == previous['context'][key]
    assert fact(0, 'P') in snapshot['execution']['facts']
    assert snapshot['reset'] is not None
    assert snapshot['locks'] == [fact(0, 'N', '2026-10-15', 'B')]
    assert snapshot['preferences']['near_week_weight'] == '7'
    assert snapshot['baseline'] == {'version': 1, 'plan': adopted['snapshot']['plan'], 'coverage': adopted['coverage']}
    assert head(client) == previous

    changed = post(client, 'save', current_command(client,
        {'kind': 'set_preferences', 'preferences': {'near_week_weight': '8'}}))
    assert changed.status_code == 200, changed.text
    stale = legacy_command(str(uuid4())) | {
        'base_revision': source['source_revision'], 'input_version': source['input_version'],
        'latest_adoption': source['latest_adoption'], 'reviewed_as_of': snapshot['time']['as_of']}
    assert client.post('/auto_plan/api/jobs', json=stale).status_code == 409
    assert client.post('/auto_plan/api/jobs/verify', json={'submission_id': stale['submission_id']}).json()['status'] == 'ABORTED_VOID'
    refreshed = client.get('/auto_plan/api/jobs/source').json()
    assert refreshed['input_version'] > source['input_version']
    assert refreshed['snapshot']['preferences']['near_week_weight'] == '8'
    assert snapshot['preferences']['near_week_weight'] == '7'
