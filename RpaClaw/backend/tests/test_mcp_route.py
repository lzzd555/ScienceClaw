import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.mcp.models import McpServerDefinition
from backend.route import mcp as mcp_route


class _User:
    id = "user-1"


class _BindingRepo:
    def __init__(self):
        self.calls = []

    async def update_one(self, filter_doc, update_doc, upsert=False):
        self.calls.append(
            {
                "filter": filter_doc,
                "update": update_doc,
                "upsert": upsert,
            }
        )
        return 1


class _MemoryRepo:
    def __init__(self, docs=None):
        self.docs = {str(doc["_id"]): dict(doc) for doc in (docs or [])}
        self.calls = []

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
        return docs

    async def update_one(self, filter_doc, update_doc, upsert=False):
        self.calls.append(
            {
                "filter": filter_doc,
                "update": update_doc,
                "upsert": upsert,
            }
        )
        for doc_id, doc in self.docs.items():
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                updated = dict(doc)
                updated.update(update_doc.get("$set", {}))
                self.docs[doc_id] = updated
                return 1
        return 0

    async def delete_one(self, filter_doc):
        for doc_id, doc in list(self.docs.items()):
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                del self.docs[doc_id]
                return 1
        return 0


def _api_monitor_server_doc(**overrides):
    doc = {
        "_id": "mcp_api_monitor",
        "user_id": "user-1",
        "name": "Example MCP",
        "description": "Captured APIs",
        "transport": "api_monitor",
        "source_type": "api_monitor",
        "enabled": True,
        "default_enabled": False,
        "endpoint_config": {},
        "credential_binding": {},
    }
    doc.update(overrides)
    return doc


def _api_monitor_tool_doc(**overrides):
    doc = {
        "_id": "tool_1",
        "mcp_server_id": "mcp_api_monitor",
        "user_id": "user-1",
        "name": "search_orders",
        "description": "Search orders by keyword",
        "method": "GET",
        "url": "/api/orders",
        "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
        "path_mapping": {"tenant_id": "{{ tenant_id }}"},
        "query_mapping": {"keyword": "{{ keyword }}"},
        "body_mapping": {"page_size": 20},
        "header_mapping": {"X-Tenant-ID": "{{ tenant_id }}"},
        "response_schema": {"type": "object"},
        "yaml_definition": "name: search_orders\n",
        "validation_status": "valid",
        "validation_errors": [],
        "order": 0,
    }
    doc.update(overrides)
    return doc


def _build_app():
    app = FastAPI()
    app.include_router(mcp_route.router, prefix="/api/v1")
    app.dependency_overrides[mcp_route.require_user] = lambda: _User()
    return app


def test_list_mcp_servers_returns_normalized_system_and_user_entries(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="pubmed",
                name="PubMed",
                description="System search",
                transport="streamable_http",
                default_enabled=True,
                url="https://example.test/mcp",
                headers={"Authorization": "Bearer top-secret"},
                env={"API_KEY": "system-secret"},
                credential_ref="vault://system-secret",
            )
        ],
    )

    async def fake_user_servers(user_id: str):
        assert user_id == "user-1"
        return [
            {
                "_id": "mcp_user_1",
                "user_id": "user-1",
                "name": "Private MCP",
                "slug": "private-mcp",
                "description": "Private search",
                "transport": "sse",
                "enabled": True,
                "default_enabled": False,
                "endpoint_config": {"url": "https://user.example.test/sse"},
                "credential_binding": {"credential_id": "", "headers": {}, "env": {}, "query": {}},
                "tool_policy": {"allowed_tools": ["search"], "blocked_tools": []},
            }
        ]

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)

    response = client.get("/api/v1/mcp/servers")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["server_key"] == "system:pubmed"
    assert data[0]["readonly"] is True
    assert data[1]["server_key"] == "user:mcp_user_1"
    assert data[1]["readonly"] is False
    assert set(data[0].keys()) == set(data[1].keys())
    assert "_id" not in data[1]
    assert "user_id" not in data[1]
    assert data[0]["endpoint_config"]["headers"] == {"Authorization": "Bearer top-secret"}
    assert "env" not in data[0]["endpoint_config"]
    assert data[0]["credential_binding"] == {}


def test_list_mcp_servers_includes_api_monitor_source(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(mcp_route, "load_system_mcp_servers", lambda: [])

    async def fake_user_servers(user_id: str):
        assert user_id == "user-1"
        return [
            {
                "_id": "mcp_api_monitor",
                "user_id": "user-1",
                "name": "Example MCP",
                "description": "Captured APIs",
                "transport": "api_monitor",
                "enabled": True,
                "default_enabled": False,
                "source_type": "api_monitor",
            }
        ]

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)

    response = client.get("/api/v1/mcp/servers")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[0]["server_key"] == "user:mcp_api_monitor"
    assert data[0]["transport"] == "api_monitor"
    assert data[0]["source_type"] == "api_monitor"
    assert data[0]["endpoint_config"] == {}


def test_discover_mcp_tools_reads_internal_api_monitor_tools(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    async def fake_resolve(server_key: str, user_id: str):
        assert server_key == "user:mcp_api_monitor"
        assert user_id == "user-1"
        return {
            "id": "mcp_api_monitor",
            "server_key": "user:mcp_api_monitor",
            "scope": "user",
            "name": "Example MCP",
            "description": "Captured APIs",
            "transport": "api_monitor",
            "source_type": "api_monitor",
        }

    async def fake_load_tools(server_id: str, user_id: str):
        assert server_id == "mcp_api_monitor"
        assert user_id == "user-1"
        return [
            {
                "name": "list_users",
                "description": "List users",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    monkeypatch.setattr(mcp_route, "_resolve_server_by_key", fake_resolve)
    monkeypatch.setattr(mcp_route, "_load_api_monitor_tools", fake_load_tools)

    response = client.post("/api/v1/mcp/servers/user:mcp_api_monitor/discover-tools")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["tool_count"] == 1
    assert data["tools"][0]["name"] == "list_users"


def test_load_api_monitor_tools_filters_invalid_and_prefers_parsed_schema(monkeypatch):
    tool_repo = _MemoryRepo(
        [
            {
                "_id": "tool_valid",
                "mcp_server_id": "mcp_api_monitor",
                "user_id": "user-1",
                "name": "search_orders",
                "description": "Search orders",
                "validation_status": "valid",
                "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
                "request_body_schema": {"type": "object", "properties": {"legacy": {"type": "string"}}},
            },
            {
                "_id": "tool_invalid",
                "mcp_server_id": "mcp_api_monitor",
                "user_id": "user-1",
                "name": "broken_tool",
                "validation_status": "invalid",
                "input_schema": {"type": "object", "properties": {"broken": {"type": "string"}}},
            },
            {
                "_id": "tool_legacy",
                "mcp_server_id": "mcp_api_monitor",
                "user_id": "user-1",
                "name": "legacy_tool",
                "request_body_schema": {"type": "object", "properties": {"page": {"type": "integer"}}},
            },
        ]
    )
    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: tool_repo)

    tools = asyncio.run(mcp_route._load_api_monitor_tools("mcp_api_monitor", "user-1"))

    assert [tool["name"] for tool in tools] == ["search_orders", "legacy_tool"]
    assert tools[0]["input_schema"]["properties"] == {"keyword": {"type": "string"}}
    assert tools[1]["input_schema"]["properties"] == {"page": {"type": "integer"}}


def test_api_monitor_mcp_detail_returns_yaml_and_contract(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_api_monitor_server_doc()])
    tool_repo = _MemoryRepo(
        [
            _api_monitor_tool_doc(_id="tool_2", name="second_tool", order=2),
            _api_monitor_tool_doc(_id="tool_1", name="search_orders", order=1),
        ]
    )

    def fake_get_repository(collection_name):
        return server_repo if collection_name == "user_mcp_servers" else tool_repo

    monkeypatch.setattr(mcp_route, "get_repository", fake_get_repository)

    response = client.get("/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-detail")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["server"]["server_key"] == "user:mcp_api_monitor"
    assert data["server"]["transport"] == "api_monitor"
    assert [tool["id"] for tool in data["tools"]] == ["tool_1", "tool_2"]
    tool = data["tools"][0]
    assert tool["yaml_definition"] == "name: search_orders\n"
    assert tool["method"] == "GET"
    assert tool["url"] == "/api/orders"
    assert tool["input_schema"]["properties"]["keyword"] == {"type": "string"}
    assert tool["path_mapping"] == {"tenant_id": "{{ tenant_id }}"}
    assert tool["query_mapping"] == {"keyword": "{{ keyword }}"}
    assert tool["body_mapping"] == {"page_size": 20}
    assert tool["header_mapping"] == {"X-Tenant-ID": "{{ tenant_id }}"}
    assert tool["response_schema"] == {"type": "object"}
    assert tool["validation_status"] == "valid"
    assert tool["validation_errors"] == []


def test_update_api_monitor_mcp_config_saves_shared_auth(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_api_monitor_server_doc()])

    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: server_repo)

    response = client.put(
        "/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-config",
        json={
            "name": "Orders MCP",
            "description": "Updated",
            "enabled": False,
            "default_enabled": True,
            "endpoint_config": {"base_url": "https://api.example.test", "timeout_ms": 15000},
            "credential_binding": {
                "credentials": [{"alias": "orders", "credential_id": "cred-orders"}],
                "headers": {"Authorization": "Bearer {{ orders.password }}"},
            },
        },
    )

    assert response.status_code == 200
    updated = server_repo.docs["mcp_api_monitor"]
    assert updated["name"] == "Orders MCP"
    assert updated["description"] == "Updated"
    assert updated["enabled"] is False
    assert updated["default_enabled"] is True
    assert updated["endpoint_config"]["base_url"] == "https://api.example.test"
    assert updated["credential_binding"]["credentials"][0]["credential_id"] == "cred-orders"
    assert "updated_at" in updated


def test_update_api_monitor_tool_reparses_yaml(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_api_monitor_server_doc()])
    tool_repo = _MemoryRepo([_api_monitor_tool_doc()])

    def fake_get_repository(collection_name):
        return server_repo if collection_name == "user_mcp_servers" else tool_repo

    monkeypatch.setattr(mcp_route, "get_repository", fake_get_repository)

    response = client.put(
        "/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-tools/tool_1",
        json={
            "yaml_definition": """
name: create_order
description: Create an order
method: POST
url: /api/orders
parameters:
  type: object
  properties:
    name:
      type: string
request:
  body:
    name: "{{ name }}"
response:
  type: object
""".strip()
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == "tool_1"
    assert data["name"] == "create_order"
    assert data["method"] == "POST"
    assert data["body_mapping"] == {"name": "{{ name }}"}
    assert data["validation_status"] == "valid"
    assert tool_repo.docs["tool_1"]["name"] == "create_order"
    assert "updated_at" in tool_repo.docs["tool_1"]


def test_api_monitor_tool_test_invalid_returns_structured_error(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_api_monitor_server_doc()])
    tool_repo = _MemoryRepo(
        [
            _api_monitor_tool_doc(
                validation_status="invalid",
                validation_errors=["name is required"],
            )
        ]
    )

    def fake_get_repository(collection_name):
        return server_repo if collection_name == "user_mcp_servers" else tool_repo

    class _Runtime:
        def __init__(self, server):
            raise AssertionError("invalid tools should not be delegated to runtime")

    monkeypatch.setattr(mcp_route, "get_repository", fake_get_repository)
    monkeypatch.setattr(mcp_route, "ApiMonitorMcpRuntime", _Runtime)

    response = client.post(
        "/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-tools/tool_1/test",
        json={"arguments": {"keyword": "abc"}},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "success": False,
        "validation_status": "invalid",
        "validation_errors": ["name is required"],
        "error": "API Monitor tool is invalid",
    }


def test_api_monitor_tool_test_valid_delegates_to_runtime(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_api_monitor_server_doc()])
    tool_repo = _MemoryRepo([_api_monitor_tool_doc()])
    calls = []

    def fake_get_repository(collection_name):
        return server_repo if collection_name == "user_mcp_servers" else tool_repo

    class _Runtime:
        def __init__(self, server):
            assert server.id == "mcp_api_monitor"
            assert server.transport == "api_monitor"

        async def call_tool(self, tool_name, arguments):
            calls.append((tool_name, arguments))
            return {"success": True, "body": {"ok": True}}

    monkeypatch.setattr(mcp_route, "get_repository", fake_get_repository)
    monkeypatch.setattr(mcp_route, "ApiMonitorMcpRuntime", _Runtime)

    response = client.post(
        "/api/v1/mcp/servers/user:mcp_api_monitor/api-monitor-tools/tool_1/test",
        json={"arguments": {"keyword": "abc"}},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"success": True, "body": {"ok": True}}
    assert calls == [("search_orders", {"keyword": "abc"})]


def test_create_mcp_server_rejects_stdio_outside_local(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    monkeypatch.setattr(mcp_route.settings, "storage_backend", "docker")

    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "Local Python MCP",
            "transport": "stdio",
            "endpoint_config": {"command": "python", "args": ["-m", "demo"]},
            "credential_binding": {},
            "tool_policy": {},
            "default_enabled": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "stdio MCP is only allowed in local mode"


def test_create_mcp_server_rejects_missing_required_endpoint_field():
    app = _build_app()
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "Broken HTTP MCP",
            "transport": "streamable_http",
            "endpoint_config": {},
            "credential_binding": {},
            "tool_policy": {},
            "default_enabled": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "endpoint_config.url is required for HTTP/SSE MCP"


def test_create_mcp_server_rejects_wrong_endpoint_field_types(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    monkeypatch.setattr(mcp_route.settings, "storage_backend", "local")

    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "Broken Local MCP",
            "transport": "stdio",
            "endpoint_config": {"command": "python", "args": "-m demo"},
            "credential_binding": {},
            "tool_policy": {},
            "default_enabled": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "endpoint_config.args must be a list of strings"


def test_create_mcp_server_rejects_boolean_timeout(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "Broken HTTP Timeout MCP",
            "transport": "streamable_http",
            "endpoint_config": {"url": "https://example.test/mcp", "timeout_ms": True},
            "credential_binding": {},
            "tool_policy": {},
            "default_enabled": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "endpoint_config.timeout_ms must be a positive integer"


def test_update_session_override_writes_binding_for_owned_session(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    repo = _BindingRepo()

    async def fake_get_session(session_id: str):
        return SimpleNamespace(session_id=session_id, user_id="user-1")

    monkeypatch.setattr(
        mcp_route,
        "async_get_science_session",
        fake_get_session,
    )
    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="pubmed",
                name="PubMed",
                transport="streamable_http",
                url="https://example.test/mcp",
                headers={"Authorization": "Bearer top-secret"},
            )
        ],
    )
    monkeypatch.setattr(
        mcp_route,
        "get_repository",
        lambda collection_name: repo,
    )

    response = client.put(
        "/api/v1/sessions/session-123/mcp/servers/system:pubmed",
        json={"mode": "enabled"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "session_id": "session-123",
        "server_key": "system:pubmed",
        "mode": "enabled",
    }
    assert len(repo.calls) == 1
    assert repo.calls[0]["filter"] == {
        "session_id": "session-123",
        "user_id": "user-1",
        "server_key": "system:pubmed",
    }
    assert repo.calls[0]["update"]["$set"]["mode"] == "enabled"
    assert "updated_at" in repo.calls[0]["update"]["$set"]
    assert repo.calls[0]["upsert"] is True


def test_update_session_override_rejects_other_users_session(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    repo = _BindingRepo()

    async def fake_get_session(session_id: str):
        return SimpleNamespace(session_id=session_id, user_id="other-user")

    monkeypatch.setattr(
        mcp_route,
        "async_get_science_session",
        fake_get_session,
    )
    monkeypatch.setattr(
        mcp_route,
        "get_repository",
        lambda collection_name: repo,
    )

    response = client.put(
        "/api/v1/sessions/session-123/mcp/servers/system:pubmed",
        json={"mode": "disabled"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Access denied"
    assert repo.calls == []


def test_update_session_override_rejects_nonexistent_server_key(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    repo = _BindingRepo()

    async def fake_get_session(session_id: str):
        return SimpleNamespace(session_id=session_id, user_id="user-1")

    monkeypatch.setattr(mcp_route, "async_get_science_session", fake_get_session)
    monkeypatch.setattr(mcp_route, "load_system_mcp_servers", lambda: [])

    async def fake_user_servers(user_id: str):
        return []

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: repo)

    response = client.put(
        "/api/v1/sessions/session-123/mcp/servers/system:missing",
        json={"mode": "enabled"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "MCP server not found"
    assert repo.calls == []


def test_update_session_override_rejects_foreign_user_server_key(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    repo = _BindingRepo()

    async def fake_get_session(session_id: str):
        return SimpleNamespace(session_id=session_id, user_id="user-1")

    monkeypatch.setattr(mcp_route, "async_get_science_session", fake_get_session)
    monkeypatch.setattr(mcp_route, "load_system_mcp_servers", lambda: [])

    async def fake_user_servers(user_id: str):
        assert user_id == "user-1"
        return [
            {
                "_id": "mcp_user_1",
                "name": "Owned MCP",
                "transport": "sse",
                "endpoint_config": {"url": "https://owned.example.test/sse"},
            }
        ]

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr(mcp_route, "get_repository", lambda collection_name: repo)

    response = client.put(
        "/api/v1/sessions/session-123/mcp/servers/user:mcp_foreign",
        json={"mode": "enabled"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "MCP server not found"
    assert repo.calls == []


def test_get_mcp_server_returns_system_detail_by_server_key(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="pubmed",
                name="PubMed",
                description="System search",
                transport="streamable_http",
                default_enabled=True,
                url="https://example.test/mcp",
                headers={"Authorization": "Bearer top-secret"},
                tool_policy={"allowed_tools": ["search_articles"]},
            )
        ],
    )
    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", lambda user_id: [])

    response = client.get("/api/v1/mcp/servers/system:pubmed")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["server_key"] == "system:pubmed"
    assert data["readonly"] is True
    assert data["endpoint_config"]["headers"] == {"Authorization": "Bearer top-secret"}
    assert data["tool_policy"]["allowed_tools"] == ["search_articles"]


def test_update_mcp_server_updates_owned_user_doc(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    repo = _MemoryRepo(
        [
            {
                "_id": "mcp_user_1",
                "user_id": "user-1",
                "name": "Private MCP",
                "description": "Before",
                "transport": "streamable_http",
                "enabled": True,
                "default_enabled": False,
                "endpoint_config": {"url": "https://old.example.test/mcp", "timeout_ms": 20000, "env": {}, "headers": {}},
                "credential_binding": {"credential_id": "", "headers": {}, "env": {}, "query": {}},
                "tool_policy": {"allowed_tools": [], "blocked_tools": []},
            }
        ]
    )
    monkeypatch.setattr(mcp_route, "get_repository", lambda _: repo)

    response = client.put(
        "/api/v1/mcp/servers/mcp_user_1",
        json={
            "name": "Updated MCP",
            "description": "After",
            "transport": "streamable_http",
            "enabled": False,
            "default_enabled": True,
            "endpoint_config": {
                "url": "https://new.example.test/mcp",
                "timeout_ms": 30000,
                "headers": {"Authorization": "Bearer user-token"},
            },
            "credential_binding": {"credential_id": "cred-1"},
            "tool_policy": {"allowed_tools": ["search_articles"]},
        },
    )

    assert response.status_code == 200
    assert repo.docs["mcp_user_1"]["name"] == "Updated MCP"
    assert repo.docs["mcp_user_1"]["enabled"] is False
    assert repo.docs["mcp_user_1"]["default_enabled"] is True
    assert repo.docs["mcp_user_1"]["endpoint_config"]["url"] == "https://new.example.test/mcp"
    assert repo.docs["mcp_user_1"]["endpoint_config"]["headers"] == {"Authorization": "Bearer user-token"}
    assert repo.docs["mcp_user_1"]["credential_binding"]["credential_id"] == "cred-1"


def test_update_mcp_server_persists_multiple_credential_bindings(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    repo = _MemoryRepo(
        [
            {
                "_id": "mcp_user_1",
                "user_id": "user-1",
                "name": "Private MCP",
                "description": "Before",
                "transport": "streamable_http",
                "enabled": True,
                "default_enabled": False,
                "endpoint_config": {"url": "https://old.example.test/mcp", "timeout_ms": 20000, "env": {}, "headers": {}},
                "credential_binding": {"credential_id": "", "credentials": [], "headers": {}, "env": {}, "query": {}},
                "tool_policy": {"allowed_tools": [], "blocked_tools": []},
            }
        ]
    )
    monkeypatch.setattr(mcp_route, "get_repository", lambda _: repo)

    response = client.put(
        "/api/v1/mcp/servers/mcp_user_1",
        json={
            "name": "Updated MCP",
            "description": "After",
            "transport": "streamable_http",
            "enabled": True,
            "default_enabled": False,
            "endpoint_config": {
                "url": "https://new.example.test/mcp",
                "timeout_ms": 30000,
                "headers": {"Accept": "application/json"},
            },
            "credential_binding": {
                "credentials": [
                    {"alias": "github", "credential_id": "cred-github"},
                    {"alias": "sentry", "credential_id": "cred-sentry"},
                ],
                "headers": {
                    "Authorization": "Bearer {{ github.password }}",
                    "X-Sentry-Token": "{{ sentry.password }}",
                },
                "env": {"GITHUB_USER": "{{ github.username }}"},
                "query": {"api_key": "{{ sentry.password }}"},
            },
            "tool_policy": {"allowed_tools": ["search_articles"], "blocked_tools": []},
        },
    )

    assert response.status_code == 200
    binding = repo.docs["mcp_user_1"]["credential_binding"]
    assert binding["credential_id"] == ""
    assert binding["credentials"] == [
        {"alias": "github", "credential_id": "cred-github"},
        {"alias": "sentry", "credential_id": "cred-sentry"},
    ]
    assert binding["headers"]["Authorization"] == "Bearer {{ github.password }}"
    assert binding["env"] == {"GITHUB_USER": "{{ github.username }}"}
    assert binding["query"] == {"api_key": "{{ sentry.password }}"}


def test_delete_mcp_server_removes_owned_user_doc(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    repo = _MemoryRepo(
        [
            {
                "_id": "mcp_user_1",
                "user_id": "user-1",
                "name": "Private MCP",
                "transport": "sse",
                "endpoint_config": {"url": "https://user.example.test/sse"},
            }
        ]
    )
    monkeypatch.setattr(mcp_route, "get_repository", lambda _: repo)

    response = client.delete("/api/v1/mcp/servers/mcp_user_1")

    assert response.status_code == 200
    assert response.json()["data"] == {"id": "mcp_user_1", "deleted": True}
    assert repo.docs == {}


def test_discover_tools_returns_marshaled_tools(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="pubmed",
                name="PubMed",
                transport="streamable_http",
                url="https://example.test/mcp",
                headers={"Authorization": "Bearer top-secret"},
            )
        ],
    )
    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", lambda user_id: [])

    class _Runtime:
        async def list_tools(self):
            return [
                {
                    "name": "search_articles",
                    "description": "Search PubMed",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                }
            ]

    class _RuntimeFactory:
        def create_runtime(self, server):
            assert server.id == "pubmed"
            assert server.headers == {"Authorization": "Bearer top-secret"}
            return _Runtime()

    monkeypatch.setattr(mcp_route, "McpSdkRuntimeFactory", lambda: _RuntimeFactory())

    response = client.post("/api/v1/mcp/servers/system:pubmed/discover-tools")

    assert response.status_code == 200
    assert response.json()["data"]["tools"] == [
        {
            "name": "search_articles",
            "description": "Search PubMed",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
    ]


def test_discover_tools_for_rpa_gateway_uses_internal_registry(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="rpa_gateway",
                name="RPA Tool Gateway",
                description="Unified entry for published RPA MCP tools",
                transport="streamable_http",
                url="http://localhost:12001/api/v1/rpa-mcp/mcp",
            )
        ],
    )
    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", lambda user_id: [])

    async def fake_rpa_gateway_tools(user_id: str):
        assert user_id == "user-1"
        return [
            {
                "name": "rpa_download_invoice",
                "description": "Download invoice",
                "input_schema": {"type": "object", "properties": {"month": {"type": "string"}}},
                "output_schema": {"type": "object", "properties": {"success": {"type": "boolean"}}},
            }
        ]

    class _RuntimeFactory:
        def create_runtime(self, server):
            raise AssertionError("RPA gateway discovery should not use the generic MCP SDK runtime")

    monkeypatch.setattr(mcp_route, "_build_rpa_gateway_tools", fake_rpa_gateway_tools, raising=False)
    monkeypatch.setattr(mcp_route, "McpSdkRuntimeFactory", lambda: _RuntimeFactory())

    response = client.post("/api/v1/mcp/servers/system:rpa_gateway/discover-tools")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "server_key": "system:rpa_gateway",
        "tools": [
            {
                "name": "rpa_download_invoice",
                "description": "Download invoice",
                "input_schema": {"type": "object", "properties": {"month": {"type": "string"}}},
            }
        ],
        "tool_count": 1,
    }


def test_discover_tools_resolves_user_mcp_credentials_before_runtime(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    monkeypatch.setattr(mcp_route, "load_system_mcp_servers", lambda: [])

    async def fake_user_servers(user_id: str):
        return [
            {
                "_id": "mcp_user_1",
                "user_id": user_id,
                "name": "Private MCP",
                "description": "Private search",
                "transport": "streamable_http",
                "enabled": True,
                "default_enabled": False,
                "endpoint_config": {
                    "url": "https://user.example.test/mcp",
                    "headers": {"Accept": "application/json"},
                    "env": {"STATIC_ENV": "1"},
                    "timeout_ms": 20000,
                },
                "credential_binding": {
                    "credentials": [{"alias": "github", "credential_id": "cred-github"}],
                    "headers": {"Authorization": "Bearer {{ github.password }}"},
                    "env": {"GITHUB_TOKEN": "{{ github.password }}"},
                    "query": {"api_key": "{{ github.password }}"},
                },
                "tool_policy": {"allowed_tools": [], "blocked_tools": []},
            }
        ]

    async def fake_apply(server, user_id: str):
        assert user_id == "user-1"
        return server.model_copy(
            update={
                "headers": {**server.headers, "Authorization": "Bearer resolved"},
                "env": {**server.env, "GITHUB_TOKEN": "resolved"},
                "url": "https://user.example.test/mcp?api_key=resolved",
            }
        )

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr(mcp_route, "apply_mcp_credentials", fake_apply)

    class _Runtime:
        async def list_tools(self):
            return []

    class _RuntimeFactory:
        def create_runtime(self, server):
            assert server.id == "mcp_user_1"
            assert server.headers == {
                "Accept": "application/json",
                "Authorization": "Bearer resolved",
            }
            assert server.env == {
                "STATIC_ENV": "1",
                "GITHUB_TOKEN": "resolved",
            }
            assert server.url == "https://user.example.test/mcp?api_key=resolved"
            return _Runtime()

    monkeypatch.setattr(mcp_route, "McpSdkRuntimeFactory", lambda: _RuntimeFactory())

    response = client.post("/api/v1/mcp/servers/user:mcp_user_1/discover-tools")

    assert response.status_code == 200
    assert response.json()["data"]["tool_count"] == 0


def test_list_session_mcp_returns_session_modes_for_owner(monkeypatch):
    app = _build_app()
    client = TestClient(app)

    async def fake_get_session(session_id: str):
        return SimpleNamespace(session_id=session_id, user_id="user-1")

    binding_repo = _MemoryRepo(
        [
            {
                "_id": "binding-1",
                "session_id": "session-123",
                "user_id": "user-1",
                "server_key": "user:mcp_user_1",
                "mode": "disabled",
            }
        ]
    )
    monkeypatch.setattr(mcp_route, "async_get_science_session", fake_get_session)
    monkeypatch.setattr(
        mcp_route,
        "load_system_mcp_servers",
        lambda: [
            McpServerDefinition(
                id="pubmed",
                name="PubMed",
                transport="streamable_http",
                default_enabled=True,
                url="https://example.test/mcp",
            )
        ],
    )

    async def fake_user_servers(user_id: str):
        return [
            {
                "_id": "mcp_user_1",
                "user_id": user_id,
                "name": "Private MCP",
                "transport": "sse",
                "default_enabled": True,
                "endpoint_config": {"url": "https://user.example.test/sse"},
            }
        ]

    monkeypatch.setattr(mcp_route, "_list_user_mcp_servers", fake_user_servers)
    monkeypatch.setattr(
        mcp_route,
        "get_repository",
        lambda collection_name: binding_repo if collection_name == "session_mcp_bindings" else _MemoryRepo(),
    )

    response = client.get("/api/v1/sessions/session-123/mcp")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["server_key"] == "system:pubmed"
    assert data[0]["session_mode"] == "inherit"
    assert data[0]["effective_enabled"] is True
    assert data[1]["server_key"] == "user:mcp_user_1"
    assert data[1]["session_mode"] == "disabled"
    assert data[1]["effective_enabled"] is False
