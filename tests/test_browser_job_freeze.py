"""Accepted job sources through the public HTTP interface under concurrent input writes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Event
from uuid import uuid4

from tests.input_support import MemoryInputStore
from tests.test_plan_transactions import shared_client
from tests.test_solver import setup
from tests.test_jobs import install_controller, wait_job
from scripts.acceptance.adapter_server import BrowserPlanStore


class DelayedInputDelivery(MemoryInputStore):
    """Deliver one immutable read slowly while a real public writer is active."""
    def __init__(self):
        super().__init__()
        self.delay_next = False
        self.read_captured = Event()
        self.writer_finished = Event()

    def read(self, version=None):
        snapshot = super().read(version)
        if version is None and self.delay_next:
            self.delay_next = False
            self.read_captured.set()
            # If admission holds the input guard, delivery resumes at the
            # bounded deadline and the real writer completes after the freeze.
            self.writer_finished.wait(1)
        return snapshot


def browser_adapter(client, original):
    inputs = DelayedInputDelivery()
    inputs.versions = {key: value.model_copy(deep=True) for key, value in original.versions.items()}
    inputs.version = original.version
    setup(inputs, [{'model_code': 'M', 'week': '2026-10-12', 'quantity': 1}])
    plans = BrowserPlanStore(inputs)
    client.app.state.input_store = inputs
    client.app.state.plan_store = plans
    return inputs, plans


def submit(source):
    return {'submission_id': str(uuid4()), 'base_revision': source['source_revision'],
        'input_version': source['input_version'], 'latest_adoption': source['latest_adoption'],
        'reviewed_as_of': source['snapshot']['time']['as_of'],
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L']},
                    # Source-freezing checks include fresh native process startup.
                    # Deadline behavior has separate tests.
                    'time_limit_seconds': 10}}


def candidate(client, identity):
    finished = wait_job(client, identity)
    assert finished['state'] == 'completed', finished
    reference = finished['candidates'][0]
    response = client.get(f"/auto_plan/api/jobs/{identity}/candidates/{reference['candidate_id']}")
    assert response.status_code == 200, response.text
    return response.json()['candidate']


def test_job_freezes_the_reviewed_version_while_a_concurrent_public_writer_waits_for_admission(shared_client):
    client, _, original = shared_client
    inputs, plans = browser_adapter(client, original)
    controller = install_controller(client, plans)
    try:
        source_response = client.get('/auto_plan/api/jobs/source')
        assert source_response.status_code == 200, source_response.text
        source = source_response.json()
        assert source['input_version'] == 1
        models = deepcopy(source['snapshot']['inputs']['masters']['models'])
        next(row for row in models if row['code'] == 'M')['name'] = 'Concurrent maintained name'

        def edit():
            assert inputs.read_captured.wait(3), 'The submitted source was never read.'
            try:
                return client.post('/auto_plan/api/inputs/apply', json={'base_version': 1, 'changes': {'models': models}})
            finally:
                inputs.writer_finished.set()

        inputs.delay_next = True
        with ThreadPoolExecutor(max_workers=1) as executor:
            writing = executor.submit(edit)
            command = submit(source)
            accepted = client.post('/auto_plan/api/jobs', json=command)
            written = writing.result(timeout=5)
        assert accepted.status_code == 200, accepted.text
        assert written.status_code == 200, written.text
        assert written.json()['input_version'] == 2
        identity = accepted.json()['job_id']
        job = client.get('/auto_plan/api/jobs/' + identity).json()
        assert job['input_version'] == 1, 'Accepted metadata changed to a version that was never submitted.'
        result = candidate(client, identity)
        assert result['input_version'] == 1
        assert result['snapshot'] == source['snapshot']
        assert result['objective'] == ['0'] * 33
        assert client.get('/auto_plan/api/inputs', params={'version': 1}).json() == source['snapshot']['inputs']
        assert client.get('/auto_plan/api/inputs', params={'version': 2}).json() == written.json()
        assert client.post('/auto_plan/api/jobs', json=command).json() == accepted.json()
    finally:
        controller.shutdown()


def test_elapsed_review_time_keeps_literal_server_acceptance_time_and_original_source_body(shared_client):
    client, _, original = shared_client
    inputs, plans = browser_adapter(client, original)
    now = [datetime.fromisoformat('2026-10-14T08:01:00+08:00')]
    client.app.state.clock = lambda: now[0]
    client.cookies.clear()
    assert client.post('/auto_plan/api/auth/login', json={'username': 'EDITOR', 'password': 'company-secret'}).status_code == 200
    controller = install_controller(client, plans)
    try:
        read = client.get('/auto_plan/api/jobs/source')
        assert read.status_code == 200, read.text
        source_bytes = read.content
        source = read.json()
        assert source['snapshot']['time'] == {'as_of': '2026-10-14T08:01:00+08:00', 'window_start': '2026-10-12', 'weeks': 15}
        now[0] += timedelta(seconds=5)
        command = submit(source)
        accepted = client.post('/auto_plan/api/jobs', json=command)
        assert accepted.status_code == 200, accepted.text
        identity = accepted.json()['job_id']
        result = candidate(client, identity)
        job = client.get('/auto_plan/api/jobs/' + identity).json()
        assert job['created_at'] == '2026-10-14T08:01:05+08:00'
        assert result['snapshot']['time'] == {'as_of': '2026-10-14T08:01:05+08:00', 'window_start': '2026-10-12', 'weeks': 15}
        for key in ('source_revision', 'input_version', 'latest_adoption'):
            assert result[key] == source[key]
        for key in ('inputs', 'plan', 'execution', 'reset', 'locks', 'baseline', 'preferences'):
            assert result['snapshot'][key] == source['snapshot'][key]
        assert read.content == source_bytes
        assert source['snapshot']['time']['as_of'] == '2026-10-14T08:01:00+08:00'
        now[0] += timedelta(seconds=5)
        assert client.post('/auto_plan/api/jobs', json=command).json() == accepted.json()
        assert client.get('/auto_plan/api/jobs/' + identity).json()['created_at'] == '2026-10-14T08:01:05+08:00'
    finally:
        controller.shutdown()
