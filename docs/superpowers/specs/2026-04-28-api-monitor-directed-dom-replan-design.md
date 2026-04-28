# API Monitor 定向分析 DOM 重规划设计

## 背景

当前 API Monitor 的定向分析会先构建一次精简 DOM 快照，再让 LLM 生成一整套多步操作计划，最后顺序执行这些动作。这个模式有一个明显上限：第一步操作一旦改变 DOM，后续动作仍然来自旧页面状态。搜索、详情页、弹窗、tab 切换、展开区域等流程都会因此变脆，表现为“只能稳定操作第一步”。

本设计的目标是把定向分析从一次性规划提升为动态浏览器分析，同时保持现有 RPA 架构军规：

- 录制与分析仍坚持 trace-first，可观察、可追踪。
- 失败事实、当前 URL/title、DOM 状态、捕获到的 API 调用是主要输入。
- 安全策略继续分层：系统风险在执行前拦截；selector 不稳定、页面结构变化、导航慢等稳定性问题在执行后基于事实进入观察与重规划。
- 经验提示和启发式规则不能主导主路径。

## 推荐方案

把当前的一次性多步执行改成有边界的 `Sense -> Plan -> Act -> Observe -> Replan` 循环。

每一轮循环都重新构建当前页面快照，把原始用户指令、运行历史和最新页面状态一起压缩后交给 LLM；LLM 每次只返回一个下一步动作，或者返回 `done` 表示目标已完成。系统执行被允许的动作后，等待 DOM/网络短暂稳定，抽取新捕获的 API 调用，再把这些事实喂给下一轮。

这样每一步操作都基于当前 DOM，而不是基于初始页面的过期假设。

## 备选方案

### 只刷新快照，但继续执行原计划

成本最低，但没有解决核心问题。系统虽然能观察到 DOM 改变，却仍然会执行第一次快照里规划出来的后续动作。它只能改善日志，不能让下一步操作真正适配新页面。

### 做完整浏览器 Agent 状态机

能力最强，但会吞掉 API Monitor 的职责边界，并且容易和现有 RPA runtime agent 重叠。API Monitor 应继续聚焦于定向 API 捕获和 MCP 工具生成，而不是变成通用浏览器 Agent。

### 动态单步重规划

这是推荐方案。它能让定向分析跟随 DOM 变化继续行动，同时保持范围收敛、可测试，并且符合 API Monitor 当前职责。

## 架构设计

### 单步决策模型

在现有 `DirectedAction` 旁边新增单步决策模型：

- `goal_status`：`continue`、`done` 或 `blocked`
- `next_action`：当 `goal_status` 为 `continue` 时返回一个 `DirectedAction`
- `summary`：本轮决策摘要
- `expected_change`：执行动作后预期发生什么变化
- `done_reason`：目标完成或被阻塞的原因

现有 `DirectedAction` 字段继续用于 Playwright 操作细节和业务风险分类。

### 运行状态

在 `analyze_directed_page` 内维护一次定向运行的内存状态：

- 原始用户指令
- 当前模式和业务安全级别
- 当前步骤序号
- 已执行动作
- 已跳过动作
- 动作失败记录
- 本次运行中新捕获的 API 调用
- 当前页面 URL 和 title
- 最新 DOM digest
- 上一次观察摘要

这些状态只作为下一轮 prompt 的上下文，不是重型 contract 层，也不能替代真实浏览器观察。

### 观察层

每次规划前采集：

- 当前 URL
- 当前 title
- 通过 `build_page_snapshot` 得到的原始页面快照
- 通过 `compact_recording_snapshot` 得到的精简快照
- 基于可见交互区域和页面身份生成的 DOM digest
- 最近捕获的 API 数量
- 上一步动作结果或错误

每次动作后进入短暂 settle 窗口：

- 可能发生导航时尝试等待 `domcontentloaded`
- 在 Playwright 支持时短暂等待网络安静
- 轮询 DOM digest，直到稳定或超时

settle 逻辑必须是 best-effort 且有上限，不能变成无限等待。

## Planner Prompt

把当前 prompt 从“生成最短可执行操作计划”改成“基于当前页面状态选择一个下一步操作”。

Prompt 规则：

- 只返回 JSON。
- 当前精简快照是事实源。
- 历史动作只说明已经发生过什么，不是待执行脚本。
- 如果页面已经变化，必须从新 DOM 推理下一步。
- 当目标 API 已捕获或用户目标已满足时返回 `done`。
- 当没有安全或有意义的浏览器动作时返回 `blocked`。
- 不允许返回 Python、shell、文件、权限、本地系统操作。
- 对候选动作继续给出业务风险分类。

## 执行流程

`ApiMonitorSessionManager.analyze_directed_page` 应改为：

1. 把历史请求 drain 到 session history。
2. 发送 `analysis_started`。
3. 在 `max_steps` 上限内循环：
   - 发送 snapshot progress
   - 构建当前观察对象
   - 调用 `build_directed_step_decision`
   - 发送 `directed_step_planned`
   - 遇到 `done` 或 `blocked` 时停止
   - 应用业务安全过滤
   - 被安全策略阻止时发送 `directed_action_skipped`
   - 执行一个被允许的动作
   - 标记动作时间，用于 request evidence
   - 发送 `directed_step_executed`
   - 观察 DOM/网络结果
   - drain 捕获到的新 API 调用
   - 发送 `directed_step_observed`
4. 使用本次定向运行中新捕获的 API 调用生成工具定义。
5. 发送 `analysis_complete`。

迁移期可以保留现有 `directed_plan_ready`、`directed_action_detail`、`directed_action_executed` 事件，避免前端兼容性问题；新的 step 级事件应成为后续主接口。

## 安全策略

保留当前两个定向模式：

- `safe_directed`：只执行 `risk=safe` 的动作；被跳过的 unsafe 动作会进入运行历史，让下一轮 planner 尝试安全替代路径或停止为 blocked。
- `directed`：用户明确选择该模式时，允许业务层面的 unsafe 动作。

系统安全高于业务安全。无论哪种模式，planner 都不能请求 shell 命令、文件操作、本地权限、下载目录或主机访问。

selector 脆弱、元素缺失、空提取、导航慢、页面结构变化不应预拦截。这些都属于执行事实，进入下一轮观察和重规划。

## 失败处理

当动作失败时：

- 记录原始异常消息
- 保留当前 URL/title
- 尽可能重新构建页面快照
- drain 失败过程中已经发生的 API 调用
- 把失败加入运行历史
- 未达到最大失败次数时，让 planner 尝试 repair step

以下情况停止为 `blocked`：

- 重复动作失败且没有 DOM/API 进展
- 页面上没有有意义的可见候选动作
- 达到最大步骤数或最大耗时
- `safe_directed` 中唯一必要动作被业务安全策略阻止

## API 捕获与工具生成

本次运行结果只把定向分析期间新捕获的 API 调用传给 `_generate_tools_from_calls`。页面加载前已有的请求仍保留在 session history 中，用于 token flow 分析。

当新捕获的 API 调用已经明显满足用户指令时，理论上可以提前停止；第一版实现可以采用保守策略：只有当 LLM 返回 `done` 或达到步骤预算时停止。

## 前端与 SSE 兼容

现有前端可以继续消费当前事件。新增以下可选事件，用于更清晰展示动态过程：

- `directed_step_snapshot`
- `directed_step_planned`
- `directed_step_executed`
- `directed_step_observed`
- `directed_replan`
- `directed_done`

第一版不需要做前端重设计。UI 可以先把这些事件渲染为进度日志。

## 测试计划

新增后端测试：

- 第一步动作改变 DOM 后，第二轮基于新的 snapshot 重新规划
- 初始计划中的过期第二步不会被盲目执行
- `safe_directed` 每一轮都过滤 unsafe 动作
- 动作失败会进入下一轮 planner 上下文
- 每一步后都会 drain 捕获到的 API，并用于工具生成
- 最大步骤预算可以阻止无限循环
- `safe_directed` 和 `directed` 的路由分发保持兼容

现有 locator 构建、动作执行、业务安全过滤相关单元测试应继续通过。

## 范围

本次包含：

- 后端 planner 模型调整
- 后端定向执行循环
- snapshot/observation helper
- SSE 事件补充
- 聚焦测试

本次不包含：

- 通用浏览器 Agent
- 新前端工作流设计
- 持久化运行历史
- 重型 DOM contract 生成
- 规则库或站点模板驱动的规划

## 实施备注

优先新增函数，再逐步替换旧路径，避免破坏现有测试和调用方：

- `build_directed_step_decision`
- `execute_directed_step`
- `observe_directed_page`
- `run_directed_analysis_loop`

初期保留 `build_directed_plan` 和 `execute_directed_plan`，用于兼容和定向测试。动态循环稳定后，再考虑废弃旧的多步计划执行。
