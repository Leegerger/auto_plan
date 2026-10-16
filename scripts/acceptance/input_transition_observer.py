"""Opt-in TEST-ONLY input protocol trace. Never a deployed persistence adapter."""
from contextvars import ContextVar
import hashlib
import itertools
import json
import os
from time import perf_counter_ns
from urllib.parse import parse_qs
from uuid import uuid4

_request = ContextVar('acceptance_input_request', default=None)
_operation = ContextVar('acceptance_input_operation', default=None)


class InputTransitionObserver:
    def __init__(self, app, output=None, *, base_path='/auto_plan'):
        from tests.input_support import MemoryInputStore
        store = app.state.input_store
        if not isinstance(store, MemoryInputStore):
            raise TypeError('Input tracing supports only the explicit memory test adapter.')
        self.app, self.output, self.base_path = app, output, base_path
        self.records, self.sequence = [], itertools.count()
        original_read, original_update, original_lock = store.read, store.update, store.lock
        observer = self

        class OriginalLock:
            def __enter__(self):
                value = original_lock.__enter__()
                observer.event('input_lock_acquired', operation=_operation.get())
                return value

            def __exit__(self, kind, error, traceback):
                # Attribute access under the original held lock observes the
                # actual stored pin; it is not another read or commit call.
                observer.event('input_lock_releasing', operation=_operation.get(), stored_version=store.version)
                return original_lock.__exit__(kind, error, traceback)

        def read(version=None):
            token = _operation.set('read')
            try:
                value = original_read(version)
                self.event('input_read_returned', requested_version=version, input_version=value.input_version)
                return value
            finally:
                _operation.reset(token)

        def update(actor, now, transform):
            token = _operation.set('update')
            transition = {}
            self.event('input_update_entered', actor=actor)
            def checked(current):
                self.event('input_transform_entered', checked_input_version=current.input_version)
                candidate = transform(current)  # The original callback runs once.
                same = candidate == current
                classification = 'no_op' if same and candidate.input_version == current.input_version else (
                    'advance' if not same and candidate.input_version == current.input_version + 1 else 'unexpected')
                transition.update(checked_input_version=current.input_version, candidate_version=candidate.input_version, classification=classification)
                self.event('input_transform_returned', **transition)
                return candidate
            try:
                result = original_update(actor, now, checked)
                self.event('input_update_returned', input_version=result.input_version, **transition,
                    activity_credit=int(transition.get('classification') == 'advance'))
                return result
            except Exception as error:
                self.event('input_update_failed', error_type=type(error).__name__, activity_credit=0)
                raise
            finally:
                _operation.reset(token)

        store.lock, store.read, store.update = OriginalLock(), read, update

    def event(self, name, **values):
        record = _request.get()
        if record is not None:
            record['events'].append({'sequence': next(self.sequence), 'at_ns': perf_counter_ns(), 'name': name, **values})

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or path not in {self.base_path + '/api/inputs',
            self.base_path + '/api/inputs/preview', self.base_path + '/api/inputs/apply'}:
            return await self.app(scope, receive, send)
        record = {'id': uuid4().hex, 'instrumentation': 'test-only passive input transition',
            'clock_domain': 'python.perf_counter_ns:pid=' + str(os.getpid()),
            'method': scope['method'], 'path': path,
            'version_query': parse_qs(scope.get('query_string', b'').decode('ascii', errors='replace')).get('version'),
            'events': [], 'request_complete': False, 'request_length': 0,
            'status': None, 'response_complete': False}
        digest = hashlib.sha256()
        token = _request.set(record)
        self.event('request_arrived')

        async def observed_receive():
            message = await receive()
            if message['type'] == 'http.request':
                chunk = message.get('body', b'')
                digest.update(chunk); record['request_length'] += len(chunk)
                self.event('request_chunk_consumed', length=len(chunk), more_body=bool(message.get('more_body', False)))
                if not message.get('more_body', False):
                    record['request_complete'] = True
                    self.event('request_body_complete')
            elif message['type'] == 'http.disconnect':
                self.event('request_disconnected')
            return message

        async def observed_send(message):
            if message['type'] == 'http.response.start':
                message = message | {'headers': [*message.get('headers', []),
                    (b'x-auto-plan-test-input', record['id'].encode())]}
            await send(message)
            if message['type'] == 'http.response.start':
                record['status'] = message['status']
                self.event('response_started', status=message['status'])
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                record['response_complete'] = True
                self.event('response_body_complete')
        try:
            await self.app(scope, observed_receive, observed_send)
        finally:
            record['request_sha256'] = digest.hexdigest()
            self.records.append(record)
            _request.reset(token)
            if self.output is not None:
                descriptor = os.open(self.output, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
                try:
                    os.write(descriptor, (json.dumps(record, separators=(',', ':')) + '\n').encode())
                finally:
                    os.close(descriptor)
