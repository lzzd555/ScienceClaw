# API Monitor MCP 认证参数设计

> 本文档补充 API Monitor MCP 的认证鉴权改造。目标是把“用户在工具库里手工配置认证”改为“API Monitor 发布 MCP 时自动识别认证参数，用户确认后保存参数定义，Agent 调用 MCP tool 时必须提供这些参数”。

## 1. 背景

当前 API Monitor MCP 的认证模型来自普通 MCP server：

- `user_mcp_servers.endpoint_config.headers` 保存静态 HTTP headers。
- `user_mcp_servers.credential_binding` 保存凭据绑定、credential 模板和 query 模板。
- `ApiMonitorMcpRuntime.call_tool()` 调用真实 API 时，把 server headers/query 与 tool YAML mapping 合并。
- 工具库中的 `ApiMonitorMcpEditDialog.vue` 允许用户手工填写 headers、query、credential binding 和 timeout。

这个模型适合“用户自己连接一个外部 MCP server”。但 API Monitor MCP 的实际使用方式不同：

- API Monitor MCP 是平台内部托管的 API wrapper。
- 调用方是 Agent，不是用户手工点按钮。
- 用户不需要把真实 token/cookie 保存进工具库。
- 系统只需要告诉 Agent：调用这些 tools 时必须提供哪些认证鉴权参数，以及这些参数应该被注入到 HTTP 请求哪里。

因此本次改造不再把 API Monitor MCP 的认证视为 server 级静态配置，而是视为 MCP tool 调用参数的一部分。

## 2. 目标

本次改造完成后：

1. API Monitor 页面点击“保存为 MCP”时，后端用“确定性规则 + AI 补充判断”分析已采用 tools 对应的 captured requests，提取疑似认证鉴权内容。
2. 保存弹窗展示这些候选认证项，用户可以确认哪些需要使用。
3. 保存时只持久化认证参数定义，不保存真实 token、cookie、api key、password、secret。
4. MCP discovery 返回的每个 API Monitor tool input schema 都包含这些认证参数，并把启用项标记为 required。
5. Agent 调用 tool 时必须传入这些认证参数。
6. Runtime 从 tool arguments 中取出认证参数，按保存的位置注入到 header、query 或 body 中，再发起真实 API 请求。
7. API Monitor MCP 编辑界面移除现有手工认证配置表单，改为展示和管理已保存的认证参数定义。

## 3. 非目标

本次不做：

- 不保存捕获到的真实认证值。
- 不接入 credential vault，不新增秘密托管能力。
- 不支持 agent 缺少认证参数时自动登录或自动刷新 token。
- 不处理复杂的多步骤 OAuth refresh 流程。
- 不改变普通 `stdio`、`sse`、`streamable_http` MCP 的认证配置方式。

## 4. 核心模型

API Monitor MCP 的认证模型改为：

```text
API Monitor MCP = N 个 API tools + 一组共享认证参数定义
认证参数定义 = Agent 必须传入的 tool argument + HTTP 注入规则
```

示例：

```json
{
  "auth_parameters": [
    {
      "id": "auth_authorization",
      "enabled": true,
      "location": "header",
      "key": "Authorization",
      "parameter_name": "authorization",
      "description": "Authorization header for the captured API. Include the full value, for example Bearer <token>.",
      "required": true,
      "example_masked": "Bearer ***",
      "source": "rule",
      "confidence": "high"
    },
    {
      "id": "auth_x_csrf_token",
      "enabled": true,
      "location": "header",
      "key": "X-CSRF-Token",
      "parameter_name": "x_csrf_token",
      "description": "CSRF token required by the captured API.",
      "required": true,
      "example_masked": "***",
      "source": "rule",
      "confidence": "high"
    }
  ]
}
```

这组定义保存在 API Monitor MCP 的 server 文档上，建议字段为：

- `auth_parameters: list[dict]`

为了兼容当前结构，也可以放在 `endpoint_config.auth_parameters` 中；但长期模型应把它视为 API Monitor MCP 的专属字段，而不是普通 endpoint 配置。

## 5. 认证候选提取

### 5.1 输入数据

后端发布 MCP 时已经能拿到：

- `ApiMonitorSession.tool_definitions`
- 每个 tool 的 `source_calls`
- `ApiMonitorSession.captured_calls`
- captured request 的 URL、headers、body、method、content-type

候选认证项只从“已采用 tools 的 source calls”中提取。未采用工具的请求不参与认证分析。

### 5.2 双层识别流程

认证识别不能只依赖固定敏感词规则。很多系统会使用业务自定义认证字段，例如：

- `roma-csrf-token`
- `x-tenant-session`
- `x-rpa-auth`
- `x-company-signature`

因此候选提取采用双层流程：

1. **确定性规则识别**：用本地规则提取常见 header、query、body 认证字段，生成高置信候选。
2. **AI 补充识别**：把脱敏后的请求结构交给模型判断是否还有规则没有覆盖的认证鉴权字段，生成补充候选。

最终展示给用户的是两类候选的合并结果。用户确认仍然是保存前的权威决策。

如果 AI 不可用、超时或返回格式无效，系统退化为只使用确定性规则，不阻塞保存流程。

### 5.3 规则候选来源

提取范围：

- Header：`Authorization`、`Cookie`、`X-Api-Key`、`Api-Key`、`X-CSRF-Token`、`X-XSRF-Token`、包含 `token`、`secret`、`credential`、`session` 的 header。
- Query：`access_token`、`refresh_token`、`api_key`、`token`、`auth`、`signature`、`credential`、`session` 等敏感 query key。
- Body：JSON body 中字段名包含 `token`、`secret`、`password`、`apiKey`、`clientSecret`、`credential`、`session` 的字段。

明确排除浏览器和内容协商噪声：

- `accept`
- `accept-language`
- `content-type`
- `origin`
- `referer`
- `user-agent`
- `host`
- `connection`
- `sec-*`
- `cache-control`
- `pragma`

`content-type` 不作为认证参数保存。Runtime 继续按现有请求体发送逻辑处理。

### 5.4 AI 补充识别

AI 只用于补充规则遗漏的认证候选，不直接决定最终保存内容。

发送给 AI 的输入必须脱敏，不能包含真实 token、cookie、password、secret 或完整敏感值。建议只发送：

- 请求 method 和 URL path，不发送完整 query value。
- Header 名称列表，以及每个 header 的脱敏 value 形态，例如 `Bearer ***`、`***`、`present`。
- Query key 列表，以及 value 形态，例如 `present`、`empty`、`numeric`。
- JSON body 字段路径列表，以及 value 类型，例如 `string`、`number`、`boolean`、`object`。
- 字段出现次数、出现在哪些 tools、是否每次请求都出现。
- 已由规则识别出的候选项，避免 AI 重复建议。
- 页面域名或 MCP 名称等低敏上下文。

AI 输出必须是结构化 JSON，包含：

```json
{
  "candidates": [
    {
      "location": "header",
      "key": "roma-csrf-token",
      "reason": "Header name contains csrf and appears on every captured API request.",
      "confidence": "high"
    }
  ]
}
```

后端必须校验 AI 输出：

- `location` 只能是 `header`、`query`、`body`。
- `key` 必须真实存在于脱敏输入字段列表中。
- 已被噪声排除列表命中的字段不能被 AI 重新加入。
- AI candidate 与规则 candidate 去重合并。
- AI candidate 的 `source` 标记为 `ai_inferred`。

AI 不能生成不存在于 captured requests 中的新字段。

### 5.5 去重和命名

候选项按 `location + normalized key` 去重。

`parameter_name` 规则：

- 使用 key 转 snake_case。
- 去掉非字母数字下划线。
- 如果以数字开头，加 `auth_` 前缀。
- 如果与业务参数重名，加 `auth_` 前缀。
- 如果仍冲突，追加序号。

示例：

- Header `Authorization` -> `authorization`
- Header `X-CSRF-Token` -> `x_csrf_token`
- Query `access_token` -> `access_token`
- Body `clientSecret` -> `client_secret`

### 5.6 脱敏示例

候选项可以展示脱敏示例，不能展示真实值。

脱敏规则：

- `Authorization: Bearer abcdef` -> `Bearer ***`
- `Cookie: a=1; b=2` -> `***`
- 其他敏感值 -> `***`
- 空值不生成 example。

### 5.7 置信度和来源

每个候选项保存并展示来源信息：

- `source = "rule"`：确定性规则识别。
- `source = "ai_inferred"`：AI 补充识别。
- `source = "user_added"`：用户手动新增。

每个候选项也应有 `confidence`：

- `high`：规则强匹配，或 AI 判断为认证且字段名包含明显认证语义。
- `medium`：AI 判断可能是认证，但需要用户确认。
- `low`：保留给未来更宽松提示；第一版可以不默认展示低置信候选。

UI 默认选中 high/medium 候选。用户可以取消任何候选。

## 6. 发布流程

### 6.1 保存弹窗

API Monitor 页保存弹窗新增“认证鉴权参数”区域：

- 打开弹窗时，如果已有已采用工具，则调用后端 preview 接口，返回候选项。
- 每个候选项默认选中。
- 用户可以取消某个候选项。
- 用户可以编辑 `parameter_name` 和 `description`。
- 不允许编辑真实 value，因为真实 value 不会保存。

保存 payload 增加：

```json
{
  "mcp_name": "Example API MCP",
  "description": "Captured APIs",
  "confirm_overwrite": false,
  "auth_parameters": [
    {
      "enabled": true,
      "location": "header",
      "key": "Authorization",
      "parameter_name": "authorization",
      "description": "Authorization header for this API.",
      "source": "rule",
      "confidence": "high",
      "required": true
    }
  ]
}
```

### 6.2 后端校验

发布接口校验：

- `auth_parameters` 必须是 list。
- `location` 只能是 `header`、`query`、`body`。
- `key` 必须是非空字符串。
- `parameter_name` 必须符合 MCP tool 参数命名规则：`^[A-Za-z_][A-Za-z0-9_]*$`。
- 同一个 MCP 的认证参数 `parameter_name` 不能重复。
- `required` 固定为 true；第一版不暴露可选认证参数。
- `source` 只能是 `rule`、`ai_inferred`、`user_added`。
- `confidence` 只能是 `high`、`medium`、`low`。

如果前端不传 `auth_parameters`，后端可以重新分析并使用默认候选项，保证 API 兼容；但新 UI 应显式传用户确认后的列表。

### 6.3 覆盖保存

覆盖已有 API Monitor MCP 时：

- 替换 tools。
- 替换 `auth_parameters` 为本次用户确认的定义。
- 保留 `enabled`、`default_enabled`、`tool_policy` 等 MCP 可用性配置。
- 不保留旧的手工 `endpoint_config.headers` 和 `credential_binding` 作为 API Monitor 认证来源。

## 7. Discovery 和 Tool Schema

`ApiMonitorMcpRuntime.list_tools()` 和 `/mcp/servers/{server_key}/discover-tools` 返回工具 schema 时，需要把启用的认证参数合并到每个 tool 的 input schema。

合并规则：

1. 从 tool 自身 `input_schema.properties` 开始。
2. 对每个 enabled auth parameter 增加一个 property。
3. property 描述来自 `auth_parameters.description`。
4. property schema 至少为：

```json
{
  "type": "string",
  "description": "Authorization header for this API. Include the full value, for example Bearer <token>.",
  "x-api-monitor-auth": {
    "location": "header",
    "key": "Authorization"
  }
}
```

5. 把每个 enabled auth parameter 的 `parameter_name` 加入 `required`。
6. 如果 tool 自身已有 required，保留并追加认证参数。
7. 如果认证参数与业务参数冲突，保存时已经通过命名规则避免冲突。

Agent 看到的 schema 必须明确这些参数是调用 tool 的必填输入。

## 8. Runtime 调用

`ApiMonitorMcpRuntime.call_tool()` 调用流程调整为：

1. 加载 MCP server 的 `auth_parameters`。
2. 从 arguments 中分离 enabled auth parameter。
3. 如果缺少任一 required auth parameter，直接返回错误，不发起 HTTP 请求。
4. 用剩余 arguments 渲染 tool 的 path/query/header/body mappings。
5. 按 `auth_parameters.location` 注入认证参数：
   - `header`: `request_headers[key] = arguments[parameter_name]`
   - `query`: `request_query[key] = arguments[parameter_name]`
   - `body`: `request_body[key] = arguments[parameter_name]`
6. 发起真实 HTTP 请求。
7. `request_preview` 中继续脱敏认证 header/query/body。

认证参数不应该作为普通业务参数自动进入 query/body，否则会重复发送。

### 8.1 与现有 server headers/query 的关系

API Monitor MCP 不再使用工具库手工配置的 `credential_binding` 作为主要认证来源。

Runtime 可以继续保留兼容行为：

- 如果历史 API Monitor MCP 没有 `auth_parameters`，仍按现有 `server.headers` 和 server URL query 执行。
- 如果存在 `auth_parameters`，优先使用 arguments 注入认证；旧 server-level headers/query 只作为非认证兼容配置保留。

## 9. 编辑界面

`ApiMonitorMcpEditDialog.vue` 改造为：

- 保留基础信息：名称、描述。
- 移除：
  - HTTP Headers textarea
  - Credential Bindings
  - Query Parameters textarea
  - 认证相关 credential 选择逻辑
- 新增“认证鉴权参数”区域：
  - 展示 `location`
  - 展示 HTTP key
  - 展示 `parameter_name`
  - 展示 description
  - 展示来源：规则识别、AI 补充或用户新增
  - 展示置信度
  - 展示 required
  - 支持启用/禁用
  - 支持删除
  - 支持编辑 `parameter_name` 和 description

编辑页不展示真实认证值。

保存时只更新：

- `name`
- `description`
- `enabled/default_enabled` 如现有入口需要
- `auth_parameters`
- `timeout_ms` 如仍需要保留非认证运行参数

## 10. API 变更

建议新增或扩展：

### 10.1 Preview 候选认证项

```text
GET /api/v1/api-monitor/session/{session_id}/auth-candidates
```

返回：

```json
{
  "status": "success",
  "auth_parameters": []
}
```

该接口读取当前 session 和已采用 tools，返回默认候选项。

### 10.2 发布 MCP

扩展：

```text
POST /api/v1/api-monitor/session/{session_id}/publish-mcp
```

新增 body 字段：

- `auth_parameters`

### 10.3 API Monitor MCP 配置

扩展：

```text
PUT /api/v1/mcp/servers/{server_key}/api-monitor-config
```

新增可更新字段：

- `auth_parameters`

## 11. 测试策略

后端单元测试：

- 候选提取：从 Authorization、Cookie、X-CSRF-Token、access_token、clientSecret 中提取认证参数。
- AI 补充提取：规则未覆盖 `roma-csrf-token` 时，AI 输出该字段后能合并为候选项。
- AI 安全输入：发送给 AI 的请求上下文不包含真实 Authorization、Cookie、token、password、secret value。
- AI 输出校验：AI 返回不存在字段或噪声字段时被丢弃。
- AI 降级：AI 不可用或格式错误时，规则候选仍可正常返回。
- 噪声过滤：accept、origin、referer、sec-* 不生成候选项。
- 发布保存：只保存用户确认的 enabled auth parameters，不保存真实 value。
- 覆盖保存：替换旧 auth parameters。
- discovery：每个 API Monitor tool schema 都包含 required auth parameters。
- runtime：缺少认证参数时不发请求并返回错误。
- runtime：认证参数正确注入 header/query/body。
- runtime：request_preview 脱敏认证参数。
- 兼容：没有 auth_parameters 的历史 MCP 继续使用现有 server-level headers/query。

前端测试：

- API 类型包含 `auth_parameters`。
- 保存弹窗能展示候选认证项并提交用户选择。
- 编辑弹窗不再展示旧认证配置 UI。
- 编辑弹窗保存认证参数定义。

## 12. 迁移和兼容

已有 API Monitor MCP：

- 若没有 `auth_parameters`，仍按历史逻辑执行。
- 用户打开编辑界面时，看到“未保存认证参数”的空状态。
- 后续可提供“重新分析认证参数”的按钮，但第一版不要求实现。

普通 MCP：

- 不受影响。
- 继续使用现有 endpoint config、credential binding 和 credential vault。

## 13. 风险

1. 自动提取可能误判业务字段为认证字段。通过用户确认和可取消选择降低风险。
2. AI 可能把业务上下文字段误判为认证。AI 输出只能作为候选，必须经过字段存在性校验和用户确认。
3. AI 输入如果处理不当可能泄露敏感值。实现必须先脱敏再调用模型，并用测试覆盖。
4. 认证参数变成每次 tool 调用必填后，Agent 必须能拿到这些值；如果用户未提供，tool 调用会失败。失败信息应明确列出缺失参数名。
5. Cookie 可能很长。schema description 应提示传完整 Cookie header value，但 UI 不保存具体值。
6. 某些 API 的认证参数每个 endpoint 不同。第一版采用 MCP 级共享认证参数；如果后续遇到差异，再扩展 tool-level auth override。

## 14. 验收标准

- 用户在 API Monitor 页面保存 MCP 前能看到自动识别出的认证鉴权候选项。
- 自定义字段如 `roma-csrf-token` 即使不在固定规则中，也能通过 AI 补充候选展示给用户确认。
- 用户取消选择的候选项不会保存。
- 保存后的 API Monitor MCP tool schema 中包含已启用认证参数，且这些参数是 required。
- Agent 调用 tool 时缺少认证参数会收到明确错误，且不会发起真实 HTTP 请求。
- Agent 传入认证参数后，runtime 将其注入到正确的 header/query/body。
- 工具库 API Monitor MCP 编辑界面不再展示旧的认证配置界面，而是展示保存的认证参数定义。
- 全程不保存捕获到的真实认证值。
