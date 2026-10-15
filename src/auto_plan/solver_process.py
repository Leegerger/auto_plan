"""Bounded fresh-native-process transport, without Oracle or native imports."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def cpu_threads(requested):
    available = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count() or 2
    try:
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota != 'max':
            available = min(available, max(1, int(quota) // int(period)))
    except (OSError, ValueError):
        pass
    return max(1, min(requested, max(1, available - 1)))


def select(route, objectives, seconds, threads, should_stop):
    """One route child per call; stop does not depend on a solver incumbent."""
    return run({'route': route, 'objectives': objectives, 'seconds': seconds,
                'threads': cpu_threads(threads)}, should_stop)


def optimize(route, model, seconds, threads, should_stop):
    return run({'route': route, 'model': model, 'seconds': seconds, 'threads': cpu_threads(threads)}, should_stop)


def run(data, should_stop):
    seconds = data['seconds']
    # Disk-backed bounded output avoids deadlocks from unread pipes and never
    # mixes native progress logs with the structured result stream.
    with tempfile.TemporaryDirectory(prefix='auto-plan-native-') as directory, tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        cancel = Path(directory) / 'stop'
        payload = json.dumps(data | {'cancel_file': str(cancel)}, separators=(',', ':')).encode()
        if len(payload) > 16 * 1024 * 1024:
            return {'status': 'failed', 'error': 'Native request too large'}
        source.write(payload)
        source.seek(0)
        child = subprocess.Popen([sys.executable, '-m', 'auto_plan.solver_native'], stdin=source,
            stdout=output, stderr=errors, close_fds=True)
        start = time.monotonic()
        stopped = False
        stop_at = None
        try:
            while child.poll() is None:
                if should_stop() or time.monotonic() - start >= seconds:
                    stopped = True
                    if stop_at is None:
                        stop_at = time.monotonic()
                        cancel.touch()
                    elif time.monotonic() - stop_at >= 10:
                        child.kill()
                        child.wait()
                        break
                time.sleep(0.05)
            output.seek(0)
            result = output.read(1024 * 1024 + 1)
            if stopped:
                return {'status': 'interrupted'}
            if len(result) > 1024 * 1024:
                return {'status': 'failed', 'error': 'Native output too large'}
            try:
                return json.loads(result)
            except (ValueError, UnicodeError):
                return {'status': 'failed', 'error': 'Malformed native result'}
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
