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


import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

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


import asyncio
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
    # pre_calls: historical calls that should be drained before execution
    # post_calls: new calls captured during directed execution
    manager._captures[session.id] = _FakeCapture(pre_calls=[], post_calls=[_captured_call()])

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

    async def fake_execute_directed_plan(page, plan, *, business_safety, on_action=None):
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
    manager._captures[session.id] = _FakeCapture(pre_calls=[], post_calls=[])

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

    async def fake_execute_directed_plan(page, plan, *, business_safety, on_action=None):
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

    async def fake_build_page_snapshot(page, frame_path_builder):
        return {"url": page.url, "title": "Orders", "frames": []}

    async def fake_build_directed_plan(*, instruction, compact_snapshot, model_config=None):
        return DirectedAnalysisPlan(summary="搜索", actions=[])

    async def fake_execute_directed_plan(page, plan, *, business_safety, on_action=None):
        return DirectedExecutionResult(executed=[], skipped=[])

    async def fake_generate_tools(session_id, calls_arg, source="auto", model_config=None):
        tool_calls_arg.extend(calls_arg)
        return []

    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_page_snapshot", fake_build_page_snapshot)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.compact_recording_snapshot", lambda snapshot, instruction: snapshot)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.build_directed_plan", fake_build_directed_plan)
    monkeypatch.setattr("backend.rpa.api_monitor.manager.execute_directed_plan", fake_execute_directed_plan)
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
