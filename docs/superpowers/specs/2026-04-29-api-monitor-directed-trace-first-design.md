# API Monitor MCP 定向分析 Trace-first 改造设计

> Status: Proposed design.
>
> Baseline: `2026-04-28-api-monitor-directed-dom-replan-design.md` 已将定向分析改为每轮重新观察 DOM、单步规划、执行后重规划。本设计在该基础上增加 API Monitor 专用 trace-first 证据链和轻量 retry guard。

Date: 2026-04-29

## 1. Problem Statement

API Monitor MCP 的定向分析当前已经具备动态重规划能力：每一轮基于当前页面快照让 LLM 选择一个下一步动作，执行后再观察页面和网络结果。失败会写入本次运行内存态 `run_history`，下一轮 planner 可以看到失败事实。

这个机制能让 LLM 避免部分重复错误，但它仍有几个边界问题：

- 失败记录是临时、扁平的 dict，缺少 before/after 页面事实、动作 fingerprint、失败次数和捕获 API 关联。
- 下一轮 planner 主要依赖自由形式的 `run_history`，容易在两个失败动作之间反复切换。
- 失败过程中如果页面或网络有变化，现有记录不能完整表达“动作失败但 API 已捕获”这类事实。
- UI 和调试层看不到完整步骤证据链，只能看到 SSE 日志。
- 这些定向分析步骤不会沉淀为普通 RPA trace，也不应该直接进入 RPA Skill 编译链路。

本设计目标是在不改变 API Monitor MCP 主产物的前提下，为定向分析引入 trace-first 证据链。

Core principle:

```text
Directed analysis time: operate browser, record factual directed traces, capture API calls.
MCP generation time: generate tools from captured API calls, using directed traces only as evidence and diagnostics.
```

## 2. Goals

- 保持 API Monitor MCP 的主目标：通过页面操作诱发 API 请求，并从 captured calls 生成 MCP/API 工具定义。
- 把每一轮定向分析动作记录为结构化 Directed Trace。
- 将失败事实、页面变化、捕获 API 和动作 fingerprint 带入下一轮 planner。
- 防止同一动作重复失败，以及 A/B/A/B 式失败横跳。
- 保留“失败事实优先，经验提示辅助”的架构边界。
- 不把 API Monitor 定向分析变成普通 RPA 技能录制。
- 不让 RPA trace compiler 或 Skill exporter 主导 MCP 工具生成。

## 3. Non-goals

- 不复用 `RPAAcceptedTrace` 作为 API Monitor 定向分析的主模型。
- 不把 directed traces 直接编译成 `skill.py`。
- 不把 `_generate_tools_from_calls(...)` 替换为 trace compiler。
- 不引入站点模板、经验库或 selector 规则作为主路径 planner。
- 不在执行前拦截 selector 脆弱、页面结构变化、导航慢等稳定性问题。
- 不持久化所有历史 analyze 运行作为长期经验库。第一版只需要保留当前 session 的 directed traces。
- 不做新的复杂前端工作流。第一版只补充日志和可选 timeline 数据。

## 4. Recommended Architecture

新增 API Monitor 专用 Directed Trace 层。它记录定向分析的每一步事实，但不改变最终 MCP 工具生成路径。

```text
observe current page
        ->
create DirectedAnalysisTrace(before)
        ->
build planner context from current DOM + directed trace summary
        ->
LLM returns one next action or done/blocked
        ->
attach decision and action fingerprint
        ->
retry guard checks repeated factual failures
        ->
execute allowed action
        ->
record success/failure/skipped facts
        ->
observe after page and drain captured API calls
        ->
update DirectedAnalysisTrace(after + captured_call_ids)
        ->
repeat until done/blocked/budget exhausted
        ->
generate MCP tools from CapturedApiCall samples
```

The key boundary:

```text
Directed traces guide planning and debugging.
Captured API calls generate MCP tools.
```

## 5. Data Model

Add API Monitor-specific models in `backend/rpa/api_monitor/models.py` or a new focused module such as `directed_trace.py`.

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

Extend `ApiMonitorSession`:

```python
directed_traces: List[DirectedAnalysisTrace] = Field(default_factory=list)
```

Keep this separate from ordinary RPA session traces:

- RPA skill recording uses `RPAAcceptedTrace`.
- API Monitor directed analysis uses `DirectedAnalysisTrace`.

The names should stay intentionally different so future code does not accidentally send API Monitor traces to the RPA Skill compiler.

## 6. Action Fingerprint

Every planned action should get a stable fingerprint based on semantic action identity:

```text
action + locator.method + locator.role + locator.name/value/selector + value/key
```

Examples:

```text
click|role|button|搜索
fill|placeholder|订单号|12345
press|role|textbox|搜索|Enter
```

The fingerprint is not a selector quality score. It is only a factual identity key for retry accounting.

Do not block an action merely because a selector looks fragile. Only block or discourage actions after actual repeated failures.

## 7. Retry Context

Replace the free-form planner history input with a compact structured retry context derived from directed traces.

The planner should receive:

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
      "reason": "failed 2 consecutive times with Locator not found"
    }
  ],
  "loop_detected": false,
  "successful_transitions": [],
  "captured_api_summary": []
}
```

`run_history` may remain during migration, but `build_directed_step_decision(...)` should prefer the structured retry context once available.

## 8. Retry Guard Rules

Retry guard must be small, factual, and bounded. It is not an experience system.

Recommended first-version rules:

- Same fingerprint failed 2 consecutive times: add it to `blocked_actions` in planner context.
- Same fingerprint failed 3 total times in one directed run: skip exact same action before execution and record `retry_guard_skipped`.
- Recent failed fingerprints match `A, B, A, B`: set `loop_detected=true` and include both fingerprints in `blocked_actions`.
- If a failed step captured API calls, do not treat it as useless. Attach call IDs to the trace and let completion check decide whether the target was satisfied.

The guard should not rewrite a different action into another action. It can only:

- inform the planner,
- skip an exact repeated failed fingerprint,
- stop as blocked when no meaningful alternative remains.

## 9. Planner Prompt Changes

The directed step prompt should continue to emphasize:

- Current compact DOM is the fact source.
- History only describes what happened.
- If URL or DOM changed, reason from the new page.
- Return one action, done, or blocked.
- Do not return shell, files, permissions, downloads, or local system operations.

Add retry-specific instructions:

- Avoid actions listed in `blocked_actions` unless the current DOM changed in a way that makes the previous failure irrelevant.
- If `loop_detected=true`, choose a genuinely different route, wait for a state change, or return blocked.
- If recent traces show captured API calls matching the user goal, return done.
- Treat errors and captured calls as facts, not as suggestions to blindly retry.

## 10. Execution Flow Changes

`ApiMonitorSessionManager.analyze_directed_page` should change from appending plain dicts to `run_history` into a trace lifecycle.

For each step:

1. Observe page and create `DirectedAnalysisTrace(before=...)`.
2. Build planner context from:
   - current compact snapshot,
   - current observation,
   - recent directed traces,
   - retry context,
   - captured API summary.
3. Call `build_directed_step_decision(...)`.
4. Attach decision and action fingerprint to the trace.
5. If planner fails, record `planner_failed`, update trace, emit `directed_replan`, continue until failure budget.
6. If planner returns `done` or `blocked`, record final decision and stop.
7. Apply business safety filtering.
8. Apply retry guard.
9. Execute the action if allowed.
10. Drain captured calls even if execution failed.
11. Observe after state.
12. Attach execution result, after observation, and captured call IDs.
13. Emit trace update events.
14. Continue.

At the end, call `_generate_tools_from_calls(session_id, directed_calls, source="auto", ...)` unchanged.

## 11. SSE and Frontend

Keep existing SSE events for compatibility:

- `directed_step_snapshot`
- `directed_step_planned`
- `directed_action_detail`
- `directed_step_executed`
- `directed_replan`
- `directed_step_observed`
- `directed_done`
- `analysis_complete`

Add optional trace events:

- `directed_trace_added`
- `directed_trace_updated`

The first frontend iteration can render these as logs. A later iteration can show a timeline with:

- step number,
- planned action,
- result,
- error,
- URL/DOM changed,
- captured API count,
- retry guard note.

## 12. Persistence and Scope

First version:

- Store directed traces on the in-memory `ApiMonitorSession`.
- Include traces in detail/debug responses only if useful for the existing UI.
- Do not publish directed traces as part of MCP server definitions.
- Do not use directed traces as long-term site experience.

Future optional extensions:

- Store directed traces in MongoDB for session replay/debug.
- Export trace evidence into API Monitor MCP metadata.
- Build a replay helper that re-executes directed traces to reproduce API capture.

These extensions are not required for the first implementation.

## 13. Error Handling

Record distinct execution states:

- `planner_failed`: LLM or schema parsing failed.
- `failed`: Playwright action raised an exception.
- `skipped`: business safety blocked an unsafe action.
- `retry_guard_skipped`: exact repeated failed fingerprint skipped.
- `executed`: action completed without exception.
- `completed_with_calls`: optional marker when a step produced target API calls even if page state did not visibly change.

Every error trace should retain:

- original error text,
- step number,
- action description,
- locator code or locator summary,
- before URL/title/digest,
- after URL/title/digest when available,
- captured call IDs from the failure window.

## 14. Testing Plan

Add or update backend tests for:

- Action failure creates a `DirectedAnalysisTrace` and feeds structured failure context to the next planner call.
- Planner failure creates a trace with `planner_failed` and does not become `analysis_error` unless the outer loop fails unexpectedly.
- Same action fingerprint repeated failures appear in `blocked_actions`.
- The third exact same failed fingerprint is skipped with `retry_guard_skipped`.
- A/B/A/B failed fingerprints set `loop_detected=true`.
- Failed execution still drains captured calls and attaches call IDs to the trace.
- Captured calls remain the only input to `_generate_tools_from_calls(...)`.
- Existing `safe_directed` business safety behavior remains intact.
- Existing directed analysis SSE compatibility remains intact.

No test should require real network calls or a live browser when a fake page and fake capture object can cover the behavior.

## 15. Migration Plan

### Phase 1: Trace models and helpers

- Add Directed Trace models.
- Add helper to create before/after observations.
- Add helper to compute action fingerprint.
- Add helper to summarize recent directed traces.

### Phase 2: Trace lifecycle in directed analysis

- Replace `run_history.append(...)` call sites with directed trace updates.
- Keep a compatibility `run_history` summary if needed for existing prompt/tests.
- Emit `directed_trace_added` and `directed_trace_updated`.

### Phase 3: Structured planner context

- Update `build_directed_step_decision(...)` to accept retry context.
- Update prompt to use recent traces, blocked actions, and captured API summary.
- Keep current compact snapshot as the primary fact source.

### Phase 4: Retry guard

- Add exact fingerprint repeat detection.
- Add A/B/A/B loop detection.
- Add pre-execution skip for exact repeated failed actions.

### Phase 5: Tests and UI polish

- Add focused backend tests.
- Update frontend logs for trace events if needed.
- Leave full timeline UI for later unless debugging needs demand it.

## 16. Acceptance Criteria

The change is complete when:

- Every directed analysis step has a trace record.
- Action and planner failures are visible in directed traces.
- The next planner call receives structured retry context derived from traces.
- Repeated exact failures are discouraged or skipped without introducing site-specific heuristics.
- Captured API calls still generate MCP tools through the existing `_generate_tools_from_calls(...)` path.
- Existing API Monitor MCP publication and tool execution behavior remains unchanged.
- Tests cover failure carryover, retry guard behavior, and captured-call preservation.

## 17. Open Decisions

These decisions should be made before implementation:

- Whether directed traces should be exposed in the existing API Monitor session detail endpoint in the first version.
- Whether `run_history` should be removed immediately or kept as a compatibility summary for one migration cycle.
- Whether retry guard thresholds should be constants in code or fields on analysis mode config.

Recommended defaults:

- Expose directed traces only through SSE first.
- Keep `run_history` as a derived compatibility summary during migration.
- Start with code constants: `consecutive_failure_limit=2`, `total_failure_skip_limit=3`, `loop_window=4`.
