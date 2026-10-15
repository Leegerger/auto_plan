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
        app = create_app(settings, store=access, input_store=inputs, authenticator=CompanyAuthentication(), **options)
        uvicorn.run(app, host='127.0.0.1', port=8774, ssl_keyfile=str(key), ssl_certfile=str(cert), access_log=False)
