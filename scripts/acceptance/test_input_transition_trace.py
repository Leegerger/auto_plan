"""Public two-actor input sequence; coordination is a protocol control, not load."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.input_models import InputSnapshot
from auto_plan.settings import Settings
from tests.input_support import MemoryInputStore
from tests.plan_support import MemoryPlanStore
from tests.demand_support import MemoryDemandStore
from tests.support import CompanyAuthentication, MemoryAccessStore
from scripts.acceptance.input_transition_observer import InputTransitionObserver


def test_public_stale_preview_noop_and_genuine_advance_have_distinct_causal_trace(tmp_path):
    inputs, access = MemoryInputStore(), MemoryAccessStore()
    inputs.versions[1] = InputSnapshot.model_validate({'input_version': 1,
        'masters': {'lines': [{'code': code, 'name': code, 'active': True} for code in ('LA', 'LB')]}})
    inputs.version = 1
    for actor in ('ACTORA', 'ACTORB'):
        access.users[actor] = {'user_id': actor, 'roles': ['editor'], 'active': True}
    plans = MemoryPlanStore(inputs)
    app = create_app(Settings(public_origin='https://testserver'), store=access, input_store=inputs,
        plan_store=plans, demand_store=MemoryDemandStore(plans), authenticator=CompanyAuthentication(),
        clock=lambda: datetime(2026, 10, 12, 1, tzinfo=timezone.utc))
    observer = InputTransitionObserver(app, tmp_path / 'trace.ndjson')
    requests, sessions = [], {}
    with TestClient(observer, base_url='https://testserver') as client:
        client.headers.update({'Origin': 'https://testserver', 'X-Auto-Plan-Request': '1'})
        for actor in ('ACTORA', 'ACTORB'):
            client.cookies.clear()
            assert client.post('/auto_plan/api/auth/login', json={'username': actor, 'password': 'company-secret'}).status_code == 200
            sessions[actor] = dict(client.cookies)
        def send(actor, path, command=None):
            client.cookies.clear(); client.cookies.update(sessions[actor])
            reply = client.get('/auto_plan/api' + path) if command is None else client.post('/auto_plan/api' + path, json=command)
            identifier = reply.headers.get('x-auto-plan-test-input')
            trace = next((row for row in observer.records if row['id'] == identifier), None)
            checked = next((event['checked_input_version'] for event in (trace or {}).get('events', []) if event['name'] == 'input_transform_entered'), None)
            requests.append({'trace': trace, 'actor': actor, 'path': path, 'status': reply.status_code,
                'command': deepcopy(command), 'request_sha256': hashlib.sha256(reply.request.content).hexdigest(),
                'response': reply.json(), 'checked_input_version': checked})
            return reply
        def preview(actor, version, code, name):
            reply = send(actor, '/inputs/preview', {'base_version': version,
                'changes': {'lines': [{'code': code, 'name': name, 'active': True}]}})
            assert reply.status_code == 200 and reply.json()['valid']
            return {key: reply.json()[key] for key in ('base_version', 'changes')}
        first = send('ACTORA', '/inputs?version=1').json()
        before = send('ACTORA', '/plans/current').json()
        plan_history = send('ACTORA', '/plans/history').json()
        demand_history = send('ACTORA', '/demands/files').json()
        pending = preview('ACTORA', 1, 'LA', 'A changed')
        competing = preview('ACTORB', 1, 'LB', 'B changed')
        saved = send('ACTORB', '/inputs/apply', competing)
        assert saved.status_code == 200 and saved.json()['input_version'] == 2
        stale = send('ACTORA', '/inputs/apply', pending)
        stale_record = requests[-1]
        assert stale.status_code == 409
        assert stale_record['command'] == pending
        second = send('ACTORA', '/inputs?version=2').json()
        assert send('ACTORA', '/inputs?version=1').json() == first
        assert second == saved.json()
        assert send('ACTORA', '/inputs').json() == second
        no_op = preview('ACTORB', 2, 'LB', 'B changed')
        unchanged = send('ACTORB', '/inputs/apply', no_op)
        assert unchanged.status_code == 200 and unchanged.json() == second
        no_op_record = requests[-1]
        real = preview('ACTORA', 2, 'LA', 'A changed')
        advanced = send('ACTORA', '/inputs/apply', real)
        assert advanced.status_code == 200 and advanced.json()['input_version'] == 3
        advance_record = requests[-1]
        third = send('ACTORA', '/inputs?version=3').json()
        expected = deepcopy(second); expected['input_version'] = 3; expected['masters']['lines'][0]['name'] = 'A changed'
        assert third == expected
        assert send('ACTORA', '/inputs?version=1').json() == first
        assert send('ACTORA', '/inputs?version=2').json() == second
        assert send('ACTORA', '/plans/history').json() == plan_history
        final_demand_history = send('ACTORA', '/demands/files').json()
        assert final_demand_history == demand_history | {'input_version': 3}
        after = send('ACTORA', '/plans/current').json()
        for key in ('revision', 'latest_adoption', 'plan', 'context', 'coverage', 'lineage'):
            assert before[key] == after[key]
        proof = {'kind': 'coordinated public protocol control; not natural load', 'requests': requests,
            'v1': first, 'v2': second, 'v3': third, 'stale_same_command': pending}
        Path(os.environ.get('AUTO_PLAN_INPUT_TRACE_PROOF', tmp_path / 'proof.json')).write_text(json.dumps(proof, indent=2))
        assert stale_record['checked_input_version'] == 2, 'HTTP-only status409 leaves its actual atomic source pin unknown.'
        assert no_op_record['checked_input_version'] == 2
        assert advance_record['checked_input_version'] == 2
        for record, classification, version, credit in [(no_op_record, 'no_op', 2, 0), (advance_record, 'advance', 3, 1)]:
            events = record['trace']['events']
            transformed = next(row for row in events if row['name'] == 'input_update_returned')
            assert (transformed['classification'], transformed['candidate_version'], transformed['activity_credit']) == (classification, version, credit)
            assert any(row['name'] == 'input_lock_releasing' and row['operation'] == 'update' and row['stored_version'] == version for row in events)
        assert any(row['name'] == 'input_update_failed' and row['error_type'] == 'InputConflict' for row in stale_record['trace']['events'])
        assert any(row['name'] == 'input_lock_releasing' and row['operation'] == 'update' and row['stored_version'] == 2 for row in stale_record['trace']['events'])
        for record in requests:
            trace = record['trace']
            if trace is None: continue
            assert trace['status'] == record['status'] and trace['response_complete']
            assert trace['request_sha256'] == record['request_sha256']
            if record['command'] is not None: assert trace['request_complete']
            assert [row['sequence'] for row in trace['events']] == sorted(row['sequence'] for row in trace['events'])
        for record in requests:
            if record['path'] != '/inputs/apply': continue
            assert next(row['actor'] for row in record['trace']['events'] if row['name'] == 'input_update_entered') == record['actor']
            names = [row['name'] for row in record['trace']['events']]
            assert names.index('input_lock_acquired') < names.index('input_transform_entered') < names.index('response_started')
        preview_record = next(row for row in requests if row['actor'] == 'ACTORA' and row['path'] == '/inputs/preview')
        assert any(row['name'] == 'input_read_returned' and row['input_version'] == 1 for row in preview_record['trace']['events'])
        assert len({row['id'] for row in observer.records}) == len(observer.records)
        text = (tmp_path / 'trace.ndjson').read_text()
        assert 'company-secret' not in text and 'cookie' not in text.lower()
