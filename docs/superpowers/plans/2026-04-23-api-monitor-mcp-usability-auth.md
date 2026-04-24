# API Monitor MCP Usability Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make API Monitor MCP tools understandable, editable, authenticated, default-enabled, and callable from chat through a YAML-backed execution contract.

**Architecture:** Add a backend contract parser that treats each tool YAML as the source of truth, persist parsed contracts beside raw YAML, expose API Monitor-specific management/detail endpoints, and update the runtime to execute HTTP calls from those contracts plus shared MCP-level auth config. Add a dedicated Tool Library detail view for API Monitor MCPs so users can rename tools, inspect YAML/request previews, configure shared headers/auth once, and test calls before relying on chat.

**Tech Stack:** FastAPI, Pydantic v2, async repository abstraction for MongoDB/local JSON storage, httpx, PyYAML, Vue 3 Composition API, TypeScript, Vite, Tailwind CSS.

---

## File Map

Backend files:

- Create `RpaClaw/backend/rpa/api_monitor_mcp_contract.py`: YAML parsing, validation, template variable extraction, request preview sanitization, and argument-to-request mapping helpers.
- Modify `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`: parse all API Monitor tool YAML during publish/update, persist parsed fields and validation status, enforce duplicate tool name rules, and default API Monitor MCPs to enabled/default-enabled.
- Modify `RpaClaw/backend/route/mcp.py`: add API Monitor MCP detail/config/tool update/test endpoints while preserving normal MCP discovery behavior.
- Modify `RpaClaw/backend/deepagent/mcp_runtime.py`: make `ApiMonitorMcpRuntime` list only valid tools and execute HTTP calls using parsed contracts plus shared config.
- Modify `RpaClaw/backend/deepagent/mcp_registry.py`: ensure API Monitor MCP credential bindings are resolved before runtime construction and default-enabled API Monitor MCPs participate in new session effective MCPs.
- Modify `RpaClaw/backend/mcp/models.py`: normalize `endpoint_config` as a dictionary containing `base_url`, `headers`, `query`, and `timeout_ms`.
- Modify `RpaClaw/backend/storage/__init__.py`: ensure local-mode collections remain available and no DB-only assumptions are introduced.
- Test `RpaClaw/backend/tests/test_api_monitor_mcp_contract.py`: parser, validation, mapping, preview sanitization.
- Test `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`: publish/update persistence and default-enabled behavior.
- Test `RpaClaw/backend/tests/test_mcp_route.py`: detail/config/update/test endpoints and invalid-tool discovery filtering.
- Test `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`: runtime HTTP call mapping, shared auth/query merge, credential error shape, non-2xx response shape.
- Test `RpaClaw/backend/tests/deepagent/test_mcp_registry.py`: API Monitor MCP inclusion for default-enabled new sessions.

Frontend files:

- Modify `RpaClaw/frontend/src/api/mcp.ts`: add API Monitor MCP detail/config/tool update/test request and response types.
- Create `RpaClaw/frontend/src/utils/apiMonitorMcp.ts`: YAML name/description syncing helpers, validation status formatting, sample argument generation, and request preview presentation helpers.
- Test `RpaClaw/frontend/src/utils/apiMonitorMcp.test.ts`: utility behavior independent of Vue rendering.
- Create `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`: dedicated API Monitor MCP detail/config/test dialog.
- Modify `RpaClaw/frontend/src/pages/ToolsPage.vue`: open the dedicated dialog for API Monitor MCPs and keep normal MCPs on the existing simplified discovery dialog.
- Modify `RpaClaw/frontend/src/components/toolViews/McpToolView.vue`: display sanitized request preview/status for API Monitor MCP calls when returned by runtime.
- Modify `RpaClaw/frontend/src/utils/mcpUi.ts`: classify API Monitor MCP/tool-call metadata consistently.
- Modify `RpaClaw/frontend/src/utils/mcpUi.test.ts`: cover API Monitor display names and sanitized runtime metadata.
- Modify `RpaClaw/frontend/src/locales/en.ts` and `RpaClaw/frontend/src/locales/zh.ts`: add all new UI strings.

---

## Task 1: YAML Contract Parser And Validator

**Files:**

- Create: `RpaClaw/backend/rpa/api_monitor_mcp_contract.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_mcp_contract.py`

- [ ] **Step 1: Write parser tests for valid GET and POST YAML**

Create `RpaClaw/backend/tests/test_api_monitor_mcp_contract.py` with these tests:

```python
from backend.rpa.api_monitor_mcp_contract import parse_api_monitor_tool_yaml


def test_parse_get_tool_yaml_builds_contract():
    contract = parse_api_monitor_tool_yaml(
        """
name: search_orders
description: Search orders by keyword and status
method: GET
url: /api/orders
parameters:
  type: object
  properties:
    keyword:
      type: string
      description: Order id, phone, or username
    status:
      type: string
      description: Order status
request:
  query:
    keyword: "{{ keyword }}"
    status: "{{ status }}"
response:
  type: object
"""
    )

    assert contract.valid is True
    assert contract.name == "search_orders"
    assert contract.method == "GET"
    assert contract.url == "/api/orders"
    assert contract.input_schema["properties"]["keyword"]["type"] == "string"
    assert contract.query_mapping == {"keyword": "{{ keyword }}", "status": "{{ status }}"}
    assert contract.body_mapping == {}
    assert contract.validation_errors == []


def test_parse_post_tool_yaml_builds_body_contract():
    contract = parse_api_monitor_tool_yaml(
        """
name: create_user
description: Create a user
method: POST
url: /api/users
parameters:
  type: object
  properties:
    name:
      type: string
    email:
      type: string
request:
  body:
    name: "{{ name }}"
    email: "{{ email }}"
response:
  type: object
"""
    )

    assert contract.valid is True
    assert contract.name == "create_user"
    assert contract.method == "POST"
    assert contract.body_mapping == {"name": "{{ name }}", "email": "{{ email }}"}
    assert contract.response_schema == {"type": "object"}
```

- [ ] **Step 2: Write validation tests for invalid YAML**

Append these tests to `RpaClaw/backend/tests/test_api_monitor_mcp_contract.py`:

```python
def test_invalid_yaml_reports_parse_error():
    contract = parse_api_monitor_tool_yaml("name: [broken")

    assert contract.valid is False
    assert contract.name == ""
    assert any("YAML" in error for error in contract.validation_errors)


def test_missing_required_fields_are_invalid():
    contract = parse_api_monitor_tool_yaml(
        """
name: ""
description: Missing request shape
parameters:
  type: object
  properties: {}
"""
    )

    assert contract.valid is False
    assert "name is required" in contract.validation_errors
    assert "method is required" in contract.validation_errors
    assert "url is required" in contract.validation_errors


def test_mapping_unknown_parameter_is_invalid():
    contract = parse_api_monitor_tool_yaml(
        """
name: search_orders
description: Search orders
method: GET
url: /api/orders
parameters:
  type: object
  properties:
    keyword:
      type: string
request:
  query:
    keyword: "{{ keyword }}"
    status: "{{ status }}"
"""
    )

    assert contract.valid is False
    assert "request.query.status references unknown parameter 'status'" in contract.validation_errors


def test_tool_name_must_be_mcp_safe():
    contract = parse_api_monitor_tool_yaml(
        """
name: search orders
description: Search orders
method: GET
url: /api/orders
parameters:
  type: object
  properties: {}
"""
    )

    assert contract.valid is False
    assert "name must match ^[A-Za-z_][A-Za-z0-9_]*$" in contract.validation_errors
```

- [ ] **Step 3: Run parser tests and confirm failure**

Run:

```bash
RpaClaw/backend/.venv/bin/python -m pytest RpaClaw/backend/tests/test_api_monitor_mcp_contract.py -q
```

Expected: FAIL because `backend.rpa.api_monitor_mcp_contract` does not exist yet.

- [ ] **Step 4: Implement contract parser model and validation**

Create `RpaClaw/backend/rpa/api_monitor_mcp_contract.py` with:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

import yaml


TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TEMPLATE_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class ApiMonitorToolContract:
    name: str = ""
    description: str = ""
    method: str = ""
    url: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    path_mapping: dict[str, Any] = field(default_factory=dict)
    query_mapping: dict[str, Any] = field(default_factory=dict)
    body_mapping: dict[str, Any] = field(default_factory=dict)
    header_mapping: dict[str, Any] = field(default_factory=dict)
    response_schema: dict[str, Any] = field(default_factory=dict)
    yaml_definition: str = ""
    valid: bool = False
    validation_errors: list[str] = field(default_factory=list)

    def to_document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "method": self.method,
            "url": self.url,
            "input_schema": self.input_schema,
            "path_mapping": self.path_mapping,
            "query_mapping": self.query_mapping,
            "body_mapping": self.body_mapping,
            "header_mapping": self.header_mapping,
            "response_schema": self.response_schema,
            "yaml_definition": self.yaml_definition,
            "validation_status": "valid" if self.valid else "invalid",
            "validation_errors": self.validation_errors,
        }


def parse_api_monitor_tool_yaml(yaml_definition: str) -> ApiMonitorToolContract:
    errors: list[str] = []
    try:
        raw = yaml.safe_load(yaml_definition) or {}
    except yaml.YAMLError as exc:
        return ApiMonitorToolContract(
            yaml_definition=yaml_definition,
            valid=False,
            validation_errors=[f"YAML parse error: {exc}"],
        )

    if not isinstance(raw, dict):
        return ApiMonitorToolContract(
            yaml_definition=yaml_definition,
            valid=False,
            validation_errors=["YAML root must be an object"],
        )

    name = str(raw.get("name") or "").strip()
    description = str(raw.get("description") or "").strip()
    method = str(raw.get("method") or "").strip().upper()
    url = str(raw.get("url") or "").strip()
    input_schema = _as_dict(raw.get("parameters"))
    request = _as_dict(raw.get("request"))
    response_schema = _as_dict(raw.get("response"))
    path_mapping = _as_dict(request.get("path"))
    query_mapping = _as_dict(request.get("query"))
    body_mapping = _as_dict(request.get("body"))
    header_mapping = _as_dict(request.get("headers"))

    if not name:
        errors.append("name is required")
    elif not TOOL_NAME_RE.match(name):
        errors.append("name must match ^[A-Za-z_][A-Za-z0-9_]*$")

    if not description:
        errors.append("description is required")

    if not method:
        errors.append("method is required")
    elif method not in ALLOWED_METHODS:
        errors.append(f"method must be one of {', '.join(sorted(ALLOWED_METHODS))}")

    if not url:
        errors.append("url is required")

    if input_schema.get("type") != "object":
        errors.append("parameters.type must be object")

    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        errors.append("parameters.properties must be an object")
        property_names: set[str] = set()
    else:
        property_names = set(properties.keys())

    _validate_mapping_variables("request.path", path_mapping, property_names, errors)
    _validate_mapping_variables("request.query", query_mapping, property_names, errors)
    _validate_mapping_variables("request.body", body_mapping, property_names, errors)
    _validate_mapping_variables("request.headers", header_mapping, property_names, errors)

    return ApiMonitorToolContract(
        name=name,
        description=description,
        method=method,
        url=url,
        input_schema=input_schema,
        path_mapping=path_mapping,
        query_mapping=query_mapping,
        body_mapping=body_mapping,
        header_mapping=header_mapping,
        response_schema=response_schema,
        yaml_definition=yaml_definition,
        valid=not errors,
        validation_errors=errors,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _validate_mapping_variables(prefix: str, mapping: Mapping[str, Any], property_names: set[str], errors: list[str]) -> None:
    for key, value in mapping.items():
        for variable in _extract_template_variables(value):
            if variable not in property_names:
                errors.append(f"{prefix}.{key} references unknown parameter '{variable}'")


def _extract_template_variables(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(TEMPLATE_RE.findall(value))
    if isinstance(value, Mapping):
        variables: set[str] = set()
        for nested in value.values():
            variables.update(_extract_template_variables(nested))
        return variables
    if isinstance(value, list):
        variables: set[str] = set()
        for nested in value:
            variables.update(_extract_template_variables(nested))
        return variables
    return set()
```

- [ ] **Step 5: Run parser tests and confirm pass**

Run:

```bash
RpaClaw/backend/.venv/bin/python -m pytest RpaClaw/backend/tests/test_api_monitor_mcp_contract.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit parser changes**

Run:

```bash
git add RpaClaw/backend/rpa/api_monitor_mcp_contract.py RpaClaw/backend/tests/test_api_monitor_mcp_contract.py
git commit -m "feat: add api monitor mcp yaml contracts"
```

Expected: commit succeeds.

---

## Task 2: Persist Parsed Contracts And Default-Enabled API Monitor MCPs

**Files:**

- Modify: `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`

- [ ] **Step 1: Add failing publish tests for parsed fields and defaults**

Append tests to `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`:

```python
@pytest.mark.anyio
async def test_publish_persists_parsed_contract_fields_and_defaults():
    server_repo = _FakeRepository([])
    tool_repo = _FakeRepository([])
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    result = await registry.publish(
        session=_build_session(),
        user_id="user_1",
        name="Orders MCP",
        description="Order APIs",
        overwrite=False,
    )

    server = server_repo.docs[0]
    tools = tool_repo.docs

    assert result["server"]["default_enabled"] is True
    assert server["enabled"] is True
    assert server["default_enabled"] is True
    assert server["transport"] == "api_monitor"
    assert server["source_type"] == "api_monitor"
    assert tools[0]["validation_status"] == "valid"
    assert tools[0]["name"] == "search_orders"
    assert tools[0]["method"] == "GET"
    assert tools[0]["url"] == "/api/orders"
    assert tools[0]["input_schema"]["type"] == "object"
    assert tools[0]["query_mapping"]["keyword"] == "{{ keyword }}"
    assert tools[0]["yaml_definition"].strip().startswith("name:")
```

Update `_build_session()` so at least one tool contains this YAML:

```python
yaml_definition="""name: search_orders
description: Search orders by keyword
method: GET
url: /api/orders
parameters:
  type: object
  properties:
    keyword:
      type: string
request:
  query:
    keyword: "{{ keyword }}"
response:
  type: object
"""
```

- [ ] **Step 2: Add failing duplicate-name validation test**

Append:

```python
@pytest.mark.anyio
async def test_publish_marks_duplicate_tool_names_invalid():
    session = _build_session()
    session.tools.append(session.tools[0].model_copy(deep=True))
    server_repo = _FakeRepository([])
    tool_repo = _FakeRepository([])
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    await registry.publish(
        session=session,
        user_id="user_1",
        name="Orders MCP",
        description="Order APIs",
        overwrite=False,
    )

    statuses = [tool["validation_status"] for tool in tool_repo.docs]
    errors = [error for tool in tool_repo.docs for error in tool["validation_errors"]]
    assert statuses == ["invalid", "invalid"]
    assert "duplicate tool name 'search_orders' in this API Monitor MCP" in errors
```

- [ ] **Step 3: Run publish tests and confirm failure**

Run:

```bash
RpaClaw/backend/.venv/bin/python -m pytest RpaClaw/backend/tests/test_api_monitor_publish_mcp.py -q
```

Expected: FAIL because registry still persists old unparsed fields and `default_enabled` may be false.

- [ ] **Step 4: Update registry publish transformation**

In `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`, import parser:

```python
from backend.rpa.api_monitor_mcp_contract import ApiMonitorToolContract, parse_api_monitor_tool_yaml
```

Add helper:

```python
def _parse_tools_with_duplicate_validation(tools: list[Any]) -> list[ApiMonitorToolContract]:
    parsed = [parse_api_monitor_tool_yaml(str(getattr(tool, "yaml_definition", "") or "")) for tool in tools]
    counts: dict[str, int] = {}
    for contract in parsed:
        if contract.name:
            counts[contract.name] = counts.get(contract.name, 0) + 1

    result: list[ApiMonitorToolContract] = []
    for contract in parsed:
        if contract.name and counts.get(contract.name, 0) > 1:
            result.append(
                ApiMonitorToolContract(
                    name=contract.name,
                    description=contract.description,
                    method=contract.method,
                    url=contract.url,
                    input_schema=contract.input_schema,
                    path_mapping=contract.path_mapping,
                    query_mapping=contract.query_mapping,
                    body_mapping=contract.body_mapping,
                    header_mapping=contract.header_mapping,
                    response_schema=contract.response_schema,
                    yaml_definition=contract.yaml_definition,
                    valid=False,
                    validation_errors=[
                        *contract.validation_errors,
                        f"duplicate tool name '{contract.name}' in this API Monitor MCP",
                    ],
                )
            )
        else:
            result.append(contract)
    return result
```

In `publish(...)`, set server fields:

```python
"enabled": True,
"default_enabled": True,
"transport": "api_monitor",
"source_type": "api_monitor",
"endpoint_config": existing_endpoint_config_or_default,
"credential_binding": existing_credential_binding_or_default,
```

When inserting tools, merge parsed document:

```python
contract_docs = _parse_tools_with_duplicate_validation(session.tools)
for index, contract in enumerate(contract_docs):
    await self._tools.insert_one({
        "mcp_server_id": server_id,
        "user_id": user_id,
        "source": "api_monitor",
        "source_session_id": session.id,
        "order": index,
        **contract.to_document(),
    })
```

When overwriting an existing API Monitor MCP, delete old child tools and replace them with the new parsed docs while preserving user-edited MCP-level `endpoint_config` and `credential_binding`.

- [ ] **Step 5: Run publish tests and confirm pass**

Run:

```bash
RpaClaw/backend/.venv/bin/python -m pytest RpaClaw/backend/tests/test_api_monitor_publish_mcp.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit registry changes**

Run:

```bash
git add RpaClaw/backend/rpa/api_monitor_mcp_registry.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py
git commit -m "feat: persist api monitor mcp contracts"
```

Expected: commit succeeds.

---

## Task 3: API Monitor MCP Detail, Config, Tool Update, And Test Endpoints

**Files:**

- Modify: `RpaClaw/backend/route/mcp.py`
- Modify: `RpaClaw/backend/tests/test_mcp_route.py`

- [ ] **Step 1: Add failing route tests for dedicated detail and invalid discovery filtering**

Append to `RpaClaw/backend/tests/test_mcp_route.py`:

```python
def test_api_monitor_mcp_detail_returns_yaml_and_contract(monkeypatch):
    server_doc = {
        "_id": "mcp_api_monitor",
        "name": "Orders MCP",
        "description": "Order APIs",
        "transport": "api_monitor",
        "source_type": "api_monitor",
        "enabled": True,
        "default_enabled": True,
        "endpoint_config": {"base_url": "https://example.com", "headers": {"X-App": "web"}, "query": {}, "timeout_ms": 30000},
        "credential_binding": {"headers": {"Authorization": "Bearer {{ api.password }}"}},
        "user_id": "user_1",
    }
    tool_doc = {
        "_id": "tool_1",
        "mcp_server_id": "mcp_api_monitor",
        "user_id": "user_1",
        "name": "search_orders",
        "description": "Search orders",
        "method": "GET",
        "url": "/api/orders",
        "yaml_definition": "name: search_orders\n",
        "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
        "query_mapping": {"keyword": "{{ keyword }}"},
        "path_mapping": {},
        "body_mapping": {},
        "header_mapping": {},
        "response_schema": {"type": "object"},
        "validation_status": "valid",
        "validation_errors": [],
        "source": "api_monitor",
    }
    monkeypatch.setattr(mcp_route, "_get_owned_user_mcp_server", AsyncMock(return_value=server_doc))
    monkeypatch.setattr(mcp_route, "_load_api_monitor_tool_documents", AsyncMock(return_value=[tool_doc]))

    response = client.get("/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-detail")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["server"]["name"] == "Orders MCP"
    assert data["server"]["endpoint_config"]["base_url"] == "https://example.com"
    assert data["tools"][0]["yaml_definition"] == "name: search_orders\n"
    assert data["tools"][0]["query_mapping"] == {"keyword": "{{ keyword }}"}


def test_discover_api_monitor_tools_excludes_invalid_tools(monkeypatch):
    monkeypatch.setattr(
        mcp_route,
        "_resolve_mcp_server_by_key",
        AsyncMock(return_value={"id": "mcp_api_monitor", "source_type": "api_monitor", "transport": "api_monitor"}),
    )
    monkeypatch.setattr(
        mcp_route,
        "_load_api_monitor_tool_documents",
        AsyncMock(return_value=[
            {"name": "valid_tool", "description": "Valid", "input_schema": {"type": "object"}, "validation_status": "valid"},
            {"name": "broken_tool", "description": "Broken", "input_schema": {"type": "object"}, "validation_status": "invalid"},
        ]),
    )

    response = client.post("/api/v1/mcp/servers/user:mcp_api_monitor/discover-tools")

    assert response.status_code == 200
    tools = response.json()["data"]["tools"]
    assert [tool["name"] for tool in tools] == ["valid_tool"]
```

- [ ] **Step 2: Add failing route tests for config and tool YAML update**

Append:

```python
def test_update_api_monitor_mcp_config_saves_shared_auth(monkeypatch):
    update_one = AsyncMock()
    monkeypatch.setattr(mcp_route, "_get_owned_user_mcp_server", AsyncMock(return_value={"_id": "mcp_api_monitor", "user_id": "user_1", "source_type": "api_monitor"}))
    monkeypatch.setattr(mcp_route, "get_repository", lambda name: SimpleNamespace(update_one=update_one))

    response = client.put(
        "/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-config",
        json={
            "name": "Orders MCP",
            "description": "Order APIs",
            "enabled": True,
            "default_enabled": True,
            "endpoint_config": {
                "base_url": "https://example.com",
                "headers": {"X-App": "web"},
                "query": {"tenant": "main"},
                "timeout_ms": 45000,
            },
            "credential_binding": {
                "headers": {"Authorization": "Bearer {{ api.password }}"},
                "query": {},
                "env": {},
            },
        },
    )

    assert response.status_code == 200
    update = update_one.await_args.args[1]["$set"]
    assert update["endpoint_config"]["base_url"] == "https://example.com"
    assert update["credential_binding"]["headers"]["Authorization"] == "Bearer {{ api.password }}"


def test_update_api_monitor_tool_reparses_yaml(monkeypatch):
    update_one = AsyncMock()
    monkeypatch.setattr(mcp_route, "_get_owned_user_mcp_server", AsyncMock(return_value={"_id": "mcp_api_monitor", "user_id": "user_1", "source_type": "api_monitor"}))
    monkeypatch.setattr(mcp_route, "_load_api_monitor_tool_documents", AsyncMock(return_value=[{"_id": "tool_1", "mcp_server_id": "mcp_api_monitor", "user_id": "user_1"}]))
    monkeypatch.setattr(mcp_route, "get_repository", lambda name: SimpleNamespace(update_one=update_one))

    response = client.put(
        "/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-tools/tool_1",
        json={
            "yaml_definition": """
name: search_orders_v2
description: Search orders with a clearer name
method: GET
url: /api/orders
parameters:
  type: object
  properties:
    keyword:
      type: string
request:
  query:
    keyword: "{{ keyword }}"
""",
        },
    )

    assert response.status_code == 200
    update = update_one.await_args.args[1]["$set"]
    assert update["name"] == "search_orders_v2"
    assert update["validation_status"] == "valid"
```

- [ ] **Step 3: Run route tests and confirm failure**

Run:

```bash
RpaClaw/backend/.venv/bin/python -m pytest RpaClaw/backend/tests/test_mcp_route.py -q
```

Expected: FAIL because the dedicated endpoints and helper function names are not implemented yet.

- [ ] **Step 4: Implement route models and helper functions**

In `RpaClaw/backend/route/mcp.py`, add imports:

```python
from backend.rpa.api_monitor_mcp_contract import parse_api_monitor_tool_yaml
```

Add request models:

```python
class ApiMonitorMcpConfigUpdate(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    default_enabled: bool = True
    endpoint_config: Dict[str, Any] = Field(default_factory=dict)
    credential_binding: Dict[str, Any] = Field(default_factory=dict)


class ApiMonitorToolUpdate(BaseModel):
    yaml_definition: str


class ApiMonitorToolTestRequest(BaseModel):
    arguments: Dict[str, Any] = Field(default_factory=dict)
```

Add helpers:

```python
async def _load_api_monitor_tool_documents(server_id: str, user_id: str) -> list[dict[str, Any]]:
    repo = get_repository("api_monitor_mcp_tools")
    docs = await repo.find_many({"mcp_server_id": server_id, "user_id": user_id})
    return sorted(docs, key=lambda item: item.get("order", 0))


def _serialize_api_monitor_tool(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc.get("_id") or doc.get("id") or ""),
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "method": doc.get("method") or "",
        "url": doc.get("url") or "",
        "yaml_definition": doc.get("yaml_definition") or "",
        "input_schema": doc.get("input_schema") or {"type": "object", "properties": {}},
        "path_mapping": doc.get("path_mapping") or {},
        "query_mapping": doc.get("query_mapping") or {},
        "body_mapping": doc.get("body_mapping") or {},
        "header_mapping": doc.get("header_mapping") or {},
        "response_schema": doc.get("response_schema") or {},
        "validation_status": doc.get("validation_status") or "invalid",
        "validation_errors": doc.get("validation_errors") or [],
    }
```

Update API Monitor discovery path so it uses `_load_api_monitor_tool_documents(...)` and filters:

```python
valid_docs = [doc for doc in docs if doc.get("validation_status") == "valid"]
tools = [
    {
        "name": doc.get("name") or "",
        "description": doc.get("description") or "",
        "input_schema": doc.get("input_schema") or {"type": "object", "properties": {}},
    }
    for doc in valid_docs
]
```

- [ ] **Step 5: Implement detail, config update, and tool update endpoints**

Add routes in `RpaClaw/backend/route/mcp.py`:

```python
@router.get("/mcp/servers/{server_key}/api-monitor-detail", response_model=ApiResponse)
async def get_api_monitor_mcp_detail(server_key: str, current_user: User = Depends(get_current_user)):
    server = await _get_owned_user_mcp_server(server_key, str(current_user.id))
    if server.get("source_type") != "api_monitor":
        raise HTTPException(status_code=400, detail="MCP server is not an API Monitor MCP")
    tools = await _load_api_monitor_tool_documents(str(server.get("_id") or server.get("id")), str(current_user.id))
    return ApiResponse(
        data={
            "server": _serialize_user_mcp_server(server),
            "tools": [_serialize_api_monitor_tool(tool) for tool in tools],
        }
    )


@router.put("/mcp/servers/{server_key}/api-monitor-config", response_model=ApiResponse)
async def update_api_monitor_mcp_config(
    server_key: str,
    body: ApiMonitorMcpConfigUpdate,
    current_user: User = Depends(get_current_user),
):
    server = await _get_owned_user_mcp_server(server_key, str(current_user.id))
    if server.get("source_type") != "api_monitor":
        raise HTTPException(status_code=400, detail="MCP server is not an API Monitor MCP")
    update = {
        "name": body.name,
        "description": body.description,
        "enabled": body.enabled,
        "default_enabled": body.default_enabled,
        "endpoint_config": body.endpoint_config,
        "credential_binding": body.credential_binding,
        "updated_at": datetime.now(timezone.utc),
    }
    await get_repository("user_mcp_servers").update_one({"_id": server["_id"], "user_id": str(current_user.id)}, {"$set": update})
    return ApiResponse(data={**_serialize_user_mcp_server({**server, **update}), "updated": True})


@router.put("/mcp/servers/{server_key}/api-monitor-tools/{tool_id}", response_model=ApiResponse)
async def update_api_monitor_mcp_tool(
    server_key: str,
    tool_id: str,
    body: ApiMonitorToolUpdate,
    current_user: User = Depends(get_current_user),
):
    server = await _get_owned_user_mcp_server(server_key, str(current_user.id))
    if server.get("source_type") != "api_monitor":
        raise HTTPException(status_code=400, detail="MCP server is not an API Monitor MCP")
    tools = await _load_api_monitor_tool_documents(str(server.get("_id") or server.get("id")), str(current_user.id))
    if not any(str(tool.get("_id") or tool.get("id")) == tool_id for tool in tools):
        raise HTTPException(status_code=404, detail="API Monitor tool not found")
    contract = parse_api_monitor_tool_yaml(body.yaml_definition)
    update = {**contract.to_document(), "updated_at": datetime.now(timezone.utc)}
    await get_repository("api_monitor_mcp_tools").update_one({"_id": tool_id, "user_id": str(current_user.id)}, {"$set": update})
    return ApiResponse(data=_serialize_api_monitor_tool({"_id": tool_id, **update}))
```

- [ ] **Step 6: Add test endpoint shell**

Add route that delegates execution to `ApiMonitorMcpRuntime`:

```python
@router.post("/mcp/servers/{server_key}/api-monitor-tools/{tool_id}/test", response_model=ApiResponse)
async def test_api_monitor_mcp_tool(
    server_key: str,
    tool_id: str,
    body: ApiMonitorToolTestRequest,
    current_user: User = Depends(get_current_user),
):
    server_doc = await _get_owned_user_mcp_server(server_key, str(current_user.id))
    if server_doc.get("source_type") != "api_monitor":
        raise HTTPException(status_code=400, detail="MCP server is not an API Monitor MCP")
    tools = await _load_api_monitor_tool_documents(str(server_doc.get("_id") or server_doc.get("id")), str(current_user.id))
    tool = next((item for item in tools if str(item.get("_id") or item.get("id")) == tool_id), None)
    if tool is None:
        raise HTTPException(status_code=404, detail="API Monitor tool not found")
    if tool.get("validation_status") != "valid":
        return ApiResponse(data={"success": False, "error": "Tool YAML is invalid", "validation_errors": tool.get("validation_errors") or []})
    server = _coerce_user_mcp_server(server_doc)
    runtime = ApiMonitorMcpRuntime(server)
    result = await runtime.call_tool(str(tool.get("name")), body.arguments)
    return ApiResponse(data=result)
```

- [ ] **Step 7: Run route tests and confirm pass**

Run:

```bash
RpaClaw/backend/.venv/bin/python -m pytest RpaClaw/backend/tests/test_mcp_route.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit route changes**

Run:

```bash
git add RpaClaw/backend/route/mcp.py RpaClaw/backend/tests/test_mcp_route.py
git commit -m "feat: add api monitor mcp management endpoints"
```

Expected: commit succeeds.

---

## Task 4: Runtime Executes Parsed Contracts With Shared Auth

**Files:**

- Modify: `RpaClaw/backend/deepagent/mcp_runtime.py`
- Modify: `RpaClaw/backend/deepagent/mcp_registry.py`
- Modify: `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`
- Modify: `RpaClaw/backend/tests/deepagent/test_mcp_registry.py`

- [ ] **Step 1: Add failing runtime tests for mapped HTTP call**

Append to `RpaClaw/backend/tests/deepagent/test_mcp_runtime.py`:

```python
@pytest.mark.anyio
async def test_api_monitor_runtime_maps_arguments_headers_and_query(monkeypatch):
    repo = _FakeRepository([
        {
            "mcp_server_id": "mcp_api_monitor",
            "name": "search_orders",
            "description": "Search orders",
            "method": "GET",
            "url": "/api/orders",
            "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
            "query_mapping": {"keyword": "{{ keyword }}"},
            "path_mapping": {},
            "body_mapping": {},
            "header_mapping": {"X-Search": "{{ keyword }}"},
            "validation_status": "valid",
        }
    ])
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda name: repo)
    client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = mcp_runtime.ApiMonitorMcpRuntime(
        McpServerDefinition(
            id="mcp_api_monitor",
            name="Orders MCP",
            transport="api_monitor",
            scope="user",
            endpoint_config={
                "base_url": "https://example.com",
                "headers": {"X-App": "web"},
                "query": {"tenant": "main"},
                "timeout_ms": 45000,
            },
            credential_binding={
                "headers": {"Authorization": "Bearer resolved-token"},
            },
        )
    )

    result = await runtime.call_tool("search_orders", {"keyword": "alice"})

    assert result["success"] is True
    assert client.calls[0]["method"] == "GET"
    assert client.calls[0]["url"] == "https://example.com/api/orders"
    assert client.calls[0]["params"] == {"tenant": "main", "keyword": "alice"}
    assert client.calls[0]["headers"]["X-App"] == "web"
    assert client.calls[0]["headers"]["X-Search"] == "alice"
    assert client.calls[0]["headers"]["Authorization"] == "Bearer resolved-token"
    assert result["request_preview"]["headers"]["Authorization"] == "Bearer ****"
```

- [ ] **Step 2: Add failing runtime tests for invalid discovery and non-2xx**

Append:

```python
@pytest.mark.anyio
async def test_api_monitor_runtime_lists_only_valid_tools(monkeypatch):
    repo = _FakeRepository([
        {"mcp_server_id": "mcp_api_monitor", "name": "valid_tool", "description": "Valid", "input_schema": {"type": "object"}, "validation_status": "valid"},
        {"mcp_server_id": "mcp_api_monitor", "name": "invalid_tool", "description": "Invalid", "input_schema": {"type": "object"}, "validation_status": "invalid"},
    ])
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda name: repo)

    runtime = mcp_runtime.ApiMonitorMcpRuntime(McpServerDefinition(id="mcp_api_monitor", name="Orders MCP", transport="api_monitor", scope="user"))

    tools = await runtime.list_tools()

    assert [tool.name for tool in tools] == ["valid_tool"]


@pytest.mark.anyio
async def test_api_monitor_runtime_returns_structured_non_2xx(monkeypatch):
    repo = _FakeRepository([
        {
            "mcp_server_id": "mcp_api_monitor",
            "name": "search_orders",
            "description": "Search orders",
            "method": "GET",
            "url": "/api/orders",
            "input_schema": {"type": "object", "properties": {}},
            "query_mapping": {},
            "path_mapping": {},
            "body_mapping": {},
            "header_mapping": {},
            "validation_status": "valid",
        }
    ])
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda name: repo)
    client = _ApiMonitorAsyncClient(status_code=401, json_data={"error": "unauthorized"})
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = mcp_runtime.ApiMonitorMcpRuntime(
        McpServerDefinition(id="mcp_api_monitor", name="Orders MCP", transport="api_monitor", scope="user", endpoint_config={"base_url": "https://example.com"})
    )

    result = await runtime.call_tool("search_orders", {})

    assert result["success"] is False
    assert result["status_code"] == 401
    assert result["body"] == {"error": "unauthorized"}
```

- [ ] **Step 3: Run runtime tests and confirm failure**

Run:

```bash
RpaClaw/backend/.venv/bin/python -m pytest RpaClaw/backend/tests/deepagent/test_mcp_runtime.py -q
```

Expected: FAIL because runtime still uses old URL-pattern execution and does not merge the new mappings/config.

- [ ] **Step 4: Implement template rendering, request building, and sanitization helpers**

In `RpaClaw/backend/rpa/api_monitor_mcp_contract.py`, add:

```python
SENSITIVE_HEADER_NAMES = {"authorization", "cookie", "x-api-key", "api-key", "token"}


def render_template_value(value: Any, arguments: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        matches = TEMPLATE_RE.findall(value)
        if len(matches) == 1 and value.strip() == "{{ " + matches[0] + " }}":
            return arguments.get(matches[0])
        rendered = value
        for name in matches:
            rendered = re.sub(r"{{\s*" + re.escape(name) + r"\s*}}", str(arguments.get(name, "")), rendered)
        return rendered
    if isinstance(value, Mapping):
        return {str(key): render_template_value(nested, arguments) for key, nested in value.items()}
    if isinstance(value, list):
        return [render_template_value(nested, arguments) for nested in value]
    return value


def render_mapping(mapping: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): render_template_value(value, arguments) for key, value in mapping.items()}


def sanitize_headers(headers: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in headers.items():
        sanitized[str(key)] = "****" if str(key).lower() in SENSITIVE_HEADER_NAMES else value
    return sanitized
```

- [ ] **Step 5: Update ApiMonitorMcpRuntime list and call behavior**

In `RpaClaw/backend/deepagent/mcp_runtime.py`, update `ApiMonitorMcpRuntime`:

```python
async def list_tools(self) -> Sequence[McpToolDefinition | Mapping[str, Any]]:
    docs = await self._tools.find_many({"mcp_server_id": self._server.id})
    return [
        McpToolDefinition(
            name=str(doc.get("name") or ""),
            description=str(doc.get("description") or ""),
            input_schema=doc.get("input_schema") or {"type": "object", "properties": {}},
        )
        for doc in docs
        if doc.get("validation_status") == "valid" and doc.get("name")
    ]
```

Update `call_tool(...)` to:

```python
doc = await self._tools.find_one({"mcp_server_id": self._server.id, "name": tool_name, "validation_status": "valid"})
if not doc:
    raise ValueError(f"API Monitor MCP tool not found: {tool_name}")

endpoint_config = self._server.endpoint_config or {}
base_url = str(endpoint_config.get("base_url") or "")
url = _join_api_monitor_url(base_url, str(doc.get("url") or ""))
headers = {
    **(endpoint_config.get("headers") or {}),
    **(self._server.credential_binding.get("headers") if isinstance(self._server.credential_binding, dict) else {}),
    **render_mapping(doc.get("header_mapping") or {}, arguments),
}
params = {
    **(endpoint_config.get("query") or {}),
    **(self._server.credential_binding.get("query") if isinstance(self._server.credential_binding, dict) else {}),
    **render_mapping(doc.get("query_mapping") or {}, arguments),
}
json_body = render_mapping(doc.get("body_mapping") or {}, arguments) or None
timeout = (endpoint_config.get("timeout_ms") or 30000) / 1000

async with httpx.AsyncClient(timeout=timeout) as client:
    response = await client.request(str(doc.get("method") or "GET"), url, params=params, headers=headers, json=json_body)

try:
    body = response.json()
except ValueError:
    body = response.text

return {
    "success": 200 <= response.status_code < 300,
    "status_code": response.status_code,
    "headers": dict(response.headers),
    "body": body,
    "request_preview": {
        "method": str(doc.get("method") or "GET"),
        "url": url,
        "query": params,
        "headers": sanitize_headers(headers),
        "body": json_body,
    },
}
```

Use `render_mapping` and `sanitize_headers` from `api_monitor_mcp_contract.py`.

- [ ] **Step 6: Add default-enabled registry test**

Append to `RpaClaw/backend/tests/deepagent/test_mcp_registry.py`:

```python
@pytest.mark.anyio
async def test_default_enabled_api_monitor_mcp_is_effective_for_new_sessions(monkeypatch):
    monkeypatch.setattr(
        mcp_registry,
        "_load_user_mcp_servers",
        AsyncMock(return_value=[
            McpServerDefinition(
                id="mcp_api_monitor",
                name="Orders MCP",
                transport="api_monitor",
                scope="user",
                enabled=True,
                default_enabled=True,
                source_type="api_monitor",
            )
        ]),
    )
    monkeypatch.setattr(mcp_registry, "_load_session_bindings", AsyncMock(return_value={}))

    servers = await mcp_registry.build_effective_mcp_servers(user_id="user_1", session_id="session_1")

    assert [server.id for server in servers] == ["mcp_api_monitor"]
```

- [ ] **Step 7: Run runtime and registry tests**

Run:

```bash
RpaClaw/backend/.venv/bin/python -m pytest RpaClaw/backend/tests/deepagent/test_mcp_runtime.py RpaClaw/backend/tests/deepagent/test_mcp_registry.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit runtime changes**

Run:

```bash
git add RpaClaw/backend/rpa/api_monitor_mcp_contract.py RpaClaw/backend/deepagent/mcp_runtime.py RpaClaw/backend/deepagent/mcp_registry.py RpaClaw/backend/tests/deepagent/test_mcp_runtime.py RpaClaw/backend/tests/deepagent/test_mcp_registry.py
git commit -m "feat: execute api monitor mcp contracts"
```

Expected: commit succeeds.

---

## Task 5: Frontend API Types And Utilities

**Files:**

- Modify: `RpaClaw/frontend/src/api/mcp.ts`
- Create: `RpaClaw/frontend/src/utils/apiMonitorMcp.ts`
- Test: `RpaClaw/frontend/src/utils/apiMonitorMcp.test.ts`

- [ ] **Step 1: Add utility tests for YAML sync and sample arguments**

Create `RpaClaw/frontend/src/utils/apiMonitorMcp.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import {
  buildSampleArguments,
  formatValidationStatus,
  syncYamlTopLevelField,
} from './apiMonitorMcp';

describe('syncYamlTopLevelField', () => {
  it('updates existing name field', () => {
    const yaml = 'name: old_name\ndescription: Old\nmethod: GET\n';
    expect(syncYamlTopLevelField(yaml, 'name', 'search_orders')).toContain('name: search_orders');
  });

  it('inserts missing description field after name', () => {
    const yaml = 'name: search_orders\nmethod: GET\n';
    expect(syncYamlTopLevelField(yaml, 'description', 'Search orders')).toBe('name: search_orders\ndescription: Search orders\nmethod: GET\n');
  });
});

describe('buildSampleArguments', () => {
  it('builds readable examples from json schema properties', () => {
    expect(buildSampleArguments({
      type: 'object',
      properties: {
        keyword: { type: 'string' },
        page: { type: 'number' },
        active: { type: 'boolean' },
      },
    })).toEqual({ keyword: 'example', page: 1, active: true });
  });
});

describe('formatValidationStatus', () => {
  it('returns valid label for valid tools', () => {
    expect(formatValidationStatus('valid')).toBe('Valid');
  });

  it('returns invalid label for invalid tools', () => {
    expect(formatValidationStatus('invalid')).toBe('Invalid');
  });
});
```

- [ ] **Step 2: Run utility tests and confirm failure**

Run:

```bash
cd RpaClaw/frontend
npm test -- apiMonitorMcp.test.ts
```

Expected: FAIL because `apiMonitorMcp.ts` does not exist. If `vitest` is still missing in local `node_modules`, record the failure and use `npm run build` after implementation as the available verification.

- [ ] **Step 3: Add frontend API types and calls**

In `RpaClaw/frontend/src/api/mcp.ts`, add:

```typescript
export interface ApiMonitorMcpToolDetail {
  id: string;
  name: string;
  description: string;
  method: string;
  url: string;
  yaml_definition: string;
  input_schema: Record<string, unknown>;
  path_mapping: Record<string, unknown>;
  query_mapping: Record<string, unknown>;
  body_mapping: Record<string, unknown>;
  header_mapping: Record<string, unknown>;
  response_schema: Record<string, unknown>;
  validation_status: 'valid' | 'invalid';
  validation_errors: string[];
}

export interface ApiMonitorMcpDetail {
  server: McpServerDefinition;
  tools: ApiMonitorMcpToolDetail[];
}

export interface ApiMonitorMcpConfigPayload {
  name: string;
  description: string;
  enabled: boolean;
  default_enabled: boolean;
  endpoint_config: Record<string, unknown>;
  credential_binding: Partial<McpCredentialBinding>;
}

export async function getApiMonitorMcpDetail(serverKey: string): Promise<ApiMonitorMcpDetail> {
  const response = await apiClient.get<ApiResponse<ApiMonitorMcpDetail>>(`/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-detail`);
  return response.data.data;
}

export async function updateApiMonitorMcpConfig(serverKey: string, payload: ApiMonitorMcpConfigPayload): Promise<McpServerDefinition> {
  const response = await apiClient.put<ApiResponse<McpServerDefinition>>(`/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-config`, payload);
  return response.data.data;
}

export async function updateApiMonitorMcpTool(serverKey: string, toolId: string, payload: { yaml_definition: string }): Promise<ApiMonitorMcpToolDetail> {
  const response = await apiClient.put<ApiResponse<ApiMonitorMcpToolDetail>>(`/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-tools/${encodeURIComponent(toolId)}`, payload);
  return response.data.data;
}

export async function testApiMonitorMcpTool(serverKey: string, toolId: string, payload: { arguments: Record<string, unknown> }): Promise<Record<string, unknown>> {
  const response = await apiClient.post<ApiResponse<Record<string, unknown>>>(`/mcp/servers/${encodeServerKey(serverKey)}/api-monitor-tools/${encodeURIComponent(toolId)}/test`, payload);
  return response.data.data;
}
```

- [ ] **Step 4: Implement frontend utilities**

Create `RpaClaw/frontend/src/utils/apiMonitorMcp.ts`:

```typescript
type JsonSchema = {
  type?: unknown;
  properties?: Record<string, { type?: unknown }>;
};

export function syncYamlTopLevelField(yaml: string, field: 'name' | 'description', value: string): string {
  const escaped = value.replace(/\n/g, ' ').trim();
  const lines = yaml.split('\n');
  const fieldIndex = lines.findIndex((line) => line.startsWith(`${field}:`));
  if (fieldIndex >= 0) {
    lines[fieldIndex] = `${field}: ${escaped}`;
    return lines.join('\n');
  }
  const nameIndex = lines.findIndex((line) => line.startsWith('name:'));
  const insertAt = field === 'description' && nameIndex >= 0 ? nameIndex + 1 : 0;
  lines.splice(insertAt, 0, `${field}: ${escaped}`);
  return lines.join('\n');
}

export function buildSampleArguments(schema: JsonSchema): Record<string, unknown> {
  const properties = schema.properties || {};
  return Object.fromEntries(
    Object.entries(properties).map(([key, value]) => {
      if (value.type === 'number' || value.type === 'integer') return [key, 1];
      if (value.type === 'boolean') return [key, true];
      if (value.type === 'array') return [key, []];
      if (value.type === 'object') return [key, {}];
      return [key, 'example'];
    }),
  );
}

export function formatValidationStatus(status: string): string {
  return status === 'valid' ? 'Valid' : 'Invalid';
}

export function prettyJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}
```

- [ ] **Step 5: Run utility tests or build**

Run:

```bash
cd RpaClaw/frontend
npm test -- apiMonitorMcp.test.ts
```

Expected if dependencies are installed: PASS.

If `vitest` is unavailable, run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: build succeeds with existing non-blocking warnings only.

- [ ] **Step 6: Commit frontend API utility changes**

Run:

```bash
git add RpaClaw/frontend/src/api/mcp.ts RpaClaw/frontend/src/utils/apiMonitorMcp.ts RpaClaw/frontend/src/utils/apiMonitorMcp.test.ts
git commit -m "feat: add api monitor mcp frontend api"
```

Expected: commit succeeds.

---

## Task 6: Dedicated Tool Library Detail View For API Monitor MCP

**Files:**

- Create: `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`
- Modify: `RpaClaw/frontend/src/pages/ToolsPage.vue`
- Modify: `RpaClaw/frontend/src/locales/en.ts`
- Modify: `RpaClaw/frontend/src/locales/zh.ts`

- [ ] **Step 1: Add component shell with server config and tool list**

Create `RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue`:

```vue
<template>
  <Dialog :open="open" @update:open="$emit('update:open', $event)">
    <DialogContent class="max-h-[92vh] max-w-6xl overflow-y-auto">
      <DialogHeader>
        <DialogTitle>{{ t('API Monitor MCP detail') }}</DialogTitle>
        <DialogDescription>{{ t('API Monitor MCP detail description') }}</DialogDescription>
      </DialogHeader>

      <div v-if="loading" class="rounded-2xl border border-slate-200 p-6 text-sm text-[var(--text-tertiary)] dark:border-white/10">
        {{ t('Loading') }}
      </div>

      <div v-else-if="detail" class="space-y-6">
        <section class="rounded-2xl border border-slate-200 p-4 dark:border-white/10">
          <div class="grid gap-4 md:grid-cols-2">
            <label class="space-y-1">
              <span class="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{{ t('Name') }}</span>
              <input v-model="configForm.name" class="form-input" />
            </label>
            <label class="space-y-1">
              <span class="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{{ t('Base URL') }}</span>
              <input v-model="configForm.baseUrl" class="form-input" placeholder="https://api.example.com" />
            </label>
          </div>
          <label class="mt-4 block space-y-1">
            <span class="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{{ t('Description') }}</span>
            <textarea v-model="configForm.description" class="form-textarea" rows="2" />
          </label>
          <div class="mt-4 flex flex-wrap gap-4 text-sm">
            <label class="inline-flex items-center gap-2">
              <input v-model="configForm.enabled" type="checkbox" />
              <span>{{ t('Enabled') }}</span>
            </label>
            <label class="inline-flex items-center gap-2">
              <input v-model="configForm.defaultEnabled" type="checkbox" />
              <span>{{ t('Default enabled for new sessions') }}</span>
            </label>
          </div>
        </section>

        <section class="rounded-2xl border border-slate-200 p-4 dark:border-white/10">
          <h3 class="text-sm font-semibold">{{ t('Shared authentication') }}</h3>
          <p class="mt-1 text-xs text-[var(--text-tertiary)]">{{ t('Shared authentication description') }}</p>
          <div class="mt-4 grid gap-4 md:grid-cols-2">
            <label class="space-y-1">
              <span class="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{{ t('Static Headers JSON') }}</span>
              <textarea v-model="configForm.headersJson" class="form-textarea font-mono text-xs" rows="6" />
            </label>
            <label class="space-y-1">
              <span class="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{{ t('Credential Headers JSON') }}</span>
              <textarea v-model="configForm.credentialHeadersJson" class="form-textarea font-mono text-xs" rows="6" />
            </label>
          </div>
          <div class="mt-4 grid gap-4 md:grid-cols-2">
            <label class="space-y-1">
              <span class="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{{ t('Query Params JSON') }}</span>
              <textarea v-model="configForm.queryJson" class="form-textarea font-mono text-xs" rows="4" />
            </label>
            <label class="space-y-1">
              <span class="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{{ t('Timeout milliseconds') }}</span>
              <input v-model.number="configForm.timeoutMs" class="form-input" type="number" min="1000" />
            </label>
          </div>
          <button class="primary-button mt-4" :disabled="savingConfig" @click="saveConfig">{{ savingConfig ? t('Saving') : t('Save shared config') }}</button>
        </section>

        <section class="space-y-3">
          <article v-for="tool in tools" :key="tool.id" class="rounded-2xl border border-slate-200 p-4 dark:border-white/10">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div class="flex items-center gap-2">
                  <span class="rounded-full bg-cyan-100 px-2 py-0.5 text-xs font-semibold text-cyan-800 dark:bg-cyan-400/15 dark:text-cyan-200">{{ tool.method }}</span>
                  <h3 class="font-semibold">{{ tool.name }}</h3>
                  <span :class="tool.validation_status === 'valid' ? 'text-emerald-600' : 'text-rose-600'" class="text-xs">{{ formatValidationStatus(tool.validation_status) }}</span>
                </div>
                <p class="mt-1 text-sm text-[var(--text-tertiary)]">{{ tool.description }}</p>
                <p class="mt-1 font-mono text-xs text-[var(--text-tertiary)]">{{ tool.url }}</p>
              </div>
              <button class="action-muted" @click="toggleTool(tool.id)">{{ expandedToolId === tool.id ? t('Collapse') : t('Details') }}</button>
            </div>

            <div v-if="expandedToolId === tool.id" class="mt-4 grid gap-4 lg:grid-cols-2">
              <div class="space-y-3">
                <label class="space-y-1">
                  <span class="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{{ t('Tool name') }}</span>
                  <input :value="tool.name" class="form-input" @input="syncToolField(tool.id, 'name', ($event.target as HTMLInputElement).value)" />
                </label>
                <label class="space-y-1">
                  <span class="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{{ t('Tool description') }}</span>
                  <textarea :value="tool.description" class="form-textarea" rows="2" @input="syncToolField(tool.id, 'description', ($event.target as HTMLTextAreaElement).value)" />
                </label>
                <label class="space-y-1">
                  <span class="text-xs font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">{{ t('YAML definition') }}</span>
                  <textarea v-model="toolDrafts[tool.id]" class="form-textarea font-mono text-xs" rows="16" />
                </label>
                <div v-if="tool.validation_errors.length" class="rounded-xl bg-rose-50 p-3 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-200">
                  <p v-for="error in tool.validation_errors" :key="error">{{ error }}</p>
                </div>
                <div class="flex gap-2">
                  <button class="primary-button" @click="saveTool(tool)">{{ t('Save tool') }}</button>
                  <button class="action-muted" @click="testTool(tool)">{{ t('Test tool') }}</button>
                </div>
              </div>
              <div class="space-y-3">
                <pre class="preview-block">{{ prettyJson(tool.input_schema) }}</pre>
                <pre class="preview-block">{{ prettyJson({ path: tool.path_mapping, query: tool.query_mapping, headers: tool.header_mapping, body: tool.body_mapping }) }}</pre>
                <pre class="preview-block">{{ prettyJson(buildSampleArguments(tool.input_schema)) }}</pre>
                <pre v-if="testResults[tool.id]" class="preview-block">{{ prettyJson(testResults[tool.id]) }}</pre>
              </div>
            </div>
          </article>
        </section>
      </div>
    </DialogContent>
  </Dialog>
</template>
```

- [ ] **Step 2: Add component script**

Add script in the same component:

```vue
<script setup lang="ts">
import { reactive, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  getApiMonitorMcpDetail,
  testApiMonitorMcpTool,
  updateApiMonitorMcpConfig,
  updateApiMonitorMcpTool,
  type ApiMonitorMcpDetail,
  type ApiMonitorMcpToolDetail,
  type McpServerDefinition,
} from '@/api/mcp';
import { buildSampleArguments, formatValidationStatus, prettyJson, syncYamlTopLevelField } from '@/utils/apiMonitorMcp';
import { showErrorToast, showSuccessToast } from '@/utils/toast';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';

const props = defineProps<{
  open: boolean;
  server: McpServerDefinition | null;
}>();

const emit = defineEmits<{
  'update:open': [value: boolean];
  updated: [];
}>();

const { t } = useI18n();
const loading = ref(false);
const savingConfig = ref(false);
const detail = ref<ApiMonitorMcpDetail | null>(null);
const tools = ref<ApiMonitorMcpToolDetail[]>([]);
const expandedToolId = ref('');
const toolDrafts = reactive<Record<string, string>>({});
const testResults = reactive<Record<string, unknown>>({});
const configForm = reactive({
  name: '',
  description: '',
  enabled: true,
  defaultEnabled: true,
  baseUrl: '',
  headersJson: '{}',
  credentialHeadersJson: '{}',
  queryJson: '{}',
  timeoutMs: 30000,
});

watch(() => [props.open, props.server?.server_key], async () => {
  if (!props.open || !props.server) return;
  await loadDetail();
});

async function loadDetail() {
  if (!props.server) return;
  loading.value = true;
  try {
    detail.value = await getApiMonitorMcpDetail(props.server.server_key);
    tools.value = detail.value.tools;
    for (const tool of tools.value) toolDrafts[tool.id] = tool.yaml_definition;
    const server = detail.value.server;
    const endpoint = server.endpoint_config || {};
    configForm.name = server.name;
    configForm.description = server.description || '';
    configForm.enabled = server.enabled;
    configForm.defaultEnabled = server.default_enabled;
    configForm.baseUrl = String(endpoint.base_url || '');
    configForm.headersJson = prettyJson(endpoint.headers || {});
    configForm.queryJson = prettyJson(endpoint.query || {});
    configForm.timeoutMs = Number(endpoint.timeout_ms || 30000);
    configForm.credentialHeadersJson = prettyJson(server.credential_binding?.headers || {});
  } catch (error: any) {
    showErrorToast(error?.message || t('Failed to load API Monitor MCP detail'));
  } finally {
    loading.value = false;
  }
}

function parseJsonInput(value: string, label: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label} must be a JSON object`);
    return parsed as Record<string, unknown>;
  } catch (error: any) {
    throw new Error(error?.message || `${label} must be valid JSON`);
  }
}

async function saveConfig() {
  if (!props.server) return;
  savingConfig.value = true;
  try {
    const headers = parseJsonInput(configForm.headersJson, 'Static Headers JSON');
    const credentialHeaders = parseJsonInput(configForm.credentialHeadersJson, 'Credential Headers JSON');
    const query = parseJsonInput(configForm.queryJson, 'Query Params JSON');
    await updateApiMonitorMcpConfig(props.server.server_key, {
      name: configForm.name,
      description: configForm.description,
      enabled: configForm.enabled,
      default_enabled: configForm.defaultEnabled,
      endpoint_config: {
        base_url: configForm.baseUrl,
        headers,
        query,
        timeout_ms: configForm.timeoutMs,
      },
      credential_binding: {
        headers: credentialHeaders,
        query: {},
        env: {},
      },
    });
    showSuccessToast(t('Saved shared config'));
    emit('updated');
  } catch (error: any) {
    showErrorToast(error?.message || t('Failed to save shared config'));
  } finally {
    savingConfig.value = false;
  }
}

function toggleTool(toolId: string) {
  expandedToolId.value = expandedToolId.value === toolId ? '' : toolId;
}

function syncToolField(toolId: string, field: 'name' | 'description', value: string) {
  toolDrafts[toolId] = syncYamlTopLevelField(toolDrafts[toolId] || '', field, value);
}

async function saveTool(tool: ApiMonitorMcpToolDetail) {
  if (!props.server) return;
  try {
    const updated = await updateApiMonitorMcpTool(props.server.server_key, tool.id, { yaml_definition: toolDrafts[tool.id] || '' });
    tools.value = tools.value.map((item) => item.id === tool.id ? updated : item);
    toolDrafts[updated.id] = updated.yaml_definition;
    showSuccessToast(t('Saved tool'));
    emit('updated');
  } catch (error: any) {
    showErrorToast(error?.message || t('Failed to save tool'));
  }
}

async function testTool(tool: ApiMonitorMcpToolDetail) {
  if (!props.server) return;
  try {
    testResults[tool.id] = await testApiMonitorMcpTool(props.server.server_key, tool.id, {
      arguments: buildSampleArguments(tool.input_schema),
    });
  } catch (error: any) {
    showErrorToast(error?.message || t('Failed to test tool'));
  }
}
</script>
```

- [ ] **Step 3: Add component styles**

Append to the component:

```vue
<style scoped>
.form-input,
.form-textarea {
  width: 100%;
  border-radius: 0.875rem;
  border: 1px solid rgb(203 213 225);
  background: white;
  padding: 0.625rem 0.75rem;
  color: var(--text-primary);
}

.form-textarea {
  resize: vertical;
}

.primary-button,
.action-muted {
  border-radius: 999px;
  padding: 0.5rem 0.9rem;
  font-size: 0.875rem;
  font-weight: 700;
}

.primary-button {
  background: #0f766e;
  color: white;
}

.action-muted {
  border: 1px solid rgb(203 213 225);
  color: var(--text-secondary);
}

.preview-block {
  max-height: 18rem;
  overflow: auto;
  border-radius: 1rem;
  background: rgb(15 23 42 / 0.92);
  padding: 1rem;
  color: rgb(226 232 240);
  font-size: 0.75rem;
}
</style>
```

- [ ] **Step 4: Wire ToolsPage to use dedicated API Monitor dialog**

In `RpaClaw/frontend/src/pages/ToolsPage.vue`, import the component:

```typescript
import ApiMonitorMcpDetailDialog from '@/components/tools/ApiMonitorMcpDetailDialog.vue';
```

Add state:

```typescript
const apiMonitorDetailOpen = ref(false);
const selectedApiMonitorServer = ref<McpServerDefinition | null>(null);
```

Update the existing API Monitor MCP view action so it does not call normal `discoverMcpTools(...)`:

```typescript
const openServerToolsDialog = async (server: McpServerDefinition) => {
  if (server.transport === 'api_monitor' || server.source_type === 'api_monitor') {
    selectedApiMonitorServer.value = server;
    apiMonitorDetailOpen.value = true;
    return;
  }
  // existing normal MCP discovery behavior remains here
};
```

Add dialog near the bottom of the template:

```vue
<ApiMonitorMcpDetailDialog
  v-model:open="apiMonitorDetailOpen"
  :server="selectedApiMonitorServer"
  @updated="loadData"
/>
```

- [ ] **Step 5: Add locale strings**

In `RpaClaw/frontend/src/locales/en.ts`, add:

```typescript
'API Monitor MCP detail': 'API Monitor MCP detail',
'API Monitor MCP detail description': 'Inspect, rename, authenticate, and test API tools generated from API Monitor.',
'Base URL': 'Base URL',
'Default enabled for new sessions': 'Default enabled for new sessions',
'Shared authentication': 'Shared authentication',
'Shared authentication description': 'These headers, query params, and credential templates apply to every tool in this API Monitor MCP.',
'Static Headers JSON': 'Static Headers JSON',
'Credential Headers JSON': 'Credential Headers JSON',
'Query Params JSON': 'Query Params JSON',
'Timeout milliseconds': 'Timeout milliseconds',
'Save shared config': 'Save shared config',
'Saved shared config': 'Saved shared config',
'Failed to load API Monitor MCP detail': 'Failed to load API Monitor MCP detail',
'Failed to save shared config': 'Failed to save shared config',
'Tool name': 'Tool name',
'Tool description': 'Tool description',
'YAML definition': 'YAML definition',
'Save tool': 'Save tool',
'Saved tool': 'Saved tool',
'Failed to save tool': 'Failed to save tool',
'Test tool': 'Test tool',
'Failed to test tool': 'Failed to test tool',
'Collapse': 'Collapse',
'Details': 'Details',
```

In `RpaClaw/frontend/src/locales/zh.ts`, add:

```typescript
'API Monitor MCP detail': 'API Monitor MCP 详情',
'API Monitor MCP detail description': '查看、重命名、认证配置并测试由 API Monitor 生成的 API 工具。',
'Base URL': 'Base URL',
'Default enabled for new sessions': '新会话默认启用',
'Shared authentication': '共享认证',
'Shared authentication description': '这些请求头、查询参数和凭据模板会应用到该 API Monitor MCP 下的所有工具。',
'Static Headers JSON': '静态请求头 JSON',
'Credential Headers JSON': '凭据请求头 JSON',
'Query Params JSON': '查询参数 JSON',
'Timeout milliseconds': '超时时间（毫秒）',
'Save shared config': '保存共享配置',
'Saved shared config': '已保存共享配置',
'Failed to load API Monitor MCP detail': '加载 API Monitor MCP 详情失败',
'Failed to save shared config': '保存共享配置失败',
'Tool name': '工具名称',
'Tool description': '工具描述',
'YAML definition': 'YAML 定义',
'Save tool': '保存工具',
'Saved tool': '已保存工具',
'Failed to save tool': '保存工具失败',
'Test tool': '测试工具',
'Failed to test tool': '测试工具失败',
'Collapse': '收起',
'Details': '详情',
```

- [ ] **Step 6: Run frontend build**

Run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: build succeeds with existing warnings only.

- [ ] **Step 7: Commit detail view changes**

Run:

```bash
git add RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue RpaClaw/frontend/src/pages/ToolsPage.vue RpaClaw/frontend/src/locales/en.ts RpaClaw/frontend/src/locales/zh.ts
git commit -m "feat: add api monitor mcp detail view"
```

Expected: commit succeeds.

---

## Task 7: Chat Tool-Call Explainability

**Files:**

- Modify: `RpaClaw/frontend/src/components/toolViews/McpToolView.vue`
- Modify: `RpaClaw/frontend/src/utils/mcpUi.ts`
- Modify: `RpaClaw/frontend/src/utils/mcpUi.test.ts`
- Modify: `RpaClaw/frontend/src/locales/en.ts`
- Modify: `RpaClaw/frontend/src/locales/zh.ts`

- [ ] **Step 1: Add UI utility tests for API Monitor runtime preview**

Append to `RpaClaw/frontend/src/utils/mcpUi.test.ts`:

```typescript
describe('formatApiMonitorRequestPreview', () => {
  it('formats sanitized request previews without exposing secrets', () => {
    expect(formatApiMonitorRequestPreview({
      method: 'GET',
      url: 'https://example.com/api/orders',
      query: { keyword: 'alice' },
      headers: { Authorization: 'Bearer ****', 'X-App': 'web' },
    })).toContain('GET https://example.com/api/orders');
  });
});
```

- [ ] **Step 2: Implement preview formatter**

In `RpaClaw/frontend/src/utils/mcpUi.ts`, add:

```typescript
export type ApiMonitorRequestPreview = {
  method?: string;
  url?: string;
  query?: Record<string, unknown>;
  headers?: Record<string, unknown>;
  body?: unknown;
};

export function formatApiMonitorRequestPreview(preview?: ApiMonitorRequestPreview | null): string {
  if (!preview) return '';
  const lines = [`${preview.method || 'GET'} ${preview.url || ''}`.trim()];
  if (preview.query && Object.keys(preview.query).length) {
    lines.push(`query: ${JSON.stringify(preview.query)}`);
  }
  if (preview.headers && Object.keys(preview.headers).length) {
    lines.push(`headers: ${JSON.stringify(preview.headers)}`);
  }
  if (preview.body) {
    lines.push(`body: ${JSON.stringify(preview.body)}`);
  }
  return lines.join('\n');
}
```

- [ ] **Step 3: Display request preview in MCP tool view**

In `RpaClaw/frontend/src/components/toolViews/McpToolView.vue`, import:

```typescript
import { formatApiMonitorRequestPreview } from '@/utils/mcpUi';
```

Add computed:

```typescript
const requestPreview = computed(() => {
  const result = props.tool?.result;
  if (!result || typeof result !== 'object') return '';
  return formatApiMonitorRequestPreview((result as any).request_preview);
});
```

Add template block below the normal MCP result header:

```vue
<div v-if="requestPreview" class="mt-3 rounded-xl border border-cyan-200 bg-cyan-50 p-3 dark:border-cyan-400/20 dark:bg-cyan-400/10">
  <p class="text-xs font-semibold uppercase tracking-wide text-cyan-800 dark:text-cyan-200">{{ t('API request preview') }}</p>
  <pre class="mt-2 whitespace-pre-wrap text-xs text-cyan-950 dark:text-cyan-100">{{ requestPreview }}</pre>
</div>
```

- [ ] **Step 4: Add locale strings**

In `RpaClaw/frontend/src/locales/en.ts`, add:

```typescript
'API request preview': 'API request preview',
```

In `RpaClaw/frontend/src/locales/zh.ts`, add:

```typescript
'API request preview': 'API 请求预览',
```

- [ ] **Step 5: Run utility tests or build**

Run:

```bash
cd RpaClaw/frontend
npm test -- mcpUi.test.ts
```

Expected if dependencies are installed: PASS.

If `vitest` is unavailable, run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: build succeeds with existing warnings only.

- [ ] **Step 6: Commit explainability changes**

Run:

```bash
git add RpaClaw/frontend/src/components/toolViews/McpToolView.vue RpaClaw/frontend/src/utils/mcpUi.ts RpaClaw/frontend/src/utils/mcpUi.test.ts RpaClaw/frontend/src/locales/en.ts RpaClaw/frontend/src/locales/zh.ts
git commit -m "feat: show api monitor mcp request previews"
```

Expected: commit succeeds.

---

## Task 8: Integration Verification And Documentation Cleanup

**Files:**

- Review: `docs/superpowers/specs/2026-04-23-api-monitor-mcp-usability-auth-design.md`
- Review: `docs/superpowers/plans/2026-04-23-api-monitor-mcp-usability-auth.md`

- [ ] **Step 1: Run focused backend verification**

Run:

```bash
RpaClaw/backend/.venv/bin/python -m pytest \
  RpaClaw/backend/tests/test_api_monitor_mcp_contract.py \
  RpaClaw/backend/tests/test_api_monitor_publish_mcp.py \
  RpaClaw/backend/tests/test_mcp_route.py \
  RpaClaw/backend/tests/deepagent/test_mcp_runtime.py \
  RpaClaw/backend/tests/deepagent/test_mcp_registry.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run focused frontend verification**

Run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: PASS with existing warnings only.

If `vitest` is available, also run:

```bash
cd RpaClaw/frontend
npm test -- apiMonitorMcp.test.ts mcpUi.test.ts
```

Expected: PASS.

- [ ] **Step 3: Manually verify the user flow in local dev**

Run backend and frontend as usual:

```bash
cd RpaClaw/backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

```bash
cd RpaClaw/frontend
npm run dev
```

Verify:

1. Open API Monitor and generate at least two API tools.
2. Click `Save as MCP Tool`.
3. Confirm the saved MCP appears in Tool Library as `API Monitor MCP`.
4. Open its detail view and confirm raw YAML is visible.
5. Rename a tool and save; confirm the YAML `name:` updates and discovery returns the renamed tool.
6. Configure `Base URL` and a shared header.
7. Test one tool and confirm the request preview shows method, URL, query/body, and masked sensitive headers.
8. Start a new chat and confirm the API Monitor MCP is included when default-enabled.
9. Disable the MCP in Tool Library and confirm new chats no longer include it by default.

- [ ] **Step 4: Check for secret leaks**

Run:

```bash
rg -n "resolved-token|Bearer [A-Za-z0-9._-]{8,}|api\\.password|session_cookie\\.password" RpaClaw/backend RpaClaw/frontend/src
```

Expected: no hardcoded real secrets. Template strings such as `{{ api.password }}` in tests or locale examples are acceptable.

- [ ] **Step 5: Final git status review**

Run:

```bash
git status --short
```

Expected: only intended changes remain. If unrelated pre-existing files are dirty, leave them untouched and mention them in the handoff.

- [ ] **Step 6: Handle final cleanup explicitly**

Run:

```bash
git status --short
```

Expected: no additional feature changes are needed. If verification fixes are needed, return to the task that owns the failing area, make a targeted fix there, rerun that task's tests, and commit using that task's `git add` command plus the message `fix: polish api monitor mcp usability`.

---

## Success Criteria

- API Monitor MCP detail view shows raw YAML, parsed schema, request mapping preview, validation errors, and test results.
- Users can edit tool `name` and `description`, and those edits sync into YAML before saving.
- Backend parses YAML on publish and edit, persists parsed execution contracts, and excludes invalid tools from Agent discovery.
- API Monitor MCPs created by users default to `enabled=true` and `default_enabled=true`.
- Shared MCP-level auth config applies to every API Monitor tool.
- Runtime executes HTTP calls from parsed contracts and returns structured results with sanitized request previews.
- Normal non-API-Monitor MCP discovery/edit flows remain unchanged.
- Focused backend tests pass.
- Frontend build passes.
