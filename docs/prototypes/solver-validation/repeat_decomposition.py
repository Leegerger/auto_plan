"""One process, first call includes solver import, subsequent calls are warm."""
from pathlib import Path
from time import perf_counter
import json
import sys
from decomposition_experiment import run

case,variant,target=sys.argv[1:4]
out=Path(target)
out.mkdir(parents=True,exist_ok=True)
records=[]
for repetition in range(5):
    began=perf_counter()
    path=out/f'{case}-{variant}-{repetition}.json'
    result=run(case=='local',variant,path,30 if case=='local' else 120)
    records.append({'repetition':repetition,'process_first_run':repetition==0,
        'wall_seconds':perf_counter()-began,'result':path.name,
        'quality_met':result['reference_quality_met'],'within_compute_target':result['within_compute_target'],
        'vector':result['vector']})
    (out/'repeats.json').write_text(json.dumps(records,indent=2)+'\n')
