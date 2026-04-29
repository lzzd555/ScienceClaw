from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.route import api_monitor_mcp_gateway as gateway


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
                for key, value in update_doc.get("$set", {}).items():
                    if "." in key:
                        parent, child = key.split(".", 1)
                        nested = dict(updated.get(parent) or {})
                        nested[child] = value
                        updated[parent] = nested
                    else:
                        updated[key] = value
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


def test_initialize_works_when_external_access_enabled(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_server_doc()])
    tool_repo = _MemoryRepo([])
    monkeypatch.setattr(
        gateway,
        "get_repository",
        lambda collection_name: tool_repo if collection_name == "api_monitor_mcp_tools" else server_repo,
    )

    response = client.post(
        "/api/v1/api-monitor-mcp/mcp_api_monitor/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
    )
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "Example MCP"


def test_tools_list_adds_auth_schema_for_test_credential(monkeypatch):
    app = _build_app()
    client = TestClient(app)
    server_repo = _MemoryRepo([_server_doc()])
    tool_repo = _MemoryRepo([_tool_doc(), _tool_doc(_id="tool_2", name="invalid_tool", validation_status="invalid")])
    monkeypatch.setattr(
        gateway,
        "get_repository",
        lambda collection_name: tool_repo if collection_name == "api_monitor_mcp_tools" else server_repo,
    )

    response = client.post(
        "/api/v1/api-monitor-mcp/mcp_api_monitor/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
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
    monkeypatch.setattr(
        gateway,
        "get_repository",
        lambda collection_name: tool_repo if collection_name == "api_monitor_mcp_tools" else server_repo,
    )

    response = client.post(
        "/api/v1/api-monitor-mcp/mcp_api_monitor/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search_orders", "arguments": {"keyword": "abc"}},
        },
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
    monkeypatch.setattr(
        gateway,
        "get_repository",
        lambda collection_name: tool_repo if collection_name == "api_monitor_mcp_tools" else server_repo,
    )

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
    )

    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {"success": True, "data": {"ok": True}}
