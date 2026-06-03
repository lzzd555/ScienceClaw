# API Monitor Token Flow 保存硬化设计

日期：2026-06-03
状态：待评审

## 1. 背景

API Monitor MCP 的 Token Flow 功能上线后，经过实际使用发现四个问题：

1. 保存 MCP 时，token flow 的 consumers 包含了用户未选中的工具对应的 URL，导致保存的配置包含无关接口信息。
2. Producer 发现策略中的高熵动态值扫描产生了过多无关 flow，信噪比太低，实际值得保存的只有显式 token 相关的内容。
3. 不同接口返回相同字段名时（如 `/api/session` 和 `/api/config` 都有 `token` 字段），系统只保留了一个来源，丢弃了其他接口的同名 token flow。
4. Token producer 接口目前仅作为 `token_flows` 配置中的数据存在，用户无法看到它的完整 Swagger 2.0 定义，也无法识别它是一个动态 token 工具。

## 2. 目标

本设计完成后：

1. Publish 时只保留对应已选中工具的 consumers，去掉无关接口。
2. Token flow 发现只产出与 token 直接相关的 flow，不产出高熵扫描发现的无关动态值。
3. 不同接口的同名字段各自独立，不被取舍。
4. Token producer 接口保存为带有 Swagger 2.0 YAML 的特殊工具，标记 `tool_type: "dynamic_token"`。

## 3. 非目标

- 不改变 Runtime 的执行管线（profile → credential → producer → consumer → target）。
- 不改变 Token Flow 的匹配算法核心（精确值匹配 + 时间线约束）。
- 不改变已保存 MCP server 的配置格式（已保存的配置不受影响）。
- 不让 Agent 自由调用 dynamic_token 工具或手动传入 token 值。

## 4. 改动 1：Publish 时过滤 Consumers

### 4.1 问题

用户点击保存 MCP 时，token flow 的 consumers 包含了未选中工具的 URL。用户只关心自己选中的工具相关的 token 注入，无关接口的 consumer 是噪声。

### 4.2 方案

在前端 publish 弹窗中，根据用户已选中的工具列表过滤 token flow consumers：

1. **展示时**：publish 弹窗显示的 token flow consumers 只包含用户已选中工具对应的 consumer（按 method + URL 匹配）。
2. **保存时**：前端组装 publish 请求时，只传过滤后的 consumers。后端 `publish_session` 不需要改动，继续原样存入前端传来的数据。

过滤规则：consumer 的 `(method, url)` 命中 selected tools 列表中的 `(method, url_pattern)` 则保留，否则移除。URL 比较使用规范化路径匹配，与 Runtime V2 的匹配逻辑一致。

如果一个 flow 过滤后 consumers 为空，则整个 flow 不展示也不保存。

### 4.3 影响范围

- 前端 publish 弹窗需要根据 selected tools 过滤 consumers 展示。
- 后端 `publish_session` 无需改动（继续存前端传来的数据）。
- 已保存的 MCP server 不受影响。

## 5. 改动 2：收窄 Producer 发现策略

### 5.1 问题

当前 `_collect_producers` 使用双层候选策略：显式语义规则 + 高熵动态值扫描。实际使用中，高熵扫描产生了大量与 token 无关的动态值 flow，信噪比太低。

### 5.2 方案

在 `api_monitor_token_flow.py` 的 `_collect_producers` 中，只保留第一层显式语义规则：

- 字段名包含 `csrf`、`xsrf`、`nonce`、`token`、`signature` 等语义关键词的候选。
- 移除第二层高熵动态值扫描逻辑。

移除的逻辑范围：
- `is_dynamic_value_candidate` 中的熵值计算和长度判断（仅限 producer 发现；consumer 侧的值匹配不受影响）。
- 基于熵值特征进入候选池的分支。

保留的逻辑范围：
- 显式语义字段名匹配（Response Headers、Set-Cookie、JSON Body 等来源的扫描）。
- Consumer 侧的值匹配逻辑完全不变（精确匹配、规范化匹配）。
- 置信度计算不变。

### 5.3 影响范围

- 仅影响发现阶段，不影响已保存的 token flow 配置和 Runtime。
- 新分析会产出更少但更精准的 token flow 候选。

## 6. 改动 3：修复 Producer 去重 Key

### 6.1 问题

不同接口返回相同字段名时（如 `GET /api/session` 和 `GET /api/config` 都返回 `token` 字段），系统只保留了一个 producer，丢弃了另一个。

### 6.2 方案

在 `api_monitor_token_flow.py` 中，将 producer 去重 key 从当前使用的 key 改为：

```
(method, url_pattern, extract_from, extract_path, extract_name)
```

与 V2 Spec 第 8.1 节定义的去重 key 一致。不同接口（URL 不同）的同名字段将产生不同的 producer，各自独立参与后续的值匹配和 flow 生成。

### 6.3 影响范围

- 仅影响分析阶段的新 flow 生成。
- 已保存的 MCP server 配置不受影响（flow_id 在分析时生成，保存后不再变化）。

## 7. 改动 4：Token Producer 作为特殊工具保存

### 7.1 问题

Token producer 接口目前仅作为 `token_flows[].producer` 中的配置数据存在。用户无法看到它的完整 Swagger 2.0 定义，也无法在工具列表中识别它的用途。

### 7.2 方案

分两个阶段实现：

#### 7.2.1 发现阶段：保留 DOM 和请求/响应数据

在 token flow 分析阶段，为每个发现的 producer 保留：

- **请求/响应数据**：通过已有的 `source_call_id` 引用 `CapturedApiCall`，包含完整的 method、URL、headers、request body、response body。
- **页面 DOM 快照**：在 token flow 分析结果中增加 `source_dom_snapshot_id` 引用，指向分析时的页面 DOM 快照（如果可用）。

这些数据保留在 session 内（内存/临时存储），不直接持久化到 MCP server 配置。

#### 7.2.2 发布阶段：生成 Swagger 2.0 YAML

在 `publish_session` 时，对 token_flows 中每个有 producer 的 flow：

1. 从 session 中读取 producer 对应的 `CapturedApiCall` 数据。
2. 读取关联的 DOM 快照（如果有）。
3. 复用现有的 `generate_tool_definition` 能力，从请求/响应数据生成 Swagger 2.0 YAML。
4. 创建 `ApiToolDefinition`，标记 `tool_type: "dynamic_token"`。
5. 将该工具加入 `selected_tools` 列表一起保存。

#### 7.2.3 工具模型扩展

`ApiToolDefinition` 新增可选字段：

```python
tool_type: str | None = None  # None = 普通业务工具, "dynamic_token" = 动态 token 工具
```

- 默认值为 `None`，现有工具不受影响。
- `dynamic_token` 类型的工具在前端展示时使用特殊样式或标注。
- Runtime 不直接调用 `dynamic_token` 工具，它由 token flow producer 机制驱动。

### 7.3 影响范围

- 工具模型新增可选字段，向后兼容。
- 现有工具列表查询、发布、Runtime 调用逻辑不需要修改（`tool_type` 为 `None` 时行为不变）。
- 前端工具列表需要适配新的 `tool_type` 字段以展示特殊标注。

## 8. 运行时兼容性

| 改动 | 已保存 MCP Server | 已有 Runtime | 前端 |
|------|-------------------|-------------|------|
| 1. 过滤 consumers | 不受影响 | 不受影响 | 不受影响 |
| 2. 收窄发现策略 | 不受影响 | 不受影响 | 不受影响 |
| 3. 修复去重 key | 不受影响 | 不受影响 | 不受影响 |
| 4. 特殊工具保存 | 不受影响（新字段可选） | 不受影响（tool_type=None 保持原有行为） | 需要适配展示 |

## 9. 测试计划

### 9.1 过滤 Consumers 测试

- 前端 publish 弹窗中只选中部分工具时，验证 token flow consumers 只展示对应选中的工具。
- 全部工具未选中时，验证 token flows 不展示。
- consumers 过滤后为空的 flow 不展示也不保存。
- 前端提交的 publish 请求中 token_flows 只包含过滤后的 consumers。

### 9.2 收窄发现策略测试

- 包含 `csrf`/`token`/`nonce` 字段名的 producer 仍能被发现。
- 不包含语义字段名但值高熵的 producer 不再被发现。
- 已有的值匹配和置信度计算不受影响。

### 9.3 Producer 去重测试

- 不同接口（URL 不同）返回相同字段名时，各自产生独立的 producer。
- 同一接口的相同字段被正确去重（只保留一个 producer）。
- 独立的 producer 各自参与值匹配，生成各自的 flow。

### 9.4 特殊工具保存测试

- Publish 时 token producer 自动生成 Swagger 2.0 YAML 工具定义。
- 工具定义中 `tool_type` 为 `"dynamic_token"`。
- 不影响普通业务工具的生成和保存。
- 已保存的无 `tool_type` 工具仍能正常加载和使用。
