from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import httpx

from backend.credential.vault import get_vault
from backend.rpa.api_monitor.models import ApiMonitorSession
from backend.rpa.api_monitor_runtime_profile import ApiMonitorRuntimeProfile

logger = logging.getLogger(__name__)

PLACEHOLDER_CREDENTIAL_TYPE = "placeholder"
TEST_CREDENTIAL_TYPE = "test"
IDAAS_CREDENTIAL_TYPE = "idaas"
ALLOWED_API_MONITOR_CREDENTIAL_TYPES = {PLACEHOLDER_CREDENTIAL_TYPE, TEST_CREDENTIAL_TYPE, IDAAS_CREDENTIAL_TYPE}

NOISE_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "origin",
    "referer",
    "user-agent",
    "host",
    "connection",
    "cache-control",
    "pragma",
}
SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"}
SENSITIVE_HEADER_FRAGMENTS = ("token", "secret", "credential", "session", "csrf")


class CredentialValueResolver(Protocol):
    async def resolve_credential_values(self, user_id: str, cred_id: str) -> Mapping[str, str] | None:
        raise NotImplementedError


@dataclass(frozen=True)
class ApiMonitorAuthApplication:
    headers: dict[str, Any] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    preview: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def build_api_monitor_auth_profile(session: ApiMonitorSession) -> dict[str, Any]:
    selected_tools = [tool for tool in session.tool_definitions if getattr(tool, "selected", False)]
    call_to_tools: dict[str, set[str]] = {}
    for tool in selected_tools:
        for call_id in tool.source_calls:
            call_to_tools.setdefault(call_id, set()).add(tool.name)

    selected_call_ids = set(call_to_tools)
    header_entries: dict[str, dict[str, Any]] = {}
    for call in session.captured_calls:
        if selected_call_ids and call.id not in selected_call_ids:
            continue
        for raw_name, raw_value in call.request.headers.items():
            normalized = _normalize_header_name(raw_name)
            if not normalized or _is_noise_header(normalized):
                continue
            signals = _header_signals(normalized, str(raw_value or ""))
            if not signals:
                continue
            entry = header_entries.setdefault(
                normalized,
                {
                    "name": normalized,
                    "display_name": str(raw_name),
                    "occurrences": 0,
                    "tools": set(),
                    "signals": set(),
                    "masked_example": _mask_header_value(normalized, str(raw_value or "")),
                },
            )
            entry["occurrences"] += 1
            entry["tools"].update(call_to_tools.get(call.id, set()))
            entry["signals"].update(signals)

    headers = []
    for entry in sorted(header_entries.values(), key=lambda item: item["name"]):
        headers.append(
            {
                **entry,
                "tools": sorted(entry["tools"]),
                "signals": sorted(entry["signals"]),
            }
        )
    return {
        "header_count": len(headers),
        "sensitive_header_count": len(headers),
        "headers": headers,
        "recommended_credential_type": PLACEHOLDER_CREDENTIAL_TYPE,
    }


def normalize_api_monitor_auth_config(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("api_monitor_auth must be an object")
    credential_type = str(value.get("credential_type") or "").strip()
    credential_id = str(value.get("credential_id") or "").strip()
    login_url = str(value.get("login_url") or "").strip()
    if not credential_type and not credential_id:
        return {}
    if credential_type not in ALLOWED_API_MONITOR_CREDENTIAL_TYPES:
        allowed = ", ".join(sorted(ALLOWED_API_MONITOR_CREDENTIAL_TYPES))
        raise ValueError(f"api_monitor_auth.credential_type must be one of: {allowed}")
    result: dict[str, str] = {"credential_type": credential_type, "credential_id": credential_id}
    if login_url:
        result["login_url"] = login_url
    return result


async def validate_api_monitor_auth_config(
    user_id: str,
    value: Any,
    *,
    vault: CredentialValueResolver | None = None,
) -> dict[str, str]:
    config = normalize_api_monitor_auth_config(value)
    credential_id = config.get("credential_id", "")
    if credential_id:
        resolved = await (vault or get_vault()).resolve_credential_values(user_id, credential_id)
        if resolved is None:
            raise ValueError("api_monitor_auth references a missing credential")
    credential_type = config.get("credential_type", "")
    if credential_type == TEST_CREDENTIAL_TYPE and not config.get("login_url"):
        raise ValueError("api_monitor_auth.login_url is required for test credential type")
    return config


async def apply_api_monitor_auth_to_request(
    *,
    user_id: str,
    auth_config: Mapping[str, Any] | None,
    headers: Mapping[str, Any],
    query: Mapping[str, Any],
    body: Mapping[str, Any] | None,
    vault: CredentialValueResolver | None = None,
) -> ApiMonitorAuthApplication:
    config = normalize_api_monitor_auth_config(auth_config)
    next_headers = dict(headers)
    next_query = dict(query)
    next_body = dict(body or {})
    if not config:
        return ApiMonitorAuthApplication(headers=next_headers, query=next_query, body=next_body, preview={})

    credential_id = config.get("credential_id", "")
    credential_configured = bool(credential_id)
    if credential_id:
        resolved = await (vault or get_vault()).resolve_credential_values(user_id, credential_id)
        if resolved is None:
            return ApiMonitorAuthApplication(error="API Monitor credential not found")

    credential_type = config["credential_type"]
    if credential_type == PLACEHOLDER_CREDENTIAL_TYPE:
        return ApiMonitorAuthApplication(
            headers=next_headers,
            query=next_query,
            body=next_body,
            preview={
                "credential_type": PLACEHOLDER_CREDENTIAL_TYPE,
                "credential_configured": credential_configured,
                "injected": False,
            },
        )

    if credential_type == TEST_CREDENTIAL_TYPE:
        login_url = config.get("login_url", "")
        if not login_url:
            return ApiMonitorAuthApplication(error="Login URL is required for test credential type")
        if not credential_id:
            return ApiMonitorAuthApplication(error="Credential is required for test credential type")
        resolved = await (vault or get_vault()).resolve_credential_values(user_id, credential_id)
        if resolved is None:
            return ApiMonitorAuthApplication(error="API Monitor credential not found")
        username = resolved.get("username", "")
        password = resolved.get("password", "")
        if not username or not password:
            return ApiMonitorAuthApplication(error="Credential must have both username and password")
        try:
            async with httpx.AsyncClient(timeout=15) as login_client:
                login_resp = await login_client.post(
                    login_url,
                    json={"username": username, "password": password},
                )
        except httpx.HTTPError as exc:
            logger.warning("API Monitor login request failed: %s", exc)
            return ApiMonitorAuthApplication(error=f"Login request failed: {exc}")
        if login_resp.status_code != 200:
            logger.warning("API Monitor login returned %s: %s", login_resp.status_code, login_resp.text[:200])
            return ApiMonitorAuthApplication(error=f"Login failed (HTTP {login_resp.status_code})")
        try:
            token_data = login_resp.json()
        except ValueError:
            return ApiMonitorAuthApplication(error="Login response is not valid JSON")
        token = token_data.get("token") or token_data.get("access_token") or ""
        if not token:
            return ApiMonitorAuthApplication(error="Login response did not contain a token")
        next_headers["Authorization"] = f"Bearer {token}"
        return ApiMonitorAuthApplication(
            headers=next_headers,
            query=next_query,
            body=next_body,
            preview={
                "credential_type": TEST_CREDENTIAL_TYPE,
                "credential_configured": True,
                "injected": True,
                "login_url": login_url,
            },
        )

    if credential_type == IDAAS_CREDENTIAL_TYPE:
        return ApiMonitorAuthApplication(
            headers=next_headers,
            query=next_query,
            body=next_body,
            preview={
                "credential_type": IDAAS_CREDENTIAL_TYPE,
                "credential_configured": False,
                "injected": False,
            },
        )

    return ApiMonitorAuthApplication(error=f"Unsupported API Monitor credential type: {credential_type}")


async def apply_api_monitor_auth_to_profile(
    *,
    user_id: str,
    auth_config: Mapping[str, Any] | None,
    profile: ApiMonitorRuntimeProfile,
    client: Any,
    vault: CredentialValueResolver | None = None,
) -> ApiMonitorAuthApplication:
    config = normalize_api_monitor_auth_config(auth_config)
    if not config:
        return ApiMonitorAuthApplication(headers=dict(profile.headers), preview={})

    credential_id = config.get("credential_id", "")
    credential_configured = bool(credential_id)
    resolved = None
    if credential_id:
        resolved = await (vault or get_vault()).resolve_credential_values(user_id, credential_id)
        if resolved is None:
            return ApiMonitorAuthApplication(error="API Monitor credential not found")

    credential_type = config["credential_type"]
    if credential_type == PLACEHOLDER_CREDENTIAL_TYPE:
        return ApiMonitorAuthApplication(
            headers=dict(profile.headers),
            preview={
                "credential_type": PLACEHOLDER_CREDENTIAL_TYPE,
                "credential_configured": credential_configured,
                "injected": False,
            },
        )

    if credential_type == TEST_CREDENTIAL_TYPE:
        login_url = config.get("login_url", "")
        if not login_url:
            return ApiMonitorAuthApplication(error="Login URL is required for test credential type")
        if not resolved:
            return ApiMonitorAuthApplication(error="Credential is required for test credential type")
        username = resolved.get("username", "")
        password = resolved.get("password", "")
        if not username or not password:
            return ApiMonitorAuthApplication(error="Credential must have both username and password")
        try:
            login_resp = await client.request(
                "POST",
                login_url,
                json={"username": username, "password": password},
            )
        except httpx.HTTPError as exc:
            return ApiMonitorAuthApplication(error=f"Login request failed: {exc}")
        if not login_resp.is_success:
            return ApiMonitorAuthApplication(error=f"Login failed (HTTP {login_resp.status_code})")
        try:
            token_data = login_resp.json()
        except ValueError:
            return ApiMonitorAuthApplication(error="Login response is not valid JSON")
        token = token_data.get("token") or token_data.get("access_token") or ""
        if not token:
            return ApiMonitorAuthApplication(error="Login response did not contain a token")
        profile.set_variable("auth_token", token, secret=True)
        profile.set_header("Authorization", f"Bearer {token}", secret=True)
        profile.has_cookies = bool(getattr(client, "cookies", None))
        return ApiMonitorAuthApplication(
            headers=dict(profile.headers),
            preview={
                "credential_type": TEST_CREDENTIAL_TYPE,
                "credential_configured": True,
                "injected": True,
                "login_url": login_url,
                "profile": profile.preview(),
            },
        )

    if credential_type == IDAAS_CREDENTIAL_TYPE:
        return ApiMonitorAuthApplication(
            headers=dict(profile.headers),
            preview={
                "credential_type": IDAAS_CREDENTIAL_TYPE,
                "credential_configured": False,
                "injected": False,
            },
        )

    return ApiMonitorAuthApplication(error=f"Unsupported API Monitor credential type: {credential_type}")


def _normalize_header_name(value: str) -> str:
    return str(value or "").strip().lower()


def _is_noise_header(name: str) -> bool:
    return name in NOISE_HEADERS or name.startswith("sec-")


def _header_signals(name: str, value: str) -> list[str]:
    signals: list[str] = []
    if name in SENSITIVE_HEADERS:
        signals.append(f"{name}-header")
    for fragment in SENSITIVE_HEADER_FRAGMENTS:
        if fragment in name and f"{fragment}-name" not in signals:
            signals.append(f"{fragment}-name")
    if name == "authorization" and value.lower().startswith("bearer "):
        signals.append("bearer-like-value")
    return signals


def _mask_header_value(name: str, value: str) -> str:
    if name == "authorization" and value.lower().startswith("bearer "):
        return "Bearer ***"
    if value:
        return "***"
    return ""
