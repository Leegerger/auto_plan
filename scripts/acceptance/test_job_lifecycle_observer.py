"""Local ownership controls; a blocked fixture child is never a solver/job result."""
from pathlib import Path
import json
import subprocess
import sys
from types import SimpleNamespace
from time import perf_counter_ns
from scripts.acceptance.job_lifecycle_observer import JobLifecycleObserver, request_nonce


def test_actual_factory_is_delegated_once_and_owned_pid_start_survives_reads(tmp_path):
    child = subprocess.Popen([sys.executable, '-c', 'import sys;sys.stdin.read()'], stdin=subprocess.PIPE)
    frozen = SimpleNamespace(source_revision=1, input_version=1, latest_adoption=1, model_dump=lambda **kw: {'local_fixture': True})
    calls = []
    process = SimpleNamespace(child=child)
    def construct(value, threads):
        calls.append((value, threads))
        return process
    observer = JobLifecycleObserver(tmp_path/'witness.ndjson', construct)
    observer.claim({'job_id':'J', 'run_id':'R', 'generation':'G', 'frozen':frozen})
    try:
        assert observer.process(frozen, 1) is process
        token = request_nonce.set('7536caab-806d-4830-86d8-ec90c5ff6562')
        try:
            observer.read({'job_id':'J', 'state':'running'}, perf_counter_ns())
        finally:
            request_nonce.reset(token)
        child.stdin.close(); child.wait(timeout=5)
        observer.read({'job_id':'J', 'state':'running'}, perf_counter_ns())
        rows = [json.loads(line) for line in observer.path.read_text().splitlines()]
        assert calls == [(frozen,1)]
        assert [row['kind'] for row in rows] == ['spawned','read','read']
        assert rows[1]['process_alive'] is True and rows[2]['process_alive'] is False
        assert rows[0]['start'] == rows[1]['start'] == rows[2]['start']
        assert rows[1]['nonce'] == '7536caab-806d-4830-86d8-ec90c5ff6562'
    finally:
        if child.poll() is None: child.terminate(); child.wait(timeout=5)
