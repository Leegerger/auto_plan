"""Company API boundary tested over local HTTPS, without company credentials."""
import json
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import certifi
import pytest
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.company_auth import CompanyAuthenticator
from auto_plan.settings import Settings
from tests.support import MemoryAccessStore


@pytest.fixture
def company_https(tmp_path):
    cert = tmp_path / 'cert.pem'
    key = tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    observed = {'requests': [], 'sni': []}
    class CompanyHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed['requests'].append(json.loads(self.rfile.read(int(self.headers['content-length']))))
            if self.path == '/delay':
                time.sleep(16)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), CompanyHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.set_servername_callback(lambda socket, name, context: observed['sni'].append(name))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, cert, observed
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def company_client(port, ca_file, host='localhost', path='/login'):
    settings = Settings(initial_admin_id='ADMIN', auth_url=f'https://{host}:{port}{path}', auth_ca_file=str(ca_file))
    authentication = CompanyAuthenticator(settings)
    application = create_app(settings, store=MemoryAccessStore(), authenticator=authentication)
    return settings, authentication, application


def test_company_https_keeps_sni_strict_ca_and_bypasses_external_proxies(company_https, monkeypatch):
    port, certificate, observed = company_https
    for name in ['HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'https_proxy', 'http_proxy', 'all_proxy']:
        monkeypatch.setenv(name, 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    monkeypatch.setenv('no_proxy', '')
    settings, authentication, application = company_client(port, certificate)
    with TestClient(application, base_url=settings.public_origin) as client:
        response = client.post('/auto_plan/api/auth/login', json={'username': ' admin ', 'password': 'synthetic-password'}, headers={'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
        assert response.status_code == 200
        assert observed['requests'] == [{'UserID': 'admin', 'Password': 'synthetic-password', 'UserType': 'Auto', 'SysID': 'FS_MOD-SCM_PORTAL'}]
        assert observed['sni'] == ['localhost']
    authentication.close()


@pytest.mark.parametrize('wrong_hostname', [False, True])
def test_company_https_rejects_untrusted_ca_and_wrong_hostname(company_https, wrong_hostname):
    port, certificate, observed = company_https
    settings, authentication, application = company_client(port, certificate if wrong_hostname else certifi.where(), host='127.0.0.1' if wrong_hostname else 'localhost')
    with TestClient(application, base_url=settings.public_origin) as client:
        response = client.post('/auto_plan/api/auth/login', json={'username': 'admin', 'password': 'synthetic-password'}, headers={'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
        assert response.status_code == 503
        assert observed['requests'] == []
        assert client.get('/auto_plan/api/auth/me').status_code == 401
    authentication.close()


def test_company_https_times_out_at_fifteen_seconds_without_retry(company_https):
    port, certificate, observed = company_https
    settings, authentication, application = company_client(port, certificate, path='/delay')
    with TestClient(application, base_url=settings.public_origin) as client:
        started = time.monotonic()
        response = client.post('/auto_plan/api/auth/login', json={'username': 'admin', 'password': 'synthetic-password'}, headers={'Origin': settings.public_origin, 'X-Auto-Plan-Request': '1'})
        elapsed = time.monotonic() - started
        assert response.status_code == 503
        assert 14 <= elapsed < 16
        assert len(observed['requests']) == 1
    authentication.close()
