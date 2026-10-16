"""Opt-in test-adapter ownership observations; real stores/processes are delegated once."""
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import subprocess
from threading import Lock, local
from time import perf_counter_ns
from uuid import UUID
from tests.jobs_support import MemoryJobStore

request_nonce = ContextVar('acceptance_job_observation', default=None)


def identity(pid):
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        fields = raw[raw.rfind(')')+2:].split()
        return {'pid': pid, 'start': fields[19], 'pgid': int(fields[2])}
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(['ps', '-o', 'pid=,pgid=,stat=,lstart=', '-p', str(pid)], capture_output=True, text=True, check=False).stdout.strip()
        parts = out.split(None, 3)
        if len(parts) < 4 or parts[2].startswith('Z'):
            return None
        return {'pid': int(parts[0]), 'start': ''.join(ch for ch in parts[3] if ch.isdigit()), 'pgid': int(parts[1])}
    except (OSError, ValueError, IndexError):
        return None


class JobLifecycleObserver:
    def __init__(self, path, construct=None):
        self.path = Path(path)
        self.path.open('x').close()
        self.construct = construct
        self.local = local()
        self.lock = Lock()
        self.processes = {}
        self.failed_acquisitions = []

    def emit(self, value):
        with self.lock, self.path.open('a') as stream:
            stream.write(json.dumps({'at_ns': perf_counter_ns(), **value}, separators=(',', ':'))+'\n')

    def claim(self, value):
        self.local.claim = value

    def process(self, frozen, threads):
        from auto_plan.job_process import ComputeProcess
        claim = self.local.claim
        if claim is None or claim['frozen'] is not frozen:
            raise ValueError('Actual claimed frozen input identity is required')
        owner = {key: claim[key] for key in ('job_id', 'run_id', 'generation')}
        process = (self.construct or ComputeProcess)(frozen, threads)
        try:
            payload = json.dumps(frozen.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            own = identity(process.child.pid)
            if own is None:
                # A genuine early exit is an unknown witness, never a running proof.
                self.emit({'kind': 'spawn_unknown', 'job_id': claim['job_id'], 'run_id': claim['run_id'], 'generation': claim['generation']})
                return process
            record = {'job_id': claim['job_id'], 'run_id': claim['run_id'], 'generation': claim['generation'], **own,
                'frozen_sha256': hashlib.sha256(payload).hexdigest(), 'source_revision': frozen.source_revision,
                'input_version': frozen.input_version, 'latest_adoption': frozen.latest_adoption, 'native_threads': threads}
            self.processes[claim['job_id']] = (process, record)
            self.emit({'kind': 'spawned', **record})
            return process
        except BaseException as error:
            # The controller owns nothing until this factory returns. Roll back
            # only the handle we acquired, even when observer publication fails.
            failure = {**owner, 'primary_error_class': type(error).__name__,
                'close_returned': False, 'close_error_class': None, 'ownership_closed': None}
            try:
                process.close()
                failure['close_returned'] = True
            except BaseException as close_error:
                failure['close_error_class'] = type(close_error).__name__
            try:
                self.failed_acquisitions.append((process, failure))
            except BaseException:
                pass  # Bookkeeping failure must not replace the primary error.
            raise

    def read(self, body, started_ns):
        item = self.processes.get(body['job_id'])
        if item is None:
            self.emit({'kind': 'read', 'nonce': request_nonce.get(), 'job_id': body['job_id'], 'state': body['state'],
                'started_ns': started_ns, 'finished_ns': perf_counter_ns(), 'process_alive': False, 'reason': 'No observed real process'})
            return
        process, owned = item
        actual = identity(owned['pid'])
        alive = actual == {key: owned[key] for key in ('pid', 'start', 'pgid')} and process.child.poll() is None
        self.emit({'kind': 'read', **owned, 'nonce': request_nonce.get(), 'state': body['state'], 'process_alive': alive,
            'started_ns': started_ns, 'finished_ns': perf_counter_ns()})


class ObservedMemoryJobStore(MemoryJobStore):
    def __init__(self, plans, access, observer):
        super().__init__(plans, access)
        self.observer = observer

    def claim(self, now):
        result = super().claim(now)
        self.observer.claim(result)
        return result

    def get(self, identity, actor, now):
        started = perf_counter_ns()
        result = super().get(identity, actor, now)
        self.observer.read(result, started)
        return result

    def progress(self, identity, run, generation, event, now):
        result = super().progress(identity, run, generation, event, now)
        self.observer.emit({'kind': 'progress', 'job_id': identity, 'run_id': run, 'generation': generation,
            'stage': event.get('stage'), 'status': event.get('status'), 'detail': event.get('detail'), 'route': event.get('route'), 'native_layers': event.get('native_layers')})
        return result


class JobObservationRequests:
    def __init__(self, app):
        self.app = app

    def __getattr__(self, key):
        return getattr(self.app, key)

    async def __call__(self, scope, receive, send):
        nonce = None
        if scope['type'] == 'http':
            headers = dict(scope.get('headers', []))
            raw = headers.get(b'x-auto-plan-test-job-observation')
            if raw is not None:
                try:
                    nonce = str(UUID(raw.decode('ascii')))
                except (ValueError, UnicodeError):
                    pass
        token = request_nonce.set(nonce)
        try:
            await self.app(scope, receive, send)
        finally:
            request_nonce.reset(token)
