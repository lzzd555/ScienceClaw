# API Monitor Directed DOM Replan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 API Monitor 定向分析从一次性多步计划改造成可感知 DOM 变化的动态单步重规划循环。

**Architecture:** 后端新增单步决策模型与单步 planner，每轮基于最新 DOM snapshot、运行历史、错误事实和捕获 API 生成一个下一步动作。`ApiMonitorSessionManager.analyze_directed_page` 改为有上限的 `Sense -> Plan -> Act -> Observe -> Replan` 循环，并保留现有事件兼容层。

**Tech Stack:** Python 3.13, FastAPI SSE, Pydantic v2, Playwright async API, pytest, LangChain model invocation.

---

## 文件结构

- Modify: `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py`
  - 新增 `DirectedGoalStatus`、`DirectedStepDecision`、`SingleFilteredDirectedAction`
  - 新增单步 prompt 和 `build_directed_step_decision`
  - 新增 `filter_action_for_business_safety`
  - 保留 `build_directed_plan` 和 `execute_directed_plan` 兼容旧测试

- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
  - 将 `analyze_directed_page` 改成动态循环
  - 新增 `_observe_directed_page`、`_build_directed_dom_digest`、`_wait_for_directed_settle`
  - 每步后 drain 新 API 调用，并把事实写入运行历史

- Modify: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`
  - 添加单步决策模型测试
  - 添加多轮 DOM 重规划测试
  - 添加每轮安全过滤、失败上下文、最大步数、逐步 drain API 的测试
  - 调整旧 directed 测试以适配动态循环，同时保留路由兼容测试

- Optional Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
  - 第一版不需要改前端；若现有日志过滤不显示新增事件，再只补事件文案映射。

---

### Task 1: 添加单步决策模型与解析测试

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败测试**

在 `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py` 的 directed analyzer import 区域加入新类型：

```python
from backend.rpa.api_monitor.directed_analyzer import (
    DirectedAction,
    DirectedAnalysisPlan,
    DirectedExecutionResult,
    DirectedStepDecision,
    build_locator,
    filter_action_for_business_safety,
    filter_actions_for_business_safety,
)
```

在 `test_user_controlled_directed_keeps_unsafe_actions` 后添加：

```python
def test_directed_step_decision_accepts_continue_done_and_blocked():
    continue_decision = DirectedStepDecision.model_validate(
        {
            "goal_status": "continue",
            "summary": "需要搜索订单",
            "expected_change": "搜索结果列表出现",
            "next_action": {
                "action": "fill",
                "locator": {"method": "placeholder", "value": "订单号"},
                "value": "123",
                "description": "填写订单号",
                "risk": "safe",
            },
        }
    )

    assert continue_decision.goal_status == "continue"
    assert continue_decision.next_action is not None
    assert continue_decision.next_action.value == "123"

    done_decision = DirectedStepDecision.model_validate(
        {
            "goal_status": "done",
            "summary": "已经捕获搜索接口",
            "done_reason": "本轮捕获到 /api/orders",
        }
    )

    assert done_decision.goal_status == "done"
    assert done_decision.next_action is None
    assert done_decision.done_reason == "本轮捕获到 /api/orders"

    blocked_decision = DirectedStepDecision.model_validate(
        {
            "goal_status": "blocked",
            "summary": "没有可继续操作的安全元素",
            "done_reason": "页面只剩删除按钮",
        }
    )

    assert blocked_decision.goal_status == "blocked"
    assert blocked_decision.next_action is None
```

添加单动作安全过滤测试：

```python
def test_filter_action_for_business_safety_handles_single_step_actions():
    safe_action = DirectedAction(
        action="click",
        locator={"method": "role", "role": "button", "name": "搜索"},
        description="点击搜索",
        risk="safe",
    )
    unsafe_action = DirectedAction(
        action="click",
        locator={"method": "role", "role": "button", "name": "删除"},
        description="删除订单",
        risk="unsafe",
        reason="删除是破坏性操作",
    )

    assert filter_action_for_business_safety(safe_action, "guarded").allowed == safe_action
    assert filter_action_for_business_safety(safe_action, "guarded").skipped is None
    assert filter_action_for_business_safety(unsafe_action, "guarded").allowed is None
    assert filter_action_for_business_safety(unsafe_action, "guarded").skipped == unsafe_action
    assert filter_action_for_business_safety(unsafe_action, "user_controlled").allowed == unsafe_action
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_step_decision_accepts_continue_done_and_blocked RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_filter_action_for_business_safety_handles_single_step_actions -q
```

Expected: FAIL，错误包含 `ImportError` 或 `cannot import name 'DirectedStepDecision'`。

- [ ] **Step 3: 添加最小实现**

在 `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py` 的类型定义区域改为：

```python
DirectedActionType = Literal["click", "fill", "press", "select", "wait"]
DirectedActionRisk = Literal["safe", "unsafe"]
DirectedGoalStatus = Literal["continue", "done", "blocked"]
```

在 `DirectedExecutionResult` 后添加：

```python
class DirectedStepDecision(BaseModel):
    goal_status: DirectedGoalStatus = "continue"
    summary: str = ""
    next_action: Optional[DirectedAction] = None
    expected_change: str = ""
    done_reason: str = ""


class SingleFilteredDirectedAction(BaseModel):
    allowed: Optional[DirectedAction] = None
    skipped: Optional[DirectedAction] = None
```

在 `filter_actions_for_business_safety` 后添加：

```python
def filter_action_for_business_safety(
    action: DirectedAction,
    business_safety: AnalysisBusinessSafety,
) -> SingleFilteredDirectedAction:
    if business_safety != "guarded" or action.risk == "safe":
        return SingleFilteredDirectedAction(allowed=action, skipped=None)
    return SingleFilteredDirectedAction(allowed=None, skipped=action)
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_step_decision_accepts_continue_done_and_blocked RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_filter_action_for_business_safety_handles_single_step_actions -q
```

Expected: PASS，2 个测试通过。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/directed_analyzer.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: add directed step decision model"
```

---

### Task 2: 添加单步 planner prompt 与 LLM 调用

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败测试**

在 `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py` 的 import 区域加入：

```python
from langchain_core.messages import HumanMessage
```

在单步决策测试后添加：

```python
def test_directed_step_prompt_includes_history_and_current_snapshot(monkeypatch):
    from langchain_core.messages import AIMessage
    from backend.rpa.api_monitor import directed_analyzer

    captured_messages = []

    class _FakeModel:
        async def ainvoke(self, messages):
            captured_messages.extend(messages)
            return AIMessage(
                content='{"goal_status":"continue","summary":"点击搜索","next_action":{"action":"click","locator":{"method":"role","role":"button","name":"搜索"},"description":"点击搜索","risk":"safe"},"expected_change":"出现结果"}'
            )

    monkeypatch.setattr(directed_analyzer, "get_llm_model", lambda config=None, streaming=False: _FakeModel())

    decision = asyncio.run(
        directed_analyzer.build_directed_step_decision(
            instruction="搜索订单 123",
            compact_snapshot={"url": "https://example.test/orders", "actionable_nodes": [{"text": "搜索"}]},
            run_history=[{"step": 1, "action": "fill", "result": "success"}],
            observation={"url": "https://example.test/orders", "title": "Orders", "new_call_count": 0},
            model_config={"model_name": "fake"},
        )
    )

    assert decision.goal_status == "continue"
    assert decision.next_action.description == "点击搜索"
    human_messages = [message for message in captured_messages if isinstance(message, HumanMessage)]
    assert len(human_messages) == 1
    prompt = human_messages[0].content
    assert "搜索订单 123" in prompt
    assert "actionable_nodes" in prompt
    assert "run_history" in prompt
    assert "new_call_count" in prompt
    assert "每次只返回一个下一步动作" in prompt
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_step_prompt_includes_history_and_current_snapshot -q
```

Expected: FAIL，错误包含 `AttributeError: module ... has no attribute 'build_directed_step_decision'`。

- [ ] **Step 3: 添加 prompt 与函数**

在 `DIRECTED_PLAN_USER` 后添加：

```python
DIRECTED_STEP_SYSTEM = """\
你是 API Monitor 的动态定向分析控制器。你会收到用户目标、当前页面精简 DOM、运行历史和最新观察事实。
只返回 JSON，不要返回 markdown。

返回结构：
{
  "goal_status": "continue|done|blocked",
  "summary": "本轮判断摘要",
  "next_action": {
    "action": "click|fill|press|select|wait",
    "locator": {"method": "role|text|placeholder|label|css", "role": "button", "name": "搜索", "value": ""},
    "value": "fill/select 使用的值",
    "key": "press 使用的按键",
    "timeout_ms": 500,
    "description": "这个动作做什么",
    "risk": "safe|unsafe",
    "reason": "为什么安全或不安全"
  },
  "expected_change": "执行动作后预期页面或网络发生什么变化",
  "done_reason": "done 或 blocked 时说明原因"
}

规划规则：
- 每次只返回一个下一步动作，不能返回多步计划。
- 当前页面精简 DOM 是事实源，历史动作只说明已经发生过什么。
- 如果 DOM 或 URL 已变化，必须基于新页面推理下一步。
- 目标 API 已捕获或用户目标已满足时返回 done，且 next_action 为空。
- 没有安全或有意义的浏览器动作时返回 blocked，且 next_action 为空。

安全判定：
- 搜索、筛选、分页、打开详情、切换 tab、展开区域通常是 safe。
- 删除、注销、支付、提交订单、撤销授权、禁用、不可逆提交通常是 unsafe。

平台约束：
- 只能规划页面内 Playwright 操作。
- 不要规划 shell、文件、权限、下载目录或本地系统操作。
- 不要返回 Python 代码。
"""


DIRECTED_STEP_USER = """\
用户目标：
{instruction}

当前页面精简 DOM：
{compact_snapshot}

运行历史 run_history：
{run_history}

最新观察 observation：
{observation}

基于当前页面状态决策。每次只返回一个下一步动作，或者返回 done/blocked。
"""
```

在 `build_directed_plan` 后添加：

```python
async def build_directed_step_decision(
    *,
    instruction: str,
    compact_snapshot: Dict[str, Any],
    run_history: List[Dict[str, Any]],
    observation: Dict[str, Any],
    model_config: Optional[Dict] = None,
) -> DirectedStepDecision:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    model = get_llm_model(config=model_config, streaming=False)
    messages = [
        SystemMessage(content=DIRECTED_STEP_SYSTEM),
        HumanMessage(
            content=DIRECTED_STEP_USER.format(
                instruction=instruction,
                compact_snapshot=json.dumps(compact_snapshot, ensure_ascii=False, indent=2),
                run_history=json.dumps(run_history, ensure_ascii=False, indent=2),
                observation=json.dumps(observation, ensure_ascii=False, indent=2),
            )
        ),
    ]
    response = await model.ainvoke(messages)
    if isinstance(response, AIMessage):
        raw = response.content or ""
    elif hasattr(response, "content"):
        raw = str(response.content)
    else:
        raw = str(response)

    parsed = json.loads(strip_json_fence(raw))
    decision = DirectedStepDecision.model_validate(parsed)
    if decision.goal_status == "continue" and decision.next_action is None:
        return DirectedStepDecision(
            goal_status="blocked",
            summary=decision.summary or "Planner returned continue without next_action",
            done_reason="Planner returned continue without next_action",
        )
    if decision.goal_status != "continue":
        decision.next_action = None
    return decision
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_step_prompt_includes_history_and_current_snapshot -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/directed_analyzer.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: add directed single-step planner"
```

---

### Task 3: 添加观察 helper 与 DOM digest

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败测试**

在 `_FakeDirectedPage` 后添加：

```python
def test_build_directed_dom_digest_changes_when_visible_actions_change():
    manager = ApiMonitorSessionManager()
    first = {
        "url": "https://example.test/orders",
        "title": "Orders",
        "actionable_nodes": [
            {"role": "button", "name": "搜索", "text": "搜索"},
            {"role": "textbox", "name": "订单号", "text": ""},
        ],
    }
    second = {
        "url": "https://example.test/orders",
        "title": "Orders",
        "actionable_nodes": [
            {"role": "button", "name": "导出", "text": "导出"},
            {"role": "link", "name": "订单详情", "text": "订单详情"},
        ],
    }

    assert manager._build_directed_dom_digest(first) != manager._build_directed_dom_digest(second)
    assert manager._build_directed_dom_digest(first) == manager._build_directed_dom_digest(dict(first))
```

添加观察 helper 测试：

```python
def test_observe_directed_page_returns_snapshot_compact_digest_and_metadata(monkeypatch):
    manager = ApiMonitorSessionManager()
    page = _FakeDirectedPage()

    async def fake_build_page_snapshot(page_arg, frame_path_builder):
        assert page_arg is page
        return {
            "url": page.url,
            "title": "Orders",
            "actionable_nodes": [{"role": "button", "name": "搜索", "text": "搜索"}],
            "frames": [],
        }

    def fake_compact_recording_snapshot(snapshot, instruction):
        return {"url": snapshot["url"], "title": snapshot["title"], "actionable_nodes": snapshot["actionable_nodes"]}

    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_page_snapshot", fake_build_page_snapshot)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.compact_recording_snapshot", fake_compact_recording_snapshot)

    observation = asyncio.run(manager._observe_directed_page(page, "搜索订单"))

    assert observation["url"] == "https://example.test/orders"
    assert observation["title"] == "Orders"
    assert observation["compact_snapshot"]["actionable_nodes"][0]["name"] == "搜索"
    assert observation["dom_digest"]
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_build_directed_dom_digest_changes_when_visible_actions_change RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_observe_directed_page_returns_snapshot_compact_digest_and_metadata -q
```

Expected: FAIL，错误包含 `_build_directed_dom_digest` 或 `_observe_directed_page` 不存在。

- [ ] **Step 3: 添加 helper**

在 `RpaClaw/backend/rpa/api_monitor/manager.py` 顶部 imports 增加：

```python
import hashlib
```

把 directed analyzer import 改成：

```python
from .directed_analyzer import (
    build_directed_plan,
    build_directed_step_decision,
    execute_directed_action,
    execute_directed_plan,
    filter_action_for_business_safety,
    describe_action,
    describe_locator_code,
)
```

在 `navigate` 方法后添加：

```python
    async def _observe_directed_page(self, page: Page, instruction: str) -> Dict:
        raw_snapshot = await build_page_snapshot(page, build_frame_path)
        compact_snapshot = compact_recording_snapshot(raw_snapshot, instruction)
        title = ""
        try:
            title = await page.title()
        except Exception:
            title = str(raw_snapshot.get("title") or "")
        url = getattr(page, "url", "") or str(raw_snapshot.get("url") or "")
        return {
            "url": url,
            "title": title,
            "raw_snapshot": raw_snapshot,
            "compact_snapshot": compact_snapshot,
            "dom_digest": self._build_directed_dom_digest(compact_snapshot),
        }

    def _build_directed_dom_digest(self, compact_snapshot: Dict) -> str:
        action_nodes = compact_snapshot.get("actionable_nodes") or compact_snapshot.get("actions") or []
        digest_payload = {
            "url": compact_snapshot.get("url") or "",
            "title": compact_snapshot.get("title") or "",
            "actionable": [
                {
                    "role": node.get("role") or "",
                    "name": node.get("name") or node.get("label") or "",
                    "text": node.get("text") or "",
                    "ref": node.get("ref") or node.get("internal_ref") or "",
                }
                for node in action_nodes[:80]
                if isinstance(node, dict)
            ],
        }
        encoded = json.dumps(digest_payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_build_directed_dom_digest_changes_when_visible_actions_change RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_observe_directed_page_returns_snapshot_compact_digest_and_metadata -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: observe directed analysis page state"
```

---

### Task 4: 将定向分析改为动态单步循环

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败测试**

新增支持多阶段 capture 的 fake：

```python
class _SequencedCapture:
    def __init__(self, batches):
        self._batches = [list(batch) for batch in batches]
        self._drain_count = 0

    def drain_new_calls(self):
        self._drain_count += 1
        if not self._batches:
            return []
        return self._batches.pop(0)
```

新增动态重规划测试：

```python
def test_directed_analysis_replans_after_dom_changes(monkeypatch):
    manager = ApiMonitorSessionManager()
    session = _route_session()
    manager.sessions[session.id] = session
    manager._pages[session.id] = _FakeDirectedPage()
    manager._captures[session.id] = _SequencedCapture([[], [], [_captured_call()]])

    observations = [
        {
            "url": "https://example.test/orders",
            "title": "Orders",
            "raw_snapshot": {"url": "https://example.test/orders"},
            "compact_snapshot": {"url": "https://example.test/orders", "actionable_nodes": [{"name": "搜索"}]},
            "dom_digest": "search-page",
        },
        {
            "url": "https://example.test/orders?keyword=123",
            "title": "Orders Search",
            "raw_snapshot": {"url": "https://example.test/orders?keyword=123"},
            "compact_snapshot": {"url": "https://example.test/orders?keyword=123", "actionable_nodes": [{"name": "订单详情"}]},
            "dom_digest": "result-page",
        },
    ]
    decision_contexts = []
    executed = []

    async def fake_observe(page, instruction):
        return observations.pop(0) if observations else {
            "url": "https://example.test/orders/123",
            "title": "Order Detail",
            "raw_snapshot": {},
            "compact_snapshot": {"url": "https://example.test/orders/123", "actionable_nodes": []},
            "dom_digest": "detail-page",
        }

    async def fake_decision(*, instruction, compact_snapshot, run_history, observation, model_config=None):
        decision_contexts.append(
            {
                "compact_snapshot": compact_snapshot,
                "run_history": list(run_history),
                "observation": dict(observation),
            }
        )
        if len(decision_contexts) == 1:
            return DirectedStepDecision(
                goal_status="continue",
                summary="先点击搜索",
                next_action=DirectedAction(
                    action="click",
                    locator={"method": "role", "role": "button", "name": "搜索"},
                    description="点击搜索",
                    risk="safe",
                ),
                expected_change="结果出现",
            )
        if len(decision_contexts) == 2:
            return DirectedStepDecision(
                goal_status="continue",
                summary="打开详情",
                next_action=DirectedAction(
                    action="click",
                    locator={"method": "text", "value": "订单详情"},
                    description="打开订单详情",
                    risk="safe",
                ),
                expected_change="详情 API 被捕获",
            )
        return DirectedStepDecision(goal_status="done", summary="完成", done_reason="已捕获详情 API")

    async def fake_execute_action(page, action):
        executed.append(action.description)

    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        assert len(calls_arg) == 1
        return []

    async def fake_wait_for_directed_settle(page, *, previous_digest, instruction, timeout_ms=1500):
        return None

    monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
    monkeypatch.setattr(manager, "_wait_for_directed_settle", fake_wait_for_directed_settle)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_decision)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.execute_directed_action", fake_execute_action)
    monkeypatch.setattr(manager, "_generate_tools_from_calls", fake_generate_tools)

    events = asyncio.run(
        _collect_events(
            manager.analyze_directed_page(
                session.id,
                instruction="搜索订单 123 并打开详情",
                mode="safe_directed",
                business_safety="guarded",
            )
        )
    )

    assert executed == ["点击搜索", "打开订单详情"]
    assert decision_contexts[0]["compact_snapshot"]["actionable_nodes"][0]["name"] == "搜索"
    assert decision_contexts[1]["compact_snapshot"]["actionable_nodes"][0]["name"] == "订单详情"
    assert decision_contexts[1]["run_history"][0]["result"] == "executed"
    assert any(event["event"] == "directed_step_planned" for event in events)
    assert any(event["event"] == "directed_step_observed" for event in events)
    assert any(event["event"] == "analysis_complete" for event in events)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_analysis_replans_after_dom_changes -q
```

Expected: FAIL，因为当前实现只调用一次旧 `build_directed_plan`，不会第二轮基于新 DOM 重规划。

- [ ] **Step 3: 添加 settle helper**

在 `manager.py` 的 `_build_directed_dom_digest` 后添加：

```python
    async def _wait_for_directed_settle(
        self,
        page: Page,
        *,
        previous_digest: str,
        instruction: str,
        timeout_ms: int = 1500,
    ) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=500)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=500)
        except Exception:
            pass
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        last_digest = previous_digest
        stable_count = 0
        while time.monotonic() < deadline:
            try:
                observation = await self._observe_directed_page(page, instruction)
                current_digest = observation["dom_digest"]
            except Exception:
                return
            if current_digest == last_digest:
                stable_count += 1
                if stable_count >= 2:
                    return
            else:
                stable_count = 0
                last_digest = current_digest
            await page.wait_for_timeout(150)
```

- [ ] **Step 4: 替换 `analyze_directed_page` 主体**

将 `analyze_directed_page` 的 `try:` 块内旧的一次性 snapshot、plan、execute 逻辑替换为：

```python
            capture = self._captures.get(session_id)
            if capture:
                pre_calls = capture.drain_new_calls()
                if pre_calls:
                    session.captured_calls.extend(pre_calls)

            max_steps = 8
            run_history: List[Dict] = []
            directed_calls: List[CapturedApiCall] = []
            stop_reason = ""

            for step_index in range(1, max_steps + 1):
                yield {
                    "event": "progress",
                    "data": json.dumps(
                        {
                            "step": "snapshot",
                            "message": f"正在构建第 {step_index} 轮页面 DOM...",
                            "current": step_index,
                            "total": max_steps,
                        },
                        ensure_ascii=False,
                    ),
                }
                observation = await self._observe_directed_page(page, instruction)
                observation_for_prompt = {
                    "url": observation["url"],
                    "title": observation["title"],
                    "dom_digest": observation["dom_digest"],
                    "new_call_count": len(directed_calls),
                    "last_result": run_history[-1] if run_history else None,
                }
                yield {
                    "event": "directed_step_snapshot",
                    "data": json.dumps(
                        {
                            "step": step_index,
                            "url": observation["url"],
                            "title": observation["title"],
                            "dom_digest": observation["dom_digest"],
                        },
                        ensure_ascii=False,
                    ),
                }

                decision = await build_directed_step_decision(
                    instruction=instruction,
                    compact_snapshot=observation["compact_snapshot"],
                    run_history=run_history,
                    observation=observation_for_prompt,
                    model_config=model_config,
                )
                yield {
                    "event": "directed_step_planned",
                    "data": json.dumps(
                        {
                            "step": step_index,
                            "goal_status": decision.goal_status,
                            "summary": decision.summary,
                            "expected_change": decision.expected_change,
                            "done_reason": decision.done_reason,
                        },
                        ensure_ascii=False,
                    ),
                }

                if decision.goal_status in ("done", "blocked"):
                    stop_reason = decision.done_reason or decision.summary or decision.goal_status
                    yield {
                        "event": "directed_done",
                        "data": json.dumps(
                            {
                                "step": step_index,
                                "goal_status": decision.goal_status,
                                "reason": stop_reason,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    break

                action = decision.next_action
                if action is None:
                    stop_reason = "Planner did not return a next action"
                    break

                filtered = filter_action_for_business_safety(action, business_safety)
                if filtered.skipped:
                    skipped = filtered.skipped
                    run_history.append(
                        {
                            "step": step_index,
                            "result": "skipped",
                            "description": skipped.description,
                            "reason": skipped.reason,
                            "risk": skipped.risk,
                        }
                    )
                    yield {
                        "event": "directed_action_skipped",
                        "data": json.dumps(
                            {
                                "step": step_index,
                                "description": skipped.description,
                                "reason": skipped.reason,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    continue

                allowed_action = filtered.allowed
                assert allowed_action is not None
                yield {
                    "event": "directed_action_detail",
                    "data": json.dumps(
                        {
                            "index": step_index,
                            "description": describe_action(allowed_action),
                            "code": describe_locator_code(allowed_action),
                            "risk": allowed_action.risk,
                        },
                        ensure_ascii=False,
                    ),
                }
                self._mark_action(session_id)
                await execute_directed_action(page, allowed_action)
                run_history.append(
                    {
                        "step": step_index,
                        "result": "executed",
                        "description": allowed_action.description,
                        "code": describe_locator_code(allowed_action),
                        "expected_change": decision.expected_change,
                    }
                )
                yield {
                    "event": "directed_step_executed",
                    "data": json.dumps(
                        {
                            "step": step_index,
                            "description": allowed_action.description,
                            "code": describe_locator_code(allowed_action),
                        },
                        ensure_ascii=False,
                    ),
                }
                yield {
                    "event": "directed_action_executed",
                    "data": json.dumps(
                        {
                            "code": describe_locator_code(allowed_action),
                            "description": allowed_action.description,
                        },
                        ensure_ascii=False,
                    ),
                }

                await self._wait_for_directed_settle(
                    page,
                    previous_digest=observation["dom_digest"],
                    instruction=instruction,
                )

                step_calls: List[CapturedApiCall] = []
                if capture:
                    step_calls = capture.drain_new_calls()
                if step_calls:
                    directed_calls.extend(step_calls)
                    session.captured_calls.extend(step_calls)
                    yield {
                        "event": "calls_captured",
                        "data": json.dumps(
                            {
                                "mode": mode,
                                "step": step_index,
                                "calls": len(step_calls),
                            },
                            ensure_ascii=False,
                        ),
                    }
                yield {
                    "event": "directed_step_observed",
                    "data": json.dumps(
                        {
                            "step": step_index,
                            "new_calls": len(step_calls),
                            "total_directed_calls": len(directed_calls),
                        },
                        ensure_ascii=False,
                    ),
                }
            else:
                stop_reason = f"Reached max directed steps: {max_steps}"

            yield {
                "event": "progress",
                "data": json.dumps(
                    {"step": "generating", "message": "Generating tool definitions via LLM..."},
                    ensure_ascii=False,
                ),
            }

            tools = await self._generate_tools_from_calls(
                session_id,
                directed_calls,
                source="auto",
                model_config=model_config,
            )

            session.status = "idle"
            session.updated_at = datetime.now()

            yield {
                "event": "analysis_complete",
                "data": json.dumps(
                    {
                        "mode": mode,
                        "tools_generated": len(tools),
                        "total_calls": len(directed_calls),
                        "steps": len(run_history),
                        "stop_reason": stop_reason,
                    },
                    ensure_ascii=False,
                ),
            }
```

- [ ] **Step 5: 运行测试确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_analysis_replans_after_dom_changes -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: replan api monitor directed analysis per DOM step"
```

---

### Task 5: 处理失败事实、每轮安全过滤和最大步数

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 写失败事实测试**

添加：

```python
def test_directed_analysis_feeds_action_failure_into_next_step(monkeypatch):
    manager = ApiMonitorSessionManager()
    session = _route_session()
    manager.sessions[session.id] = session
    manager._pages[session.id] = _FakeDirectedPage()
    manager._captures[session.id] = _SequencedCapture([[], []])

    decision_contexts = []

    async def fake_observe(page, instruction):
        return {
            "url": page.url,
            "title": "Orders",
            "raw_snapshot": {},
            "compact_snapshot": {"url": page.url, "actionable_nodes": [{"name": "搜索"}]},
            "dom_digest": "same-page",
        }

    async def fake_decision(*, instruction, compact_snapshot, run_history, observation, model_config=None):
        decision_contexts.append(list(run_history))
        if not run_history:
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
        return DirectedStepDecision(goal_status="blocked", summary="无法继续", done_reason="搜索按钮不存在")

    async def fake_execute_action(page, action):
        raise RuntimeError("Locator not found: 搜索")

    monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_decision)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.execute_directed_action", fake_execute_action)
    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        return []

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

    assert decision_contexts[1][0]["result"] == "failed"
    assert "Locator not found" in decision_contexts[1][0]["error"]
    assert any(event["event"] == "directed_replan" for event in events)
    assert any(event["event"] == "analysis_complete" for event in events)
```

添加每轮安全过滤测试：

```python
def test_directed_analysis_filters_unsafe_action_each_step(monkeypatch):
    manager = ApiMonitorSessionManager()
    session = _route_session()
    manager.sessions[session.id] = session
    manager._pages[session.id] = _FakeDirectedPage()
    manager._captures[session.id] = _SequencedCapture([[], []])

    executed = []
    contexts = []

    async def fake_observe(page, instruction):
        return {
            "url": page.url,
            "title": "Orders",
            "raw_snapshot": {},
            "compact_snapshot": {"url": page.url, "actionable_nodes": [{"name": "删除"}, {"name": "搜索"}]},
            "dom_digest": "orders",
        }

    async def fake_decision(*, instruction, compact_snapshot, run_history, observation, model_config=None):
        contexts.append(list(run_history))
        if not run_history:
            return DirectedStepDecision(
                goal_status="continue",
                summary="尝试删除",
                next_action=DirectedAction(
                    action="click",
                    locator={"method": "role", "role": "button", "name": "删除"},
                    description="删除订单",
                    risk="unsafe",
                    reason="删除是破坏性操作",
                ),
            )
        return DirectedStepDecision(goal_status="blocked", summary="安全模式停止", done_reason="删除被阻止")

    async def fake_execute_action(page, action):
        executed.append(action.description)

    monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_decision)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.execute_directed_action", fake_execute_action)
    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        return []

    monkeypatch.setattr(manager, "_generate_tools_from_calls", fake_generate_tools)

    events = asyncio.run(
        _collect_events(
            manager.analyze_directed_page(
                session.id,
                instruction="删除订单",
                mode="safe_directed",
                business_safety="guarded",
            )
        )
    )

    assert executed == []
    assert contexts[1][0]["result"] == "skipped"
    assert any(event["event"] == "directed_action_skipped" for event in events)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_analysis_feeds_action_failure_into_next_step RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_analysis_filters_unsafe_action_each_step -q
```

Expected: 至少失败事实测试 FAIL，因为 Task 4 的实现没有捕获 action exception 后继续重规划。

- [ ] **Step 3: 在动作执行处捕获失败事实**

在 Task 4 的 `await execute_directed_action(page, allowed_action)` 周围改为：

```python
                try:
                    await execute_directed_action(page, allowed_action)
                except Exception as action_exc:
                    error_text = str(action_exc)
                    run_history.append(
                        {
                            "step": step_index,
                            "result": "failed",
                            "description": allowed_action.description,
                            "code": describe_locator_code(allowed_action),
                            "error": error_text,
                            "expected_change": decision.expected_change,
                        }
                    )
                    if capture:
                        failed_step_calls = capture.drain_new_calls()
                        if failed_step_calls:
                            directed_calls.extend(failed_step_calls)
                            session.captured_calls.extend(failed_step_calls)
                    yield {
                        "event": "directed_replan",
                        "data": json.dumps(
                            {
                                "step": step_index,
                                "description": allowed_action.description,
                                "error": error_text,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    continue
```

保留成功路径里的原 `run_history.append({"result": "executed", ...})`。

- [ ] **Step 4: 添加最大失败数保护**

在循环前加入：

```python
            failed_steps = 0
            max_failures = 3
```

在 `except Exception as action_exc:` 内 `continue` 前加入：

```python
                    failed_steps += 1
                    if failed_steps >= max_failures:
                        stop_reason = f"Reached max directed action failures: {max_failures}"
                        break
```

- [ ] **Step 5: 运行测试确认通过**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_analysis_feeds_action_failure_into_next_step RpaClaw/backend/tests/test_api_monitor_analysis_modes.py::test_directed_analysis_filters_unsafe_action_each_step -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "fix: replan directed analysis after action failures"
```

---

### Task 6: 更新旧测试并跑定向分析回归

**Files:**
- Modify: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 更新旧的一次性 plan 测试**

将 `test_directed_analysis_uses_compact_snapshot_and_generates_tools` 中的 monkeypatch 从旧函数：

```python
monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_plan", fake_build_directed_plan)
monkeypatch.setattr("backend.rpa.api_monitor.manager.execute_directed_plan", fake_execute_directed_plan)
```

改成动态循环函数：

```python
async def fake_observe(page, instruction):
    calls["snapshot_page"] = page
    calls["compact_instruction"] = instruction
    return {
        "url": page.url,
        "title": "Orders",
        "raw_snapshot": {"url": page.url, "title": "Orders"},
        "compact_snapshot": {"mode": "clean_snapshot", "url": page.url, "title": "Orders"},
        "dom_digest": "orders",
    }

async def fake_build_directed_step_decision(*, instruction, compact_snapshot, run_history, observation, model_config=None):
    calls["plan_instruction"] = instruction
    calls["compact_snapshot"] = compact_snapshot
    calls["business_safety"] = "guarded"
    return DirectedStepDecision(goal_status="done", summary="完成", done_reason="无需额外动作")

monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_build_directed_step_decision)
```

保留断言：

```python
assert calls["compact_instruction"] == "搜索订单 123"
assert calls["plan_instruction"] == "搜索订单 123"
assert calls["compact_snapshot"] == {
    "mode": "clean_snapshot",
    "url": "https://example.test/orders",
    "title": "Orders",
}
assert calls["tool_source"] == "auto"
assert calls["tool_call_count"] == 0
assert any(event["event"] == "directed_step_planned" for event in events)
assert any(event["event"] == "analysis_complete" for event in events)
```

- [ ] **Step 2: 更新 skipped 测试**

将 `test_directed_analysis_emits_skipped_actions` 中旧的 `fake_build_directed_plan` 和 `fake_execute_directed_plan` 改成：

```python
async def fake_build_directed_step_decision(*, instruction, compact_snapshot, run_history, observation, model_config=None):
    if not run_history:
        return DirectedStepDecision(
            goal_status="continue",
            summary="删除订单",
            next_action=DirectedAction(
                action="click",
                locator={"method": "role", "role": "button", "name": "删除"},
                description="删除订单",
                risk="unsafe",
                reason="删除属于高风险动作",
            ),
        )
    return DirectedStepDecision(goal_status="blocked", summary="停止", done_reason="unsafe 动作已跳过")

monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_build_directed_step_decision)
```

删除对 `execute_directed_plan` 的 monkeypatch。

- [ ] **Step 3: 更新 pre-drain 测试**

将 `test_directed_analysis_pre_drains_historical_calls` 中 planner monkeypatch 改成返回 `done`：

```python
async def fake_build_directed_step_decision(*, instruction, compact_snapshot, run_history, observation, model_config=None):
    return DirectedStepDecision(goal_status="done", summary="完成", done_reason="只验证 pre-drain")

monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_build_directed_step_decision)
```

删除旧 `execute_directed_plan` monkeypatch。

- [ ] **Step 4: 运行定向分析测试文件**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py -q
```

Expected: PASS。

- [ ] **Step 5: 跑 API Monitor 相关回归**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py RpaClaw/backend/tests/test_api_monitor_capture.py RpaClaw/backend/tests/test_api_monitor_confidence.py RpaClaw/backend/tests/test_api_monitor_mcp_contract.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "test: cover dynamic api monitor directed analysis"
```

---

### Task 7: 最终集成检查

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py`
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: 检查旧函数兼容性**

Run:

```bash
rg -n "build_directed_plan|execute_directed_plan|build_directed_step_decision|execute_directed_action" RpaClaw/backend
```

Expected:

- `build_directed_plan` 和 `execute_directed_plan` 仍在 `directed_analyzer.py` 中定义。
- `analyze_directed_page` 使用 `build_directed_step_decision` 和 `execute_directed_action`。
- 没有路由层直接依赖旧 plan 执行函数。

- [ ] **Step 2: 检查新增事件名称**

Run:

```bash
rg -n "directed_step_snapshot|directed_step_planned|directed_step_executed|directed_step_observed|directed_replan|directed_done" RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue
```

Expected:

- `manager.py` 至少包含全部 6 个事件名。
- 如果 `ApiMonitorPage.vue` 对未知 SSE 事件只作为日志显示，则前端无需修改。
- 如果 `ApiMonitorPage.vue` 白名单过滤事件，则增加这 6 个事件映射，文案使用中文短句。

- [ ] **Step 3: 跑 focused backend tests**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest RpaClaw/backend/tests/test_api_monitor_analysis_modes.py RpaClaw/backend/tests/test_api_monitor_capture.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py -q
```

Expected: PASS。

- [ ] **Step 4: 查看工作区 diff**

Run:

```bash
git diff --stat
git diff -- RpaClaw/backend/rpa/api_monitor/directed_analyzer.py RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
```

Expected:

- diff 只包含 directed analyzer、API monitor manager、API monitor directed tests。
- 没有 `.rpaclaw/` 或 `package-lock.json` 被加入 diff。

- [ ] **Step 5: 最终提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/directed_analyzer.py RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: enable api monitor directed DOM replanning"
```

---

## 自检记录

- Spec 覆盖：计划覆盖单步决策、运行状态、观察层、prompt、动态循环、安全过滤、失败事实、逐步 API drain、SSE 事件和测试计划。
- 范围控制：不实现通用浏览器 Agent，不新增持久化运行历史，不引入站点模板或经验规则主路径。
- 类型一致性：`DirectedStepDecision.goal_status` 使用 `continue|done|blocked`；`next_action` 复用 `DirectedAction`；manager 循环调用 `build_directed_step_decision` 与 `execute_directed_action`。
- 测试策略：每个行为先写失败测试，再最小实现，再跑 focused tests，最后跑 API Monitor 回归。
