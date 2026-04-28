# API Monitor Runtime Profile Token Flow V2 实施计划

> **给执行 Agent：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐步执行。步骤使用 checkbox（`- [ ]`）跟踪。

**目标：** 将当前“无状态 token setup/inject”改造成基于 runtime profile 的执行管线：先登录并把认证材料写入 profile，再用 profile 获取 token producer，最后用 profile 中的 auth token 和 token consumer 信息调用目标 API。

**架构：** 新增 `ApiMonitorRuntimeProfile` 作为运行期状态对象，负责持有 `httpx.AsyncClient` 会话上下文、cookie、认证 headers、提取出的动态变量和脱敏 preview。Credential auth 先写入 profile；token producer 使用 profile 发请求并把提取结果写回 profile；目标 tool 请求再从同一个 profile 中渲染 consumer 注入规则。发布流程同时支持自动识别、去重和人工配置 token flow。

**技术栈：** FastAPI、Pydantic v2、异步 repository、httpx、pytest、Vue 3 Composition API、TypeScript、Vite/Vitest、Tailwind CSS。

---

## 依据的设计规格

本计划以 `docs/superpowers/specs/2026-04-28-api-monitor-runtime-profile-token-flow-v2-design.md` 为准。早先的计划草案已经覆盖了 profile、producer、consumer、去重和人工配置的主路径，但还需要按 spec 补齐以下执行要求：

- 模板变量缺失不能被静默渲染成空字符串，必须返回可诊断错误。
- 多个 flow 在同一次调用中写入同名 secret variable 时不能静默覆盖。
- consumer 未匹配、producer 提取为空、producer HTTP 失败必须在 preview/error 中区分。
- 一个 flow 包含多个 consumers 时，runtime 只注入当前目标请求匹配的 consumer。
- 目标请求返回 `refresh_on_status` 状态码时，必须重新执行 producer 并重试一次。
- V1 token flow 迁移或明确报错属于实现内容，不能只放在最终验证中。

## 为什么要替换旧计划

旧实现把 token flow 当成一个简单的每次调用前置步骤：

```text
credential auth -> token setup -> extract token -> inject target request
```

实际测试证明这个模型不够：

- 登录可能产生 auth token、cookie、session 等运行期材料。
- token producer 请求本身可能需要登录后的 profile。
- 目标 API 同时需要 auth token 和动态 token。
- 录制阶段可能重复捕获同一个接口，导致 flow 重复。
- 自动识别不完整时，需要人工配置或修正 producer/consumer。

正确的运行顺序应该是：

```text
创建 runtime profile
  -> 执行 credential auth，把登录结果写入 profile
  -> 使用 profile 解析并请求 token producer
  -> 将 producer 提取出的动态值写回 profile
  -> 渲染目标 tool 请求
  -> 使用 profile 注入匹配的 token consumer
  -> 使用 profile 的 client/cookie/header 执行目标请求
```

---

## 文件结构

后端文件：

- 新建 `RpaClaw/backend/rpa/api_monitor_runtime_profile.py`：运行期 profile、变量渲染、脱敏 preview、请求注入辅助函数。
- 修改 `RpaClaw/backend/rpa/api_monitor_auth.py`：新增 profile-based auth，不破坏旧兼容函数。
- 修改 `RpaClaw/backend/rpa/api_monitor_token_flow.py`：新增 V2 flow schema、去重、人工 flow 校验、producer/consumer runtime helper。
- 修改 `RpaClaw/backend/deepagent/mcp_runtime.py`：将当前 ad hoc token setup 改为 runtime profile 管线。
- 修改 `RpaClaw/backend/rpa/api_monitor/models.py`：增加人工 token flow request model 和 V2 publish selection。
- 修改 `RpaClaw/backend/route/api_monitor.py`：发布时合并自动 flow 和人工 flow，并在保存前校验人工 flow。
- 修改 `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`：持久化 dedupe 后的自动 flow 和校验后的人工 flow。
- 修改 `RpaClaw/backend/route/mcp.py`：MCP 工具库详情/编辑接口支持读取和更新 API Monitor token flow 配置。
- 测试 `RpaClaw/backend/tests/test_api_monitor_runtime_profile.py`。
- 测试 `RpaClaw/backend/tests/test_api_monitor_auth.py`。
- 测试 `RpaClaw/backend/tests/test_api_monitor_token_flow.py`。
- 测试 `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`。
- 测试 `RpaClaw/backend/tests/test_mcp_route.py`。
- 测试 `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`。

前端文件：

- 修改 `RpaClaw/frontend/src/api/apiMonitor.ts`：增加 V2 token flow profile、人工配置类型和 API。
- 修改 `RpaClaw/frontend/src/api/mcp.ts`：增加 MCP detail/edit 中的 token flow 类型。
- 修改 `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`：发布弹窗展示去重后的候选 flow，并提供人工配置入口。
- 修改 `RpaClaw/frontend/src/components/tools/ApiMonitorMcpEditDialog.vue`：发布后允许人工新增/编辑/删除 token flow。
- 修改 `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`：展示 runtime profile 和 token flow 摘要。
- 修改 `RpaClaw/frontend/src/locales/en.ts` 和 `RpaClaw/frontend/src/locales/zh.ts`。

---

## V2 数据结构

持久化 token flow：

```json
{
  "id": "flow_csrf_orders",
  "name": "csrf_token",
  "source": "auto",
  "enabled": true,
  "producer": {
    "request": {
      "method": "GET",
      "url": "/api/session",
      "headers": {},
      "query": {},
      "body": null,
      "content_type": ""
    },
    "extract": [
      {
        "name": "csrf_token",
        "from": "response.body",
        "path": "$.csrfToken",
        "secret": true
      }
    ]
  },
  "consumers": [
    {
      "method": "GET",
      "url": "/api/orders",
      "inject": {
        "headers": {
          "X-CSRF-Token": "{{ csrf_token }}"
        },
        "query": {},
        "body": {}
      }
    }
  ],
  "refresh_on_status": [401, 403, 419],
  "confidence": "high",
  "summary": {
    "producer": "GET /api/session response.body.$.csrfToken",
    "consumers": ["GET /api/orders request.headers.X-CSRF-Token"],
    "sample_count": 2,
    "source_call_ids": ["call_1", "call_7"],
    "reasons": ["exact-value-match", "producer-before-consumer", "same-origin"]
  }
}
```

运行期 profile 变量示例：

```python
{
    "auth_token": "Bearer token from login",
    "csrf_token": "short-lived csrf token",
}
```

request preview 示例：

```json
{
  "auth": {
    "credential_type": "test",
    "credential_configured": true,
    "injected": true,
    "profile": {
      "headers": ["Authorization"],
      "variables": ["auth_token", "csrf_token"],
      "cookies": true
    },
    "token_flows": [
      {
        "id": "flow_csrf_orders",
        "name": "csrf_token",
        "producer_applied": true,
        "consumer_applied": true,
        "injected": ["headers.X-CSRF-Token"]
      }
    ]
  }
}
```

---

### Task 1: Runtime Profile 核心对象

**文件：**

- 新建：`RpaClaw/backend/rpa/api_monitor_runtime_profile.py`
- 测试：`RpaClaw/backend/tests/test_api_monitor_runtime_profile.py`

- [ ] **Step 1: 写失败测试**

创建 `RpaClaw/backend/tests/test_api_monitor_runtime_profile.py`：

```python
import pytest

from backend.rpa.api_monitor_runtime_profile import ApiMonitorRuntimeProfile, ApiMonitorRuntimeProfileError


def test_profile_stores_secret_variables_and_masks_preview():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")

    profile.set_variable("auth_token", "secret-auth-token", secret=True)
    profile.set_header("Authorization", "Bearer secret-auth-token", secret=True)

    assert profile.render_template("Bearer {{ auth_token }}") == "Bearer secret-auth-token"
    assert profile.preview() == {
        "headers": ["Authorization"],
        "variables": ["auth_token"],
        "cookies": False,
    }
    assert "secret-auth-token" not in str(profile.preview())


def test_profile_renders_nested_mappings_without_mutating_input():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")
    profile.set_variable("csrf_token", "csrf-secret", secret=True)
    source = {
        "headers": {"X-CSRF-Token": "{{ csrf_token }}"},
        "body": {"nested": ["{{ csrf_token }}"]},
    }

    rendered = profile.render_value(source)

    assert rendered == {
        "headers": {"X-CSRF-Token": "csrf-secret"},
        "body": {"nested": ["csrf-secret"]},
    }
    assert source["headers"]["X-CSRF-Token"] == "{{ csrf_token }}"


def test_profile_apply_injection_supports_headers_query_and_body():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")
    profile.set_variable("csrf_token", "csrf-secret", secret=True)
    headers = {"Authorization": "Bearer auth"}
    query = {"page": 1}
    body = {"name": "order"}

    applied = profile.apply_injection(
        {
            "headers": {"X-CSRF-Token": "{{ csrf_token }}"},
            "query": {"csrf": "{{ csrf_token }}"},
            "body": {"_csrf": "{{ csrf_token }}"},
        },
        headers=headers,
        query=query,
        body=body,
    )

    assert headers["X-CSRF-Token"] == "csrf-secret"
    assert query["csrf"] == "csrf-secret"
    assert body["_csrf"] == "csrf-secret"
    assert applied == ["headers.X-CSRF-Token", "query.csrf", "body._csrf"]


def test_profile_raises_when_template_variable_is_missing():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")

    with pytest.raises(ApiMonitorRuntimeProfileError, match="missing variable: csrf_token"):
        profile.render_template("{{ csrf_token }}")


def test_profile_rejects_conflicting_secret_variable_overwrite():
    profile = ApiMonitorRuntimeProfile(base_url="https://api.example.test")
    profile.set_variable("csrf_token", "first-token", secret=True, source="flow_a")

    with pytest.raises(ApiMonitorRuntimeProfileError, match="variable conflict: csrf_token"):
        profile.set_variable("csrf_token", "second-token", secret=True, source="flow_b")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_runtime_profile.py -q
```

预期：失败，提示 `backend.rpa.api_monitor_runtime_profile` 不存在。

- [ ] **Step 3: 实现 runtime profile**

创建 `RpaClaw/backend/rpa/api_monitor_runtime_profile.py`：

```python
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any


TEMPLATE_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
SINGLE_TEMPLATE_RE = re.compile(r"^\s*{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}\s*$")


class ApiMonitorRuntimeProfileError(ValueError):
    pass


@dataclass
class ApiMonitorRuntimeProfile:
    base_url: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    secret_variables: set[str] = field(default_factory=set)
    variable_sources: dict[str, str] = field(default_factory=dict)
    headers: dict[str, Any] = field(default_factory=dict)
    secret_headers: set[str] = field(default_factory=set)
    has_cookies: bool = False

    def set_variable(self, name: str, value: Any, *, secret: bool = True, source: str = "") -> None:
        key = str(name or "").strip()
        if not key:
            return
        if key in self.variables and self.variables[key] != value:
            previous_source = self.variable_sources.get(key, "")
            if previous_source and source and previous_source != source:
                raise ApiMonitorRuntimeProfileError(f"Runtime profile variable conflict: {key}")
        self.variables[key] = value
        if source:
            self.variable_sources[key] = source
        if secret:
            self.secret_variables.add(key)

    def set_header(self, name: str, value: Any, *, secret: bool = True) -> None:
        key = str(name or "").strip()
        if not key:
            return
        self.headers[key] = value
        if secret:
            self.secret_headers.add(key)

    def render_template(self, value: str) -> Any:
        single = SINGLE_TEMPLATE_RE.match(value)
        if single:
            key = single.group(1)
            if key not in self.variables:
                raise ApiMonitorRuntimeProfileError(f"Runtime profile missing variable: {key}")
            return self.variables[key]

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in self.variables:
                raise ApiMonitorRuntimeProfileError(f"Runtime profile missing variable: {key}")
            rendered = self.variables[key]
            return "" if rendered is None else str(rendered)

        return TEMPLATE_RE.sub(replace, value)

    def render_value(self, value: Any) -> Any:
        value = deepcopy(value)
        if isinstance(value, str):
            return self.render_template(value)
        if isinstance(value, dict):
            return {key: self.render_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.render_value(item) for item in value]
        return value

    def apply_injection(
        self,
        inject: dict[str, Any] | Any,
        *,
        headers: dict[str, Any],
        query: dict[str, Any],
        body: dict[str, Any],
    ) -> list[str]:
        if not isinstance(inject, dict):
            return []
        applied: list[str] = []
        for name, template in (inject.get("headers") or {}).items():
            headers[str(name)] = self.render_value(template)
            applied.append(f"headers.{name}")
        for name, template in (inject.get("query") or {}).items():
            query[str(name)] = self.render_value(template)
            applied.append(f"query.{name}")
        for name, template in (inject.get("body") or {}).items():
            body[str(name).removeprefix("$.")] = self.render_value(template)
            applied.append(f"body.{name}")
        return applied

    def preview(self) -> dict[str, Any]:
        return {
            "headers": sorted(self.headers.keys()),
            "variables": sorted(self.variables.keys()),
            "cookies": self.has_cookies,
        }
```

- [ ] **Step 4: 运行测试**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_runtime_profile.py -q
```

预期：通过。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor_runtime_profile.py RpaClaw/backend/tests/test_api_monitor_runtime_profile.py
git commit -m "feat: add api monitor runtime profile"
```

---

### Task 2: 让认证写入 Runtime Profile

**文件：**

- 修改：`RpaClaw/backend/rpa/api_monitor_auth.py`
- 测试：`RpaClaw/backend/tests/test_api_monitor_auth.py`

- [ ] **Step 1: 写失败测试**

追加到 `RpaClaw/backend/tests/test_api_monitor_auth.py`：

```python
from backend.rpa.api_monitor_runtime_profile import ApiMonitorRuntimeProfile
from backend.rpa.api_monitor_auth import apply_api_monitor_auth_to_profile


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
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_auth.py::test_test_credential_auth_writes_authorization_to_profile -q
```

预期：失败，`apply_api_monitor_auth_to_profile` 不存在。

- [ ] **Step 3: 实现 profile-based auth**

在 `RpaClaw/backend/rpa/api_monitor_auth.py` 中引入：

```python
from backend.rpa.api_monitor_runtime_profile import ApiMonitorRuntimeProfile
```

新增：

```python
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

    return ApiMonitorAuthApplication(error=f"Unsupported API Monitor credential type: {credential_type}")
```

- [ ] **Step 4: 运行认证测试**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_auth.py -q
```

预期：通过。

- [ ] **Step 5: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor_auth.py RpaClaw/backend/tests/test_api_monitor_auth.py
git commit -m "feat: write api monitor auth into runtime profile"
```

---

### Task 3: Token Flow V2 去重与人工配置校验

**文件：**

- 修改：`RpaClaw/backend/rpa/api_monitor_token_flow.py`
- 测试：`RpaClaw/backend/tests/test_api_monitor_token_flow.py`

- [ ] **Step 1: 写失败测试**

追加到 `RpaClaw/backend/tests/test_api_monitor_token_flow.py`：

```python
from backend.rpa.api_monitor_token_flow import validate_manual_token_flow


def test_profile_deduplicates_repeated_same_endpoint_consumers():
    calls = [
        _call(
            "producer",
            method="GET",
            url="https://example.test/api/session",
            response_body='{"csrfToken":"8fa7c91e2d8a4c90b0f7"}',
            seconds=0,
        ),
        _call(
            "orders_1",
            method="GET",
            url="https://example.test/api/orders",
            request_headers={"X-CSRF-Token": "8fa7c91e2d8a4c90b0f7"},
            seconds=1,
        ),
        _call(
            "orders_2",
            method="GET",
            url="https://example.test/api/orders",
            request_headers={"X-CSRF-Token": "8fa7c91e2d8a4c90b0f7"},
            seconds=2,
        ),
    ]

    profile = build_api_monitor_token_flow_profile(calls)

    assert profile["flow_count"] == 1
    flow = profile["flows"][0]
    assert flow["consumer_summaries"] == ["GET /api/orders request.headers.X-CSRF-Token"]
    assert flow["sample_count"] == 2
    assert flow["source_call_ids"] == ["orders_1", "orders_2"]


def test_validate_manual_token_flow_accepts_complete_config():
    flow = validate_manual_token_flow(
        {
            "id": "manual_csrf",
            "name": "csrf_token",
            "enabled": True,
            "source": "manual",
            "producer": {
                "request": {"method": "GET", "url": "/api/session"},
                "extract": [{"name": "csrf_token", "from": "response.body", "path": "$.csrfToken"}],
            },
            "consumers": [
                {
                    "method": "GET",
                    "url": "/api/orders",
                    "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                }
            ],
        }
    )

    assert flow["id"] == "manual_csrf"
    assert flow["producer"]["request"]["method"] == "GET"
    assert flow["consumers"][0]["inject"]["headers"] == {"X-CSRF-Token": "{{ csrf_token }}"}


def test_validate_manual_token_flow_rejects_missing_extract():
    with pytest.raises(ValueError, match="producer.extract"):
        validate_manual_token_flow(
            {
                "id": "manual_csrf",
                "name": "csrf_token",
                "producer": {"request": {"method": "GET", "url": "/api/session"}, "extract": []},
                "consumers": [{"method": "GET", "url": "/api/orders", "inject": {"headers": {}}}],
            }
        )


def test_validate_manual_token_flow_rejects_unknown_template_variable():
    with pytest.raises(ValueError, match="unknown template variable: missing_token"):
        validate_manual_token_flow(
            {
                "id": "manual_csrf",
                "name": "csrf_token",
                "producer": {
                    "request": {"method": "GET", "url": "/api/session"},
                    "extract": [{"name": "csrf_token", "from": "response.body", "path": "$.csrfToken"}],
                },
                "consumers": [
                    {
                        "method": "GET",
                        "url": "/api/orders",
                        "inject": {"headers": {"X-CSRF-Token": "{{ missing_token }}"}},
                    }
                ],
            }
        )
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_token_flow.py::test_profile_deduplicates_repeated_same_endpoint_consumers tests/test_api_monitor_token_flow.py::test_validate_manual_token_flow_accepts_complete_config tests/test_api_monitor_token_flow.py::test_validate_manual_token_flow_rejects_missing_extract -q
```

预期：失败，因为去重字段和 `validate_manual_token_flow` 尚不存在。

- [ ] **Step 3: 增加 consumer 去重**

在 `RpaClaw/backend/rpa/api_monitor_token_flow.py` 中新增：

```python
def _dedupe_consumers(consumers: list[_TokenConsumer]) -> tuple[list[_TokenConsumer], list[str]]:
    seen: dict[tuple[str, str, str, str], _TokenConsumer] = {}
    source_call_ids: list[str] = []
    for consumer in consumers:
        key = (consumer.method.upper(), consumer.url_pattern, consumer.location, consumer.path)
        if key not in seen:
            seen[key] = consumer
        if consumer.call_id not in source_call_ids:
            source_call_ids.append(consumer.call_id)
    return list(seen.values()), source_call_ids
```

修改 `_flow_profile_doc()`：

```python
deduped_consumers, source_call_ids = _dedupe_consumers(flow.consumers)
consumer_summaries = [
    f"{c.method} {c.url_pattern} {c.location}.{_display_path(c.path)}"
    for c in deduped_consumers
]
return {
    "id": flow.id,
    "name": flow.name,
    "producer_summary": producer_summary,
    "consumer_summaries": consumer_summaries,
    "confidence": flow.confidence,
    "enabled_by_default": flow.confidence in ("high", "medium"),
    "reasons": flow.reasons,
    "sample_count": len(flow.consumers),
    "source_call_ids": source_call_ids,
}
```

修改 `_flow_runtime_doc()`，遍历 `deduped_consumers`，不要遍历原始重复 consumers。

- [ ] **Step 4: 增加人工 flow 校验**

在 `api_monitor_token_flow.py` 中确保已有：

```python
import re
from typing import Any
```

然后新增：

```python
ALLOWED_EXTRACT_SOURCES = {"response.body", "response.headers", "cookie", "set-cookie"}
ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
KNOWN_PROFILE_VARIABLES = {"auth_token"}
TOKEN_TEMPLATE_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


def _template_variables(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(TOKEN_TEMPLATE_RE.findall(value))
    if isinstance(value, dict):
        found: set[str] = set()
        for item in value.values():
            found.update(_template_variables(item))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found.update(_template_variables(item))
        return found
    return set()


def validate_manual_token_flow(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("token flow must be an object")
    flow_id = str(value.get("id") or "").strip()
    name = str(value.get("name") or "").strip()
    if not flow_id:
        raise ValueError("token flow id is required")
    if not name:
        raise ValueError("token flow name is required")

    producer = value.get("producer") or {}
    request = producer.get("request") or {}
    method = str(request.get("method") or "GET").upper()
    url = str(request.get("url") or "").strip()
    if method not in ALLOWED_METHODS:
        raise ValueError("producer.request.method is invalid")
    if not url:
        raise ValueError("producer.request.url is required")

    extracts = producer.get("extract") or []
    if not isinstance(extracts, list) or not extracts:
        raise ValueError("producer.extract must contain at least one item")
    normalized_extracts = []
    for item in extracts:
        source = str(item.get("from") or "").strip()
        path = str(item.get("path") or item.get("name") or "").strip()
        token_name = str(item.get("name") or name).strip()
        if source not in ALLOWED_EXTRACT_SOURCES:
            raise ValueError("producer.extract.from is invalid")
        if not path:
            raise ValueError("producer.extract.path is required")
        normalized_extracts.append({"name": token_name, "from": source, "path": path, "secret": True})
    available_variables = {item["name"] for item in normalized_extracts} | KNOWN_PROFILE_VARIABLES

    consumers = value.get("consumers") or []
    if not isinstance(consumers, list) or not consumers:
        raise ValueError("consumers must contain at least one item")
    normalized_consumers = []
    for consumer in consumers:
        consumer_method = str(consumer.get("method") or "").upper()
        consumer_url = str(consumer.get("url") or "").strip()
        inject = consumer.get("inject") or {}
        if consumer_method not in ALLOWED_METHODS:
            raise ValueError("consumer.method is invalid")
        if not consumer_url:
            raise ValueError("consumer.url is required")
        if not any((inject.get("headers") or {}, inject.get("query") or {}, inject.get("body") or {})):
            raise ValueError("consumer.inject must include headers, query, or body")
        for variable_name in sorted(_template_variables(inject)):
            if variable_name not in available_variables:
                raise ValueError(f"unknown template variable: {variable_name}")
        normalized_consumers.append(
            {
                "method": consumer_method,
                "url": consumer_url,
                "inject": {
                    "headers": dict(inject.get("headers") or {}),
                    "query": dict(inject.get("query") or {}),
                    "body": dict(inject.get("body") or {}),
                },
            }
        )

    return {
        "id": flow_id,
        "name": name,
        "source": "manual",
        "enabled": bool(value.get("enabled", True)),
        "producer": {
            "request": {
                "method": method,
                "url": url,
                "headers": dict(request.get("headers") or {}),
                "query": dict(request.get("query") or {}),
                "body": request.get("body"),
                "content_type": str(request.get("content_type") or ""),
            },
            "extract": normalized_extracts,
        },
        "consumers": normalized_consumers,
        "refresh_on_status": list(value.get("refresh_on_status") or [401, 403, 419]),
        "confidence": "manual",
        "summary": value.get("summary") or {"producer": f"{method} {url}", "consumers": []},
    }
```

- [ ] **Step 5: 运行 token flow 测试**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_token_flow.py -q
```

预期：通过。

- [ ] **Step 6: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor_token_flow.py RpaClaw/backend/tests/test_api_monitor_token_flow.py
git commit -m "feat: dedupe and validate api monitor token flows"
```

---

### Task 4: Profile-Based Runtime 管线

**文件：**

- 修改：`RpaClaw/backend/deepagent/mcp_runtime.py`
- 测试：`RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`

- [ ] **Step 1: 写失败集成测试**

追加到 `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`：

```python
def test_api_monitor_runtime_profile_auth_then_producer_then_consumer(monkeypatch):
    repo = _MemoryRepo([
        {
            "mcp_server_id": "mcp_api_monitor",
            "name": "list_orders",
            "validation_status": "valid",
            "method": "GET",
            "url": "/api/orders",
            "base_url": "http://localhost:11451",
            "query_mapping": {"name": "{{ name }}"},
        }
    ])

    class _SeqClient:
        def __init__(self, **kwargs):
            self.calls = []
            self.cookies = {"sid": "cookie"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if url.endswith("/api/login"):
                return _ApiResponse(json_body={"token": "auth-token"})
            if url.endswith("/api/session"):
                assert kwargs["headers"]["Authorization"] == "Bearer auth-token"
                return _ApiResponse(json_body={"csrfToken": "csrf-token"})
            assert kwargs["headers"]["Authorization"] == "Bearer auth-token"
            assert kwargs["headers"]["X-CSRF-Token"] == "csrf-token"
            assert "csrf" not in kwargs.get("params", {})
            return _ApiResponse(json_body={"orders": []})

    client = _SeqClient()
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)

    class _Vault:
        async def resolve_credential_values(self, user_id, cred_id):
            return {"username": "alice", "password": "secret"}

    monkeypatch.setattr(mcp_runtime, "get_vault", lambda: _Vault())

    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="user-1",
        name="Orders MCP",
        transport="api_monitor",
        scope="user",
        api_monitor_auth={
            "credential_type": "test",
            "credential_id": "cred_1",
            "login_url": "http://localhost:11451/api/login",
            "token_flows": [
                {
                    "id": "flow_csrf",
                    "name": "csrf_token",
                    "enabled": True,
                    "producer": {
                        "request": {"method": "GET", "url": "/api/session"},
                        "extract": [
                            {"name": "csrf_token", "from": "response.body", "path": "$.csrfToken"}
                        ],
                    },
                    "consumers": [
                        {
                            "method": "GET",
                            "url": "/api/orders",
                            "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                        },
                        {
                            "method": "POST",
                            "url": "/api/orders",
                            "inject": {"query": {"csrf": "{{ csrf_token }}"}},
                        }
                    ],
                    "refresh_on_status": [401, 403, 419],
                }
            ],
        },
    )

    result = asyncio.run(mcp_runtime.ApiMonitorMcpRuntime(server).call_tool("list_orders", {"name": "sample"}))

    assert result["success"] is True
    assert [call[1] for call in client.calls] == [
        "http://localhost:11451/api/login",
        "http://localhost:11451/api/session",
        "http://localhost:11451/api/orders",
    ]
    assert result["request_preview"]["auth"]["token_flows"][0]["consumer_applied"] is True
    assert result["request_preview"]["auth"]["token_flows"][0]["matched_consumers"] == ["GET /api/orders"]
    assert "csrf-token" not in str(result)
    assert "auth-token" not in str(result)


def test_api_monitor_runtime_preview_empty_token_flows_when_consumer_does_not_match(monkeypatch):
    repo = _MemoryRepo([
        {
            "mcp_server_id": "mcp_api_monitor",
            "name": "list_orders",
            "validation_status": "valid",
            "method": "GET",
            "url": "/api/orders",
            "base_url": "http://localhost:11451",
        }
    ])

    class _SeqClient:
        def __init__(self, **kwargs):
            self.calls = []
            self.cookies = {"sid": "cookie"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if url.endswith("/api/login"):
                return _ApiResponse(json_body={"token": "auth-token"})
            assert url.endswith("/api/orders")
            assert kwargs["headers"]["Authorization"] == "Bearer auth-token"
            assert "X-CSRF-Token" not in kwargs["headers"]
            return _ApiResponse(status_code=403, json_body={"message": "missing csrf"})

    client = _SeqClient()
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)

    class _Vault:
        async def resolve_credential_values(self, user_id, cred_id):
            return {"username": "alice", "password": "secret"}

    monkeypatch.setattr(mcp_runtime, "get_vault", lambda: _Vault())

    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="user-1",
        name="Orders MCP",
        transport="api_monitor",
        scope="user",
        api_monitor_auth={
            "credential_type": "test",
            "credential_id": "cred_1",
            "login_url": "http://localhost:11451/api/login",
            "token_flows": [
                {
                    "id": "flow_csrf",
                    "name": "csrf_token",
                    "enabled": True,
                    "producer": {
                        "request": {"method": "GET", "url": "/api/session"},
                        "extract": [{"name": "csrf_token", "from": "response.body", "path": "$.csrfToken"}],
                    },
                    "consumers": [
                        {
                            "method": "POST",
                            "url": "/api/orders",
                            "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                        }
                    ],
                }
            ],
        },
    )

    result = asyncio.run(mcp_runtime.ApiMonitorMcpRuntime(server).call_tool("list_orders", {}))

    assert result["success"] is False
    assert [call[1] for call in client.calls] == [
        "http://localhost:11451/api/login",
        "http://localhost:11451/api/orders",
    ]
    assert result["request_preview"]["auth"]["profile"]["variables"] == ["auth_token"]
    assert result["request_preview"]["auth"]["token_flows"] == []
```

同时增加一次刷新重试测试，确保 `refresh_on_status` 不是只写在配置里：

```python
def test_api_monitor_runtime_refreshes_token_and_retries_once_on_403(monkeypatch):
    repo = _MemoryRepo([
        {
            "mcp_server_id": "mcp_api_monitor",
            "name": "list_orders",
            "validation_status": "valid",
            "method": "GET",
            "url": "/api/orders",
            "base_url": "http://localhost:11451",
        }
    ])

    class _SeqClient:
        def __init__(self, **kwargs):
            self.calls = []
            self.cookies = {"sid": "cookie"}
            self.session_count = 0
            self.order_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if url.endswith("/api/login"):
                return _ApiResponse(json_body={"token": "auth-token"})
            if url.endswith("/api/session"):
                self.session_count += 1
                return _ApiResponse(json_body={"csrfToken": f"csrf-token-{self.session_count}"})
            self.order_count += 1
            assert kwargs["headers"]["X-CSRF-Token"] == f"csrf-token-{self.order_count}"
            if self.order_count == 1:
                return _ApiResponse(status_code=403, json_body={"message": "stale csrf"})
            return _ApiResponse(json_body={"orders": []})

    client = _SeqClient()
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)

    class _Vault:
        async def resolve_credential_values(self, user_id, cred_id):
            return {"username": "alice", "password": "secret"}

    monkeypatch.setattr(mcp_runtime, "get_vault", lambda: _Vault())

    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="user-1",
        name="Orders MCP",
        transport="api_monitor",
        scope="user",
        api_monitor_auth={
            "credential_type": "test",
            "credential_id": "cred_1",
            "login_url": "http://localhost:11451/api/login",
            "token_flows": [
                {
                    "id": "flow_csrf",
                    "name": "csrf_token",
                    "enabled": True,
                    "producer": {
                        "request": {"method": "GET", "url": "/api/session"},
                        "extract": [{"name": "csrf_token", "from": "response.body", "path": "$.csrfToken"}],
                    },
                    "consumers": [
                        {
                            "method": "GET",
                            "url": "/api/orders",
                            "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                        }
                    ],
                    "refresh_on_status": [403, 419],
                }
            ],
        },
    )

    result = asyncio.run(mcp_runtime.ApiMonitorMcpRuntime(server).call_tool("list_orders", {}))

    assert result["success"] is True
    assert [call[1] for call in client.calls] == [
        "http://localhost:11451/api/login",
        "http://localhost:11451/api/session",
        "http://localhost:11451/api/orders",
        "http://localhost:11451/api/session",
        "http://localhost:11451/api/orders",
    ]
    assert result["request_preview"]["auth"]["token_flows"][0]["refreshed"] is True
    assert "csrf-token-1" not in str(result)
    assert "csrf-token-2" not in str(result)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/deepagent/test_mcp_runtime.py::test_api_monitor_runtime_profile_auth_then_producer_then_consumer tests/deepagent/test_mcp_runtime.py::test_api_monitor_runtime_preview_empty_token_flows_when_consumer_does_not_match tests/deepagent/test_mcp_runtime.py::test_api_monitor_runtime_refreshes_token_and_retries_once_on_403 -q
```

预期：失败，因为 runtime 还没有使用 profile-based auth 和 V2 producer/consumer 结构。

- [ ] **Step 3: 添加 V2 runtime helper**

在 `RpaClaw/backend/deepagent/mcp_runtime.py` 引入：

```python
from urllib.parse import urlsplit

from backend.rpa.api_monitor_auth import apply_api_monitor_auth_to_profile
from backend.rpa.api_monitor_runtime_profile import ApiMonitorRuntimeProfile, ApiMonitorRuntimeProfileError
```

新增 helper：

```python
def _normalize_token_path(value: str) -> str:
    parsed = urlsplit(str(value or ""))
    path = parsed.path or str(value or "")
    return "/" + path.strip("/")


def _token_urls_match(expected: str, tool_url: str, absolute_url: str) -> bool:
    expected = str(expected or "").strip()
    if not expected:
        return False
    expected_parts = urlsplit(expected)
    absolute_parts = urlsplit(absolute_url)
    if expected_parts.scheme and expected_parts.netloc:
        return (
            expected_parts.scheme == absolute_parts.scheme
            and expected_parts.netloc == absolute_parts.netloc
            and _normalize_token_path(expected) == _normalize_token_path(absolute_url)
        )
    return _normalize_token_path(expected) == _normalize_token_path(tool_url) == _normalize_token_path(absolute_url)


def _matching_v2_token_flows(token_flows: list[dict[str, Any]], method: str, tool_url: str, absolute_url: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for flow in token_flows or []:
        if flow.get("enabled") is False:
            continue
        consumers = flow.get("consumers") or []
        for consumer in consumers:
            target_method = str(consumer.get("method") or "").upper()
            target_url = str(consumer.get("url") or "")
            if target_method == method and _token_urls_match(target_url, tool_url, absolute_url):
                matches.append(flow)
                break
    return matches
```

新增 producer 执行：

```python
async def _resolve_v2_token_producer(
    *,
    client: httpx.AsyncClient,
    profile: ApiMonitorRuntimeProfile,
    base_url: str,
    flow: dict[str, Any],
    build_url,
) -> tuple[list[str], str]:
    producer = flow.get("producer") or {}
    request = producer.get("request") or {}
    extracts = producer.get("extract") or []
    method = str(request.get("method") or "GET").upper()
    raw_url = str(request.get("url") or "")
    url = build_url(base_url, raw_url, {})
    if not url:
        return [], f"Token flow '{flow.get('name', 'unknown')}' producer URL is not callable"
    try:
        headers = {**profile.headers, **profile.render_value(request.get("headers") or {})}
        query = profile.render_value(request.get("query") or {})
        body = profile.render_value(request.get("body"))
    except ApiMonitorRuntimeProfileError as exc:
        return [], f"Token flow '{flow.get('name', 'unknown')}' producer render failed: {exc}"
    kwargs: dict[str, Any] = {"headers": headers, "params": query}
    if body is not None:
        kwargs["json"] = body
    response = await client.request(method, url, **kwargs)
    if not response.is_success:
        return [], f"Token flow '{flow.get('name', 'unknown')}' producer got HTTP {response.status_code} from {method} {_token_safe_url(url)}"
    extracted_names: list[str] = []
    for extract in extracts:
        value = _extract_token_from_response(response, {"from": extract.get("from"), "path": extract.get("path")})
        if value is not None:
            name = str(extract.get("name") or flow.get("name") or "token")
            try:
                profile.set_variable(name, value, secret=bool(extract.get("secret", True)), source=str(flow.get("id") or ""))
            except ApiMonitorRuntimeProfileError as exc:
                return [], str(exc)
            extracted_names.append(name)
    if not extracted_names:
        return [], f"Token flow '{flow.get('name', 'unknown')}' producer did not extract any values"
    profile.has_cookies = profile.has_cookies or bool(getattr(client, "cookies", None))
    return extracted_names, ""
```

新增 consumer 注入：

```python
def _apply_v2_token_consumers(
    *,
    profile: ApiMonitorRuntimeProfile,
    flows: list[dict[str, Any]],
    method: str,
    tool_url: str,
    absolute_url: str,
    headers: dict[str, Any],
    query: dict[str, Any],
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for flow in flows:
        applied: list[str] = []
        matched_consumers: list[str] = []
        for consumer in flow.get("consumers") or []:
            target_method = str(consumer.get("method") or "").upper()
            target_url = str(consumer.get("url") or "")
            if target_method != method:
                continue
            if not _token_urls_match(target_url, tool_url, absolute_url):
                continue
            matched_consumers.append(f"{target_method} {target_url}")
            try:
                applied.extend(profile.apply_injection(consumer.get("inject") or {}, headers=headers, query=query, body=body))
            except ApiMonitorRuntimeProfileError as exc:
                previews.append(
                    {
                        "id": flow.get("id", ""),
                        "name": flow.get("name", ""),
                        "producer_applied": True,
                        "consumer_applied": False,
                        "error": str(exc),
                    }
                )
                return previews
        previews.append(
            {
                "id": flow.get("id", ""),
                "name": flow.get("name", ""),
                "producer_applied": True,
                "consumer_applied": bool(applied),
                "matched_consumers": matched_consumers,
                "injected": applied,
            }
        )
    return previews
```

- [ ] **Step 4: 重排 `call_tool()` 执行顺序**

在 `ApiMonitorMcpRuntime.call_tool()` 中将当前 `apply_api_monitor_auth_to_request()` 和 ad hoc token setup 替换为：

```python
profile = ApiMonitorRuntimeProfile(base_url=request_base_url)


def _build_target_request_parts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    target_headers = {**profile.headers, **render_mapping(header_mapping, rendered_arguments)}
    target_query = _api_monitor_base_query(self._server) if not self._server.api_monitor_auth else {}
    target_query.update(render_mapping(query_mapping, rendered_arguments))
    target_body = render_mapping(body_mapping, rendered_arguments)
    return target_headers, target_query, target_body

async with httpx.AsyncClient(timeout=_api_monitor_timeout_seconds(self._server)) as client:
    auth_application = await apply_api_monitor_auth_to_profile(
        user_id=self._server.user_id,
        auth_config=self._server.api_monitor_auth,
        profile=profile,
        client=client,
        vault=get_vault(),
    )
    if auth_application.error:
        return {"success": False, "error": auth_application.error}

    token_flows_config = (self._server.api_monitor_auth or {}).get("token_flows", [])
    matching_flows = _matching_v2_token_flows(token_flows_config, method, _api_monitor_tool_url(doc), url)
    token_previews: list[dict[str, Any]] = []
    for flow in matching_flows:
        _, producer_error = await _resolve_v2_token_producer(
            client=client,
            profile=profile,
            base_url=request_base_url,
            flow=flow,
            build_url=_build_api_monitor_url,
        )
        if producer_error:
            return {"success": False, "error": producer_error}

    request_headers, request_query, request_body = _build_target_request_parts()
    token_previews = _apply_v2_token_consumers(
        profile=profile,
        flows=matching_flows,
        method=method,
        tool_url=_api_monitor_tool_url(doc),
        absolute_url=url,
        headers=request_headers,
        query=request_query,
        body=request_body,
    )
    consumer_error = next((preview.get("error") for preview in token_previews if preview.get("error")), "")
    if consumer_error:
        return {"success": False, "error": consumer_error}

    request_kwargs = {"params": request_query, "headers": request_headers}
    if request_body:
        request_kwargs["json"] = request_body
    response = await client.request(method, url, **request_kwargs)

    refresh_statuses = {
        int(status)
        for flow in matching_flows
        for status in (flow.get("refresh_on_status") or [])
        if isinstance(status, int)
    }
    if matching_flows and response.status_code in refresh_statuses:
        for flow in matching_flows:
            _, producer_error = await _resolve_v2_token_producer(
                client=client,
                profile=profile,
                base_url=request_base_url,
                flow=flow,
                build_url=_build_api_monitor_url,
            )
            if producer_error:
                return {"success": False, "error": producer_error}
        request_headers, request_query, request_body = _build_target_request_parts()
        token_previews = _apply_v2_token_consumers(
            profile=profile,
            flows=matching_flows,
            method=method,
            tool_url=_api_monitor_tool_url(doc),
            absolute_url=url,
            headers=request_headers,
            query=request_query,
            body=request_body,
        )
        for preview in token_previews:
            preview["refreshed"] = True
        consumer_error = next((preview.get("error") for preview in token_previews if preview.get("error")), "")
        if consumer_error:
            return {"success": False, "error": consumer_error}
        request_kwargs = {"params": request_query, "headers": request_headers}
        if request_body:
            request_kwargs["json"] = request_body
        response = await client.request(method, url, **request_kwargs)
```

preview 生成中使用：

```python
auth_preview = dict(auth_application.preview) if auth_application.preview else {}
auth_preview["profile"] = profile.preview()
auth_preview["token_flows"] = token_previews
```

- [ ] **Step 5: 运行 runtime 测试**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/deepagent/test_mcp_runtime.py -q
```

预期：通过；如旧 token flow 测试仍使用 V1 shape，应迁移到 V2 shape。

- [ ] **Step 6: 提交**

```bash
git add RpaClaw/backend/deepagent/mcp_runtime.py RpaClaw/backend/tests/deepagent/test_mcp_runtime.py
git commit -m "refactor: use runtime profile for api monitor mcp calls"
```

---

### Task 5: 发布和 MCP 配置支持自动 + 人工 Flow

**文件：**

- 修改：`RpaClaw/backend/rpa/api_monitor/models.py`
- 修改：`RpaClaw/backend/route/api_monitor.py`
- 修改：`RpaClaw/backend/rpa/api_monitor_token_flow.py`
- 修改：`RpaClaw/backend/rpa/api_monitor_mcp_registry.py`
- 修改：`RpaClaw/backend/route/mcp.py`
- 测试：`RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`
- 测试：`RpaClaw/backend/tests/test_mcp_route.py`

- [ ] **Step 1: 写发布人工 flow 的失败测试**

追加到 `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`：

```python
def test_publish_persists_manual_token_flow(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo()
    tool_repo = _MemoryRepo()

    monkeypatch.setattr(
        "backend.rpa.api_monitor_mcp_registry.get_repository",
        lambda collection_name: server_repo if collection_name == "user_mcp_servers" else tool_repo,
    )
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: _build_session())

    response = client.post(
        "/api/v1/api-monitor/session/session_1/publish-mcp",
        json={
            "mcp_name": "Manual Flow MCP",
            "description": "Captured APIs",
            "confirm_overwrite": False,
            "api_monitor_auth": {
                "credential_type": "placeholder",
                "credential_id": "",
                "manual_token_flows": [
                    {
                        "id": "manual_csrf",
                        "name": "csrf_token",
                        "producer": {
                            "request": {"method": "GET", "url": "/api/session"},
                            "extract": [{"name": "csrf_token", "from": "response.body", "path": "$.csrfToken"}],
                        },
                        "consumers": [
                            {
                                "method": "GET",
                                "url": "/api/orders",
                                "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                            }
                        ],
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    saved_server = next(iter(server_repo.docs.values()))
    flows = saved_server["api_monitor_auth"]["token_flows"]
    assert flows[0]["id"] == "manual_csrf"
    assert flows[0]["source"] == "manual"
```

- [ ] **Step 2: 增加 request models**

修改 `RpaClaw/backend/rpa/api_monitor/models.py`：

```python
class ManualTokenFlowRequest(BaseModel):
    id: str
    name: str
    enabled: bool = True
    producer: Dict
    consumers: List[Dict]
    refresh_on_status: List[int] = Field(default_factory=lambda: [401, 403, 419])


class ApiMonitorAuthConfigRequest(BaseModel):
    credential_type: str = "placeholder"
    credential_id: str = ""
    login_url: str = ""
    token_flows: List[TokenFlowSelection] = Field(default_factory=list)
    manual_token_flows: List[ManualTokenFlowRequest] = Field(default_factory=list)
```

- [ ] **Step 3: 发布时合并自动 flow 和人工 flow**

修改 `RpaClaw/backend/route/api_monitor.py`：

```python
from backend.rpa.api_monitor_token_flow import resolve_token_flows_for_publish, validate_manual_token_flow
```

在 `publish_mcp()` 中解析 selected auto flows 后添加：

```python
manual_flows = []
if request.api_monitor_auth and request.api_monitor_auth.manual_token_flows:
    try:
        manual_flows = [
            validate_manual_token_flow(flow.model_dump())
            for flow in request.api_monitor_auth.manual_token_flows
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

combined_flows = [*token_flows, *manual_flows]
if combined_flows:
    api_monitor_auth["token_flows"] = combined_flows
```

- [ ] **Step 4: Registry 保留 V2 token flows**

修改 `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`，避免 `normalize_api_monitor_auth_config()` 丢掉已校验的 `token_flows`：

```python
normalized_auth = normalize_api_monitor_auth_config(api_monitor_auth)
if isinstance(api_monitor_auth, Mapping) and api_monitor_auth.get("token_flows"):
    normalized_auth["token_flows"] = list(api_monitor_auth["token_flows"])
```

- [ ] **Step 5: 补 MCP 编辑接口测试**

在 `RpaClaw/backend/tests/test_mcp_route.py` 增加测试：通过现有 MCP config endpoint 更新一个 API Monitor MCP，payload 中包含一个 manual token flow。断言：

```python
assert updated["api_monitor_auth"]["token_flows"][0]["source"] == "manual"
assert "headers" not in updated["endpoint_config"]
assert "query" not in updated["endpoint_config"]
```

- [ ] **Step 6: 实现 V1 token flow 迁移**

在 `RpaClaw/backend/tests/test_api_monitor_token_flow.py` 增加测试：

```python
from backend.rpa.api_monitor_token_flow import normalize_token_flow_config


def test_normalize_token_flow_config_converts_legacy_v1_flow():
    flow = normalize_token_flow_config(
        {
            "id": "legacy_csrf",
            "name": "csrf_token",
            "setup": {"method": "GET", "url": "/api/session"},
            "extract": {"from": "response.body", "path": "$.csrfToken", "name": "csrf_token"},
            "inject": {
                "method": "GET",
                "url": "/api/orders",
                "to": "request.headers",
                "name": "X-CSRF-Token",
            },
        }
    )

    assert flow["producer"]["request"] == {"method": "GET", "url": "/api/session"}
    assert flow["producer"]["extract"] == [
        {"name": "csrf_token", "from": "response.body", "path": "$.csrfToken", "secret": True}
    ]
    assert flow["consumers"] == [
        {
            "method": "GET",
            "url": "/api/orders",
            "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}, "query": {}, "body": {}},
        }
    ]
```

在 `RpaClaw/backend/rpa/api_monitor_token_flow.py` 增加：

```python
def normalize_token_flow_config(flow: dict[str, Any]) -> dict[str, Any]:
    if "producer" in flow and "consumers" in flow:
        return flow
    if "setup" in flow and "extract" in flow and "inject" in flow:
        return legacy_v1_to_v2(flow)
    raise ValueError("token flow config is neither V2 nor migratable V1")


def legacy_v1_to_v2(flow: dict[str, Any]) -> dict[str, Any]:
    setup = flow.get("setup") or {}
    extract = flow.get("extract") or {}
    inject = flow.get("inject") or {}
    method = str(inject.get("method") or "").upper()
    url = str(inject.get("url") or "").strip()
    location = str(inject.get("to") or "")
    header_or_param_name = str(inject.get("name") or "").strip()
    token_name = str(extract.get("name") or flow.get("name") or "token").strip()
    template = "{{ " + token_name + " }}"
    if not method or not url:
        raise ValueError("legacy token flow inject method/url is required")
    if location == "request.headers":
        inject_doc = {"headers": {header_or_param_name: template}, "query": {}, "body": {}}
    elif location == "request.query":
        inject_doc = {"headers": {}, "query": {header_or_param_name: template}, "body": {}}
    elif location == "request.body":
        inject_doc = {"headers": {}, "query": {}, "body": {header_or_param_name: template}}
    else:
        raise ValueError("legacy token flow inject target is unsupported")
    return {
        "id": str(flow.get("id") or f"flow_{token_name}"),
        "name": token_name,
        "source": str(flow.get("source") or "auto"),
        "enabled": bool(flow.get("enabled", True)),
        "producer": {
            "request": {"method": str(setup.get("method") or "GET").upper(), "url": str(setup.get("url") or "")},
            "extract": [
                {
                    "name": token_name,
                    "from": str(extract.get("from") or "response.body"),
                    "path": str(extract.get("path") or ""),
                    "secret": True,
                }
            ],
        },
        "consumers": [{"method": method, "url": url, "inject": inject_doc}],
        "refresh_on_status": list(flow.get("refresh_on_status") or [401, 403, 419]),
        "confidence": str(flow.get("confidence") or "medium"),
        "summary": flow.get("summary") or {"producer": f"{setup.get('method', 'GET')} {setup.get('url', '')}", "consumers": [f"{method} {url} {location}.{header_or_param_name}"]},
    }
```

在 registry 和 MCP 编辑保存路径中，保存前对每个 flow 执行 `normalize_token_flow_config()`，无法迁移时返回明确 `400` 错误。

- [ ] **Step 7: 运行 route 测试**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_token_flow.py tests/test_api_monitor_publish_mcp.py tests/test_mcp_route.py -q
```

预期：通过。

- [ ] **Step 8: 提交**

```bash
git add RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/route/api_monitor.py RpaClaw/backend/rpa/api_monitor_token_flow.py RpaClaw/backend/rpa/api_monitor_mcp_registry.py RpaClaw/backend/route/mcp.py RpaClaw/backend/tests/test_api_monitor_token_flow.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py RpaClaw/backend/tests/test_mcp_route.py
git commit -m "feat: support manual api monitor token flows"
```

---

### Task 6: 前端去重展示和人工编辑

**文件：**

- 修改：`RpaClaw/frontend/src/api/apiMonitor.ts`
- 修改：`RpaClaw/frontend/src/api/mcp.ts`
- 修改：`RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
- 修改：`RpaClaw/frontend/src/components/tools/ApiMonitorMcpEditDialog.vue`
- 修改：`RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`
- 修改：`RpaClaw/frontend/src/locales/en.ts`
- 修改：`RpaClaw/frontend/src/locales/zh.ts`

- [ ] **Step 1: 增加 TypeScript 类型**

在 `RpaClaw/frontend/src/api/apiMonitor.ts` 中增加：

```ts
export interface ApiMonitorManualTokenFlow {
  id: string
  name: string
  enabled?: boolean
  producer: {
    request: {
      method: string
      url: string
      headers?: Record<string, string>
      query?: Record<string, string>
      body?: unknown
      content_type?: string
    }
    extract: Array<{ name: string; from: string; path: string; secret?: boolean }>
  }
  consumers: Array<{
    method: string
    url: string
    inject: {
      headers?: Record<string, string>
      query?: Record<string, string>
      body?: Record<string, string>
    }
  }>
  refresh_on_status?: number[]
}
```

扩展 `ApiMonitorAuthConfig`：

```ts
manual_token_flows?: ApiMonitorManualTokenFlow[]
```

- [ ] **Step 2: 发布弹窗展示去重信息**

在 `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue` 的 token flow candidate 卡片中展示：

```vue
<div class="mt-1 text-[11px] opacity-70">
  {{ t('Samples: {count}', { count: flow.sample_count || 1 }) }}
</div>
```

consumer 列表直接使用后端返回的去重后 `consumer_summaries`。

- [ ] **Step 3: 增加人工 flow JSON 草稿**

在 `ApiMonitorPage.vue` 增加：

```ts
const manualTokenFlowJson = ref('');
const manualTokenFlowJsonError = ref('');

const parseManualTokenFlows = (): ApiMonitorManualTokenFlow[] => {
  manualTokenFlowJsonError.value = '';
  if (!manualTokenFlowJson.value.trim()) return [];
  try {
    const parsed = JSON.parse(manualTokenFlowJson.value);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch (error) {
    manualTokenFlowJsonError.value = error instanceof Error ? error.message : 'Invalid JSON';
    return [];
  }
};
```

发布 payload 中加入：

```ts
const manualTokenFlows = parseManualTokenFlows();
if (manualTokenFlowJsonError.value) {
  return;
}

manual_token_flows: manualTokenFlows,
```

- [ ] **Step 4: 发布弹窗增加人工 JSON 输入**

在自动 flow 候选下方增加：

```vue
<label class="mb-4 block">
  <span class="mb-1 block text-xs font-medium text-[var(--text-secondary)]">
    {{ t('Manual token flows JSON') }}
  </span>
  <textarea
    v-model="manualTokenFlowJson"
    class="h-32 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 font-mono text-xs"
    :placeholder="t('Paste manual token flow JSON')"
  />
  <span v-if="manualTokenFlowJsonError" class="mt-1 block text-xs text-red-500">
    {{ manualTokenFlowJsonError }}
  </span>
</label>
```

- [ ] **Step 5: 工具库编辑弹窗支持 token flow JSON**

在 `RpaClaw/frontend/src/components/tools/ApiMonitorMcpEditDialog.vue` 中增加 advanced token flow JSON 编辑区域，绑定 `api_monitor_auth.token_flows`。保存时将现有 token flows 原样随 `api_monitor_auth.token_flows` 发送。

- [ ] **Step 6: 工具库详情展示 flow 摘要**

在 `ApiMonitorMcpDetailDialog.vue` 中展示：

- flow name
- source：`auto` 或 `manual`
- producer request
- consumers
- sample count

- [ ] **Step 7: 增加 i18n**

`en.ts`：

```ts
'Samples: {count}': 'Samples: {count}',
'Manual token flows JSON': 'Manual token flows JSON',
'Paste manual token flow JSON': 'Paste manual token flow JSON',
'Invalid JSON': 'Invalid JSON',
```

`zh.ts`：

```ts
'Samples: {count}': '样本数：{count}',
'Manual token flows JSON': '手动 Token Flow JSON',
'Paste manual token flow JSON': '粘贴手动 Token Flow JSON',
'Invalid JSON': '无效的 JSON',
```

- [ ] **Step 8: 构建检查**

```bash
cd RpaClaw/frontend
npm run build
```

预期：通过。

- [ ] **Step 9: 提交**

```bash
git add RpaClaw/frontend/src/api/apiMonitor.ts RpaClaw/frontend/src/api/mcp.ts RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue RpaClaw/frontend/src/components/tools/ApiMonitorMcpEditDialog.vue RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue RpaClaw/frontend/src/locales/en.ts RpaClaw/frontend/src/locales/zh.ts
git commit -m "feat: add manual api monitor token flow editor"
```

---

### Task 7: 验证与迁移

**文件：**

- 仅修改验证失败所需的文件。

- [ ] **Step 1: 运行后端核心测试**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_runtime_profile.py tests/test_api_monitor_auth.py tests/test_api_monitor_token_flow.py tests/test_api_monitor_publish_mcp.py tests/test_mcp_route.py tests/deepagent/test_mcp_runtime.py -q
```

预期：通过。

- [ ] **Step 2: 运行 API Monitor 相关后端测试**

```bash
cd RpaClaw/backend
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_api_monitor_capture.py tests/test_api_monitor_confidence.py tests/test_api_monitor_mcp_contract.py tests/test_api_monitor_publish_mcp.py tests/deepagent/test_mcp_registry.py tests/deepagent/test_mcp_runtime.py -q
```

预期：通过。

- [ ] **Step 3: 前端构建**

```bash
cd RpaClaw/frontend
npm run build
```

预期：通过。

- [ ] **Step 4: 手动 smoke test**

使用一个同时需要登录 token 和 CSRF token 的 API：

1. 录制登录和 `/api/orders`。
2. 发布 MCP，选择 `credential_type=test`。
3. 确认 token flow 候选已经去重。
4. 如自动识别不完整，添加或修正一个 manual flow。
5. 调用 `list_orders`。
6. 确认 request preview 中包含：

```json
{
  "auth": {
    "profile": {
      "headers": ["Authorization"],
      "variables": ["auth_token", "csrf_token"]
    },
    "token_flows": [
      {
        "producer_applied": true,
        "consumer_applied": true
      }
    ]
  }
}
```

7. 确认 preview 中没有原始 `auth_token` 或 `csrf_token` 明文。

- [ ] **Step 5: V1 迁移检查**

确认 Task 5 中的 `normalize_token_flow_config()` 已覆盖旧 V1 flows：

```python
assert normalize_token_flow_config(v2_flow) == v2_flow
assert normalize_token_flow_config(legacy_v1_flow)["producer"]["request"]["url"] == "/api/session"
```

如果遇到无法确定目标 method/url 的旧 V1 flow，确认接口返回明确错误，提示用户重新发布或人工配置。

- [ ] **Step 6: 提交验证修复**

如果验证中有修复：

```bash
git add <fixed-files>
git commit -m "fix: harden api monitor runtime profile token flow"
```

如果没有修复，不创建空提交。

---

## 自查说明

覆盖点：

- Runtime profile 持有 auth token、cookie、headers、动态变量：Task 1、2、4。
- 登录先于 token producer：Task 4。
- token producer 使用 profile 中的认证材料：Task 4。
- 目标 consumer 同时使用 auth token 和动态 token：Task 4。
- 一个 flow 多个 consumers，runtime 只注入当前目标请求匹配的 consumer：Task 4。
- consumer 未匹配、producer 提取为空、模板变量缺失三类问题可区分：Task 1、4、7。
- `refresh_on_status` 触发 producer refresh 并重试一次：Task 4。
- 重复录制同接口去重：Task 3。
- 支持发布时人工配置和发布后编辑：Task 5、6。
- 旧 V1 token flow 迁移或明确失败：Task 5、7。
- 持久化配置和 preview 不泄漏 token 明文：Task 1、3、7。

执行顺序：

1. 先构建 runtime profile。
2. 再让认证写入 profile。
3. 修正 token flow 数据模型、去重和人工校验。
4. 围绕 profile 重排 runtime。
5. 持久化并编辑 V2 flows。
6. 增加前端人工配置。
7. 验证并处理迁移。
