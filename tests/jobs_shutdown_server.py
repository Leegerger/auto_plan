"""Disposable public app with owned stalled children for actual SIGTERM evidence."""
from datetime import datetime,timezone
from pathlib import Path
import sys
import uvicorn
from auto_plan.app import create_app
from auto_plan.settings import Settings
from auto_plan.job_process import ComputeProcess,JobController
from auto_plan.input_models import InputSnapshot
from tests.support import MemoryAccessStore,CompanyAuthentication
from tests.input_support import MemoryInputStore
from tests.plan_support import MemoryPlanStore
from tests.jobs_support import MemoryJobStore
from tests.test_plan_preview import snapshot

port,evidence=sys.argv[1:3]
inputs=MemoryInputStore()
data=InputSnapshot.model_validate(snapshot()['inputs']).model_dump(mode='json');data['business']['demands']=[{'model_code':'M','week':'2026-10-12','quantity':1}]
inputs.versions[1]=InputSnapshot.model_validate(data|{'input_version':1});inputs.version=1
plans=MemoryPlanStore(inputs);access=MemoryAccessStore()
access.users['EDITOR']={'user_id':'EDITOR','roles':['editor'],'active':True}
clock=lambda:datetime(2026,10,14,tzinfo=timezone.utc)
class StalledProcess(ComputeProcess):
    command=[sys.executable,str(Path(__file__).with_name('job_worker_fixture.py')),'stall',evidence]
jobs=MemoryJobStore(plans,access)
controller=JobController(jobs,workers=1,clock=clock,process_factory=StalledProcess)
app=create_app(Settings(public_origin='https://testserver'),store=access,input_store=inputs,plan_store=plans,
    job_store=jobs,job_controller=controller,authenticator=CompanyAuthentication(),clock=clock)
class Server(uvicorn.Server):
    def handle_exit(self,sig,frame):
        controller.close_admission()
        super().handle_exit(sig,frame)
Server(uvicorn.Config(app,host='127.0.0.1',port=int(port),log_level='error',timeout_graceful_shutdown=20)).run()
