"""CPU-only public API diagnostic, with complete response hashes and profiles.

Run from the repository with PYTHONPATH=src:. python scripts/measure_evaluator.py
--output /tmp/auto-plan-measurement. External services use test adapters.
"""
import argparse
import cProfile
import gzip
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import pstats
import resource
import subprocess
from time import perf_counter, process_time

from fastapi.testclient import TestClient
import auto_plan
from tests.scale_support import full_window, seeded_app, final_day_command, check_final_day, check_full_window


def read_limit(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def measure(output):
    output.mkdir(parents=True, exist_ok=False)
    payload = full_window()
    fixture = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    (output / 'fixture.json').write_bytes(fixture)
    package = Path(auto_plan.__file__).resolve().parent
    sources = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(package.glob('*.py'))}
    cpu = next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), None)
    evidence = {
        'kind': 'CPU diagnostic; excludes Oracle, network, browser and concurrent editors',
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'source_import': str(package), 'source_files_sha256': sources,
        'fixture_sha256': hashlib.sha256(fixture).hexdigest(), 'fixture_bytes': len(fixture),
        'environment': {'python': platform.python_version(), 'platform': platform.platform(),
            'cpu_model': cpu, 'affinity': sorted(os.sched_getaffinity(0)),
            'cpu_max': read_limit('/sys/fs/cgroup/cpu.max'), 'memory_max': read_limit('/sys/fs/cgroup/memory.max'),
            'load_average': os.getloadavg(), 'exclusive_resources': False,
            'packages': {name: version(name) for name in ('fastapi', 'starlette', 'pydantic', 'httpx', 'oracledb')}},
        'requests': [],
    }
    with TestClient(seeded_app(payload), base_url='https://testserver') as client:
        client.headers.update({'Origin': 'https://testserver', 'X-Auto-Plan-Request': '1'})
        assert client.post('/auto_plan/api/auth/login', json={'username': 'SCALEEDITOR', 'password': 'company-secret'}).status_code == 200
        for kind, route, command in [('diagnostic', 'evaluate', {'snapshot': payload}),
                                     ('shared', 'preview', final_day_command(payload))]:
            for repeat in range(3):
                began, cpu_began = perf_counter(), process_time()
                response = client.post('/auto_plan/api/plans/' + route, json=command)
                elapsed, cpu_elapsed = perf_counter() - began, process_time() - cpu_began
                assert response.status_code == 200, response.text[:1000]
                rendered = response.json()
                result = rendered if kind == 'diagnostic' else rendered['evaluation']
                (check_full_window if kind == 'diagnostic' else check_final_day)(result)
                canonical = json.dumps(rendered, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                with gzip.open(output / (kind + '-response.json.gz'), 'wb') as file:
                    file.write(canonical)
                row = {'kind': kind, 'repeat': repeat, 'seconds': elapsed, 'cpu_seconds': cpu_elapsed,
                    'response_bytes': len(response.content), 'canonical_sha256': hashlib.sha256(canonical).hexdigest(),
                    'within_5_seconds': elapsed <= 5, 'through_checks_seconds': perf_counter() - began,
                    'totals': result['output_totals']}
                evidence['requests'].append(row)
                print(json.dumps(row), flush=True)
        profile = cProfile.Profile()
        response = profile.runcall(client.post, '/auto_plan/api/plans/preview', json=final_day_command(payload))
        assert response.status_code == 200
        check_final_day(response.json()['evaluation'])
        profile.dump_stats(str(output / 'shared.prof'))
        with (output / 'profile.txt').open('w') as file:
            pstats.Stats(profile, stream=file).strip_dirs().sort_stats('cumulative').print_stats(80)
    evidence['peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    (output / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='new directory, never overwrites a prior run')
    measure(parser.parse_args().output)
