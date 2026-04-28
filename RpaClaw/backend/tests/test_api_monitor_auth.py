from datetime import datetime

import pytest

from backend.rpa.api_monitor.models import (
    ApiMonitorSession,
    ApiToolDefinition,
    CapturedApiCall,
    CapturedRequest,
)
from backend.rpa.api_monitor_auth import (
    apply_api_monitor_auth_to_profile,
    build_api_monitor_auth_profile,
    normalize_api_monitor_auth_config,
    validate_api_monitor_auth_config,
)
from backend.rpa.api_monitor_runtime_profile import ApiMonitorRuntimeProfile


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


# ── Profile-based auth ─────────────────────────────────────────────────


class _LoginResponse:
    status_code = 200
    is_success = True
    headers = {"content-type": "application/json"}
    text = '{"token":"login-token"}'

    def json(self):
        return {"token": "login-token"}


class _LoginClient:
    def __init__(self):
        self.calls = []
        self.cookies = {"sid": "cookie-value"}

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _LoginResponse()


@pytest.mark.anyio
async def test_test_credential_auth_writes_authorization_to_profile():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")
    client = _LoginClient()

    app = await apply_api_monitor_auth_to_profile(
        user_id="user-1",
        auth_config={
            "credential_type": "test",
            "credential_id": "cred_1",
            "login_url": "https://api.example.test/api/login",
        },
        profile=profile,
        client=client,
        vault=FakeVault({"cred_1": {"username": "alice", "password": "secret"}}),
    )

    assert app.error == ""
    assert client.calls == [
        (
            "POST",
            "https://api.example.test/api/login",
            {"json": {"username": "alice", "password": "secret"}},
        )
    ]
    assert profile.headers == {"Authorization": "Bearer login-token"}
    assert profile.variables["auth_token"] == "login-token"
    assert profile.has_cookies is True
    assert "login-token" not in str(app.preview)
