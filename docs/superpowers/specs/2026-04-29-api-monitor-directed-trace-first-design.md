# API Monitor MCP 定向分析 Trace-first 改造设计

> 状态：设计提案。
>
> 基线：`2026-04-28-api-monitor-directed-dom-replan-design.md` 已将定向分析改为每轮重新观察 DOM、单步规划、执行后重规划。本设计在该基础上增加 API Monitor 专用 trace-first 证据链和轻量重试保护。

日期：2026-04-29

## 1. 问题陈述

API Monitor MCP 的定向分析当前已经具备动态重规划能力：每一轮基于当前页面快照让 LLM 选择一个下一步动作，执行后再观察页面和网络结果。失败会写入本次运行内存态 `run_history`，下一轮 planner 可以看到失败事实。

这个机制能让 LLM 避免部分重复错误，但它仍有几个边界问题：

- 失败记录是临时、扁平的 dict，缺少 before/after 页面事实、动作 fingerprint、失败次数和捕获 API 关联。
- 下一轮 planner 主要依赖自由形式的 `run_history`，容易在两个失败动作之间反复切换。
- 失败过程中如果页面或网络有变化，现有记录不能完整表达“动作失败但 API 已捕获”这类事实。
- UI 和调试层看不到完整步骤证据链，只能看到 SSE 日志。
- 这些定向分析步骤不会沉淀为普通 RPA trace，也不应该直接进入 RPA Skill 编译链路。

本设计目标是在不改变 API Monitor MCP 主产物的前提下，为定向分析引入 trace-first 证据链。

核心原则：

```text
定向分析阶段：真实操作浏览器，记录事实型定向 traces，捕获 API 调用。
MCP 生成阶段：从捕获到的 API 调用生成工具，定向 traces 只作为证据和诊断信息。
```

## 2. 目标

- 保持 API Monitor MCP 的主目标：通过页面操作诱发 API 请求，并从捕获到的调用生成 MCP/API 工具定义。
- 把每一轮定向分析动作记录为结构化 Directed Trace。
- 将失败事实、页面变化、捕获 API 和动作 fingerprint 带入下一轮 planner。
- 防止同一动作重复失败，以及 A/B/A/B 式失败横跳。
- 保留“失败事实优先，经验提示辅助”的架构边界。
- 不把 API Monitor 定向分析变成普通 RPA 技能录制。
- 不让 RPA trace 编译器或 Skill exporter 主导 MCP 工具生成。

## 3. 非目标

- 不复用 `RPAAcceptedTrace` 作为 API Monitor 定向分析的主模型。
- 不把定向 traces 直接编译成 `skill.py`。
- 不把 `_generate_tools_from_calls(...)` 替换为 trace 编译器。
- 不引入站点模板、经验库或 selector 规则作为主路径 planner。
- 不在执行前拦截 selector 脆弱、页面结构变化、导航慢等稳定性问题。
- 不持久化所有历史 analyze 运行作为长期经验库。第一版只需要保留当前 session 的定向 traces。
- 不做新的复杂前端工作流。第一版只补充日志和可选时间线数据。

## 4. 推荐架构

新增 API Monitor 专用 Directed Trace 层。它记录定向分析的每一步事实，但不改变最终 MCP 工具生成路径。

```text
观察当前页面
        ->
创建 DirectedAnalysisTrace(before)
        ->
从当前 DOM 和定向 trace 摘要构建 planner 上下文
        ->
LLM 返回一个下一步动作，或返回 done/blocked
        ->
写入决策和动作 fingerprint
        ->
重试保护检查重复失败事实
        ->
执行允许的动作
        ->
记录成功、失败或跳过事实
        ->
观察动作后的页面，并 drain 捕获到的 API 调用
        ->
更新 DirectedAnalysisTrace(after + captured_call_ids)
        ->
重复直到 done、blocked 或预算耗尽
        ->
从 CapturedApiCall 样本生成 MCP 工具
```

关键边界：

```text
定向 traces 指导规划和调试。
捕获到的 API 调用生成 MCP 工具。
```

## 5. 数据模型

在 `backend/rpa/api_monitor/models.py` 中增加 API Monitor 专用模型，或新建更聚焦的模块，例如 `directed_trace.py`。

```python
class DirectedObservation(BaseModel):
    url: str = ""
    title: str = ""
    dom_digest: str = ""
    compact_snapshot_summary: Dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=datetime.now)


class DirectedDecisionSnapshot(BaseModel):
    goal_status: str = "continue"
    summary: str = ""
    expected_change: str = ""
    done_reason: str = ""
    action: Dict[str, Any] | None = None
    risk: str = "safe"


class DirectedExecutionSnapshot(BaseModel):
    result: str  # executed, failed, skipped, planner_failed, retry_guard_skipped
    error: str = ""
    duration_ms: int | None = None
    url_changed: bool = False
    dom_changed: bool = False


class DirectedAnalysisTrace(BaseModel):
    id: str = Field(default_factory=_gen_id)
    step: int
    instruction: str
    mode: str
    before: DirectedObservation
    decision: DirectedDecisionSnapshot | None = None
    action_fingerprint: str | None = None
    execution: DirectedExecutionSnapshot | None = None
    after: DirectedObservation | None = None
    captured_call_ids: List[str] = Field(default_factory=list)
    retry_advice: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

扩展 `ApiMonitorSession`：

```python
directed_traces: List[DirectedAnalysisTrace] = Field(default_factory=list)
```

它必须与普通 RPA session traces 保持隔离：

- RPA 技能录制使用 `RPAAcceptedTrace`。
- API Monitor 定向分析使用 `DirectedAnalysisTrace`。

命名应刻意保持不同，避免未来代码误把 API Monitor traces 送进 RPA Skill 编译器。

## 6. 动作指纹

每一个规划动作都应根据语义动作身份生成稳定动作指纹：

```text
action + locator.method + locator.role + locator.name/value/selector + value/key
```

示例：

```text
click|role|button|搜索
fill|placeholder|订单号|12345
press|role|textbox|搜索|Enter
```

动作指纹不是 selector 质量评分。它只是用于重试统计的事实身份键。

不要仅因为 selector 看起来脆弱就阻止动作。只有在真实重复失败之后，才阻止或弱化同一个动作。

## 7. 重试上下文

把自由形式的 planner history 输入替换为由定向 traces 派生出的紧凑结构化重试上下文。

planner 应收到类似下面的结构：

```json
{
  "recent_traces": [
    {
      "step": 3,
      "result": "failed",
      "action": "click",
      "locator": "role=button[name=搜索]",
      "fingerprint": "click|role|button|搜索",
      "error": "Locator not found: 搜索",
      "url_changed": false,
      "dom_changed": false,
      "new_calls": []
    }
  ],
  "blocked_actions": [
    {
      "fingerprint": "click|role|button|搜索",
      "reason": "连续 2 次失败，错误为 Locator not found"
    }
  ],
  "loop_detected": false,
  "successful_transitions": [],
  "captured_api_summary": []
}
```

迁移期间可以保留 `run_history`，但一旦结构化重试上下文可用，`build_directed_step_decision(...)` 应优先使用它。

## 8. 重试保护规则

重试保护必须小而事实化，并且有明确边界。它不是经验系统。

第一版推荐规则：

- 同一个动作指纹连续失败 2 次：加入 planner context 的 `blocked_actions`。
- 同一个动作指纹在一次 directed run 中累计失败 3 次：执行前跳过完全相同的动作，并记录 `retry_guard_skipped`。
- 最近失败动作指纹呈现 `A, B, A, B`：设置 `loop_detected=true`，并把两个 fingerprint 都加入 `blocked_actions`。
- 如果失败步骤捕获到了 API 调用，不要把它视为无效步骤。应把 call IDs 附加到 trace，由完成检查判断是否已经满足目标。

重试保护不应把一个动作改写成另一个动作。它只能：

- 通知 planner；
- 跳过完全相同且重复失败的动作指纹；
- 在没有有意义替代路径时停止为 blocked。

## 9. Planner Prompt 改造

directed step prompt 应继续强调：

- 当前 compact DOM 是事实源。
- 历史只描述已经发生过什么。
- 如果 URL 或 DOM 已变化，必须基于新页面推理。
- 只返回一个动作、done 或 blocked。
- 不允许返回 shell、文件、权限、下载或本地系统操作。

增加重试相关指令：

- 避免选择 `blocked_actions` 中的动作，除非当前 DOM 已发生变化，导致此前失败不再相关。
- 如果 `loop_detected=true`，选择真正不同的路线、等待状态变化，或返回 blocked。
- 如果最近 traces 显示捕获到的 API 调用已匹配用户目标，返回 done。
- 把错误和捕获到的调用视为事实，而不是盲目重试的建议。

## 10. 执行流程改造

`ApiMonitorSessionManager.analyze_directed_page` 应从向 `run_history` 追加普通 dict，改为维护一个 trace 生命周期。

每一步：

1. 观察页面并创建 `DirectedAnalysisTrace(before=...)`。
2. 从以下信息构建 planner context：
   - 当前 compact snapshot；
   - 当前 observation；
   - 最近的定向 traces；
   - 重试上下文；
   - captured API summary。
3. 调用 `build_directed_step_decision(...)`。
4. 将 decision 和 action fingerprint 写入 trace。
5. 如果 planner 失败，记录 `planner_failed`，更新 trace，发送 `directed_replan`，并在失败预算内继续。
6. 如果 planner 返回 `done` 或 `blocked`，记录最终决策并停止。
7. 应用业务安全过滤。
8. 应用重试保护。
9. 如果动作被允许，执行动作。
10. 即使执行失败，也 drain 捕获到的 API 调用。
11. 观察 after state。
12. 写入 execution result、after observation 和 captured call IDs。
13. 发送 trace 更新事件。
14. 继续下一轮。

结束时，继续原样调用 `_generate_tools_from_calls(session_id, directed_calls, source="auto", ...)`。

## 11. SSE 与前端

为了兼容，保留现有 SSE 事件：

- `directed_step_snapshot`
- `directed_step_planned`
- `directed_action_detail`
- `directed_step_executed`
- `directed_replan`
- `directed_step_observed`
- `directed_done`
- `analysis_complete`

新增可选 trace 事件：

- `directed_trace_added`
- `directed_trace_updated`

第一版前端可以先把这些事件渲染为日志。后续可以展示时间线，包括：

- 步骤编号；
- 规划动作；
- 结果；
- 错误；
- URL/DOM 是否变化；
- 捕获 API 数量；
- 重试保护备注。

## 12. 持久化与范围

第一版：

- 将定向 traces 存在内存态 `ApiMonitorSession` 上。
- 只有在现有 UI/debug 需要时，才把 traces 包含进 detail/debug 响应。
- 不把定向 traces 发布为 MCP server definitions 的一部分。
- 不把定向 traces 当作长期站点经验使用。

未来可选扩展：

- 将定向 traces 存入 MongoDB，用于 session replay/debug。
- 将 trace evidence 导出到 API Monitor MCP metadata。
- 构建 replay helper，重新执行定向 traces 以复现 API 捕获。

这些扩展不属于第一版实现要求。

## 13. 错误处理

记录不同执行状态：

- `planner_failed`：LLM 或 schema 解析失败。
- `failed`：Playwright 动作抛出异常。
- `skipped`：业务安全策略阻止了 unsafe 动作。
- `retry_guard_skipped`：完全相同的重复失败动作指纹被跳过。
- `executed`：动作无异常完成。
- `completed_with_calls`：可选标记；表示页面状态未明显变化，但步骤产生了目标 API 调用。

每条错误 trace 应保留：

- 原始错误文本；
- 步骤编号；
- 动作描述；
- locator code 或 locator summary；
- before URL/title/digest；
- 可用时记录 after URL/title/digest；
- 失败窗口内捕获到的 call IDs。

## 14. 测试计划

新增或更新后端测试：

- action 失败会创建 `DirectedAnalysisTrace`，并把结构化失败上下文传给下一次 planner 调用。
- planner 失败会创建带 `planner_failed` 的 trace；除非外层循环意外失败，否则不变成 `analysis_error`。
- 同一个 action fingerprint 重复失败后会出现在 `blocked_actions`。
- 第三次完全相同失败动作指纹会以 `retry_guard_skipped` 跳过。
- A/B/A/B 失败动作指纹会设置 `loop_detected=true`。
- 执行失败仍会 drain 捕获到的 API 调用，并把 call IDs 附加到 trace。
- 捕获到的 API 调用仍然是 `_generate_tools_from_calls(...)` 的唯一输入。
- 现有 `safe_directed` 业务安全行为保持不变。
- 现有 directed analysis SSE 兼容性保持不变。

只要 fake page 和 fake capture object 可以覆盖行为，测试就不应依赖真实网络调用或真实浏览器。

## 15. 迁移计划

### 阶段 1：Trace 模型与辅助函数

- 增加 Directed Trace models。
- 增加创建 before/after observations 的 helper。
- 增加计算 action fingerprint 的 helper。
- 增加汇总最近定向 traces 的 helper。

### 阶段 2：定向分析中的 Trace 生命周期

- 将 `run_history.append(...)` 调用点替换为 directed trace updates。
- 如现有 prompt/tests 仍需要，保留一个兼容性的 `run_history` summary。
- 发送 `directed_trace_added` 和 `directed_trace_updated`。

### 阶段 3：结构化 Planner 上下文

- 更新 `build_directed_step_decision(...)`，使其接受 retry context。
- 更新 prompt，使其使用 recent traces、blocked actions 和 captured API summary。
- 保持当前 compact snapshot 作为主要事实源。

### 阶段 4：重试保护

- 增加完全相同动作指纹的重复失败检测。
- 增加 A/B/A/B 循环检测。
- 增加针对完全重复失败动作的执行前跳过。

### 阶段 5：测试与 UI 微调

- 增加聚焦的后端测试。
- 如有需要，更新前端日志以展示 trace 事件。
- 完整时间线 UI 留到后续，除非调试需求已经强烈要求。

## 16. 验收标准

满足以下条件时，本次改造完成：

- 每一个 directed analysis step 都有 trace record。
- action 和 planner failures 能在定向 traces 中看到。
- 下一次 planner call 会收到由 traces 派生的结构化重试上下文。
- 不引入站点特定启发式的情况下，重复的完全相同失败会被弱化或跳过。
- 捕获到的 API 调用仍然通过现有 `_generate_tools_from_calls(...)` 路径生成 MCP 工具。
- 现有 API Monitor MCP 发布和工具执行行为保持不变。
- 测试覆盖失败传递、重试保护行为和捕获调用保留。

## 17. 待决策事项

实现前应确认以下决策：

- 第一版是否要在现有 API Monitor session detail endpoint 中暴露定向 traces。
- `run_history` 是立即移除，还是作为兼容 summary 保留一个迁移周期。
- 重试保护阈值是写成代码常量，还是放进 analysis mode config。

推荐默认值：

- 第一版只通过 SSE 暴露定向 traces。
- 迁移期间把 `run_history` 保留为派生兼容 summary。
- 先使用代码常量：`consecutive_failure_limit=2`、`total_failure_skip_limit=3`、`loop_window=4`。
