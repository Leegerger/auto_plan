"""Browser verification server. Explicitly inject test external boundaries only."""
import os
import subprocess
import tempfile
from pathlib import Path
import uvicorn
from auto_plan.app import create_app
from auto_plan.settings import Settings
from tests.support import CompanyAuthentication, MemoryAccessStore
from tests.input_support import MemoryInputStore

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='auto-plan-browser-') as directory:
        cert = Path(directory) / 'cert.pem'
        key = Path(directory) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        settings = Settings(initial_admin_id='ADMIN', public_origin='https://localhost:8774', base_path='/planning', frontend_dist=Path('frontend/dist'))
        access = MemoryAccessStore()
        inputs = MemoryInputStore()
        options = {}
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
        app = create_app(settings, store=access, input_store=inputs, authenticator=CompanyAuthentication(), **options)
        uvicorn.run(app, host='127.0.0.1', port=8774, ssl_keyfile=str(key), ssl_certfile=str(cert), access_log=False)
