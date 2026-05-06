# API Monitor 采集窗口边界设计

日期：2026-05-06

## 1. 背景

API Monitor 已经支持在分析和录制过程中实时捕获 API，并异步生成 MCP 工具。近期引入实时生成后，系统为了避免漏掉页面初始化阶段的 token/auth 接口，会在录制或分析开始前 drain capture buffer，并把这些历史调用写入 session。

这个做法暴露了一个边界问题：

- 录制前或分析前的 API 可能被当作工具生成输入。
- 用户预期“录制/分析过程中触发的 API”才是工具生成对象。
- 但 token flow 又需要保留录制前或分析前的 CSRF/session/bootstrap 等上下文接口。

因此需要把“可生成工具的 API 调用”和“仅作为 token/auth 证据的 API 调用”从语义上拆开。

## 2. 目标

本设计完成后，API Monitor 应满足：

1. 只有在明确的分析窗口或录制窗口内捕获到的 API，才能作为工具生成依赖。
2. 窗口外捕获到的 API 不生成工具、不创建 generation candidate、不触发 LLM 工具生成。
3. 窗口外 API 仍可作为 token flow、认证推断、CSRF/XSRF 依赖分析的证据。
4. 实时工具生成的去重、排队、429 重试、stale 更新机制不被破坏。
5. 已有 confidence/score/selected 判断机制不改变。
6. 前端展示的“生成中/已生成工具”只反映可生成工具的 API，不展示 evidence-only API。

## 3. 非目标

本设计不做：

- 不重写 API Monitor 的 token flow 检测算法。
- 不引入新的“是否采用工具”判断系统。
- 不把录制流程改成完整 RPA trace 编译流程。
- 不用经验规则或站点模板决定哪些 API 可生成工具。
- 不要求第一版把 evidence-only 数据持久化到数据库；是否持久化由现有 session 生命周期决定。
- 不改变 MCP 发布规则，发布仍只发布已生成且被选中的工具。

## 4. 核心问题

当前 capture engine 是 session/page 级别监听，而不是录制/分析窗口级监听。也就是说，只要页面存在，网络请求就可能进入 capture buffer。

这会产生三类调用：

1. **窗口前调用**
   - 例如打开页面自动触发的 `/api/csrf`、`/api/session`、`/api/login/status`。
   - 它们可能对 token flow 很重要。
   - 但它们不是用户在录制或分析过程中触发的业务 API。

2. **窗口内调用**
   - 例如用户录制时点击“搜索”触发的 `/api/orders`。
   - 或自动分析 probe 某个按钮时触发的 `/api/users`。
   - 它们应进入工具生成链路。

3. **窗口后调用**
   - 例如停止录制之后页面异步刷新出的请求。
   - 第一版不应进入工具生成链路。

本设计重点解决窗口前和窗口内的语义隔离。

## 5. 核心概念

### 5.1 Generation Call

Generation call 是可用于生成 MCP 工具的 API 调用。

来源必须满足：

- 在 `recording` 状态期间由录制 drain loop 或 stop final drain 捕获。
- 在自由分析 probe 某个元素期间捕获。
- 在定向分析执行某个 step 期间捕获，包括失败 step drain 到的调用。

Generation call 可以：

- 写入 `session.captured_calls`。
- 创建或更新 `ApiToolGenerationCandidate`。
- 进入后台生成队列。
- 作为 confidence/score 的样本。
- 出现在前端候选工具或已生成工具列表中。

### 5.2 Evidence Call

Evidence call 是只作为上下文证据的 API 调用。

典型来源：

- 录制开始前 capture buffer 中已有的调用。
- 自由分析开始 probe 前 drain 到的调用。
- 定向分析开始 step 前 drain 到的调用。

Evidence call 可以：

- 参与 token producer/consumer 检测。
- 参与 CSRF/XSRF、session bootstrap、auth header/cookie 推断。
- 作为后续工具 runtime auth flow 的依赖来源。

Evidence call 不可以：

- 创建 `ApiToolGenerationCandidate`。
- 入队 LLM 工具生成。
- 作为独立 MCP 工具出现在前端。
- 被 `reconcile_generation_candidates()` 重新捞出来生成工具。

## 6. 数据语义

建议将 session 内调用分成两个集合：

```text
session.captured_calls  = generation-eligible calls
session.evidence_calls  = evidence-only calls
```

语义约束：

- `captured_calls` 只表示可生成工具的调用。
- `evidence_calls` 只表示上下文证据调用。
- 同一个 call id 不能同时出现在两个集合里。
- 去重时应同时检查两个集合，避免重复保存。
- token flow 读取时应读取两个集合的并集。
- 工具生成、candidate reconcile、前端候选列表只能读取 `captured_calls` 派生出的 candidate/tool。

## 7. 录制流程设计

### 7.1 开始录制

用户点击开始录制时：

1. 系统允许 drain 当前 capture buffer。
2. drain 到的调用视为窗口前调用。
3. 这些调用进入 evidence-only 存储。
4. 不创建 generation candidate。
5. 不触发后台 LLM 工具生成。
6. session 状态切换为 `recording`。
7. 启动录制期间的周期性 drain loop。

### 7.2 录制过程中

录制 drain loop 每次 drain 到调用时：

1. 确认 session 仍处于 `recording`。
2. 将调用视为窗口内调用。
3. 写入 `captured_calls`。
4. 按 endpoint 去重 upsert generation candidate。
5. 入队后台生成任务。
6. 前端轮询或事件流展示生成中状态。

### 7.3 停止录制

用户点击停止录制时：

1. 等待正在处理的 drain 任务安全收尾，避免调用从 buffer 取出后丢失。
2. final drain 当前 buffer。
3. final drain 到的调用仍属于录制窗口内调用。
4. 这些调用进入 generation 链路。
5. session 状态切回 `idle`。

### 7.4 停止后的调用

停止后才捕获到的调用不应进入本次录制工具生成链路。第一版可以保留在 capture buffer，下一次开始录制时会作为 evidence-only 处理。

## 8. 自由分析流程设计

自由分析包含扫描 DOM、分类可交互元素、逐个 probe 元素。

### 8.1 Probe 前 Drain

每次 probe 前 drain 到的调用，语义上属于“上一个阶段遗留调用”或“页面初始化调用”。

这些调用：

- 进入 evidence-only。
- 不生成工具。
- 可参与 token flow。

### 8.2 Probe 中调用

执行某个元素 probe 后捕获到的调用，属于该元素触发的窗口内调用。

这些调用：

- 进入 `captured_calls`。
- 创建或更新 generation candidate。
- 异步生成工具。

## 9. 定向分析流程设计

定向分析由观察页面、规划动作、执行动作、等待 settle、drain 调用组成。

### 9.1 初始 Pre-Drain

定向分析开始时 drain 到的调用是历史调用。

这些调用：

- 进入 evidence-only。
- 不生成工具。

### 9.2 Step 调用

每个 directed step 执行期间或执行失败后 drain 到的调用，属于窗口内调用。

这些调用：

- 进入 `captured_calls`。
- 创建或更新 generation candidate。
- 可用于 step trace 和最终工具生成。

## 10. Token Flow 兼容性

Token flow 检测不能只读取 generation calls。

原因：

- CSRF/session/bootstrap 接口常常在用户开始录制前已经触发。
- 如果这些调用不参与 token flow，后续业务 API 可能无法正确生成 auth flow。
- 但如果这些调用参与工具生成，又会产生用户不期望的工具。

因此 token flow 的输入应是：

```text
token_flow_calls = evidence_calls ∪ captured_calls
```

工具生成的输入仍然是：

```text
generation_calls = captured_calls
```

示例：

```text
录制前：GET /api/csrf -> evidence_calls
录制中：POST /api/orders with X-CSRF-Token -> captured_calls

结果：
- /api/csrf 不生成独立工具
- /api/orders 生成工具
- /api/orders 的工具可以引用 /api/csrf 作为 token producer
```

## 11. 前端展示语义

前端不需要展示 evidence-only 调用为工具候选。

前端展示规则：

- “生成中工具”来自 generation candidates。
- “已生成工具”来自 tool definitions。
- evidence-only 调用不计入工具数量。
- 停止录制日志不应把异步生成状态描述成“本次同步生成了 N 个工具”。
- 如果需要未来增强，可以在 debug/diagnostics 区域展示 evidence-only 数量，但不进入主工具列表。

## 12. 崩溃和重试语义

实时生成的崩溃恢复仍以 generation calls 为事实源：

- worker 崩溃后，`captured_calls` 可重建 generation candidates。
- evidence-only calls 不参与 candidate reconcile。
- 429/rate_limited 重试只作用于 generation candidates。
- token flow 在重试时仍可读取 evidence-only calls。

## 13. 验收标准

### 13.1 录制验收

场景：

1. 打开页面，页面自动请求 `GET /api/csrf`。
2. 点击开始录制。
3. 用户点击搜索，触发 `GET /api/orders`。
4. 点击停止录制。

预期：

- `/api/csrf` 不出现在工具候选或已生成工具中。
- `/api/orders` 出现在生成中或已生成工具中。
- 如果 `/api/orders` 依赖 CSRF token，token flow 仍能引用 `/api/csrf`。

### 13.2 自由分析验收

场景：

1. 页面初始化请求 `GET /api/csrf`。
2. 开始自由分析。
3. probe “搜索”按钮触发 `GET /api/orders`。

预期：

- `/api/csrf` 不生成工具。
- `/api/orders` 生成工具。
- token flow 可使用 `/api/csrf`。

### 13.3 定向分析验收

场景：

1. 开始定向分析前 buffer 中已有 `GET /api/session`。
2. directed step 执行后触发 `POST /api/orders`。

预期：

- `/api/session` 不生成工具。
- `/api/orders` 生成工具。
- `/api/orders` 可使用 `/api/session` 中产生的动态 token/session evidence。

### 13.4 Reconcile 验收

场景：

1. session 中同时存在 evidence-only `/api/csrf` 和 generation `/api/orders`。
2. 调用 candidate reconcile。

预期：

- 只为 `/api/orders` 创建或更新 candidate。
- 不为 `/api/csrf` 创建 candidate。

## 14. 风险与约束

### 14.1 Token Flow 输入遗漏风险

如果 token flow 仍只读取 `captured_calls`，会导致窗口外 CSRF/session producer 丢失。因此实现时必须明确把 token flow 输入改为 evidence 与 generation 的并集。

### 14.2 Evidence 被 Reconcile 误用风险

如果未来有人把 `reconcile_generation_candidates()` 改成扫描所有 session calls，evidence-only 调用会重新被生成工具。因此 reconcile 必须保持只扫描 `captured_calls`。

### 14.3 时间边界歧义

第一版不依赖 timestamp 判断窗口边界，而依赖 drain 位置判断语义：

- start/analyze pre-drain -> evidence-only
- recording/probe/step drain -> generation

这样更贴近当前 architecture，也避免 response 结束时间和用户动作时间之间的竞态。

### 14.4 前端认知风险

如果前端只显示“当前 0 个工具”，用户可能误以为 token flow evidence 丢失。主 UI 不展示 evidence-only 是正确的，但后续可考虑在诊断区域展示 evidence 数量。

## 15. 与现有设计的关系

本设计是对以下设计的边界补充：

- `2026-04-27-api-monitor-token-flow-design.md`
- `2026-04-30-api-monitor-realtime-tool-generation-design.md`

它不改变实时工具生成的核心机制，只补充一个关键语义：

```text
不是所有 captured network calls 都是 generation calls。
```

只有分析/录制窗口内的调用才是工具生成事实源；窗口外调用只能作为 evidence。
