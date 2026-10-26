"""Protected native-source evidence survives durable PLAN_TEST jobs and replay."""
import os
from datetime import datetime
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from auto_plan.oracle_access import OracleAccessStore
from auto_plan.oracle_inputs import OracleInputStore
from auto_plan.oracle_plans import OraclePlanStore
from auto_plan.oracle_jobs import OracleJobStore
from auto_plan.job_process import JobController
from auto_plan.jobs import SubmitJob
from auto_plan.solver_models import FrozenSolveInput
from tests.oracle_namespace import disposable_business_namespace
from tests.test_initialization import initial_payload
from tests.test_unfinished_switches import unfinished, residual
from tests.test_execution_reset import head, current_command
from tests.test_plan_transactions import post
from tests.test_switch_continuity import confirmation
from tests.job_http_support import calculate
from tests.test_jobs import wait_job
from tests.support import CompanyAuthentication

pytestmark = [pytest.mark.oracle, pytest.mark.skipif(os.getenv('AUTO_PLAN_TEST_ORACLE') != '1', reason='Opt-in disposable PLAN_TEST objects only')]


def test_real_native_candidate_freezes_latent_progress_review_and_replays_after_reopen():
    settings = Settings(initial_admin_id='T24ADMIN')
    assert settings.oracle_user.upper() == 'PLAN_TEST'
    access = OracleAccessStore.connect(settings)
    try:
        with disposable_business_namespace(access.pool, 'AP_TEST_' + uuid4().hex[:6].upper()) as namespace:
            inputs, plans, jobs = OracleInputStore(namespace), OraclePlanStore(namespace), OracleJobStore(namespace,worker_limit=1)
            now = [datetime.fromisoformat('2026-10-14T10:00:00+08:00')]
            controller = JobController(jobs, workers=1, clock=lambda:now[0])
            def application(plan_store, job_store, worker=None):
                return create_app(settings, store=access, input_store=inputs, plan_store=plan_store, job_store=job_store,
                    job_controller=worker, authenticator=CompanyAuthentication(), clock=lambda:now[0])
            def login(client):
                client.headers.update({'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
                assert client.post('/auto_plan/api/auth/login',json={'username':'T24ADMIN','password':'company-secret'}).status_code == 200
                assert client.put('/auto_plan/api/admin/authorizations/T33EDITOR',json={'roles':['editor']}).status_code == 200
                assert client.post('/auto_plan/api/auth/login',json={'username':'T33EDITOR','password':'company-secret'}).status_code == 200
            with TestClient(application(plans,jobs,controller), base_url=settings.public_origin) as client:
                login(client)
                initial = initial_payload()
                initial['segments'] = []
                for row in initial['changes']['capacities']: row['pieces'] = 1000
                for row in initial['demands']: row.update(model_code='P',quantity=10 if row['week']=='2026-10-12' else 0)
                initial['reset']['models'] = [{'model_code':'P','wip':0,'net_balance':0}]
                for row in initial['coverage'] + initial['execution']['coverage']: row['model_codes'] = ['P']
                written = client.post('/auto_plan/api/initialization/apply',json=initial|{'operation_id':str(uuid4())})
                assert written.status_code == 200, written.text
                progress = unfinished()
                progress['rows'][0]['effective_at'] = '2026-10-14T08:30:00+08:00'
                assert post(client,'save',current_command(client,progress)).status_code == 200
                record = head(client)['context']['line_states']['records'][0]
                assert post(client,'save',current_command(client,{'kind':'patch_plan','switches':[residual(record['id'],day='2026-10-14')]})).status_code == 200
                now[0] = datetime.fromisoformat('2026-10-15T10:00:00+08:00')
                login(client)
                source = client.get('/auto_plan/api/jobs/source').json()
                assert len(source['snapshot']['state_reviews']) == 1
                frozen_history = client.get(f"/auto_plan/api/plans/revisions/{source['source_revision']}").json()
                result = calculate(client, {'base_revision':source['source_revision'],'input_version':source['input_version'],'latest_adoption':source['latest_adoption'],
                    'request':{'scope':{'start_date':'2026-10-14','end_date':'2026-10-16','line_codes':['L']},'time_limit_seconds':10}})
                assert result.status_code == 200, result.text
                candidate = result.json()['candidates'][0]
                assert candidate['snapshot'] == source['snapshot']
                assert {row['code'] for row in candidate['evaluation']['adoption_conditions']} == {'line_state_review','remaining_switch_continuity'}
                # Seed the external durable adapter with an older frozen body.
                # All verification and the actual rerun still use public HTTP.
                legacy_snapshot = source['snapshot'] | {}
                legacy_snapshot.pop('state_reviews')
                legacy = FrozenSolveInput(snapshot=legacy_snapshot,source_revision=source['source_revision'],
                    input_version=source['input_version'],latest_adoption=source['latest_adoption'],request=candidate['request'])
                seed = SubmitJob(submission_id=uuid4(),base_revision=source['source_revision'],input_version=source['input_version'],
                    latest_adoption=source['latest_adoption'],request=legacy.request)
                archived = jobs.submit(seed,'T33EDITOR',now[0],lambda unit:legacy)
                finished = wait_job(client,archived['job_id'],timeout=30)
                assert finished['state'] == 'completed' and finished['candidates'], finished
                repeated = client.post(f"/auto_plan/api/jobs/{archived['job_id']}/rerun",json={'submission_id':str(uuid4())})
                assert repeated.status_code == 200, repeated.text
                finished = wait_job(client,repeated.json()['job_id'],timeout=30)
                assert finished['state'] == 'completed' and finished['candidates'], finished
                old_ref = finished['candidates'][0]
                old_view = client.get(f"/auto_plan/api/jobs/{finished['job_id']}/candidates/{old_ref['candidate_id']}")
                assert old_view.status_code == 200, old_view.text
                assert old_view.json()['candidate']['snapshot']['state_reviews'] == []
                assert old_view.json()['candidate']['snapshot']['plan'] == source['snapshot']['plan']
                assert {row['code'] for row in old_view.json()['candidate']['evaluation']['adoption_conditions']} == {'line_state_review','remaining_switch_continuity'}
                proposal = candidate['server_reference']
                operation = proposal['operations'][0]
                candidate_path = f"/auto_plan/api/jobs/{operation['job_id']}/candidates/{operation['candidate_id']}"
                immutable = client.get(candidate_path).json()['candidate']
                checked = post(client,'preview',proposal)
                assert checked.status_code == 200, checked.text
                assert checked.json()['evaluation']['executable'] is False
                command = proposal | {'operation_id':str(uuid4()),'pending':True}
                saved = post(client,'save',command)
                assert saved.status_code == 200, saved.text
                assert post(client,'save',command).json() == saved.json()
                dto = head(client)['workspace_view']['switch_continuity'][0]
                reconfirmed = post(client,'save',current_command(client,confirmation(dto)))
                assert reconfirmed.status_code == 200, reconfirmed.text
                assert {row['code'] for row in head(client)['evaluation']['adoption_conditions']} == {'line_state_review'}
                assert post(client,'adopt',current_command(client)).status_code == 422
                assert client.get(f"/auto_plan/api/plans/revisions/{source['source_revision']}").json() == frozen_history
            controller.shutdown()
            with TestClient(application(OraclePlanStore(namespace),OracleJobStore(namespace)),base_url=settings.public_origin) as client:
                login(client)
                assert client.get(candidate_path).json()['candidate'] == immutable
                assert head(client)['context']['line_states']['records'][0] == record
                assert {row['code'] for row in head(client)['evaluation']['adoption_conditions']} == {'line_state_review'}
                assert plans.check_consistency()
    finally:
        access.close()
