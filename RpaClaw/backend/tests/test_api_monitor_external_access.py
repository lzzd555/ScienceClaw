from __future__ import annotations

from datetime import datetime

import pytest

from backend.rpa.api_monitor_external_access import (
    CALLER_AUTH_EXTENSION_KEY,
    CallerAuthError,
    build_caller_auth_requirements,
    build_external_mcp_url,
    build_external_tool_input_schema,
    extract_caller_auth_profile,
    serialize_external_access_state,
    with_caller_auth_description,
)


def test_placeholder_requires_no_caller_auth_and_does_not_change_schema():
    requirements = build_caller_auth_requirements({"credential_type": "placeholder"})
    assert requirements == {
        "required": False,
        "credential_type": "placeholder",
        "accepted_fields": [],
        "notes": ["No caller target API credential is required or injected for this tool."],
    }

    schema = {
        "type": "object",
        "properties": {"keyword": {"type": "string"}},
        "required": ["keyword"],
    }
    external_schema = build_external_tool_input_schema(schema, requirements)

    assert external_schema == schema
    assert external_schema is not schema


def test_test_credential_requires_authorization_auth_schema():
    requirements = build_caller_auth_requirements({"credential_type": "test"})
    schema = {
        "type": "object",
        "properties": {"keyword": {"type": "string"}},
        "required": ["keyword"],
    }

    external_schema = build_external_tool_input_schema(schema, requirements)

    assert external_schema["required"] == ["keyword", "_auth"]
    assert external_schema["properties"]["_auth"]["required"] == ["headers"]
    assert external_schema["properties"]["_auth"]["properties"]["headers"]["required"] == ["Authorization"]
    assert (
        external_schema["properties"]["_auth"]["properties"]["headers"]["properties"]["Authorization"]["description"]
        == "Full Authorization header value, for example: Bearer <token>."
    )


def test_schema_rejects_business_auth_field_for_test_credential():
    requirements = build_caller_auth_requirements({"credential_type": "test"})
    schema = {"type": "object", "properties": {"_auth": {"type": "string"}}}

    with pytest.raises(CallerAuthError, match="_auth is reserved"):
        build_external_tool_input_schema(schema, requirements)


def test_description_contains_machine_extension_and_human_hint():
    requirements = build_caller_auth_requirements({"credential_type": "test"})

    description, extension = with_caller_auth_description("Search orders", requirements)

    assert "credential_type=test" in description
    assert "_auth.headers.Authorization" in description
    assert extension == {
        CALLER_AUTH_EXTENSION_KEY: {
            "required": True,
            "credential_type": "test",
            "accepted_fields": ["_auth.headers.Authorization"],
        }
    }


def test_extract_caller_auth_prefers_arguments_over_target_header():
    requirements = build_caller_auth_requirements({"credential_type": "test"})
    arguments = {
        "keyword": "invoice",
        "_auth": {"headers": {"Authorization": "Bearer from-arguments"}},
    }

    cleaned, profile, preview = extract_caller_auth_profile(
        arguments,
        requirements=requirements,
        request_headers={"X-RpaClaw-Target-Authorization": "Bearer from-header"},
    )

    assert cleaned == {"keyword": "invoice"}
    assert profile.headers == {"Authorization": "Bearer from-arguments"}
    assert profile.variables["auth_token"] == "Bearer from-arguments"
    assert preview == {
        "credential_type": "test",
        "source": "arguments._auth.headers.Authorization",
        "headers": ["Authorization"],
        "injected": True,
    }


def test_extract_caller_auth_can_use_request_target_header():
    requirements = build_caller_auth_requirements({"credential_type": "test"})

    cleaned, profile, preview = extract_caller_auth_profile(
        {"keyword": "invoice"},
        requirements=requirements,
        request_headers={"X-RpaClaw-Target-Authorization": "Bearer from-header"},
    )

    assert cleaned == {"keyword": "invoice"}
    assert profile.headers == {"Authorization": "Bearer from-header"}
    assert profile.variables["auth_token"] == "Bearer from-header"
    assert preview["source"] == "X-RpaClaw-Target-Authorization"


def test_extract_caller_auth_errors_when_test_credential_missing_authorization():
    requirements = build_caller_auth_requirements({"credential_type": "test"})

    with pytest.raises(CallerAuthError, match="Missing caller Authorization"):
        extract_caller_auth_profile({"keyword": "invoice"}, requirements=requirements, request_headers={})


def test_placeholder_ignores_auth_argument():
    requirements = build_caller_auth_requirements({"credential_type": "placeholder"})

    cleaned, profile, preview = extract_caller_auth_profile(
        {"keyword": "invoice", "_auth": {"headers": {"Authorization": "Bearer ignored"}}},
        requirements=requirements,
        request_headers={},
    )

    assert cleaned == {"keyword": "invoice"}
    assert profile.headers == {}
    assert preview == {
        "credential_type": "placeholder",
        "source": "",
        "headers": [],
        "injected": False,
        "ignored_fields": ["_auth"],
    }


def test_build_external_mcp_url_uses_api_v1_prefix():
    assert (
        build_external_mcp_url("http://localhost:12001/api/v1", "mcp_abc123")
        == "http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc123/mcp"
    )


def test_serialize_external_access_state_returns_url_and_no_token_fields():
    state = serialize_external_access_state(
        {
            "external_access": {
                "enabled": True,
                "created_at": datetime(2026, 4, 28, 1, 2, 3),
                "last_used_at": datetime(2026, 4, 28, 3, 4, 5),
            },
            "api_monitor_auth": {"credential_type": "test"},
        },
        external_url="http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc123/mcp",
    )

    assert state["enabled"] is True
    assert state["url"] == "http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc123/mcp"
    assert state["require_caller_credentials"] is True
    assert "access_token" not in state
    assert "access_token_hash" not in state
    assert "token_hint" not in state


# ── IDaaS credential type ──────────────────────────────────────────────


def test_idaas_build_caller_auth_requirements():
    requirements = build_caller_auth_requirements({"credential_type": "idaas"})
    assert requirements == {
        "required": True,
        "credential_type": "idaas",
        "accepted_fields": ["_auth.headers.X-RE-AppId", "_auth.cookie.X-Auth-Token"],
        "notes": ["Provide IDaaS X-RE-AppId header and X-Auth-Token cookie via _auth."],
    }


def test_idaas_external_tool_schema_contains_auth_fields():
    requirements = build_caller_auth_requirements({"credential_type": "idaas"})
    schema = {
        "type": "object",
        "properties": {"keyword": {"type": "string"}},
        "required": ["keyword"],
    }

    external_schema = build_external_tool_input_schema(schema, requirements)

    assert external_schema["required"] == ["keyword", "_auth"]
    auth_props = external_schema["properties"]["_auth"]["properties"]
    assert auth_props["headers"]["required"] == ["X-RE-AppId"]
    assert auth_props["headers"]["properties"]["X-RE-AppId"]["type"] == "string"
    assert auth_props["cookie"]["required"] == ["X-Auth-Token"]
    assert auth_props["cookie"]["properties"]["X-Auth-Token"]["type"] == "string"
    assert external_schema["properties"]["_auth"]["required"] == ["headers", "cookie"]


def test_idaas_extract_caller_auth_profile_from_arguments():
    requirements = build_caller_auth_requirements({"credential_type": "idaas"})
    arguments = {
        "keyword": "invoice",
        "_auth": {
            "headers": {"X-RE-AppId": "my-app-123"},
            "cookie": {"X-Auth-Token": "token-abc"},
        },
    }

    cleaned, profile, preview = extract_caller_auth_profile(
        arguments,
        requirements=requirements,
        request_headers={},
    )

    assert cleaned == {"keyword": "invoice"}
    assert profile.headers["X-RE-AppId"] == "my-app-123"
    assert profile.headers["Cookie"] == "X-Auth-Token=token-abc"
    assert profile.variables["auth_token"] == "token-abc"
    assert preview == {
        "credential_type": "idaas",
        "source": "_auth",
        "headers": ["X-RE-AppId", "Cookie"],
        "injected": True,
    }


def test_idaas_extract_caller_auth_errors_when_missing_fields():
    requirements = build_caller_auth_requirements({"credential_type": "idaas"})

    with pytest.raises(CallerAuthError, match="Missing IDaaS"):
        extract_caller_auth_profile(
            {"keyword": "invoice", "_auth": {"headers": {"X-RE-AppId": "app"}, "cookie": {}}},
            requirements=requirements,
            request_headers={},
        )


def test_idaas_extract_caller_auth_errors_when_missing_app_id():
    requirements = build_caller_auth_requirements({"credential_type": "idaas"})

    with pytest.raises(CallerAuthError, match="Missing IDaaS"):
        extract_caller_auth_profile(
            {"keyword": "invoice", "_auth": {"headers": {}, "cookie": {"X-Auth-Token": "tok"}}},
            requirements=requirements,
            request_headers={},
        )


def test_idaas_description_contains_idaas_hint():
    requirements = build_caller_auth_requirements({"credential_type": "idaas"})

    description, extension = with_caller_auth_description("Search orders", requirements)

    assert "credential_type=idaas" in description
    assert "X-RE-AppId" in description
    assert "X-Auth-Token" in description
    assert extension[CALLER_AUTH_EXTENSION_KEY] == {
        "required": True,
        "credential_type": "idaas",
        "accepted_fields": ["_auth.headers.X-RE-AppId", "_auth.cookie.X-Auth-Token"],
    }
