"""Browser-only external clock adapter, with ordinary public business APIs."""
from datetime import datetime
import os
from pathlib import Path
import subprocess
import tempfile

import uvicorn
from auto_plan.app import create_app
from auto_plan.input_models import InputSnapshot
from auto_plan.settings import Settings
from tests.input_support import MemoryInputStore
from tests.plan_support import MemoryPlanStore
from tests.support import CompanyAuthentication, MemoryAccessStore
from tests.test_plan_preview import snapshot


if __name__ == '__main__':
    clock_file = Path(os.environ['AUTO_PLAN_BROWSER_CLOCK_FILE'])
    clock = lambda: datetime.fromisoformat(clock_file.read_text().strip())
    port = int(os.environ.get('AUTO_PLAN_BROWSER_PORT', '8816'))
    inputs = MemoryInputStore()
    data = snapshot()['inputs'] | {'input_version': 1}
    data['business'] = {'demands': [
        {'model_code': code, 'week': '2026-10-12', 'quantity': 1000}
        for code in ('M', 'N', 'P')]}
    inputs.versions[1] = InputSnapshot.model_validate(data)
    inputs.version = 1
    plans = MemoryPlanStore(inputs)
    access = MemoryAccessStore()
    access.users['SHARED_EDITOR'] = {
        'user_id': 'SHARED_EDITOR', 'roles': ['editor'], 'active': True}
    settings = Settings(public_origin=f'https://localhost:{port}', base_path='/planning',
                        frontend_dist=Path('frontend/dist'))
    app = create_app(settings, store=access, input_store=inputs, plan_store=plans,
                     authenticator=CompanyAuthentication(), clock=clock)
    with tempfile.TemporaryDirectory(prefix='auto-plan-clock-browser-') as directory:
        cert, key = (Path(directory) / name for name in ('cert.pem', 'key.pem'))
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
            '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost'],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        uvicorn.run(app, host='127.0.0.1', port=port,
                    ssl_keyfile=str(key), ssl_certfile=str(cert), access_log=False)
