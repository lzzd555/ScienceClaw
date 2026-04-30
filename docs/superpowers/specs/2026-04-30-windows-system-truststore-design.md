# Windows 系统证书库用于后端 HTTPS 请求

## 背景

API Monitor MCP 工具测试由本地后端进程执行，而不是由 Postman 执行。当前失败链路是：

```text
前端点击工具测试
-> backend.route.mcp.test_api_monitor_tool
-> backend.deepagent.mcp_runtime.ApiMonitorMcpRuntime.call_tool
-> httpx.AsyncClient
-> 目标 HTTPS API
```

在 Windows 上，Postman 开启证书校验后仍然可以成功调用同一个 API，是因为 Postman 使用了 Windows 系统证书存储。后端里的 Python HTTP 客户端，包括 `httpx`，当前使用的是 Python/OpenSSL 的证书解析方式，常见情况下由 `certifi` 提供 CA bundle，因此不会自动信任已经安装到 Windows“受信任的根证书颁发机构”里的证书。

这会导致某些 API 出现 `CERTIFICATE_VERIFY_FAILED`：这些 API 的证书链在 Windows 中是可信的，但在 Python 默认 CA bundle 中不可信。

## 目标

- 允许 Windows 本地后端运行时使用 Windows 系统证书库发起出站 HTTPS 请求。
- 修复 API Monitor MCP 工具测试访问企业 CA、自签 CA、私有 CA 签发接口时的证书校验失败问题，前提是这些 CA 已经被 Windows 信任。
- 在足够早的后端启动阶段应用证书策略，让后端中创建的 `httpx.AsyncClient` 共享一致的 TLS 信任行为。
- 保留用户显式配置 CA bundle 的能力。
- 不关闭 TLS 证书校验。

## 非目标

- 不添加 `verify=False`、`NODE_TLS_REJECT_UNAUTHORIZED=0` 或类似的不安全绕过方式。
- 不要求 Windows 默认本地流程中的用户手动导出证书并维护 PEM bundle。
- 本设计不处理 Docker 容器中的证书挂载。
- 不重构 API Monitor MCP 的请求执行模型。
- 不在没有开关的情况下强制所有平台改变 TLS 行为。

## 推荐方案

新增 Python `truststore` 依赖，并在后端启动早期按配置启用。`truststore` 可以让 Python TLS 客户端使用操作系统证书库：

- Windows：使用 Windows CryptoAPI 系统证书存储
- macOS：使用系统 Keychain
- Linux：通过 OpenSSL 使用系统 CA 路径

本次主要解决 Windows 本地开发和 Windows 桌面打包运行场景。

## 配置

新增后端环境变量：

```text
RPA_USE_SYSTEM_TRUSTSTORE=auto
```

支持值：

- `auto`：在 Windows 本地后端运行、且没有显式 CA bundle 覆盖时启用。
- `true`：在任意平台尝试启用 `truststore`。
- `false`：不启用 `truststore`。

默认值：

```text
auto
```

以下显式 CA bundle 环境变量优先级更高：

```text
SSL_CERT_FILE
REQUESTS_CA_BUNDLE
CURL_CA_BUNDLE
```

如果设置了其中任意一个变量，`auto` 模式不应注入 `truststore`，因为用户已经显式选择了证书 bundle。若设置为 `true`，后端仍可尝试注入，但应记录日志说明当前存在显式 CA 环境变量。

## 后端设计

新增一个小型 TLS 启动辅助模块，例如：

```text
backend/tls_trust.py
```

职责：

- 读取 `RPA_USE_SYSTEM_TRUSTSTORE`。
- 使用 `sys.platform` 判断当前平台。
- 检测是否存在显式 CA bundle 环境变量。
- 判断是否需要执行 `truststore.inject_into_ssl()`。
- 记录当前采用的 TLS 信任策略。
- 不记录证书内容，也不记录敏感环境变量值。

建议决策表：

| 模式 | 平台 | 是否存在显式 CA 环境变量 | 行为 |
| --- | --- | --- | --- |
| `false` | 任意 | 任意 | 不注入 |
| `auto` | Windows | 否 | 注入 `truststore` |
| `auto` | Windows | 是 | 不注入，尊重显式 CA bundle |
| `auto` | 非 Windows | 任意 | 默认不注入 |
| `true` | 任意受支持平台 | 任意 | 尝试注入 |

在 `backend/main.py` 顶部调用该辅助模块，位置应早于路由模块或其他后端模块创建出站 HTTP client。

示例位置：

```python
from backend.tls_trust import configure_tls_trust

configure_tls_trust()
```

这个调用必须发生在代码创建长期存在的 `httpx.AsyncClient` 之前。某些模块在注入前导入 `httpx` 是可以接受的，关键边界是不要在注入前创建 client 或发起出站请求。

## 依赖变更

在后端依赖中新增：

```text
truststore>=0.10.0
```

项目后端使用 Python 3.13，满足 `truststore` 的 Python 版本要求。

## 错误处理

`truststore` 设置应采用尽力而为策略：

- 注入成功时记录 info 日志：
  ```text
  TLS trust store: system via truststore
  ```
- 当 `RPA_USE_SYSTEM_TRUSTSTORE=false` 时记录 debug 日志：
  ```text
  TLS trust store: Python default
  ```
- 当 `auto` 因存在显式 CA 环境变量而跳过时，记录 info 日志：
  ```text
  TLS trust store: explicit CA bundle environment detected; system truststore not injected
  ```
- 注入失败时记录 warning，并继续启动：
  ```text
  TLS trust store: truststore injection failed; falling back to Python default
  ```

后端不应仅因为 `truststore` 注入失败而启动失败。目标 API 请求如果仍然失败，应继续把原始 HTTPS 错误暴露给用户。

## 数据流

变更后，Windows 上的 API Monitor MCP 工具测试链路变为：

```text
前端点击工具测试
-> FastAPI 后端进程启动
-> configure_tls_trust 注入 truststore
-> ApiMonitorMcpRuntime 创建 httpx.AsyncClient
-> Python SSL 使用 Windows 系统证书库
-> 目标 HTTPS API 证书链校验通过
```

这样可以让 Python 后端的证书信任行为与 Postman 使用 Windows 系统证书库的行为对齐。

## 测试

单元测试：

- `RPA_USE_SYSTEM_TRUSTSTORE=false` 时不导入、不注入 `truststore`。
- Windows 平台、无 CA 环境变量、`RPA_USE_SYSTEM_TRUSTSTORE=auto` 时会走注入路径。
- Windows 平台、设置了 `SSL_CERT_FILE`、`RPA_USE_SYSTEM_TRUSTSTORE=auto` 时跳过注入。
- Linux/macOS 平台、`RPA_USE_SYSTEM_TRUSTSTORE=auto` 时跳过注入。
- `RPA_USE_SYSTEM_TRUSTSTORE=true` 时在受支持平台尝试注入。
- 注入失败时只记录日志，不抛出异常。

集成/手工验证：

1. 在 Windows 中把企业 CA 或私有 CA 安装到“受信任的根证书颁发机构”。
2. 确认 Postman 在开启证书校验时可以成功访问目标接口。
3. 本地启动后端，且不设置 `SSL_CERT_FILE`、`REQUESTS_CA_BUNDLE`、`CURL_CA_BUNDLE`。
4. 确认后端日志出现 `TLS trust store: system via truststore`。
5. 使用 API Monitor MCP 工具测试访问同一个 HTTPS 接口。
6. 确认请求不再因为 `CERTIFICATE_VERIFY_FAILED` 失败。

回归检查：

- 验证模型连接测试以及其他后端 `httpx.AsyncClient` 调用点仍然正常。
- 设置 `SSL_CERT_FILE` 时，显式 CA bundle 工作流仍然正常。

## 风险与缓解

- 风险：Windows 上所有后端出站 HTTPS 请求的 TLS 行为都会变化。
  缓解：使用 `RPA_USE_SYSTEM_TRUSTSTORE` 开关控制，默认仅在 Windows `auto` 场景启用，并保留显式 CA bundle 覆盖。

- 风险：`truststore` 与 `certifi` 对某些公网 endpoint 的信任结果不同。
  缓解：允许通过 `RPA_USE_SYSTEM_TRUSTSTORE=false` 回退到 Python 默认行为。

- 风险：注入发生得太晚。
  缓解：在 `backend/main.py` 顶部调用 `configure_tls_trust()`，早于可能创建 client 的路由导入。

- 风险：Windows 桌面打包启动流程没有传入新增环境变量。
  缓解：默认值 `auto` 覆盖 Windows 场景，不要求桌面端新增配置。

## 发布步骤

1. 在后端依赖中加入 `truststore`。
2. 新增 `backend/tls_trust.py`。
3. 在 `backend/main.py` 早期调用 TLS 启动逻辑。
4. 为决策表添加单元测试。
5. 在 Windows 上使用 API Monitor MCP 工具测试做手工验证。
6. 在本地开发文档中说明新的 `RPA_USE_SYSTEM_TRUSTSTORE` 环境变量。

