"""Throwaway worker interruption experiment. Files stand in for task storage.

No Oracle, authorization, queue or browser behavior is certified here.
"""
from pathlib import Path
from time import monotonic, sleep
import hashlib
import json
import subprocess
import sys

from quantity_experiment import evaluate

root=Path(__file__).parent
out=Path(sys.argv[1])
out.mkdir(parents=True,exist_ok=True)
results=[]
for route in ('cp-sat','cp-sat-hint','highs'):
    path=out/f'cancel-{route}.json'
    for suffix in ('.json','.candidate.json','.phase.json','.stop'):
        path.with_suffix(suffix).unlink(missing_ok=True)
    with path.with_suffix('.log').open('w') as log:
        began=monotonic()
        child=subprocess.Popen([sys.executable,str(root/'quantity_experiment.py'),
            'full',route,str(path),'120'],stdout=log,stderr=log)
        phase=path.with_suffix('.phase.json')
        while child.poll() is None and not phase.exists() and monotonic()-began<30:
            sleep(.05)
        if not phase.exists():
            child.terminate();child.wait(timeout=10)
            raise RuntimeError('worker never entered solve phase')
        candidate=path.with_suffix('.candidate.json')
        if route=='highs':
            while child.poll() is None and not candidate.exists() and monotonic()-began<30:
                sleep(.05)
        before=candidate.read_bytes() if candidate.exists() else None
        requested=monotonic()
        path.with_suffix('.stop').write_text('stop and preserve validated candidate\n')
        try:
            child.wait(timeout=10)
            mode='cooperative'
        except subprocess.TimeoutExpired:
            child.kill();child.wait(timeout=10)
            mode='interrupted_after_10s'
        response=monotonic()-requested
        after=candidate.read_bytes() if candidate.exists() else None
        exact=False
        if after:
            artifact=json.loads(after)
            result=evaluate(artifact['input'],artifact['candidate'],set(artifact['mutable']))
            exact=result['vector']==artifact['vector'] and not any(result['vector'][:30])
        results.append({'route':route,'termination':mode,'returncode':child.returncode,
            'stop_response_seconds':response,'had_validated_candidate_before_stop':before is not None,
            'has_candidate_after_stop':after is not None,'candidate_bytes_unchanged':before==after,
            'retained_candidate_passes_exact_subset_check':exact,
            'sha256':hashlib.sha256(after).hexdigest() if after else None,
            'scope':__doc__})
        print(json.dumps(results[-1]),flush=True)
(out/'cancellation-results.json').write_text(json.dumps(results,indent=2)+'\n')
