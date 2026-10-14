# Bonding 生产排程

Vue 3、TypeScript、Element Plus 前端与 FastAPI 应用共用一个服务。Oracle 19c 是会话和授权的唯一持久存储。

安装与构建：

```bash
uv sync
cd frontend
npm ci
npm run build
cd ..
```

配置通过环境变量传入，不把密码写入仓库。必要配置为 `AUTO_PLAN_ORACLE_USER`、`AUTO_PLAN_ORACLE_PASSWORD`、`AUTO_PLAN_ORACLE_DSN`、`AUTO_PLAN_AUTH_CA_FILE` 和 `AUTO_PLAN_INITIAL_ADMIN_ID`。`AUTO_PLAN_PUBLIC_ORIGIN` 默认 `https://fsscm.cminl.oa`，`AUTO_PLAN_BASE_PATH` 默认 `/auto_plan`，代理保留此前缀。`AUTO_PLAN_ORACLE_CLIENT_PATH` 默认 `/opt/oracle/instantclient_21_13`，连接池上限默认 10。

维护者先记录恢复点，再用配置的目标账号显式执行迁移。Oracle DDL 会提交，失败时应检查已完成对象，不能直接重复整份迁移。

```bash
uv run python -m auto_plan.migrate migrations/001_access.sql --expected-user PLAN_TEST
uv run uvicorn auto_plan.app:create_app --factory --host 0.0.0.0 --port 8000
```

生产将 FastAPI 放在配置的 HTTPS Origin 后面，保留原始路径前缀。FastAPI 提供 `frontend/dist` 静态文件，注入运行时前缀，前端支持直接打开深层路由。Cookie 只在 HTTPS 下发送。运行时不迁移数据库；启动会使旧会话失效。初始管理员只有公司验证通过且授权表为空时才建立，其默认角色仅为管理员。

API 写请求必须带 `Origin` 和 `X-Auto-Plan-Request: 1`。角色为 `viewer`、`editor`、`administrator`，可兼任。管理员不能撤销最后一个有效管理员。公司 API 15 秒超时，不重试，严格验证根 CA、主机名与 SNI，仅该客户端启用旧 TLS 重协商兼容并绕过环境代理。

验证入口：

```bash
uv run pytest
cd frontend
npm run build
npx playwright install chromium
npm run test:e2e
```

普通 API 测试明确注入公司 API 与数据库边界适配器，不验证 Oracle。浏览器测试启动本机 HTTPS 服务并注入相同测试边界，无真实公司账号参与。真实 Oracle 测试使用当前应用的公开 API，只在显式配置 `AUTO_PLAN_TEST_ORACLE=1` 且账号为 `PLAN_TEST` 时运行，先单独执行迁移，测试只写 `AP_*` 对象和 `T24*` 测试账号。

```bash
AUTO_PLAN_TEST_ORACLE=1 uv run pytest tests/test_oracle_access.py
```

Oracle 测试会使本应用测试会话失效，并保留授权审计。应在无人使用该测试应用时执行。测试认证仅可显式启用 `AUTO_PLAN_TEST_AUTH_ENABLED=true` 与独立的 `AUTO_PLAN_TEST_AUTH_PASSWORD`，且只允许 PLAN_TEST；PLAN_PROD 会拒绝启动。真实公司账号登录和容器部署另行验证。
