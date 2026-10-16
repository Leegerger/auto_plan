"""Distinct native-quantity successor;171's two-second hybrid failure stays sealed.

This explicit standard20-second local CP-SAT case follows the existing public
native quantity business test. It runs only after a separate one-job grant.
"""
from datetime import datetime, timezone
import json
import hashlib
import os
from time import monotonic, sleep
from uuid import uuid4
import pytest
from scripts.acceptance.native_quantity_case import quantity_inputs, quantity_command, native_quantity_stage, quantity_objective, PublicJobProof


@pytest.mark.skipif(os.getenv('AUTO_PLAN_ACCEPTANCE_NATIVE_QUANTITY_PROBE') != '1', reason='Separate one-job native-quantity grant required')
def test_native_quantity_public_job_keeps_source_uuid_candidate_and_owned_process(tmp_path):
    from fastapi.testclient import TestClient
    from auto_plan.app import create_app
    from auto_plan.job_process import JobController
    from auto_plan.settings import Settings
    from scripts.acceptance.job_lifecycle_observer import JobLifecycleObserver, ObservedMemoryJobStore, JobObservationRequests, identity
    from tests.input_support import MemoryInputStore
    from tests.plan_support import MemoryPlanStore
    from tests.support import MemoryAccessStore, CompanyAuthentication
    inputs = MemoryInputStore()
    inputs.versions[1] = quantity_inputs(); inputs.version = 1
    plans, access = MemoryPlanStore(inputs), MemoryAccessStore()
    access.users['EDITOR'] = {'user_id':'EDITOR','roles':['editor'],'active':True}
    observer = JobLifecycleObserver(tmp_path/'job-observations.ndjson')
    store = ObservedMemoryJobStore(plans, access, observer)
    clock = lambda: datetime(2026,10,14,tzinfo=timezone.utc)
    controller = JobController(store, workers=1, clock=clock, process_factory=observer.process)
    app = create_app(Settings(public_origin='https://testserver'), store=access, input_store=inputs,
        plan_store=plans, job_store=store, job_controller=controller, authenticator=CompanyAuthentication(), clock=clock)
    proof = PublicJobProof(tmp_path/'public')
    try:
        with TestClient(JobObservationRequests(app), base_url='https://testserver') as client:
            client.headers.update({'Origin':'https://testserver','X-Auto-Plan-Request':'1'})
            # No credentials, cookies or authentication body enter the proof files.
            assert client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'}).status_code == 200
            response = client.get('/auto_plan/api/jobs/source')
            before = proof.capture('source-before', response)
            assert response.status_code == 200
            assert (before['source_revision'],before['input_version'],before['latest_adoption']) == (0,1,0)
            assert before['snapshot']['inputs'] == quantity_inputs().model_dump(mode='json')
            assert before['snapshot']['plan']['segments'] == before['snapshot']['plan']['switches'] == []
            command = quantity_command(before, str(uuid4()))
            (tmp_path/'submitted-command.json').write_text(json.dumps(command,indent=2)+'\n')
            response = client.post('/auto_plan/api/jobs',json=command)
            accepted = proof.capture('submitted', response, command)
            assert response.status_code == 200, response.text
            job_id = accepted['job_id']
            verify = {'submission_id':command['submission_id']}
            response = client.post('/auto_plan/api/jobs/verify',json=verify)
            verified = proof.capture('verified-before', response, verify)
            assert response.status_code == 200 and verified == accepted
            deadline, reads = monotonic()+45, []
            while monotonic()<deadline:
                nonce = str(uuid4())
                response = client.get('/auto_plan/api/jobs/'+job_id,headers={'X-Auto-Plan-Test-Job-Observation':nonce})
                last = proof.capture('job-read', response)
                reads.append({'nonce':nonce,'body':last})
                with (tmp_path/'read-correlations.ndjson').open('a') as stream:
                    stream.write(json.dumps(reads[-1])+'\n')
                assert response.status_code == 200
                if last['state'] not in ('queued','running'): break
                sleep(.05)
            assert last['state'] == 'completed' and last['candidates'], last
            response = client.get('/auto_plan/api/jobs/'+job_id+'/candidates/'+last['candidates'][0]['candidate_id'])
            offered = proof.capture('candidate', response)
            assert response.status_code == 200
            candidate = offered['candidate']
            evaluation_command = {'snapshot': {**candidate['snapshot'], 'plan': candidate['plan']}}
            response = client.post('/auto_plan/api/plans/evaluate', json=evaluation_command)
            independently_evaluated = proof.capture('candidate-evaluated', response, evaluation_command)
            assert response.status_code == 200
            response = client.get('/auto_plan/api/jobs/source')
            after = proof.capture('source-after', response)
            source_status = response.status_code
            response = client.post('/auto_plan/api/jobs/verify',json=verify)
            verified_after = proof.capture('verified-after', response, verify)
            verify_status = response.status_code
            # Complete bodies above are retained before these business assertions.
            assert source_status == verify_status == 200
            assert candidate['snapshot'] == before['snapshot']
            assert (candidate['source_revision'],candidate['input_version'],candidate['latest_adoption']) == (0,1,0)
            assert candidate['request'] == command['request']
            assert candidate['eligibility'] == 'executable'
            for key in ['capacity_valid','computation_complete','executable']:
                assert candidate['evaluation'][key] is True
            assert candidate['evaluation'] == independently_evaluated
            assert candidate['objective'] == quantity_objective(independently_evaluated, before['snapshot']['time']['window_start'])
            assert candidate['objective'][:30] == ['0']*30
            for key, expected in {'expected':1000,'on_time':1000,'gap':0,'late':0,'pending':0,'unallocated':0,'outside_window':0}.items():
                assert independently_evaluated['output_totals'][key] == expected
            assert after == before and plans.revision == plans.adoption == 0 and inputs.version == 1
            assert verified_after == accepted
    finally:
        records = [json.loads(line) for line in observer.path.read_text().splitlines()]
        owned = []
        for process, record in observer.processes.values():
            try:
                os.killpg(record['pgid'],0)
                group_closed = False
            except ProcessLookupError:
                group_closed = True
            except OSError:
                group_closed = None  # Unknown cleanup must not mask the original failure.
            owned.append({**record,'actual_identity':identity(record['pid']),'returncode':process.child.poll(),'group_closed':group_closed})
        cleanup = {'owned':owned,'all_closed':bool(owned) and all(row['actual_identity'] is None and row['returncode'] is not None and row['group_closed'] for row in owned),'acceptance':'not_run','repeats':[]}
        (tmp_path/'cleanup.json').write_text(json.dumps(cleanup,indent=2)+'\n')
    spawned = [row for row in records if row['kind']=='spawned']
    assert len(spawned)==1 and spawned[0]['job_id']==job_id and spawned[0]['native_threads']==1
    frozen = {'snapshot':before['snapshot'],'source_revision':0,'input_version':1,'latest_adoption':0,'request':command['request']}
    digest = hashlib.sha256(json.dumps(frozen,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    assert spawned[0]['frozen_sha256'] == digest
    assert (spawned[0]['source_revision'],spawned[0]['input_version'],spawned[0]['latest_adoption']) == (0,1,0)
    living = [row for row in records if row['kind']=='read' and row.get('process_alive')]
    assert living and all(row['pid']==spawned[0]['pid'] and row['start']==spawned[0]['start'] for row in living)
    assert any(row.get('nonce') in {read['nonce'] for read in reads} for row in living)
    assert cleanup['all_closed']
    stage = native_quantity_stage(records, spawned[0])
    assert 'Optimal' in stage['detail'] or 'OPTIMAL' in stage['detail']
    (tmp_path/'result.json').write_text(json.dumps({'status':'pass','case':'native-quantity-cp-sat20s-successor','accepted':accepted,'submission':command,'source':before,'reads':reads,'candidate':candidate,'native_stage':stage,'cleanup':cleanup,'acceptance':'not_run','repeats':[]},indent=2)+'\n')
