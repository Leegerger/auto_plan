"""Sequential comparison; one process per case/route, five repetitions."""
from pathlib import Path
from time import perf_counter
import json
import subprocess
import sys

root = Path(__file__).parent
out = Path(sys.argv[1])
out.mkdir(parents=True,exist_ok=True)
entries = []
for case in ('normal-full','normal-local','scrambled-full','scrambled-local',
             'tight-full','tight-local','tail-full','tail-local'):
    for route in ('cp-sat','highs','cp-sat-hint'):
        path = out/f'{case}-{route}.json'
        started = perf_counter()
        result = subprocess.run([sys.executable,str(root/'order_experiment.py'),case,route,str(path),'5'],
                                capture_output=True,text=True)
        (out/f'{case}-{route}.log').write_text(result.stdout+result.stderr)
        entries.append({'case':case,'route':route,'returncode':result.returncode,
                        'process_seconds':perf_counter()-started,'result':str(path.name)})
        (out/'matrix.json').write_text(json.dumps(entries,indent=2)+'\n')
        print(json.dumps(entries[-1]),flush=True)
        if result.returncode:
            print(result.stderr[-1500:],flush=True)
if any(entry['returncode'] for entry in entries):
    raise SystemExit(1)
