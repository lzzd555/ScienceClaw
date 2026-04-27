# API Monitor MCP 动态 Token 流量追踪设计

## 1. 背景

API Monitor MCP 当前已经能通过凭证配置解决一部分登录问题：用户在凭证库中保存账号密码或其他长期凭证，API Monitor MCP 保存凭证引用，runtime 根据凭证类型执行认证逻辑。

但许多网页 API 除了登录态之外，还需要短期动态 token，例如：

- CSRF token / XSRF token
- nonce
- 表单隐藏字段 `_csrf`
- 页面 meta 中的 `csrf-token`
- cookie 中的 `XSRF-TOKEN`
- 登录后由某个 bootstrap/session/config 接口返回的临时 token

这些值有两个特点：

1. 它们通常不是用户应该手动填写的长期凭证。
2. 它们必须从一次真实页面或 API 响应中提取，并在后续请求的 header、query、body 或 cookie 中使用。

如果只让 AI 判断“哪个接口返回 token、哪个请求后续用了 token”，可靠性不足。AI 可以识别字段名像 token，但很难证明该 token 后续到底注入到哪里。因此本设计将动态 token 识别改为确定性的流量值追踪：系统观察实际 token 值在请求/响应时间线中的出现和复用，建立 producer -> consumer 依赖图。

## 2. 目标

本设计完成后，API Monitor MCP 应支持：

1. 采集阶段从 response headers、response body、HTML、cookies、DOM 和 storage 中提取候选 token source。
2. 采集阶段扫描后续 request headers、query、body 和 cookies，找到候选 token 的实际使用位置。
3. 根据真实值匹配建立 token producer -> consumer 关系，而不是只靠字段名或 AI 语义判断。
4. 发布 MCP 时把高置信 token 关系保存为 `auth_flow` 规则，不保存真实 token 明文。
5. MCP runtime 调用 tool 前自动执行 token setup/extract，并把新 token 注入目标请求。
6. 当目标请求返回常见认证失败状态时，自动刷新 token 并重试一次。
7. UI 展示“系统检测到动态 token 流程”，允许用户确认或关闭。

## 3. 非目标

本设计不做：

- 不保存捕获到的真实 token、cookie、password、secret 或 API key 明文。
- 不让 Agent 在 tool arguments 中手动传入 CSRF token。
- 不依赖 AI 自动决定最终 token 绑定关系。
- 不实现完整 OAuth authorization code / refresh token 协议。
- 不破解前端加密、签名或混淆 JS 算法。
- 不保证能识别 token 值被 hash、加密、拼接或签名后的派生使用方式。
- 不改变普通 MCP server 的认证配置方式。

## 4. 核心概念

### 4.1 长期凭证

长期凭证由现有 credential vault 管理，例如用户名密码、API key、refresh token。API Monitor MCP 只保存 `credential_id` 引用。

### 4.2 动态会话材料

动态会话材料由 API Monitor runtime 自动获取和维护，例如 cookie、csrf token、nonce。它们不进入 Agent tool input schema，也不要求用户手填。

### 4.3 Token Producer

Producer 表示“某个响应或浏览器状态位置产生了一个候选 token”。

示例：

```json
{
  "id": "producer_1",
  "source_call_id": "call_bootstrap",
  "method": "GET",
  "url_pattern": "/api/session",
  "location": "response.body",
  "path": "$.csrfToken",
  "name": "csrfToken",
  "value_hash": "sha256:...",
  "signals": ["csrf-name", "high-entropy", "same-origin"]
}
```

### 4.4 Token Consumer

Consumer 表示“某个后续请求在某个位置使用了该 token”。

示例：

```json
{
  "id": "consumer_1",
  "target_call_id": "call_create_order",
  "method": "POST",
  "url_pattern": "/api/orders",
  "location": "request.headers",
  "path": "X-CSRF-Token",
  "value_hash": "sha256:..."
}
```

### 4.5 Token Flow

Token flow 是 producer 和 consumer 的绑定关系。

示例：

```json
{
  "id": "flow_1",
  "token_name": "csrf_token",
  "producer": {
    "method": "GET",
    "url": "/api/session",
    "extract": {
      "from": "response.body",
      "path": "$.csrfToken"
    }
  },
  "consumers": [
    {
      "method": "POST",
      "url": "/api/orders",
      "inject": {
        "to": "request.headers",
        "name": "X-CSRF-Token"
      }
    }
  ],
  "confidence": "high",
  "reasons": ["exact-value-match", "csrf-name", "producer-before-consumer"]
}
```

## 5. 采集阶段数据扩展

当前 `CapturedRequest` 和 `CapturedResponse` 已记录请求、响应和 headers/body。为了支持动态 token 追踪，需要在 session 内维护一个临时分析结果，建议不要直接把真实 token 明文持久化到 MongoDB。

### 5.1 临时内存数据

采集时可在 API Monitor session manager 或单独 analyzer 中维护：

```python
class TokenCandidate:
    id: str
    value: str                 # 仅内存短期使用
    value_hash: str
    normalized_values: list[str]
    source_call_id: str
    source_kind: str           # response_body, response_header, set_cookie, html_meta, dom, storage
    source_path: str
    field_name: str
    timestamp: datetime
    signals: list[str]
```

明文 `value` 只用于当前采集会话内匹配，不写入持久化文档。

### 5.2 持久化摘要

如果需要在 session 文档或调试视图中保存分析结果，只保存：

```json
{
  "token_flow_profile": {
    "flows": [
      {
        "token_name": "csrf_token",
        "producer_summary": "GET /api/session response.body.$.csrfToken",
        "consumer_summaries": ["POST /api/orders request.headers.X-CSRF-Token"],
        "confidence": "high",
        "reasons": ["exact-value-match", "csrf-name"]
      }
    ]
  }
}
```

该 profile 不包含真实 token value。

## 6. Producer 发现规则

Producer 发现器从以下来源提取候选 token。

### 6.1 Response Headers

扫描响应 headers：

- `x-csrf-token`
- `x-xsrf-token`
- `csrf-token`
- `x-request-token`
- 包含 `csrf`、`xsrf`、`nonce`、`token`、`session`、`signature` 的 header

### 6.2 Set-Cookie

解析 `set-cookie`：

- `csrftoken`
- `csrf_token`
- `XSRF-TOKEN`
- `CSRF-TOKEN`
- 包含 `csrf`、`xsrf`、`token`、`session` 的 cookie name

对 cookie token 要记录 cookie name 和 cookie domain/path。后续 consumer 可能是 cookie 自动携带，也可能是 cookie-to-header。

### 6.3 JSON Response Body

递归扫描 JSON response body，记录字段路径：

- 字段名包含 `csrf`、`xsrf`、`nonce`、`token`、`signature`
- 字段值是字符串
- 字符串长度和熵符合 token 特征

示例：

```json
{
  "csrfToken": "abc123",
  "data": {
    "nonce": "n-456"
  }
}
```

生成：

```text
response.body.$.csrfToken
response.body.$.data.nonce
```

### 6.4 HTML Response Body

当请求类型是 document，或 response content-type 是 HTML 时，解析：

- `<meta name="csrf-token" content="...">`
- `<meta name="csrf-param" content="...">`
- `<input type="hidden" name="_csrf" value="...">`
- `<input type="hidden" name="authenticity_token" value="...">`

当前 `should_capture()` 只抓 xhr/fetch，不抓 document。为了覆盖 HTML token，需要补充以下任一方案：

1. 对主文档 response 做轻量 token 扫描，但不把 document 作为普通 API tool 候选。
2. 在页面加载后执行 DOM 快照扫描，提取 meta 和 hidden input。

第一版建议同时做 DOM 快照扫描，因为它不会改变现有 API 捕获过滤逻辑。

### 6.5 Browser Storage

在关键时点扫描：

- `localStorage`
- `sessionStorage`

候选 key 包含 `csrf`、`xsrf`、`nonce`、`token`、`session` 时记录。

时点包括：

- 初始页面加载完成后
- 登录完成后
- 每次捕获到 API response 后
- 用户停止录制前

## 7. Consumer 发现规则

Consumer 发现器扫描后续请求中的所有可能注入位置。

### 7.1 Request Headers

扫描 request headers：

- 精确匹配候选 token 值
- URL decode 后匹配
- 对 cookie-to-header 场景，允许 cookie 值和 header 值匹配

示例：

```text
X-CSRF-Token: abc123
X-XSRF-TOKEN: abc123
```

### 7.2 Query Parameters

解析 query string，扫描所有 key/value：

```text
POST /api/orders?csrf_token=abc123
```

记录 consumer：

```text
request.query.csrf_token
```

### 7.3 Request Body

按 content type 解析：

- `application/json`: 递归扫描 JSON 字段
- `application/x-www-form-urlencoded`: 解析表单字段
- `multipart/form-data`: 扫描文本字段，不扫描文件内容
- 其他类型：第一版只做保守字符串扫描，不自动生成高置信 flow

示例：

```json
{
  "_csrf": "abc123",
  "name": "order"
}
```

记录 consumer：

```text
request.body.$._csrf
```

### 7.4 Cookies

如果 producer 来自 `Set-Cookie`，而后续 request cookie header 自动带上同名 cookie，则这是浏览器会话 cookie，不一定需要显式 injection。

两种情况区别处理：

1. Cookie-only flow：runtime 只需要维护同一个 cookie jar。
2. Cookie-to-header flow：runtime 需要从 cookie jar 读取 cookie 值，并注入到 header。

示例：

```text
Set-Cookie: XSRF-TOKEN=abc123
Request Header: X-XSRF-TOKEN: abc123
```

应生成：

```yaml
extract:
  from: cookie
  name: XSRF-TOKEN
inject:
  headers:
    X-XSRF-TOKEN: "{{ xsrf_token }}"
```

## 8. 值匹配算法

### 8.1 时间线约束

只有 source 先出现、consumer 后出现，才建立 producer -> consumer 关系。

```text
producer.timestamp < consumer.request.timestamp
```

### 8.2 精确匹配

第一优先级是精确值匹配：

```text
candidate.value == request_value
```

这是高置信绑定的核心证据。

### 8.3 规范化匹配

为了覆盖常见编码差异，候选值和请求值都生成规范化集合：

- 原始值
- URL decode 值
- HTML entity decode 值
- 去除引号后的值
- 对 cookie value 做 URL decode

任一规范化值匹配即可建立中高置信关系。

### 8.4 派生值限制

第一版不自动匹配以下派生关系：

- hash(token)
- HMAC(token + body)
- base64(JSON 包装 token)
- token 拼接 timestamp 后签名
- 前端 JS 加密后的值

这些只能生成低置信提示，要求用户手动配置或回退到浏览器会话执行。

### 8.5 熵和长度过滤

为了避免把普通业务字段误判为 token，候选值需满足基本过滤：

- 字符串长度大于等于 8，特殊框架 token 可放宽。
- 不是纯数字短 ID。
- 不是明显枚举值，例如 `true`、`false`、`active`、`success`。
- 字符集有一定复杂度，或字段名强烈命中 csrf/xsrf/nonce。

## 9. 置信度模型

### 9.1 高置信

满足：

- producer 和 consumer 真实值精确匹配。
- producer 出现在 consumer 之前。
- 同 origin 或明确属于同一站点 API。
- 字段名命中 csrf/xsrf/nonce/token，或同一 flow 在多次录制中重复出现。

### 9.2 中置信

满足：

- 规范化后匹配。
- cookie-to-header 匹配。
- producer 字段名强命中，但只观察到一次 consumer。

### 9.3 低置信

只满足：

- 字段名像 token，但没有观察到值流向。
- token 可能被派生、签名或加密。
- 匹配值太短或太常见。

低置信 flow 不应默认保存为自动 auth_flow，只能在 UI 中提示用户确认。

## 10. MCP 发布模型

发布 API Monitor MCP 时，后端将高置信或用户确认的 flow 保存为 server 级配置，建议字段为：

```json
{
  "api_monitor_auth": {
    "credential_type": "placeholder",
    "credential_id": "cred_abc123",
    "token_flows": [
      {
        "name": "csrf_token",
        "setup": [
          {
            "method": "GET",
            "url": "/api/session",
            "extract": {
              "from": "response.body",
              "path": "$.csrfToken"
            }
          }
        ],
        "inject": {
          "headers": {
            "X-CSRF-Token": "{{ csrf_token }}"
          }
        },
        "applies_to": [
          {
            "method": "POST",
            "url": "/api/orders"
          }
        ],
        "refresh_on_status": [401, 403, 419],
        "confidence": "high"
      }
    ]
  }
}
```

说明：

- `credential_type` 继续表示长期凭证处理类型。
- `token_flows` 表示动态会话材料获取和注入规则。
- `setup` 可以包含多个请求，但第一版优先支持单步 GET/POST token 获取。
- `applies_to` 限制该 flow 只注入到观察到需要 token 的工具请求，避免污染所有 API。
- 不保存任何 token 明文。

## 11. Runtime 执行流程

`ApiMonitorMcpRuntime.call_tool()` 当前是一次性渲染并发请求。支持 token flow 后，runtime 需要有一个 API Monitor 专属的 auth/session runner。

流程：

1. 加载 server 的 `api_monitor_auth` 和 tool contract。
2. 创建或复用带 cookie jar 的 `httpx.AsyncClient`。
3. 应用长期凭证逻辑，例如登录、Bearer、API key、cookie credential。
4. 判断目标 tool 是否命中某个 `token_flows[].applies_to`。
5. 如果命中且 token 缓存不存在或过期，执行 `setup` 请求。
6. 从 setup response 中按 extract 规则提取 token。
7. 将 token 放入短期内存缓存。
8. 渲染业务请求 path/query/header/body。
9. 按 inject 规则注入 token。
10. 发起目标请求。
11. 如果返回 `refresh_on_status`，清理 token 缓存，重新执行 setup，并重试一次。
12. 返回响应和脱敏 request preview。

Preview 示例：

```json
{
  "request_preview": {
    "auth": {
      "credential_type": "placeholder",
      "credential_configured": true,
      "token_flows": [
        {
          "name": "csrf_token",
          "applied": true,
          "source": "GET /api/session response.body.$.csrfToken",
          "injected": ["headers.X-CSRF-Token"]
        }
      ]
    }
  }
}
```

Preview 不能展示 token 明文。

## 12. 运行时缓存策略

第一版可以使用进程内短期缓存：

```text
cache key = user_id + mcp_server_id + origin + credential_id
```

缓存内容：

- cookie jar
- extracted token values
- token flow timestamps

失效条件：

- 认证失败状态：401、403、419
- token flow 配置变化
- credential_id 变化
- 超过默认 TTL，例如 15 分钟

后续如果需要跨进程共享，可再引入 Redis；第一版不要求。

## 13. UI 设计

### 13.1 保存 MCP 弹窗

保存 MCP 时展示动态 token 识别结果：

```text
检测到动态 token 流程

csrf_token
来源: GET /api/session -> response.body.$.csrfToken
用途: POST /api/orders -> headers.X-CSRF-Token
置信度: 高
```

用户操作：

- 高置信 flow 默认启用。
- 中置信 flow 默认启用或要求确认，可根据产品取舍。
- 低置信 flow 默认关闭，只展示建议。
- 用户可以展开查看命中原因，但看不到真实 token。

### 13.2 工具库详情页

API Monitor MCP 详情页展示：

- 长期凭证配置状态。
- 动态 token flow 数量。
- 每个 flow 的 source 和 inject 位置。
- 最近一次 runtime 是否成功应用 flow。

## 14. 后端模块建议

新增模块：

```text
RpaClaw/backend/rpa/api_monitor_token_flow.py
```

职责：

- 从 captured calls 和页面状态生成 token candidates。
- 建立 producer/consumer 匹配关系。
- 计算 confidence。
- 输出脱敏 `token_flow_profile`。
- 将确认后的 profile 转成可持久化 `token_flows`。

新增或扩展模块：

```text
RpaClaw/backend/rpa/api_monitor_auth.py
```

职责：

- 在现有 credential auth 分派基础上，增加 token flow runtime 应用。
- 提供 `apply_api_monitor_token_flows_to_request()` 或专门 runner。

扩展 runtime：

```text
RpaClaw/backend/deepagent/mcp_runtime.py
```

职责：

- `ApiMonitorMcpRuntime.call_tool()` 在发请求前调用 token flow runner。
- request preview 脱敏展示 token flow 应用状态。

扩展模型：

```text
RpaClaw/backend/rpa/api_monitor/models.py
RpaClaw/backend/mcp/models.py
```

职责：

- API Monitor publish payload 支持 token flow confirmation。
- `McpServerDefinition` 携带 `api_monitor_auth.token_flows`。

## 15. API 设计

### 15.1 预览 token flow

```text
GET /api/v1/api-monitor/session/{session_id}/token-flow-profile
```

返回：

```json
{
  "status": "success",
  "profile": {
    "flows": [
      {
        "id": "flow_1",
        "name": "csrf_token",
        "producer_summary": "GET /api/session response.body.$.csrfToken",
        "consumer_summaries": ["POST /api/orders request.headers.X-CSRF-Token"],
        "confidence": "high",
        "enabled_by_default": true,
        "reasons": ["exact-value-match", "csrf-name"]
      }
    ]
  }
}
```

### 15.2 发布 MCP

扩展现有 publish payload：

```json
{
  "mcp_name": "Orders API MCP",
  "description": "Captured order APIs",
  "api_monitor_auth": {
    "credential_type": "placeholder",
    "credential_id": "cred_abc123",
    "token_flows": [
      {
        "id": "flow_1",
        "enabled": true
      }
    ]
  }
}
```

后端根据 session 内 profile 将 enabled flow 转为持久化 runtime config，避免前端提交完整 extract/inject 细节被篡改。

## 16. 安全要求

- 真实 token 明文只允许在当前采集会话内存和 runtime 短期内存中存在。
- 持久化文档只保存 extract/inject 规则、hash、摘要和脱敏示例。
- 日志不能输出 token value、cookie value、Authorization value。
- request preview 必须复用现有脱敏逻辑，并覆盖 token flow 注入内容。
- AI 补充识别输入不能包含真实 token 明文。
- 低置信 flow 不自动启用。

## 17. 测试计划

### 17.1 Token Flow Analyzer 单元测试

覆盖：

- response JSON `csrfToken` -> request header `X-CSRF-Token`
- `Set-Cookie: XSRF-TOKEN` -> request header `X-XSRF-TOKEN`
- HTML meta csrf -> request body `_csrf`
- query token consumer
- form-urlencoded body consumer
- 短 ID、枚举值、普通业务字段不被识别为 token
- token value 不出现在 profile 序列化结果中

### 17.2 Runtime 单元测试

覆盖：

- 调用目标 tool 前先执行 setup request。
- 从 setup response body 提取 token 并注入 header。
- cookie jar 在 setup 和目标请求之间复用。
- 401/403/419 后刷新 token 并重试一次。
- token flow preview 脱敏。
- token setup 失败时返回结构化错误，不发目标请求。

### 17.3 API 测试

覆盖：

- `token-flow-profile` 只返回脱敏摘要。
- publish 只接受 session 中存在的 flow id。
- publish 不保存 token value。
- 工具库详情接口返回 token flow 配置摘要。

## 18. 分阶段实现

### Phase 1: 采集期分析和 UI 预览

- 增加 token flow analyzer。
- 增加 `token-flow-profile` 接口。
- 保存弹窗展示脱敏 flow 结果。
- 不改变 runtime。

### Phase 2: 发布和持久化

- publish payload 支持确认 flow。
- server 文档保存 `api_monitor_auth.token_flows`。
- 工具库详情展示 token flow 配置。

### Phase 3: Runtime 执行

- API Monitor runtime 支持 setup/extract/inject。
- 加入 cookie jar 和短期 token 缓存。
- 支持失败刷新和一次重试。

### Phase 4: 覆盖更多来源

- DOM meta / hidden input 扫描。
- localStorage/sessionStorage 扫描。
- 多步 token setup。
- 中低置信 flow 用户确认流程。

## 19. 风险和边界

### 19.1 可以可靠支持

- 响应 JSON 返回 token，后续 header/query/body 使用同值。
- cookie 返回 XSRF token，后续 header 使用同值。
- HTML meta 或 hidden input 暴露 token，后续请求使用同值。
- 常见 CSRF/XSRF 框架。

### 19.2 只能部分支持

- token 名字不明显，但值流向明确。这可以靠精确匹配解决，但命名可能需要 AI 或规则辅助。
- token 来自 document HTML，但当前采集没有 document response。需要 DOM 快照补足。

### 19.3 不可靠或不支持

- token 被前端 JS 加密、hash 或签名后使用。
- token 每次请求前由复杂 JS 算法生成。
- token 只存在于 WebAssembly 或高度混淆代码内部。
- token 和业务字段值相同或太短，导致无法区分。

这些场景应回退到浏览器会话执行、用户手动配置，或后续单独设计“前端签名函数录制/复用”能力。

## 20. 推荐决策

采用“确定性值流追踪 + 用户确认 + runtime 状态机”的方案。

理由：

- 它能回答两个关键问题：token 从哪里来，以及后续用在哪里。
- producer/consumer 关系由真实值匹配证明，AI 不参与最终裁决。
- 不把动态 token 暴露给 Agent 或用户手填。
- 和当前 API Monitor MCP credential auth 模型兼容：长期凭证负责登录，token flow 负责短期会话材料。
- 可以分阶段落地，先做 profile，再做发布，最后做 runtime。
