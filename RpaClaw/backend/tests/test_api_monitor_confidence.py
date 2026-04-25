from datetime import datetime

from backend.rpa.api_monitor.confidence import (
    classify_api_candidate,
    dedup_key_for_tool,
    score_api_candidate,
)
from backend.rpa.api_monitor.models import ApiMonitorSession, CapturedApiCall, CapturedRequest, CapturedResponse


def _call(
    url: str,
    *,
    action_window_matched: bool = True,
    initiator_urls: list[str] | None = None,
    js_stack_urls: list[str] | None = None,
    body: str = '{"items":[]}',
) -> CapturedApiCall:
    return CapturedApiCall(
        request=CapturedRequest(
            request_id="req_1",
            url=url,
            method="GET",
            headers={"accept": "application/json"},
            timestamp=datetime(2026, 1, 1),
            resource_type="fetch",
        ),
        response=CapturedResponse(
            status=200,
            status_text="OK",
            headers={"content-type": "application/json"},
            body=body,
            content_type="application/json",
            timestamp=datetime(2026, 1, 1),
        ),
        source_evidence={
            "initiator_type": "script",
            "initiator_urls": initiator_urls or [],
            "js_stack_urls": js_stack_urls or [],
            "frame_url": "https://example.com/app",
            "action_window_matched": action_window_matched,
        },
    )


def test_business_api_with_page_script_is_high_and_selected():
    result = score_api_candidate([
        _call(
            "https://example.com/api/orders",
            initiator_urls=["https://example.com/app/assets/main.js"],
        )
    ])

    assert result.confidence == "high"
    assert result.score == 100
    assert result.breakdown["response_richness"] == 10
    assert result.selected is True
    assert "由用户动作触发" in result.reasons
    assert "由页面业务脚本发起" in result.reasons


def test_config_query_from_injected_stack_is_low_and_not_selected():
    result = score_api_candidate([
        _call(
            "https://example.com/hicweb/services/hic.config.queryConfig?class_code=his.evaluation.modelAlias",
            initiator_urls=["chrome-extension://abc/injected.js"],
            js_stack_urls=["chrome-extension://abc/injected.js"],
        )
    ])

    assert result.confidence == "low"
    assert result.score == 5
    assert result.breakdown["injected_source"] == -40
    assert result.breakdown["noise_path"] == -30
    assert result.selected is False
    assert "路径疑似配置或后台请求" in result.reasons
    assert "来源疑似注入脚本或扩展" in result.reasons


def test_missing_source_evidence_still_scores_from_other_strong_signals():
    result = score_api_candidate([
        _call("https://example.com/api/orders", initiator_urls=[], js_stack_urls=[])
    ])

    assert result.confidence == "high"
    assert result.score == 85
    assert result.selected is True
    assert "缺少 initiator 或 JS 调用栈" in result.reasons


def test_legacy_classify_api_candidate_returns_scored_result():
    result = classify_api_candidate([
        _call("https://example.com/api/orders", initiator_urls=["https://example.com/app.js"])
    ])

    assert result.confidence == "high"
    assert result.score == 100


def test_dedup_key_uses_method_and_parameterized_path_only():
    assert dedup_key_for_tool("GET", "/api/users/123?fields=name") == "GET /api/users/{id}"
    assert dedup_key_for_tool("GET", "/api/search?q=foo&page=2") == "GET /api/search"


def test_apply_confidence_to_tool_definition():
    from backend.rpa.api_monitor.manager import _apply_confidence_to_tool
    from backend.rpa.api_monitor.models import ApiToolDefinition

    tool = ApiToolDefinition(
        session_id="session_1",
        name="list_orders",
        description="List orders",
        method="GET",
        url_pattern="/api/orders",
        yaml_definition="name: list_orders\nmethod: GET\nurl: /api/orders\n",
        source_calls=["call_1"],
    )

    call = _call(
        "https://example.com/api/orders",
        initiator_urls=["https://example.com/app/assets/main.js"],
    )
    updated = _apply_confidence_to_tool(tool, [call])

    assert updated.confidence == "high"
    assert updated.score == 100
    assert updated.selected is True
    assert updated.confidence_reasons
    assert updated.source_evidence["action_window_matched"] is True
    assert updated.source_evidence["breakdown"]["response_richness"] == 10


def test_dedup_session_tools_keeps_highest_score_and_strips_query():
    from backend.rpa.api_monitor.manager import ApiMonitorSessionManager
    from backend.rpa.api_monitor.models import ApiToolDefinition

    manager = ApiMonitorSessionManager()
    session = ApiMonitorSession(
        id="session_1",
        user_id="user_1",
        sandbox_session_id="sandbox_1",
        tool_definitions=[
            ApiToolDefinition(
                id="existing_low",
                session_id="session_1",
                name="search_low",
                description="Low scoring search",
                method="GET",
                url_pattern="/api/search?q={query}",
                yaml_definition="name: search_low",
                score=40,
            )
        ],
    )
    manager.sessions[session.id] = session

    new_tools = [
        ApiToolDefinition(
            id="new_high",
            session_id="session_1",
            name="search_high",
            description="High scoring search",
            method="GET",
            url_pattern="/api/search?page={page}",
            yaml_definition="name: search_high",
            score=80,
        ),
        ApiToolDefinition(
            id="new_other",
            session_id="session_1",
            name="list_orders",
            description="List orders",
            method="GET",
            url_pattern="/api/orders",
            yaml_definition="name: list_orders",
            score=75,
        ),
    ]

    manager._dedup_session_tools(session.id, new_tools)

    assert [tool.id for tool in session.tool_definitions] == ["new_high", "new_other"]
    assert [tool.id for tool in new_tools] == ["new_high", "new_other"]
