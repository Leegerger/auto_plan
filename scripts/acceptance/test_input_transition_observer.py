"""Passive receive and original adapter-call controls; no browser or timing claim."""
import asyncio
import hashlib
from types import SimpleNamespace
import pytest
from tests.input_support import MemoryInputStore
from scripts.acceptance.input_transition_observer import InputTransitionObserver


class CountingStore(MemoryInputStore):
    def __init__(self):
        super().__init__()
        self.calls = {'read': 0, 'update': 0, 'transform': 0}
    def read(self, version=None):
        self.calls['read'] += 1
        return super().read(version)
    def update(self, actor, now, transform):
        self.calls['update'] += 1
        def counted(current):
            self.calls['transform'] += 1
            return transform(current)
        return super().update(actor, now, counted)


def test_partial_rejected_request_preserves_original_messages_and_exact_store_call_counts():
    first = {'type': 'http.request', 'body': b'abc', 'more_body': True}
    tail = {'type': 'http.request', 'body': b'untouched', 'more_body': False}
    queued, sent = [first, tail], []
    store = CountingStore()
    class App:
        state = SimpleNamespace(input_store=store)
        async def __call__(self, scope, receive, send):
            assert await receive() is first
            store.read()
            store.update('ACTOR', None, lambda current: current)
            await send({'type': 'http.response.start', 'status': 422, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'rejected'})
    async def receive(): return queued.pop(0)
    async def send(message): sent.append(message)
    trace = InputTransitionObserver(App())
    asyncio.run(trace({'type': 'http', 'method': 'POST', 'path': '/auto_plan/api/inputs/apply',
        'headers': [(b'cookie', b'NEVER_RECORD_COOKIE')], 'query_string': b''}, receive, send))
    assert queued == [tail] and store.calls == {'read': 2, 'update': 1, 'transform': 1}
    record = trace.records[0]
    assert record['request_complete'] is False and record['response_complete'] is True
    assert record['request_length'] == 3 and record['request_sha256'] == hashlib.sha256(b'abc').hexdigest()
    assert record['status'] == 422
    assert dict(sent[0]['headers'])[b'x-auto-plan-test-input'].decode() == record['id']
    assert sent[1] == {'type': 'http.response.body', 'body': b'rejected'}
    assert sum(row['name'] == 'input_lock_acquired' for row in record['events']) == 3
    assert sum(row['name'] == 'input_lock_releasing' for row in record['events']) == 3
    assert 'NEVER_RECORD_COOKIE' not in str(record)
    before = len(record['events']); store.read(); assert len(record['events']) == before


def test_disconnect_and_failed_handler_keep_completion_and_status_unknown():
    disconnected = {'type': 'http.disconnect'}
    class App:
        state = SimpleNamespace(input_store=MemoryInputStore())
        async def __call__(self, scope, receive, send):
            assert await receive() is disconnected
            raise RuntimeError('DO_NOT_LOG_EXCEPTION_INPUT')
    async def receive(): return disconnected
    async def send(message): raise AssertionError('No response is sent.')
    trace = InputTransitionObserver(App())
    with pytest.raises(RuntimeError):
        asyncio.run(trace({'type': 'http', 'method': 'POST', 'path': '/auto_plan/api/inputs/apply',
            'query_string': b''}, receive, send))
    record = trace.records[0]
    assert record['status'] is None and not record['response_complete'] and not record['request_complete']
    assert [row['name'] for row in record['events']] == ['request_arrived', 'request_disconnected']
    assert 'DO_NOT_LOG_EXCEPTION_INPUT' not in str(record)
