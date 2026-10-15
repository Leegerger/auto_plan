import hashlib
import html
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr, field_validator
from .access import Principal, AuthorizationDenied, LastAdministrator, StoreUnavailable, normalize_account
from .company_auth import AuthenticationUnavailable, CompanyAuthenticator, TestAuthenticator
from .settings import Settings

COOKIE_NAME = "auto_plan_session"


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_principal(request: Request) -> Principal:
    token = request.cookies.get(COOKIE_NAME)
    principal = request.app.state.access_store.session(token_digest(token), request.app.state.clock()) if token else None
    if principal is None:
        raise HTTPException(401, "登录已失效，请重新登录")
    return principal


def require_role(*roles: str):
    def check(principal: Principal = Depends(current_principal)):
        if not principal.roles.intersection(roles):
            raise HTTPException(403, "没有执行此操作的权限")
        return principal
    return check


require_editor = require_role("editor")
require_viewer = require_role("viewer", "editor")
require_administrator = require_role("administrator")


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(repr=False)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("账号不能为空或包含控制字符")
        return value


class Authorization(BaseModel):
    roles: set[Literal["viewer", "editor", "administrator"]]
    active: bool = True


def create_app(settings: Settings | None = None, *, store=None, input_store=None, plan_store=None, demand_store=None, authenticator=None, clock=None, routers=()):
    settings = settings or Settings()
    owns_store = store is None
    owns_auth = authenticator is None

    @asynccontextmanager
    async def lifespan(app):
        if app.state.access_store is None:
            from .oracle_access import OracleAccessStore
            app.state.access_store = OracleAccessStore.connect(settings)
        if app.state.authenticator is None:
            app.state.authenticator = TestAuthenticator(settings) if settings.test_auth_enabled else CompanyAuthenticator(settings)
        try:
            app.state.access_store.startup()
            yield
        finally:
            if owns_auth and app.state.authenticator:
                app.state.authenticator.close()
            if owns_store and app.state.access_store:
                app.state.access_store.close()

    app = FastAPI(title="Bonding 生产排程", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=f"{settings.base_path}/api/openapi.json")
    app.state.settings = settings
    app.state.access_store = store
    app.state.input_store = input_store
    app.state.plan_store = plan_store
    app.state.demand_store = demand_store
    app.state.authenticator = authenticator
    app.state.clock = clock or (lambda: datetime.now(timezone.utc))

    @app.middleware("http")
    async def security(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("origin") != settings.public_origin or request.headers.get("x-auto-plan-request") != "1":
                return JSONResponse({"detail": "请求来源无效，请重新打开应用"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.exception_handler(StoreUnavailable)
    async def database_unavailable(request, error):
        return JSONResponse({"detail": "数据库暂不可用，请稍后重试"}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Do not reflect request bodies or supplied company credentials.
        return JSONResponse({"detail": "请求格式无效，请检查输入"}, status_code=422)

    @app.exception_handler(AuthorizationDenied)
    async def access_denied(request, error):
        return JSONResponse({"detail": "账号未获本系统授权或授权已撤销"}, status_code=403)

    api = APIRouter(prefix=f"{settings.base_path}/api")

    @api.get("/health")
    def health():
        return {"status": "ok"}

    @api.post("/auth/login")
    def login(payload: Login, request: Request):
        try:
            valid = app.state.authenticator.verify(payload.username.strip(), payload.password.get_secret_value())
        except AuthenticationUnavailable:
            raise HTTPException(503, "验证服务不可用，请稍后重试") from None
        if not valid:
            raise HTTPException(401, "账号或密码错误")
        token = secrets.token_urlsafe(32)
        principal = app.state.access_store.login(normalize_account(payload.username), token_digest(token), app.state.clock(), normalize_account(settings.initial_admin_id))
        old_token = request.cookies.get(COOKIE_NAME)
        if old_token:
            app.state.access_store.logout(token_digest(old_token))
        response = JSONResponse(principal.public())
        response.set_cookie(COOKIE_NAME, token, httponly=True, secure=True, samesite="strict", path=settings.base_path or "/", max_age=8 * 3600)
        return response

    @api.get("/auth/me")
    def me(principal: Principal = Depends(current_principal)):
        return principal.public()

    @api.post("/auth/logout")
    def logout(request: Request):
        token = request.cookies.get(COOKIE_NAME)
        if token:
            app.state.access_store.logout(token_digest(token))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie(COOKIE_NAME, path=settings.base_path or "/", httponly=True, secure=True, samesite="strict")
        return response

    @api.get("/admin/authorizations")
    def authorizations(principal: Principal = Depends(require_administrator)):
        return app.state.access_store.authorizations(principal.user_id)

    @api.put("/admin/authorizations/{user_id}")
    def authorize(user_id: str, payload: Authorization, principal: Principal = Depends(require_administrator)):
        user_id = normalize_account(user_id)
        if not user_id or len(user_id) > 128 or any(ord(c) < 32 for c in user_id):
            raise HTTPException(422, "账号无效")
        try:
            return app.state.access_store.authorize(principal.user_id, user_id, frozenset(payload.roles), payload.active, app.state.clock())
        except LastAdministrator:
            raise HTTPException(409, "至少保留一个有效管理员") from None

    @api.get("/admin/audit")
    def audit(principal: Principal = Depends(require_administrator)):
        return app.state.access_store.audit(principal.user_id)

    from .initialization_api import router as initialization_router
    api.include_router(initialization_router)
    app.include_router(api)
    from .input_api import router as input_router
    app.include_router(input_router, prefix=f"{settings.base_path}/api")
    from .plan_api import router as plan_router
    app.include_router(plan_router, prefix=f"{settings.base_path}/api")
    from .shared_plan_api import router as shared_plan_router
    app.include_router(shared_plan_router, prefix=f"{settings.base_path}/api")
    for router in routers:
        app.include_router(router, prefix=f"{settings.base_path}/api")

    @app.get(f"{settings.base_path}/{{path:path}}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith("api/") or path == "api":
            raise HTTPException(404, "接口不存在")
        dist = settings.frontend_dist.resolve()
        target = (dist / path).resolve()
        if target.is_relative_to(dist) and target.is_file() and target.name != "index.html":
            return FileResponse(target)
        if path.startswith("assets/") or not target.is_relative_to(dist):
            raise HTTPException(404, "资源不存在")
        index = dist / "index.html"
        if not index.exists():
            return HTMLResponse("前端尚未构建，请执行 frontend 的 npm run build", status_code=503)
        config = json.dumps({"basePath": settings.base_path}, ensure_ascii=True)
        document = index.read_text().replace("<!--AUTO_PLAN_CONFIG-->", f'<base href="{html.escape(settings.base_path or "")}/"><script>window.__AUTO_PLAN_CONFIG__={config}</script>')
        return HTMLResponse(document)

    return app
