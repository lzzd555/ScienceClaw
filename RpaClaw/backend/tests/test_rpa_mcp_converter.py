import pytest

from backend.rpa.mcp_models import RpaMcpToolDefinition
from backend.rpa.mcp_converter import RpaMcpConverter


def test_rpa_mcp_tool_definition_defaults():
    tool = RpaMcpToolDefinition(
        id="rpa_mcp_tool_1",
        user_id="user-1",
        name="download_invoice",
        tool_name="rpa_download_invoice",
        description="Download invoice",
        allowed_domains=["example.com"],
        post_auth_start_url="https://example.com/dashboard",
        steps=[],
        params={},
        input_schema={"type": "object", "properties": {}, "required": []},
        sanitize_report={"removed_steps": [], "removed_params": [], "warnings": []},
        source={"type": "rpa_skill", "session_id": "session-1", "skill_name": "invoice_skill"},
    )

    assert tool.enabled is True
    assert tool.requires_cookies is False
    assert tool.output_schema["properties"]["data"]["type"] == "object"
    assert tool.recommended_output_schema["properties"]["data"]["type"] == "object"
    assert tool.output_schema_confirmed is False
    assert tool.output_examples == []
    assert tool.allowed_domains == ["example.com"]
    assert tool.sanitize_report.warnings == []


def test_preview_strips_login_steps_and_sensitive_params():
    converter = RpaMcpConverter()
    steps = [
        {"action": "navigate", "url": "https://example.com/login", "description": "Open login"},
        {"action": "fill", "target": '{"method":"label","value":"Email"}', "value": "alice@example.com", "description": "Fill email"},
        {"action": "fill", "target": '{"method":"label","value":"Password"}', "value": "{{credential}}", "description": "Fill password", "sensitive": True},
        {"action": "click", "target": '{"method":"role","role":"button","name":"Sign in"}', "description": "Sign in"},
        {"action": "navigate", "url": "https://example.com/dashboard", "description": "Open dashboard"},
        {"action": "click", "target": '{"method":"role","role":"button","name":"Export"}', "description": "Export invoice"},
    ]
    params = {
        "email": {"original_value": "alice@example.com"},
        "password": {"original_value": "{{credential}}", "sensitive": True, "credential_id": "cred-1"},
        "month": {"original_value": "2026-03", "description": "Invoice month"},
    }

    preview = converter.preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="invoice_skill",
        name="download_invoice",
        description="Download invoice",
        steps=steps,
        params=params,
    )

    assert preview.post_auth_start_url == "https://example.com/dashboard"
    assert preview.allowed_domains == ["example.com"]
    assert preview.requires_cookies is True
    assert preview.sanitize_report.removed_params == ["email", "password"]
    assert [step["description"] for step in preview.steps] == ["Open dashboard", "Export invoice"]
    assert "cookies" in preview.input_schema["required"]
    assert "password" not in preview.input_schema["properties"]


def test_preview_without_login_does_not_require_cookies_or_warning():
    converter = RpaMcpConverter()
    steps = [
        {"action": "navigate", "url": "https://example.com/workspace", "description": "Open workspace"},
        {"action": "click", "target": '{"method":"role","role":"button","name":"Export"}', "description": "Export invoice"},
    ]

    preview = converter.preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="skill",
        name="workspace_tool",
        description="Workspace tool",
        steps=steps,
        params={"month": {"original_value": "2026-03", "description": "Invoice month"}},
    )

    assert preview.requires_cookies is False
    assert "cookies" not in preview.input_schema["properties"]
    assert "cookies" not in preview.input_schema["required"]
    assert preview.sanitize_report.warnings == []


def test_preview_adds_warning_when_login_range_is_ambiguous():
    converter = RpaMcpConverter()
    steps = [
        {"action": "navigate", "url": "https://example.com/login", "description": "Open login"},
        {"action": "fill", "target": '{"method":"label","value":"Email"}', "value": "alice@example.com", "description": "Fill email"},
        {"action": "navigate", "url": "https://example.com/workspace", "description": "Open workspace"},
    ]

    preview = converter.preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="skill",
        name="workspace_tool",
        description="Workspace tool",
        steps=steps,
        params={},
    )

    assert preview.requires_cookies is False
    assert preview.sanitize_report.warnings


def test_preview_builds_recommended_output_schema_from_recording_signals():
    converter = RpaMcpConverter()
    steps = [
        {"action": "navigate", "url": "https://example.com/workspace", "description": "Open workspace"},
        {
            "action": "extract_text",
            "target": '{"method":"role","role":"heading","name":"Invoice total"}',
            "description": "Capture invoice total",
            "result_key": "invoice_total",
        },
        {
            "action": "click",
            "target": '{"method":"role","role":"button","name":"Download invoice"}',
            "description": "Download invoice",
            "signals": {"download": {"filename": "invoice.pdf"}},
        },
    ]

    preview = converter.preview(
        user_id="user-1",
        session_id="session-1",
        skill_name="skill",
        name="invoice_tool",
        description="Invoice tool",
        steps=steps,
        params={},
    )

    data_schema = preview.recommended_output_schema["properties"]["data"]
    assert data_schema["type"] == "object"
    assert data_schema["properties"]["invoice_total"]["type"] == "string"
    assert preview.recommended_output_schema["properties"]["downloads"]["items"]["properties"]["filename"]["type"] == "string"
    assert "recording_signals" in preview.output_inference_report
    assert any(signal["kind"] == "extract_text" for signal in preview.output_inference_report["recording_signals"])
