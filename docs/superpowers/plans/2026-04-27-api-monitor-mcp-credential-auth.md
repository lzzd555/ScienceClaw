# API Monitor MCP Credential Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace API Monitor MCP manual header authentication with transient request-header profiling, persisted credential binding, and placeholder credential-type runtime dispatch.

**Architecture:** Add a small API Monitor auth helper module that builds a non-persisted header profile, validates persisted `api_monitor_auth`, and applies credential-type dispatch at runtime. Persist only `api_monitor_auth` on user MCP server documents, expose it through API Monitor publish/config/detail APIs, and update the frontend API Monitor MCP screens to choose a credential type and credential instead of editing headers.

**Tech Stack:** FastAPI, Pydantic v2, async repository abstraction, existing credential vault, httpx, Vue 3 Composition API, TypeScript, Vitest, Tailwind CSS.

---

## File Structure

Backend files:

- Create `RpaClaw/backend/rpa/api_monitor_auth.py`: transient profile builder, `api_monitor_auth` normalization/validation, placeholder runtime injector.
- Modify `RpaClaw/backend/rpa/api_monitor/models.py`: add request models for `api_monitor_auth`.
- Modify `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`: persist `api_monitor_auth` during publish and avoid saving profile data.
- Modify `RpaClaw/backend/mcp/models.py`: carry `user_id` and `api_monitor_auth` on `McpServerDefinition`.
- Modify `RpaClaw/backend/route/api_monitor.py`: add `/auth-profile`, accept `api_monitor_auth` on publish, validate credential ownership.
- Modify `RpaClaw/backend/route/mcp.py`: return and update `api_monitor_auth` in API Monitor MCP detail/config flows.
- Modify `RpaClaw/backend/deepagent/mcp_registry.py`: load `api_monitor_auth`, skip legacy credential template application when new API Monitor auth exists.
- Modify `RpaClaw/backend/deepagent/mcp_runtime.py`: invoke placeholder auth dispatch before HTTP request and include auth preview.
- Test `RpaClaw/backend/tests/test_api_monitor_auth.py`: profile, validation, placeholder dispatch.
- Test `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`: publish persistence and no `auth_profile`.
- Test `RpaClaw/backend/tests/test_mcp_route.py`: config/detail endpoint behavior.
- Test `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`: placeholder dispatch and missing credential behavior.
- Test `RpaClaw/backend/tests/deepagent/test_mcp_registry.py`: registry loading and legacy credential skip.

Frontend files:

- Modify `RpaClaw/frontend/src/api/apiMonitor.ts`: add auth profile and publish auth payload types/API call.
- Modify `RpaClaw/frontend/src/api/mcp.ts`: add `ApiMonitorAuthConfig` to server/detail/config types.
- Create `RpaClaw/frontend/src/utils/apiMonitorAuth.ts`: placeholder credential type options and display helpers.
- Create `RpaClaw/frontend/src/utils/apiMonitorAuth.test.ts`: utility tests.
- Modify `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`: load transient profile and credentials in publish dialog, submit `api_monitor_auth`.
- Modify `RpaClaw/frontend/src/components/tools/ApiMonitorMcpEditDialog.vue`: replace manual headers/query/template UI with credential type and credential selectors.
- Modify `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`: show API Monitor auth status in overview.
- Modify `RpaClaw/frontend/src/locales/en.ts` and `RpaClaw/frontend/src/locales/zh.ts`: add UI strings.

---

## Task 1: Backend Auth Profile And Config Helpers

**Files:**

- Create: `RpaClaw/backend/rpa/api_monitor_auth.py`
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_auth.py`

- [ ] **Step 1: Write failing tests for transient profile generation**

Create `RpaClaw/backend/tests/test_api_monitor_auth.py`:

```python
from datetime import datetime

import pytest

from backend.rpa.api_monitor.models import (
    ApiMonitorSession,
    ApiToolDefinition,
    CapturedApiCall,
    CapturedRequest,
)
from backend.rpa.api_monitor_auth import (
    build_api_monitor_auth_profile,
    normalize_api_monitor_auth_config,
    validate_api_monitor_auth_config,
)


class FakeVault:
    def __init__(self, values=None):
        self.values = values or {}
        self.calls = []

    async def resolve_credential_values(self, user_id: str, cred_id: str):
        self.calls.append((user_id, cred_id))
        return self.values.get(cred_id)


def _call(call_id: str, headers: dict[str, str]) -> CapturedApiCall:
    call = CapturedApiCall(
        request=CapturedRequest(
            request_id=f"req-{call_id}",
            url="https://example.test/api/orders",
            method="GET",
            headers=headers,
            timestamp=datetime.now(),
            resource_type="fetch",
        )
    )
    call.id = call_id
    return call


def _session() -> ApiMonitorSession:
    return ApiMonitorSession(
        id="session_1",
        user_id="user-1",
        sandbox_session_id="sandbox_1",
        captured_calls=[
            _call(
                "call_1",
                {
                    "Authorization": "Bearer secret-token",
                    "Accept": "application/json",
                    "Sec-Fetch-Mode": "cors",
                    "X-CSRF-Token": "csrf-secret",
                },
            ),
            _call("call_2", {"Cookie": "sid=secret-cookie", "Referer": "https://example.test"}),
            _call("call_3", {"X-Not-Used": "ignored-secret"}),
        ],
        tool_definitions=[
            ApiToolDefinition(
                id="tool_1",
                session_id="session_1",
                name="search_orders",
                description="Search orders",
                method="GET",
                url_pattern="/api/orders",
                yaml_definition="name: search_orders",
                source_calls=["call_1", "call_2"],
                selected=True,
            ),
            ApiToolDefinition(
                id="tool_2",
                session_id="session_1",
                name="ignored_tool",
                description="Ignored",
                method="GET",
                url_pattern="/api/ignored",
                yaml_definition="name: ignored_tool",
                source_calls=["call_3"],
                selected=False,
            ),
        ],
    )


def test_build_api_monitor_auth_profile_filters_noise_and_uses_selected_calls_only():
    profile = build_api_monitor_auth_profile(_session())

    names = [item["name"] for item in profile["headers"]]
    assert names == ["authorization", "cookie", "x-csrf-token"]
    assert profile["header_count"] == 3
    assert profile["sensitive_header_count"] == 3
    assert profile["recommended_credential_type"] == "placeholder"
    assert profile["headers"][0]["tools"] == ["search_orders"]


def test_build_api_monitor_auth_profile_does_not_include_real_values():
    profile = build_api_monitor_auth_profile(_session())

    serialized = str(profile)
    assert "secret-token" not in serialized
    assert "secret-cookie" not in serialized
    assert "csrf-secret" not in serialized
    assert "Bearer ***" in serialized
```

- [ ] **Step 2: Write failing tests for config validation**

Append to `RpaClaw/backend/tests/test_api_monitor_auth.py`:

```python
def test_normalize_api_monitor_auth_config_accepts_placeholder():
    assert normalize_api_monitor_auth_config(
        {"credential_type": "placeholder", "credential_id": " cred_1 "}
    ) == {"credential_type": "placeholder", "credential_id": "cred_1"}


def test_normalize_api_monitor_auth_config_rejects_unknown_type():
    with pytest.raises(ValueError, match="api_monitor_auth.credential_type"):
        normalize_api_monitor_auth_config({"credential_type": "bearer_token", "credential_id": "cred_1"})


@pytest.mark.anyio
async def test_validate_api_monitor_auth_config_rejects_missing_credential():
    with pytest.raises(ValueError, match="references a missing credential"):
        await validate_api_monitor_auth_config(
            "user-1",
            {"credential_type": "placeholder", "credential_id": "missing"},
            vault=FakeVault({}),
        )


@pytest.mark.anyio
async def test_validate_api_monitor_auth_config_accepts_empty_credential_id():
    config = await validate_api_monitor_auth_config(
        "user-1",
        {"credential_type": "placeholder", "credential_id": ""},
        vault=FakeVault({}),
    )

    assert config == {"credential_type": "placeholder", "credential_id": ""}
```

- [ ] **Step 3: Run tests and confirm they fail because the helper module does not exist**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_auth.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rpa.api_monitor_auth'`.

- [ ] **Step 4: Implement the helper module**

Create `RpaClaw/backend/rpa/api_monitor_auth.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from backend.credential.vault import get_vault
from backend.rpa.api_monitor.models import ApiMonitorSession


PLACEHOLDER_CREDENTIAL_TYPE = "placeholder"
ALLOWED_API_MONITOR_CREDENTIAL_TYPES = {PLACEHOLDER_CREDENTIAL_TYPE}

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
    if not credential_type and not credential_id:
        return {}
    if credential_type not in ALLOWED_API_MONITOR_CREDENTIAL_TYPES:
        allowed = ", ".join(sorted(ALLOWED_API_MONITOR_CREDENTIAL_TYPES))
        raise ValueError(f"api_monitor_auth.credential_type must be one of: {allowed}")
    return {"credential_type": credential_type, "credential_id": credential_id}


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
```

- [ ] **Step 5: Add request models**

Modify `RpaClaw/backend/rpa/api_monitor/models.py`:

```python
from typing import Any, Dict, List, Literal, Optional
```

Add before `PublishMcpRequest`:

```python
class ApiMonitorAuthConfigRequest(BaseModel):
    credential_type: str = "placeholder"
    credential_id: str = ""
```

Change `PublishMcpRequest`:

```python
class PublishMcpRequest(BaseModel):
    mcp_name: str
    description: str = ""
    confirm_overwrite: bool = False
    api_monitor_auth: Optional[ApiMonitorAuthConfigRequest] = None
```

- [ ] **Step 6: Run helper tests and commit**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_auth.py -q
```

Expected: PASS.

Commit:

```bash
git add RpaClaw/backend/rpa/api_monitor_auth.py RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/tests/test_api_monitor_auth.py
git commit -m "feat: add api monitor auth profile helpers"
```

---

## Task 2: Persist `api_monitor_auth` Through Publish And Config APIs

**Files:**

- Modify: `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`
- Modify: `RpaClaw/backend/route/api_monitor.py`
- Modify: `RpaClaw/backend/route/mcp.py`
- Modify: `RpaClaw/backend/mcp/models.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`
- Test: `RpaClaw/backend/tests/test_mcp_route.py`

- [ ] **Step 1: Write failing publish tests**

Append to `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`:

```python
@pytest.mark.anyio
async def test_publish_persists_api_monitor_auth_without_auth_profile():
    server_repo = _MemoryRepo([])
    tool_repo = _MemoryRepo([])
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    await registry.publish_session(
        session=_build_session(),
        user_id="user_1",
        mcp_name="Orders MCP",
        description="Order APIs",
        overwrite=False,
        api_monitor_auth={"credential_type": "placeholder", "credential_id": "cred_1"},
    )

    server = list(server_repo.docs.values())[0]
    assert server["api_monitor_auth"] == {"credential_type": "placeholder", "credential_id": "cred_1"}
    assert "auth_profile" not in server
    assert all("auth_profile" not in tool for tool in tool_repo.docs.values())


@pytest.mark.anyio
async def test_publish_with_api_monitor_auth_clears_legacy_auth_config():
    server_repo = _MemoryRepo(
        [
            {
                "_id": "mcp_existing",
                "user_id": "user_1",
                "name": "Orders MCP",
                "description": "Old",
                "transport": "api_monitor",
                "source_type": "api_monitor",
                "endpoint_config": {
                    "base_url": "https://api.example.test",
                    "headers": {"Authorization": "Bearer {{ orders.password }}"},
                    "query": {"api_key": "{{ orders.password }}"},
                    "timeout_ms": 15000,
                },
                "credential_binding": {
                    "credentials": [{"alias": "orders", "credential_id": "cred_old"}],
                    "headers": {"Authorization": "Bearer {{ orders.password }}"},
                    "query": {"api_key": "{{ orders.password }}"},
                },
            }
        ]
    )
    tool_repo = _MemoryRepo([])
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    await registry.publish_session(
        session=_build_session(),
        user_id="user_1",
        mcp_name="Orders MCP",
        description="New",
        overwrite=True,
        existing_server_id="mcp_existing",
        api_monitor_auth={"credential_type": "placeholder", "credential_id": ""},
    )

    server = server_repo.docs["mcp_existing"]
    assert server["endpoint_config"] == {"base_url": "https://api.example.test", "timeout_ms": 15000}
    assert server["credential_binding"] == {}
    assert server["api_monitor_auth"] == {"credential_type": "placeholder", "credential_id": ""}
```

- [ ] **Step 2: Write failing route tests**

Append to `RpaClaw/backend/tests/test_mcp_route.py`:

```python
def test_api_monitor_detail_returns_api_monitor_auth(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([
        _api_monitor_server_doc(api_monitor_auth={"credential_type": "placeholder", "credential_id": "cred_1"})
    ])
    tool_repo = _MemoryRepo([_api_monitor_tool_doc()])

    def fake_get_repository(collection_name):
        return server_repo if collection_name == "user_mcp_servers" else tool_repo

    monkeypatch.setattr(mcp_route, "get_repository", fake_get_repository)

    response = client.get("/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-detail")

    assert response.status_code == 200
    assert response.json()["data"]["server"]["api_monitor_auth"] == {
        "credential_type": "placeholder",
        "credential_id": "cred_1",
    }


def test_update_api_monitor_mcp_config_saves_api_monitor_auth_and_clears_legacy_auth(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([
        _api_monitor_server_doc(
            endpoint_config={"headers": {"Authorization": "old"}, "query": {"api_key": "old"}, "timeout_ms": 10000},
            credential_binding={"headers": {"Authorization": "{{ credential.password }}"}, "query": {"api_key": "{{ credential.password }}"}},
        )
    ])

    class _Vault:
        async def resolve_credential_values(self, user_id: str, cred_id: str):
            return {"username": "", "password": "secret", "domain": ""}

    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: server_repo)
    monkeypatch.setattr(mcp_route, "get_vault", lambda: _Vault())

    response = client.put(
        "/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-config",
        json={
            "endpoint_config": {"timeout_ms": 30000},
            "api_monitor_auth": {"credential_type": "placeholder", "credential_id": "cred_1"},
        },
    )

    assert response.status_code == 200
    updated = server_repo.docs["mcp_api_monitor"]
    assert updated["api_monitor_auth"] == {"credential_type": "placeholder", "credential_id": "cred_1"}
    assert updated["endpoint_config"] == {"timeout_ms": 30000}
    assert updated["credential_binding"] == {}


def test_update_api_monitor_mcp_config_rejects_unknown_credential_type(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_api_monitor_server_doc()])
    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: server_repo)

    response = client.put(
        "/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-config",
        json={"api_monitor_auth": {"credential_type": "bearer_token", "credential_id": ""}},
    )

    assert response.status_code == 400
    assert "api_monitor_auth.credential_type" in response.json()["detail"]
```

- [ ] **Step 3: Run route/publish tests and confirm failure**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_publish_mcp.py tests/test_mcp_route.py -q
```

Expected: FAIL because `api_monitor_auth` is not serialized, accepted, or persisted yet.

- [ ] **Step 4: Extend MCP server models**

Modify `RpaClaw/backend/mcp/models.py`:

```python
class McpServerDefinition(BaseModel):
    id: str
    user_id: str = ""
    name: str
    description: str = ""
    transport: McpTransport
    scope: McpScope = "system"
    enabled: bool = True
    default_enabled: bool = False
    url: str = ""
    command: str = ""
    args: List[str] = Field(default_factory=list)
    cwd: str = ""
    headers: Dict[str, str] = Field(default_factory=dict)
    env: Dict[str, str] = Field(default_factory=dict)
    timeout_ms: int = 20000
    credential_ref: str = ""
    credential_binding: McpCredentialBinding = Field(default_factory=McpCredentialBinding)
    api_monitor_auth: Dict[str, Any] = Field(default_factory=dict)
    tool_policy: McpToolPolicy = Field(default_factory=McpToolPolicy)
```

- [ ] **Step 5: Persist auth in registry**

Modify `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`:

```python
from backend.rpa.api_monitor_auth import normalize_api_monitor_auth_config
```

Change `publish_session` signature:

```python
async def publish_session(
    self,
    *,
    session: ApiMonitorSession,
    user_id: str,
    mcp_name: str,
    description: str,
    overwrite: bool,
    existing_server_id: str | None = None,
    api_monitor_auth: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

Inside `publish_session`, before `server_doc`:

```python
normalized_auth = normalize_api_monitor_auth_config(api_monitor_auth)
endpoint_config = _api_monitor_endpoint_config_without_legacy_auth((existing_server or {}).get("endpoint_config") or {})
credential_binding = {} if normalized_auth else (existing_server or {}).get("credential_binding") or {}
```

In `server_doc`, set:

```python
"endpoint_config": endpoint_config,
"credential_binding": credential_binding,
"api_monitor_auth": normalized_auth,
```

Add helper at module bottom:

```python
def _api_monitor_endpoint_config_without_legacy_auth(endpoint_config: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(endpoint_config)
    cleaned.pop("headers", None)
    cleaned.pop("query", None)
    return cleaned
```

- [ ] **Step 6: Add auth profile and publish route wiring**

Modify `RpaClaw/backend/route/api_monitor.py` imports:

```python
from backend.rpa.api_monitor_auth import build_api_monitor_auth_profile, validate_api_monitor_auth_config
from backend.credential.vault import get_vault
```

Add endpoint before `publish_mcp`:

```python
@router.get("/session/{session_id}/auth-profile")
async def get_auth_profile(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    return {"status": "success", "profile": build_api_monitor_auth_profile(session)}
```

In `publish_mcp`, before `registry.publish_session`:

```python
auth_payload = (
    request.api_monitor_auth.model_dump()
    if request.api_monitor_auth is not None
    else {"credential_type": "placeholder", "credential_id": ""}
)
try:
    api_monitor_auth = await validate_api_monitor_auth_config(
        str(current_user.id),
        auth_payload,
        vault=get_vault(),
    )
except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
```

Pass into registry:

```python
api_monitor_auth=api_monitor_auth,
```

- [ ] **Step 7: Add config/detail route wiring**

Modify `RpaClaw/backend/route/mcp.py` imports:

```python
from backend.credential.vault import get_vault
from backend.rpa.api_monitor_auth import validate_api_monitor_auth_config
```

Add this field inside `McpServerListItem`:

```python
api_monitor_auth: Dict[str, Any] = Field(default_factory=dict)
```

Add this field inside `ApiMonitorMcpConfigUpdate`:

```python
api_monitor_auth: Dict[str, Any] | None = None
```

In `_serialize_user_server` and `_serialize_api_monitor_user_server`, include:

```python
api_monitor_auth=doc.get("api_monitor_auth") or {},
```

Change `_to_server_definition` to accept user id:

```python
def _to_server_definition(server: Dict[str, Any], *, user_id: str = ""):
    endpoint = server.get("endpoint_config") or {}
    return McpServerDefinition(
        id=server["id"],
        user_id=user_id,
        name=server["name"],
        description=server.get("description", ""),
        transport=server["transport"],
        scope=server["scope"],
        enabled=server.get("enabled", True),
        default_enabled=server.get("default_enabled", False),
        url=endpoint.get("url") or endpoint.get("base_url", ""),
        command=endpoint.get("command", ""),
        args=endpoint.get("args", []),
        cwd=endpoint.get("cwd", ""),
        headers=endpoint.get("headers", {}),
        env=endpoint.get("env", {}),
        timeout_ms=endpoint.get("timeout_ms", 20000),
        credential_binding=server.get("credential_binding") or {},
        api_monitor_auth=server.get("api_monitor_auth") or {},
        tool_policy=server.get("tool_policy") or {},
    )
```

Update callers:

```python
definition = _to_server_definition(server, user_id=user_id)
```

In `update_api_monitor_mcp_config`, build auth update:

```python
auth_field_set = "api_monitor_auth" in body.model_fields_set
api_monitor_auth = server_doc.get("api_monitor_auth") or {}
if auth_field_set:
    try:
        api_monitor_auth = await validate_api_monitor_auth_config(
            user_id,
            body.api_monitor_auth,
            vault=get_vault(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

When auth field is set, clear old auth templates:

```python
endpoint_config = _api_monitor_config_dict_value(body, server_doc, "endpoint_config")
credential_binding = _api_monitor_config_dict_value(body, server_doc, "credential_binding")
if auth_field_set:
    endpoint_config.pop("headers", None)
    endpoint_config.pop("query", None)
    credential_binding = {}
```

Set:

```python
"endpoint_config": endpoint_config,
"credential_binding": credential_binding,
"api_monitor_auth": api_monitor_auth,
```

In `test_api_monitor_tool`, construct server with user id:

```python
server = _to_server_definition(_serialize_api_monitor_user_server(server_doc), user_id=user_id)
```

- [ ] **Step 8: Run route/publish tests and commit**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_auth.py tests/test_api_monitor_publish_mcp.py tests/test_mcp_route.py -q
```

Expected: PASS.

Commit:

```bash
git add RpaClaw/backend/rpa/api_monitor_mcp_registry.py RpaClaw/backend/route/api_monitor.py RpaClaw/backend/route/mcp.py RpaClaw/backend/mcp/models.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py RpaClaw/backend/tests/test_mcp_route.py
git commit -m "feat: persist api monitor mcp auth config"
```

---

## Task 3: Runtime Dispatch And Effective MCP Registry

**Files:**

- Modify: `RpaClaw/backend/deepagent/mcp_runtime.py`
- Modify: `RpaClaw/backend/deepagent/mcp_registry.py`
- Test: `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`
- Test: `RpaClaw/backend/tests/deepagent/test_mcp_registry.py`

- [ ] **Step 1: Write failing runtime tests**

Append to `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`:

```python
def test_api_monitor_placeholder_auth_does_not_inject_credentials(monkeypatch):
    api_client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: api_client)
    monkeypatch.setattr(
        mcp_runtime,
        "get_repository",
        lambda collection_name: _MemoryRepo([
            {
                "_id": "tool_1",
                "mcp_server_id": "mcp_api_monitor",
                "name": "search_orders",
                "description": "Search orders",
                "method": "GET",
                "url": "/api/orders",
                "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
                "query_mapping": {"keyword": "{{ keyword }}"},
                "body_mapping": {},
                "header_mapping": {},
                "path_mapping": {},
                "validation_status": "valid",
            }
        ]),
    )

    class _Vault:
        async def resolve_credential_values(self, user_id: str, cred_id: str):
            return {"username": "alice", "password": "secret", "domain": ""}

    monkeypatch.setattr(mcp_runtime, "get_vault", lambda: _Vault())
    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="user-1",
        name="API Monitor",
        transport="api_monitor",
        scope="user",
        url="https://api.example.test",
        api_monitor_auth={"credential_type": "placeholder", "credential_id": "cred_1"},
    )

    result = asyncio.run(mcp_runtime.ApiMonitorMcpRuntime(server).call_tool("search_orders", {"keyword": "abc"}))

    assert result["success"] is True
    method, url, kwargs = api_client.calls[0]
    assert method == "GET"
    assert url == "https://api.example.test/api/orders"
    assert kwargs["params"] == {"keyword": "abc"}
    assert kwargs["headers"] == {}
    assert "secret" not in str(kwargs)
    assert result["request_preview"]["auth"] == {
        "credential_type": "placeholder",
        "credential_configured": True,
        "injected": False,
    }


def test_api_monitor_missing_configured_credential_returns_error_without_http(monkeypatch):
    api_client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: api_client)
    monkeypatch.setattr(
        mcp_runtime,
        "get_repository",
        lambda collection_name: _MemoryRepo([
            {
                "_id": "tool_1",
                "mcp_server_id": "mcp_api_monitor",
                "name": "search_orders",
                "description": "Search orders",
                "method": "GET",
                "url": "/api/orders",
                "input_schema": {"type": "object", "properties": {}},
                "validation_status": "valid",
            }
        ]),
    )

    class _Vault:
        async def resolve_credential_values(self, user_id: str, cred_id: str):
            return None

    monkeypatch.setattr(mcp_runtime, "get_vault", lambda: _Vault())
    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="user-1",
        name="API Monitor",
        transport="api_monitor",
        scope="user",
        url="https://api.example.test",
        api_monitor_auth={"credential_type": "placeholder", "credential_id": "missing"},
    )

    result = asyncio.run(mcp_runtime.ApiMonitorMcpRuntime(server).call_tool("search_orders", {}))

    assert result == {"success": False, "error": "API Monitor credential not found"}
    assert api_client.calls == []
```

- [ ] **Step 2: Write failing registry tests**

Append to `RpaClaw/backend/tests/deepagent/test_mcp_registry.py`:

```python
def test_load_user_api_monitor_mcp_includes_api_monitor_auth(monkeypatch):
    repo = _Repo([
        {
            "_id": "mcp_api_monitor",
            "user_id": "u1",
            "name": "API Monitor",
            "description": "Captured APIs",
            "transport": "api_monitor",
            "enabled": True,
            "default_enabled": True,
            "source_type": "api_monitor",
            "endpoint_config": {"base_url": "https://api.example.test"},
            "api_monitor_auth": {"credential_type": "placeholder", "credential_id": "cred_1"},
        }
    ])
    monkeypatch.setattr(mcp_registry, "get_repository", lambda _: repo)

    servers = asyncio.run(mcp_registry._load_user_mcp_servers("u1"))

    assert servers[0].user_id == "u1"
    assert servers[0].api_monitor_auth == {"credential_type": "placeholder", "credential_id": "cred_1"}


def test_build_effective_mcp_servers_skips_legacy_credential_resolution_for_new_api_monitor_auth(monkeypatch):
    api_server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="u1",
        name="API Monitor",
        transport="api_monitor",
        scope="user",
        enabled=True,
        default_enabled=True,
        api_monitor_auth={"credential_type": "placeholder", "credential_id": "cred_1"},
    )

    async def fake_user_servers(user_id: str):
        return [api_server]

    async def fake_bindings(session_id: str, user_id: str):
        return {}

    async def exploding_apply(server, user_id):
        raise AssertionError("new API Monitor auth should not use legacy credential templates")

    monkeypatch.setattr(mcp_registry, "load_system_mcp_servers", lambda: [])
    monkeypatch.setattr(mcp_registry, "_load_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr(mcp_registry, "_load_session_mcp_bindings", fake_bindings)
    monkeypatch.setattr(mcp_registry, "apply_mcp_credentials", exploding_apply)

    servers = asyncio.run(mcp_registry.build_effective_mcp_servers("s1", "u1"))

    assert servers == [api_server]
```

If `test_mcp_registry.py` does not already expose `_Repo`, add this local helper near the other in-memory repository helpers:

```python
class _Repo:
    def __init__(self, docs):
        self.docs = [dict(doc) for doc in docs]

    async def find_many(self, filter_doc, projection=None, sort=None, skip=0, limit=0):
        return [
            dict(doc)
            for doc in self.docs
            if all(doc.get(key) == value for key, value in filter_doc.items())
        ]
```

- [ ] **Step 3: Run runtime/registry tests and confirm failure**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/deepagent/test_mcp_runtime.py tests/deepagent/test_mcp_registry.py -q
```

Expected: FAIL because runtime does not dispatch `api_monitor_auth` and registry does not load/skip legacy auth yet.

- [ ] **Step 4: Implement runtime dispatch**

Modify `RpaClaw/backend/deepagent/mcp_runtime.py` imports:

```python
from backend.credential.vault import get_vault
from backend.rpa.api_monitor_auth import apply_api_monitor_auth_to_request
```

In `ApiMonitorMcpRuntime.call_tool`, split legacy base auth from new API Monitor auth:

```python
has_api_monitor_auth = bool(self._server.api_monitor_auth)
request_query = _api_monitor_base_query(self._server) if not has_api_monitor_auth else {}
request_query.update(render_mapping(query_mapping, rendered_arguments))
request_headers: dict[str, Any] = dict(self._server.headers) if not has_api_monitor_auth else {}
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
json_body = request_body or None
```

In the returned `request_preview`, include auth:

```python
"auth": auth_application.preview,
```

- [ ] **Step 5: Implement registry loading and credential skip**

Modify `RpaClaw/backend/deepagent/mcp_registry.py` inside `_load_user_mcp_servers`:

```python
servers.append(McpServerDefinition(
    id=str(doc["_id"]),
    user_id=str(doc.get("user_id") or user_id),
    name=doc["name"],
    description=doc.get("description", ""),
    transport=doc["transport"],
    scope="user",
    enabled=doc.get("enabled", True),
    default_enabled=doc.get("default_enabled", False),
    url=endpoint_url,
    command=endpoint.get("command", ""),
    args=endpoint.get("args", []),
    cwd=endpoint.get("cwd", ""),
    headers=endpoint.get("headers", {}),
    env=endpoint.get("env", {}),
    timeout_ms=endpoint.get("timeout_ms", default_timeout_ms),
    credential_binding=doc.get("credential_binding") or {},
    api_monitor_auth=doc.get("api_monitor_auth") or {},
    tool_policy=doc.get("tool_policy", {}),
))
```

Add helper:

```python
def _should_apply_legacy_mcp_credentials(server: McpServerDefinition) -> bool:
    return not (server.transport == "api_monitor" and bool(server.api_monitor_auth))
```

Change `build_effective_mcp_servers`:

```python
if server.scope == "user" and _should_apply_legacy_mcp_credentials(server):
    try:
        server = await apply_mcp_credentials(server, user_id)
    except McpCredentialResolutionError as exc:
        logger.warning("Skipping MCP server %s because credentials failed: %s", server.id, exc)
        continue
```

- [ ] **Step 6: Run runtime/registry tests and commit**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_auth.py tests/deepagent/test_mcp_runtime.py tests/deepagent/test_mcp_registry.py -q
```

Expected: PASS.

Commit:

```bash
git add RpaClaw/backend/deepagent/mcp_runtime.py RpaClaw/backend/deepagent/mcp_registry.py RpaClaw/backend/tests/deepagent/test_mcp_runtime.py RpaClaw/backend/tests/deepagent/test_mcp_registry.py
git commit -m "feat: dispatch api monitor mcp auth at runtime"
```

---

## Task 4: Frontend API Types And Publish Dialog

**Files:**

- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts`
- Modify: `RpaClaw/frontend/src/api/mcp.ts`
- Create: `RpaClaw/frontend/src/utils/apiMonitorAuth.ts`
- Create: `RpaClaw/frontend/src/utils/apiMonitorAuth.test.ts`
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`

- [ ] **Step 1: Write failing utility tests**

Create `RpaClaw/frontend/src/utils/apiMonitorAuth.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';

import {
  API_MONITOR_CREDENTIAL_TYPE_OPTIONS,
  formatApiMonitorAuthStatus,
  normalizeApiMonitorAuth,
} from './apiMonitorAuth';

describe('API Monitor auth helpers', () => {
  it('exposes the placeholder credential type', () => {
    expect(API_MONITOR_CREDENTIAL_TYPE_OPTIONS).toEqual([
      {
        value: 'placeholder',
        labelKey: 'API Monitor Placeholder credential type',
        descriptionKey: 'API Monitor Placeholder credential type hint',
      },
    ]);
  });

  it('normalizes empty auth to placeholder with no credential', () => {
    expect(normalizeApiMonitorAuth(undefined)).toEqual({
      credential_type: 'placeholder',
      credential_id: '',
    });
  });

  it('formats configured and unconfigured status', () => {
    expect(formatApiMonitorAuthStatus({ credential_type: 'placeholder', credential_id: 'cred_1' })).toBe('configured');
    expect(formatApiMonitorAuthStatus({ credential_type: 'placeholder', credential_id: '' })).toBe('missing_credential');
  });
});
```

- [ ] **Step 2: Run frontend utility test and confirm failure**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/utils/apiMonitorAuth.test.ts
```

Expected: FAIL because `apiMonitorAuth.ts` does not exist.

- [ ] **Step 3: Add frontend API auth types**

Modify `RpaClaw/frontend/src/api/apiMonitor.ts`:

```typescript
export type ApiMonitorCredentialType = 'placeholder'

export interface ApiMonitorAuthConfig {
  credential_type: ApiMonitorCredentialType
  credential_id: string
}

export interface ApiMonitorAuthProfileHeader {
  name: string
  display_name: string
  occurrences: number
  tools: string[]
  signals: string[]
  masked_example: string
}

export interface ApiMonitorAuthProfile {
  header_count: number
  sensitive_header_count: number
  headers: ApiMonitorAuthProfileHeader[]
  recommended_credential_type: ApiMonitorCredentialType
}
```

Change `PublishMcpPayload`:

```typescript
export interface PublishMcpPayload {
  mcp_name: string
  description: string
  confirm_overwrite: boolean
  api_monitor_auth?: ApiMonitorAuthConfig
}
```

Add API function:

```typescript
export async function getAuthProfile(sessionId: string): Promise<ApiMonitorAuthProfile> {
  const response = await apiClient.get(`/api-monitor/session/${sessionId}/auth-profile`)
  return response.data.profile
}
```

Modify `RpaClaw/frontend/src/api/mcp.ts`:

```typescript
export type ApiMonitorCredentialType = 'placeholder';

export interface ApiMonitorAuthConfig {
  credential_type: ApiMonitorCredentialType;
  credential_id: string;
}
```

Add to `McpServerItem`:

```typescript
api_monitor_auth?: ApiMonitorAuthConfig;
```

Add to `ApiMonitorMcpConfigPayload`:

```typescript
api_monitor_auth?: ApiMonitorAuthConfig;
```

- [ ] **Step 4: Implement frontend auth utility**

Create `RpaClaw/frontend/src/utils/apiMonitorAuth.ts`:

```typescript
import type { ApiMonitorAuthConfig, ApiMonitorCredentialType } from '@/api/mcp';

export const API_MONITOR_PLACEHOLDER_CREDENTIAL_TYPE: ApiMonitorCredentialType = 'placeholder';

export const API_MONITOR_CREDENTIAL_TYPE_OPTIONS = [
  {
    value: API_MONITOR_PLACEHOLDER_CREDENTIAL_TYPE,
    labelKey: 'API Monitor Placeholder credential type',
    descriptionKey: 'API Monitor Placeholder credential type hint',
  },
] as const;

export function normalizeApiMonitorAuth(value?: Partial<ApiMonitorAuthConfig> | null): ApiMonitorAuthConfig {
  return {
    credential_type: value?.credential_type || API_MONITOR_PLACEHOLDER_CREDENTIAL_TYPE,
    credential_id: value?.credential_id || '',
  };
}

export function formatApiMonitorAuthStatus(value?: Partial<ApiMonitorAuthConfig> | null): 'configured' | 'missing_credential' {
  const normalized = normalizeApiMonitorAuth(value);
  return normalized.credential_id ? 'configured' : 'missing_credential';
}
```

- [ ] **Step 5: Update API Monitor publish dialog script**

Modify imports in `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`:

```typescript
import {
  getAuthProfile,
  publishMcpToolBundle,
  type ApiMonitorAuthConfig,
  type ApiMonitorAuthProfile,
} from '@/api/apiMonitor';
import { listCredentials, type Credential } from '@/api/credential';
import { API_MONITOR_CREDENTIAL_TYPE_OPTIONS, normalizeApiMonitorAuth } from '@/utils/apiMonitorAuth';
```

Add state near publish form state:

```typescript
const authProfile = ref<ApiMonitorAuthProfile | null>(null);
const publishCredentials = ref<Credential[]>([]);
const isLoadingAuthProfile = ref(false);
const publishAuth = reactive<ApiMonitorAuthConfig>({
  credential_type: 'placeholder',
  credential_id: '',
});
```

Change `openPublishDialog`:

```typescript
const openPublishDialog = async () => {
  if (!sessionId.value || !adoptedToolCount.value) return;
  publishForm.mcpName = publishForm.mcpName || getDefaultMcpName();
  publishForm.description = publishForm.description || session.value?.target_url || urlInput.value || '';
  publishAuth.credential_type = 'placeholder';
  publishAuth.credential_id = '';
  publishDialogOpen.value = true;
  isLoadingAuthProfile.value = true;
  try {
    const [profile, creds] = await Promise.all([
      getAuthProfile(sessionId.value),
      listCredentials(),
    ]);
    authProfile.value = profile;
    publishCredentials.value = creds;
    publishAuth.credential_type = profile.recommended_credential_type || 'placeholder';
  } catch (err: any) {
    authProfile.value = null;
    publishCredentials.value = [];
    addLog('ERROR', `加载认证配置失败: ${err.message}`);
  } finally {
    isLoadingAuthProfile.value = false;
  }
};
```

Change publish payload:

```typescript
const result = await publishMcpToolBundle(sessionId.value, {
  mcp_name: publishForm.mcpName.trim(),
  description: publishForm.description.trim(),
  confirm_overwrite: confirmOverwrite,
  api_monitor_auth: normalizeApiMonitorAuth(publishAuth),
});
```

- [ ] **Step 6: Update API Monitor publish dialog template**

Inside the publish dialog body in `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`, after description field, add:

```vue
<section class="rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-white/[0.04]">
  <div class="mb-3 flex items-center justify-between gap-3">
    <div>
      <h3 class="text-sm font-black text-[var(--text-primary)]">认证配置</h3>
      <p class="mt-1 text-xs leading-5 text-[var(--text-tertiary)]">
        请求头画像仅用于本次判断，不会保存到 MCP。
      </p>
    </div>
    <Loader2 v-if="isLoadingAuthProfile" class="animate-spin text-sky-500" :size="16" />
  </div>
  <div v-if="authProfile" class="mb-4 rounded-xl bg-slate-50 px-3 py-2 text-xs text-[var(--text-secondary)] dark:bg-white/5">
    检测到 {{ authProfile.sensitive_header_count }} 个疑似认证请求头，推荐使用占位凭证类型。
  </div>
  <label class="mb-3 flex flex-col gap-2">
    <span class="text-sm font-bold text-[var(--text-secondary)]">凭证类型</span>
    <select v-model="publishAuth.credential_type" class="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-[var(--text-primary)] outline-none transition focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 dark:border-white/10 dark:bg-white/5">
      <option v-for="option in API_MONITOR_CREDENTIAL_TYPE_OPTIONS" :key="option.value" :value="option.value">
        占位符
      </option>
    </select>
  </label>
  <label class="flex flex-col gap-2">
    <span class="text-sm font-bold text-[var(--text-secondary)]">凭证</span>
    <select v-model="publishAuth.credential_id" class="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-[var(--text-primary)] outline-none transition focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 dark:border-white/10 dark:bg-white/5">
      <option value="">暂不配置凭证</option>
      <option v-for="credential in publishCredentials" :key="credential.id" :value="credential.id">
        {{ credential.name }} ({{ credential.username || credential.domain || credential.id }})
      </option>
    </select>
  </label>
</section>
```

- [ ] **Step 7: Run frontend tests and commit**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/utils/apiMonitorAuth.test.ts src/utils/apiMonitorMcp.test.ts
```

Expected: PASS.

Commit:

```bash
git add RpaClaw/frontend/src/api/apiMonitor.ts RpaClaw/frontend/src/api/mcp.ts RpaClaw/frontend/src/utils/apiMonitorAuth.ts RpaClaw/frontend/src/utils/apiMonitorAuth.test.ts RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue
git commit -m "feat: configure api monitor auth on publish"
```

---

## Task 5: Frontend Tools Page API Monitor Config UI

**Files:**

- Modify: `RpaClaw/frontend/src/components/tools/ApiMonitorMcpEditDialog.vue`
- Modify: `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`
- Modify: `RpaClaw/frontend/src/locales/en.ts`
- Modify: `RpaClaw/frontend/src/locales/zh.ts`

- [ ] **Step 1: Replace edit dialog form state**

Modify imports in `RpaClaw/frontend/src/components/tools/ApiMonitorMcpEditDialog.vue`:

```typescript
import { Loader2, Pencil, ShieldCheck } from 'lucide-vue-next';
import { API_MONITOR_CREDENTIAL_TYPE_OPTIONS, normalizeApiMonitorAuth } from '@/utils/apiMonitorAuth';
```

Remove imports from `@/utils/mcpUi`.

Change form:

```typescript
const form = reactive({
  name: '',
  description: '',
  credentialType: 'placeholder' as 'placeholder',
  credentialId: '',
  timeoutMs: 20000,
});
```

Change `populateFromServer`:

```typescript
function populateFromServer(server: McpServerItem) {
  form.name = server.name || '';
  form.description = server.description || '';
  const endpointConfig = isPlainObject(server.endpoint_config) ? server.endpoint_config : {};
  const auth = normalizeApiMonitorAuth(server.api_monitor_auth);
  form.credentialType = auth.credential_type;
  form.credentialId = auth.credential_id;
  form.timeoutMs = (endpointConfig.timeout_ms as number) || 20000;
}
```

Remove `addCredentialBinding` and `removeCredentialBinding`.

Change `save` payload:

```typescript
const result = await updateApiMonitorMcpConfig(props.server.server_key, {
  name: form.name,
  description: form.description,
  endpoint_config: {
    timeout_ms: form.timeoutMs,
  },
  api_monitor_auth: {
    credential_type: form.credentialType,
    credential_id: form.credentialId,
  },
});
```

- [ ] **Step 2: Replace authentication template**

In `ApiMonitorMcpEditDialog.vue`, replace the entire `<!-- Authentication -->` section with:

```vue
<section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
  <div class="mb-4 flex items-center gap-3">
    <ShieldCheck :size="18" class="text-violet-600 dark:text-violet-300" />
    <div>
      <h3 class="text-sm font-black uppercase tracking-[0.1em] text-violet-600 dark:text-violet-300">{{ t('API Monitor Authentication') }}</h3>
      <p class="mt-1 text-xs text-[var(--text-tertiary)]">{{ t('API Monitor credential auth hint') }}</p>
    </div>
  </div>

  <div class="grid gap-4 lg:grid-cols-2">
    <label class="field">
      <span>{{ t('Credential Type') }}</span>
      <select v-model="form.credentialType" class="tools-input">
        <option v-for="option in API_MONITOR_CREDENTIAL_TYPE_OPTIONS" :key="option.value" :value="option.value">
          {{ t(option.labelKey) }}
        </option>
      </select>
      <small>{{ t('API Monitor Placeholder credential type hint') }}</small>
    </label>

    <label class="field">
      <span>{{ t('Credential') }}</span>
      <select v-model="form.credentialId" class="tools-input">
        <option value="">{{ t('No credential') }}</option>
        <option v-for="credential in credentials" :key="credential.id" :value="credential.id">
          {{ credential.name }} ({{ credential.username || credential.domain || credential.id }})
        </option>
      </select>
      <small v-if="credentials.length === 0">{{ t('No credentials available') }}</small>
    </label>
  </div>

  <label class="field mt-4">
    <span>{{ t('Timeout (ms)') }}</span>
    <input v-model.number="form.timeoutMs" type="number" min="1" class="tools-input font-mono" />
  </label>
</section>
```

- [ ] **Step 3: Show auth status in detail dialog**

In `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`, import:

```typescript
import { formatApiMonitorAuthStatus } from '@/utils/apiMonitorAuth';
```

Add helper:

```typescript
function apiMonitorAuthStatusLabel() {
  const status = formatApiMonitorAuthStatus(detail.value?.server.api_monitor_auth);
  return status === 'configured' ? t('Configured') : t('No credential');
}
```

In the overview chips grid, replace or add one chip:

```vue
<div class="detail-chip">
  <span class="detail-chip-label">{{ t('Authentication') }}</span>
  <span :class="detail.server.api_monitor_auth?.credential_id ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-700 dark:text-amber-300'">
    {{ apiMonitorAuthStatusLabel() }}
  </span>
</div>
```

- [ ] **Step 4: Add locale strings**

Add to `RpaClaw/frontend/src/locales/en.ts`:

```typescript
'API Monitor Authentication': 'API Monitor Authentication',
'API Monitor credential auth hint': 'Choose a managed credential and credential type for this API Monitor MCP. Captured header profiles are not saved.',
'Credential Type': 'Credential Type',
'API Monitor Placeholder credential type': 'Placeholder',
'API Monitor Placeholder credential type hint': 'Placeholder type validates the selected credential but does not inject authentication into requests yet.',
'Authentication': 'Authentication',
'Configured': 'Configured',
```

Add to `RpaClaw/frontend/src/locales/zh.ts`:

```typescript
'API Monitor Authentication': 'API Monitor 认证',
'API Monitor credential auth hint': '为此 API Monitor MCP 选择凭证管理中的凭证和凭证类型。捕获到的请求头画像不会被保存。',
'Credential Type': '凭证类型',
'API Monitor Placeholder credential type': '占位符',
'API Monitor Placeholder credential type hint': '占位类型只校验所选凭证，当前不会把认证信息注入请求。',
'Authentication': '认证',
'Configured': '已配置',
```

- [ ] **Step 5: Run frontend checks and commit**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/utils/apiMonitorAuth.test.ts src/utils/apiMonitorMcp.test.ts
npm run type-check
```

Expected: PASS.

Commit:

```bash
git add RpaClaw/frontend/src/components/tools/ApiMonitorMcpEditDialog.vue RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue RpaClaw/frontend/src/locales/en.ts RpaClaw/frontend/src/locales/zh.ts
git commit -m "feat: manage api monitor mcp credentials in tools"
```

---

## Task 6: Final Verification

**Files:**

- Verify backend and frontend only.

- [ ] **Step 1: Run focused backend suite**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_auth.py tests/test_api_monitor_publish_mcp.py tests/test_mcp_route.py tests/deepagent/test_mcp_runtime.py tests/deepagent/test_mcp_registry.py -q
```

Expected: PASS.

- [ ] **Step 2: Run focused frontend suite**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/utils/apiMonitorAuth.test.ts src/utils/apiMonitorMcp.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run frontend type check**

Run:

```bash
cd RpaClaw/frontend
npm run type-check
```

Expected: PASS.

- [ ] **Step 4: Inspect git diff**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: only files from this plan are changed; unrelated `.rpaclaw/` and root `package-lock.json` remain untracked unless the user explicitly asks to include them.

- [ ] **Step 5: Final commit if needed**

If any verification-only fixes were made after Task 5, commit them:

```bash
git add RpaClaw/backend/rpa/api_monitor_auth.py RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/rpa/api_monitor_mcp_registry.py RpaClaw/backend/route/api_monitor.py RpaClaw/backend/route/mcp.py RpaClaw/backend/mcp/models.py RpaClaw/backend/deepagent/mcp_runtime.py RpaClaw/backend/deepagent/mcp_registry.py RpaClaw/backend/tests/test_api_monitor_auth.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py RpaClaw/backend/tests/test_mcp_route.py RpaClaw/backend/tests/deepagent/test_mcp_runtime.py RpaClaw/backend/tests/deepagent/test_mcp_registry.py RpaClaw/frontend/src/api/apiMonitor.ts RpaClaw/frontend/src/api/mcp.ts RpaClaw/frontend/src/utils/apiMonitorAuth.ts RpaClaw/frontend/src/utils/apiMonitorAuth.test.ts RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue RpaClaw/frontend/src/components/tools/ApiMonitorMcpEditDialog.vue RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue RpaClaw/frontend/src/locales/en.ts RpaClaw/frontend/src/locales/zh.ts
git commit -m "fix: stabilize api monitor mcp credential auth"
```
