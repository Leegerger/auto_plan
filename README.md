# Bonding 生产排程

Vue 3、TypeScript、Element Plus 前端与 FastAPI 应用共用一个服务。Oracle 19c 是会话、授权、输入资料、共享计划、采用版与幂等结果的唯一真源。

已完成数据库迁移的 `PLAN_TEST` 可用交互脚本快速配置：

```bash
./scripts/setup-test-env.sh
```

脚本隐藏密码输入，将配置保存到仓库外的 `~/.config/auto-plan/plan-test.env`，权限为 `600`。支持 `XDG_CONFIG_HOME`、`AUTO_PLAN_TEST_ENV_FILE` 或 `--env-file PATH` 指定位置。它会安装依赖、构建前端、检查 schema 与数据一致性，并询问是否启动服务。公司登录和独立测试登录可在设置时选择。需要 Python 3、uv、Node.js 22 或更新版本及 npm，Oracle Instant Client 和 HTTPS 反代须已准备好。

```bash
./scripts/setup-test-env.sh --check       # 检查已保存的配置与数据库
./scripts/setup-test-env.sh --start       # 使用已保存配置启动，不重复安装和构建
./scripts/setup-test-env.sh --grant-admin # 确认后维护授权，并记录审计
```

`INITIAL_ADMIN_ID` 仅在授权表为空时建立初始管理员。已有测试授权时，脚本会提示并单独确认补授管理员权限，保留原有其他角色。管理员权限不包含业务编辑权限。脚本只允许 `PLAN_TEST`，不自动迁移、推定 baseline 或修补历史数据；数据库尚未就绪时按[部署运行手册](docs/deployment.md)处理。

安装与构建：

```bash
uv sync
cd frontend
npm ci
npm run build
cd ..
```

前端采用工作流优先的桌面信息架构：工作台、排程、需求与资料、版本与采用、系统管理。视觉 token 与交互规则见[前端设计规格](docs/frontend-design.md)，架构取舍见[ADR 0002](docs/adr/0002-workflow-first-desktop-frontend.md)。`DESIGN.md` 是视觉参考来源，项目适配规则以设计规格为准。

配置通过环境变量传入，不把密码写入仓库。必要配置为 `AUTO_PLAN_ORACLE_USER`、`AUTO_PLAN_ORACLE_PASSWORD`、`AUTO_PLAN_ORACLE_DSN`、`AUTO_PLAN_AUTH_CA_FILE` 和 `AUTO_PLAN_INITIAL_ADMIN_ID`。`AUTO_PLAN_PUBLIC_ORIGIN` 默认 `https://fsscm.cminl.oa`，`AUTO_PLAN_BASE_PATH` 默认 `/auto_plan`，代理保留此前缀。`AUTO_PLAN_ORACLE_CLIENT_PATH` 默认 `/opt/oracle/instantclient_21_13`，连接池上限默认 10。

打包、运行配置、显式校验和迁移、启动就绪、信号停止、升级与回退流程见[部署运行手册](docs/deployment.md)。使用 `scripts/build-image.sh RELEASE_ID IMAGE_TAG` 构建包含前端、API 和 worker 的单镜像。维护者先记录恢复点，再显式安装迁移台账并顺序执行 001 至 005，启动不改 schema。

```bash
uv run python -m auto_plan.serve
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

普通 API 测试明确注入公司 API 与数据库边界适配器，不验证 Oracle。浏览器测试启动本机 HTTPS 服务并注入相同测试边界，无真实公司账号参与。真实 Oracle 测试使用当前应用的公开 API，只在显式配置 `AUTO_PLAN_TEST_ORACLE=1` 且账号为 `PLAN_TEST` 时运行，先单独执行迁移，测试只写 `AP_*` 对象和 `T24*`、`T28*`、`T29*` 测试账号。

```bash
AUTO_PLAN_TEST_ORACLE=1 uv run pytest tests/test_oracle_access.py
```

Oracle 测试会使本应用测试会话失效，并保留授权审计。应在无人使用该测试应用时执行。测试认证仅可显式启用 `AUTO_PLAN_TEST_AUTH_ENABLED=true` 与独立的 `AUTO_PLAN_TEST_AUTH_PASSWORD`，且只允许 PLAN_TEST；PLAN_PROD 会拒绝启动。真实公司账号登录和容器部署另行验证。

业务编辑者可在「日内详情」预览精确生产工时、Recipe 汇总容量与有向切机。十进制和分数工时无损保存于预览上下文，默认四位小数仅用于显示；标准数量和顺序可生成工时依据。参见[精确容量与切机预览](docs/plan-preview.md)。

「共享计划」区分本机修改、共享草稿与正式采用。保存未知时只读，先核实为已提交或已作废；作废后由用户重新发起。参见[共享计划事务与扩展接口](docs/shared-plans.md)。
