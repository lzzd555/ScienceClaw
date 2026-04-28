# API Monitor MCP External Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个 API Monitor MCP item 可以派生一个外部 MCP URL，并要求外部 Agent 按 `api_monitor_auth.credential_type` 在每次工具调用时提供目标 API 凭证。

**Architecture:** 后端新增 API Monitor MCP 专用 Gateway 路由和 external access 管理能力，外部访问 token 只保存 hash，工具执行复用 `ApiMonitorMcpRuntime` 的映射与 token flow 逻辑。外部调用进入 caller-only runtime，`placeholder` 不注入凭证，`test` 只接受调用方传入的 `Authorization` header，不读取 vault、不执行内部登录。

**Tech Stack:** FastAPI, Pydantic v2, async repository abstraction, httpx, pytest, Vue 3, TypeScript, Vite/Vitest, Tailwind CSS.

**Spec:** `docs/superpowers/specs/2026-04-28-api-monitor-mcp-external-access-design.md`

---

## File Structure

- Create `RpaClaw/backend/rpa/api_monitor_external_access.py`
  - External access token generation, hash/verify, sanitized state serialization.
  - Caller auth requirements generated from `api_monitor_auth.credential_type`.
  - External `tools/list` schema decoration and `_auth` extraction.

- Create `RpaClaw/backend/route/api_monitor_mcp_gateway.py`
  - Public JSON-RPC MCP endpoint: `POST /api-monitor-mcp/{server_id}/mcp`.
  - External token authentication.
  - `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`.

- Modify `RpaClaw/backend/deepagent/mcp_runtime.py`
  - Add caller-only execution mode to `ApiMonitorMcpRuntime`.
  - Reuse request mapping and token flow logic without vault or internal login.

- Modify `RpaClaw/backend/route/mcp.py`
  - Add management APIs for enable/get/rotate/disable external access.
  - Return `external_access` state on API Monitor server detail.
  - Return `caller_auth_requirements` on API Monitor tool detail.

- Modify `RpaClaw/backend/main.py`
  - Register `api_monitor_mcp_gateway` router under `/api/v1`.

- Create `RpaClaw/backend/tests/test_api_monitor_external_access.py`
  - Unit tests for token helper, caller requirements, schema decoration, `_auth` extraction.

- Create `RpaClaw/backend/tests/test_api_monitor_external_gateway.py`
  - Route tests for external MCP endpoint and management behavior that does not need a real MongoDB.

- Modify `RpaClaw/backend/tests/test_mcp_route.py`
  - Add coverage for `api-monitor-detail` returning `external_access` and `caller_auth_requirements`.

- Modify `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`
  - Add caller-only runtime tests.

- Modify `RpaClaw/frontend/src/api/mcp.ts`
  - Add external access types and API functions.

- Create `RpaClaw/frontend/src/utils/apiMonitorExternalAccess.ts`
  - Small UI helpers for caller auth display and external MCP client snippets.

- Create `RpaClaw/frontend/src/utils/apiMonitorExternalAccess.test.ts`
  - Unit tests for UI helper output.

- Modify `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`
  - Add “外部访问” section inside the existing API Monitor MCP detail dialog.

- Modify `RpaClaw/frontend/src/locales/en.ts`
  - Add English UI strings.

- Modify `RpaClaw/frontend/src/locales/zh.ts`
  - Add Chinese UI strings.

---

## Task 1: Caller Auth Contract Helper

**Files:**
- Create: `RpaClaw/backend/rpa/api_monitor_external_access.py`
- Create: `RpaClaw/backend/tests/test_api_monitor_external_access.py`

- [ ] **Step 1: Write failing caller-auth contract tests**

Add `RpaClaw/backend/tests/test_api_monitor_external_access.py`:

```python
from __future__ import annotations

import pytest

from backend.rpa.api_monitor_external_access import (
    CALLER_AUTH_EXTENSION_KEY,
    CallerAuthError,
    build_caller_auth_requirements,
    build_external_tool_input_schema,
    extract_caller_auth_profile,
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
```

- [ ] **Step 2: Run the new tests and confirm helper is missing**

Run:

```bash
cd RpaClaw/backend
pytest tests/test_api_monitor_external_access.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rpa.api_monitor_external_access'`.

- [ ] **Step 3: Implement caller-auth contract helper**

Create `RpaClaw/backend/rpa/api_monitor_external_access.py`:

```python
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from backend.rpa.api_monitor_auth import PLACEHOLDER_CREDENTIAL_TYPE, TEST_CREDENTIAL_TYPE
from backend.rpa.api_monitor_runtime_profile import ApiMonitorRuntimeProfile

CALLER_AUTH_EXTENSION_KEY = "x-rpaclaw-authRequirements"
TARGET_AUTH_HEADER = "X-RpaClaw-Target-Authorization"


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
```

- [ ] **Step 4: Run helper tests**

Run:

```bash
cd RpaClaw/backend
pytest tests/test_api_monitor_external_access.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor_external_access.py RpaClaw/backend/tests/test_api_monitor_external_access.py
git commit -m "feat: add api monitor external caller auth contract"
```

---

## Task 2: External Access Token Helpers

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor_external_access.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_external_access.py`

- [ ] **Step 1: Add failing token lifecycle tests**

Append to `RpaClaw/backend/tests/test_api_monitor_external_access.py`:

```python
from datetime import datetime

from backend.rpa.api_monitor_external_access import (
    build_external_mcp_url,
    generate_external_access_token,
    hash_external_access_token,
    serialize_external_access_state,
    token_hint,
    verify_external_access_token,
)


def test_external_access_token_hash_and_verify():
    token = generate_external_access_token()
    token_hash = hash_external_access_token(token)

    assert token.startswith("rpamcp_")
    assert token_hash.startswith("sha256:")
    assert verify_external_access_token(token, token_hash) is True
    assert verify_external_access_token(token + "x", token_hash) is False


def test_token_hint_masks_token():
    assert token_hint("rpamcp_abcdefghijklmnopqrstuvwxyz") == "rpamcp_...wxyz"


def test_build_external_mcp_url_uses_api_v1_prefix():
    assert (
        build_external_mcp_url("http://localhost:12001/api/v1", "mcp_abc123")
        == "http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc123/mcp"
    )


def test_serialize_external_access_state_hides_hash_and_optionally_returns_once_token():
    state = serialize_external_access_state(
        {
            "external_access": {
                "enabled": True,
                "access_token_hash": "sha256:secret",
                "token_hint": "rpamcp_...abcd",
                "created_at": datetime(2026, 4, 28, 1, 2, 3),
                "last_rotated_at": datetime(2026, 4, 28, 2, 3, 4),
                "last_used_at": datetime(2026, 4, 28, 3, 4, 5),
            },
            "api_monitor_auth": {"credential_type": "test"},
        },
        external_url="http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc123/mcp",
        once_visible_token="rpamcp_once",
    )

    assert state["enabled"] is True
    assert state["url"] == "http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc123/mcp"
    assert state["token_hint"] == "rpamcp_...abcd"
    assert state["access_token"] == "rpamcp_once"
    assert state["require_caller_credentials"] is True
    assert "access_token_hash" not in state
```

- [ ] **Step 2: Run tests and confirm new helper symbols are missing**

Run:

```bash
cd RpaClaw/backend
pytest tests/test_api_monitor_external_access.py -q
```

Expected: FAIL with import errors for token helper functions.

- [ ] **Step 3: Implement token helpers**

Append to `RpaClaw/backend/rpa/api_monitor_external_access.py`:

```python
import hashlib
import hmac
import secrets
from datetime import datetime

EXTERNAL_ACCESS_TOKEN_PREFIX = "rpamcp_"


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
```

- [ ] **Step 4: Run token and caller contract tests**

Run:

```bash
cd RpaClaw/backend
pytest tests/test_api_monitor_external_access.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor_external_access.py RpaClaw/backend/tests/test_api_monitor_external_access.py
git commit -m "feat: add api monitor external access token helpers"
```

---

## Task 3: Management APIs and API Monitor Detail Metadata

**Files:**
- Modify: `RpaClaw/backend/route/mcp.py`
- Modify: `RpaClaw/backend/tests/test_mcp_route.py`

- [ ] **Step 1: Add failing route tests for external access management**

Append to `RpaClaw/backend/tests/test_mcp_route.py`:

```python
def test_api_monitor_detail_includes_external_access_and_caller_auth_requirements(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo(
        [
            _api_monitor_server_doc(
                api_monitor_auth={"credential_type": "test", "credential_id": "cred_1", "login_url": "https://login.test"},
                external_access={"enabled": False, "token_hint": ""},
            )
        ]
    )
    tool_repo = _MemoryRepo([_api_monitor_tool_doc()])

    def fake_get_repository(collection_name: str):
        return tool_repo if collection_name == "api_monitor_mcp_tools" else server_repo

    monkeypatch.setattr(mcp_route, "get_repository", fake_get_repository)

    response = client.get("/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-detail")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["server"]["external_access"]["enabled"] is False
    assert data["server"]["external_access"]["require_caller_credentials"] is True
    assert data["tools"][0]["caller_auth_requirements"]["required"] is True
    assert data["tools"][0]["caller_auth_requirements"]["accepted_fields"] == ["_auth.headers.Authorization"]


def test_enable_api_monitor_external_access_updates_existing_server_only(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_api_monitor_server_doc(api_monitor_auth={"credential_type": "placeholder"})])
    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: server_repo)

    response = client.post("/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-external-access/enable")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is True
    assert data["access_token"].startswith("rpamcp_")
    assert data["url"].endswith("/api/v1/api-monitor-mcp/mcp_api_monitor/mcp")
    stored = server_repo.docs["mcp_api_monitor"]["external_access"]
    assert stored["enabled"] is True
    assert stored["access_token_hash"].startswith("sha256:")
    assert stored["token_hint"] == data["token_hint"]
    assert "access_token" not in stored
    assert set(server_repo.docs) == {"mcp_api_monitor"}


def test_get_api_monitor_external_access_does_not_return_plain_token(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    token_hash = mcp_route.hash_external_access_token("rpamcp_secret")
    server_repo = _MemoryRepo(
        [
            _api_monitor_server_doc(
                api_monitor_auth={"credential_type": "test", "login_url": "https://login.test"},
                external_access={
                    "enabled": True,
                    "access_token_hash": token_hash,
                    "token_hint": "rpamcp_...cret",
                },
            )
        ]
    )
    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: server_repo)

    response = client.get("/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-external-access")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is True
    assert data["token_hint"] == "rpamcp_...cret"
    assert "access_token" not in data
    assert "access_token_hash" not in data


def test_rotate_api_monitor_external_access_replaces_hash(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    original_hash = mcp_route.hash_external_access_token("rpamcp_old")
    server_repo = _MemoryRepo(
        [
            _api_monitor_server_doc(
                external_access={
                    "enabled": True,
                    "access_token_hash": original_hash,
                    "token_hint": "rpamcp_...old",
                }
            )
        ]
    )
    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: server_repo)

    response = client.post("/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-external-access/rotate-token")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access_token"].startswith("rpamcp_")
    assert server_repo.docs["mcp_api_monitor"]["external_access"]["access_token_hash"] != original_hash


def test_disable_api_monitor_external_access_keeps_token_hash_for_future_rotation(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    token_hash = mcp_route.hash_external_access_token("rpamcp_secret")
    server_repo = _MemoryRepo(
        [
            _api_monitor_server_doc(
                external_access={
                    "enabled": True,
                    "access_token_hash": token_hash,
                    "token_hint": "rpamcp_...cret",
                }
            )
        ]
    )
    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: server_repo)

    response = client.post("/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-external-access/disable")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is False
    assert server_repo.docs["mcp_api_monitor"]["external_access"]["enabled"] is False
    assert server_repo.docs["mcp_api_monitor"]["external_access"]["access_token_hash"] == token_hash
```

- [ ] **Step 2: Run route tests and confirm new API fields are absent**

Run:

```bash
cd RpaClaw/backend
pytest tests/test_mcp_route.py -q
```

Expected: FAIL because `external_access` and management endpoints are not implemented.

- [ ] **Step 3: Import helper functions and extend response model**

Modify imports and `McpServerListItem` in `RpaClaw/backend/route/mcp.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from backend.rpa.api_monitor_external_access import (
    build_caller_auth_requirements,
    build_external_mcp_url,
    generate_external_access_token,
    hash_external_access_token,
    serialize_external_access_state,
    token_hint,
)
```

```python
class McpServerListItem(BaseModel):
    id: str
    server_key: str
    scope: str
    name: str
    description: str = ""
    transport: str
    source_type: str = ""
    enabled: bool = True
    default_enabled: bool = False
    readonly: bool = False
    endpoint_config: Dict[str, Any] = Field(default_factory=dict)
    credential_binding: Dict[str, Any] = Field(default_factory=dict)
    api_monitor_auth: Dict[str, Any] = Field(default_factory=dict)
    tool_policy: Dict[str, Any] = Field(default_factory=dict)
    external_access: Dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Add serialization helpers**

Add below `_serialize_api_monitor_user_server`:

```python
def _api_v1_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/api/v1"


def _api_monitor_external_url(request: Request, server_id: str) -> str:
    return build_external_mcp_url(_api_v1_base_url(request), server_id)


def _serialize_api_monitor_external_access_for_request(
    request: Request,
    server_doc: Dict[str, Any],
    *,
    once_visible_token: str = "",
) -> Dict[str, Any]:
    return serialize_external_access_state(
        server_doc,
        external_url=_api_monitor_external_url(request, str(server_doc["_id"])),
        once_visible_token=once_visible_token,
    )
```

Modify `_serialize_api_monitor_user_server` to include sanitized state when called without a request:

```python
external_access=serialize_external_access_state(
    doc,
    external_url="",
),
```

Modify `_serialize_api_monitor_tool_detail` signature and body:

```python
def _serialize_api_monitor_tool_detail(doc: Dict[str, Any], server_doc: Dict[str, Any] | None = None) -> Dict[str, Any]:
    caller_auth_requirements = build_caller_auth_requirements(
        (server_doc or {}).get("api_monitor_auth") or {}
    )
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name", ""),
        "description": doc.get("description", ""),
        "yaml_definition": doc.get("yaml_definition", ""),
        "method": doc.get("method", ""),
        "url": doc.get("url", ""),
        "input_schema": _api_monitor_tool_input_schema(doc),
        "path_mapping": doc.get("path_mapping") or {},
        "query_mapping": doc.get("query_mapping") or {},
        "body_mapping": doc.get("body_mapping") or {},
        "header_mapping": doc.get("header_mapping") or {},
        "response_schema": doc.get("response_schema") or {},
        "validation_status": doc.get("validation_status", "valid"),
        "validation_errors": doc.get("validation_errors") or [],
        "order": doc.get("order", 0),
        "caller_auth_requirements": caller_auth_requirements,
    }
```

Update all existing calls to `_serialize_api_monitor_tool_detail(doc)`:

```python
_serialize_api_monitor_tool_detail(doc, server_doc)
```

When only a merged tool doc is available after update, pass the already loaded server document:

```python
return ApiResponse(data=_serialize_api_monitor_tool_detail({**tool_doc, **update_doc}, server_doc))
```

- [ ] **Step 5: Update API Monitor detail endpoint to include request-aware external access**

Change `get_api_monitor_mcp_detail` signature and return:

```python
@router.get("/mcp/servers/{server_key}/api-monitor-detail", response_model=ApiResponse)
async def get_api_monitor_mcp_detail(
    server_key: str,
    request: Request,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    user_id = str(current_user.id)
    server_doc = await _get_owned_api_monitor_server_doc(server_key, user_id)
    tool_docs = await _load_api_monitor_tool_documents(str(server_doc["_id"]), user_id)
    server_payload = _serialize_api_monitor_user_server(server_doc)
    server_payload["external_access"] = _serialize_api_monitor_external_access_for_request(request, server_doc)
    return ApiResponse(
        data={
            "server": server_payload,
            "tools": [_serialize_api_monitor_tool_detail(doc, server_doc) for doc in tool_docs],
        }
    )
```

- [ ] **Step 6: Add management endpoints**

Add below `get_api_monitor_mcp_detail`:

```python
def _external_access_update_payload(token: str, *, enabled: bool, now: datetime) -> Dict[str, Any]:
    return {
        "enabled": enabled,
        "access_token_hash": hash_external_access_token(token),
        "token_hint": token_hint(token),
        "created_at": now,
        "last_rotated_at": now,
        "last_used_at": "",
        "require_caller_credentials": False,
        "allowed_credential_channels": ["arguments", "headers"],
        "allowed_target_auth_headers": ["authorization"],
    }


@router.get("/mcp/servers/{server_key}/api-monitor-external-access", response_model=ApiResponse)
async def get_api_monitor_external_access(
    server_key: str,
    request: Request,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    server_doc = await _get_owned_api_monitor_server_doc(server_key, str(current_user.id))
    return ApiResponse(data=_serialize_api_monitor_external_access_for_request(request, server_doc))


@router.post("/mcp/servers/{server_key}/api-monitor-external-access/enable", response_model=ApiResponse)
async def enable_api_monitor_external_access(
    server_key: str,
    request: Request,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    user_id = str(current_user.id)
    server_doc = await _get_owned_api_monitor_server_doc(server_key, user_id)
    token = generate_external_access_token()
    now = datetime.now()
    requirements = build_caller_auth_requirements(server_doc.get("api_monitor_auth") or {})
    external_access = _external_access_update_payload(token, enabled=True, now=now)
    external_access["require_caller_credentials"] = bool(requirements.get("required"))
    repo = get_repository("user_mcp_servers")
    await repo.update_one(
        {"_id": str(server_doc["_id"]), "user_id": user_id},
        {"$set": {"external_access": external_access, "updated_at": now}},
    )
    updated_doc = {**server_doc, "external_access": external_access}
    return ApiResponse(
        data=_serialize_api_monitor_external_access_for_request(
            request,
            updated_doc,
            once_visible_token=token,
        )
    )


@router.post("/mcp/servers/{server_key}/api-monitor-external-access/rotate-token", response_model=ApiResponse)
async def rotate_api_monitor_external_access_token(
    server_key: str,
    request: Request,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    user_id = str(current_user.id)
    server_doc = await _get_owned_api_monitor_server_doc(server_key, user_id)
    token = generate_external_access_token()
    now = datetime.now()
    existing = server_doc.get("external_access") if isinstance(server_doc.get("external_access"), dict) else {}
    external_access = dict(existing or {})
    external_access.update(
        {
            "enabled": True,
            "access_token_hash": hash_external_access_token(token),
            "token_hint": token_hint(token),
            "last_rotated_at": now,
            "allowed_credential_channels": ["arguments", "headers"],
            "allowed_target_auth_headers": ["authorization"],
        }
    )
    external_access.setdefault("created_at", now)
    repo = get_repository("user_mcp_servers")
    await repo.update_one(
        {"_id": str(server_doc["_id"]), "user_id": user_id},
        {"$set": {"external_access": external_access, "updated_at": now}},
    )
    updated_doc = {**server_doc, "external_access": external_access}
    return ApiResponse(
        data=_serialize_api_monitor_external_access_for_request(
            request,
            updated_doc,
            once_visible_token=token,
        )
    )


@router.post("/mcp/servers/{server_key}/api-monitor-external-access/disable", response_model=ApiResponse)
async def disable_api_monitor_external_access(
    server_key: str,
    request: Request,
    current_user: User = Depends(require_user),
) -> ApiResponse:
    user_id = str(current_user.id)
    server_doc = await _get_owned_api_monitor_server_doc(server_key, user_id)
    external_access = dict(server_doc.get("external_access") or {})
    external_access["enabled"] = False
    now = datetime.now()
    repo = get_repository("user_mcp_servers")
    await repo.update_one(
        {"_id": str(server_doc["_id"]), "user_id": user_id},
        {"$set": {"external_access": external_access, "updated_at": now}},
    )
    updated_doc = {**server_doc, "external_access": external_access}
    return ApiResponse(data=_serialize_api_monitor_external_access_for_request(request, updated_doc))
```

- [ ] **Step 7: Run route tests**

Run:

```bash
cd RpaClaw/backend
pytest tests/test_mcp_route.py tests/test_api_monitor_external_access.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add RpaClaw/backend/route/mcp.py RpaClaw/backend/tests/test_mcp_route.py
git commit -m "feat: add api monitor external access management api"
```

---

## Task 4: Caller-Only Runtime

**Files:**
- Modify: `RpaClaw/backend/deepagent/mcp_runtime.py`
- Modify: `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`

- [ ] **Step 1: Add failing caller-only runtime tests**

Append to `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`:

```python
def test_api_monitor_caller_only_runtime_uses_caller_authorization_without_vault(monkeypatch):
    api_client = _FakeApiMonitorClient()
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: api_client)
    monkeypatch.setattr(
        mcp_runtime,
        "get_repository",
        lambda collection_name: _MemoryRepo(
            [
                {
                    "mcp_server_id": "mcp_api_monitor",
                    "name": "search_orders",
                    "description": "Search orders",
                    "method": "GET",
                    "url": "/api/orders",
                    "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
                    "query_mapping": {"keyword": "{{ keyword }}"},
                    "validation_status": "valid",
                }
            ]
        ),
    )

    def fail_get_vault():
        raise AssertionError("caller-only runtime must not read vault")

    monkeypatch.setattr(mcp_runtime, "get_vault", fail_get_vault)

    profile = mcp_runtime.ApiMonitorRuntimeProfile(base_url="https://api.example.test")
    profile.set_header("Authorization", "Bearer caller-token", secret=True)
    profile.set_variable("auth_token", "Bearer caller-token", secret=True, source="test")
    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="u1",
        name="Example MCP",
        transport="api_monitor",
        scope="user",
        url="https://api.example.test",
        headers={"Authorization": "Bearer internal-token"},
        api_monitor_auth={
            "credential_type": "test",
            "credential_id": "cred_1",
            "login_url": "https://login.example.test",
        },
    )

    result = asyncio.run(
        mcp_runtime.ApiMonitorMcpRuntime(
            server,
            caller_only=True,
            caller_profile=profile,
            caller_auth_preview={"credential_type": "test", "headers": ["Authorization"], "injected": True},
        ).call_tool("search_orders", {"keyword": "abc", "_auth": {"headers": {"Authorization": "Bearer ignored"}}})
    )

    assert result["success"] is True
    assert api_client.requests == [
        {
            "method": "GET",
            "url": "https://api.example.test/api/orders",
            "headers": {"Authorization": "Bearer caller-token"},
            "params": {"keyword": "abc"},
            "json": None,
        }
    ]
    assert result["request_preview"]["headers"] == {"Authorization": "***"}
    assert result["request_preview"]["auth"]["credential_type"] == "test"
    assert result["request_preview"]["auth"]["injected"] is True


def test_api_monitor_caller_only_runtime_placeholder_does_not_use_server_headers(monkeypatch):
    api_client = _FakeApiMonitorClient()
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: api_client)
    monkeypatch.setattr(
        mcp_runtime,
        "get_repository",
        lambda collection_name: _MemoryRepo(
            [
                {
                    "mcp_server_id": "mcp_api_monitor",
                    "name": "search_orders",
                    "method": "GET",
                    "url": "/api/orders",
                    "validation_status": "valid",
                }
            ]
        ),
    )

    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="u1",
        name="Example MCP",
        transport="api_monitor",
        scope="user",
        url="https://api.example.test?internal=1",
        headers={"Authorization": "Bearer internal-token"},
        api_monitor_auth={"credential_type": "placeholder", "credential_id": "cred_1"},
    )

    result = asyncio.run(
        mcp_runtime.ApiMonitorMcpRuntime(
            server,
            caller_only=True,
            caller_profile=mcp_runtime.ApiMonitorRuntimeProfile(base_url="https://api.example.test"),
            caller_auth_preview={"credential_type": "placeholder", "headers": [], "injected": False},
        ).call_tool("search_orders", {})
    )

    assert result["success"] is True
    assert api_client.requests[0]["headers"] == {}
    assert api_client.requests[0]["params"] == {}
```

If `_FakeApiMonitorClient` in the existing test file does not record `json`, update the fake client once:

```python
class _FakeApiMonitorClient:
    def __init__(self, response=None):
        self.response = response or httpx.Response(200, json={"ok": True})
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, method, url, **kwargs):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": kwargs.get("headers") or {},
                "params": kwargs.get("params") or {},
                "json": kwargs.get("json"),
            }
        )
        return self.response
```

- [ ] **Step 2: Run runtime tests and confirm constructor does not accept caller-only options**

Run:

```bash
cd RpaClaw/backend
pytest tests/deepagent/test_mcp_runtime.py::test_api_monitor_caller_only_runtime_uses_caller_authorization_without_vault tests/deepagent/test_mcp_runtime.py::test_api_monitor_caller_only_runtime_placeholder_does_not_use_server_headers -q
```

Expected: FAIL with `TypeError` for unexpected `caller_only` argument.

- [ ] **Step 3: Extend `ApiMonitorMcpRuntime.__init__`**

In `RpaClaw/backend/deepagent/mcp_runtime.py`, change the class initializer:

```python
class ApiMonitorMcpRuntime:
    def __init__(
        self,
        server: McpServerDefinition,
        *,
        caller_only: bool = False,
        caller_profile: ApiMonitorRuntimeProfile | None = None,
        caller_auth_preview: Mapping[str, Any] | None = None,
    ) -> None:
        self._server = server
        self._tools = get_repository("api_monitor_mcp_tools")
        self._caller_only = caller_only
        self._caller_profile = caller_profile
        self._caller_auth_preview = dict(caller_auth_preview or {})
```

- [ ] **Step 4: Strip reserved `_auth` from runtime arguments**

Inside `call_tool`, replace:

```python
rendered_arguments = dict(arguments)
```

with:

```python
rendered_arguments = dict(arguments)
rendered_arguments.pop("_auth", None)
```

- [ ] **Step 5: Update V1 request construction for caller-only**

In the V1 path inside `call_tool`, replace the existing base request construction and auth application block with:

```python
        if self._caller_only:
            request_query = {}
            request_headers = dict((self._caller_profile or ApiMonitorRuntimeProfile()).headers)
            request_headers.update(render_mapping(header_mapping, rendered_arguments))
            request_body = render_mapping(body_mapping, rendered_arguments)
            auth_application = ApiMonitorAuthApplication(
                headers=request_headers,
                query=request_query,
                body=request_body,
                preview=dict(self._caller_auth_preview),
            )
        else:
            request_query = _api_monitor_base_query(self._server) if not has_api_monitor_auth else {}
            request_query.update(render_mapping(query_mapping, rendered_arguments))
            request_headers = dict(self._server.headers) if not has_api_monitor_auth else {}
            request_headers.update(render_mapping(header_mapping, rendered_arguments))
            request_body = render_mapping(body_mapping, rendered_arguments)

            auth_application = await apply_api_monitor_auth_to_request(
                user_id=self._server.user_id,
                auth_config=self._server.api_monitor_auth,
                headers=request_headers,
                query=request_query,
                body=request_body,
                vault=get_vault(),
            )
            if auth_application.error:
                return {"success": False, "error": auth_application.error}

            request_headers = auth_application.headers
            request_query = auth_application.query
            request_body = auth_application.body
```

Keep the existing `json_body = request_body or None` immediately after this block.

- [ ] **Step 6: Update V2 path to accept caller profile**

Change `_call_tool_v2` signature:

```python
    async def _call_tool_v2(
        self,
        *,
        doc: Mapping[str, Any],
        method: str,
        url: str,
        rendered_arguments: dict[str, Any],
        request_base_url: str,
        query_mapping: dict[str, Any],
        body_mapping: dict[str, Any],
        header_mapping: dict[str, Any],
    ) -> dict[str, Any]:
        profile = self._caller_profile or ApiMonitorRuntimeProfile(base_url=request_base_url)
        if not profile.base_url:
            profile.base_url = request_base_url
```

Replace the auth application block inside `_call_tool_v2`:

```python
            if self._caller_only:
                auth_application = ApiMonitorAuthApplication(
                    headers=dict(profile.headers),
                    preview=dict(self._caller_auth_preview),
                )
            else:
                auth_application = await apply_api_monitor_auth_to_profile(
                    user_id=self._server.user_id,
                    auth_config=self._server.api_monitor_auth,
                    profile=profile,
                    client=client,
                    vault=get_vault(),
                )
                if auth_application.error:
                    return {"success": False, "error": auth_application.error}
```

- [ ] **Step 7: Run targeted runtime tests**

Run:

```bash
cd RpaClaw/backend
pytest tests/deepagent/test_mcp_runtime.py::test_api_monitor_caller_only_runtime_uses_caller_authorization_without_vault tests/deepagent/test_mcp_runtime.py::test_api_monitor_caller_only_runtime_placeholder_does_not_use_server_headers -q
```

Expected: PASS.

- [ ] **Step 8: Run full API Monitor runtime regression tests**

Run:

```bash
cd RpaClaw/backend
pytest tests/deepagent/test_mcp_runtime.py tests/test_api_monitor_auth.py tests/test_api_monitor_token_flow.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add RpaClaw/backend/deepagent/mcp_runtime.py RpaClaw/backend/tests/deepagent/test_mcp_runtime.py
git commit -m "feat: support caller-only api monitor runtime"
```

---

## Task 5: External API Monitor MCP Gateway

**Files:**
- Create: `RpaClaw/backend/route/api_monitor_mcp_gateway.py`
- Modify: `RpaClaw/backend/main.py`
- Create: `RpaClaw/backend/tests/test_api_monitor_external_gateway.py`

- [ ] **Step 1: Add failing Gateway route tests**

Create `RpaClaw/backend/tests/test_api_monitor_external_gateway.py`:

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.route import api_monitor_mcp_gateway as gateway
from backend.rpa.api_monitor_external_access import hash_external_access_token


class _MemoryRepo:
    def __init__(self, docs=None):
        self.docs = {str(doc["_id"]): dict(doc) for doc in (docs or [])}

    async def find_one(self, filter_doc, projection=None):
        for doc in self.docs.values():
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                return dict(doc)
        return None

    async def find_many(self, filter_doc, projection=None, sort=None, skip=0, limit=0):
        docs = [
            dict(doc)
            for doc in self.docs.values()
            if all(doc.get(key) == value for key, value in filter_doc.items())
        ]
        if sort:
            for key, direction in reversed(sort):
                docs.sort(key=lambda item, field=key: item.get(field), reverse=(direction == -1))
        return docs

    async def update_one(self, filter_doc, update_doc, upsert=False):
        for doc_id, doc in self.docs.items():
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                updated = dict(doc)
                updated.update(update_doc.get("$set", {}))
                self.docs[doc_id] = updated
                return 1
        return 0


def _server_doc(**overrides):
    doc = {
        "_id": "mcp_api_monitor",
        "user_id": "user-1",
        "name": "Example MCP",
        "description": "Captured APIs",
        "transport": "api_monitor",
        "source_type": "api_monitor",
        "enabled": True,
        "endpoint_config": {"url": "https://api.example.test"},
        "api_monitor_auth": {"credential_type": "test", "login_url": "https://login.example.test"},
        "external_access": {
            "enabled": True,
            "access_token_hash": hash_external_access_token("rpamcp_secret"),
            "token_hint": "rpamcp_...cret",
        },
    }
    doc.update(overrides)
    return doc


def _tool_doc(**overrides):
    doc = {
        "_id": "tool_1",
        "mcp_server_id": "mcp_api_monitor",
        "user_id": "user-1",
        "name": "search_orders",
        "description": "Search orders by keyword",
        "method": "GET",
        "url": "/api/orders",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string"}},
            "required": ["keyword"],
        },
        "query_mapping": {"keyword": "{{ keyword }}"},
        "validation_status": "valid",
        "order": 0,
    }
    doc.update(overrides)
    return doc


def _build_app():
    app = FastAPI()
    app.include_router(gateway.router, prefix="/api/v1")
    return app


def test_initialize_requires_valid_external_token(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_server_doc()])
    tool_repo = _MemoryRepo([])
    monkeypatch.setattr(gateway, "get_repository", lambda collection_name: tool_repo if collection_name == "api_monitor_mcp_tools" else server_repo)

    response = client.post(
        "/api/v1/api-monitor-mcp/mcp_api_monitor/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Authorization": "Bearer wrong"},
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32002

    ok = client.post(
        "/api/v1/api-monitor-mcp/mcp_api_monitor/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Authorization": "Bearer rpamcp_secret"},
    )
    assert ok.status_code == 200
    assert ok.json()["result"]["serverInfo"]["name"] == "Example MCP"


def test_tools_list_adds_auth_schema_for_test_credential(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_server_doc()])
    tool_repo = _MemoryRepo([_tool_doc(), _tool_doc(_id="tool_2", name="invalid_tool", validation_status="invalid")])
    monkeypatch.setattr(gateway, "get_repository", lambda collection_name: tool_repo if collection_name == "api_monitor_mcp_tools" else server_repo)

    response = client.post(
        "/api/v1/api-monitor-mcp/mcp_api_monitor/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={"Authorization": "Bearer rpamcp_secret"},
    )

    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["search_orders"]
    schema = tools[0]["inputSchema"]
    assert schema["required"] == ["keyword", "_auth"]
    assert tools[0]["x-rpaclaw-authRequirements"]["accepted_fields"] == ["_auth.headers.Authorization"]


def test_tools_call_returns_error_when_test_auth_missing(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_server_doc()])
    tool_repo = _MemoryRepo([_tool_doc()])
    monkeypatch.setattr(gateway, "get_repository", lambda collection_name: tool_repo if collection_name == "api_monitor_mcp_tools" else server_repo)

    response = client.post(
        "/api/v1/api-monitor-mcp/mcp_api_monitor/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_orders", "arguments": {"keyword": "abc"}}},
        headers={"Authorization": "Bearer rpamcp_secret"},
    )

    payload = response.json()["result"]
    assert payload["isError"] is True
    assert payload["structuredContent"]["success"] is False
    assert "Missing caller Authorization" in payload["structuredContent"]["error"]


def test_tools_call_uses_caller_profile(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_server_doc()])
    tool_repo = _MemoryRepo([_tool_doc()])
    monkeypatch.setattr(gateway, "get_repository", lambda collection_name: tool_repo if collection_name == "api_monitor_mcp_tools" else server_repo)

    class _Runtime:
        def __init__(self, server, **kwargs):
            self.server = server
            self.kwargs = kwargs

        async def call_tool(self, tool_name, arguments):
            assert tool_name == "search_orders"
            assert arguments == {"keyword": "abc"}
            assert self.kwargs["caller_only"] is True
            assert self.kwargs["caller_profile"].headers["Authorization"] == "Bearer caller-token"
            return {"success": True, "data": {"ok": True}}

    monkeypatch.setattr(gateway, "ApiMonitorMcpRuntime", _Runtime)

    response = client.post(
        "/api/v1/api-monitor-mcp/mcp_api_monitor/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "search_orders",
                "arguments": {
                    "keyword": "abc",
                    "_auth": {"headers": {"Authorization": "Bearer caller-token"}},
                },
            },
        },
        headers={"Authorization": "Bearer rpamcp_secret"},
    )

    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {"success": True, "data": {"ok": True}}
```

- [ ] **Step 2: Run Gateway tests and confirm module is missing**

Run:

```bash
cd RpaClaw/backend
pytest tests/test_api_monitor_external_gateway.py -q
```

Expected: FAIL with import error for `backend.route.api_monitor_mcp_gateway`.

- [ ] **Step 3: Implement Gateway route**

Create `RpaClaw/backend/route/api_monitor_mcp_gateway.py`:

```python
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from backend.deepagent.mcp_runtime import ApiMonitorMcpRuntime
from backend.mcp.models import McpServerDefinition
from backend.rpa.api_monitor_external_access import (
    CALLER_AUTH_EXTENSION_KEY,
    CallerAuthError,
    build_caller_auth_requirements,
    build_external_tool_input_schema,
    extract_caller_auth_profile,
    verify_external_access_token,
    with_caller_auth_description,
)
from backend.storage import get_repository

router = APIRouter(tags=["api-monitor-mcp-gateway"])


def _is_json_rpc_request(body: Mapping[str, Any]) -> bool:
    return body.get("jsonrpc") == "2.0"


def _json_rpc_result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _json_rpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _tool_result_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        "structuredContent": result,
        "isError": not bool(result.get("success", True)),
    }


def _extract_external_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("X-RpaClaw-MCP-Token", "").strip()


def _is_api_monitor_mcp(doc: Mapping[str, Any]) -> bool:
    return doc.get("source_type") == "api_monitor" or doc.get("transport") == "api_monitor"


async def _load_external_server_doc(server_id: str, request: Request) -> tuple[dict[str, Any] | None, int, str]:
    repo = get_repository("user_mcp_servers")
    doc = await repo.find_one({"_id": server_id})
    if not doc or not _is_api_monitor_mcp(doc):
        return None, -32602, "API Monitor MCP not found"
    external_access = doc.get("external_access") if isinstance(doc.get("external_access"), dict) else {}
    if not external_access.get("enabled"):
        return None, -32001, "External access is disabled"
    token = _extract_external_token(request)
    if not verify_external_access_token(token, str(external_access.get("access_token_hash") or "")):
        return None, -32002, "Invalid external access token"
    return doc, 0, ""


def _server_definition(server_doc: Mapping[str, Any]) -> McpServerDefinition:
    endpoint = server_doc.get("endpoint_config") if isinstance(server_doc.get("endpoint_config"), dict) else {}
    return McpServerDefinition(
        id=str(server_doc["_id"]),
        user_id=str(server_doc.get("user_id") or ""),
        name=str(server_doc.get("name") or "API Monitor MCP"),
        description=str(server_doc.get("description") or ""),
        transport="api_monitor",
        scope="user",
        enabled=bool(server_doc.get("enabled", True)),
        default_enabled=bool(server_doc.get("default_enabled", False)),
        url=str(endpoint.get("url") or endpoint.get("base_url") or ""),
        headers=dict(endpoint.get("headers") or {}),
        timeout_ms=int(endpoint.get("timeout_ms") or 20000),
        api_monitor_auth=dict(server_doc.get("api_monitor_auth") or {}),
    )


async def _load_tool_docs(server_doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    repo = get_repository("api_monitor_mcp_tools")
    docs = await repo.find_many(
        {
            "mcp_server_id": str(server_doc["_id"]),
            "user_id": str(server_doc.get("user_id") or ""),
        },
        sort=[("order", 1)],
    )
    return [
        dict(doc)
        for doc in docs
        if doc.get("validation_status") == "valid" and str(doc.get("name") or "").strip()
    ]


def _tool_descriptor(doc: Mapping[str, Any], server_doc: Mapping[str, Any]) -> dict[str, Any]:
    requirements = build_caller_auth_requirements(server_doc.get("api_monitor_auth") or {})
    description, extension = with_caller_auth_description(str(doc.get("description") or ""), requirements)
    schema = build_external_tool_input_schema(doc.get("input_schema") or {"type": "object", "properties": {}}, requirements)
    return {
        "name": str(doc.get("name") or ""),
        "description": description,
        "inputSchema": schema,
        "input_schema": schema,
        **extension,
    }


def _initialize_result(server_doc: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": str(server_doc.get("name") or "API Monitor MCP"), "version": "0.1.0"},
        "instructions": "Provides externally callable API Monitor MCP tools. Target API credentials are supplied by the caller per tool contract.",
    }


async def _mark_last_used(server_id: str) -> None:
    repo = get_repository("user_mcp_servers")
    await repo.update_one(
        {"_id": server_id},
        {"$set": {"external_access.last_used_at": datetime.now()}},
    )


@router.post("/api-monitor-mcp/{server_id}/mcp", response_model=None)
async def api_monitor_mcp_gateway(
    server_id: str,
    body: dict[str, Any],
    request: Request,
) -> dict[str, Any] | JSONResponse | Response:
    method = body.get("method")
    params = body.get("params") or {}
    request_id = body.get("id")
    is_json_rpc = _is_json_rpc_request(body)

    server_doc, auth_error_code, auth_error_message = await _load_external_server_doc(server_id, request)
    if auth_error_code:
        return _json_rpc_error(request_id, auth_error_code, auth_error_message)
    assert server_doc is not None

    if is_json_rpc and method == "initialize":
        await _mark_last_used(server_id)
        return _json_rpc_result(request_id, _initialize_result(server_doc))
    if is_json_rpc and method == "notifications/initialized":
        return Response(status_code=202)
    if is_json_rpc and method == "ping":
        await _mark_last_used(server_id)
        return _json_rpc_result(request_id, {})
    if method == "tools/list":
        await _mark_last_used(server_id)
        result = {"tools": [_tool_descriptor(doc, server_doc) for doc in await _load_tool_docs(server_doc)]}
        return _json_rpc_result(request_id, result) if is_json_rpc else {"result": result}
    if method == "tools/call":
        tool_name = str(params.get("name") or "").strip()
        docs = await _load_tool_docs(server_doc)
        if not any(str(doc.get("name") or "") == tool_name for doc in docs):
            return _json_rpc_error(request_id, -32602, "API Monitor tool not found")
        requirements = build_caller_auth_requirements(server_doc.get("api_monitor_auth") or {})
        try:
            cleaned_arguments, caller_profile, caller_preview = extract_caller_auth_profile(
                dict(params.get("arguments") or {}),
                requirements=requirements,
                request_headers=request.headers,
            )
        except CallerAuthError as exc:
            return _json_rpc_result(
                request_id,
                _tool_result_payload({"success": False, "error": str(exc)}),
            )
        server = _server_definition(server_doc)
        result = await ApiMonitorMcpRuntime(
            server,
            caller_only=True,
            caller_profile=caller_profile,
            caller_auth_preview=caller_preview,
        ).call_tool(tool_name, cleaned_arguments)
        await _mark_last_used(server_id)
        return _json_rpc_result(request_id, _tool_result_payload(result))
    if is_json_rpc:
        return _json_rpc_error(request_id, -32601, "Unsupported MCP method")
    raise HTTPException(status_code=400, detail="Unsupported MCP method")
```

- [ ] **Step 4: Register Gateway router**

Modify `RpaClaw/backend/main.py` imports:

```python
from backend.route.api_monitor_mcp_gateway import router as api_monitor_mcp_gateway_router
```

Add router registration after `api_monitor_router`:

```python
app.include_router(api_monitor_mcp_gateway_router, prefix="/api/v1")
```

- [ ] **Step 5: Run Gateway tests**

Run:

```bash
cd RpaClaw/backend
pytest tests/test_api_monitor_external_gateway.py tests/test_api_monitor_external_access.py -q
```

Expected: PASS.

- [ ] **Step 6: Run management and runtime regressions**

Run:

```bash
cd RpaClaw/backend
pytest tests/test_mcp_route.py tests/deepagent/test_mcp_runtime.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add RpaClaw/backend/route/api_monitor_mcp_gateway.py RpaClaw/backend/main.py RpaClaw/backend/tests/test_api_monitor_external_gateway.py
git commit -m "feat: expose api monitor mcp external gateway"
```

---

## Task 6: Frontend API Types and Utility Helpers

**Files:**
- Modify: `RpaClaw/frontend/src/api/mcp.ts`
- Create: `RpaClaw/frontend/src/utils/apiMonitorExternalAccess.ts`
- Create: `RpaClaw/frontend/src/utils/apiMonitorExternalAccess.test.ts`

- [ ] **Step 1: Add failing utility tests**

Create `RpaClaw/frontend/src/utils/apiMonitorExternalAccess.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import {
  buildApiMonitorExternalClientConfig,
  formatCallerAuthRequirement,
  formatExternalAccessTokenHint,
} from './apiMonitorExternalAccess';

describe('apiMonitorExternalAccess', () => {
  it('formats placeholder caller auth requirement', () => {
    expect(formatCallerAuthRequirement({ required: false, credential_type: 'placeholder', accepted_fields: [] })).toBe(
      'placeholder: no target API credential is injected',
    );
  });

  it('formats test caller auth requirement', () => {
    expect(formatCallerAuthRequirement({ required: true, credential_type: 'test', accepted_fields: ['_auth.headers.Authorization'] })).toBe(
      'test: pass _auth.headers.Authorization on each tool call',
    );
  });

  it('formats empty token hint', () => {
    expect(formatExternalAccessTokenHint('')).toBe('not generated');
  });

  it('builds external MCP client config', () => {
    expect(
      buildApiMonitorExternalClientConfig({
        name: 'Orders API Monitor MCP',
        url: 'http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc/mcp',
        accessToken: 'rpamcp_secret',
      }),
    ).toEqual({
      name: 'Orders API Monitor MCP',
      transport: 'streamable_http',
      url: 'http://localhost:12001/api/v1/api-monitor-mcp/mcp_abc/mcp',
      headers: {
        Authorization: 'Bearer rpamcp_secret',
      },
    });
  });
});
```

- [ ] **Step 2: Run utility tests and confirm file is missing**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/utils/apiMonitorExternalAccess.test.ts
```

Expected: FAIL because `apiMonitorExternalAccess.ts` does not exist.

- [ ] **Step 3: Add frontend API types and functions**

In `RpaClaw/frontend/src/api/mcp.ts`, add after `ApiMonitorAuthConfig`:

```ts
export interface CallerAuthRequirements {
  required: boolean;
  credential_type: ApiMonitorCredentialType;
  accepted_fields: string[];
  notes?: string[];
}

export interface ApiMonitorExternalAccessState {
  enabled: boolean;
  url: string;
  token_hint: string;
  access_token?: string;
  created_at: string;
  last_rotated_at: string;
  last_used_at: string;
  require_caller_credentials: boolean;
  caller_auth_requirements: CallerAuthRequirements;
}
```

Extend `McpServerItem`:

```ts
external_access?: ApiMonitorExternalAccessState;
```

Extend `ApiMonitorMcpToolDetail`:

```ts
caller_auth_requirements?: CallerAuthRequirements;
```

Add API functions after `getApiMonitorMcpDetail`:

```ts
export async function getApiMonitorExternalAccess(serverKey: string): Promise<ApiMonitorExternalAccessState> {
  const response = await apiClient.get<ApiResponse<ApiMonitorExternalAccessState>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-external-access`,
  );
  return response.data.data;
}

export async function enableApiMonitorExternalAccess(serverKey: string): Promise<ApiMonitorExternalAccessState> {
  const response = await apiClient.post<ApiResponse<ApiMonitorExternalAccessState>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-external-access/enable`,
  );
  return response.data.data;
}

export async function rotateApiMonitorExternalAccessToken(serverKey: string): Promise<ApiMonitorExternalAccessState> {
  const response = await apiClient.post<ApiResponse<ApiMonitorExternalAccessState>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-external-access/rotate-token`,
  );
  return response.data.data;
}

export async function disableApiMonitorExternalAccess(serverKey: string): Promise<ApiMonitorExternalAccessState> {
  const response = await apiClient.post<ApiResponse<ApiMonitorExternalAccessState>>(
    `/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-external-access/disable`,
  );
  return response.data.data;
}
```

- [ ] **Step 4: Implement utility helper**

Create `RpaClaw/frontend/src/utils/apiMonitorExternalAccess.ts`:

```ts
import type { CallerAuthRequirements } from '@/api/mcp';

export type ExternalClientConfigInput = {
  name: string;
  url: string;
  accessToken: string;
};

export function formatCallerAuthRequirement(requirements?: CallerAuthRequirements | null): string {
  if (!requirements || requirements.credential_type === 'placeholder' || !requirements.required) {
    return 'placeholder: no target API credential is injected';
  }
  return 'test: pass _auth.headers.Authorization on each tool call';
}

export function formatExternalAccessTokenHint(tokenHint?: string | null): string {
  return tokenHint && tokenHint.trim() ? tokenHint : 'not generated';
}

export function buildApiMonitorExternalClientConfig(input: ExternalClientConfigInput) {
  return {
    name: input.name,
    transport: 'streamable_http',
    url: input.url,
    headers: {
      Authorization: `Bearer ${input.accessToken}`,
    },
  };
}
```

- [ ] **Step 5: Run frontend utility tests**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/utils/apiMonitorExternalAccess.test.ts
```

Expected: PASS.

- [ ] **Step 6: Run frontend type check**

Run:

```bash
cd RpaClaw/frontend
npm run type-check
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add RpaClaw/frontend/src/api/mcp.ts RpaClaw/frontend/src/utils/apiMonitorExternalAccess.ts RpaClaw/frontend/src/utils/apiMonitorExternalAccess.test.ts
git commit -m "feat: add api monitor external access frontend api"
```

---

## Task 7: Frontend External Access Panel

**Files:**
- Modify: `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`
- Modify: `RpaClaw/frontend/src/locales/en.ts`
- Modify: `RpaClaw/frontend/src/locales/zh.ts`

- [ ] **Step 1: Add imports and state**

Modify the import block in `ApiMonitorMcpDetailDialog.vue`:

```ts
import {
  Copy,
  KeyRound,
  Power,
  RefreshCw,
  ShieldCheck,
  ChevronDown,
  Loader2,
  Server,
  Save,
  Play,
  Wrench,
  Terminal,
} from 'lucide-vue-next';
```

Extend API imports:

```ts
import {
  disableApiMonitorExternalAccess,
  enableApiMonitorExternalAccess,
  getApiMonitorExternalAccess,
  getApiMonitorMcpDetail,
  rotateApiMonitorExternalAccessToken,
  testApiMonitorMcpTool,
  updateApiMonitorMcpTool,
  type ApiMonitorExternalAccessState,
  type ApiMonitorMcpDetail,
  type ApiMonitorMcpToolDetail,
  type McpServerItem,
} from '@/api/mcp';
```

Add utility import:

```ts
import {
  buildApiMonitorExternalClientConfig,
  formatCallerAuthRequirement,
  formatExternalAccessTokenHint,
} from '@/utils/apiMonitorExternalAccess';
```

Add state below `activeLoadToken`:

```ts
const externalAccess = ref<ApiMonitorExternalAccessState | null>(null);
const externalAccessOnceToken = ref('');
const externalAccessBusy = ref<'enable' | 'rotate' | 'disable' | ''>('');
```

- [ ] **Step 2: Load external access state with detail**

Update `clearDetailState`:

```ts
function clearDetailState() {
  detail.value = null;
  externalAccess.value = null;
  externalAccessOnceToken.value = '';
  expandedToolIds.value = new Set();
  resetToolStates();
}
```

Update `applyDetail`:

```ts
function applyDetail(nextDetail: ApiMonitorMcpDetail) {
  detail.value = nextDetail;
  externalAccess.value = nextDetail.server.external_access ?? null;
  resetToolStates();
  nextDetail.tools.forEach((tool) => applyToolState(tool));
  expandedToolIds.value = new Set(nextDetail.tools.length > 0 ? [nextDetail.tools[0].id] : []);
}
```

Add helper functions before `watch`:

```ts
async function refreshExternalAccessState() {
  if (!detail.value?.server.server_key) return;
  externalAccess.value = await getApiMonitorExternalAccess(detail.value.server.server_key);
}

async function enableExternalAccess() {
  if (!detail.value?.server.server_key) return;
  externalAccessBusy.value = 'enable';
  try {
    const state = await enableApiMonitorExternalAccess(detail.value.server.server_key);
    externalAccess.value = state;
    externalAccessOnceToken.value = state.access_token ?? '';
    showSuccessToast(t('API Monitor external access enabled'));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to update API Monitor external access'));
  } finally {
    externalAccessBusy.value = '';
  }
}

async function rotateExternalAccessToken() {
  if (!detail.value?.server.server_key) return;
  externalAccessBusy.value = 'rotate';
  try {
    const state = await rotateApiMonitorExternalAccessToken(detail.value.server.server_key);
    externalAccess.value = state;
    externalAccessOnceToken.value = state.access_token ?? '';
    showSuccessToast(t('API Monitor external token rotated'));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to update API Monitor external access'));
  } finally {
    externalAccessBusy.value = '';
  }
}

async function disableExternalAccess() {
  if (!detail.value?.server.server_key) return;
  externalAccessBusy.value = 'disable';
  try {
    externalAccess.value = await disableApiMonitorExternalAccess(detail.value.server.server_key);
    externalAccessOnceToken.value = '';
    showSuccessToast(t('API Monitor external access disabled'));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to update API Monitor external access'));
  } finally {
    externalAccessBusy.value = '';
  }
}

async function copyExternalText(value: string, messageKey: string) {
  if (!value) return;
  await navigator.clipboard.writeText(value);
  showSuccessToast(t(messageKey));
}

function externalClientConfigText() {
  if (!detail.value?.server.name || !externalAccess.value?.url || !externalAccessOnceToken.value) {
    return '';
  }
  return JSON.stringify(
    buildApiMonitorExternalClientConfig({
      name: detail.value.server.name,
      url: externalAccess.value.url,
      accessToken: externalAccessOnceToken.value,
    }),
    null,
    2,
  );
}
```

- [ ] **Step 3: Add UI section**

Insert this section after the MCP Overview section and before the Token Flow Summary section:

```vue
<section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
  <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
    <div>
      <div class="flex items-center gap-2">
        <ShieldCheck :size="18" class="text-teal-600 dark:text-teal-300" />
        <h3 class="text-base font-black text-[var(--text-primary)]">{{ t('External MCP Access') }}</h3>
        <span
          class="rounded-full px-2.5 py-1 text-[11px] font-bold"
          :class="externalAccess?.enabled ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300' : 'bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-300'"
        >
          {{ externalAccess?.enabled ? t('Enabled') : t('Disabled') }}
        </span>
      </div>
      <p class="mt-1 text-sm text-[var(--text-tertiary)]">
        {{ formatCallerAuthRequirement(externalAccess?.caller_auth_requirements) }}
      </p>
    </div>
    <div class="flex flex-wrap gap-2">
      <button
        v-if="!externalAccess?.enabled"
        class="inline-flex items-center gap-1.5 rounded-xl bg-teal-600 px-4 py-2 text-xs font-bold text-white shadow-sm transition disabled:cursor-not-allowed disabled:opacity-60"
        :disabled="externalAccessBusy === 'enable'"
        @click="enableExternalAccess"
      >
        <Loader2 v-if="externalAccessBusy === 'enable'" class="animate-spin" :size="14" />
        <Power v-else :size="14" />
        {{ t('Enable external access') }}
      </button>
      <button
        v-else
        class="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-[var(--text-secondary)] shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/10 dark:bg-white/[0.04] dark:hover:bg-white/[0.08]"
        :disabled="externalAccessBusy === 'disable'"
        @click="disableExternalAccess"
      >
        <Loader2 v-if="externalAccessBusy === 'disable'" class="animate-spin" :size="14" />
        <Power v-else :size="14" />
        {{ t('Disable external access') }}
      </button>
      <button
        class="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-[var(--text-secondary)] shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/10 dark:bg-white/[0.04] dark:hover:bg-white/[0.08]"
        :disabled="!externalAccess?.url"
        @click="copyExternalText(externalAccess?.url || '', 'External MCP URL copied')"
      >
        <Copy :size="14" />
        {{ t('Copy URL') }}
      </button>
      <button
        class="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-[var(--text-secondary)] shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/10 dark:bg-white/[0.04] dark:hover:bg-white/[0.08]"
        :disabled="externalAccessBusy === 'rotate'"
        @click="rotateExternalAccessToken"
      >
        <Loader2 v-if="externalAccessBusy === 'rotate'" class="animate-spin" :size="14" />
        <RefreshCw v-else :size="14" />
        {{ t('Rotate token') }}
      </button>
    </div>
  </div>

  <div class="grid gap-3 lg:grid-cols-3">
    <div class="detail-chip lg:col-span-2">
      <span class="detail-chip-label">{{ t('MCP URL') }}</span>
      <span class="break-all font-mono text-xs text-[var(--text-primary)]">{{ externalAccess?.url || '-' }}</span>
    </div>
    <div class="detail-chip">
      <span class="detail-chip-label">{{ t('Token') }}</span>
      <span class="inline-flex items-center gap-1.5 text-[var(--text-primary)]">
        <KeyRound :size="13" />
        {{ formatExternalAccessTokenHint(externalAccess?.token_hint) }}
      </span>
    </div>
  </div>

  <div v-if="externalAccessOnceToken" class="mt-4 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-500/20 dark:bg-emerald-500/10">
    <div class="mb-2 flex items-center justify-between gap-3">
      <div class="text-xs font-black uppercase tracking-[0.12em] text-emerald-700 dark:text-emerald-300">
        {{ t('One-time token') }}
      </div>
      <button class="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white" @click="copyExternalText(externalAccessOnceToken, 'External MCP token copied')">
        <Copy :size="13" />
        {{ t('Copy token') }}
      </button>
    </div>
    <pre class="overflow-auto rounded-xl bg-white p-3 font-mono text-xs text-[var(--text-primary)] dark:bg-black/20"><code>{{ externalAccessOnceToken }}</code></pre>
    <pre v-if="externalClientConfigText()" class="mt-3 overflow-auto rounded-xl bg-white p-3 font-mono text-xs text-[var(--text-primary)] dark:bg-black/20"><code>{{ externalClientConfigText() }}</code></pre>
  </div>
</section>
```

- [ ] **Step 4: Add locale keys**

Add to `RpaClaw/frontend/src/locales/en.ts`:

```ts
'External MCP Access': 'External MCP Access',
'Enable external access': 'Enable external access',
'Disable external access': 'Disable external access',
'Rotate token': 'Rotate token',
'Copy URL': 'Copy URL',
'Copy token': 'Copy token',
'MCP URL': 'MCP URL',
'One-time token': 'One-time token',
'API Monitor external access enabled': 'External access enabled',
'API Monitor external access disabled': 'External access disabled',
'API Monitor external token rotated': 'External token rotated',
'Failed to update API Monitor external access': 'Failed to update external access',
'External MCP URL copied': 'External MCP URL copied',
'External MCP token copied': 'External MCP token copied',
```

Add to `RpaClaw/frontend/src/locales/zh.ts`:

```ts
'External MCP Access': '外部 MCP 访问',
'Enable external access': '开启外部访问',
'Disable external access': '关闭外部访问',
'Rotate token': '轮换 token',
'Copy URL': '复制 URL',
'Copy token': '复制 token',
'MCP URL': 'MCP URL',
'One-time token': '一次性 token',
'API Monitor external access enabled': '外部访问已开启',
'API Monitor external access disabled': '外部访问已关闭',
'API Monitor external token rotated': '外部 token 已轮换',
'Failed to update API Monitor external access': '更新外部访问失败',
'External MCP URL copied': '外部 MCP URL 已复制',
'External MCP token copied': '外部 MCP token 已复制',
```

- [ ] **Step 5: Run frontend tests and type check**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/utils/apiMonitorExternalAccess.test.ts
npm run type-check
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue RpaClaw/frontend/src/locales/en.ts RpaClaw/frontend/src/locales/zh.ts
git commit -m "feat: add api monitor external access panel"
```

---

## Task 8: Full Verification and Manual MCP Smoke Test

**Files:**
- No production file changes expected.

- [ ] **Step 1: Run backend targeted suite**

Run:

```bash
cd RpaClaw/backend
pytest tests/test_api_monitor_external_access.py tests/test_api_monitor_external_gateway.py tests/test_mcp_route.py tests/deepagent/test_mcp_runtime.py tests/test_api_monitor_auth.py tests/test_api_monitor_token_flow.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend targeted suite**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/utils/apiMonitorExternalAccess.test.ts
npm run type-check
```

Expected: PASS.

- [ ] **Step 3: Start backend for manual smoke test**

Run:

```bash
cd RpaClaw/backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

Expected: backend starts and logs that the application is ready.

- [ ] **Step 4: Manually enable external access from the UI**

Open Tools page, select an API Monitor MCP item, open detail dialog, click “开启外部访问”.

Expected:

- The detail dialog shows an MCP URL ending in `/api/v1/api-monitor-mcp/{server_id}/mcp`.
- The one-time token is visible.
- Refreshing the dialog hides the one-time token and keeps only `token_hint`.
- Tools list does not show a second Gateway item.

- [ ] **Step 5: Manually call MCP initialize**

Use the one-time token from Step 4:

```bash
curl -s http://localhost:8000/api/v1/api-monitor-mcp/mcp_api_monitor/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer rpamcp_replace_with_real_token' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}'
```

Expected JSON contains:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "capabilities": {
      "tools": {
        "listChanged": false
      }
    }
  }
}
```

- [ ] **Step 6: Manually call tools/list**

Run:

```bash
curl -s http://localhost:8000/api/v1/api-monitor-mcp/mcp_api_monitor/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer rpamcp_replace_with_real_token' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Expected:

- For `credential_type=placeholder`, `inputSchema.required` does not include `_auth`.
- For `credential_type=test`, `inputSchema.required` includes `_auth`, and `_auth.headers.Authorization` is required.

- [ ] **Step 7: Manually call tools/call for `credential_type=test`**

Run with a real tool name and business arguments from `tools/list`:

```bash
curl -s http://localhost:8000/api/v1/api-monitor-mcp/mcp_api_monitor/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer rpamcp_replace_with_real_token' \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "search_orders",
      "arguments": {
        "keyword": "abc",
        "_auth": {
          "headers": {
            "Authorization": "Bearer caller-target-api-token"
          }
        }
      }
    }
  }'
```

Expected:

- Gateway does not reject the call for missing caller auth.
- `request_preview.headers.Authorization` is masked.
- Response never contains `caller-target-api-token` in plain text.

- [ ] **Step 8: Commit verification notes only if files changed**

If no files changed during manual testing, skip this step. If documentation was updated with manual QA notes:

```bash
git add docs/superpowers/plans/2026-04-28-api-monitor-mcp-external-access.md
git commit -m "docs: record api monitor external access verification"
```

---

## Self-Review Checklist

- Spec coverage:
  - No new item: Task 3 updates existing `user_mcp_servers` document only.
  - Derived URL: Task 2 and Task 3 build `/api/v1/api-monitor-mcp/{server_id}/mcp`.
  - External access token separation: Task 2 and Task 5 verify endpoint token separately from caller target API Authorization.
  - Credential-type-bound caller auth: Task 1, Task 3, Task 5, Task 6.
  - `placeholder` does nothing: Task 1 and Task 4.
  - `test` only accepts Authorization header: Task 1 and Task 5.
  - Caller-only runtime avoids vault and internal login: Task 4.
  - Token flow reuse with caller profile: Task 4 keeps V2 producer/consumer flow on `ApiMonitorRuntimeProfile`.
  - UI usability without extra item: Task 7 adds controls to the existing detail dialog only.

- Placeholder scan:
  - The plan intentionally uses `placeholder` only as the existing credential type name.
  - No unresolved implementation markers are present.

- Type consistency:
  - Backend uses `caller_auth_requirements`.
  - Frontend uses `CallerAuthRequirements`.
  - MCP extension key is consistently `x-rpaclaw-authRequirements`.
  - Target API request header fallback is consistently `X-RpaClaw-Target-Authorization`.
