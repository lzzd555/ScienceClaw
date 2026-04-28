import pytest

from backend.rpa.api_monitor.analysis_modes import (
    ANALYSIS_MODE_REGISTRY,
    get_analysis_mode_config,
)
from backend.rpa.api_monitor.models import AnalyzeSessionRequest, ApiMonitorSession


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


import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from backend.route import api_monitor as api_monitor_route


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
    assert "analysis_complete" in response.text
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
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "analyze_directed_page", fake_analyze_directed_page, raising=False)
    monkeypatch.setattr(api_monitor_route, "_resolve_user_model_config", fake_resolve_user_model_config)

    response = TestClient(_route_app()).post(
        "/api/v1/api-monitor/session/session-1/analyze",
        json={"mode": "safe_directed", "instruction": "搜索订单 123"},
    )

    assert response.status_code == 200
    assert "analysis_complete" in response.text
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
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "analyze_directed_page", fake_analyze_directed_page, raising=False)
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


from backend.rpa.api_monitor.directed_analyzer import (
    DirectedAction,
    DirectedAnalysisPlan,
    DirectedExecutionResult,
    DirectedStepDecision,
    build_locator,
    filter_action_for_business_safety,
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


from datetime import datetime

from backend.rpa.api_monitor.manager import ApiMonitorSessionManager
from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest


class _FakeCapture:
    """Two-stage capture: pre_calls drained first, post_calls drained after execution."""
    def __init__(self, pre_calls=None, post_calls=None):
        self._pre_calls = list(pre_calls or [])
        self._post_calls = list(post_calls or [])
        self._drain_count = 0

    def drain_new_calls(self):
        self._drain_count += 1
        if self._drain_count == 1:
            calls = list(self._pre_calls)
            self._pre_calls = []
            return calls
        calls = list(self._post_calls)
        self._post_calls = []
        return calls


class _SequencedCapture:
    def __init__(self, batches):
        self._batches = [list(batch) for batch in batches]
        self._drain_count = 0

    def drain_new_calls(self):
        self._drain_count += 1
        if not self._batches:
            return []
        return self._batches.pop(0)


class _FakeDirectedPage:
    url = "https://example.test/orders"
    main_frame = object()

    async def title(self):
        return "Orders"

    async def wait_for_timeout(self, _timeout):
        return None


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
    # pre_calls: historical calls that should be drained before execution
    # post_calls: new calls captured during directed execution
    manager._captures[session.id] = _FakeCapture(pre_calls=[], post_calls=[_captured_call()])

    calls: dict[str, object] = {}

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
        return DirectedStepDecision(goal_status="done", summary="完成", done_reason="无需额外动作")

    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        calls["tool_source"] = source
        calls["tool_call_count"] = len(calls_arg)
        return []

    monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_build_directed_step_decision)
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
    assert calls["tool_source"] == "auto"
    assert calls["tool_call_count"] == 0
    assert any(event["event"] == "directed_step_planned" for event in events)
    assert any(event["event"] == "analysis_complete" for event in events)


def test_directed_analysis_emits_skipped_actions(monkeypatch):
    manager = ApiMonitorSessionManager()
    session = _route_session()
    manager.sessions[session.id] = session
    manager._pages[session.id] = _FakeDirectedPage()
    manager._captures[session.id] = _FakeCapture(pre_calls=[], post_calls=[])

    async def fake_observe(page, instruction):
        return {
            "url": page.url,
            "title": "Orders",
            "raw_snapshot": {"url": page.url, "title": "Orders"},
            "compact_snapshot": {"url": page.url, "title": "Orders"},
            "dom_digest": "orders",
        }

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

    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        return []

    monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_build_directed_step_decision)
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


def test_directed_analysis_pre_drains_historical_calls(monkeypatch):
    """Pre-existing calls in capture buffer should be drained before execution
    and NOT passed to tool generation."""
    manager = ApiMonitorSessionManager()
    session = _route_session()
    manager.sessions[session.id] = session
    manager._pages[session.id] = _FakeDirectedPage()
    historical = _captured_call()
    manager._captures[session.id] = _FakeCapture(pre_calls=[historical], post_calls=[])

    tool_calls_arg: list = []

    async def fake_observe(page, instruction):
        return {
            "url": page.url,
            "title": "Orders",
            "raw_snapshot": {"url": page.url, "title": "Orders"},
            "compact_snapshot": {"url": page.url, "title": "Orders"},
            "dom_digest": "orders",
        }

    async def fake_build_directed_step_decision(*, instruction, compact_snapshot, run_history, observation, model_config=None):
        return DirectedStepDecision(goal_status="done", summary="完成", done_reason="只验证 pre-drain")

    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        tool_calls_arg.extend(calls_arg)
        return []

    monkeypatch.setattr(manager, "_observe_directed_page", fake_observe)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_step_decision", fake_build_directed_step_decision)
    monkeypatch.setattr(manager, "_generate_tools_from_calls", fake_generate_tools)

    asyncio.run(
        _collect_events(
            manager.analyze_directed_page(
                session.id,
                instruction="搜索订单",
                mode="directed",
                business_safety="user_controlled",
            )
        )
    )

    # Historical call was moved to session.captured_calls, not passed to tool generation
    assert len(session.captured_calls) == 1
    assert session.captured_calls[0].request.request_id == "req-1"
    assert tool_calls_arg == []


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

    assert decision_contexts[1][0]["result"] == "failed"
    assert "Locator not found" in decision_contexts[1][0]["error"]
    assert any(event["event"] == "directed_replan" for event in events)
    assert any(event["event"] == "analysis_complete" for event in events)


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
                instruction="删除订单",
                mode="safe_directed",
                business_safety="guarded",
            )
        )
    )

    assert executed == []
    assert contexts[1][0]["result"] == "skipped"
    assert any(event["event"] == "directed_action_skipped" for event in events)
