"""Stop the decomposed worker after its first independently checked candidate."""
from pathlib import Path
from time import monotonic,sleep
import json
import subprocess
import sys
from decomposition_experiment import validate

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
path=out/'decomposition-stop.json'
for suffix in ('.json','.candidate.json','.stop'):
    path.with_suffix(suffix).unlink(missing_ok=True)
with path.with_suffix('.log').open('w') as log:
    child=subprocess.Popen([sys.executable,str(Path(__file__).with_name('decomposition_experiment.py')),
        'full','scrambled5',str(path)],stdout=log,stderr=log)
    began=monotonic();candidate=path.with_suffix('.candidate.json')
    while child.poll() is None and not candidate.exists() and monotonic()-began<20:
        sleep(.02)
    if not candidate.exists():
        child.kill();child.wait();raise RuntimeError('no candidate saved within observation period')
    before=json.loads(candidate.read_text())
    requested=monotonic();path.with_suffix('.stop').write_text('stop\n')
    try:
        child.wait(timeout=10);mode='cooperative'
    except subprocess.TimeoutExpired:
        child.kill();child.wait();mode='interrupted_after_10s'
    response=monotonic()-requested
    after=json.loads(candidate.read_text())
    vector=validate(after['input'],after['candidate'],{tuple(c) for c in after['mutable_cells']})
    result={'mode':mode,'returncode':child.returncode,'stop_response_seconds':response,
        'before_vector':before['vector'],'after_vector':after['vector'],
        'retained_or_improved':after['vector']<=before['vector'],
        'exact_subset_check':vector==after['vector'] and not any(vector[:30]),
        'scope':'local subprocess and file publication only; not Oracle/browser/task authorization'}
    (out/'cancellation-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
    if mode!='cooperative' or not result['retained_or_improved'] or not result['exact_subset_check']:raise SystemExit(1)
