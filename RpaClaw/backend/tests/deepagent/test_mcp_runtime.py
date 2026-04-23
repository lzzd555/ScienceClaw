from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

import asyncio

import pytest
from mcp import types
from mcp.client.stdio import StdioServerParameters

from backend.deepagent import mcp_runtime
from backend.deepagent.mcp_runtime import McpSdkRuntimeFactory
from backend.mcp.models import McpServerDefinition


@dataclass
class FakeSession:
    list_tools_result: Callable[[str | None], Any] | None = None
    call_tool_result: Any = None
    list_calls: list[str | None] = field(default_factory=list)
    call_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    initialized: bool = False

    def __init__(
        self,
        read_stream: Any,
        write_stream: Any,
        *,
        list_tools_result: Callable[[str | None], Any] | None = None,
        call_tool_result: Any = None,
    ) -> None:
        self.read_stream = read_stream
        self.write_stream = write_stream
        self.list_tools_result = list_tools_result
        self.call_tool_result = call_tool_result
        self.list_calls = []
        self.call_calls = []
        self.initialized = False

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self, cursor: str | None = None):
        self.list_calls.append(cursor)
        if self.list_tools_result is None:
            return types.ListToolsResult(tools=[])
        return self.list_tools_result(cursor)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None):
        self.call_calls.append((name, dict(arguments or {})))
        return self.call_tool_result


class FakeAsyncClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "FakeAsyncClient":
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.exited = True

    async def request(self, method: str, url: str, **kwargs: Any):
        return _ApiResponse()


def _make_server(*, transport: str) -> McpServerDefinition:
    return McpServerDefinition(
        id="pubmed",
        name="PubMed",
        transport=transport,
        scope="system",
        url="https://example.test/mcp",
        command="python",
        args=["-m", "demo_mcp"],
        cwd="C:/demo",
        headers={"Authorization": "Bearer token"},
        env={"FOO": "bar"},
    )


class _MemoryRepo:
    def __init__(self, docs):
        self.docs = [dict(doc) for doc in docs]

    async def find_one(self, filter_doc):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in filter_doc.items()):
                return dict(doc)
        return None

    async def find_many(self, filter_doc, projection=None, sort=None, skip=0, limit=0):
        return [
            dict(doc)
            for doc in self.docs
            if all(doc.get(key) == value for key, value in filter_doc.items())
        ]


class _ApiResponse:
    is_success = True
    status_code = 200
    headers = {"content-type": "application/json"}
    text = '{"ok": true}'

    def json(self):
        return {"ok": True}


class _ApiMonitorAsyncClient:
    def __init__(self, **kwargs: Any) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any):
        self.calls.append((method, url, kwargs))
        return _ApiResponse()


@asynccontextmanager
async def _fake_stdio_client(captured: dict[str, Any], server_params: StdioServerParameters):
    captured["server_params"] = server_params
    yield ("read-stream", "write-stream")


@asynccontextmanager
async def _fake_sse_client(captured: dict[str, Any], *args: Any, **kwargs: Any):
    captured["args"] = args
    captured["kwargs"] = kwargs
    yield ("read-stream", "write-stream")


@asynccontextmanager
async def _fake_streamable_http_client(captured: dict[str, Any], *args: Any, **kwargs: Any):
    captured["args"] = args
    captured["kwargs"] = kwargs
    yield ("read-stream", "write-stream", lambda: "session-1")


def test_stdio_rejected_outside_local_mode(monkeypatch):
    monkeypatch.setattr(mcp_runtime.settings, "storage_backend", "mongo")
    factory = McpSdkRuntimeFactory()

    with pytest.raises(ValueError, match="stdio MCP is only allowed in local mode"):
        factory.create_runtime(_make_server(transport="stdio"))


def test_stdio_client_parameters_built_correctly_in_local_mode(monkeypatch):
    monkeypatch.setattr(mcp_runtime.settings, "storage_backend", "local")

    captured: dict[str, Any] = {}

    def fake_client_session(read_stream: Any, write_stream: Any):
        return FakeSession(read_stream, write_stream)

    monkeypatch.setattr(mcp_runtime, "ClientSession", fake_client_session)
    monkeypatch.setattr(
        mcp_runtime,
        "stdio_client",
        lambda server_params: _fake_stdio_client(captured, server_params),
    )

    runtime = McpSdkRuntimeFactory().create_runtime(_make_server(transport="stdio"))

    result = asyncio.run(runtime.list_tools())

    assert result == []
    assert isinstance(captured["server_params"], StdioServerParameters)
    assert captured["server_params"].command == "python"
    assert captured["server_params"].args == ["-m", "demo_mcp"]
    assert captured["server_params"].cwd == "C:/demo"
    assert captured["server_params"].env == {"FOO": "bar"}


@pytest.mark.parametrize(
    ("transport", "helper_name", "helper_factory"),
    [
        ("sse", "sse_client", _fake_sse_client),
        ("streamable_http", "streamable_http_client", _fake_streamable_http_client),
    ],
)
def test_transport_selection(monkeypatch, transport: str, helper_name: str, helper_factory):
    monkeypatch.setattr(mcp_runtime.settings, "storage_backend", "mongo")

    captured: dict[str, Any] = {}
    monkeypatch.setattr(mcp_runtime, helper_name, lambda *args, **kwargs: helper_factory(captured, *args, **kwargs))
    monkeypatch.setattr(mcp_runtime, "ClientSession", lambda read_stream, write_stream: FakeSession(read_stream, write_stream))
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient(**kwargs))

    runtime = McpSdkRuntimeFactory().create_runtime(_make_server(transport=transport))

    asyncio.run(runtime.list_tools())

    assert captured["args"][0] == "https://example.test/mcp"
    if transport == "sse":
        assert captured["kwargs"]["headers"] == {"Authorization": "Bearer token"}
        assert captured["kwargs"]["timeout"] == 20.0
        assert captured["kwargs"]["sse_read_timeout"] == 300.0
    else:
        assert captured["kwargs"]["http_client"].kwargs["headers"] == {"Authorization": "Bearer token"}
        assert captured["kwargs"]["http_client"].kwargs["timeout"] == 20.0


def test_list_tools_paginates(monkeypatch):
    monkeypatch.setattr(mcp_runtime.settings, "storage_backend", "mongo")

    pages = {
        None: types.ListToolsResult(
            tools=[
                types.Tool(
                    name="search",
                    description="Search",
                    inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
                )
            ],
            nextCursor="page-2",
        ),
        "page-2": types.ListToolsResult(
            tools=[
                types.Tool(
                    name="lookup",
                    description="Lookup",
                    inputSchema={"type": "object", "properties": {"id": {"type": "string"}}},
                )
            ],
        ),
    }

    def list_tools_result(cursor: str | None):
        return pages[cursor]

    monkeypatch.setattr(mcp_runtime, "ClientSession", lambda read_stream, write_stream: FakeSession(
        read_stream,
        write_stream,
        list_tools_result=list_tools_result,
    ))
    monkeypatch.setattr(
        mcp_runtime,
        "streamable_http_client",
        lambda *args, **kwargs: _fake_streamable_http_client({}, *args, **kwargs),
    )
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient(**kwargs))

    runtime = McpSdkRuntimeFactory().create_runtime(_make_server(transport="streamable_http"))

    tools = asyncio.run(runtime.list_tools())

    assert [tool.name for tool in tools] == ["search", "lookup"]
    assert [tool.description for tool in tools] == ["Search", "Lookup"]
    assert [set(tool.input_schema["properties"].keys()) for tool in tools] == [{"query"}, {"id"}]


def test_call_tool_result_normalization(monkeypatch):
    monkeypatch.setattr(mcp_runtime.settings, "storage_backend", "mongo")

    call_result = types.CallToolResult(
        content=[types.TextContent(type="text", text="hello")],
        structuredContent={"ok": True},
        isError=False,
    )

    monkeypatch.setattr(mcp_runtime, "ClientSession", lambda read_stream, write_stream: FakeSession(
        read_stream,
        write_stream,
        call_tool_result=call_result,
    ))
    monkeypatch.setattr(
        mcp_runtime,
        "streamable_http_client",
        lambda *args, **kwargs: _fake_streamable_http_client({}, *args, **kwargs),
    )
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient(**kwargs))

    runtime = McpSdkRuntimeFactory().create_runtime(_make_server(transport="streamable_http"))

    result = asyncio.run(runtime.call_tool("search", {"query": "cells"}))

    assert result == {
        "content": [{"type": "text", "text": "hello"}],
        "structuredContent": {"ok": True},
        "isError": False,
    }


def test_api_monitor_runtime_lists_internal_tools(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "list_users",
                "description": "List users",
                "validation_status": "valid",
                "input_schema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
                "request_body_schema": {"type": "object", "properties": {"page": {"type": "integer"}}},
            },
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "broken_tool",
                "validation_status": "invalid",
                "input_schema": {"type": "object", "properties": {"broken": {"type": "string"}}},
            },
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "legacy_tool",
                "request_body_schema": {"type": "object", "properties": {"page": {"type": "integer"}}},
            },
        ]
    )
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    tools = asyncio.run(runtime.list_tools())

    assert [tool.name for tool in tools] == ["list_users", "legacy_tool"]
    assert tools[0].input_schema["properties"] == {"keyword": {"type": "string"}}
    assert tools[1].input_schema["properties"] == {"page": {"type": "integer"}}


def test_api_monitor_runtime_does_not_call_invalid_tool(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "broken_tool",
                "validation_status": "invalid",
                "method": "GET",
                "base_url": "https://example.test",
                "url_pattern": "/api/broken",
            }
        ]
    )
    client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    result = asyncio.run(runtime.call_tool("broken_tool", {}))

    assert result == {"success": False, "error": "API Monitor tool 'broken_tool' not found"}
    assert client.calls == []


def test_api_monitor_runtime_calls_saved_api(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "get_user",
                "method": "GET",
                "base_url": "https://example.test",
                "url_pattern": "/api/users/{id}",
            }
        ]
    )
    client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    result = asyncio.run(runtime.call_tool("get_user", {"id": "42", "expand": "profile"}))

    assert result["success"] is True
    assert result["body"] == {"ok": True}
    assert client.calls == [
        ("GET", "https://example.test/api/users/42", {"params": {"expand": "profile"}})
    ]
