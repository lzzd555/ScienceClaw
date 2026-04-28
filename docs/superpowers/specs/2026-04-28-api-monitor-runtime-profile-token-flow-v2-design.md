# API Monitor Runtime Profile Token Flow V2 设计规格

状态：草案，等待人工评审  
日期：2026-04-28  
适用范围：API Monitor MCP 发布、配置和 runtime 调用

## 1. 背景

API Monitor MCP 已经支持通过凭证解决一部分登录问题。用户可以在凭证库中保存账号密码或测试凭证，发布 API Monitor MCP 时绑定凭证，runtime 调用 tool 时再执行认证。

当前问题不在“是否能登录”，而在“登录态、动态 token 和目标请求没有共享同一个运行期上下文”。实际测试中已经出现两个典型失败：

1. token flow setup 使用相对 URL 时找不到 base URL。
2. 修复 base URL 后，目标接口仍返回 `403`，错误为“无效或缺失的 CSRF Token”。request preview 只显示 `Authorization`，没有显示 `token_flows`，说明 runtime 只注入了认证 header，没有正确完成 token producer 到 token consumer 的链路。

这暴露出旧模型的结构性缺陷。旧模型把 token flow 当成目标请求前的一段孤立 preflight：

```text
credential auth -> token setup -> extract token -> inject target request
```

真实网页 API 的顺序应该是：

```text
创建 runtime profile
  -> 登录并把 auth token、cookie、headers 写入 profile
  -> 使用 profile 请求 token producer
  -> 把 producer 提取出的 csrf、nonce 等变量写回 profile
  -> 渲染目标 API 请求参数
  -> 使用 token consumer 规则从 profile 注入动态 token
  -> 使用同一个 profile 的 client、cookie、headers 发起目标请求
```

因此，V2 的核心不是再补一个 token setup 参数，而是引入 runtime profile，让登录、producer 和目标 consumer 共享同一份运行期状态。

## 2. 目标

V2 需要实现以下能力：

1. 每次 API Monitor MCP tool 调用创建一个 runtime profile，profile 负责保存本次调用内的认证材料、cookie、headers 和动态变量。
2. credential auth 不再只返回一组 headers，而是先写入 runtime profile。
3. token producer 请求必须使用 runtime profile 中已有的认证 headers、cookie 和变量。
4. token producer 提取出的动态 token 必须写回同一个 runtime profile。
5. 目标请求必须同时应用 runtime profile 中的 auth 材料和匹配到的 token consumer 注入规则。
6. 同一个 token 可以被多个 consumer 使用，runtime 必须根据目标请求只注入当前匹配的 consumer，同时保留完整 preview。
7. 自动 token flow 生成阶段必须对重复录制的相同接口去重，不能把同一个 consumer 重复展示或重复保存。
8. 用户必须能够人工配置 token flow，并能在发布后继续编辑。
9. AI 可以辅助命名、摘要和候选解释，但不能作为最终绑定 producer/consumer 的唯一依据。
10. preview、持久化配置和日志不得泄漏真实 token、cookie、password、API key 或 session secret 明文。

## 3. 非目标

V2 不解决以下问题：

- 不实现完整 OAuth authorization code、PKCE 或 refresh token 协议。
- 不破解前端加密、签名、混淆算法，无法推导由 token 派生出的签名值。
- 不让 Agent 在 tool input schema 中手动填写 CSRF token。
- 不把捕获到的真实 token 明文保存到 MCP 配置、MongoDB 或日志。
- 不尝试让 AI 自动决定最终安全策略。
- 不重写 API Monitor 的录制 UI 和基础请求抽取能力。
- 不改变普通非 API Monitor MCP server 的调用方式。

## 4. 核心概念

### 4.1 Runtime Profile

Runtime profile 是一次 API Monitor MCP tool 调用内的临时状态对象。它只在 runtime 内存中存在，不作为长期配置持久化。

profile 持有：

- `base_url`：当前 API Monitor MCP 的请求基准 URL。
- `client`：同一次调用共享的 `httpx.AsyncClient`，用于保持 cookie jar。
- `headers`：认证和运行期默认 headers，例如 `Authorization`。
- `cookies`：由 client cookie jar 维护。
- `variables`：运行期变量，例如 `auth_token`、`csrf_token`。
- `secret_names`：需要脱敏的变量和 header 名称。
- `events`：用于 preview 的非敏感执行摘要，例如 producer 是否执行、consumer 是否注入。

profile 的生命周期：

```text
tool call start -> 创建 profile -> 登录 -> producer -> consumer -> target request -> 生成 preview -> 释放 profile
```

### 4.2 Credential Auth

Credential auth 是长期凭证到运行期认证材料的转换。它可以读取 credential vault 中的账号密码、token 或测试凭证，但写入 runtime profile 的内容必须是本次调用使用的运行期材料。

例如登录接口返回：

```json
{
  "token": "eyJhbGciOi..."
}
```

profile 写入：

```json
{
  "headers": {
    "Authorization": "Bearer {{ auth_token }}"
  },
  "variables": {
    "auth_token": "eyJhbGciOi..."
  }
}
```

preview 只能显示：

```json
{
  "headers": ["Authorization"],
  "variables": ["auth_token"],
  "cookies": true
}
```

### 4.3 Token Producer

Token producer 是“产生动态 token 的请求或浏览器状态位置”。V2 runtime 第一阶段只要求支持 HTTP producer 请求。

示例：

```json
{
  "request": {
    "method": "GET",
    "url": "/api/session",
    "headers": {},
    "query": {},
    "body": null,
    "content_type": ""
  },
  "extract": [
    {
      "name": "csrf_token",
      "from": "response.body",
      "path": "$.csrfToken",
      "secret": true
    }
  ]
}
```

producer 请求必须使用 runtime profile：

- 默认带上 profile headers。
- 默认复用 profile client 的 cookies。
- producer 自己配置的 headers、query、body 可以引用 profile variables。
- producer 提取出的变量写回 profile。

### 4.4 Token Consumer

Token consumer 是“某个目标请求需要把 profile 中的动态变量注入到哪里”。

示例：

```json
{
  "method": "GET",
  "url": "/api/orders",
  "inject": {
    "headers": {
      "X-CSRF-Token": "{{ csrf_token }}"
    },
    "query": {},
    "body": {}
  }
}
```

consumer 只描述注入规则，不保存真实 token 值。

### 4.5 Token Flow

Token flow 是 producer 和一个或多个 consumers 的绑定关系。

一个 token flow 可以对应多个 consumer。例如同一个 `csrf_token` 同时被下面接口使用：

- `GET /api/orders`
- `POST /api/orders`
- `DELETE /api/orders/:id`

V2 将它们保存为同一个 flow 的多个 consumers，而不是保存多份重复 producer。

## 5. V2 持久化数据结构

MCP server 的 `api_monitor_auth.token_flows` 保存 V2 flows。该配置只保存规则，不保存 token 明文。

```json
{
  "id": "flow_csrf_orders",
  "name": "csrf_token",
  "source": "auto",
  "enabled": true,
  "producer": {
    "request": {
      "method": "GET",
      "url": "/api/session",
      "headers": {},
      "query": {},
      "body": null,
      "content_type": ""
    },
    "extract": [
      {
        "name": "csrf_token",
        "from": "response.body",
        "path": "$.csrfToken",
        "secret": true
      }
    ]
  },
  "consumers": [
    {
      "method": "GET",
      "url": "/api/orders",
      "inject": {
        "headers": {
          "X-CSRF-Token": "{{ csrf_token }}"
        },
        "query": {},
        "body": {}
      }
    }
  ],
  "refresh_on_status": [401, 403, 419],
  "confidence": "high",
  "summary": {
    "producer": "GET /api/session response.body.$.csrfToken",
    "consumers": ["GET /api/orders request.headers.X-CSRF-Token"],
    "sample_count": 2,
    "source_call_ids": ["call_1", "call_7"],
    "reasons": ["exact-value-match", "producer-before-consumer", "same-origin"]
  }
}
```

字段约束：

- `id`：稳定 ID，用于编辑和 preview。
- `name`：人可读名称，默认使用 extract name。
- `source`：`auto` 或 `manual`。
- `enabled`：关闭后 runtime 不执行该 flow。
- `producer.request.method`：HTTP method，统一大写。
- `producer.request.url`：绝对 URL 或相对 URL；相对 URL 以 API Monitor MCP 的 request base URL 解析。
- `producer.extract`：至少一条提取规则。
- `consumers`：至少一条 consumer 规则。
- `refresh_on_status`：目标请求返回这些状态码时，允许刷新 producer 并重试一次。
- `confidence`：`high`、`medium`、`low` 或 `manual`。
- `summary`：只保存摘要、样本数和原因，不保存真实 token 值。

## 6. Runtime 执行顺序

每次 API Monitor MCP tool 调用必须按以下顺序执行。

### 6.1 创建 Profile

runtime 从 MCP server 配置和 tool doc 计算 request base URL：

1. 优先使用 API Monitor MCP server 配置的 base URL。
2. 如果 server base URL 为空，使用 tool doc 中的 base URL。
3. 如果目标 URL 和 producer URL 都是绝对 URL，可以不要求 base URL。
4. 如果存在相对 URL 且无法解析 base URL，返回明确错误。

### 6.2 应用 Credential Auth

runtime 调用 profile-based auth：

```text
apply_api_monitor_auth_to_profile(server, tool_doc, credential, profile)
```

该步骤负责：

- 请求登录接口。
- 把登录接口产生的 token 写入 profile variables。
- 把认证 header 写入 profile headers。
- 保留登录接口设置的 cookies。
- 生成脱敏 preview。

如果认证失败，runtime 直接返回认证错误，不继续 producer 和 target request。

### 6.3 匹配 Token Flows

runtime 从 `api_monitor_auth.token_flows` 中筛选当前目标请求需要的 flows。

匹配规则：

- flow 必须 `enabled=true`。
- 至少一个 consumer 的 method 与目标 method 一致。
- consumer URL 与目标 URL 规范化后匹配。
- 目标 URL 可以是绝对 URL，consumer URL 可以是相对 URL，比较时统一解析到同一 origin 或统一比较 path。

### 6.4 执行 Producer

对匹配到的每个 flow，runtime 执行 producer：

1. 使用 profile client 发请求。
2. headers = profile headers + producer headers。
3. query、body、headers 中的 `{{ variable }}` 从 profile variables 渲染。
4. 从响应中按 `extract` 提取值。
5. 提取成功后写入 profile variables。
6. 提取失败时返回明确错误，指出 flow id、producer URL 和 extract path。

producer HTTP 状态码为 `4xx` 或 `5xx` 时，runtime 返回错误，不继续调用目标请求。

### 6.5 渲染目标请求

runtime 使用 tool arguments 渲染目标请求的 path、query、headers 和 body。

目标请求 headers 的初始值：

```text
profile headers + tool doc headers + argument-rendered headers
```

后者覆盖前者。覆盖只允许在同名 header 上发生，preview 必须记录覆盖后的 header 名称，不显示值。

### 6.6 应用 Consumer

runtime 对匹配到的 flows 执行 consumer 注入：

- 只对当前目标请求匹配的 consumer 注入。
- 同一个 flow 有多个 consumers 时，只执行 method 和 URL 匹配当前目标请求的那一条或多条。
- 不匹配当前目标请求的 consumers 不注入，但保留在配置和详情中。
- 注入位置支持 `headers`、`query`、`body`。
- 注入值通过 profile variables 渲染。
- 注入后 preview 记录 `headers.X-CSRF-Token`、`query.csrf`、`body._csrf` 等路径。

### 6.7 发送目标请求

runtime 使用同一个 profile client 发送目标请求，确保 producer 和目标请求共享 cookies。

如果目标请求返回 `refresh_on_status` 中的状态码：

1. 重新执行匹配 flows 的 producer。
2. 重新应用 consumers。
3. 重试目标请求一次。
4. 第二次仍失败时返回响应，不继续重试。

## 7. 多个 Consumer 使用同一 Token 的处理

V2 明确支持“一个 producer 提供一个 token，多个接口复用该 token”。

保存方式：

```json
{
  "id": "flow_csrf_shared",
  "name": "csrf_token",
  "producer": {
    "request": {"method": "GET", "url": "/api/session"},
    "extract": [{"name": "csrf_token", "from": "response.body", "path": "$.csrfToken"}]
  },
  "consumers": [
    {
      "method": "GET",
      "url": "/api/orders",
      "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}}
    },
    {
      "method": "POST",
      "url": "/api/orders",
      "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}}
    }
  ]
}
```

runtime 行为：

- 调用 `GET /api/orders` 时，只应用 `GET /api/orders` consumer。
- 调用 `POST /api/orders` 时，只应用 `POST /api/orders` consumer。
- producer 可以在每次 tool call 中执行一次，并把 token 存入 profile。
- 如果同一次 tool call 有多个匹配 flows 引用同名变量，后执行的 producer 不能静默覆盖已有 secret 变量；runtime 必须返回冲突错误，除非两个 extract path 属于同一个 flow。

preview 示例：

```json
{
  "token_flows": [
    {
      "id": "flow_csrf_shared",
      "name": "csrf_token",
      "producer_applied": true,
      "consumer_applied": true,
      "matched_consumer": "GET /api/orders",
      "injected": ["headers.X-CSRF-Token"]
    }
  ]
}
```

## 8. 自动识别与去重

旧版 token flow 设计中的“值流追踪”仍然成立：系统通过真实值在响应和后续请求中的出现关系建立 producer -> consumer，而不是仅靠字段名。

V2 在发布前必须对重复录制结果去重。

### 8.1 Producer 去重

producer 去重 key：

```text
normalized_method
normalized_url
extract.from
extract.path
extract.name
```

如果多个录制样本得到同一个 producer，保留一个 producer，`summary.sample_count` 累加，`summary.source_call_ids` 保留来源调用 ID。

### 8.2 Consumer 去重

consumer 去重 key：

```text
normalized_method
normalized_url
inject_location
inject_path
render_template
```

如果同一个接口被录制多次，只展示并保存一条 consumer。

### 8.3 合并规则

当多个 consumers 使用同一个 producer extract 时，合并为同一个 flow 的 `consumers` 数组。

当 producer 不同但 consumer 相同，系统保留多个 flow，并在 UI 中提示它们会同时匹配当前接口。用户可以关闭低置信 flow 或改为人工配置。

### 8.4 候选置信度

置信度由确定性信号计算：

- producer 值在后续 request 中精确出现。
- producer 时间早于 consumer。
- producer 和 consumer 同 origin。
- 字段名包含 `csrf`、`xsrf`、`nonce`、`token` 等语义。
- 值满足高熵动态值特征。
- 多次录制中出现同一 producer -> consumer 关系。

AI 不参与置信度的最终数值计算。AI 可以生成解释文案和推荐名称，但不能把低证据关系提升为高置信。

## 9. 人工配置

自动识别可能漏掉以下情况：

- token 字段名无语义，例如 `r`、`guard`、`state`。
- token 使用位置经过轻微变换。
- 录制过程中 producer 出现了，但目标 consumer 没有被捕获。
- 系统只录到了目标接口，没有录到 bootstrap/session 接口。

因此 V2 必须提供人工配置路径。

### 9.1 发布时人工配置

发布 API Monitor MCP 时，用户可以在自动候选之外添加 manual token flow。

manual flow 使用与 V2 持久化结构相同的 schema：

```json
{
  "id": "manual_csrf",
  "name": "csrf_token",
  "source": "manual",
  "enabled": true,
  "producer": {
    "request": {
      "method": "GET",
      "url": "/api/session"
    },
    "extract": [
      {
        "name": "csrf_token",
        "from": "response.body",
        "path": "$.csrfToken",
        "secret": true
      }
    ]
  },
  "consumers": [
    {
      "method": "GET",
      "url": "/api/orders",
      "inject": {
        "headers": {
          "X-CSRF-Token": "{{ csrf_token }}"
        }
      }
    }
  ],
  "refresh_on_status": [401, 403, 419]
}
```

后端必须校验：

- `id`、`name` 为非空字符串。
- `producer.request.method` 是合法 HTTP method。
- `producer.request.url` 非空。
- `producer.extract` 至少一条。
- 每个 extract 的 `name` 非空，`from` 属于允许来源，`path` 非空。
- `consumers` 至少一条。
- 每个 consumer 的 method、url、inject 合法。
- inject 至少包含 headers、query、body 中的一个非空对象。
- 模板变量必须能对应 producer extract name 或已知 profile variable。

### 9.2 发布后编辑

MCP 工具库详情页和编辑页必须支持查看和编辑 `api_monitor_auth.token_flows`。

编辑操作包括：

- 启用或关闭 flow。
- 新增 manual flow。
- 修改 manual flow。
- 删除 manual flow。
- 查看 auto flow 摘要。

auto flow 可以关闭；是否允许直接编辑 auto flow 由 UI 决定。后端只要求保存的 flow 通过 schema 校验。

## 10. AI 的使用边界

AI 在 V2 中是辅助角色，不是判定器。

AI 可以做：

- 给 token flow 生成更可读的名称。
- 把 `response.body.$.csrfToken -> request.headers.X-CSRF-Token` 解释成人类可理解的摘要。
- 在用户人工配置时，根据已有录制片段建议 producer URL、extract path 和 consumer inject path。
- 给候选关系生成原因说明。

AI 不能做：

- 在没有值流证据或人工确认的情况下创建高置信 token flow。
- 决定真实 token 值是否安全。
- 读取或输出真实 secret 明文。
- 替代后端 schema 校验。
- 替代 runtime 中的 method、URL 和 inject 规则匹配。

最终生效条件只有两类：

1. 由确定性流量追踪生成，并被用户在发布时选择的 auto flow。
2. 由用户人工配置，并通过后端校验的 manual flow。

## 11. Preview 与错误信息

runtime 返回的 `request_preview` 必须能帮助用户判断链路是否生效。

成功 preview：

```json
{
  "method": "GET",
  "url": "http://localhost:11451/api/orders",
  "headers": {
    "Authorization": "***",
    "X-CSRF-Token": "***"
  },
  "auth": {
    "credential_type": "test",
    "credential_configured": true,
    "injected": true,
    "profile": {
      "headers": ["Authorization"],
      "variables": ["auth_token", "csrf_token"],
      "cookies": true
    },
    "token_flows": [
      {
        "id": "flow_csrf_orders",
        "name": "csrf_token",
        "producer_applied": true,
        "consumer_applied": true,
        "injected": ["headers.X-CSRF-Token"]
      }
    ]
  }
}
```

失败错误必须区分以下情况：

- base URL 缺失，导致相对 producer URL 或 target URL 无法解析。
- credential auth 登录失败。
- producer HTTP 状态码失败。
- producer extract 没有提取到值。
- consumer 匹配为空。
- consumer 模板变量不存在。
- 目标请求返回认证失败状态，并且刷新重试后仍失败。

当 consumer 匹配为空时，preview 应显示：

```json
{
  "auth": {
    "profile": {
      "headers": ["Authorization"],
      "variables": ["auth_token"],
      "cookies": true
    },
    "token_flows": []
  }
}
```

这个状态表示“认证已注入，但 token flow 没有匹配或没有配置”，不是“token 提取为空”。

## 12. 安全与隐私

V2 必须遵守以下安全规则：

- 真实 token 明文只存在于 runtime profile 内存中。
- MCP server 配置只保存 URL、extract path、inject path 和模板，不保存 token 值。
- 日志和 preview 中所有 secret 值显示为 `***`。
- 手动配置 JSON 中不得要求用户填写真实 token 值。
- 失败错误可以包含 URL、method、flow id、extract path，但不能包含响应体中的 secret 值。
- profile 在 tool call 结束后释放，不跨用户、跨 session 或跨 tool call 复用。

## 13. 兼容与迁移

如果已有 MCP server 保存了旧版 token flow：

```json
{
  "setup": {},
  "extract": {},
  "inject": {}
}
```

runtime 有两种允许行为：

1. 在读取配置时转换成 V2 `producer` + `consumers`。
2. 返回明确错误，提示该 server 使用旧版 token flow，需要重新发布或迁移。

默认推荐第一种行为，以减少已有工具失效。

迁移原则：

- `setup` 转为 `producer.request`。
- `extract` 转为 `producer.extract`。
- `inject` 转为单个 consumer。
- 无法确定目标 consumer method 或 URL 时，不自动迁移，返回明确错误。

## 14. 用户工作流

### 14.1 自动识别流程

1. 用户录制登录和业务接口。
2. API Monitor 分析流量，生成 token flow candidates。
3. 系统去重，展示 producer 摘要、consumer 摘要、样本数、置信度。
4. 用户选择要发布的 flows。
5. MCP server 保存 V2 `api_monitor_auth.token_flows`。
6. runtime 调用 tool 时按 profile 管线执行。

### 14.2 人工配置流程

1. 用户发现自动识别缺失或错误。
2. 用户在发布弹窗或 MCP 编辑弹窗中添加 manual flow。
3. 后端校验 flow schema 和模板变量。
4. 保存后 runtime 与 auto flow 使用同一套执行逻辑。

### 14.3 调试流程

1. 用户调用 MCP tool。
2. 如果接口失败，查看 request preview。
3. preview 中只有 `Authorization`、没有 `token_flows`，说明 flow 未配置或未匹配。
4. preview 中 `producer_applied=false`，说明 producer 未执行或失败。
5. preview 中 `consumer_applied=false`，说明 producer 成功但目标请求没有匹配 consumer。
6. 用户据此修改 flow 配置。

## 15. 验收标准

后端验收：

- 登录接口返回 auth token 后，runtime profile 中存在 `auth_token` 和 `Authorization` header。
- producer 请求会携带登录后的 profile headers 和 cookies。
- producer 能从响应 body、headers 或 cookie 中提取 token 并写入 profile。
- 目标请求能同时携带 `Authorization` 和 `X-CSRF-Token`。
- 同一个 token flow 支持多个 consumers，并且只注入当前目标请求匹配的 consumer。
- 重复录制同一个接口不会生成重复 consumer。
- manual flow 保存前会被后端校验。
- preview 不泄漏 token 明文。
- `403`、`419` 等状态码触发 producer refresh 并重试一次。

前端验收：

- 发布弹窗展示去重后的 token flow 候选。
- 候选展示 producer、consumer、置信度、样本数和来源摘要。
- 发布弹窗支持添加 manual flow。
- MCP 编辑页支持查看、启用、关闭和编辑 token flows。
- 中文界面使用中文文案，英文界面使用英文文案。

回归验收：

- 无 token flow 的普通 API Monitor MCP 仍可使用 credential auth。
- 无认证的 API Monitor MCP 不受影响。
- 普通非 API Monitor MCP 不受影响。

## 16. 与实施计划的关系

现有 `docs/superpowers/plans/2026-04-28-api-monitor-runtime-profile-token-flow-v2.zh-CN.md` 是基于讨论生成的执行草案。该计划需要以本 spec 为依据重新校准，尤其是：

- runtime profile 的生命周期和字段。
- producer 必须使用 profile。
- consumer 匹配为空与 producer 提取为空的错误区分。
- 一个 flow 多个 consumers 的保存和 runtime 行为。
- manual flow 的后端校验规则。
- AI 只能辅助解释和建议，不能作为最终绑定依据。

计划校准完成前，不应直接按旧草案继续实现。
