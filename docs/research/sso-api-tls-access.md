# 公司账号 API 容器网络与 TLS 接入实测报告

承接议题：[目标容器内公司账号 API 的网络与 TLS 接入条件是什么？](https://github.com/Leegerger/auto_plan/issues/19)。
实测日期：2026-10-13（北京时间）。
验证目标：查明公司账号 API（`https://psamv4athefs.cminl.oa/api/SSO/Login`）在容器环境下的网络路径、CA 证书信任、TLS/SNI 要求、状态码及身份键规范化规则。

## 1. 测试环境与命令记录

- 测试容器环境：Linux x86_64，Python 3.12+，`requests==2.34.2`，`urllib3==2.7.0`，OpenSSL 3.0.x。
- 根证书提取与校验：
  - 通过 OpenSSL 提取服务器证书链之根颁发机构证书（`DC=oa, DC=cminl, CN=cminl-oa`）。
  - 证书有效期：`2024-12-24 01:02:48 GMT` 至 `2034-10-18 03:02:45 GMT`。
  - SHA-256 指纹：`25:27:33:E5:27:EC:31:0A:37:BA:42:F2:F7:1B:0F:B0:A3:38:60:CC:C4:52:C8:D2:89:88:80:14:0A:A9:57:22`。
  - 叶子证书：`CN = *.cminl.oa`，有效期至 `2027-04-14 07:07:31 GMT`。

## 2. 网络路径与 DNS 解析

1. **DNS 解析**：
   - 内部 DNS（`10.0.0.1`）解析 `psamv4athefs.cminl.oa` 得到内部 IP `10.189.127.161`。
2. **代理配置**：
   - 该地址为公司纯内网地址，无需走外部 HTTP/HTTPS 代理。
   - 容器若配置了环境变量 `http_proxy`/`https_proxy`，请求必须显式设置 `proxies={'http': None, 'https': None}` 绕过代理，否则代理握手会失败。

## 3. TLS 握手与证书验证实测

测试了 5 种不同的 TLS 与适配器组合：

| 组合 | 结果 | 现象与原因 |
| --- | --- | --- |
| 1. 默认 requests + 挂载 CA（verify=True） | 失败（`SSLError`） | OpenSSL 3 报 `unsafe legacy renegotiation disabled` (`0x0A000152`)。 |
| 2. 启用 Legacy Renegotiation + 正常 SNI + 挂载 CA（verify=True） | **成功**（严格校验通过） | 成功完成 TLS 1.2 握手，证书链与主机名严格校验一致。 |
| 3. Legacy + 正常 SNI + verify=False | 失败 | urllib3 在 `check_hostname=True` 时拒绝 `CERT_NONE`。 |
| 4. Legacy + 关闭 SNI + 挂载 CA（verify=True） | 失败（`ValueError`） | Python ssl/urllib3 要求 `check_hostname` 必须有 `server_hostname`。 |
| 5. 参考实现 Legacy + 关闭 SNI + verify=False | 可连但无证书校验 | 完全绕过了安全信任链，与安全决议相悖。 |

**最小放宽项结论**：
- **必须放宽**：仅在调用该 API 的客户端启用 `OP_LEGACY_SERVER_CONNECT`（`options |= 0x4`），以兼容内网旧版 Windows/IIS TLS 服务器的不安全重协商机制。
- **无需放宽**：**不能且不需要关闭 SNI**。保留 SNI（`server_hostname='psamv4athefs.cminl.oa'`）完全支持泛域名证书 `*.cminl.oa` 匹配，同时可保持 `check_hostname=True` 与 `verify='/path/to/cminl_ca.crt'` 的严格校验证书模式。

## 4. API 交互与响应语义

接口地址：`POST https://psamv4athefs.cminl.oa/api/SSO/Login`  
请求体结构：
```json
{
  "UserID": "<username>",
  "Password": "<password>",
  "UserType": "Auto",
  "SysID": "FS_MOD-SCM_PORTAL"
}
```

实测响应行为：
- **认证成功（HTTP 200）**：
  - `Content-Type: text/plain; charset=utf-8`。
  - 响应体内容为一次性 Ticket GUID（例如 `4c9efd93-5ad4-4f7d-b192-7f1ba06c06d0`），响应头带 `CustomSsoTicket4: <guid>`。
  - 接口不返回员工工号、姓名、角色等扩展信息；扩展信息需由系统内部权限表 `PLAN_USER_PERMISSION` 或 HR 接口提供。
- **凭证错误（HTTP 401）**：
  - 响应体为 JSON：`{"message":"9001: 帳號or密碼錯誤! ErrMsg=LdapException: Invalid Credentials (49)..."}`。
- **参数缺失或空值（HTTP 401）**：
  - 响应体为 JSON，包含参数解析异常详情（如 `json 參數 :UserID 找不到或錯誤的 json 格式`）。
- **超时与网络异常**：
  - 按决议设定 15 秒超时，连接超时或 5xx 统一按“验证服务不可用”提示。

## 5. 身份键规范化规则验证

针对真实账号实测大小写与空格表现：

| 输入形式 | HTTP 状态码 | 返回现象 / 说明 |
| --- | --- | --- |
| 精确小写 `guorong.li` | **200 OK** | 认证通过 |
| 全大写 `GUORONG.LI` | **200 OK** | 认证通过，LDAP/AD 账号不区分大小写 |
| 混合大小写 `Guorong.Li` | **200 OK** | 认证通过 |
| 包含前导空格 `' guorong.li'` | 401 Unauthorized | 服务端报错 `9008: GUORONG.LI is not exist on the database !` |
| 包含尾随空格 `'guorong.li '` | 401 Unauthorized | 服务端报错 LDAP Invalid Credentials |
| 两端空格 `'  guorong.li  '` | 401 Unauthorized | 服务端报错 LDAP Invalid Credentials |

**规范化结论**：
- 前端和后端接收用户输入后，必须先执行 `.strip()` 去除首尾空格，再调用公司 SSO API，否则会导致有效账号认证失败。
- 系统内部身份键必须在去空格后统一转为大写（`username.strip().upper()`），以此作为会话、权限表、审计表与草稿归属的唯一标识，确保大小写输入统一对应同一业务实体。
