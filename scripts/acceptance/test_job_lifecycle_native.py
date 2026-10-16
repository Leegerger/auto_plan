"""One opt-in genuine native job proves observer wiring, never full-load overlap."""
from datetime import datetime, timezone
import json
import os
from time import monotonic, sleep
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.input_models import InputSnapshot
from auto_plan.job_process import JobController
from auto_plan.settings import Settings
from scripts.acceptance.job_lifecycle_observer import JobLifecycleObserver, ObservedMemoryJobStore, JobObservationRequests, identity
from tests.input_support import MemoryInputStore
from tests.plan_support import MemoryPlanStore
from tests.support import MemoryAccessStore, CompanyAuthentication
from tests.test_plan_preview import snapshot


@pytest.mark.skipif(os.getenv('AUTO_PLAN_ACCEPTANCE_NATIVE_JOB_PROBE') != '1', reason='Explicit one-job functional grant required')
def test_real_public_native_job_keeps_original_source_uuid_and_process_identity(tmp_path):
    inputs = MemoryInputStore()
    data = InputSnapshot.model_validate(snapshot()['inputs']).model_dump(mode='json')
    data['business']['demands'] = [{'model_code':'M','week':'2026-10-12','quantity':1}]
    inputs.versions[1] = InputSnapshot.model_validate(data | {'input_version':1}); inputs.version = 1
    plans, access = MemoryPlanStore(inputs), MemoryAccessStore()
    access.users['EDITOR'] = {'user_id':'EDITOR','roles':['editor'],'active':True}
    observer = JobLifecycleObserver(tmp_path/'job-observations.ndjson')
    store = ObservedMemoryJobStore(plans, access, observer)
    clock = lambda: datetime(2026,10,14,tzinfo=timezone.utc)
    controller = JobController(store, workers=1, clock=clock, process_factory=observer.process)
    app = create_app(Settings(public_origin='https://testserver'), store=access, input_store=inputs,
        plan_store=plans, job_store=store, job_controller=controller, authenticator=CompanyAuthentication(), clock=clock)
    with TestClient(JobObservationRequests(app), base_url='https://testserver') as client:
        client.headers.update({'Origin':'https://testserver','X-Auto-Plan-Request':'1'})
        assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code == 200
        before = client.get('/auto_plan/api/jobs/source').json()
        command = {'submission_id':str(uuid4()), 'base_revision':0,'input_version':1,'latest_adoption':0,'reviewed_as_of':before['snapshot']['time']['as_of'],
            'request':{'scope':{'start_date':'2026-10-15','end_date':'2026-10-15','line_codes':['L']},'strategy':'hybrid','time_limit_seconds':2,'max_candidates':1,'native_threads':1}}
        response = client.post('/auto_plan/api/jobs',json=command); assert response.status_code == 200, response.text
        accepted = response.json(); job_id = accepted['job_id']
        assert client.post('/auto_plan/api/jobs/verify',json={'submission_id':command['submission_id']}).json() == accepted
        deadline = monotonic()+25
        reads = []
        while monotonic()<deadline:
            nonce = str(uuid4())
            response = client.get('/auto_plan/api/jobs/'+job_id,headers={'X-Auto-Plan-Test-Job-Observation':nonce})
            assert response.status_code == 200
            last = response.json(); reads.append({'nonce':nonce,'body':last})
            if last['state'] not in ('queued','running'): break
            sleep(.05)
        assert last['state'] == 'completed' and last['candidates'], last
        candidate = client.get('/auto_plan/api/jobs/'+job_id+'/candidates/'+last['candidates'][0]['candidate_id']).json()['candidate']
        assert candidate['snapshot'] == before['snapshot'] and candidate['source_revision'] == 0
        assert candidate['evaluation']['capacity_valid'] and candidate['evaluation']['computation_complete']
        assert client.get('/auto_plan/api/jobs/source').json() == before
        assert plans.revision == plans.adoption == 0 and inputs.version == 1
        assert client.post('/auto_plan/api/jobs/verify',json={'submission_id':command['submission_id']}).json() == accepted
    records = [json.loads(line) for line in observer.path.read_text().splitlines()]
    spawned = [row for row in records if row['kind']=='spawned']
    assert len(spawned)==1 and spawned[0]['job_id']==job_id and spawned[0]['native_threads']==1
    living = [row for row in records if row['kind']=='read' and row.get('process_alive')]
    assert living and all(row['pid']==spawned[0]['pid'] and row['start']==spawned[0]['start'] for row in living)
    assert any(row.get('nonce') in {read['nonce'] for read in reads} for row in living)
    assert identity(spawned[0]['pid']) is None
    assert all(process.child.poll() is not None for process,_ in observer.processes.values())
    (tmp_path/'job-proof.json').write_text(json.dumps({'status':'job_observer_pass_backend_unqualified','kind':'small real public ComputeProcess-job functional only','accepted':accepted,'submission':command,'source':before,'reads':reads,'candidate':candidate,'owned_process_closed':True,'acceptance':'not_run','repeats':[]},indent=2)+'\n')
    assert any(row['kind']=='progress' and row.get('route') in ('cp_sat','highs') for row in records), 'Job proof is retained separately; native-backend phase remains unqualified.'
