# API Monitor Directed Trace-first Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 API Monitor MCP 定向分析增加专用 trace-first 证据链、结构化重试上下文和轻量重试保护，同时保持 MCP 工具仍由捕获到的 API 调用生成。

**Architecture:** 后端新增 `DirectedAnalysisTrace` 数据模型和一组纯函数，用于记录每轮定向分析的 before/decision/execution/after/captured calls。`analyze_directed_page` 改为写入 directed traces，并从 traces 派生 retry context 传给 planner；retry guard 只基于事实指纹限制重复失败，成功步骤会清除 `blocked_actions`/`block_steps`。工具生成路径继续使用 `_generate_tools_from_calls(...)`，不接入 RPA Skill 编译器。

**Tech Stack:** Python 3.13, FastAPI SSE, Pydantic v2, Playwright async API, pytest, LangChain model invocation.

---

## 文件结构

- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
  - 添加 `DirectedObservation`、`DirectedDecisionSnapshot`、`DirectedExecutionSnapshot`、`DirectedAnalysisTrace`
  - 给 `ApiMonitorSession` 添加 `directed_traces`

- Create: `RpaClaw/backend/rpa/api_monitor/directed_trace.py`
  - 负责动作指纹、trace 摘要、retry context、retry guard 判断
  - 保持纯函数，不依赖 Playwright 页面对象

- Modify: `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py`
  - `build_directed_step_decision(...)` 增加可选 `retry_context`
  - prompt 增加重试上下文、解除阻塞规则和 captured API 判断要求

- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
  - 在 `analyze_directed_page` 中创建、更新、保存 directed traces
  - 发出 `directed_trace_added` 和 `directed_trace_updated`
  - 从 directed traces 派生 `run_history` 兼容摘要和 retry context
  - 在执行前应用 retry guard

- Modify: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`
  - 补充模型、纯函数、planner context、trace lifecycle、retry guard、SSE 兼容测试

- Optional Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
  - 如果前端日志需要展示新增 trace 事件，只增加事件文案映射

---

### Task 1: 添加 Directed Trace 模型

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败测试**

在 `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py` 的 import 区域加入：

```python
from backend.rpa.api_monitor.models import (
    DirectedAnalysisTrace,
    DirectedDecisionSnapshot,
    DirectedExecutionSnapshot,
    DirectedObservation,
)
```

在现有模型测试附近添加：

```python
def test_api_monitor_session_stores_directed_traces():
    session = _route_session()
    trace = DirectedAnalysisTrace(
        step=1,
        instruction="搜索订单",
        mode="safe_directed",
        before=DirectedObservation(
            url="https://example.test/orders",
            title="Orders",
            dom_digest="orders-before",
            compact_snapshot_summary={"actionable_count": 2},
        ),
        decision=DirectedDecisionSnapshot(
            goal_status="continue",
            summary="点击搜索",
            expected_change="捕获订单搜索接口",
            action={
                "action": "click",
                "locator": {"method": "role", "role": "button", "name": "搜索"},
                "description": "点击搜索",
                "risk": "safe",
            },
            risk="safe",
        ),
        action_fingerprint="click|role|button|搜索",
        execution=DirectedExecutionSnapshot(result="executed", url_changed=False, dom_changed=True),
        after=DirectedObservation(
            url="https://example.test/orders",
            title="Orders",
            dom_digest="orders-after",
            compact_snapshot_summary={"actionable_count": 3},
        ),
        captured_call_ids=["call-1"],
    )

    session.directed_traces.append(trace)

    dumped = session.model_dump(mode="json")
    assert dumped["directed_traces"][0]["step"] == 1
    assert dumped["directed_traces"][0]["execution"]["result"] == "executed"
    assert dumped["directed_traces"][0]["captured_call_ids"] == ["call-1"]
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_api_monitor_session_stores_directed_traces -q
```

Expected: FAIL，错误包含 `ImportError` 或 `cannot import name 'DirectedAnalysisTrace'`。

- [ ] **Step 3: 添加模型实现**

在 `RpaClaw/backend/rpa/api_monitor/models.py` 的 `ApiToolDefinition` 后、`ApiMonitorSession` 前添加：

```python
# ── Directed analysis trace ──────────────────────────────────────────


class DirectedObservation(BaseModel):
    url: str = ""
    title: str = ""
    dom_digest: str = ""
    compact_snapshot_summary: Dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=datetime.now)


class DirectedDecisionSnapshot(BaseModel):
    goal_status: str = "continue"
    summary: str = ""
    expected_change: str = ""
    done_reason: str = ""
    action: Optional[Dict] = None
    risk: str = "safe"


class DirectedExecutionSnapshot(BaseModel):
    result: str
    error: str = ""
    duration_ms: Optional[int] = None
    url_changed: bool = False
    dom_changed: bool = False


class DirectedAnalysisTrace(BaseModel):
    id: str = Field(default_factory=_gen_id)
    step: int
    instruction: str
    mode: str
    before: DirectedObservation
    decision: Optional[DirectedDecisionSnapshot] = None
    action_fingerprint: Optional[str] = None
    execution: Optional[DirectedExecutionSnapshot] = None
    after: Optional[DirectedObservation] = None
    captured_call_ids: List[str] = Field(default_factory=list)
    retry_advice: Dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

在 `ApiMonitorSession` 中添加字段：

```python
    directed_traces: List[DirectedAnalysisTrace] = Field(default_factory=list)
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_api_monitor_session_stores_directed_traces -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: add api monitor directed trace models"
```

---

### Task 2: 添加动作指纹与重试上下文纯函数

**Files:**
- Create: `RpaClaw/backend/rpa/api_monitor/directed_trace.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败测试**

在 `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py` 的 import 区域加入：

```python
from backend.rpa.api_monitor.directed_trace import (
    build_directed_retry_context,
    directed_action_fingerprint,
)
```

添加测试：

```python
def test_directed_action_fingerprint_uses_action_locator_and_value():
    action = DirectedAction(
        action="fill",
        locator={"method": "placeholder", "value": "订单号"},
        value="123",
        description="填写订单号",
        risk="safe",
    )

    assert directed_action_fingerprint(action) == "fill|placeholder|订单号|123"


def test_build_directed_retry_context_blocks_repeated_failures():
    traces = [
        DirectedAnalysisTrace(
            step=1,
            instruction="搜索订单",
            mode="safe_directed",
            before=DirectedObservation(dom_digest="orders"),
            action_fingerprint="click|role|button|搜索",
            execution=DirectedExecutionSnapshot(result="failed", error="Locator not found"),
        ),
        DirectedAnalysisTrace(
            step=2,
            instruction="搜索订单",
            mode="safe_directed",
            before=DirectedObservation(dom_digest="orders"),
            action_fingerprint="click|role|button|搜索",
            execution=DirectedExecutionSnapshot(result="failed", error="Locator not found"),
        ),
    ]

    context = build_directed_retry_context(traces, captured_api_summary=[])

    assert context["blocked_actions"][0]["fingerprint"] == "click|role|button|搜索"
    assert "连续 2 次失败" in context["blocked_actions"][0]["reason"]
    assert context["loop_detected"] is False
    assert context["recent_traces"][-1]["result"] == "failed"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_action_fingerprint_uses_action_locator_and_value RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_build_directed_retry_context_blocks_repeated_failures -q
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'backend.rpa.api_monitor.directed_trace'`。

- [ ] **Step 3: 添加纯函数实现**

创建 `RpaClaw/backend/rpa/api_monitor/directed_trace.py`：

```python
"""Trace helpers for API Monitor directed analysis."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Iterable

from .directed_analyzer import DirectedAction, describe_locator_code
from .models import (
    CapturedApiCall,
    DirectedAnalysisTrace,
    DirectedDecisionSnapshot,
    DirectedExecutionSnapshot,
    DirectedObservation,
)


CONSECUTIVE_FAILURE_LIMIT = 2
TOTAL_FAILURE_SKIP_LIMIT = 3
LOOP_WINDOW = 4


def directed_action_fingerprint(action: DirectedAction | dict[str, Any] | None) -> str:
    if action is None:
        return ""
    payload = action.model_dump() if isinstance(action, DirectedAction) else dict(action)
    locator = payload.get("locator") if isinstance(payload.get("locator"), dict) else {}
    parts = [
        str(payload.get("action") or ""),
        str(locator.get("method") or ""),
        str(locator.get("role") or ""),
        str(locator.get("name") or locator.get("value") or locator.get("selector") or locator.get("text") or ""),
        str(payload.get("value") or payload.get("key") or ""),
    ]
    return "|".join(part for part in parts if part)


def observation_from_payload(payload: dict[str, Any]) -> DirectedObservation:
    compact = payload.get("compact_snapshot")
    summary = _compact_snapshot_summary(compact if isinstance(compact, dict) else {})
    return DirectedObservation(
        url=str(payload.get("url") or ""),
        title=str(payload.get("title") or ""),
        dom_digest=str(payload.get("dom_digest") or ""),
        compact_snapshot_summary=summary,
    )


def decision_snapshot(decision: Any) -> DirectedDecisionSnapshot:
    action = getattr(decision, "next_action", None)
    action_payload = action.model_dump(mode="json") if action is not None else None
    return DirectedDecisionSnapshot(
        goal_status=str(getattr(decision, "goal_status", "") or "continue"),
        summary=str(getattr(decision, "summary", "") or ""),
        expected_change=str(getattr(decision, "expected_change", "") or ""),
        done_reason=str(getattr(decision, "done_reason", "") or ""),
        action=action_payload,
        risk=str((action_payload or {}).get("risk") or "safe"),
    )


def execution_snapshot(
    *,
    result: str,
    error: str = "",
    before: DirectedObservation | None = None,
    after: DirectedObservation | None = None,
    started_at: datetime | None = None,
) -> DirectedExecutionSnapshot:
    duration_ms = None
    if started_at is not None:
        duration_ms = max(0, int((datetime.now() - started_at).total_seconds() * 1000))
    return DirectedExecutionSnapshot(
        result=result,
        error=error,
        duration_ms=duration_ms,
        url_changed=bool(before and after and before.url != after.url),
        dom_changed=bool(before and after and before.dom_digest != after.dom_digest),
    )


def build_directed_retry_context(
    traces: list[DirectedAnalysisTrace],
    *,
    captured_api_summary: list[dict[str, Any]],
) -> dict[str, Any]:
    recent = traces[-10:]
    recent_summary = [_trace_summary(trace) for trace in recent]
    failed = [trace for trace in traces if _trace_result(trace) in {"failed", "planner_failed", "retry_guard_skipped"}]
    blocked = _blocked_actions(traces)
    loop_detected = _loop_detected(failed)
    if loop_detected:
        for fingerprint in _recent_failed_fingerprints(failed)[-LOOP_WINDOW:]:
            if fingerprint and not any(item["fingerprint"] == fingerprint for item in blocked):
                blocked.append({"fingerprint": fingerprint, "reason": "最近失败呈现 A/B/A/B 循环"})
    return {
        "recent_traces": recent_summary,
        "blocked_actions": blocked,
        "block_steps": blocked,
        "loop_detected": loop_detected,
        "successful_transitions": [
            item for item in recent_summary if item["result"] == "executed" and (item["url_changed"] or item["dom_changed"])
        ],
        "captured_api_summary": captured_api_summary,
    }


def retry_guard_skip_reason(action_fingerprint: str, traces: list[DirectedAnalysisTrace]) -> str:
    if not action_fingerprint:
        return ""
    failures = [
        trace for trace in traces
        if trace.action_fingerprint == action_fingerprint and _trace_result(trace) in {"failed", "retry_guard_skipped"}
    ]
    if len(failures) >= TOTAL_FAILURE_SKIP_LIMIT:
        return f"动作指纹 {action_fingerprint} 已失败 {len(failures)} 次"
    return ""


def captured_call_ids(calls: Iterable[CapturedApiCall]) -> list[str]:
    return [str(call.id) for call in calls]


def _compact_snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    nodes = snapshot.get("actionable_nodes")
    return {
        "url": snapshot.get("url") or "",
        "title": snapshot.get("title") or "",
        "actionable_count": len(nodes) if isinstance(nodes, list) else 0,
    }


def _trace_result(trace: DirectedAnalysisTrace) -> str:
    return trace.execution.result if trace.execution else ""


def _trace_summary(trace: DirectedAnalysisTrace) -> dict[str, Any]:
    return {
        "step": trace.step,
        "result": _trace_result(trace),
        "fingerprint": trace.action_fingerprint or "",
        "error": trace.execution.error if trace.execution else "",
        "url": trace.after.url if trace.after else trace.before.url,
        "title": trace.after.title if trace.after else trace.before.title,
        "url_changed": bool(trace.execution and trace.execution.url_changed),
        "dom_changed": bool(trace.execution and trace.execution.dom_changed),
        "new_calls": list(trace.captured_call_ids),
    }


def _recent_failed_fingerprints(failed: list[DirectedAnalysisTrace]) -> list[str]:
    return [trace.action_fingerprint or "" for trace in failed if trace.action_fingerprint]


def _loop_detected(failed: list[DirectedAnalysisTrace]) -> bool:
    recent = _recent_failed_fingerprints(failed)[-LOOP_WINDOW:]
    return len(recent) == LOOP_WINDOW and recent[0] == recent[2] and recent[1] == recent[3] and recent[0] != recent[1]


def _blocked_actions(traces: list[DirectedAnalysisTrace]) -> list[dict[str, str]]:
    blocked: list[dict[str, str]] = []
    consecutive: list[DirectedAnalysisTrace] = []
    for trace in reversed(traces):
        result = _trace_result(trace)
        if result == "executed":
            break
        if result in {"failed", "retry_guard_skipped"} and trace.action_fingerprint:
            consecutive.append(trace)
            continue
        break
    if len(consecutive) >= CONSECUTIVE_FAILURE_LIMIT:
        fingerprint = consecutive[0].action_fingerprint or ""
        if all(trace.action_fingerprint == fingerprint for trace in consecutive[:CONSECUTIVE_FAILURE_LIMIT]):
            blocked.append({"fingerprint": fingerprint, "reason": f"连续 {CONSECUTIVE_FAILURE_LIMIT} 次失败"})
    counts = Counter(
        trace.action_fingerprint for trace in traces
        if trace.action_fingerprint and _trace_result(trace) in {"failed", "retry_guard_skipped"}
    )
    for fingerprint, count in counts.items():
        if count >= TOTAL_FAILURE_SKIP_LIMIT and not any(item["fingerprint"] == fingerprint for item in blocked):
            blocked.append({"fingerprint": fingerprint, "reason": f"累计失败 {count} 次"})
    return blocked
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_action_fingerprint_uses_action_locator_and_value RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_build_directed_retry_context_blocks_repeated_failures -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/directed_trace.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: add directed retry context helpers"
```

---

### Task 3: 覆盖成功步骤清除 blocked_actions/block_steps

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/directed_trace.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败测试**

添加测试：

```python
def test_directed_retry_context_clears_blocked_actions_after_success():
    traces = [
        DirectedAnalysisTrace(
            step=1,
            instruction="搜索订单",
            mode="safe_directed",
            before=DirectedObservation(dom_digest="orders"),
            action_fingerprint="click|role|button|搜索",
            execution=DirectedExecutionSnapshot(result="failed", error="Locator not found"),
        ),
        DirectedAnalysisTrace(
            step=2,
            instruction="搜索订单",
            mode="safe_directed",
            before=DirectedObservation(dom_digest="orders"),
            action_fingerprint="click|role|button|搜索",
            execution=DirectedExecutionSnapshot(result="failed", error="Locator not found"),
        ),
        DirectedAnalysisTrace(
            step=3,
            instruction="搜索订单",
            mode="safe_directed",
            before=DirectedObservation(url="https://example.test/orders", dom_digest="orders"),
            after=DirectedObservation(url="https://example.test/orders", dom_digest="orders-results"),
            action_fingerprint="click|role|button|搜索",
            execution=DirectedExecutionSnapshot(result="executed", dom_changed=True),
        ),
    ]

    context = build_directed_retry_context(traces, captured_api_summary=[])

    assert context["blocked_actions"] == []
    assert context["block_steps"] == []
    assert context["loop_detected"] is False
    assert context["successful_transitions"][0]["dom_changed"] is True
```

- [ ] **Step 2: 运行测试确认当前行为**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_retry_context_clears_blocked_actions_after_success -q
```

Expected: PASS。如果失败，修正 `_blocked_actions(...)`，确保遇到最近的 `executed` trace 后停止连续失败统计，并在成功后不返回旧 `blocked_actions`。

- [ ] **Step 3: 添加 A/B/A/B 被成功步骤打断的测试**

添加测试：

```python
def test_directed_retry_context_success_breaks_loop_window():
    traces = [
        DirectedAnalysisTrace(step=1, instruction="搜索订单", mode="safe_directed", before=DirectedObservation(), action_fingerprint="A", execution=DirectedExecutionSnapshot(result="failed")),
        DirectedAnalysisTrace(step=2, instruction="搜索订单", mode="safe_directed", before=DirectedObservation(), action_fingerprint="B", execution=DirectedExecutionSnapshot(result="failed")),
        DirectedAnalysisTrace(step=3, instruction="搜索订单", mode="safe_directed", before=DirectedObservation(), action_fingerprint="A", execution=DirectedExecutionSnapshot(result="failed")),
        DirectedAnalysisTrace(step=4, instruction="搜索订单", mode="safe_directed", before=DirectedObservation(), action_fingerprint="A", execution=DirectedExecutionSnapshot(result="executed")),
        DirectedAnalysisTrace(step=5, instruction="搜索订单", mode="safe_directed", before=DirectedObservation(), action_fingerprint="B", execution=DirectedExecutionSnapshot(result="failed")),
    ]

    context = build_directed_retry_context(traces, captured_api_summary=[])

    assert context["loop_detected"] is False
    assert context["blocked_actions"] == []
```

- [ ] **Step 4: 运行纯函数测试**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_retry_context_clears_blocked_actions_after_success RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_retry_context_success_breaks_loop_window -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/directed_trace.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: clear directed retry blocks after success"
```

---

### Task 4: Planner 接收结构化 retry context

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败测试**

更新或新增 planner prompt 测试：

```python
def test_directed_step_prompt_includes_retry_context(monkeypatch):
    from langchain_core.messages import AIMessage
    from backend.rpa.api_monitor import directed_analyzer

    captured_messages = []

    class _FakeModel:
        async def ainvoke(self, messages):
            captured_messages.extend(messages)
            return AIMessage(
                content='{"goal_status":"blocked","summary":"搜索按钮已重复失败","next_action":null,"done_reason":"blocked action"}'
            )

    monkeypatch.setattr(directed_analyzer, "get_llm_model", lambda config=None, streaming=False: _FakeModel())

    decision = asyncio.run(
        directed_analyzer.build_directed_step_decision(
            instruction="搜索订单",
            compact_snapshot={"url": "https://example.test/orders", "actionable_nodes": [{"name": "搜索"}]},
            run_history=[],
            observation={"url": "https://example.test/orders", "title": "Orders"},
            retry_context={
                "blocked_actions": [{"fingerprint": "click|role|button|搜索", "reason": "连续 2 次失败"}],
                "block_steps": [{"fingerprint": "click|role|button|搜索", "reason": "连续 2 次失败"}],
                "loop_detected": False,
                "recent_traces": [],
                "captured_api_summary": [],
            },
            model_config={"model_name": "fake"},
        )
    )

    prompt = "\n".join(str(message.content) for message in captured_messages if hasattr(message, "content"))
    assert decision.goal_status == "blocked"
    assert "重试上下文 retry_context" in prompt
    assert "blocked_actions" in prompt
    assert "click|role|button|搜索" in prompt
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_step_prompt_includes_retry_context -q
```

Expected: FAIL，错误包含 `got an unexpected keyword argument 'retry_context'`。

- [ ] **Step 3: 更新 prompt 和函数签名**

在 `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py` 中更新 `DIRECTED_STEP_SYSTEM`，加入：

```python
- 如果 retry_context.blocked_actions 或 retry_context.block_steps 中列出动作，除非当前 DOM 明显变化，否则不要继续选择这些动作。
- 如果 retry_context.loop_detected 为 true，必须选择真正不同的路线、等待状态变化，或返回 blocked。
- 如果 retry_context.recent_traces 显示某一步已经捕获到匹配用户目标的 API，返回 done。
- 任意成功步骤会清除旧的 block_steps；不要把旧页面状态下的失败当成长期黑名单。
```

更新 `DIRECTED_STEP_USER`，加入：

```python
重试上下文 retry_context：
{retry_context}

```

更新函数签名：

```python
async def build_directed_step_decision(
    *,
    instruction: str,
    compact_snapshot: Dict[str, Any],
    run_history: List[Dict[str, Any]],
    observation: Dict[str, Any],
    retry_context: Optional[Dict[str, Any]] = None,
    model_config: Optional[Dict] = None,
) -> DirectedStepDecision:
```

更新 format 调用：

```python
retry_context=json.dumps(retry_context or {}, ensure_ascii=False, indent=2),
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_step_prompt_includes_retry_context -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/directed_analyzer.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: pass retry context to directed planner"
```

---

### Task 5: 在定向分析循环中写入 directed traces

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败测试**

添加测试：

```python
def test_directed_analysis_records_trace_for_action_failure(monkeypatch):
    manager = ApiMonitorSessionManager()
    session = _route_session()
    manager.sessions[session.id] = session
    manager._pages[session.id] = _FakeDirectedPage()
    manager._captures[session.id] = _SequencedCapture([[], []])

    async def fake_observe(page, instruction):
        return {
            "url": page.url,
            "title": "Orders",
            "raw_snapshot": {},
            "compact_snapshot": {"url": page.url, "actionable_nodes": [{"name": "搜索"}]},
            "dom_digest": "same-page",
        }

    async def fake_decision(*, instruction, compact_snapshot, run_history, observation, retry_context=None, model_config=None):
        if not session.directed_traces:
            return DirectedStepDecision(
                goal_status="continue",
                summary="点击搜索",
                expected_change="捕获搜索接口",
                next_action=DirectedAction(
                    action="click",
                    locator={"method": "role", "role": "button", "name": "搜索"},
                    description="点击搜索",
                    risk="safe",
                ),
            )
        return DirectedStepDecision(goal_status="blocked", summary="无法继续", done_reason="搜索按钮不存在")

    async def fake_execute_action(page, action):
        raise RuntimeError("Locator not found: 搜索")

    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        return []

    monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_decision)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.execute_directed_action", fake_execute_action)
    monkeypatch.setattr(manager, "_generate_tools_from_calls", fake_generate_tools)

    events = asyncio.run(
        _collect_events(
            manager.analyze_directed_page(
                session.id,
                instruction="搜索订单",
                mode="safe_directed",
                business_safety="guarded",
            )
        )
    )

    assert session.directed_traces[0].step == 1
    assert session.directed_traces[0].before.dom_digest == "same-page"
    assert session.directed_traces[0].decision.summary == "点击搜索"
    assert session.directed_traces[0].action_fingerprint == "click|role|button|搜索"
    assert session.directed_traces[0].execution.result == "failed"
    assert "Locator not found" in session.directed_traces[0].execution.error
    assert any(event["event"] == "directed_trace_added" for event in events)
    assert any(event["event"] == "directed_trace_updated" for event in events)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_analysis_records_trace_for_action_failure -q
```

Expected: FAIL，错误包含 `AttributeError` 或 trace list 为空。

- [ ] **Step 3: 接入 trace lifecycle**

在 `RpaClaw/backend/rpa/api_monitor/manager.py` import 区域加入：

```python
from .directed_trace import (
    build_directed_retry_context,
    captured_call_ids,
    decision_snapshot,
    directed_action_fingerprint,
    execution_snapshot,
    observation_from_payload,
    retry_guard_skip_reason,
)
from .models import DirectedAnalysisTrace
```

在每轮 `observation = await self._observe_directed_page(...)` 后创建 trace：

```python
before_observation = observation_from_payload(observation)
trace = DirectedAnalysisTrace(
    step=step_index,
    instruction=instruction,
    mode=mode,
    before=before_observation,
)
session.directed_traces.append(trace)
yield {
    "event": "directed_trace_added",
    "data": json.dumps(trace.model_dump(mode="json"), ensure_ascii=False),
}
```

在 planner 成功后写入 decision：

```python
trace.decision = decision_snapshot(decision)
if decision.next_action is not None:
    trace.action_fingerprint = directed_action_fingerprint(decision.next_action)
trace.updated_at = datetime.now()
```

在 action 失败 except 中写入 execution：

```python
after_payload = await self._observe_directed_page(page, instruction)
trace.after = observation_from_payload(after_payload)
trace.execution = execution_snapshot(
    result="failed",
    error=error_text,
    before=trace.before,
    after=trace.after,
)
trace.captured_call_ids = captured_call_ids(failed_step_calls)
trace.updated_at = datetime.now()
yield {
    "event": "directed_trace_updated",
    "data": json.dumps(trace.model_dump(mode="json"), ensure_ascii=False),
}
```

在成功执行和 skipped/planner_failed 分支也按同样模式写入 `execution.result`。

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_analysis_records_trace_for_action_failure -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: record directed analysis traces"
```

---

### Task 6: 从 traces 派生 retry context 并应用 retry guard

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败测试**

添加测试：

```python
def test_directed_analysis_passes_retry_context_and_skips_repeated_action(monkeypatch):
    manager = ApiMonitorSessionManager()
    session = _route_session()
    manager.sessions[session.id] = session
    manager._pages[session.id] = _FakeDirectedPage()
    manager._captures[session.id] = _SequencedCapture([[], [], [], []])

    contexts = []

    async def fake_observe(page, instruction):
        return {
            "url": page.url,
            "title": "Orders",
            "raw_snapshot": {},
            "compact_snapshot": {"url": page.url, "actionable_nodes": [{"name": "搜索"}]},
            "dom_digest": "orders",
        }

    async def fake_decision(*, instruction, compact_snapshot, run_history, observation, retry_context=None, model_config=None):
        contexts.append(retry_context or {})
        if len(contexts) <= 4:
            return DirectedStepDecision(
                goal_status="continue",
                summary="点击搜索",
                next_action=DirectedAction(
                    action="click",
                    locator={"method": "role", "role": "button", "name": "搜索"},
                    description="点击搜索",
                    risk="safe",
                ),
            )
        return DirectedStepDecision(goal_status="blocked", summary="停止", done_reason="重复失败")

    async def fake_execute_action(page, action):
        raise RuntimeError("Locator not found: 搜索")

    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        return []

    monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_decision)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.execute_directed_action", fake_execute_action)
    monkeypatch.setattr(manager, "_generate_tools_from_calls", fake_generate_tools)

    events = asyncio.run(
        _collect_events(
            manager.analyze_directed_page(
                session.id,
                instruction="搜索订单",
                mode="safe_directed",
                business_safety="guarded",
            )
        )
    )

    assert any(ctx.get("blocked_actions") for ctx in contexts[2:])
    assert any(trace.execution and trace.execution.result == "retry_guard_skipped" for trace in session.directed_traces)
    assert any(event["event"] == "directed_replan" and "重复失败" in event["data"] for event in events)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_analysis_passes_retry_context_and_skips_repeated_action -q
```

Expected: FAIL，因为 manager 还没有向 planner 传 `retry_context`，也没有 pre-execution skip。

- [ ] **Step 3: 在 manager 中构造 retry context**

在每轮调用 planner 前添加：

```python
retry_context = build_directed_retry_context(
    session.directed_traces,
    captured_api_summary=self._summarize_directed_calls(directed_calls),
)
observation_for_prompt["retry_context"] = retry_context
decision = await build_directed_step_decision(
    instruction=instruction,
    compact_snapshot=observation["compact_snapshot"],
    run_history=run_history,
    observation=observation_for_prompt,
    retry_context=retry_context,
    model_config=model_config,
)
```

在执行动作前添加 retry guard：

```python
skip_reason = retry_guard_skip_reason(trace.action_fingerprint or "", session.directed_traces[:-1])
if skip_reason:
    trace.execution = execution_snapshot(result="retry_guard_skipped", error=skip_reason, before=trace.before, after=trace.before)
    trace.retry_advice = {"reason": skip_reason, "blocked_actions": retry_context.get("blocked_actions", [])}
    trace.updated_at = datetime.now()
    run_history.append(
        {
            "step": step_index,
            "result": "retry_guard_skipped",
            "description": allowed_action.description,
            "code": describe_locator_code(allowed_action),
            "error": skip_reason,
            "expected_change": decision.expected_change,
        }
    )
    failed_steps += 1
    yield {
        "event": "directed_trace_updated",
        "data": json.dumps(trace.model_dump(mode="json"), ensure_ascii=False),
    }
    yield {
        "event": "directed_replan",
        "data": json.dumps(
            {"step": step_index, "description": allowed_action.description, "error": skip_reason},
            ensure_ascii=False,
        ),
    }
    continue
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_analysis_passes_retry_context_and_skips_repeated_action -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: apply directed retry guard"
```

---

### Task 7: 保持 captured calls 仍是工具生成唯一输入

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写回归测试**

添加测试：

```python
def test_directed_trace_does_not_feed_tool_generation(monkeypatch):
    manager = ApiMonitorSessionManager()
    session = _route_session()
    manager.sessions[session.id] = session
    manager._pages[session.id] = _FakeDirectedPage()
    call = _captured_call()
    manager._captures[session.id] = _SequencedCapture([[], [call]])

    tool_calls = []

    async def fake_observe(page, instruction):
        return {
            "url": page.url,
            "title": "Orders",
            "raw_snapshot": {},
            "compact_snapshot": {"url": page.url, "actionable_nodes": [{"name": "搜索"}]},
            "dom_digest": "orders",
        }

    async def fake_decision(*, instruction, compact_snapshot, run_history, observation, retry_context=None, model_config=None):
        if not tool_calls and len(session.directed_traces) <= 1:
            return DirectedStepDecision(
                goal_status="continue",
                summary="点击搜索",
                next_action=DirectedAction(
                    action="click",
                    locator={"method": "role", "role": "button", "name": "搜索"},
                    description="点击搜索",
                    risk="safe",
                ),
            )
        return DirectedStepDecision(goal_status="done", summary="完成", done_reason="已捕获 API")

    async def fake_execute_action(page, action):
        return None

    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        tool_calls.extend(calls_arg)
        return []

    monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_decision)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.execute_directed_action", fake_execute_action)
    monkeypatch.setattr(manager, "_wait_for_directed_settle", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(manager, "_generate_tools_from_calls", fake_generate_tools)

    asyncio.run(
        _collect_events(
            manager.analyze_directed_page(
                session.id,
                instruction="搜索订单",
                mode="safe_directed",
                business_safety="guarded",
            )
        )
    )

    assert tool_calls == [call]
    assert session.directed_traces[0].captured_call_ids == [call.id]
```

- [ ] **Step 2: 运行测试**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_trace_does_not_feed_tool_generation -q
```

Expected: PASS。如果失败，确认 `_generate_tools_from_calls(...)` 只接收 `directed_calls`，不要传入 trace 对象。

- [ ] **Step 3: 运行定向分析相关测试**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py -q
```

Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "test: preserve api call based tool generation"
```

---

### Task 8: 前端日志兼容新增 trace 事件

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`

- [ ] **Step 1: 检查是否已有默认日志处理**

Run:

```bash
rg -n "directed_trace_added|directed_trace_updated|switch \\(event|case 'directed" RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue
```

Expected: 看到 `handleAnalysisEvent` 或相近 switch；如果已有 default 分支可读地展示未知事件，本任务只需要确认无需改动。

- [ ] **Step 2: 添加事件文案映射**

在 `ApiMonitorPage.vue` 的 SSE event switch 中添加：

```ts
      case 'directed_trace_added':
        addLog('ANALYZE', `第 ${data.step || '-'} 轮 trace 已创建`);
        break;
      case 'directed_trace_updated':
        addLog('ANALYZE', `第 ${data.step || '-'} 轮 trace 已更新: ${data.execution?.result || data.decision?.goal_status || ''}`);
        break;
```

- [ ] **Step 3: 运行前端静态检查或构建**

Run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: PASS。如果本地依赖缺失，先运行 `npm install`，然后重试 `npm run build`。

- [ ] **Step 4: 提交**

```bash
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue
git commit -m "feat: show directed trace analysis events"
```

---

### Task 9: 最终回归验证

**Files:**
- Verify only

- [ ] **Step 1: 运行后端聚焦测试**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py -q
```

Expected: PASS。

- [ ] **Step 2: 运行 API Monitor MCP 相关测试**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_mcp_contract.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py -q
```

Expected: PASS。

- [ ] **Step 3: 检查工作区和提交**

Run:

```bash
git status --short
```

Expected: clean working tree。

- [ ] **Step 4: 如果 Task 8 改了前端，运行前端构建**

Run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: PASS。

---

## 自检清单

- Spec 覆盖：模型、动作指纹、retry context、retry guard、成功后清除 block_steps、SSE、工具生成边界、测试计划均有对应任务。
- 边界保持：计划没有把 directed traces 送进 RPA Skill compiler，也没有替换 `_generate_tools_from_calls(...)`。
- 测试优先：每个行为任务先写失败测试，再实现最小代码。
- 兼容策略：`run_history` 作为兼容摘要保留，新增 retry context 不破坏现有 planner 调用方。
