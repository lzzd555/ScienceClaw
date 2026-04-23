# API Monitor 保存为 MCP 设计

> 本文档描述 `api-monitor` 页面从“导出 YAML / 单个 tool 保存”改为“批量保存为一个 MCP”的产品、数据和发现设计。该设计只覆盖 `api-monitor` 生成 API MCP 的保存与展示链路，不改变普通外部 MCP 的连接方式。

## 1. 目标

当前 `api-monitor` 页面存在两个问题：

1. 右上角“导出”只返回 YAML 文本，没有进入工具库的“我的 MCP”
2. 页面内单个 tool 的保存不符合用户心智。用户想要的是把当前会话提取出的整批 API 一次性保存成一个 MCP，而不是逐个保存

本次改造后的目标是：

- 一次 `api-monitor` 会话的提取结果，对应一个用户私有 MCP
- 该 MCP 下包含多个 tools
- 每个 tool 对应一个被提取出的单独 API 接口
- 用户调用某个 tool，本质上就是调用对应的 API 接口
- 保存后，该 MCP 能在工具库的“我的 MCP”中看到

关系模型为：

`一次 api-monitor 结果 = 1 个 MCP = N 个 API tools`

## 2. 用户交互

### 2.1 页面行为

`api-monitor` 页面改成“会话内编辑，统一发布”的模式：

- 右上角按钮从“导出”改成“保存为 MCP 工具”
- 工具卡片不再提供“单独保存”
- 每个工具卡片只保留：
  - 编辑
  - 删除

页面内对工具的修改，只影响当前会话中的待发布结果。真正持久化到“我的 MCP”，只能通过右上角按钮一次性完成。

### 2.2 保存弹窗

点击“保存为 MCP 工具”后弹出保存对话框，至少包含：

- `MCP 名称`
- `MCP 描述`（可选）

默认值策略：

- 名称：可基于站点标题、域名或当前 URL 预填，但用户可修改
- 描述：可基于站点标题或页面语义预填，但用户可修改

### 2.3 重名覆盖

保存时按“当前用户 + MCP 名称”查找已有 MCP：

- 若不存在同名 MCP：直接新建
- 若存在同名 MCP：先弹确认框

确认框需要明确提示：

- 当前操作会覆盖该 MCP 下已有的全部 tools
- 覆盖后，以当前 `api-monitor` 页面中保留的 tools 为准

只有在用户确认后，才执行覆盖保存。

### 2.4 会话模型

`api-monitor` 会话按一次性使用设计：

- 用户在本次会话中分析、录制、编辑 API tools
- 保存后进入工具库
- 不要求支持“再次打开同一个 api-monitor 会话继续编辑”

因此保存逻辑只围绕“当前会话的最终结果”展开，不为跨会话增量编辑引入额外复杂度。

## 3. 数据模型

### 3.1 设计原则

`api-monitor` 生成的 MCP 需要使用项目现有存储抽象，而不是绑定到某一种持久化后端。

项目中 `get_repository(...)` 已经实现了运行模式分流：

- `STORAGE_BACKEND=local` 时，走文件系统 JSON 仓库
- 非 `local` 时，走 MongoDB 仓库

因此本设计采用“统一领域模型 + 由 Repository 决定落盘后端”的方案。

### 3.2 MCP 主体

MCP 主体保存到 `user_mcp_servers`，但增加明确的来源标识，用于区分它和普通外部 MCP：

- `source_type = "api_monitor"`

主体字段至少包括：

- `id`
- `user_id`
- `name`
- `description`
- `scope = "user"`
- `enabled`
- `default_enabled`
- `source_type = "api_monitor"`
- `tool_count`
- `updated_at`

这类 MCP 不是连接外部 `stdio/http/sse` endpoint 的远端服务，而是平台内部托管的用户私有 MCP。

### 3.3 API Tool 子项

每个 API tool 单独保存，并归属于某个 `api-monitor MCP`。建议新增独立集合：

- `api_monitor_mcp_tools`

每条 tool 记录至少包括：

- `id`
- `user_id`
- `mcp_server_id`
- `name`
- `description`
- `method`
- `url_pattern`
- `headers_schema`
- `request_body_schema`
- `response_body_schema`
- `yaml_definition`
- `source = "api_monitor"`
- `updated_at`

如果后续需要支持 API 执行器，还可以继续扩展：

- `base_url`
- `auth_config`
- `header_template`
- `query_mapping`
- `body_mapping`
- `response_mapping`

### 3.4 为什么拆成 MCP 主体 + Tool 子表

不建议把全部 tools 直接内嵌到 `user_mcp_servers` 单条文档里，原因有三点：

1. “一个 MCP 下多个 tool”是稳定的层级关系，拆开后更符合工具发现与调用模型
2. tools 独立存储后，更容易做分页、筛选、单条调试与后续扩展
3. 与现有 `rpa_mcp_tools` 的注册思路更接近，便于未来统一 MCP 工具发现逻辑

## 4. 保存接口

### 4.1 新接口职责

新增一个专用保存接口，用于把当前 `api-monitor` 会话发布为一个 MCP。

请求输入应至少包含：

- `session_id`
- `mcp_name`
- `description`
- `confirm_overwrite`

保存接口不要求前端直接上传整批 tools。服务端应以当前 session 内的工具列表为准，避免前端和服务端状态漂移。

### 4.2 服务端流程

服务端保存流程如下：

1. 读取当前 `api-monitor session` 的全部工具定义
2. 按“当前用户 + MCP 名称”查找已有 MCP
3. 若不存在同名 MCP：
   - 创建新的 `user_mcp_servers` 记录
   - 写入当前 session 的所有 API tools
4. 若存在同名 MCP 且 `confirm_overwrite = false`：
   - 返回“发现重名，需要确认覆盖”
5. 若存在同名 MCP 且 `confirm_overwrite = true`：
   - 更新 MCP 主信息
   - 删除该 MCP 下面原有的全部 API tools
   - 重新写入当前 session 的全部 API tools

关键语义是：

- 覆盖时不是追加
- 而是“整批替换为这次会话中的最新结果”

## 5. 工具库展示

保存成功后，工具库“我的 MCP”中应出现一个新的或被更新的 MCP 条目。

这类条目与普通用户私有 MCP 一起展示，但可以通过来源文案或徽章提示其来源是 `API Monitor`。

用户感知应为：

- 这是一个 MCP
- 里面包含很多 tools
- 每个 tool 对应一个具体 API 接口

不应再让用户感知为“保存了一堆分散的 tool 文本”。

## 6. 工具发现

### 6.1 发现分流

当系统列出某个 MCP 的 tools 时，需要根据来源类型分流：

- 普通外部 MCP：继续按现有逻辑，从 endpoint discover tools
- `source_type = "api_monitor"` 的 MCP：直接从 `api_monitor_mcp_tools` 中读取该 MCP 名下的 tools

### 6.2 对“我的 MCP”和会话选择器的影响

这样设计后：

- 工具库“我的 MCP”可以统一展示所有用户私有 MCP
- Chat 会话里的 MCP 选择器也可以统一感知这些 MCP
- 对用户而言，这些 MCP 都属于同一类能力，只是来源不同

用户不需要知道底层是“连接来的 MCP”还是“平台内部托管的 API MCP”。

## 7. 工具调用

`api-monitor` 生成的 MCP 中，每个 tool 本质上都是一个 API 接口包装器。

调用某个 tool 时，系统需要完成：

1. 根据 tool 定义读取请求方法、URL pattern、参数 schema
2. 将用户输入映射到 path/query/header/body
3. 发起真实 HTTP 请求
4. 按配置返回结构化结果

即：

- MCP 是容器
- tool 是具体 API 接口
- 调用 tool = 调用该接口

本次设计主要覆盖保存与发现链路；真正的 API 执行器实现可在后续迭代中接入，但数据模型要为该能力预留字段。

## 8. 前端改动范围

### 8.1 ApiMonitorPage.vue

需要修改：

- 顶部按钮文案和行为
- 删除单卡片“保存”能力
- 增加“保存为 MCP 工具”弹窗
- 增加重名覆盖确认弹窗
- 保存成功后提示用户可在“我的 MCP”中查看

### 8.2 ToolsPage.vue

需要确保：

- 保存后的 `api-monitor MCP` 能出现在“我的 MCP”列表中
- 打开 tools 预览时能看到该 MCP 下的所有 API tools
- 来源文案能区分普通 MCP 与 API Monitor MCP

## 9. 存储模式兼容性

本设计必须遵守项目已有存储模式：

### 9.1 Local 模式

当 `STORAGE_BACKEND=local` 时：

- `user_mcp_servers` 走本地文件仓库
- `api_monitor_mcp_tools` 走本地文件仓库

也就是说，API Monitor 生成的 MCP 应保存在文件系统，而不是强依赖数据库。

### 9.2 非 Local 模式

当 `STORAGE_BACKEND != local` 时：

- `user_mcp_servers` 走 MongoDB
- `api_monitor_mcp_tools` 走 MongoDB

### 9.3 统一约束

同一套业务逻辑不因后端不同而改变：

- 新建 MCP
- 重名确认
- 覆盖替换 tools
- 工具发现

这些语义都应一致，只有底层存储介质不同。

## 10. 测试范围

建议测试覆盖以下部分：

### 10.1 前端交互

- “导出”按钮改为“保存为 MCP 工具”
- tool 卡片不再展示单独保存入口
- 保存弹窗字段与默认值正常
- 重名时正确弹出覆盖确认

### 10.2 保存接口

- 新建 MCP 成功
- 重名时返回需确认状态
- 二次确认后执行覆盖
- 覆盖时旧 tools 被整批替换，而不是追加

### 10.3 存储分流

- `local` 模式下写入文件仓库
- 非 `local` 模式下写入 Mongo 仓库

### 10.4 工具发现

- “我的 MCP”可以显示 `api-monitor` 来源的 MCP
- 该 MCP 的 tools 列表来自平台内部保存的 API tools，而不是 endpoint discover

## 11. 非目标

本次设计不包含：

- 重新定义普通外部 MCP 的连接方式
- 支持重新进入历史 `api-monitor` 会话继续编辑
- 在本轮内完成完整 API 执行器的高级鉴权编排
- 将 API Monitor MCP 与 RPA MCP 工具完全统一为同一张表

这些能力可在后续迭代中继续收敛，但不应阻塞本次“保存为 MCP 工具”的主流程落地。
