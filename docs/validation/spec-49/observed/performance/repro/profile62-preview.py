"""One unchanged dense public preview, profiled in its actual sync worker."""
import argparse
import cProfile
from functools import wraps
import gzip
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import pstats
import subprocess
from threading import get_ident
from time import perf_counter, process_time

from fastapi.testclient import TestClient
from tests.scale_support import full_window, seeded_app, final_day_command, check_final_day

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--profile', action='store_true')
parser.add_argument('--budget', type=float)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
payload = full_window()
app = seeded_app(payload)
profiles = []
worker_ids = []
if args.profile:
    routes = [route for included in app.routes for route in (included.effective_route_contexts() if hasattr(included,'effective_route_contexts') else [included])]
    endpoint = next(route for route in routes if getattr(route,'path','') == '/auto_plan/api/plans/preview')
    original = endpoint.dependant.call
    @wraps(original)
    def profiled_endpoint(*a, **kw):
        profiler = cProfile.Profile()
        profiles.append(profiler)
        worker_ids.append(get_ident())
        profiler.enable()
        try:
            return original(*a, **kw)
        finally:
            profiler.disable()
    endpoint.dependant.call = profiled_endpoint
with TestClient(app, base_url='https://testserver') as client:
    client.headers.update({'Origin':'https://testserver','X-Auto-Plan-Request':'1'})
    assert client.post('/auto_plan/api/auth/login',json={'username':'SCALEEDITOR','password':'company-secret'}).status_code == 200
    request = final_day_command(payload)
    began, cpu = perf_counter(), process_time()
    response = client.post('/auto_plan/api/plans/preview',json=request)
    elapsed, cpu_elapsed = perf_counter()-began, process_time()-cpu
    assert response.status_code == 200, response.text[:1000]
    body = response.json()
    check_final_day(body['evaluation'])
    assert body['plan']['switches'] == payload['plan']['switches']
    original_rows = {row['id']:row for row in payload['plan']['segments']}
    assert all(row == original_rows[row['id']] for row in body['plan']['segments'] if row['date'] != '2027-01-24')
    assert client.app.state.plan_store.revision == 1
    assert client.app.state.plan_store.plan.model_dump(mode='json')['segments'] == payload['plan']['segments']
    digest = hashlib.sha256(response.content).hexdigest()
    assert digest == '33f8eec3087d9258b787e76a6f558d4db4c0dcb1878775f07ecde34c330dd1e5',digest
    result = {'profiled':args.profile,'wall_seconds':elapsed,'process_cpu_seconds':cpu_elapsed,
        'status':response.status_code,'response_bytes':len(response.content),'response_sha256':digest,
        'worker_profile_count':len(profiles),'worker_thread_ids':worker_ids,
        'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'src_tree':subprocess.check_output(['git','rev-parse','HEAD:src'],text=True).strip(),
        'python':platform.python_version(),'platform':platform.platform(),
        'packages':{name:version(name) for name in ('pydantic','fastapi','starlette','httpx')},
        'cpu_count':os.cpu_count(),'affinity':sorted(os.sched_getaffinity(0)),
        'literals':{'segments':4200,'switches':4182,'output':419960,'gap':40,'unknown_conditions':18},
        'outside_and_source_unchanged':True}
    (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    (args.output/'request.json').write_text(json.dumps(request,indent=2)+'\n')
    (args.output/'response.json.gz').write_bytes(gzip.compress(response.content,mtime=0))
    if args.profile:
        assert len(profiles)==1
        profiles[0].dump_stats(str(args.output/'worker.prof'))
        with (args.output/'worker-profile.txt').open('w') as stream:
            pstats.Stats(profiles[0],stream=stream).strip_dirs().sort_stats('cumulative').print_stats(70)
            pstats.Stats(profiles[0],stream=stream).strip_dirs().sort_stats('tottime').print_stats(40)
        with (args.output/'worker-callers.txt').open('w') as stream:
            pstats.Stats(profiles[0],stream=stream).strip_dirs().sort_stats('cumulative').print_callers('workspace_view|_line_weeks|deepcopy|analyze_recipe_states')
    print(json.dumps(result),flush=True)
    if args.budget is not None:
        assert elapsed <= args.budget, f'Unchanged public preview exceeded{args.budget}s: {elapsed:.6f}s'
