# API Monitor MCP 可用性与认证设计

> 本文档补充 `API Monitor 保存为 MCP` 的第二阶段设计：让用户能看懂、配置、测试并在对话中可靠使用 API Monitor MCP。上一阶段已经把 API Monitor 结果保存成一个 MCP；本阶段关注 MCP 中的 tool 如何定义、如何展示、如何认证、如何被 Agent 调用。

## 1. 背景与问题

当前 API Monitor MCP 已经可以被保存到“我的 MCP”，但用户仍然很难理解和使用它：

1. 在工具库点开 API Monitor MCP 后，只能看到普通 MCP discovery 的简化工具列表，看不到原来 API Monitor 页面里的 YAML。
2. 用户无法直观看到每个 tool 会调用哪个 API、使用什么 method、URL、query、body。
3. 用户不能重命名 tool 或优化 description，导致 Agent 在对话中可能无法按用户意图选择正确 tool。
4. API 调用通常需要请求头、Cookie、Bearer token、API key 等认证信息，但当前 API Monitor MCP 没有面向用户的配置入口。
5. YAML 目前更像被保存下来的文本，还没有明确成为 tool 调用的权威定义。

本阶段目标是把 API Monitor MCP 从“注册成功”提升到“用户可以理解并真正使用”。

## 2. 核心结论

API Monitor MCP 的使用模型应明确为：

```text
一个 API Monitor MCP = 一组 API tools + 一份 MCP 级共享认证配置
每个 API tool = 一个 YAML 定义 + 解析后的结构化执行契约
对话中调用 tool = runtime 根据执行契约发起对应 API 请求
```

其中：

- YAML 是用户可读、可编辑的 tool 权威定义。
- 保存或编辑 tool 时，后端必须解析 YAML 并生成结构化执行契约。
- MCP discovery 使用解析后的 `name`、`description`、`parameters`。
- runtime 使用解析后的 `method`、`url`、`request mapping` 和 MCP 级认证配置发起 HTTP 请求。

## 3. 对话中如何使用 API Monitor MCP

用户在 API Monitor 页面保存 MCP 后：

1. 该 MCP 默认 `enabled=true`。
2. 该 MCP 默认 `default_enabled=true`，因为它是用户主动创建的工具。
3. 新对话创建时，如果用户没有额外禁用它，Agent 默认能看到这个 MCP 下的 tools。
4. 用户在对话中用自然语言表达需求，例如“帮我查今天的订单”。
5. Agent 根据 tool 的 `name`、`description`、`parameters` 判断是否调用某个 API Monitor tool。
6. 后端 runtime 根据该 tool 的执行契约和 MCP 级认证配置发出真实 API 请求。
7. API 返回结果后，Agent 将结果组织成回复。

用户不需要在聊天中手动点击某个 tool。工具调用应由 Agent 根据对话意图自动选择。

用户仍然可以控制可用性：

- 在工具库中关闭该 API Monitor MCP，关闭后新对话默认不可用。
- 在具体对话的 MCP 选择器中临时禁用或启用。

## 4. Tool YAML 定义

### 4.1 YAML 的角色

YAML 不是 HTTP 请求本身，不能“直接执行”。它是 API 调用说明书。

```text
YAML = API call spec
runtime = API call executor
```

后端应在保存或编辑时解析 YAML。解析成功后生成结构化执行契约；解析失败则该 tool 不能作为可调用 MCP tool 发布。

### 4.2 YAML 最小格式

GET 示例：

```yaml
name: search_orders
description: 根据订单号、手机号、用户名称或状态查询订单列表
method: GET
url: /api/orders
parameters:
  type: object
  properties:
    keyword:
      type: string
      description: 订单号、手机号或用户名
    status:
      type: string
      description: 订单状态
request:
  query:
    keyword: "{{ keyword }}"
    status: "{{ status }}"
response:
  type: object
```

POST 示例：

```yaml
name: create_user
description: 创建一个新用户
method: POST
url: /api/users
parameters:
  type: object
  properties:
    name:
      type: string
      description: 用户名称
    email:
      type: string
      description: 用户邮箱
request:
  body:
    name: "{{ name }}"
    email: "{{ email }}"
response:
  type: object
```

### 4.3 结构化执行契约

保存时，后端从 YAML 解析并保存以下字段：

- `name`
- `description`
- `method`
- `url`
- `input_schema`
- `query_mapping`
- `path_mapping`
- `body_mapping`
- `header_mapping`
- `response_schema`
- `yaml_definition`

`yaml_definition` 保留原始文本，用于展示和再次编辑；其他字段用于 discovery 和 runtime 执行。

### 4.4 校验规则

后端必须校验：

- `name` 必填，且必须是合法 MCP tool 名称，建议使用 `snake_case`。
- 同一个 API Monitor MCP 下 tool name 不能重复。
- `description` 必填或至少由系统生成一个可用描述。
- `method` 必须是 `GET`、`POST`、`PUT`、`PATCH`、`DELETE` 之一。
- `url` 必填，可以是绝对 URL，也可以是基于 MCP `base_url` 的相对路径。
- `parameters` 必须是 JSON Schema object。
- `request.query`、`request.body`、`request.headers` 中引用的变量必须能在 `parameters.properties` 中找到。
- YAML 解析失败或校验失败时，不能保存为可调用 tool。

## 5. Tool 重命名与描述编辑

Tool 名称和描述是 Agent 选择工具时最重要的信号之一。API Monitor 自动生成的名称可能不符合用户意图，因此必须支持编辑。

用户应能在工具详情中修改：

- `name`
- `description`
- YAML
- 参数说明

同步规则：

- 用户通过表单修改 `name` 或 `description` 时，系统同步更新 YAML 中的对应字段。
- 用户直接修改 YAML 中的 `name` 或 `description` 时，界面展示也应同步。
- 保存时以 YAML 解析后的 `name` 和 `description` 为准。
- discovery 返回解析后的 `name`、`description` 和 `input_schema`。

这样 Agent 在对话中看到的是用户调整后的工具语义。例如用户把 `get_data` 改成 `search_orders`，Agent 更容易在“查订单”场景中选中它。

## 6. MCP 级共享认证配置

### 6.1 配置层级

认证和请求头配置放在 API Monitor MCP 级别，而不是每个 tool 单独配置。

理由：

- 同一网站或系统的 API 通常共享一套认证。
- 用户配置一次即可应用到所有 tools。
- 避免每个 tool 都重复填写 token、Cookie 或 API key。

后续如果确实需要单 tool 覆盖，可作为高级能力扩展。

### 6.2 配置项

API Monitor MCP 详情页应提供：

- `Base URL`
- `Static Headers`
- `Credential Headers`
- `Cookie Header`
- `Query Params`
- `Timeout`
- `Enabled`
- `Default enabled for new sessions`

示例：

```text
Authorization: Bearer {{ api.password }}
X-API-Key: {{ my_api_key.password }}
Cookie: {{ session_cookie.password }}
```

### 6.3 凭据机制

认证配置应复用现有 MCP credential binding 机制：

- 用户在配置中绑定凭据 alias。
- headers/query 中使用 `{{ alias.password }}`、`{{ alias.username }}`、`{{ alias.domain }}`。
- 运行时由后端解析凭据模板。
- 前端和本地文件中不直接保存明文密钥。

API Monitor MCP 不应新造一套秘密存储。

## 7. 工具库详情视图

普通 MCP 的 tools 弹窗可以继续保持简化展示；API Monitor MCP 需要专门视图。

### 7.1 MCP 顶部区域

展示：

- MCP 名称
- 描述
- 来源：`API Monitor`
- 启用开关
- 默认启用开关
- Tool 数量

### 7.2 共享认证区域

展示并允许编辑：

- Base URL
- Static Headers
- Credential Headers
- Credential Bindings
- Cookie Header
- Advanced Query Params
- Timeout

保存该配置后，所有 tools 调用都使用这份共享配置。

### 7.3 Tools 列表

每个 tool 卡片显示：

- Tool name
- Description
- HTTP method
- URL
- 参数数量
- 是否通过 YAML 校验

### 7.4 Tool 展开详情

展开后展示：

- 原始 YAML 编辑器
- 解析后的 input schema
- 请求映射预览：path/query/body/header
- 响应 schema
- 调用示例
- 校验错误
- 保存按钮
- 测试按钮

调用示例应让用户看懂 Agent 调用时会传什么参数，例如：

```json
{
  "keyword": "alice",
  "status": "paid"
}
```

对应请求预览：

```http
GET /api/orders?keyword=alice&status=paid
Authorization: Bearer {{ api.password }}
```

## 8. Runtime 调用流程

Agent 调用某个 API Monitor MCP tool 时：

1. runtime 根据 `mcp_server_id + tool_name` 找到 tool。
2. 读取 tool 的结构化执行契约。
3. 读取 MCP 级共享认证配置。
4. 解析 credential templates。
5. 将 Agent arguments 映射到 path/query/body/header。
6. 合并 MCP 级 headers/query/cookie。
7. 发起 HTTP 请求。
8. 返回结构化结果，包括：
   - `success`
   - `status_code`
   - `headers`
   - `body`
   - `request_preview`（可选，隐藏敏感值）

敏感值在日志和 UI 中必须脱敏。

## 9. Discovery 返回内容

对 Agent 的 MCP discovery 应保持标准 MCP tool 结构：

```json
{
  "name": "search_orders",
  "description": "根据订单号、手机号、用户名称或状态查询订单列表",
  "input_schema": {
    "type": "object",
    "properties": {
      "keyword": { "type": "string" },
      "status": { "type": "string" }
    }
  }
}
```

对工具库前端的 API Monitor MCP 详情接口，应返回更完整的信息：

- `name`
- `description`
- `method`
- `url`
- `yaml_definition`
- `input_schema`
- `query_mapping`
- `path_mapping`
- `body_mapping`
- `header_mapping`
- `response_schema`
- `validation_status`
- `validation_errors`

标准 discovery 面向 Agent；详情接口面向用户配置和理解。两者不应混用。

## 10. 默认启用规则

API Monitor MCP 是用户主动创建的工具，因此默认规则为：

- 创建时 `enabled=true`
- 创建时 `default_enabled=true`
- 用户可在工具库关闭 `enabled`
- 用户可在工具库关闭 `default_enabled`
- 单个会话仍可通过 MCP selector 临时覆盖

这样用户创建后可以立即在对话中使用，同时仍保留控制权。

## 11. 错误处理

### 11.1 YAML 错误

如果 YAML 无法解析或校验失败：

- 工具详情中显示具体错误。
- 该 tool 标记为 invalid。
- invalid tool 不应出现在 Agent discovery 中。
- 保存 MCP 配置时不应阻止保存其他 valid tools，但应明确提示 invalid tools 不可调用。

### 11.2 认证错误

如果 credential alias 缺失或无法解析：

- 测试调用时显示认证配置错误。
- 对话调用时返回结构化错误给 Agent。
- 不应把 secret 原文输出到日志或前端。

### 11.3 API 请求错误

如果目标 API 返回非 2xx：

- runtime 返回 `success=false`
- 包含 `status_code`
- 包含响应 body 摘要
- Agent 可据此向用户解释失败原因

## 12. 数据模型补充

### 12.1 user_mcp_servers

API Monitor MCP 主体继续保存在 `user_mcp_servers`，补充：

- `source_type = "api_monitor"`
- `transport = "api_monitor"`
- `default_enabled = true`
- `endpoint_config.base_url`
- `endpoint_config.headers`
- `endpoint_config.query`
- `endpoint_config.timeout_ms`
- `credential_binding`

### 12.2 api_monitor_mcp_tools

每个 tool 保存：

- `mcp_server_id`
- `user_id`
- `name`
- `description`
- `method`
- `url`
- `yaml_definition`
- `input_schema`
- `path_mapping`
- `query_mapping`
- `body_mapping`
- `header_mapping`
- `response_schema`
- `validation_status`
- `validation_errors`
- `source = "api_monitor"`

## 13. 非目标

本阶段不做：

- 单 tool 独立认证覆盖。
- 自动破解登录态或自动提取浏览器 Cookie。
- 完整 OpenAPI 导入导出。
- 历史 API Monitor 会话再次编辑。
- 对所有普通外部 MCP 重新设计详情页。

## 14. 测试范围

### 14.1 YAML 解析与校验

- 能解析合法 GET YAML。
- 能解析合法 POST YAML。
- 缺少 name/method/url 时返回明确错误。
- 同一 MCP 下重复 name 报错。
- request mapping 引用不存在的参数时报错。

### 14.2 MCP 详情接口

- API Monitor MCP tools 详情返回 YAML、method、url、schema、mapping。
- 普通 MCP 仍走原 discovery 行为。
- invalid tool 不出现在 Agent discovery，但能在详情页看到错误。

### 14.3 认证配置

- 能保存 MCP 级 static headers。
- 能保存 credential headers。
- runtime 调用时合并 MCP 级 headers/query。
- 日志和 UI 不泄露 secret 明文。

### 14.4 对话调用

- 默认启用的 API Monitor MCP 能进入新对话有效 MCP 列表。
- Agent 调用 tool 时按 YAML contract 发出正确 HTTP 请求。
- API 错误以结构化结果返回。

### 14.5 前端交互

- Tools 弹窗中 API Monitor MCP 使用专门详情视图。
- Tool 可重命名并同步 YAML。
- YAML 编辑后能校验并保存。
- 共享认证配置保存后作用于所有 tools。
