# API Monitor MCP 凭证鉴权改造设计

## 1. 背景

当前 API Monitor MCP 的认证配置沿用了普通 MCP server 的模型：用户在 `/chat/tools` 中手动配置 HTTP headers、query parameters 和 credential templates。这个模型可以工作，但不适合 API Monitor MCP：

- API Monitor MCP 是由浏览器请求捕获结果生成的内部 API wrapper，不是用户手动接入的外部 MCP 服务。
- 真实 API 的认证特征已经出现在捕获到的 request headers 中，用户不应该再手动复写 headers。
- 凭证明文应继续由现有凭证管理保存，API Monitor MCP 只引用凭证，不保存捕获到的 token、cookie 或 header value。
- 后续需要支持不同凭证类型的注入逻辑，但当前没有可用凭证类型，因此第一版只提供占位类型。

本设计将 API Monitor MCP 的认证配置改为“捕获阶段临时 profile 判断 + 工具库选择凭证 + runtime 按凭证类型处理”。

## 2. 目标

本次改造完成后：

1. API Monitor 页面在分析或录制 API 的过程中，基于 captured request headers 建立临时 profile，用于汇总请求头名称、出现情况和疑似认证信号。
2. 保存 MCP 时，后端根据临时 profile 判断推荐凭证类型；当前只有一个占位类型。
3. 发布后的 API Monitor MCP 不保存 `auth_profile`，只保存最终认证配置结果。
4. `/chat/tools` 页面中，API Monitor MCP 的认证配置不再让用户手动编辑请求头，而是选择凭证管理中的凭证，并选择凭证类型。
5. 测试和实际运行 API Monitor MCP tool 时，runtime 根据保存的凭证类型和凭证引用进入对应处理分支。
6. 当前占位凭证类型不注入真实认证 header/query/body，只返回清晰的占位行为，便于后续扩展真实类型。

## 3. 非目标

本次不做：

- 不保存捕获到的完整 request headers 或脱敏 header profile。
- 不保存真实 token、cookie、api key、password、secret。
- 不实现 Bearer、Cookie、API Key、OAuth 等真实凭证类型。
- 不自动登录、刷新 token 或维护会话。
- 不改变普通 `stdio`、`sse`、`streamable_http` MCP 的认证配置方式。
- 不把认证参数暴露成 Agent 每次调用 tool 时必须填写的 input schema 参数。

## 4. 核心模型

### 4.1 临时认证 Profile

API Monitor session 在分析和录制期间可以维护一个临时 profile。该 profile 只存在于运行中的 session 内，用来帮助保存时判断凭证类型。

临时 profile 的建议结构：

```json
{
  "header_names": ["authorization", "cookie", "x-csrf-token"],
  "headers": [
    {
      "name": "authorization",
      "display_name": "Authorization",
      "occurrences": 3,
      "tools": ["search_orders", "create_order"],
      "signals": ["authorization-header", "bearer-like-value"],
      "masked_example": "Bearer ***"
    }
  ],
  "recommended_credential_type": "placeholder"
}
```

该 profile 可以用于前端保存弹窗展示“系统检测到可能需要认证”，也可以用于后端默认推荐 `credential_type`。但它不能写入 `user_mcp_servers` 或 `api_monitor_mcp_tools`。

### 4.2 持久认证配置

发布后的 API Monitor MCP 只保存最终配置结果。建议字段保存在 server 文档顶层：

```json
{
  "api_monitor_auth": {
    "credential_type": "placeholder",
    "credential_id": "cred_abc123"
  }
}
```

字段含义：

- `credential_type`: API Monitor MCP 的凭证处理类型。第一版只允许 `"placeholder"`。
- `credential_id`: 引用凭证管理中的凭证 ID。可以为空，表示用户还没有配置凭证。

不保存：

- `auth_profile`
- 捕获到的 request header value
- 脱敏示例
- 手动 header templates

### 4.3 凭证类型

第一版只提供占位类型：

```text
placeholder
```

占位类型的行为：

- UI 中显示为“占位符”或“Placeholder”。
- 后端允许保存该类型。
- runtime 可加载所选 credential，证明凭证引用有效。
- runtime 不把 credential 的 username/password/domain 注入任何真实 API 请求。
- request preview 中标记当前使用了占位凭证类型，但不展示 credential 明文。

后续真实类型可以在同一分派接口中增加，例如：

- `bearer_token`
- `cookie_header`
- `api_key_header`
- `api_key_query`

## 5. API Monitor 页面流程

### 5.1 分析和录制期间

API Monitor 已经在 `CapturedRequest.headers` 中保存请求头。新增逻辑应在 session 层按已捕获 calls 汇总 profile：

1. 读取 captured calls 的 request headers。
2. 标准化 header name 为小写。
3. 排除浏览器噪声 headers：
   - `accept`
   - `accept-language`
   - `content-type`
   - `origin`
   - `referer`
   - `user-agent`
   - `host`
   - `connection`
   - `cache-control`
   - `pragma`
   - `sec-*`
4. 对剩余 headers 统计出现次数和关联 tool。
5. 对敏感 header 生成信号，不保留真实值：
   - `authorization`
   - `cookie`
   - `proxy-authorization`
   - `x-api-key`
   - `api-key`
   - 包含 `token`、`secret`、`credential`、`session`、`csrf` 的 header。
6. 根据 profile 推荐凭证类型。第一版无论命中何种信号，都推荐 `placeholder`。

### 5.2 保存 MCP

保存弹窗新增认证配置区域：

- 展示 profile 结论，例如“检测到请求中存在认证相关 headers”。
- 展示推荐凭证类型，当前为占位符。
- 允许用户选择凭证管理中的凭证。
- 允许用户不选择凭证并继续保存，但工具库中应显示“未配置凭证”状态。

保存 payload 增加：

```json
{
  "mcp_name": "Orders API MCP",
  "description": "Captured order APIs",
  "confirm_overwrite": false,
  "api_monitor_auth": {
    "credential_type": "placeholder",
    "credential_id": "cred_abc123"
  }
}
```

后端发布时：

- 校验 `credential_type` 只能是 `placeholder`。
- 若传入 `credential_id`，校验该 credential 属于当前用户。
- 持久化 `api_monitor_auth`。
- 不持久化临时 profile。
- 覆盖保存时，用本次 payload 替换旧 `api_monitor_auth`。
- 清理或忽略旧的 API Monitor MCP 手动认证配置来源：`endpoint_config.headers`、`endpoint_config.query`、`credential_binding.headers`、`credential_binding.query`。

## 6. `/chat/tools` 配置流程

API Monitor MCP 的编辑弹窗改为 API Monitor 专属认证配置：

保留：

- 名称
- 描述
- timeout
- enabled
- default enabled

移除：

- HTTP Headers textarea
- Query Parameters textarea
- Credential Bindings alias 列表
- credential template 输入方式

新增：

- Credential Type select：第一版只有 `placeholder`。
- Credential select：读取现有凭证管理列表。
- 空状态：没有凭证时提示用户去凭证管理创建。
- 状态提示：占位类型当前不会注入真实认证，只用于打通配置链路。

保存配置时调用现有 API Monitor MCP config endpoint，payload 中包含：

```json
{
  "name": "Orders API MCP",
  "description": "Captured order APIs",
  "endpoint_config": {
    "timeout_ms": 30000
  },
  "api_monitor_auth": {
    "credential_type": "placeholder",
    "credential_id": "cred_abc123"
  }
}
```

普通 MCP server 的配置界面不受影响。

## 7. Runtime 和测试流程

`ApiMonitorMcpRuntime.call_tool()` 调用真实 API 前读取 `server.api_monitor_auth`。

处理流程：

1. 加载 tool contract。
2. 渲染 tool path/query/header/body mappings。
3. 读取 `api_monitor_auth.credential_type` 和 `api_monitor_auth.credential_id`。
4. 如果 `credential_id` 不为空，从 credential vault 解析当前用户的 credential。
5. 根据 `credential_type` 分派到注入器。
6. 当前 `placeholder` 注入器不修改 request headers/query/body。
7. 发起真实 API 请求。
8. 返回结果中包含脱敏 request preview。

占位类型 preview 示例：

```json
{
  "request_preview": {
    "auth": {
      "credential_type": "placeholder",
      "credential_configured": true,
      "injected": false
    }
  }
}
```

错误处理：

- 未配置 `api_monitor_auth`：兼容历史行为，按现有非认证请求执行。
- `credential_type` 不支持：返回 `success=false`，不发请求。
- `credential_id` 不存在或不属于当前用户：返回 `success=false`，不发请求。
- `placeholder` 类型：即使配置了 credential，也不注入认证信息。

## 8. Backend API 变更

### 8.1 Profile Preview

新增或扩展接口：

```text
GET /api/v1/api-monitor/session/{session_id}/auth-profile
```

返回临时计算结果，不落库：

```json
{
  "status": "success",
  "profile": {
    "header_count": 3,
    "sensitive_header_count": 2,
    "recommended_credential_type": "placeholder"
  }
}
```

### 8.2 发布 MCP

扩展：

```text
POST /api/v1/api-monitor/session/{session_id}/publish-mcp
```

新增 body 字段：

- `api_monitor_auth`

### 8.3 API Monitor MCP 配置

扩展：

```text
PUT /api/v1/mcp/servers/{server_key}/api-monitor-config
```

新增 body 字段：

- `api_monitor_auth`

详情接口返回 `api_monitor_auth`，用于工具库编辑弹窗回填。

## 9. 数据兼容

已有 API Monitor MCP 可能存在旧字段：

- `endpoint_config.headers`
- `endpoint_config.query`
- `credential_binding.headers`
- `credential_binding.query`
- `credential_binding.credentials`

兼容策略：

- 若没有 `api_monitor_auth`，runtime 保持当前历史行为，继续使用已有 server headers/query 和 credential binding。
- 一旦用户在新 UI 保存 API Monitor MCP 认证配置，后端写入 `api_monitor_auth`，并停止把旧 header template 当作 API Monitor 认证来源。
- 普通 MCP 不受此逻辑影响。

## 10. 测试策略

后端测试：

- 临时 profile 能从 captured request headers 中识别 `Authorization`、`Cookie`、`X-CSRF-Token`。
- profile 过滤 `accept`、`referer`、`origin`、`sec-*` 等噪声 headers。
- profile 不包含真实 header value。
- 发布 MCP 保存 `api_monitor_auth`，但不保存 `auth_profile`。
- 覆盖保存替换旧 `api_monitor_auth`。
- API Monitor config endpoint 能保存 `credential_type` 和 `credential_id`。
- `credential_type` 只允许 `placeholder`。
- 传入不存在的 `credential_id` 时返回 400。
- runtime 遇到 `placeholder` 时不注入任何认证 header/query/body。
- runtime 遇到不存在 credential 时不发起 HTTP 请求。
- 没有 `api_monitor_auth` 的历史 MCP 保持旧行为。

前端测试：

- API 类型包含 `api_monitor_auth`。
- API Monitor 保存弹窗能加载临时 profile 和凭证列表。
- 保存 MCP payload 包含 `api_monitor_auth`，不包含 `auth_profile`。
- API Monitor MCP 编辑弹窗不再展示手动 headers/query/credential template。
- 编辑弹窗能选择占位凭证类型和凭证。

## 11. 验收标准

- API Monitor 页面保存 MCP 时，可以基于请求头 profile 推荐占位凭证类型。
- 已发布的 MCP server 文档只保存 `api_monitor_auth`，不保存 `auth_profile`。
- `/chat/tools` 中 API Monitor MCP 的认证配置改为选择凭证和凭证类型，不再手动配置请求头。
- 测试和实际运行 API Monitor MCP tool 时会进入凭证类型分派逻辑。
- 当前 `placeholder` 类型不会注入真实认证内容，但会在 preview 中明确标记。
- 普通 MCP 的认证配置和历史 API Monitor MCP 的兼容行为不被破坏。
