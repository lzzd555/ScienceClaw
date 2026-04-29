from __future__ import annotations

import hashlib
import hmac
import secrets
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

from backend.rpa.api_monitor_auth import PLACEHOLDER_CREDENTIAL_TYPE, TEST_CREDENTIAL_TYPE
from backend.rpa.api_monitor_runtime_profile import ApiMonitorRuntimeProfile

CALLER_AUTH_EXTENSION_KEY = "x-rpaclaw-authRequirements"
TARGET_AUTH_HEADER = "X-RpaClaw-Target-Authorization"
EXTERNAL_ACCESS_TOKEN_PREFIX = "rpamcp_"


class CallerAuthError(ValueError):
    pass


def _credential_type(auth_config: Mapping[str, Any] | None) -> str:
    value = str((auth_config or {}).get("credential_type") or "").strip()
    return value or PLACEHOLDER_CREDENTIAL_TYPE


def build_caller_auth_requirements(auth_config: Mapping[str, Any] | None) -> dict[str, Any]:
    credential_type = _credential_type(auth_config)
    if credential_type == TEST_CREDENTIAL_TYPE:
        return {
            "required": True,
            "credential_type": TEST_CREDENTIAL_TYPE,
            "accepted_fields": ["_auth.headers.Authorization"],
            "notes": ["Provide caller-owned target API Authorization header for this call only."],
        }
    return {
        "required": False,
        "credential_type": PLACEHOLDER_CREDENTIAL_TYPE,
        "accepted_fields": [],
        "notes": ["No caller target API credential is required or injected for this tool."],
    }


def _auth_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Caller-provided target API Authorization header for this call only. Values are never stored.",
        "properties": {
            "headers": {
                "type": "object",
                "properties": {
                    "Authorization": {
                        "type": "string",
                        "description": "Full Authorization header value, for example: Bearer <token>.",
                    }
                },
                "required": ["Authorization"],
                "additionalProperties": False,
            }
        },
        "required": ["headers"],
        "additionalProperties": False,
    }


def build_external_tool_input_schema(
    input_schema: Mapping[str, Any] | None,
    requirements: Mapping[str, Any],
) -> dict[str, Any]:
    schema = deepcopy(dict(input_schema or {"type": "object", "properties": {}}))
    schema.setdefault("type", "object")
    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        properties = {}
        schema["properties"] = properties

    if not requirements.get("required"):
        return schema

    if "_auth" in properties:
        raise CallerAuthError("_auth is reserved for external caller credentials")
    properties["_auth"] = _auth_input_schema()
    required = list(schema.get("required") or [])
    if "_auth" not in required:
        required.append("_auth")
    schema["required"] = required
    return schema


def with_caller_auth_description(
    description: str,
    requirements: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    credential_type = str(requirements.get("credential_type") or PLACEHOLDER_CREDENTIAL_TYPE)
    if requirements.get("required"):
        suffix = (
            "Caller auth: this API Monitor MCP is configured with credential_type=test. "
            "Pass caller-owned Authorization in _auth.headers.Authorization for each call."
        )
    else:
        suffix = "Caller auth: credential_type=placeholder, no caller target API credential is injected."
    extension = {
        CALLER_AUTH_EXTENSION_KEY: {
            "required": bool(requirements.get("required")),
            "credential_type": credential_type,
            "accepted_fields": list(requirements.get("accepted_fields") or []),
        }
    }
    base = str(description or "").strip()
    return (f"{base}\n\n{suffix}" if base else suffix), extension


def _header_value(headers: Mapping[str, Any] | None, name: str) -> str:
    expected = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == expected:
            return str(value or "").strip()
    return ""


def extract_caller_auth_profile(
    arguments: Mapping[str, Any],
    *,
    requirements: Mapping[str, Any],
    request_headers: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], ApiMonitorRuntimeProfile, dict[str, Any]]:
    cleaned = dict(arguments or {})
    auth_payload = cleaned.pop("_auth", None)
    profile = ApiMonitorRuntimeProfile()
    credential_type = str(requirements.get("credential_type") or PLACEHOLDER_CREDENTIAL_TYPE)

    if not requirements.get("required"):
        preview = {
            "credential_type": credential_type,
            "source": "",
            "headers": [],
            "injected": False,
        }
        if auth_payload is not None:
            preview["ignored_fields"] = ["_auth"]
        return cleaned, profile, preview

    auth_headers = auth_payload.get("headers") if isinstance(auth_payload, Mapping) else {}
    authorization = _header_value(auth_headers if isinstance(auth_headers, Mapping) else {}, "Authorization")
    source = "arguments._auth.headers.Authorization" if authorization else ""
    if not authorization:
        authorization = _header_value(request_headers, TARGET_AUTH_HEADER)
        source = TARGET_AUTH_HEADER if authorization else ""
    if not authorization:
        raise CallerAuthError("Missing caller Authorization for credential_type=test")

    profile.set_header("Authorization", authorization, secret=True)
    profile.set_variable("auth_token", authorization, secret=True, source=source)
    return cleaned, profile, {
        "credential_type": credential_type,
        "source": source,
        "headers": ["Authorization"],
        "injected": True,
    }


def generate_external_access_token() -> str:
    return EXTERNAL_ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_external_access_token(token: str) -> str:
    digest = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_external_access_token(token: str, token_hash: str) -> bool:
    if not token or not token_hash:
        return False
    return hmac.compare_digest(hash_external_access_token(token), str(token_hash))


def token_hint(token: str) -> str:
    value = str(token or "")
    if len(value) <= 12:
        return value[:4] + "..." if value else ""
    return f"{value[:7]}...{value[-4:]}"


def build_external_mcp_url(api_v1_base_url: str, server_id: str) -> str:
    base = str(api_v1_base_url or "").rstrip("/")
    return f"{base}/api-monitor-mcp/{server_id}/mcp"


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def serialize_external_access_state(
    server_doc: Mapping[str, Any],
    *,
    external_url: str,
    once_visible_token: str = "",
) -> dict[str, Any]:
    external_access = server_doc.get("external_access") if isinstance(server_doc, Mapping) else {}
    if not isinstance(external_access, Mapping):
        external_access = {}
    requirements = build_caller_auth_requirements(server_doc.get("api_monitor_auth") or {})
    state = {
        "enabled": bool(external_access.get("enabled")),
        "url": external_url,
        "token_hint": str(external_access.get("token_hint") or ""),
        "created_at": _iso(external_access.get("created_at")),
        "last_rotated_at": _iso(external_access.get("last_rotated_at")),
        "last_used_at": _iso(external_access.get("last_used_at")),
        "require_caller_credentials": bool(requirements.get("required")),
        "caller_auth_requirements": requirements,
    }
    if once_visible_token:
        state["access_token"] = once_visible_token
    return state
