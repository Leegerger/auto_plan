"""Real PLAN_TEST public job admission, FIFO and recovery evidence."""
import ast
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
import oracledb
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_jobs import OracleJobStore
from tests.oracle_namespace import disposable_business_namespace, NamespaceCursor
from tests.support import CompanyAuthentication

pytestmark=[pytest.mark.oracle,pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE')!='1',reason='Opt-in PLAN_TEST AP_* only')]
NOW=datetime(2026,10,14,tzinfo=timezone.utc)


@pytest.fixture
def durable_jobs():
    settings=Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper()=='PLAN_TEST'
    access=OracleAccessStore.connect(settings)
    with disposable_business_namespace(access.pool,'AP_TEST_'+uuid4().hex[:6].upper()) as pool:
        jobs=OracleJobStore(pool,queue_capacity=3,worker_limit=2)
        plans=OraclePlanStore(pool)
        app=create_app(settings,store=access,plan_store=plans,job_store=jobs,input_store=OracleInputStore(pool),authenticator=CompanyAuthentication(),clock=lambda:NOW)
        access.job_revocation=lambda cursor,actor,now:jobs.revoke_in_transaction(NamespaceCursor(cursor,pool),actor,now)
        with TestClient(app,base_url=settings.public_origin) as client:
            client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
            assert client.post('/auto_plan/api/auth/login',json={'username':'T24ADMIN','password':'company-secret'}).status_code==200
            for account in ['T37A','T37B','T37VIEW','T37OTHER']:
                assert client.put('/auto_plan/api/admin/authorizations/'+account,json={'roles':['viewer'] if account=='T37VIEW' else ['editor']}).status_code==200
            client.post('/auto_plan/api/auth/login',json={'username':'T37A','password':'company-secret'})
            yield client,jobs,access,plans
    access.close()


def payload(client):
    head=client.get('/auto_plan/api/plans/current').json()
    return {'submission_id':str(uuid4()),'base_revision':head['revision'],'input_version':head['input_version'],'latest_adoption':head['latest_adoption'],
        'request':{'scope':{'start_date':'2026-10-15','end_date':'2026-10-15','line_codes':['L']},'time_limit_seconds':1}}


def login(client,actor):
    assert client.post('/auto_plan/api/auth/login',json={'username':actor,'password':'company-secret'}).status_code==200


def test_fifo_per_account_and_acknowledgement_void_survive_reopen(durable_jobs):
    client,jobs,access,plans=durable_jobs
    jobs.recover_startup(NOW)
    first=payload(client)
    accepted=client.post('/auto_plan/api/jobs',json=first)
    assert accepted.status_code==200,accepted.text
    identity=accepted.json()['job_id']
    assert client.post('/auto_plan/api/jobs',json=first).json()==accepted.json()
    claimed=jobs.claim(NOW)
    assert claimed['job_id']==identity
    second=client.post('/auto_plan/api/jobs',json=payload(client)).json()['job_id']
    login(client,'T37B')
    third=client.post('/auto_plan/api/jobs',json=payload(client)).json()['job_id']
    assert jobs.claim(NOW) is None  # Strict FIFO waits for A rather than starting B.
    jobs.finish(identity,claimed['run_id'],claimed['generation'],'completed',{'solve_status':'no_candidate'},[],NOW)
    assert jobs.claim(NOW)['job_id']==second
    assert jobs.claim(NOW)['job_id']==third
    login(client,'T37A')
    assert client.post('/auto_plan/api/jobs/verify',json={'submission_id':first['submission_id']}).json()==accepted.json()
    late=payload(client)
    assert client.post('/auto_plan/api/jobs/verify',json={'submission_id':late['submission_id']}).json()['status']=='ABORTED_VOID'
    assert client.post('/auto_plan/api/jobs',json=late).status_code==409
    assert client.get('/auto_plan/api/plans/current').json()['revision']==0
    jobs.recover_startup(NOW+timedelta(minutes=1))
    assert client.get('/auto_plan/api/jobs/'+second).json()['state']=='interrupted'
    assert jobs.check_consistency() is True


def test_concurrent_account_quota_and_unknown_submission_ack_are_arbitrated(durable_jobs):
    from tests.test_oracle_plans import FaultPool
    client,jobs,access,plans=durable_jobs
    first,second=payload(client),payload(client)
    def send(body): return client.post('/auto_plan/api/jobs',json=body)
    with ThreadPoolExecutor(max_workers=2) as threads:
        responses=list(threads.map(send,[first,second]))
    assert sorted(r.status_code for r in responses)==[200,429]
    accepted=next(r.json() for r in responses if r.status_code==200)
    assert len(client.get('/auto_plan/api/jobs').json()['jobs'])==1
    assert client.post('/auto_plan/api/jobs/'+accepted['job_id']+'/stop').json()['state']=='stopped'
    faults=FaultPool(jobs.pool)
    jobs.pool=faults
    body=payload(client)
    faults.commit_fault='after'
    response=client.post('/auto_plan/api/jobs',json=body)
    assert response.status_code==503 and response.json()['state']=='unknown'
    verified=client.post('/auto_plan/api/jobs/verify',json={'submission_id':body['submission_id']})
    assert verified.status_code==200,verified.text
    assert verified.json()['status']=='ACCEPTED'
    assert len(client.get('/auto_plan/api/jobs').json()['jobs'])==2


def test_logout_preserves_jobs_but_editor_revocation_stops_queued_and_running(durable_jobs):
    client,jobs,access,_=durable_jobs
    jobs.recover_startup(NOW)
    first=client.post('/auto_plan/api/jobs',json=payload(client)).json()['job_id']
    run=jobs.claim(NOW)
    queued=client.post('/auto_plan/api/jobs',json=payload(client)).json()['job_id']
    client.post('/auto_plan/api/auth/logout')
    login(client,'T37VIEW')
    assert client.get('/auto_plan/api/jobs/'+first).json()['state']=='running'
    assert client.post('/auto_plan/api/jobs/'+first+'/stop').status_code==403
    login(client,'T37OTHER')
    assert client.post('/auto_plan/api/jobs/'+first+'/stop').status_code==403
    login(client,'T24ADMIN')
    assert client.get('/auto_plan/api/jobs/'+first).status_code==403
    assert client.put('/auto_plan/api/admin/authorizations/T37A',json={'roles':['viewer']}).status_code==200
    login(client,'T37VIEW')
    assert client.get('/auto_plan/api/jobs/'+queued).json()['state']=='stopped'
    control=jobs.control(first,run['run_id'],run['generation'],NOW)
    assert control['stop_requested_at']==NOW
    assert client.get('/auto_plan/api/jobs/'+first).json()['stop_reason']=='authorization_revoked'
    login(client,'T24ADMIN')
    assert client.post('/auto_plan/api/jobs/'+first+'/stop').status_code==200


def test_complete_candidate_reference_survives_reopen_application_and_expiry(durable_jobs):
    from auto_plan.job_process import JobController
    from tests.test_initialization import initial_payload
    from tests.job_http_support import calculate
    client,jobs,access,plans=durable_jobs
    initial=initial_payload();initial['segments']=[];initial['demands'][0]['quantity']=5
    initial['reset']['models'][0]['net_balance']=0
    response=client.post('/auto_plan/api/initialization/apply',json=initial|{'operation_id':str(uuid4())})
    assert response.status_code==200,response.text
    controller=JobController(jobs,workers=1,clock=client.app.state.clock)
    client.app.state.job_controller=controller
    controller.recover_startup();controller.start()
    try:
        source=payload(client)
        result=calculate(client,{k:v for k,v in source.items() if k!='submission_id'})
        assert result.status_code==200,result.text
        candidate=result.json()['candidates'][0]
        assert sum(row['quantity'] for row in candidate['plan']['segments'])==5
        assert candidate['objective'][:30]==['0']*30
        proposal=candidate['server_reference']
        application=proposal|{'operation_id':str(uuid4()),'pending':False}
        saved=client.post('/auto_plan/api/plans/save',json=application)
        assert saved.status_code==200,saved.text
        assert saved.json()['latest_adoption']==0
        assert client.get('/auto_plan/api/plans/current').json()['plan']==candidate['plan']
        controller.shutdown()
        jobs.recover_startup(NOW+timedelta(days=1))
        reference=proposal['operations'][0]
        url=f"/auto_plan/api/jobs/{result.json()['job_id']}/candidates/{reference['candidate_id']}"
        # Reopen retains the exact complete candidate and original input.
        assert client.get(url).json()['candidate']=={k:v for k,v in candidate.items() if k!='server_reference'}
        assert jobs.cleanup(NOW+timedelta(days=29))==0
        clock=[NOW+timedelta(days=29)]
        client.app.state.clock=lambda:clock[0]
        login(client,'T37A')
        assert client.get(url).status_code==200
        jobs.cleanup(NOW+timedelta(days=31))
        clock[0]=NOW+timedelta(days=45)
        login(client,'T37A')
        assert client.get('/auto_plan/api/jobs/'+result.json()['job_id']).status_code==200
        clock[0]=NOW+timedelta(days=59)
        login(client,'T37A')
        assert client.get(url).status_code==410  # Exact boundary is expired.
        assert jobs.cleanup(clock[0])>=1
        assert client.post('/auto_plan/api/plans/save',json=application).json()==saved.json()
        assert client.get('/auto_plan/api/plans/revisions/'+str(saved.json()['revision'])).json()['plan']==candidate['plan']
        assert plans.check_consistency() and jobs.check_consistency()
        rerun=client.post('/auto_plan/api/jobs/'+result.json()['job_id']+'/rerun',json={'submission_id':str(uuid4())})
        # A stopped controller closes admission. Recovery can later accept the rerun.
        assert rerun.status_code==503
        client.app.state.job_controller=None
        rerun=client.post('/auto_plan/api/jobs/'+result.json()['job_id']+'/rerun',json={'submission_id':str(uuid4())})
        assert rerun.status_code==200,rerun.text
        claimed=jobs.claim(clock[0])
        assert claimed['frozen'].snapshot.time.as_of==NOW
        assert claimed['frozen'].source_revision==proposal['base_revision']
    finally:
        controller.shutdown()
        client.app.state.job_controller=None


def test_queue_full_rolls_back_admission_and_verify_waits_for_inflight_winner(durable_jobs):
    from threading import Event
    from tests.test_oracle_plans import FaultPool
    client,jobs,_,_=durable_jobs
    jobs.queue_capacity=2
    one=client.post('/auto_plan/api/jobs',json=payload(client)).json()
    login(client,'T37B')
    assert client.post('/auto_plan/api/jobs',json=payload(client)).status_code==200
    login(client,'T37OTHER')
    rejected=payload(client)
    assert client.post('/auto_plan/api/jobs',json=rejected).status_code==429
    assert len(client.get('/auto_plan/api/jobs').json()['jobs'])==2
    assert client.post('/auto_plan/api/jobs/verify',json={'submission_id':rejected['submission_id']}).json()['status']=='ABORTED_VOID'
    login(client,'T37A')
    assert client.post('/auto_plan/api/jobs/'+one['job_id']+'/stop').status_code==200
    faults=FaultPool(jobs.pool);jobs.pool=faults
    inserted,release=Event(),Event()
    def pause(statement,values):
        if 'INSERT INTO AP_JOB_KEY' in statement:
            inserted.set()
            assert release.wait(5)
    faults.after_execute=pause
    body=payload(client)
    with ThreadPoolExecutor(max_workers=2) as threads:
        saving=threads.submit(client.post,'/auto_plan/api/jobs',json=body)
        assert inserted.wait(5)
        verifying=threads.submit(client.post,'/auto_plan/api/jobs/verify',json={'submission_id':body['submission_id']})
        assert not verifying.done()
        release.set()
        accepted=saving.result(timeout=10)
        verified=verifying.result(timeout=10)
    assert accepted.status_code==verified.status_code==200
    assert accepted.json()==verified.json()
    assert len(client.get('/auto_plan/api/jobs').json()['jobs'])==3
    faults.after_execute=None
