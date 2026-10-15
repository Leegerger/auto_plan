"""Startup failures observed through the public service, with explicit DB adapters."""
from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from tests.support import MemoryAccessStore, CompanyAuthentication


def test_failed_startup_keeps_safe_reason_visible_and_refuses_every_business_route(tmp_path):
    class BrokenPlans:
        def check_consistency(self):
            raise ValueError('SECRET_SENTINEL dsn=private.example')
    (tmp_path/'index.html').write_text('<html><!--AUTO_PLAN_CONFIG--><div id="app"></div></html>')
    settings = Settings(frontend_dist=tmp_path)
    with TestClient(create_app(settings, store=MemoryAccessStore(), plan_store=BrokenPlans(), authenticator=CompanyAuthentication()), base_url=settings.public_origin) as client:
        readiness = client.get('/auto_plan/api/ready')
        assert readiness.status_code == 503
        assert readiness.json()['reason_code'] == 'plan_integrity'
        assert 'SECRET_SENTINEL' not in readiness.text
        assert client.get('/auto_plan/api/health').json()['status'] == 'alive'
        assert client.get('/auto_plan/').status_code == 200
        for method,path in [('GET','/inputs'),('GET','/plans/current'),('POST','/inputs/apply'),('POST','/jobs'),('POST','/plans/verify')]:
            response=client.request(method,'/auto_plan/api'+path,json={},headers={'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
            assert response.status_code == 503
            assert response.json()['reason_code'] == 'plan_integrity'


def test_failed_session_recovery_is_sanitized_and_never_opens_admission():
    class BrokenAccess(MemoryAccessStore):
        def startup(self): raise RuntimeError('SECRET_SENTINEL')
    settings=Settings()
    with TestClient(create_app(settings,store=BrokenAccess(),authenticator=CompanyAuthentication()),base_url=settings.public_origin) as client:
        response=client.get('/auto_plan/api/ready')
        assert response.status_code == 503
        assert response.json()['reason_code'] == 'session_recovery'
        assert 'SECRET_SENTINEL' not in response.text
        response=client.post('/auto_plan/api/auth/login',json={'username':'admin','password':'company-secret'},headers={'Origin':settings.public_origin,'X-Auto-Plan-Request':'1'})
        assert response.status_code == 503


def test_good_startup_opens_readiness_after_session_recovery():
    settings=Settings()
    with TestClient(create_app(settings,store=MemoryAccessStore(),authenticator=CompanyAuthentication()),base_url=settings.public_origin) as client:
        assert client.get('/auto_plan/api/ready').json()['status']=='ready'


def test_entrypoint_configuration_error_never_prints_validation_inputs():
    import os, subprocess, sys
    environment=os.environ.copy()
    environment.update(AUTO_PLAN_ORACLE_USER='PLAN_PROD',AUTO_PLAN_TEST_AUTH_ENABLED='true',AUTO_PLAN_TEST_AUTH_PASSWORD='TEST_FAKE_SENTINEL',AUTO_PLAN_ORACLE_PASSWORD='ORACLE_FAKE_SENTINEL')
    result=subprocess.run([sys.executable,'-m','auto_plan.serve'],env=environment,capture_output=True,text=True,timeout=10)
    assert result.returncode==2
    assert 'configuration invalid' in result.stdout
    assert 'FAKE_SENTINEL' not in result.stdout+result.stderr
    assert 'ValidationError' not in result.stdout+result.stderr


def test_sigterm_deadline_survives_a_blocked_external_cleanup(tmp_path):
    import os, signal, socket, subprocess, sys, time, urllib.request
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','tests.deadline_server',str(port),str(tmp_path/'cleanup')],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/auto_plan/api/ready',timeout=.2) as response:
                    if response.status==200: break
            except OSError: time.sleep(.05)
        else: raise AssertionError('HTTP server did not become ready')
        started=time.monotonic();os.kill(process.pid,signal.SIGTERM)
        stdout,stderr=process.communicate(timeout=32)
        elapsed=time.monotonic()-started
        assert (tmp_path/'cleanup').exists()
        assert elapsed<31
        assert process.returncode==0
        assert 'shutdown deadline guard' in stdout
    finally:
        if process.poll() is None: process.kill();process.wait()
