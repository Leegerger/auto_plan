"""Sequential prototype runs, preserving failures and full subprocess output."""
from pathlib import Path
from time import perf_counter
import json
import subprocess
import sys

root=Path(__file__).parent
out=Path(sys.argv[1])
out.mkdir(parents=True,exist_ok=True)
entries=[]
for case,variant in [('local','uniform5'),('full','uniform7'),('full','uneven'),('full','scrambled5')]:
    path=out/f'{case}-{variant}.json'
    began=perf_counter()
    result=subprocess.run([sys.executable,str(root/'decomposition_experiment.py'),case,variant,str(path)],capture_output=True,text=True)
    path.with_suffix('.log').write_text(result.stdout+result.stderr)
    entries.append({'case':case,'variant':variant,'returncode':result.returncode,'process_seconds':perf_counter()-began,'result':path.name})
    (out/'matrix.json').write_text(json.dumps(entries,indent=2)+'\n')
    print(json.dumps(entries[-1]),flush=True)
    print(result.stdout+result.stderr,flush=True)
if any(r['returncode'] for r in entries):raise SystemExit(1)
