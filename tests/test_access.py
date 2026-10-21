from fastapi.testclient import TestClient
from auto_plan.app import create_app
from auto_plan.settings import Settings
from tests.support import MemoryAccessStore, CompanyAuthentication


def test_company_login_bootstraps_configured_administrator_and_secure_session():
    settings = Settings(initial_admin_id=" Admin ")
    store = MemoryAccessStore()
    authentication = CompanyAuthentication()
    with TestClient(create_app(settings, store=store, authenticator=authentication), base_url=settings.public_origin) as client:
        response = client.post("/auto_plan/api/auth/login", json={"username": " admin ", "password": "company-secret"}, headers={"Origin": settings.public_origin, "X-Auto-Plan-Request": "1"})
        assert response.status_code == 200
        assert response.json() == {"user_id": "ADMIN", "roles": ["administrator"]}
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie and "Path=/auto_plan" in cookie
        assert client.get("/auto_plan/api/auth/me").json() == response.json()
        audit = client.get("/auto_plan/api/admin/audit").json()
        assert audit[0]["action"] == "bootstrap"


def test_malformed_login_never_returns_company_password():
    settings = Settings(initial_admin_id="ADMIN")
    with TestClient(create_app(settings, store=MemoryAccessStore(), authenticator=CompanyAuthentication()), base_url=settings.public_origin) as client:
        response = client.post("/auto_plan/api/auth/login", json={"password": "never-echo-company-password"}, headers={"Origin": settings.public_origin, "X-Auto-Plan-Request": "1"})
        assert response.status_code == 422
        assert "never-echo-company-password" not in response.text

from datetime import datetime, timedelta, timezone
import pytest
from fastapi import APIRouter, Depends
from auto_plan.app import require_editor, require_viewer
from auto_plan.company_auth import AuthenticationUnavailable, CompanyAuthenticator
from auto_plan.access import StoreUnavailable
import httpx
import certifi


@pytest.fixture
def scenario():
    settings = Settings(initial_admin_id="ADMIN")
    now = [datetime(2026, 10, 14, 0, tzinfo=timezone.utc)]
    store = MemoryAccessStore()
    router = APIRouter()
    @router.get("/business")
    def read(principal=Depends(require_viewer)):
        return {"read": True}
    @router.post("/business")
    def write(principal=Depends(require_editor)):
        return {"written": True}
    application = create_app(settings, store=store, authenticator=CompanyAuthentication(), clock=lambda: now[0], routers=[router])
    with TestClient(application, base_url=settings.public_origin) as client:
        headers = {"Origin": settings.public_origin, "X-Auto-Plan-Request": "1"}
        yield client, store, now, headers


def sign_in(client, headers, username="admin", password="company-secret"):
    return client.post("/auto_plan/api/auth/login", json={"username": username, "password": password}, headers=headers)


def test_independent_roles_revoke_on_next_request_and_protect_last_admin(scenario):
    client, store, now, headers = scenario
    assert sign_in(client, headers).status_code == 200
    assert client.post("/auto_plan/api/business", headers=headers).status_code == 403
    assert client.get("/auto_plan/api/business").status_code == 403
    assert client.put("/auto_plan/api/admin/authorizations/admin", json={"roles": ["editor"]}, headers=headers).status_code == 409
    assert client.put("/auto_plan/api/admin/authorizations/ viewer ", json={"roles": ["viewer"]}, headers=headers).status_code == 200
    viewer = TestClient(client.app, base_url="https://fsscm.cminl.oa")
    assert sign_in(viewer, headers, " VIEWER ").json() == {"user_id": "VIEWER", "roles": ["viewer"]}
    assert viewer.get("/auto_plan/api/business").status_code == 200
    assert viewer.post("/auto_plan/api/business", headers=headers).status_code == 403
    assert viewer.get("/auto_plan/api/admin/authorizations").status_code == 403
    assert client.put("/auto_plan/api/admin/authorizations/VIEWER", json={"roles": ["viewer", "editor"]}, headers=headers).status_code == 200
    assert viewer.post("/auto_plan/api/business", headers=headers).status_code == 200
    assert client.put("/auto_plan/api/admin/authorizations/VIEWER", json={"roles": [], "active": False}, headers=headers).status_code == 200
    assert viewer.get("/auto_plan/api/auth/me").status_code == 401
    assert sign_in(viewer, headers, "VIEWER").status_code == 403
    viewer.close()


@pytest.mark.parametrize("elapsed", [timedelta(hours=2), timedelta(hours=8)])
def test_session_expires_at_idle_or_absolute_boundary(scenario, elapsed):
    client, store, now, headers = scenario
    assert sign_in(client, headers).status_code == 200
    if elapsed == timedelta(hours=8):
        for hour in range(1, 8):
            now[0] += timedelta(hours=1)
            assert client.get("/auto_plan/api/auth/me").status_code == 200
        now[0] += timedelta(hours=1)
    else:
        now[0] += elapsed
    assert client.get("/auto_plan/api/auth/me").status_code == 401


def test_failed_company_login_and_empty_authorizations_do_not_bootstrap_other_account(scenario):
    client, store, now, headers = scenario
    assert sign_in(client, headers, password="wrong").status_code == 401
    assert sign_in(client, headers, "someone-else").status_code == 403
    assert sign_in(client, headers).status_code == 200
    assert client.get("/auto_plan/api/admin/authorizations").json() == [{"user_id": "ADMIN", "roles": ["administrator"], "active": True}]
    assert client.post("/auto_plan/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/auto_plan/api/auth/me").status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://attacker.example", "X-Auto-Plan-Request": "1"}, {"Origin": "https://fsscm.cminl.oa"}])
def test_write_requests_require_approved_origin_and_custom_header(scenario, headers):
    client, store, now, valid_headers = scenario
    assert sign_in(client, headers).status_code == 403


@pytest.mark.parametrize("status", [401, 500, 503, 302])
def test_company_http_response_is_distinguished_from_service_unavailable(scenario, status):
    client, store, now, headers = scenario
    settings = Settings(auth_ca_file=certifi.where())
    authentication = CompanyAuthenticator(settings, transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    client.app.state.authenticator = authentication
    assert sign_in(client, headers).status_code == (401 if status == 401 else 503)
    assert client.get("/auto_plan/api/auth/me").status_code == 401
    authentication.close()


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
def test_company_timeout_and_connection_failure_never_create_session(scenario, error):
    client, store, now, headers = scenario
    def unavailable(request):
        raise error("test external failure")
    authentication = CompanyAuthenticator(Settings(auth_ca_file=certifi.where()), transport=httpx.MockTransport(unavailable))
    client.app.state.authenticator = authentication
    response = sign_in(client, headers)
    assert response.status_code == 503 and "验证服务不可用" in response.text
    assert client.get("/auto_plan/api/auth/me").status_code == 401
    authentication.close()


def test_fake_authentication_cannot_start_on_production():
    with pytest.raises(ValueError, match="PLAN_PROD forbids"):
        Settings(oracle_user="PLAN_PROD", test_auth_enabled=True, test_auth_password="test-only")


def test_database_failure_never_reports_success(scenario):
    client, store, now, headers = scenario
    class UnavailableDatabase(MemoryAccessStore):
        def login(self, *args):
            raise StoreUnavailable
    client.app.state.access_store = UnavailableDatabase()
    response = sign_in(client, headers)
    assert response.status_code == 503 and "数据库暂不可用" in response.text
    assert "set-cookie" not in response.headers


def test_new_server_start_invalidates_existing_sessions():
    settings = Settings(initial_admin_id="ADMIN")
    store = MemoryAccessStore()
    with TestClient(create_app(settings, store=store, authenticator=CompanyAuthentication()), base_url=settings.public_origin) as client:
        headers = {"Origin": settings.public_origin, "X-Auto-Plan-Request": "1"}
        assert sign_in(client, headers).status_code == 200
        cookie = client.cookies.get("auto_plan_session")
    with TestClient(create_app(settings, store=store, authenticator=CompanyAuthentication()), base_url=settings.public_origin) as client:
        client.cookies.set("auto_plan_session", cookie, domain="fsscm.cminl.oa", path="/auto_plan")
        assert client.get("/auto_plan/api/auth/me").status_code == 401
        assert sign_in(client, headers).status_code == 200
        assert len(client.get("/auto_plan/api/admin/audit").json()) == 1


def test_configured_prefix_serves_nested_route_and_assets_without_html_for_missing_asset(tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'index.html').write_text('<head><!--AUTO_PLAN_CONFIG--></head><script src="./assets/app.js"></script>')
    (tmp_path / 'assets' / 'app.js').write_text('console.log("ready")')
    settings = Settings(base_path='/planning', frontend_dist=tmp_path)
    with TestClient(create_app(settings, store=MemoryAccessStore(), authenticator=CompanyAuthentication()), base_url=settings.public_origin) as client:
        response = client.get('/planning/admin')
        assert response.status_code == 200
        assert '<base href="/planning/">' in response.text and '"basePath": "/planning"' in response.text
        assert client.get('/planning/assets/app.js').text == 'console.log("ready")'
        assert client.get('/planning/assets/missing.js').status_code == 404
        assert client.get('/planning/api/not-an-api').status_code == 404
