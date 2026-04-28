# API Monitor 多分析模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 API Monitor 的单一分析动作扩展为“自由分析 / 安全分析 / 定向分析”三种可扩展模式，并把保存 MCP 的结果反馈从监控日志迁移到页面 toast/message。

**Architecture:** 后端保留现有自由分析路径，新增模式注册表和定向分析 handler；定向类模式使用 RPA 录制同款 snapshot compression，再执行受限的结构化 Playwright action plan。前端使用统一 `ANALYSIS_MODES` 配置渲染分析下拉框，并通过同一个 `/analyze` payload 传递 `mode` 和 `instruction`，为后续模式继续扩展。

**Tech Stack:** FastAPI, Pydantic v2, Playwright async API, LangChain model wrapper, Vue 3 Composition API, TypeScript, Vitest, pytest.

---

## File Structure

- Create: `RpaClaw/backend/rpa/api_monitor/analysis_modes.py`
  - Holds backend analysis mode registry, config dataclass, and validation helpers.
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
  - Adds `AnalyzeSessionRequest`.
- Modify: `RpaClaw/backend/route/api_monitor.py`
  - Accepts optional analyze request body and dispatches to free or directed handler through the registry.
- Create: `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py`
  - Builds directed action plans from compact snapshots, filters safe-mode actions, validates structured actions, and executes only allowed Playwright page operations.
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
  - Adds `analyze_directed_page`, reusing capture draining, snapshot building, `compact_recording_snapshot`, directed planner/executor, and existing `_generate_tools_from_calls`.
- Create: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`
  - Covers route defaults, validation, dispatch, directed manager flow, and DOM compression use.
- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts`
  - Adds typed `AnalyzeSessionPayload` and sends request body to SSE helper.
- Create: `RpaClaw/frontend/src/utils/apiMonitorAnalysisModes.ts`
  - Holds frontend mode config and small pure helpers for rendering and validation.
- Create: `RpaClaw/frontend/src/utils/apiMonitorAnalysisModes.test.ts`
  - Covers mode config, instruction visibility, and start eligibility.
- Create: `RpaClaw/frontend/src/api/apiMonitor.test.ts`
  - Covers `analyzeSession` SSE payload behavior.
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
  - Replaces single analyze button with dropdown-driven mode selection, instruction input, mode-aware SSE handling, and toast feedback for MCP publish.
- Create: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.analysis.test.ts`
  - Covers dropdown rendering, directed instruction gating, API payload, and publish toast behavior.

## Task 1: Backend Mode Registry And Request Model

**Files:**
- Create: `RpaClaw/backend/rpa/api_monitor/analysis_modes.py`
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: Write failing tests for mode registry and request defaults**

Add this initial test file:

```py
import pytest

from backend.rpa.api_monitor.analysis_modes import (
    ANALYSIS_MODE_REGISTRY,
    get_analysis_mode_config,
)
from backend.rpa.api_monitor.models import AnalyzeSessionRequest


def test_analyze_request_defaults_to_free_mode():
    request = AnalyzeSessionRequest()

    assert request.mode == "free"
    assert request.instruction == ""


def test_analysis_mode_registry_exposes_three_initial_modes():
    assert set(ANALYSIS_MODE_REGISTRY) == {"free", "safe_directed", "directed"}
    assert ANALYSIS_MODE_REGISTRY["free"].handler == "free"
    assert ANALYSIS_MODE_REGISTRY["free"].requires_instruction is False
    assert ANALYSIS_MODE_REGISTRY["safe_directed"].handler == "directed"
    assert ANALYSIS_MODE_REGISTRY["safe_directed"].business_safety == "guarded"
    assert ANALYSIS_MODE_REGISTRY["directed"].handler == "directed"
    assert ANALYSIS_MODE_REGISTRY["directed"].business_safety == "user_controlled"


def test_get_analysis_mode_config_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Unknown API Monitor analysis mode: mystery"):
        get_analysis_mode_config("mystery")
```

- [ ] **Step 2: Run the backend test and verify it fails**

Run:

```bash
cd RpaClaw/backend
python -m pytest tests/test_api_monitor_analysis_modes.py -q
```

Expected: FAIL because `backend.rpa.api_monitor.analysis_modes` and `AnalyzeSessionRequest` do not exist.

- [ ] **Step 3: Add mode registry implementation**

Create `RpaClaw/backend/rpa/api_monitor/analysis_modes.py`:

```py
"""Analysis mode registry for API Monitor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AnalysisHandler = Literal["free", "directed"]
AnalysisBusinessSafety = Literal["none", "guarded", "user_controlled"]


@dataclass(frozen=True)
class AnalysisModeConfig:
    key: str
    label: str
    handler: AnalysisHandler
    requires_instruction: bool
    business_safety: AnalysisBusinessSafety = "none"


ANALYSIS_MODE_REGISTRY: dict[str, AnalysisModeConfig] = {
    "free": AnalysisModeConfig(
        key="free",
        label="自由分析",
        handler="free",
        requires_instruction=False,
        business_safety="none",
    ),
    "safe_directed": AnalysisModeConfig(
        key="safe_directed",
        label="安全分析",
        handler="directed",
        requires_instruction=True,
        business_safety="guarded",
    ),
    "directed": AnalysisModeConfig(
        key="directed",
        label="定向分析",
        handler="directed",
        requires_instruction=True,
        business_safety="user_controlled",
    ),
}


def normalize_analysis_mode(value: str | None) -> str:
    mode = str(value or "free").strip() or "free"
    return mode


def get_analysis_mode_config(value: str | None) -> AnalysisModeConfig:
    mode = normalize_analysis_mode(value)
    config = ANALYSIS_MODE_REGISTRY.get(mode)
    if config is None:
        raise ValueError(f"Unknown API Monitor analysis mode: {mode}")
    return config
```

- [ ] **Step 4: Add request model**

Append this model in `RpaClaw/backend/rpa/api_monitor/models.py` near the other API request schemas:

```py
class AnalyzeSessionRequest(BaseModel):
    mode: str = "free"
    instruction: str = ""
```

- [ ] **Step 5: Run the test and verify it passes**

Run:

```bash
cd RpaClaw/backend
python -m pytest tests/test_api_monitor_analysis_modes.py -q
```

Expected: PASS for the three tests in this file.

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/analysis_modes.py RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: add API Monitor analysis mode registry"
```

## Task 2: Backend Analyze Route Dispatch

**Files:**
- Modify: `RpaClaw/backend/route/api_monitor.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: Add failing route dispatch tests**

Append these tests to `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`:

```py
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.route import api_monitor as api_monitor_route
from backend.rpa.api_monitor.models import ApiMonitorSession


class _RouteUser:
    id = "user-1"
    username = "tester"
    role = "user"


def _route_session() -> ApiMonitorSession:
    return ApiMonitorSession(
        id="session-1",
        user_id="user-1",
        sandbox_session_id="sandbox-1",
        target_url="https://example.test/app",
    )


def _route_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_monitor_route.router, prefix="/api/v1")
    app.dependency_overrides[api_monitor_route.get_current_user] = lambda: _RouteUser()
    return app


def _sse_payload(response) -> str:
    return response.text


def test_analyze_route_empty_body_dispatches_free_mode(monkeypatch):
    calls: list[dict] = []

    async def fake_analyze_page(session_id, model_config=None):
        calls.append({"session_id": session_id, "model_config": model_config})
        yield {"event": "analysis_complete", "data": json.dumps({"tools_generated": 0, "total_calls": 0})}

    async def fake_resolve_user_model_config(user_id):
        return None

    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: _route_session())
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "analyze_page", fake_analyze_page)
    monkeypatch.setattr(api_monitor_route, "_resolve_user_model_config", fake_resolve_user_model_config)

    response = TestClient(_route_app()).post("/api/v1/api-monitor/session/session-1/analyze")

    assert response.status_code == 200
    assert "analysis_complete" in _sse_payload(response)
    assert calls == [{"session_id": "session-1", "model_config": None}]


def test_analyze_route_unknown_mode_returns_400(monkeypatch):
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: _route_session())

    response = TestClient(_route_app()).post(
        "/api/v1/api-monitor/session/session-1/analyze",
        json={"mode": "mystery"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown API Monitor analysis mode: mystery"


def test_analyze_route_directed_mode_requires_instruction(monkeypatch):
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: _route_session())

    response = TestClient(_route_app()).post(
        "/api/v1/api-monitor/session/session-1/analyze",
        json={"mode": "safe_directed", "instruction": "   "},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Instruction is required for safe_directed analysis"


def test_analyze_route_dispatches_safe_directed_mode(monkeypatch):
    calls: list[dict] = []

    async def fake_analyze_directed_page(
        session_id,
        *,
        instruction,
        mode,
        business_safety,
        model_config=None,
    ):
        calls.append(
            {
                "session_id": session_id,
                "instruction": instruction,
                "mode": mode,
                "business_safety": business_safety,
                "model_config": model_config,
            }
        )
        yield {"event": "analysis_complete", "data": json.dumps({"mode": mode, "tools_generated": 0, "total_calls": 0})}

    async def fake_resolve_user_model_config(user_id):
        return None

    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: _route_session())
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "analyze_directed_page", fake_analyze_directed_page)
    monkeypatch.setattr(api_monitor_route, "_resolve_user_model_config", fake_resolve_user_model_config)

    response = TestClient(_route_app()).post(
        "/api/v1/api-monitor/session/session-1/analyze",
        json={"mode": "safe_directed", "instruction": "搜索订单 123"},
    )

    assert response.status_code == 200
    assert "analysis_complete" in _sse_payload(response)
    assert calls == [
        {
            "session_id": "session-1",
            "instruction": "搜索订单 123",
            "mode": "safe_directed",
            "business_safety": "guarded",
            "model_config": None,
        }
    ]


def test_analyze_route_dispatches_user_controlled_directed_mode(monkeypatch):
    calls: list[dict] = []

    async def fake_analyze_directed_page(
        session_id,
        *,
        instruction,
        mode,
        business_safety,
        model_config=None,
    ):
        calls.append({"mode": mode, "business_safety": business_safety, "instruction": instruction})
        yield {"event": "analysis_complete", "data": json.dumps({"mode": mode, "tools_generated": 0, "total_calls": 0})}

    async def fake_resolve_user_model_config(user_id):
        return None

    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: _route_session())
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "analyze_directed_page", fake_analyze_directed_page)
    monkeypatch.setattr(api_monitor_route, "_resolve_user_model_config", fake_resolve_user_model_config)

    response = TestClient(_route_app()).post(
        "/api/v1/api-monitor/session/session-1/analyze",
        json={"mode": "directed", "instruction": "删除测试订单"},
    )

    assert response.status_code == 200
    assert calls == [
        {
            "mode": "directed",
            "business_safety": "user_controlled",
            "instruction": "删除测试订单",
        }
    ]
```

- [ ] **Step 2: Run the route tests and verify they fail**

Run:

```bash
cd RpaClaw/backend
python -m pytest tests/test_api_monitor_analysis_modes.py -q
```

Expected: FAIL because the analyze route does not accept a request body, does not validate modes, and does not dispatch directed mode.

- [ ] **Step 3: Update route imports**

Modify the import section in `RpaClaw/backend/route/api_monitor.py`:

```py
from fastapi import APIRouter, Body, Depends, HTTPException, WebSocket, WebSocketDisconnect
```

Add imports from API Monitor modules:

```py
from backend.rpa.api_monitor.analysis_modes import get_analysis_mode_config
from backend.rpa.api_monitor.models import (
    AnalyzeSessionRequest,
    ApiMonitorSession,
    StartSessionRequest,
    NavigateRequest,
    PublishMcpRequest,
    UpdateToolRequest,
    UpdateToolSelectionRequest,
)
```

- [ ] **Step 4: Replace `analyze_session` with mode-aware dispatch**

Replace the existing `analyze_session` function in `RpaClaw/backend/route/api_monitor.py` with:

```py
@router.post("/session/{session_id}/analyze")
async def analyze_session(
    session_id: str,
    request: AnalyzeSessionRequest | None = Body(default=None),
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    payload = request or AnalyzeSessionRequest()
    try:
        mode_config = get_analysis_mode_config(payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    instruction = payload.instruction.strip()
    if mode_config.requires_instruction and not instruction:
        raise HTTPException(
            status_code=400,
            detail=f"Instruction is required for {mode_config.key} analysis",
        )

    model_config = await _resolve_user_model_config(str(current_user.id))

    async def event_generator():
        if mode_config.handler == "free":
            async for event in api_monitor_manager.analyze_page(
                session_id,
                model_config=model_config,
            ):
                yield event
            return

        async for event in api_monitor_manager.analyze_directed_page(
            session_id,
            instruction=instruction,
            mode=mode_config.key,
            business_safety=mode_config.business_safety,
            model_config=model_config,
        ):
            yield event

    return EventSourceResponse(event_generator())
```

- [ ] **Step 5: Run the route tests and verify they pass**

Run:

```bash
cd RpaClaw/backend
python -m pytest tests/test_api_monitor_analysis_modes.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/route/api_monitor.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: dispatch API Monitor analysis modes"
```

## Task 3: Directed Analyzer Planning And Safety Unit

**Files:**
- Create: `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: Add failing directed analyzer tests**

Append these tests to `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`:

```py
import pytest

from backend.rpa.api_monitor.directed_analyzer import (
    DirectedAction,
    DirectedAnalysisPlan,
    DirectedExecutionResult,
    build_locator,
    filter_actions_for_business_safety,
)


def test_safe_directed_filters_unsafe_actions():
    plan = DirectedAnalysisPlan(
        summary="Search then delete",
        actions=[
            DirectedAction(
                action="fill",
                locator={"method": "placeholder", "value": "订单号"},
                value="123",
                description="填写订单号",
                risk="safe",
            ),
            DirectedAction(
                action="click",
                locator={"method": "role", "role": "button", "name": "删除"},
                description="删除订单",
                risk="unsafe",
                reason="删除订单属于破坏性动作",
            ),
        ],
    )

    filtered = filter_actions_for_business_safety(plan, "guarded")

    assert [action.description for action in filtered.allowed] == ["填写订单号"]
    assert [action.description for action in filtered.skipped] == ["删除订单"]


def test_user_controlled_directed_keeps_unsafe_actions():
    plan = DirectedAnalysisPlan(
        summary="Delete test order",
        actions=[
            DirectedAction(
                action="click",
                locator={"method": "role", "role": "button", "name": "删除"},
                description="删除测试订单",
                risk="unsafe",
                reason="用户选择定向分析，业务风险由用户把控",
            )
        ],
    )

    filtered = filter_actions_for_business_safety(plan, "user_controlled")

    assert [action.description for action in filtered.allowed] == ["删除测试订单"]
    assert filtered.skipped == []


class _FakePage:
    def __init__(self):
        self.calls = []

    def get_by_role(self, role, name=None):
        self.calls.append(("role", role, name))
        return "role-locator"

    def get_by_text(self, value):
        self.calls.append(("text", value))
        return "text-locator"

    def get_by_placeholder(self, value):
        self.calls.append(("placeholder", value))
        return "placeholder-locator"

    def get_by_label(self, value):
        self.calls.append(("label", value))
        return "label-locator"

    def locator(self, value):
        self.calls.append(("css", value))
        return "css-locator"


def test_build_locator_supports_allowed_locator_methods():
    page = _FakePage()

    assert build_locator(page, {"method": "role", "role": "button", "name": "搜索"}) == "role-locator"
    assert build_locator(page, {"method": "text", "value": "订单"}) == "text-locator"
    assert build_locator(page, {"method": "placeholder", "value": "订单号"}) == "placeholder-locator"
    assert build_locator(page, {"method": "label", "value": "状态"}) == "label-locator"
    assert build_locator(page, {"method": "css", "value": "[data-testid='search']"}) == "css-locator"

    assert page.calls == [
        ("role", "button", "搜索"),
        ("text", "订单"),
        ("placeholder", "订单号"),
        ("label", "状态"),
        ("css", "[data-testid='search']"),
    ]


def test_build_locator_rejects_unknown_method():
    with pytest.raises(ValueError, match="Unsupported directed locator method: xpath"):
        build_locator(_FakePage(), {"method": "xpath", "value": "//button"})
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd RpaClaw/backend
python -m pytest tests/test_api_monitor_analysis_modes.py -q
```

Expected: FAIL because `directed_analyzer.py` does not exist.

- [ ] **Step 3: Implement directed analyzer models, filtering, locator building, LLM planning, and execution**

Create `RpaClaw/backend/rpa/api_monitor/directed_analyzer.py`:

```py
"""Directed analysis planning and execution for API Monitor."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from backend.deepagent.engine import get_llm_model

from .analysis_modes import AnalysisBusinessSafety


DirectedActionType = Literal["click", "fill", "press", "select", "wait"]
DirectedActionRisk = Literal["safe", "unsafe"]


class DirectedAction(BaseModel):
    action: DirectedActionType
    locator: Dict[str, Any] = Field(default_factory=dict)
    value: str = ""
    key: str = ""
    timeout_ms: int = 500
    description: str = ""
    risk: DirectedActionRisk = "safe"
    reason: str = ""


class DirectedAnalysisPlan(BaseModel):
    summary: str = ""
    actions: List[DirectedAction] = Field(default_factory=list)


class FilteredDirectedActions(BaseModel):
    allowed: List[DirectedAction] = Field(default_factory=list)
    skipped: List[DirectedAction] = Field(default_factory=list)


class DirectedExecutionResult(BaseModel):
    executed: List[DirectedAction] = Field(default_factory=list)
    skipped: List[DirectedAction] = Field(default_factory=list)


DIRECTED_PLAN_SYSTEM = """\
你是 API Monitor 的浏览器操作规划器。你会收到用户操作目标和精简后的页面 DOM。
只返回 JSON，不要返回 markdown。

返回结构：
{
  "summary": "一句话说明计划",
  "actions": [
    {
      "action": "click|fill|press|select|wait",
      "locator": {"method": "role|text|placeholder|label|css", "role": "button", "name": "搜索", "value": ""},
      "value": "fill/select 使用的值",
      "key": "press 使用的按键",
      "timeout_ms": 500,
      "description": "这个动作做什么",
      "risk": "safe|unsafe",
      "reason": "为什么安全或不安全"
    }
  ]
}

安全判定：
- 搜索、筛选、分页、打开详情、切换 tab、展开区域通常是 safe。
- 删除、注销、支付、提交订单、撤销授权、禁用、不可逆提交通常是 unsafe。

平台约束：
- 只能规划页面内 Playwright 操作。
- 不要规划 shell、文件、权限、下载目录或本地系统操作。
- 不要返回 Python 代码。
"""


DIRECTED_PLAN_USER = """\
用户目标：
{instruction}

当前页面精简 DOM：
{compact_snapshot}

生成最短可执行操作计划。
"""


def strip_json_fence(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\\s*", "", text)
    text = re.sub(r"\\s*```\\s*$", "", text)
    return text.strip()


async def build_directed_plan(
    *,
    instruction: str,
    compact_snapshot: Dict[str, Any],
    model_config: Optional[Dict] = None,
) -> DirectedAnalysisPlan:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    model = get_llm_model(config=model_config, streaming=False)
    messages = [
        SystemMessage(content=DIRECTED_PLAN_SYSTEM),
        HumanMessage(
            content=DIRECTED_PLAN_USER.format(
                instruction=instruction,
                compact_snapshot=json.dumps(compact_snapshot, ensure_ascii=False, indent=2),
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
    return DirectedAnalysisPlan.model_validate(parsed)


def filter_actions_for_business_safety(
    plan: DirectedAnalysisPlan,
    business_safety: AnalysisBusinessSafety,
) -> FilteredDirectedActions:
    if business_safety != "guarded":
        return FilteredDirectedActions(allowed=list(plan.actions), skipped=[])

    allowed: List[DirectedAction] = []
    skipped: List[DirectedAction] = []
    for action in plan.actions:
        if action.risk == "safe":
            allowed.append(action)
        else:
            skipped.append(action)
    return FilteredDirectedActions(allowed=allowed, skipped=skipped)


def build_locator(page: Any, locator: Dict[str, Any]) -> Any:
    method = str(locator.get("method") or "").strip()
    if method == "role":
        return page.get_by_role(locator.get("role") or "button", name=locator.get("name") or None)
    if method == "text":
        return page.get_by_text(locator.get("value") or locator.get("text") or locator.get("name") or "")
    if method == "placeholder":
        return page.get_by_placeholder(locator.get("value") or locator.get("placeholder") or "")
    if method == "label":
        return page.get_by_label(locator.get("value") or locator.get("label") or "")
    if method == "css":
        return page.locator(locator.get("value") or locator.get("selector") or "")
    raise ValueError(f"Unsupported directed locator method: {method}")


async def execute_directed_action(page: Any, action: DirectedAction) -> None:
    if action.action == "wait":
        await page.wait_for_timeout(max(0, min(action.timeout_ms, 10_000)))
        return

    target = build_locator(page, action.locator)
    if action.action == "click":
        await target.click()
        await page.wait_for_timeout(500)
        return
    if action.action == "fill":
        await target.fill(action.value)
        await page.wait_for_timeout(300)
        return
    if action.action == "press":
        await target.press(action.key or "Enter")
        await page.wait_for_timeout(500)
        return
    if action.action == "select":
        await target.select_option(action.value)
        await page.wait_for_timeout(300)
        return
    raise ValueError(f"Unsupported directed action: {action.action}")


async def execute_directed_plan(
    page: Any,
    plan: DirectedAnalysisPlan,
    *,
    business_safety: AnalysisBusinessSafety,
) -> DirectedExecutionResult:
    filtered = filter_actions_for_business_safety(plan, business_safety)
    executed: List[DirectedAction] = []
    for action in filtered.allowed:
        await execute_directed_action(page, action)
        executed.append(action)
    return DirectedExecutionResult(executed=executed, skipped=filtered.skipped)
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
cd RpaClaw/backend
python -m pytest tests/test_api_monitor_analysis_modes.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/directed_analyzer.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: add directed API Monitor analyzer"
```

## Task 4: Manager Directed Analysis Flow With DOM Compression

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`

- [ ] **Step 1: Add failing manager flow tests**

Append these tests to `RpaClaw/backend/tests/test_api_monitor_analysis_modes.py`:

```py
import asyncio
from datetime import datetime

from backend.rpa.api_monitor.manager import ApiMonitorSessionManager
from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest


class _FakeCapture:
    def __init__(self, calls):
        self.calls = list(calls)

    def drain_new_calls(self):
        calls = list(self.calls)
        self.calls = []
        return calls


class _FakeDirectedPage:
    url = "https://example.test/orders"
    main_frame = object()

    async def title(self):
        return "Orders"

    async def wait_for_timeout(self, _timeout):
        return None


def _captured_call() -> CapturedApiCall:
    return CapturedApiCall(
        request=CapturedRequest(
            request_id="req-1",
            url="https://example.test/api/orders?keyword=123",
            method="GET",
            headers={},
            timestamp=datetime(2026, 4, 28),
            resource_type="fetch",
        ),
        url_pattern="/api/orders",
    )


async def _collect_events(generator):
    events = []
    async for event in generator:
        events.append(event)
    return events


def test_directed_analysis_uses_compact_snapshot_and_generates_tools(monkeypatch):
    manager = ApiMonitorSessionManager()
    session = _route_session()
    manager.sessions[session.id] = session
    manager._pages[session.id] = _FakeDirectedPage()
    manager._captures[session.id] = _FakeCapture([_captured_call()])

    calls: dict[str, object] = {}

    async def fake_build_page_snapshot(page, frame_path_builder):
        calls["snapshot_page"] = page
        calls["frame_path_builder"] = frame_path_builder
        return {
            "url": page.url,
            "title": "Orders",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
            "table_views": [],
            "detail_views": [],
        }

    def fake_compact_recording_snapshot(snapshot, instruction):
        calls["compact_instruction"] = instruction
        return {"mode": "clean_snapshot", "url": snapshot["url"], "title": snapshot["title"]}

    async def fake_build_directed_plan(*, instruction, compact_snapshot, model_config=None):
        calls["plan_instruction"] = instruction
        calls["compact_snapshot"] = compact_snapshot
        return DirectedAnalysisPlan(summary="搜索订单", actions=[])

    async def fake_execute_directed_plan(page, plan, *, business_safety):
        calls["business_safety"] = business_safety
        return DirectedExecutionResult(executed=[], skipped=[])

    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        calls["tool_source"] = source
        calls["tool_call_count"] = len(calls_arg)
        return []

    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_page_snapshot", fake_build_page_snapshot)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.compact_recording_snapshot", fake_compact_recording_snapshot)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_plan", fake_build_directed_plan)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.execute_directed_plan", fake_execute_directed_plan)
    monkeypatch.setattr(manager, "_generate_tools_from_calls", fake_generate_tools)

    events = asyncio.run(
        _collect_events(
            manager.analyze_directed_page(
                session.id,
                instruction="搜索订单 123",
                mode="safe_directed",
                business_safety="guarded",
            )
        )
    )

    assert session.status == "idle"
    assert calls["compact_instruction"] == "搜索订单 123"
    assert calls["plan_instruction"] == "搜索订单 123"
    assert calls["compact_snapshot"] == {
        "mode": "clean_snapshot",
        "url": "https://example.test/orders",
        "title": "Orders",
    }
    assert calls["business_safety"] == "guarded"
    assert calls["tool_source"] == "auto"
    assert calls["tool_call_count"] == 1
    assert any(event["event"] == "directed_plan_ready" for event in events)
    assert any(event["event"] == "analysis_complete" for event in events)


def test_directed_analysis_emits_skipped_actions(monkeypatch):
    manager = ApiMonitorSessionManager()
    session = _route_session()
    manager.sessions[session.id] = session
    manager._pages[session.id] = _FakeDirectedPage()
    manager._captures[session.id] = _FakeCapture([])

    async def fake_build_page_snapshot(page, frame_path_builder):
        return {"url": page.url, "title": "Orders", "frames": []}

    async def fake_build_directed_plan(*, instruction, compact_snapshot, model_config=None):
        return DirectedAnalysisPlan(
            summary="删除订单",
            actions=[
                DirectedAction(
                    action="click",
                    locator={"method": "role", "role": "button", "name": "删除"},
                    description="删除订单",
                    risk="unsafe",
                    reason="删除属于高风险动作",
                )
            ],
        )

    async def fake_execute_directed_plan(page, plan, *, business_safety):
        return DirectedExecutionResult(executed=[], skipped=plan.actions)

    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        return []

    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_page_snapshot", fake_build_page_snapshot)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.compact_recording_snapshot", lambda snapshot, instruction: snapshot)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_plan", fake_build_directed_plan)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.execute_directed_plan", fake_execute_directed_plan)
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

    skipped = [event for event in events if event["event"] == "directed_action_skipped"]
    assert len(skipped) == 1
    assert "删除订单" in skipped[0]["data"]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
cd RpaClaw/backend
python -m pytest tests/test_api_monitor_analysis_modes.py -q
```

Expected: FAIL because `analyze_directed_page` and manager imports do not exist.

- [ ] **Step 3: Add manager imports**

Add these imports near the top of `RpaClaw/backend/rpa/api_monitor/manager.py`:

```py
from typing import Any

from backend.rpa.assistant_runtime import build_page_snapshot
from backend.rpa.frame_selectors import build_frame_path
from backend.rpa.snapshot_compression import compact_recording_snapshot

from .analysis_modes import AnalysisBusinessSafety
from .directed_analyzer import build_directed_plan, execute_directed_plan
```

If `Any` is already covered by an existing typing import after local edits, keep a single consolidated import line.

- [ ] **Step 4: Add `analyze_directed_page` to `ApiMonitorSessionManager`**

Add this method below the existing `analyze_page` method in `RpaClaw/backend/rpa/api_monitor/manager.py`:

```py
    async def analyze_directed_page(
        self,
        session_id: str,
        *,
        instruction: str,
        mode: str,
        business_safety: AnalysisBusinessSafety,
        model_config: Optional[Dict] = None,
    ) -> AsyncGenerator[Dict, None]:
        """Directed analysis: plan browser actions from compact DOM, execute them, then generate tools."""
        session = self._require_session(session_id)
        page = self._require_page(session_id)
        session.status = "analyzing"
        session.updated_at = datetime.now()

        yield {
            "event": "analysis_started",
            "data": json.dumps(
                {
                    "session_id": session_id,
                    "url": session.target_url or getattr(page, "url", ""),
                    "mode": mode,
                    "has_instruction": bool(instruction.strip()),
                },
                ensure_ascii=False,
            ),
        }

        try:
            capture = self._captures.get(session_id)
            if capture:
                pre_calls = capture.drain_new_calls()
                if pre_calls:
                    session.captured_calls.extend(pre_calls)

            yield {
                "event": "progress",
                "data": json.dumps(
                    {"step": "snapshot", "message": "正在构建并精简当前页面 DOM..."},
                    ensure_ascii=False,
                ),
            }

            raw_snapshot = await build_page_snapshot(page, build_frame_path)
            compact_snapshot = compact_recording_snapshot(raw_snapshot, instruction)

            yield {
                "event": "progress",
                "data": json.dumps(
                    {"step": "planning", "message": "正在根据指令生成操作计划..."},
                    ensure_ascii=False,
                ),
            }

            plan = await build_directed_plan(
                instruction=instruction,
                compact_snapshot=compact_snapshot,
                model_config=model_config,
            )

            yield {
                "event": "directed_plan_ready",
                "data": json.dumps(
                    {
                        "mode": mode,
                        "business_safety": business_safety,
                        "summary": plan.summary,
                        "action_count": len(plan.actions),
                    },
                    ensure_ascii=False,
                ),
            }

            yield {
                "event": "progress",
                "data": json.dumps(
                    {"step": "executing", "message": "正在执行定向分析操作..."},
                    ensure_ascii=False,
                ),
            }

            execution = await execute_directed_plan(
                page,
                plan,
                business_safety=business_safety,
            )

            for skipped in execution.skipped:
                yield {
                    "event": "directed_action_skipped",
                    "data": json.dumps(
                        {
                            "description": skipped.description,
                            "reason": skipped.reason,
                        },
                        ensure_ascii=False,
                    ),
                }

            new_calls: List[CapturedApiCall] = []
            if capture:
                new_calls = capture.drain_new_calls()

            if new_calls:
                session.captured_calls.extend(new_calls)
                yield {
                    "event": "calls_captured",
                    "data": json.dumps(
                        {
                            "mode": mode,
                            "calls": len(new_calls),
                        },
                        ensure_ascii=False,
                    ),
                }

            yield {
                "event": "progress",
                "data": json.dumps(
                    {"step": "generating", "message": "Generating tool definitions via LLM..."},
                    ensure_ascii=False,
                ),
            }

            tools = await self._generate_tools_from_calls(
                session_id,
                new_calls,
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
                        "total_calls": len(new_calls),
                    },
                    ensure_ascii=False,
                ),
            }

        except Exception as exc:
            session.status = "idle"
            session.updated_at = datetime.now()
            logger.error("[ApiMonitor] Directed analysis failed for session %s: %s", session_id, exc, exc_info=True)
            yield {
                "event": "analysis_error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }
```

- [ ] **Step 5: Run backend tests and fix import issues**

Run:

```bash
cd RpaClaw/backend
python -m pytest tests/test_api_monitor_analysis_modes.py -q
```

Expected: PASS. If the typing import line conflicts with existing imports, consolidate it and rerun the same command until it passes.

- [ ] **Step 6: Run existing API Monitor backend tests**

Run:

```bash
cd RpaClaw/backend
python -m pytest tests/test_api_monitor_capture.py tests/test_api_monitor_publish_mcp.py tests/test_api_monitor_mcp_contract.py tests/test_api_monitor_token_flow.py -q
```

Expected: PASS. These tests protect capture, publish, contract parsing, and token-flow behavior from the mode changes.

- [ ] **Step 7: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py
git commit -m "feat: run directed API Monitor analysis"
```

## Task 5: Frontend API Payload And Mode Utility

**Files:**
- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts`
- Create: `RpaClaw/frontend/src/api/apiMonitor.test.ts`
- Create: `RpaClaw/frontend/src/utils/apiMonitorAnalysisModes.ts`
- Create: `RpaClaw/frontend/src/utils/apiMonitorAnalysisModes.test.ts`

- [ ] **Step 1: Add failing API helper test**

Create `RpaClaw/frontend/src/api/apiMonitor.test.ts`:

```ts
import { describe, expect, it, vi, beforeEach } from 'vitest';

const createSSEConnection = vi.fn();

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  createSSEConnection: (...args: unknown[]) => createSSEConnection(...args),
}));

describe('apiMonitor analyzeSession', () => {
  beforeEach(() => {
    createSSEConnection.mockReset();
    createSSEConnection.mockResolvedValue(vi.fn());
  });

  it('sends free mode payload by default', async () => {
    const { analyzeSession } = await import('./apiMonitor');

    analyzeSession('session-1', vi.fn());
    await Promise.resolve();

    expect(createSSEConnection).toHaveBeenCalledWith(
      '/api-monitor/session/session-1/analyze',
      { method: 'POST', body: { mode: 'free', instruction: '' } },
      expect.any(Object),
    );
  });

  it('sends selected analysis mode and instruction', async () => {
    const { analyzeSession } = await import('./apiMonitor');

    analyzeSession('session-1', vi.fn(), {
      mode: 'safe_directed',
      instruction: '搜索订单 123',
    });
    await Promise.resolve();

    expect(createSSEConnection).toHaveBeenCalledWith(
      '/api-monitor/session/session-1/analyze',
      { method: 'POST', body: { mode: 'safe_directed', instruction: '搜索订单 123' } },
      expect.any(Object),
    );
  });
});
```

- [ ] **Step 2: Add failing mode utility test**

Create `RpaClaw/frontend/src/utils/apiMonitorAnalysisModes.test.ts`:

```ts
import { describe, expect, it } from 'vitest';

import {
  ANALYSIS_MODES,
  canStartAnalysis,
  getAnalysisMode,
  modeRequiresInstruction,
} from './apiMonitorAnalysisModes';

describe('apiMonitorAnalysisModes', () => {
  it('defines the initial modes in dropdown order', () => {
    expect(ANALYSIS_MODES.map((mode) => mode.key)).toEqual(['free', 'safe_directed', 'directed']);
  });

  it('marks only directed modes as requiring instruction', () => {
    expect(modeRequiresInstruction('free')).toBe(false);
    expect(modeRequiresInstruction('safe_directed')).toBe(true);
    expect(modeRequiresInstruction('directed')).toBe(true);
  });

  it('falls back to free mode for unknown mode keys', () => {
    expect(getAnalysisMode('future_mode').key).toBe('free');
  });

  it('allows free analysis without instruction', () => {
    expect(canStartAnalysis({ hasSession: true, isAnalyzing: false, mode: 'free', instruction: '' })).toBe(true);
  });

  it('requires instruction for directed modes', () => {
    expect(canStartAnalysis({ hasSession: true, isAnalyzing: false, mode: 'directed', instruction: '   ' })).toBe(false);
    expect(canStartAnalysis({ hasSession: true, isAnalyzing: false, mode: 'directed', instruction: '删除测试订单' })).toBe(true);
  });
});
```

- [ ] **Step 3: Run frontend tests and verify they fail**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/api/apiMonitor.test.ts src/utils/apiMonitorAnalysisModes.test.ts
```

Expected: FAIL because payload support and utility file do not exist.

- [ ] **Step 4: Implement mode utility**

Create `RpaClaw/frontend/src/utils/apiMonitorAnalysisModes.ts`:

```ts
export type AnalysisModeKey = 'free' | 'safe_directed' | 'directed';
export type AnalysisRiskLevel = 'low' | 'guarded' | 'user_controlled';

export interface AnalysisModeOption {
  key: AnalysisModeKey;
  label: string;
  description: string;
  requiresInstruction: boolean;
  riskLevel: AnalysisRiskLevel;
}

export const ANALYSIS_MODES: AnalysisModeOption[] = [
  {
    key: 'free',
    label: '自由分析',
    description: '自动扫描并探测页面上的安全交互元素。',
    requiresInstruction: false,
    riskLevel: 'low',
  },
  {
    key: 'safe_directed',
    label: '安全分析',
    description: '根据你的目标执行安全操作，跳过高风险业务动作。',
    requiresInstruction: true,
    riskLevel: 'guarded',
  },
  {
    key: 'directed',
    label: '定向分析',
    description: '根据你的目标执行操作，业务风险由你自行把控。',
    requiresInstruction: true,
    riskLevel: 'user_controlled',
  },
];

export function getAnalysisMode(modeKey: string): AnalysisModeOption {
  return ANALYSIS_MODES.find((mode) => mode.key === modeKey) || ANALYSIS_MODES[0];
}

export function modeRequiresInstruction(modeKey: string): boolean {
  return getAnalysisMode(modeKey).requiresInstruction;
}

export function canStartAnalysis(input: {
  hasSession: boolean;
  isAnalyzing: boolean;
  mode: string;
  instruction: string;
}): boolean {
  if (!input.hasSession || input.isAnalyzing) return false;
  if (!modeRequiresInstruction(input.mode)) return true;
  return input.instruction.trim().length > 0;
}
```

- [ ] **Step 5: Update `analyzeSession` API helper**

Modify `RpaClaw/frontend/src/api/apiMonitor.ts` near `AnalyzeEvent`:

```ts
export type AnalysisModeKey = 'free' | 'safe_directed' | 'directed'

export interface AnalyzeSessionPayload {
  mode?: AnalysisModeKey | string
  instruction?: string
}
```

Replace the `analyzeSession` signature and `createSSEConnection` call with:

```ts
export function analyzeSession(
  sessionId: string,
  onMessage: (evt: AnalyzeEvent) => void,
  payload: AnalyzeSessionPayload = {},
): () => void {
  let cleanup: (() => void) | null = null
  const body = {
    mode: payload.mode || 'free',
    instruction: payload.instruction || '',
  }

  createSSEConnection<unknown>(
    `/api-monitor/session/${sessionId}/analyze`,
    { method: 'POST', body },
    {
      onMessage({ event, data }) {
        onMessage({ event, data })
      },
    },
  ).then((fn) => {
    cleanup = fn
  })

  return () => {
    cleanup?.()
  }
}
```

- [ ] **Step 6: Run frontend utility/API tests and verify they pass**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/api/apiMonitor.test.ts src/utils/apiMonitorAnalysisModes.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add RpaClaw/frontend/src/api/apiMonitor.ts RpaClaw/frontend/src/api/apiMonitor.test.ts RpaClaw/frontend/src/utils/apiMonitorAnalysisModes.ts RpaClaw/frontend/src/utils/apiMonitorAnalysisModes.test.ts
git commit -m "feat: add frontend API Monitor analysis modes"
```

## Task 6: ApiMonitorPage Dropdown And Instruction Input

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
- Create: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.analysis.test.ts`

- [ ] **Step 1: Add failing page behavior test**

Create `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.analysis.test.ts`:

```ts
// @vitest-environment jsdom

import { createApp, nextTick } from 'vue';
import { createI18n } from 'vue-i18n';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import zh from '../../locales/zh';
import en from '../../locales/en';

const analyzeSession = vi.fn();
const startSession = vi.fn();
const listTools = vi.fn();
const publishMcpToolBundle = vi.fn();
const listCredentials = vi.fn();
const getAuthProfile = vi.fn();
const getTokenFlowProfile = vi.fn();
const showSuccessToast = vi.fn();
const showErrorToast = vi.fn();

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock('@/api/apiMonitor', () => ({
  startSession: (...args: unknown[]) => startSession(...args),
  stopSession: vi.fn(),
  analyzeSession: (...args: unknown[]) => analyzeSession(...args),
  startRecording: vi.fn(),
  stopRecording: vi.fn(),
  listTools: (...args: unknown[]) => listTools(...args),
  updateTool: vi.fn(),
  deleteTool: vi.fn(),
  publishMcpToolBundle: (...args: unknown[]) => publishMcpToolBundle(...args),
  updateToolSelection: vi.fn(),
  getAuthProfile: (...args: unknown[]) => getAuthProfile(...args),
  getTokenFlowProfile: (...args: unknown[]) => getTokenFlowProfile(...args),
}));

vi.mock('@/api/credential', () => ({
  listCredentials: (...args: unknown[]) => listCredentials(...args),
}));

vi.mock('@/utils/sandbox', () => ({
  getBackendWsUrl: () => 'ws://localhost/ws',
}));

vi.mock('@/utils/toast', () => ({
  showSuccessToast: (...args: unknown[]) => showSuccessToast(...args),
  showErrorToast: (...args: unknown[]) => showErrorToast(...args),
}));

class FakeWebSocket {
  static OPEN = 1;
  readyState = 1;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  constructor(public url: string) {
    setTimeout(() => this.onopen?.(new Event('open')), 0);
  }
  send() {}
  close() {
    this.readyState = 3;
  }
}

async function flushAsyncUpdates() {
  await Promise.resolve();
  await Promise.resolve();
  await nextTick();
}

async function mountPage() {
  const { default: ApiMonitorPage } = await import('./ApiMonitorPage.vue');
  const root = document.createElement('div');
  document.body.appendChild(root);
  const app = createApp(ApiMonitorPage);
  app.use(createI18n({
    legacy: false,
    locale: 'zh',
    fallbackLocale: 'en',
    messages: { zh, en },
  }));
  app.mount(root);
  await flushAsyncUpdates();
  return { app, root };
}

describe('ApiMonitorPage analysis modes', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      drawImage: vi.fn(),
    })) as unknown as HTMLCanvasElement['getContext'];
    startSession.mockResolvedValue({
      id: 'session-1',
      user_id: 'user-1',
      sandbox_session_id: 'sandbox-1',
      status: 'idle',
      target_url: 'https://example.test',
      captured_calls: [],
      tool_definitions: [],
      created_at: '2026-04-28T00:00:00Z',
      updated_at: '2026-04-28T00:00:00Z',
    });
    listTools.mockResolvedValue([]);
    analyzeSession.mockReturnValue(vi.fn());
    listCredentials.mockResolvedValue([]);
    getAuthProfile.mockResolvedValue({
      header_count: 0,
      sensitive_header_count: 0,
      headers: [],
      recommended_credential_type: 'placeholder',
    });
    getTokenFlowProfile.mockResolvedValue({ flow_count: 0, flows: [] });
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders analysis dropdown modes from the mode config', async () => {
    const { app, root } = await mountPage();

    const trigger = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.includes('自由分析'));
    expect(trigger).toBeTruthy();
    trigger?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await nextTick();

    expect(root.textContent || '').toContain('自由分析');
    expect(root.textContent || '').toContain('安全分析');
    expect(root.textContent || '').toContain('定向分析');

    app.unmount();
  });

  it('requires instruction for safe analysis and sends selected mode payload', async () => {
    const { app, root } = await mountPage();

    const urlInput = root.querySelector('input[placeholder="输入 URL 进行监控..."]') as HTMLInputElement;
    urlInput.value = 'https://example.test';
    urlInput.dispatchEvent(new Event('input', { bubbles: true }));
    const goButton = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Go');
    goButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await flushAsyncUpdates();

    const dropdownTrigger = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.includes('自由分析'));
    dropdownTrigger?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await nextTick();

    const safeButton = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.includes('安全分析'));
    safeButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await nextTick();

    const analyzeButton = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.trim() === '分析');
    expect(analyzeButton?.getAttribute('disabled')).not.toBeNull();

    const instructionInput = root.querySelector('input[placeholder="描述希望分析的操作流程..."]') as HTMLInputElement;
    instructionInput.value = '搜索订单 123';
    instructionInput.dispatchEvent(new Event('input', { bubbles: true }));
    await nextTick();

    expect(analyzeButton?.getAttribute('disabled')).toBeNull();
    analyzeButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await nextTick();

    expect(analyzeSession).toHaveBeenCalledWith(
      'session-1',
      expect.any(Function),
      { mode: 'safe_directed', instruction: '搜索订单 123' },
    );

    app.unmount();
  });
});
```

- [ ] **Step 2: Run the page test and verify it fails**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/pages/rpa/ApiMonitorPage.analysis.test.ts
```

Expected: FAIL because the page still has a single analyze button and no instruction input.

- [ ] **Step 3: Update imports and state in `ApiMonitorPage.vue`**

Add the toast import:

```ts
import { showErrorToast, showSuccessToast } from '@/utils/toast';
```

Add mode utility imports:

```ts
import {
  ANALYSIS_MODES,
  canStartAnalysis,
  getAnalysisMode,
  modeRequiresInstruction,
  type AnalysisModeKey,
} from '@/utils/apiMonitorAnalysisModes';
```

Add state near other analysis state:

```ts
const analysisModes = ANALYSIS_MODES;
const analysisMode = ref<AnalysisModeKey>('free');
const analysisInstruction = ref('');
const analysisMenuOpen = ref(false);
const selectedAnalysisMode = computed(() => getAnalysisMode(analysisMode.value));
const showAnalysisInstruction = computed(() => modeRequiresInstruction(analysisMode.value));
const canRunAnalysis = computed(() => canStartAnalysis({
  hasSession: Boolean(sessionId.value),
  isAnalyzing: isAnalyzing.value,
  mode: analysisMode.value,
  instruction: analysisInstruction.value,
}));

const selectAnalysisMode = (mode: AnalysisModeKey) => {
  analysisMode.value = mode;
  analysisMenuOpen.value = false;
};
```

- [ ] **Step 4: Update `startAnalysis` payload and event logging**

In `startAnalysis`, replace the early guard and call:

```ts
const startAnalysis = async () => {
  if (!canRunAnalysis.value) return;
  isAnalyzing.value = true;
  const mode = selectedAnalysisMode.value;
  const instruction = showAnalysisInstruction.value ? analysisInstruction.value.trim() : '';
  addLog('INFO', `开始${mode.label}...`);

  const cleanup = analyzeSession(sessionId.value, (evt) => {
```

with:

```ts
  }, {
    mode: analysisMode.value,
    instruction,
  });
```

Keep the existing SSE switch, and add these cases inside the switch:

```ts
      case 'directed_plan_ready':
        addLog('ANALYZE', `操作计划已生成: ${data.action_count || 0} 个动作`);
        break;
      case 'directed_action_skipped':
        addLog('ANALYZE', `已跳过动作: ${data.description || ''}${data.reason ? `（${data.reason}）` : ''}`);
        break;
```

Update `analysis_started` log to tolerate mode:

```ts
        addLog('INFO', `正在分析: ${data.url || ''}${data.mode ? ` [${data.mode}]` : ''}`);
```

- [ ] **Step 5: Replace analyze button markup with dropdown and instruction input**

Replace the existing analyze button block with this button group:

```vue
<div class="relative">
  <div class="inline-flex overflow-hidden rounded-full border border-white/15 bg-white/10 shadow-inner backdrop-blur">
    <button
      @click="startAnalysis"
      :disabled="!canRunAnalysis"
      class="inline-flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/20 disabled:opacity-50"
    >
      <BarChart2 :size="16" />
      分析
    </button>
    <button
      type="button"
      class="inline-flex items-center gap-1 border-l border-white/15 px-3 py-2 text-sm font-semibold text-white transition hover:bg-white/20 disabled:opacity-50"
      :disabled="!sessionId || isAnalyzing"
      @click="analysisMenuOpen = !analysisMenuOpen"
    >
      {{ selectedAnalysisMode.label }}
      <ChevronDown :size="14" />
    </button>
  </div>
  <div
    v-if="analysisMenuOpen"
    class="absolute right-0 z-30 mt-2 w-64 overflow-hidden rounded-2xl border border-slate-200 bg-white py-2 text-left shadow-xl dark:border-white/10 dark:bg-[#17181d]"
  >
    <button
      v-for="mode in analysisModes"
      :key="mode.key"
      type="button"
      class="block w-full px-4 py-3 text-left transition hover:bg-slate-50 dark:hover:bg-white/[0.06]"
      @click="selectAnalysisMode(mode.key)"
    >
      <span class="block text-sm font-bold text-[var(--text-primary)]">{{ mode.label }}</span>
      <span class="mt-1 block text-xs leading-5 text-[var(--text-tertiary)]">{{ mode.description }}</span>
    </button>
  </div>
</div>
```

Add this input after the top button row when instruction is needed:

```vue
<input
  v-if="showAnalysisInstruction"
  v-model="analysisInstruction"
  type="text"
  placeholder="描述希望分析的操作流程..."
  class="w-full rounded-full border border-white/20 bg-slate-950/20 px-4 py-2 text-sm text-white caret-white placeholder:text-white/55 shadow-[inset_0_1px_4px_rgba(0,0,0,0.16)] outline-none backdrop-blur transition focus:border-white/45 focus:bg-slate-950/25 focus:ring-2 focus:ring-white/25 lg:w-[360px]"
  @keyup.enter="startAnalysis"
/>
```

Keep the existing `录制` and `保存为 MCP` buttons beside the analysis dropdown.

- [ ] **Step 6: Run focused frontend tests**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/pages/rpa/ApiMonitorPage.analysis.test.ts src/api/apiMonitor.test.ts src/utils/apiMonitorAnalysisModes.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.analysis.test.ts
git commit -m "feat: add API Monitor analysis dropdown"
```

## Task 7: MCP Publish Toast Feedback

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.analysis.test.ts`

- [ ] **Step 1: Add failing publish feedback tests**

Append these tests to `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.analysis.test.ts`:

```ts
  it('shows toast instead of monitor log when MCP publish succeeds', async () => {
    const { app, root } = await mountPage();
    startSession.mockResolvedValueOnce({
      id: 'session-1',
      user_id: 'user-1',
      sandbox_session_id: 'sandbox-1',
      status: 'idle',
      target_url: 'https://example.test',
      captured_calls: [],
      tool_definitions: [
        {
          id: 'tool-1',
          session_id: 'session-1',
          name: 'search_orders',
          description: 'Search orders',
          method: 'GET',
          url_pattern: '/api/orders',
          yaml_definition: 'name: search_orders',
          source_calls: [],
          source: 'auto',
          confidence: 'high',
          score: 90,
          selected: true,
          confidence_reasons: [],
          source_evidence: {},
          created_at: '2026-04-28T00:00:00Z',
          updated_at: '2026-04-28T00:00:00Z',
        },
      ],
      created_at: '2026-04-28T00:00:00Z',
      updated_at: '2026-04-28T00:00:00Z',
    });
    listTools.mockResolvedValueOnce([
      {
        id: 'tool-1',
        session_id: 'session-1',
        name: 'search_orders',
        description: 'Search orders',
        method: 'GET',
        url_pattern: '/api/orders',
        yaml_definition: 'name: search_orders',
        source_calls: [],
        source: 'auto',
        confidence: 'high',
        score: 90,
        selected: true,
        confidence_reasons: [],
        source_evidence: {},
        created_at: '2026-04-28T00:00:00Z',
        updated_at: '2026-04-28T00:00:00Z',
      },
    ]);
    publishMcpToolBundle.mockResolvedValue({ saved: true, server_id: 'mcp-1', tool_count: 1, overwritten: false });

    const urlInput = root.querySelector('input[placeholder="输入 URL 进行监控..."]') as HTMLInputElement;
    urlInput.value = 'https://example.test';
    urlInput.dispatchEvent(new Event('input', { bubbles: true }));
    const goButton = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Go');
    goButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await flushAsyncUpdates();

    const publishButton = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.includes('保存为 MCP'));
    publishButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await flushAsyncUpdates();

    const nameInput = Array.from(root.querySelectorAll('input')).find((input) => input.value.includes('API MCP')) as HTMLInputElement;
    nameInput.value = 'Example API MCP';
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    const saveButton = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.trim() === '保存');
    saveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await flushAsyncUpdates();

    expect(showSuccessToast).toHaveBeenCalledWith('已保存 MCP "Example API MCP"，包含 1 个工具');
    expect(root.textContent || '').not.toContain('已保存 MCP "Example API MCP"，包含 1 个工具');

    app.unmount();
  });

  it('shows error toast instead of monitor log when MCP publish fails', async () => {
    const { app, root } = await mountPage();
    startSession.mockResolvedValueOnce({
      id: 'session-1',
      user_id: 'user-1',
      sandbox_session_id: 'sandbox-1',
      status: 'idle',
      target_url: 'https://example.test',
      captured_calls: [],
      tool_definitions: [
        {
          id: 'tool-1',
          session_id: 'session-1',
          name: 'search_orders',
          description: 'Search orders',
          method: 'GET',
          url_pattern: '/api/orders',
          yaml_definition: 'name: search_orders',
          source_calls: [],
          source: 'auto',
          confidence: 'high',
          score: 90,
          selected: true,
          confidence_reasons: [],
          source_evidence: {},
          created_at: '2026-04-28T00:00:00Z',
          updated_at: '2026-04-28T00:00:00Z',
        },
      ],
      created_at: '2026-04-28T00:00:00Z',
      updated_at: '2026-04-28T00:00:00Z',
    });
    listTools.mockResolvedValueOnce([
      {
        id: 'tool-1',
        session_id: 'session-1',
        name: 'search_orders',
        description: 'Search orders',
        method: 'GET',
        url_pattern: '/api/orders',
        yaml_definition: 'name: search_orders',
        source_calls: [],
        source: 'auto',
        confidence: 'high',
        score: 90,
        selected: true,
        confidence_reasons: [],
        source_evidence: {},
        created_at: '2026-04-28T00:00:00Z',
        updated_at: '2026-04-28T00:00:00Z',
      },
    ]);
    publishMcpToolBundle.mockRejectedValue(new Error('保存失败'));

    const urlInput = root.querySelector('input[placeholder="输入 URL 进行监控..."]') as HTMLInputElement;
    urlInput.value = 'https://example.test';
    urlInput.dispatchEvent(new Event('input', { bubbles: true }));
    const goButton = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Go');
    goButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await flushAsyncUpdates();

    const publishButton = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.includes('保存为 MCP'));
    publishButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await flushAsyncUpdates();

    const saveButton = Array.from(root.querySelectorAll('button')).find((button) => button.textContent?.trim() === '保存');
    saveButton?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await flushAsyncUpdates();

    expect(showErrorToast).toHaveBeenCalledWith('保存 MCP 失败: 保存失败');
    expect(root.textContent || '').not.toContain('保存 MCP 失败: 保存失败');

    app.unmount();
  });
```

- [ ] **Step 2: Run the publish feedback tests and verify they fail**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/pages/rpa/ApiMonitorPage.analysis.test.ts
```

Expected: FAIL because `submitPublish` still writes final publish success/failure to monitor logs.

- [ ] **Step 3: Update `submitPublish` success/failure feedback**

In `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`, keep the initial process log:

```ts
addLog('INFO', '正在发布 MCP 工具...');
```

Replace the success log:

```ts
addLog('INFO', `已保存 MCP "${publishForm.mcpName}"，包含 ${result.tool_count} 个工具`);
```

with:

```ts
showSuccessToast(`已保存 MCP "${publishForm.mcpName}"，包含 ${result.tool_count} 个工具`);
```

Replace the ordinary failure log:

```ts
addLog('ERROR', `保存 MCP 失败: ${err.message}`);
```

with:

```ts
showErrorToast(`保存 MCP 失败: ${err.message}`);
```

Keep the 409 conflict branch as:

```ts
if (err?.response?.status === 409 && err?.response?.data?.needs_confirmation) {
  overwriteDialogOpen.value = true;
  addLog('INFO', '发现已存在的 MCP。等待覆盖确认。');
  return;
}
```

- [ ] **Step 4: Run page tests and verify they pass**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/pages/rpa/ApiMonitorPage.analysis.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.analysis.test.ts
git commit -m "fix: show API Monitor MCP publish toast"
```

## Task 8: Final Verification

**Files:**
- Verify only; no expected source edits.

- [ ] **Step 1: Run focused backend suite**

Run:

```bash
cd RpaClaw/backend
python -m pytest tests/test_api_monitor_analysis_modes.py tests/test_api_monitor_capture.py tests/test_api_monitor_publish_mcp.py tests/test_api_monitor_mcp_contract.py tests/test_api_monitor_token_flow.py -q
```

Expected: all selected backend tests PASS.

- [ ] **Step 2: Run focused frontend suite**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/api/apiMonitor.test.ts src/utils/apiMonitorAnalysisModes.test.ts src/pages/rpa/ApiMonitorPage.analysis.test.ts
```

Expected: all selected frontend tests PASS.

- [ ] **Step 3: Run frontend type-check**

Run:

```bash
cd RpaClaw/frontend
npm run type-check
```

Expected: PASS. If this fails because of unrelated pre-existing errors outside touched files, capture the exact errors and run the focused tests above again before reporting residual risk.

- [ ] **Step 4: Inspect git status**

Run:

```bash
git status --short
```

Expected: only intentional modified/new files are present. Pre-existing untracked `.rpaclaw/` and root `package-lock.json` may still appear and should not be staged unless the user explicitly asks.

- [ ] **Step 5: Final commit if verification changed files**

If any verification-related edits were needed, commit them:

```bash
git add RpaClaw/backend/rpa/api_monitor/analysis_modes.py RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/route/api_monitor.py RpaClaw/backend/rpa/api_monitor/directed_analyzer.py RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_analysis_modes.py RpaClaw/frontend/src/api/apiMonitor.ts RpaClaw/frontend/src/api/apiMonitor.test.ts RpaClaw/frontend/src/utils/apiMonitorAnalysisModes.ts RpaClaw/frontend/src/utils/apiMonitorAnalysisModes.test.ts RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.analysis.test.ts
git commit -m "test: verify API Monitor analysis modes"
```

Expected: commit succeeds only if there are staged verification edits. If there are no edits, skip this commit.
