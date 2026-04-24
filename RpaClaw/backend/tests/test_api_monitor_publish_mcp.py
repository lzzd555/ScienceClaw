import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.route import api_monitor as api_monitor_route
from backend.rpa.api_monitor.models import ApiMonitorSession, ApiToolDefinition
from backend.rpa.api_monitor_mcp_registry import ApiMonitorMcpRegistry


class _User:
    id = "user-1"
    username = "tester"


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

    async def insert_one(self, document):
        self.docs[str(document["_id"])] = dict(document)
        return str(document["_id"])

    async def update_one(self, filter_doc, update_doc, upsert=False):
        for doc_id, doc in list(self.docs.items()):
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                updated = dict(doc)
                updated.update(update_doc.get("$set", {}))
                self.docs[doc_id] = updated
                return 1
        if upsert:
            new_doc = {key: value for key, value in filter_doc.items() if not isinstance(value, dict)}
            new_doc.update(update_doc.get("$setOnInsert", {}))
            new_doc.update(update_doc.get("$set", {}))
            self.docs[str(new_doc["_id"])] = new_doc
            return 1
        return 0

    async def delete_many(self, filter_doc):
        deleted = 0
        for doc_id, doc in list(self.docs.items()):
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                del self.docs[doc_id]
                deleted += 1
        return deleted


def _build_session() -> ApiMonitorSession:
    return ApiMonitorSession(
        id="session_1",
        user_id="user-1",
        sandbox_session_id="sandbox_1",
        tool_definitions=[
            ApiToolDefinition(
                id="tool_1",
                session_id="session_1",
                name="search_orders",
                description="Search orders by keyword",
                method="GET",
                url_pattern="/api/orders",
                request_body_schema={"type": "object", "properties": {"keyword": {"type": "string"}}},
                yaml_definition="""name: search_orders
description: Search orders by keyword
method: GET
url: /api/orders
parameters:
  type: object
  properties:
    tenant_id:
      type: string
    keyword:
      type: string
request:
  path:
    tenant_id: "{{ tenant_id }}"
  query:
    keyword: "{{ keyword }}"
  body:
    page_size: 20
  headers:
    X-Tenant-ID: "{{ tenant_id }}"
response:
  type: object
""",
                selected=True,
            ),
            ApiToolDefinition(
                id="tool_2",
                session_id="session_1",
                name="create_user",
                description="Create user",
                method="POST",
                url_pattern="/api/users",
                request_body_schema={"type": "object", "properties": {"name": {"type": "string"}}},
                yaml_definition="name: create_user",
                selected=True,
            ),
        ],
    )


def _build_app():
    app = FastAPI()
    app.include_router(api_monitor_route.router, prefix="/api/v1")
    app.dependency_overrides[api_monitor_route.get_current_user] = lambda: _User()
    return app


def test_registry_replace_tools_rewrites_existing_collection():
    server_repo = _MemoryRepo()
    tool_repo = _MemoryRepo(
        [
            {
                "_id": "old_tool",
                "user_id": "u1",
                "mcp_server_id": "mcp_existing",
                "name": "old_tool",
                "method": "GET",
                "url_pattern": "/old",
                "source": "api_monitor",
            }
        ]
    )
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    asyncio.run(
        registry.replace_tools(
            mcp_server_id="mcp_existing",
            user_id="u1",
            session_tools=[
                {
                    "id": "tool_1",
                    "name": "list_users",
                    "description": "List users",
                    "method": "GET",
                    "url_pattern": "/api/users",
                    "yaml_definition": "name: list_users",
                }
            ],
        )
    )

    tools = asyncio.run(tool_repo.find_many({"mcp_server_id": "mcp_existing", "user_id": "u1"}))
    assert len(tools) == 1
    assert tools[0]["name"] == "list_users"
    assert tools[0]["source"] == "api_monitor"


def test_registry_replace_tools_does_not_delete_when_document_build_fails():
    server_repo = _MemoryRepo()
    tool_repo = _MemoryRepo(
        [
            {
                "_id": "old_tool",
                "user_id": "u1",
                "mcp_server_id": "mcp_existing",
                "name": "old_tool",
                "source": "api_monitor",
            }
        ]
    )
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    with pytest.raises(AttributeError):
        asyncio.run(
            registry.replace_tools(
                mcp_server_id="mcp_existing",
                user_id="u1",
                session_tools=[object()],
            )
        )

    tools = asyncio.run(tool_repo.find_many({"mcp_server_id": "mcp_existing", "user_id": "u1"}))
    assert [tool["name"] for tool in tools] == ["old_tool"]


def test_publish_session_overwrite_replaces_all_existing_tools():
    server_repo = _MemoryRepo(
        [
            {
                "_id": "mcp_existing",
                "user_id": "u1",
                "name": "Example MCP",
                "description": "Old description",
                "transport": "api_monitor",
                "source_type": "api_monitor",
                "endpoint_config": {"base_url": "https://api.example.test"},
                "credential_binding": {"type": "bearer", "secret_id": "secret_1"},
            }
        ]
    )
    tool_repo = _MemoryRepo(
        [
            {
                "_id": "old_tool",
                "user_id": "u1",
                "mcp_server_id": "mcp_existing",
                "name": "old_tool",
                "method": "GET",
                "url_pattern": "/old",
                "source": "api_monitor",
            }
        ]
    )
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    result = asyncio.run(
        registry.publish_session(
            session=_build_session(),
            user_id="u1",
            mcp_name="Example MCP",
            description="New description",
            overwrite=True,
            existing_server_id="mcp_existing",
        )
    )

    tools = asyncio.run(tool_repo.find_many({"mcp_server_id": "mcp_existing", "user_id": "u1"}))
    server = asyncio.run(server_repo.find_one({"_id": "mcp_existing", "user_id": "u1"}))
    assert result["overwritten"] is True
    assert server["endpoint_config"] == {"base_url": "https://api.example.test"}
    assert server["credential_binding"] == {"type": "bearer", "secret_id": "secret_1"}
    assert sorted(tool["name"] for tool in tools) == ["create_user", "search_orders"]


def test_publish_api_monitor_mcp_creates_server_and_tools(monkeypatch):
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
        json={"mcp_name": "Example MCP", "description": "Captured APIs", "confirm_overwrite": False},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["saved"] is True
    assert data["tool_count"] == 2
    assert len(tool_repo.docs) == 2


def test_publish_api_monitor_mcp_requires_confirm_on_duplicate(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo(
        [
            {
                "_id": "mcp_existing",
                "user_id": "user-1",
                "name": "Example MCP",
                "description": "Old",
                "transport": "api_monitor",
                "source_type": "api_monitor",
            }
        ]
    )
    tool_repo = _MemoryRepo()

    monkeypatch.setattr(
        "backend.rpa.api_monitor_mcp_registry.get_repository",
        lambda collection_name: server_repo if collection_name == "user_mcp_servers" else tool_repo,
    )
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: _build_session())

    response = client.post(
        "/api/v1/api-monitor/session/session_1/publish-mcp",
        json={"mcp_name": "Example MCP", "description": "Captured APIs", "confirm_overwrite": False},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "MCP with the same name already exists"
    assert response.json()["needs_confirmation"] is True


@pytest.mark.anyio
async def test_publish_persists_parsed_contract_fields_and_defaults():
    server_repo = _MemoryRepo([])
    tool_repo = _MemoryRepo([])
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    result = await registry.publish_session(
        session=_build_session(),
        user_id="user_1",
        mcp_name="Orders MCP",
        description="Order APIs",
        overwrite=False,
    )

    server = list(server_repo.docs.values())[0]
    tools = list(tool_repo.docs.values())

    assert result["server"]["default_enabled"] is True
    assert server["enabled"] is True
    assert server["default_enabled"] is True
    assert server["transport"] == "api_monitor"
    assert server["source_type"] == "api_monitor"
    assert server["endpoint_config"] == {}
    assert server["credential_binding"] == {}
    assert tools[0]["mcp_server_id"] == result["server_id"]
    assert tools[0]["user_id"] == "user_1"
    assert tools[0]["source"] == "api_monitor"
    assert tools[0]["source_session_id"] == "session_1"
    assert tools[0]["order"] == 0
    assert tools[0]["validation_status"] == "valid"
    assert tools[0]["validation_errors"] == []
    assert tools[0]["name"] == "search_orders"
    assert tools[0]["description"] == "Search orders by keyword"
    assert tools[0]["method"] == "GET"
    assert tools[0]["url"] == "/api/orders"
    assert tools[0]["input_schema"]["type"] == "object"
    assert tools[0]["path_mapping"]["tenant_id"] == "{{ tenant_id }}"
    assert tools[0]["query_mapping"]["keyword"] == "{{ keyword }}"
    assert tools[0]["body_mapping"]["page_size"] == 20
    assert tools[0]["header_mapping"]["X-Tenant-ID"] == "{{ tenant_id }}"
    assert tools[0]["response_schema"] == {"type": "object"}
    assert tools[0]["yaml_definition"].strip().startswith("name:")


@pytest.mark.anyio
async def test_publish_marks_duplicate_tool_names_invalid():
    session = _build_session()
    session.tool_definitions = [
        session.tool_definitions[0],
        session.tool_definitions[0].model_copy(deep=True),
    ]
    server_repo = _MemoryRepo([])
    tool_repo = _MemoryRepo([])
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)

    await registry.publish_session(
        session=session,
        user_id="user_1",
        mcp_name="Orders MCP",
        description="Order APIs",
        overwrite=False,
    )

    statuses = [tool["validation_status"] for tool in tool_repo.docs.values()]
    errors = [error for tool in tool_repo.docs.values() for error in tool["validation_errors"]]
    assert statuses == ["invalid", "invalid"]
    assert "duplicate tool name 'search_orders' in this API Monitor MCP" in errors


@pytest.mark.anyio
async def test_publish_session_includes_selected_tools_only():
    server_repo = _MemoryRepo([])
    tool_repo = _MemoryRepo([])
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)
    session = _build_session()
    session.tool_definitions[0].selected = True
    session.tool_definitions[1].selected = False
    skipped = session.tool_definitions[0].model_copy(deep=True)
    skipped.id = "tool_skipped"
    skipped.name = "skipped_tool"
    skipped.selected = False
    session.tool_definitions.append(skipped)

    result = await registry.publish_session(
        session=session,
        user_id="user_1",
        mcp_name="Example MCP",
        description="",
        overwrite=False,
    )

    tools = await tool_repo.find_many({"mcp_server_id": result["server_id"], "user_id": "user_1"})
    assert len(tools) == 1
    assert tools[0]["name"] == session.tool_definitions[0].name
    assert result["tool_count"] == 1


def test_update_tool_selection(monkeypatch):
    session = _build_session()
    session.tool_definitions[0].selected = True
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: session)

    app = _build_app()
    client = TestClient(app)

    response = client.patch(
        f"/api/v1/api-monitor/session/{session.id}/tools/{session.tool_definitions[0].id}/selection",
        json={"selected": False},
    )

    assert response.status_code == 200
    assert response.json()["tool"]["selected"] is False
    assert session.tool_definitions[0].selected is False
