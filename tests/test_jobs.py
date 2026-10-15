"""Durable calculation behavior through the public business HTTP interface."""
from uuid import uuid4
import pytest
from tests.test_plan_transactions import shared_client


def test_synchronous_compute_cannot_bypass_the_durable_queue(shared_client):
    client, _, _ = shared_client
    response = client.post('/auto_plan/api/plans/candidates/preview', json={
        'base_revision': 0, 'input_version': 1, 'latest_adoption': 0,
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L']}}})
    assert response.status_code == 410
    assert response.json()['replacement'] == '/jobs'


def submission(**extra):
    return {'submission_id': str(uuid4()), 'base_revision': 0, 'input_version': 1, 'latest_adoption': 0,
        'request': {'scope': {'start_date': '2026-10-15', 'end_date': '2026-10-15', 'line_codes': ['L']}, 'time_limit_seconds': 1}, **extra}


def test_submission_is_recoverable_by_separate_uuid_and_never_writes_a_revision(shared_client):
    from tests.jobs_support import MemoryJobStore
    client, plans, _ = shared_client
    jobs = MemoryJobStore(plans, client.app.state.access_store)
    client.app.state.job_store = jobs
    payload = submission()
    accepted = client.post('/auto_plan/api/jobs', json=payload)
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()
    assert result['status'] == 'ACCEPTED'
    assert result['job_id'] != payload['submission_id']
    assert plans.revision == plans.adoption == 0
    assert client.post('/auto_plan/api/jobs', json=payload).json() == result
    assert client.post('/auto_plan/api/jobs/verify', json={'submission_id': payload['submission_id']}).json() == result
    assert client.get('/auto_plan/api/jobs').json()['jobs'][0]['state'] == 'queued'
    unknown = str(uuid4())
    assert client.post('/auto_plan/api/jobs/verify', json={'submission_id': unknown}).json()['status'] == 'ABORTED_VOID'
    assert client.post('/auto_plan/api/jobs', json=submission(submission_id=unknown)).status_code == 409


def wait_job(client,identity,timeout=15):
    import time
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        response=client.get('/auto_plan/api/jobs/'+identity)
        assert response.status_code==200,response.text
        if response.json()['state'] not in {'queued','running'}: return response.json()
        time.sleep(.05)
    raise AssertionError('Job did not finish within the test deadline')


def install_controller(client,plans,workers=1,process_factory=None):
    from tests.jobs_support import MemoryJobStore
    from auto_plan.job_process import JobController
    store=MemoryJobStore(plans,client.app.state.access_store,worker_limit=workers)
    controller=JobController(store,workers=workers,clock=client.app.state.clock,process_factory=process_factory)
    client.app.state.job_store=store;client.app.state.job_controller=controller
    controller.recover_startup();controller.start()
    return controller


def test_fresh_compute_commits_complete_candidate_and_reference_application_preserves_independent_edit(shared_client):
    from tests.test_solver import setup
    from tests.test_plan_transactions import command,post,patch,row
    client,plans,inputs=shared_client
    setup(inputs,[{'model_code':'M','week':'2026-10-12','quantity':1}])
    controller=install_controller(client,plans)
    try:
        submitted=client.post('/auto_plan/api/jobs',json=submission())
        assert submitted.status_code==200,submitted.text
        identity=submitted.json()['job_id']
        finished=wait_job(client,identity)
        assert finished['state']=='completed',finished
        reference=finished['candidates'][0]
        viewed=client.get(f"/auto_plan/api/jobs/{identity}/candidates/{reference['candidate_id']}").json()
        assert sum(r['quantity'] for r in viewed['candidate']['plan']['segments'])==1
        assert viewed['candidate']['objective']==['0']*33
        # N on another day/line is independent. The candidate retains it.
        assert post(client,'save',command(patch(row('independent','N','2026-10-16','B',1)))).status_code==200
        proposal=viewed['proposal']|{'operation_id':str(uuid4()),'pending':True}
        saved=post(client,'save',proposal)
        assert saved.status_code==200,saved.text
        current=client.get('/auto_plan/api/plans/current').json()
        assert {(r['model_code'],r['quantity']) for r in current['plan']['segments']}=={('M',1),('N',1)}
        assert current['latest_adoption']==0
        # Relabeling the old source as the latest base cannot bypass conflicts.
        changed=post(client,'preview',viewed['proposal']|{'base_revision':current['revision']})
        assert changed.status_code==409
    finally: controller.shutdown()


def fixture_factory(mode,path,processes):
    import sys
    from auto_plan.job_process import ComputeProcess
    class FixtureProcess(ComputeProcess):
        command=[sys.executable,str(__import__('pathlib').Path(__file__).with_name('job_worker_fixture.py')),mode,str(path)]
    def create(frozen,threads):
        process=FixtureProcess(frozen,threads);processes.append(process)
        return process
    return create


def test_malformed_worker_frame_preserves_only_prior_complete_candidate(shared_client,tmp_path):
    from tests.test_solver import setup
    client,plans,inputs=shared_client
    setup(inputs,[{'model_code':'M','week':'2026-10-12','quantity':1}])
    processes=[]
    controller=install_controller(client,plans,process_factory=fixture_factory('partial',tmp_path/'owned.json',processes))
    try:
        identity=client.post('/auto_plan/api/jobs',json=submission()).json()['job_id']
        job=wait_job(client,identity)
        assert job['state']=='failed'
        assert len(job['candidates'])==1
        reference=job['candidates'][0]
        viewed=client.get(f"/auto_plan/api/jobs/{identity}/candidates/{reference['candidate_id']}")
        assert sum(r['quantity'] for r in viewed.json()['candidate']['plan']['segments'])==1
    finally: controller.shutdown()


@pytest.mark.parametrize('with_candidate',[True,False])
def test_stop_force_kills_owned_group_at_ten_seconds_and_retains_candidate(shared_client,tmp_path,monkeypatch,with_candidate):
    import json,time,os
    from tests.test_solver import setup
    client,plans,inputs=shared_client
    setup(inputs,[{'model_code':'M','week':'2026-10-12','quantity':1}])
    monkeypatch.setenv('T37_PARENT_SECRET','never-pass-to-compute')
    processes=[];evidence=tmp_path/'owned.json'
    controller=install_controller(client,plans,process_factory=fixture_factory('stall' if with_candidate else 'empty_stall',evidence,processes))
    try:
        identity=client.post('/auto_plan/api/jobs',json=submission()).json()['job_id']
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            job=client.get('/auto_plan/api/jobs/'+identity).json()
            if job['candidates'] or not with_candidate and evidence.exists(): break
            time.sleep(.05)
        assert len(job['candidates'])==(1 if with_candidate else 0)
        started=time.monotonic()
        assert client.post('/auto_plan/api/jobs/'+identity+'/stop').status_code==200
        job=wait_job(client,identity,timeout=13)
        elapsed=time.monotonic()-started
        assert 9.5<=elapsed<=12.5
        assert job['state']=='interrupted' and len(job['candidates'])==(1 if with_candidate else 0)
        assert job['result']['reason']=='stop_deadline'
        record=json.loads(evidence.read_text())
        (tmp_path/'stop-measurement.json').write_text(json.dumps({'stop_seconds':elapsed,'limit_seconds':10,'owned_pid':record['pid'],'descendant_pid':record['descendant_pid']}))
        assert record['secret_free'] is True and record['oracle_imported'] is False
        for pid in [record['pid'],record['descendant_pid']]:
            try: os.kill(pid,0)
            except ProcessLookupError: pass
            else: raise AssertionError('Owned process survived forced stop')
    finally: controller.shutdown()


def test_actual_server_sigterm_closes_admission_and_reaps_owned_children_within_thirty_seconds(tmp_path):
    import json,os,signal,socket,subprocess,sys,time
    from pathlib import Path
    import httpx
    from auto_plan.solver_process import compute_environment
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    evidence=tmp_path/'server-owned.json'
    environment=compute_environment(1)
    environment['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')+os.pathsep+str(Path(__file__).resolve().parents[1])
    output=tmp_path/'server.log'
    with output.open('wb') as log:
        process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('jobs_shutdown_server.py')),str(port),str(evidence)],env=environment,stdout=log,stderr=log,start_new_session=True)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'Origin':'https://testserver','X-Auto-Plan-Request':'1'}) as client:
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    try:
                        if client.get('/auto_plan/api/health').status_code==200: break
                    except httpx.ConnectError: pass
                    if process.poll() is not None: raise AssertionError(output.read_text())
                    time.sleep(.05)
                login=client.post('/auto_plan/api/auth/login',json={'username':'EDITOR','password':'company-secret'})
                assert login.status_code==200,login.text
                client.headers['Cookie']='auto_plan_session='+login.cookies['auto_plan_session']
                accepted=client.post('/auto_plan/api/jobs',json=submission())
                assert accepted.status_code==200,accepted.text
                identity=accepted.json()['job_id']
                while time.monotonic()<deadline:
                    job=client.get('/auto_plan/api/jobs/'+identity).json()
                    if job['candidates']: break
                    time.sleep(.05)
                assert job['candidates']
                started=time.monotonic();process.send_signal(signal.SIGTERM)
                process.wait(timeout=29)
                shutdown_seconds=time.monotonic()-started
                (tmp_path/'shutdown-measurement.json').write_text(json.dumps({'sigterm_seconds':shutdown_seconds,'limit_seconds':30}))
                assert shutdown_seconds<30
                assert process.returncode in {0,-signal.SIGTERM}
            record=json.loads(evidence.read_text())
            for pid in [record['pid'],record['descendant_pid']]:
                try: os.kill(pid,0)
                except ProcessLookupError: pass
                else: raise AssertionError('Owned child survived SIGTERM shutdown')
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL);process.wait()


def test_partial_order_exchange_gets_fresh_certificate_instead_of_full_candidate_badge(shared_client):
    from auto_plan.plan_models import Plan
    from auto_plan.plans import PlanningContext
    from tests.test_solver import setup
    from tests.test_plan_transactions import row
    from tests.job_http_support import calculate
    client,plans,inputs=shared_client
    setup(inputs,[{'model_code':model,'week':'2026-10-12','quantity':1} for model in ('M','N')])
    original=Plan(segments=[row('n','N',quantity=1)|{'hours':'1'},row('m','M',quantity=1)|{'order':1,'hours':'2'}])
    plans.plan=original;plans.revisions[0]['snapshot']['plan']=original.model_dump(mode='json')
    plans.adoption=1
    plans.adoptions[1]={'snapshot':{'plan':original.model_copy(update={'segments':[r.model_copy(update={'order':1-r.order}) for r in original.segments]}).model_dump(mode='json')},
        'coverage':[{'start_date':'2026-10-12','end_date':'2027-01-24','line_codes':['L'],'model_codes':['M','N'],'blanks_are_zero':True}]}
    plans.context=PlanningContext(locks=[{'date':'2026-10-15','line_code':'L','model_code':model,'quantity':1} for model in ('M','N')])
    controller=install_controller(client,plans)
    try:
        body=submission(latest_adoption=1)
        result=calculate(client,{k:v for k,v in body.items() if k!='submission_id'})
        assert result.status_code==200,result.text
        candidate=result.json()['candidates'][0]
        assert candidate['evaluation']['executable'] is True
        assert candidate['objective'][32]=='0'
        proposal=candidate['server_reference']
        partial=proposal|{'operations':[proposal['operations'][0]|{'selection':['segments:m']}]}
        response=client.post('/auto_plan/api/plans/preview',json=partial)
        assert response.status_code==200,response.text
        assert response.json()['evaluation']['stability']['order_change_count']==1
        assert plans.revision==0 and plans.adoption==1
        whole=client.post('/auto_plan/api/plans/preview',json=proposal)
        assert whole.status_code==200,whole.text
        assert whole.json()['evaluation']['executable'] is True
    finally: controller.shutdown()


def test_capacity_invalid_half_of_rebalance_is_rejected_before_shared_save(shared_client):
    from auto_plan.plan_models import Plan
    from tests.test_solver import setup
    from tests.test_plan_transactions import row
    from tests.job_http_support import calculate
    client,plans,inputs=shared_client
    setup(inputs,[{'model_code':'M','week':'2026-10-12','quantity':1},{'model_code':'N','week':'2026-10-12','quantity':10}],capacity=11)
    plans.plan=Plan(segments=[row('m',quantity=10)|{'hours':'20'},row('n','N',quantity=1)|{'order':1,'hours':'4'}])
    plans.revisions[0]['snapshot']['plan']=plans.plan.model_dump(mode='json')
    controller=install_controller(client,plans)
    try:
        body=submission()
        result=calculate(client,{k:v for k,v in body.items() if k!='submission_id'})
        assert result.status_code==200,result.text
        candidate=result.json()['candidates'][0]
        assert sum(r['quantity'] for r in candidate['plan']['segments'] if r['model_code']=='M')==1
        assert sum(r['quantity'] for r in candidate['plan']['segments'] if r['model_code']=='N')==10
        # Accepting N's increase without M's decrease asks for20 on capacity11.
        chosen=['segments:'+r['id'] for r in candidate['operation']['segments'] if r['model_code']=='N']
        assert chosen
        proposal=candidate['server_reference']
        response=client.post('/auto_plan/api/plans/preview',json=proposal|{'operations':[proposal['operations'][0]|{'selection':chosen}]})
        assert response.status_code==422,response.text
        assert plans.revision==plans.adoption==0
    finally: controller.shutdown()
