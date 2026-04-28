# API Monitor MCP 外部访问 Gateway 设计

状态：草案，等待人工评审  
日期：2026-04-28  
适用范围：API Monitor MCP 发布、工具库管理、外部 MCP 调用入口、runtime 凭证处理

## 1. 背景

API Monitor MCP 当前已经能把浏览器中捕获到的网站 API 请求发布成内部 MCP 工具。发布后的数据主要由两部分组成：

- `user_mcp_servers` 中的 API Monitor MCP server 记录，`transport=api_monitor`，`source_type=api_monitor`。
- `api_monitor_mcp_tools` 中的工具定义，包含 tool name、method、url、input schema、request mappings、response schema 和 token flow 配置。

内部 Agent 使用这些 MCP 时，并不是连接一个独立的外部 MCP 进程，而是由 `ApiMonitorMcpRuntime` 读取这些持久化定义，渲染请求参数，然后直接发起 HTTP 请求。

现在希望把 API Monitor MCP 对外开放，让外部 MCP client 通过一个链接发现并调用某个 API Monitor MCP 中的所有工具。同时，外部调用目标网站/API 时不能使用 RpaClaw 内部配置的 token 或凭证，而必须由外部调用方在每次调用时主动提供 cookie、Authorization token 或其他认证材料。

## 2. 设计选择

采用方案 A：API Monitor MCP 专属外部访问入口。

这个方案不是新增一个独立的 Gateway item。用户在 Tools 页面仍然只看到一个 API Monitor MCP item。外部访问能力是这个 item 的一个配置面板和派生 endpoint。

```text
Tools 页面中的一个 API Monitor MCP item
  -> 可开启外部访问
  -> 派生一个 MCP URL
  -> 外部 MCP client 使用该 URL 访问这个 item 内的工具
```

不采用方案 B 的全局 Gateway 聚合模式。全局 Gateway 会把 RPA MCP、API Monitor MCP 以及未来其他工具来源混在一个入口中，导致工具命名、权限、凭证策略和审计边界变复杂。API Monitor MCP 的外放语义更适合一对一：一个 API Monitor MCP item 对应一个外部 MCP URL。

## 3. 目标

1. 每个 API Monitor MCP item 可以独立开启或关闭外部访问。
2. 开启外部访问不创建新的 MCP server item，也不复制工具定义。
3. 外部 MCP URL 能支持标准 JSON-RPC MCP 方法：
   - `initialize`
   - `notifications/initialized`
   - `ping`
   - `tools/list`
   - `tools/call`
4. `tools/list` 返回该 API Monitor MCP item 下所有 valid tools。
5. `tools/call` 复用现有 API Monitor MCP 工具定义和 request mapping。
6. 外部调用目标 API 时必须使用调用方提供的认证材料。
7. 外部调用模式下不得读取或注入 RpaClaw 内部 vault 中的 credential。
8. 外部访问 token 可创建、轮换、撤销，并与 RpaClaw 用户登录 session 分离。
9. 日志、preview、错误信息不得泄漏调用方 cookie、token、API key 或内部 credential secret。
10. 内部 Agent 调用现有 API Monitor MCP 的行为保持不变。

## 4. 非目标

本设计不做：

- 不把 API Monitor MCP 导出成独立文件包或独立 MCP server 进程。
- 不新增一个“Gateway MCP server”item。
- 不把所有 API Monitor MCP 聚合到一个全局 URL。
- 不替外部调用方保存长期 cookie、storage state 或 bearer token。
- 不让外部调用默认使用 `api_monitor_auth.credential_id` 指向的 vault 凭证。
- 不修改普通 `stdio`、`sse`、`streamable_http` MCP 的配置方式。
- 不重写 API Monitor 录制、分析、YAML 生成和 token flow 发现主流程。

## 5. API Monitor MCP 本体与外部 Gateway 的关系

API Monitor MCP 本体负责“工具是什么、怎么执行”：

- 工具列表来自 `api_monitor_mcp_tools`。
- 工具 contract 来自 YAML 解析结果。
- 请求构造依赖 `method`、`url`、`path_mapping`、`query_mapping`、`body_mapping`、`header_mapping`。
- 动态 token 规则来自 `api_monitor_auth.token_flows`。
- HTTP 调用逻辑复用 `ApiMonitorMcpRuntime` 或拆出的共享执行服务。

外部 Gateway 负责“怎么被外部 MCP client 调用”：

- 处理 MCP JSON-RPC 协议。
- 校验外部访问 token。
- 根据 URL 中的 `server_id` 限定只能访问这个 API Monitor MCP item。
- 把调用方提供的目标 API 认证材料转换成 runtime profile。
- 禁止外部调用路径使用内部 credential。
- 包装 MCP `tools/list` 和 `tools/call` 返回格式。

两者的关系是一层包装，不是两份数据：

```text
External MCP client
  -> API Monitor MCP external endpoint
  -> external access auth
  -> caller credential extraction
  -> ApiMonitorMcpRuntime caller-only mode
  -> api_monitor_mcp_tools
  -> target website/API
```

## 6. 数据模型

外部访问配置保存在现有 `user_mcp_servers` 文档中。建议新增字段：

```json
{
  "external_access": {
    "enabled": true,
    "access_token_hash": "sha256:...",
    "token_hint": "rpa_...abcd",
    "created_at": "2026-04-28T00:00:00",
    "last_rotated_at": "2026-04-28T00:00:00",
    "last_used_at": "2026-04-28T00:00:00",
    "require_caller_credentials": true,
    "allowed_credential_channels": ["arguments", "headers"],
    "allowed_target_auth_headers": [
      "authorization"
    ]
  }
}
```

字段说明：

- `enabled`: 是否开启外部访问。
- `access_token_hash`: 外部访问 token 的哈希，不保存明文。
- `token_hint`: 只用于 UI 展示最后几位或前后缀。
- `require_caller_credentials`: 是否要求外部调用方传目标 API 凭证。第一版由 API Monitor MCP 的 `api_monitor_auth.credential_type` 推导，`placeholder` 为 `false`，`test` 为 `true`。
- `allowed_credential_channels`: 调用方认证材料允许从哪里传入。
- `allowed_target_auth_headers`: 允许注入到目标 API 的认证 header 白名单。

不新增 `user_mcp_servers` 记录，不新增 `api_monitor_mcp_tools` 记录，不复制原工具。

外部调用契约需要暴露 caller auth requirements，但第一版不把它作为独立字段写入 `api_monitor_mcp_tools`。Gateway 在 `tools/list` 时根据 API Monitor MCP server 的 `api_monitor_auth.credential_type` 动态生成 requirements，并写入 tool description、input schema 和扩展 metadata。

```json
{
  "api_monitor_auth": {
    "credential_type": "test",
    "credential_id": "cred_abc123"
  },
  "caller_auth_requirements": {
    "required": true,
    "credential_type": "test",
    "accepted_fields": ["_auth.headers.Authorization"],
    "notes": [
      "Provide caller-owned target API Authorization header for this call only."
    ]
  }
}
```

该 metadata 只说明凭证类型对应的调用方入参形状，不保存任何真实 token、cookie、password、API key 或 session secret。

## 7. 外部 MCP Endpoint

新增路由：

```text
POST /api/v1/api-monitor-mcp/{server_id}/mcp
```

该 endpoint 是 streamable HTTP MCP 风格的 JSON-RPC 入口，与现有 RPA MCP Gateway 的协议处理方式保持一致。

外部 MCP client 配置示例：

```json
{
  "name": "Orders API Monitor MCP",
  "transport": "streamable_http",
  "url": "http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc123/mcp",
  "headers": {
    "Authorization": "Bearer <external_access_token>"
  }
}
```

这里的 `Authorization` 只表示访问 RpaClaw external MCP endpoint 的 token，不表示目标网站/API 的 token。

## 8. 外部访问认证

外部访问 token 与 RpaClaw 登录 session 分离。

原因：

- 现有 `Authorization: Bearer ...` 已被后端用户认证当成 RpaClaw session token。
- 外部 MCP client 通常只能方便地配置 MCP endpoint 级别 headers。
- 使用独立 token 可以做到每个 API Monitor MCP item 单独撤销和轮换。
- 不需要把用户完整登录 session 暴露给外部 MCP client。

校验规则：

1. endpoint 读取 `server_id`。
2. 查找当前 `server_id` 对应的 API Monitor MCP server 文档。
3. 要求 `external_access.enabled=true`。
4. 从 `Authorization: Bearer <token>` 或 `X-RpaClaw-MCP-Token` 读取外部访问 token。
5. 对 token 做 hash 后与 `external_access.access_token_hash` 常量时间比较。
6. 校验通过后，将调用归属到 server 文档的 `user_id`。
7. 校验失败返回 MCP JSON-RPC error 或 HTTP 401。

外部访问 token 只授权访问这个 API Monitor MCP item 的工具，不授权访问用户的其他 RpaClaw API。

## 9. 调用方目标 API 凭证契约

目标网站/API 的认证材料由 API Monitor MCP 上配置的 `api_monitor_auth.credential_type` 决定。外部 Agent 调用一个 API Monitor MCP tool 时，入参分成三层：

1. MCP endpoint 访问 token：配置在外部 MCP client 的 server headers 中，不属于 tool arguments。
2. 业务参数：来自 API Monitor MCP tool 原本的 `input_schema`，例如 `keyword`、`order_id`、`page`。
3. 目标 API 凭证：仅当该 API Monitor MCP 的凭证类型要求外部调用方提供时，才放在保留参数 `_auth` 中。

这里的 `_auth` 不是从 RpaClaw 凭证库、API Monitor MCP 配置或保存时的 `api_monitor_auth` 自动来的。它是外部 Agent 在每一次 `tools/call` 时临时传入的一段参数，表示“这次请求目标网站/API 要用的调用方身份”。Gateway 只在本次调用内使用它，调用结束后丢弃。

换句话说，外部 Agent 的 `tools/call.arguments` 只包含业务参数，以及由凭证类型决定是否需要的 `_auth`。不要把 RpaClaw external access token 当成工具参数，也不要把目标网站 token 配到 RpaClaw endpoint 的普通 `Authorization` 后再期待它自动进入目标 API。

第一版只支持两个凭证类型对应的外部调用契约：

| `credential_type` | 外部调用方是否传 `_auth` | 行为 |
| --- | --- | --- |
| `placeholder` | 否 | Gateway 不注入任何目标 API 凭证，只按业务参数发起请求。 |
| `test` | 是 | 外部调用方必须传 `_auth.headers.Authorization`，Gateway 把该 header 用于 token flow producer 和目标 API 请求。 |

### 9.1 通过 tool arguments 传入

`credential_type=test` 的推荐调用方式：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "search_orders",
    "arguments": {
      "keyword": "abc",
      "_auth": {
        "headers": {
          "Authorization": "Bearer caller-token"
        }
      }
    }
  }
}
```

`_auth` 是 Gateway 保留字段，不传给业务 request mapping。

允许字段：

- `_auth.headers.Authorization`: 调用方自己的目标 API Authorization header 完整值，例如 `Bearer caller-token`。

保留但不作为第一版必做：

- `_auth.headers` 中的其他 header，例如 `X-API-Key`、`X-CSRF-Token`。
- `_auth.cookie`: 原始 Cookie header 字符串。
- `_auth.cookies`: cookie object 数组，可在后续版本转成 Cookie header 或交给 `httpx` cookie jar。
- `_auth.query`: 目标 API query 认证参数，仅限白名单 key。

`credential_type=placeholder` 时，外部 Agent 不需要传 `_auth`。如果传了 `_auth`，Gateway 应忽略并在 preview 中标记未使用，避免调用方误以为 placeholder 会注入凭证。

### 9.2 通过 MCP request headers 传入

适合 MCP client 不方便每次构造 `_auth` 的场景：

```text
X-RpaClaw-Target-Authorization: Bearer caller-token
```

这些 headers 只用于目标 API，不参与 RpaClaw endpoint 登录认证。

优先级：

1. `arguments._auth` 优先。
2. MCP request target headers 作为默认值。
3. 两者冲突时，`arguments._auth` 覆盖 request target headers。

### 9.3 外部 Agent 实际需要传哪些 tool arguments

对每个 API Monitor MCP tool，外部 Agent 一定需要传工具自己的业务参数；是否需要 `_auth` 由 `credential_type` 决定。

业务参数来自该 tool 的原始 schema。比如 API Monitor MCP 中有一个工具：

```json
{
  "name": "search_orders",
  "input_schema": {
    "type": "object",
    "properties": {
      "tenant_id": { "type": "string" },
      "keyword": { "type": "string" },
      "page": { "type": "integer", "default": 1 }
    },
    "required": ["tenant_id", "keyword"]
  }
}
```

外部 Gateway 暴露给 Agent 的 schema 应变成：

当 `credential_type=placeholder`：

```json
{
  "type": "object",
  "properties": {
    "tenant_id": { "type": "string" },
    "keyword": { "type": "string" },
    "page": { "type": "integer", "default": 1 }
  },
  "required": ["tenant_id", "keyword"]
}
```

当 `credential_type=test`：

```json
{
  "type": "object",
  "properties": {
    "tenant_id": { "type": "string" },
    "keyword": { "type": "string" },
    "page": { "type": "integer", "default": 1 },
    "_auth": {
      "type": "object",
      "description": "Caller-provided target API Authorization header for this call only. Values are never stored.",
      "properties": {
        "headers": {
          "type": "object",
          "properties": {
            "Authorization": {
              "type": "string",
              "description": "Full Authorization header value, for example: Bearer <token>."
            }
          },
          "required": ["Authorization"],
          "additionalProperties": false
        }
      },
      "required": ["headers"]
    }
  },
  "required": ["tenant_id", "keyword", "_auth"]
}
```

`credential_type=test` 对应的 `tools/call.arguments`：

```json
{
  "tenant_id": "acme",
  "keyword": "invoice",
  "page": 1,
  "_auth": {
    "headers": {
      "Authorization": "Bearer caller-access-token"
    }
  }
}
```

`credential_type=placeholder` 对应的 `tools/call.arguments`：

```json
{
  "tenant_id": "acme",
  "keyword": "invoice",
  "page": 1
}
```

`_auth` 是保留字段，不参与业务参数映射：

- 不会被 `query_mapping`、`body_mapping`、`path_mapping` 当成普通业务参数渲染。
- 不会出现在目标 API body 中，除非 token flow consumer 明确把 profile 中的变量注入到 body。
- 如果原始 API Monitor tool schema 中已经存在 `_auth` 业务字段，发布或外放时应拒绝该工具或要求重命名，避免语义冲突。

### 9.4 哪些内容不是 tool arguments

以下内容不放进 `tools/call.arguments`：

- 外部访问 token：它配置在 MCP client 连接这个 endpoint 的 headers 中，例如 `Authorization: Bearer <external_access_token>`。
- RpaClaw 用户登录 session token：外部 MCP client 不需要也不应该拿到用户登录态。
- RpaClaw 内部 credential id：外部 Agent 不能传 `credential_id` 来要求 RpaClaw 从 vault 取凭证。
- token flow 配置：这是 API Monitor MCP item 的工具配置，不由每次调用提供。

### 9.5 系统如何知道 `_auth` 需要哪些内容

系统不再根据录制到的 Authorization、Cookie、CSRF headers 猜测 `_auth` 结构。第一版只根据用户在 API Monitor MCP 上选择的 `api_monitor_auth.credential_type` 生成 caller auth requirements。

```json
{
  "caller_auth_requirements": {
    "required": true,
    "credential_type": "test",
    "accepted_fields": ["_auth.headers.Authorization"],
    "notes": [
      "Provide caller-owned target API Authorization header for this call only."
    ]
  }
}
```

`credential_type=placeholder` 对应：

```json
{
  "caller_auth_requirements": {
    "required": false,
    "credential_type": "placeholder",
    "accepted_fields": [],
    "notes": [
      "No caller target API credential is required or injected for this tool."
    ]
  }
}
```

录制事实仍可以用于 UI 提示“录制时观察到这些 header”，但不能驱动外部调用契约。外部调用契约必须由用户选择的凭证类型决定。

## 10. Caller-Only Runtime 模式

外部调用必须进入 caller-only runtime 模式。

caller-only 模式要求：

1. 不调用 `get_vault()` 解析 `api_monitor_auth.credential_id`。
2. 不执行 `credential_type=test` 的内部用户名密码登录。
3. 不使用 `server.headers` 中配置的内部认证 header。
4. 不使用 `endpoint_config.query` 中配置的内部认证 query。
5. 允许使用 API Monitor MCP 的工具结构、base URL、mappings、timeout 和 token flow 规则。
6. 如果凭证类型要求调用方提供 `_auth`，将 `_auth` 写入本次调用的 runtime profile。
7. token producer 请求和目标请求都使用同一个 caller profile。
8. 调用结束后释放 profile，不保存调用方 cookie/token。

内部调用模式保持不变：

- RpaClaw 内部 Agent 仍可使用 `api_monitor_auth` 中已配置的 placeholder/test credential 和 token flow。
- `/mcp/servers/{server_key}/api-monitor-tools/{tool_id}/test` 仍按内部测试语义运行，除非后续专门增加外部模式测试按钮。

## 11. Token Flow 处理

API Monitor MCP 的 token flow 是登录后动态 token 处理规则，不负责决定外部调用方需要传哪些凭证。外部调用方的 `_auth` 结构由 `credential_type` 决定；token flow 只在 runtime profile 已经具备基础身份后，负责获取和注入 CSRF、nonce 等动态值。

因此第一版边界是：

- `credential_type=test`: 外部调用方提供 `Authorization`，Gateway 写入 profile。
- token flow: 使用 profile 中的 `Authorization` 请求 producer，提取 CSRF/nonce，再注入目标请求。
- 外部 Agent 不直接传 CSRF，除非未来新增专门凭证类型。

### 11.1 允许复用的内容

- producer request 的 method 和 url。
- producer extract 规则。
- consumer method/url 匹配规则。
- consumer inject 规则。
- refresh status 策略。

### 11.2 需要限制的内容

producer request 中的 headers、query、body 如果包含固定明文 secret，外部模式不能盲目发送。

建议规则：

- 允许 producer request 引用 profile 变量，例如 `{{ auth_token }}`、`{{ csrf_token }}`。
- 允许非敏感常量，例如 `Accept: application/json`。
- 对敏感 header/query/body key，只允许模板引用 caller profile 变量，不允许 literal secret。
- 如果发现不可安全执行的 literal secret，`tools/call` 返回结构化错误，提示用户编辑 token flow。

## 12. MCP Tool 暴露格式

`tools/list` 返回每个 valid API Monitor tool：

当 `credential_type=test`：

```json
{
  "name": "search_orders",
  "description": "Search orders by keyword\n\nCaller auth: this API Monitor MCP is configured with credential_type=test. Pass caller-owned Authorization in _auth.headers.Authorization for each call.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "keyword": {
        "type": "string"
      },
      "_auth": {
        "type": "object",
        "description": "Caller-provided target API Authorization header. Values are used for this call only and are never stored.",
        "properties": {
          "headers": {
            "type": "object",
            "properties": {
              "Authorization": {
                "type": "string",
                "description": "Full Authorization header value, for example: Bearer <token>."
              }
            },
            "required": ["Authorization"],
            "additionalProperties": false
          }
        },
        "required": ["headers"]
      }
    },
    "required": ["keyword", "_auth"]
  },
  "x-rpaclaw-authRequirements": {
    "required": true,
    "credential_type": "test",
    "accepted_fields": ["_auth.headers.Authorization"]
  }
}
```

当 `credential_type=placeholder`：

```json
{
  "name": "search_orders",
  "description": "Search orders by keyword\n\nCaller auth: credential_type=placeholder, no caller target API credential is injected.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "keyword": {
        "type": "string"
      }
    },
    "required": ["keyword"]
  },
  "x-rpaclaw-authRequirements": {
    "required": false,
    "credential_type": "placeholder",
    "accepted_fields": []
  }
}
```

第一版只在凭证类型要求时把 `_auth` 注入到 `inputSchema` 并标记为 required。`placeholder` 不暴露 `_auth`，也不执行任何凭证注入。

为了兼容不展示 custom metadata 的 MCP client，认证需求必须同时写入 tool description 和 `_auth.description`。`x-rpaclaw-authRequirements` 是给理解扩展字段的客户端或未来 RpaClaw UI 使用的结构化提示；它不包含任何真实 token、cookie 或 secret。

对于 `credential_type=test`，如果调用方选择通过 MCP request target headers 提供目标凭证，仍建议在 `tools/call.arguments` 中传 `_auth: {}`，使调用契约保持稳定。runtime 可以把 request target headers 合并进 caller profile，但不应因此把 `_auth` 从 schema 中隐藏。

对于 `require_caller_credentials=true` 的外部访问，`tools/call` 如果没有 `Authorization`，应返回 MCP tool error，而不是尝试无凭证请求。对于 `placeholder`，缺少 `_auth` 是合法调用。

## 13. UI 设计

API Monitor MCP detail/edit dialog 中新增“外部访问”区域。

显示内容：

- 当前状态：关闭或开启。
- MCP URL。
- 外部访问 token 状态：未创建、已创建、最后轮换时间、最后使用时间。
- 操作：
  - 开启外部访问并生成 token。
  - 复制 MCP URL。
  - 显示一次性 token。
  - 轮换 token。
  - 关闭外部访问。
- 凭证提示：外部调用入参由当前 `credential_type` 决定；`placeholder` 不注入凭证，`test` 要求外部调用方每次提供目标 API Authorization header。RpaClaw 不会使用内部保存的凭证。

不新增左侧列表 item，不新增 Tools 页面卡片，不新增单独 Gateway 管理页。

## 14. 后端 API

管理 API：

```text
POST   /api/v1/mcp/servers/{server_key}/api-monitor-external-access/enable
POST   /api/v1/mcp/servers/{server_key}/api-monitor-external-access/rotate-token
POST   /api/v1/mcp/servers/{server_key}/api-monitor-external-access/disable
GET    /api/v1/mcp/servers/{server_key}/api-monitor-external-access
```

返回示例：

```json
{
  "enabled": true,
  "url": "http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc123/mcp",
  "token_hint": "rpa_...abcd",
  "access_token": "rpa_external_once_visible_token",
  "require_caller_credentials": true,
  "last_rotated_at": "2026-04-28T00:00:00",
  "last_used_at": ""
}
```

`access_token` 只在 enable 或 rotate 响应中返回一次。后续 GET 只返回 `token_hint`。

API Monitor MCP detail API 也需要返回每个 tool 的 `caller_auth_requirements`，让前端在外部访问面板中展示“外部调用方需要提供哪些目标 API 凭证”。也就是说，现有：

```text
GET /api/v1/mcp/servers/{server_key}/api-monitor-detail
```

返回的 `tools[]` 中应包含：

```json
{
  "id": "tool_1",
  "name": "search_orders",
  "caller_auth_requirements": {
    "required": true,
    "credential_type": "test",
    "accepted_fields": ["_auth.headers.Authorization"]
  }
}
```

MCP endpoint：

```text
POST /api/v1/api-monitor-mcp/{server_id}/mcp
```

## 15. 错误处理

外部 MCP endpoint 应尽量返回 MCP 标准 JSON-RPC 错误或 MCP tool error payload。

典型错误：

- 外部访问未开启：`-32001 External access is disabled`
- token 无效：`-32002 Invalid external access token`
- server 不是 API Monitor MCP：`-32602 API Monitor MCP not found`
- tool 不存在：`-32602 API Monitor tool not found`
- tool contract invalid：tool result `isError=true`
- 缺少 caller credentials：tool result `isError=true`
- caller credential header 不在白名单：tool result `isError=true`
- token flow 含不安全 literal secret：tool result `isError=true`
- 目标 API 返回非 2xx：tool result `isError=true`，structuredContent 中保留 status code 和脱敏 request preview。

## 16. 安全与隐私

必须遵守以下规则：

- 外部访问 token 只保存 hash。
- 调用方目标 API cookie/token 不落库。
- 调用方目标 API cookie/token 不写日志。
- request preview 使用现有脱敏逻辑，并补充 `_auth` 脱敏。
- `Authorization` 分层：
  - `Authorization` 或 `X-RpaClaw-MCP-Token` 认证外部 MCP endpoint。
  - `X-RpaClaw-Target-Authorization` 或 `_auth.headers.Authorization` 认证目标 API。
- 外部模式下禁止读取 vault。
- 外部模式下禁止使用内部 credential 自动登录。
- 每次 tool call 使用独立 runtime profile。
- 如果支持 cookie jar，不跨调用复用 cookie jar。
- 管理 API 仍要求 RpaClaw 登录用户，并校验 server owner。

## 17. 测试计划

后端测试：

1. 开启外部访问时只更新现有 API Monitor MCP server 文档，不创建新 item。
2. enable/rotate 只返回一次明文 token，持久化只保存 hash。
3. `credential_type=placeholder` 时 `caller_auth_requirements.required=false`，`tools/list` 不把 `_auth` 加入 required schema。
4. `credential_type=test` 时 `caller_auth_requirements.required=true`，且只要求 `_auth.headers.Authorization`。
5. API Monitor MCP detail API 返回根据 `credential_type` 计算出的 `caller_auth_requirements`。
6. `tools/list` 把 `caller_auth_requirements` 写入 tool description、`_auth.description` 和 `x-rpaclaw-authRequirements`。
7. disabled endpoint 拒绝 `initialize`、`tools/list`、`tools/call`。
8. invalid token 返回认证错误。
9. `tools/list` 只返回该 server 下 valid tools。
10. `tools/list` 不返回其他 API Monitor MCP server 的 tools。
11. `credential_type=test` 的 `tools/call` 缺少 `Authorization` 时返回 tool error。
12. `tools/call` 使用 `_auth.headers.Authorization` 调用目标 API。
13. `tools/call` 使用 `X-RpaClaw-Target-Authorization` 调用目标 API。
14. `_auth` 优先级高于 request target headers。
15. 外部模式不调用 `get_vault()`。
16. 外部模式不执行 `credential_type=test` 登录。
17. 外部模式复用 token flow producer/consumer，但 token flow 使用 caller profile。
18. preview 和错误信息不包含真实 token/cookie。
19. 非 owner 无法开启、关闭或轮换外部访问。

前端测试：

1. API Monitor MCP item detail 中展示外部访问区域。
2. 开启外部访问后显示 URL 和一次性 token。
3. 再次打开页面只显示 token hint，不显示明文 token。
4. 轮换 token 后旧 hint 更新。
5. 关闭外部访问后 URL 保留展示但状态为不可用，或隐藏 URL。
6. Tools 列表不新增第二个 item。

## 18. 兼容性与迁移

现有 API Monitor MCP 文档没有 `external_access` 字段时视为关闭外部访问。

现有内部调用保持兼容：

- session MCP binding 不变。
- `build_effective_mcp_servers` 不变。
- `ApiMonitorMcpRuntime` 默认内部模式不变。
- `api_monitor_auth`、`token_flows` 继续服务内部 Agent 调用和工具测试。

外部访问是 opt-in 能力。除用户主动开启外，不改变任何已发布 API Monitor MCP 的外部暴露面。

## 19. 实现边界建议

为了避免让 route 文件继续膨胀，建议拆出小模块：

```text
backend/
  route/
    api_monitor_mcp_gateway.py
  rpa/
    api_monitor_external_access.py
    api_monitor_external_runtime.py
```

职责：

- `api_monitor_mcp_gateway.py`: MCP JSON-RPC endpoint 和 request 解析。
- `api_monitor_external_access.py`: external_access token 生成、hash、校验、URL 构造。
- `api_monitor_external_runtime.py`: caller credential 规范化、caller-only profile 构建、调用 runtime。

如果实现量较小，也可以先在 `route/mcp.py` 和 `deepagent/mcp_runtime.py` 中加薄层，但要避免把外部访问 token 管理和 MCP 协议处理塞进现有普通 MCP 管理接口。

## 20. 开放问题

1. 后续真实凭证类型是否需要开放 `allowed_target_auth_headers` 配置，还是继续使用固定安全白名单。
2. 是否需要为外部访问增加 IP allowlist 或本地网络限制。
3. 外部访问 URL 是否应该支持短路径，例如 `/mcp/api-monitor/{server_id}`，以便用户复制。
4. 是否需要单独记录外部调用审计事件，例如 tool name、status code、耗时、脱敏 preview。
5. 后续版本是否允许隐藏 `_auth` schema，仅通过 MCP request target headers 提供目标凭证。

## 21. 推荐第一版取舍

第一版建议选择最窄可用版本：

1. 不新增 item。
2. 每个 API Monitor MCP item 一个派生 URL。
3. 外部访问 token 独立生成、可轮换、可撤销。
4. `tools/list` 根据 `credential_type` 决定是否把 `_auth` 加入 schema；`placeholder` 不加，`test` 加并标记 required。
5. 第一版 `test` 只支持 `_auth.headers.Authorization`，request target `X-RpaClaw-Target-Authorization` 作为补充。
6. caller-only 模式禁止读取 vault，禁止内部自动登录。
7. token flow 仅复用无 literal secret 的结构规则。

这个版本能满足外放和凭证隔离的核心需求，同时保持用户心智简单：管理一个 API Monitor MCP item，打开一个外部访问开关，复制一个 MCP 链接。
