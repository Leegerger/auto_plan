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
        app = create_app(settings, store=MemoryAccessStore(), input_store=MemoryInputStore(), authenticator=CompanyAuthentication())
        uvicorn.run(app, host='127.0.0.1', port=8774, ssl_keyfile=str(key), ssl_certfile=str(cert), access_log=False)
