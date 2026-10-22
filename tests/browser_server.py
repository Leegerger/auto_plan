"""Browser verification server. Explicitly inject test external boundaries only."""
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import uvicorn
from auto_plan.app import create_app
from auto_plan.settings import Settings
from tests.support import CompanyAuthentication, MemoryAccessStore
from tests.input_support import MemoryInputStore
from tests.plan_support import MemoryPlanStore
from tests.demand_support import MemoryDemandStore

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='auto-plan-browser-') as directory:
        cert = Path(directory) / 'cert.pem'
        key = Path(directory) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port=int(os.getenv('AUTO_PLAN_BROWSER_PORT','8774'))
        settings = Settings(initial_admin_id='ADMIN', public_origin=f'https://localhost:{port}', base_path='/planning', frontend_dist=Path('frontend/dist'))
        access = MemoryAccessStore()
        inputs = MemoryInputStore()
        # Browser business fixtures use October 2026 dates, independent of wall time.
        default_plans = MemoryPlanStore(inputs)
        options = {'clock': lambda: datetime(2026, 10, 14, tzinfo=timezone.utc), 'plan_store': default_plans, 'demand_store': MemoryDemandStore(default_plans)}
        if os.getenv('AUTO_PLAN_BROWSER_OUTPUT_FIXTURE') == '1':
            from datetime import datetime, timezone
            from auto_plan.input_models import InputSnapshot
            from tests.test_output_preview import output_snapshot
            data = output_snapshot(1072, '2026-10-15')
            data['inputs']['business']['demands'] = [
                {'model_code': 'M', 'week': '2026-10-12', 'quantity': 600},
                {'model_code': 'M', 'week': '2026-10-19', 'quantity': 400}]
            inputs.versions[0] = InputSnapshot.model_validate(data['inputs'])
            access.users['OUTPUT_EDITOR'] = {'user_id': 'OUTPUT_EDITOR', 'roles': ['editor'], 'active': True}
            options['clock'] = lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
        if os.getenv('AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE') == '1':
            from datetime import datetime, timezone
            from auto_plan.input_models import InputSnapshot
            from auto_plan.plans import PlanService, SubmitPlan
            from tests.plan_support import MemoryPlanStore
            from tests.test_plan_preview import snapshot
            from uuid import uuid4
            inputs.versions[1] = InputSnapshot.model_validate(snapshot()['inputs'] | {'input_version':1})
            inputs.version = 1
            plans = MemoryPlanStore(inputs)
            plan = snapshot()['plan']
            for row in plan['segments']: row['date']='2026-10-15'
            now = datetime(2026,10,14,tzinfo=timezone.utc)
            PlanService(plans).submit(SubmitPlan(base_revision=0,input_version=1,latest_adoption=0,
                operations=[{'kind':'replace_plan','plan':plan}],operation_id=uuid4(),pending=True),'SHARED_EDITOR',now)
            options['plan_store'] = plans
            options['clock'] = lambda: now
            access.users['SHARED_EDITOR']={'user_id':'SHARED_EDITOR','roles':['editor'],'active':True}
            access.users['SECOND_EDITOR']={'user_id':'SECOND_EDITOR','roles':['editor'],'active':True}
            access.users['SHARED_VIEWER']={'user_id':'SHARED_VIEWER','roles':['viewer'],'active':True}
        if os.getenv('AUTO_PLAN_BROWSER_INITIALIZATION_FIXTURE') == '1':
            from datetime import datetime, timezone
            from tests.plan_support import MemoryPlanStore
            from tests.demand_support import MemoryDemandStore
            plans=MemoryPlanStore(inputs)
            options['plan_store']=plans
            options['demand_store']=MemoryDemandStore(plans)
            options['clock']=lambda: datetime(2026,10,14,tzinfo=timezone.utc)
            access.users['IMPORT_EDITOR']={'user_id':'IMPORT_EDITOR','roles':['editor'],'active':True}
        if os.getenv('AUTO_PLAN_BROWSER_RESET_FIXTURE') == '1':
            from datetime import date, datetime, timezone
            from uuid import uuid4
            from auto_plan.input_models import InputSnapshot
            from auto_plan.plan_models import ExecutionCoverage, ExecutionContext
            from auto_plan.plans import PlanService, SubmitPlan, PlanningContext
            from tests.plan_support import MemoryPlanStore
            from tests.test_plan_preview import snapshot
            inputs.versions[1]=InputSnapshot.model_validate(snapshot()['inputs']|{'input_version':1});inputs.version=1
            plans=MemoryPlanStore(inputs)
            coverage=ExecutionCoverage(start_date='2026-10-12',end_date='2026-10-18',line_codes=['L'],model_codes=['M','N','P'],blanks_are_zero=True)
            now=datetime(2026,10,14,tzinfo=timezone.utc)
            plans.context=PlanningContext(coverage=[coverage],execution=ExecutionContext(coverage=[coverage.model_copy(update={'end_date':date(2026,10,14)})],cutoff=now))
            rows=[{'id':identity,'date':day,'line_code':'L','model_code':'M','order':0,'quantity':1000,'hours':'24'} for identity,day in [('early','2026-10-15'),('late','2026-10-16')]]
            PlanService(plans).submit(SubmitPlan(base_revision=0,input_version=1,latest_adoption=0,operation_id=uuid4(),operations=[{'kind':'replace_plan','plan':{'segments':rows}}]),'RESET_EDITOR',now,'adopt')
            options['plan_store']=plans;options['clock']=lambda:now
            access.users['RESET_EDITOR']={'user_id':'RESET_EDITOR','roles':['editor'],'active':True}
        if 'plan_store' in options:
            from tests.jobs_support import MemoryJobStore
            from auto_plan.job_process import JobController
            jobs=MemoryJobStore(options['plan_store'],access)
            options['job_store']=jobs
            options['job_controller']=JobController(jobs,workers=1,clock=options['clock'])
        app = create_app(settings, store=access, input_store=inputs, authenticator=CompanyAuthentication(), **options)
        uvicorn.run(app, host='127.0.0.1', port=port, ssl_keyfile=str(key), ssl_certfile=str(cert), access_log=False)
