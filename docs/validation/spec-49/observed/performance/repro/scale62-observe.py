"""Observe the unchanged public scale test; preserve its exact assertions."""
import gzip
import hashlib
import json
from pathlib import Path
from time import perf_counter, process_time

from fastapi.testclient import TestClient
import pytest

directory = Path('/tmp/auto-plan-spec49/final62-evidence/cpu')
directory.mkdir(parents=True, exist_ok=True)
samples = []
responses = []
original = TestClient.post


def observed(client, url, *args, **kwargs):
    if not str(url).endswith('/plans/preview'):
        return original(client, url, *args, **kwargs)
    started, cpu = perf_counter(), process_time()
    response = original(client, url, *args, **kwargs)
    elapsed, cpu = perf_counter() - started, process_time() - cpu
    samples.append({'sample': len(samples) + 1, 'wall_seconds': elapsed,
        'process_cpu_seconds': cpu, 'status': response.status_code,
        'bytes': len(response.content)})
    responses.append((response.content, kwargs['json']))
    return response


TestClient.post = observed
try:
    result = int(pytest.main(['-q', 'tests/test_evaluator_scale.py']))
finally:
    TestClient.post = original
    # Hashing, serialization and filesystem writes happen after the test's
    # timed assertions, not inside a measured public request.
    for index, (body, request) in enumerate(responses, 1):
        samples[index - 1]['sha256'] = hashlib.sha256(body).hexdigest()
        (directory / f'preview-{index}.json.gz').write_bytes(gzip.compress(body, mtime=0))
        if index == 1:
            (directory / 'request.json').write_text(json.dumps(request, indent=2) + '\n')
    (directory / 'samples.json').write_text(json.dumps({'measurement':
        'Public TestClient.post wall and process CPU; artifact work deferred until after all assertions. The unchanged original five-second test assertion remains authoritative.',
        'samples': samples}, indent=2) + '\n')
raise SystemExit(result)
