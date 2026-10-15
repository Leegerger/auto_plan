"""Owned exec fixture for real truncation, stop and descendant-kill evidence."""
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import time
from auto_plan.job_compute import emit
from auto_plan.solver_models import FrozenSolveInput
from auto_plan.job_codec import PAYLOAD_LIMIT

mode,evidence=sys.argv[1:3]
data=json.loads(sys.stdin.buffer.read(PAYLOAD_LIMIT+1))
frozen=FrozenSolveInput.model_validate(data['frozen'])
assert not any(key.startswith(('AUTO_PLAN_','T37_PARENT_SECRET')) for key in os.environ)
assert not any(name.startswith('oracledb') for name in sys.modules)
descendant=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],close_fds=True)
Path(evidence).write_text(json.dumps({'pid':os.getpid(),'descendant_pid':descendant.pid,'secret_free':True,'oracle_imported':False}))
if mode!='empty_stall':
    from auto_plan.solver import solve
    result=solve(frozen)
    for candidate in result.candidates:
        emit({'kind':'candidate','candidate':candidate.model_dump(mode='json')})
if mode=='bad_manifest':
    emit({'kind':'result','result':{'status':'feasible','elapsed_seconds':0,'global_optimal':False},'manifest':['b'*64]})
    descendant.terminate();descendant.wait()
    raise SystemExit(0)
if mode=='partial':
    sys.stdout.buffer.write(struct.pack('!I',500)+b'{"kind":"candidate"')
    sys.stdout.buffer.flush()
    descendant.terminate();descendant.wait()
    raise SystemExit(0)
if mode=='oversized':
    sys.stdout.buffer.write(struct.pack('!I',PAYLOAD_LIMIT+1));sys.stdout.buffer.flush()
    descendant.terminate();descendant.wait()
    raise SystemExit(0)
while True: time.sleep(.05)
