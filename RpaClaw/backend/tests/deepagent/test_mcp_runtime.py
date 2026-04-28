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
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        text: str = '{"ok": true}',
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}
        self._json_body = {"ok": True} if json_body is None else json_body
        self.text = text

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return self._json_body


class _ApiMonitorAsyncClient:
    def __init__(self, response: _ApiResponse | None = None, **kwargs: Any) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.kwargs = kwargs
        self.response = response or _ApiResponse()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any):
        self.calls.append((method, url, kwargs))
        return self.response


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


def test_api_monitor_runtime_lists_only_valid_tools(monkeypatch):
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
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "",
                "description": "Empty name",
                "validation_status": "valid",
                "input_schema": {"type": "object", "properties": {"ignored": {"type": "string"}}},
            },
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "pending_tool",
                "description": "No parsed validation status yet",
                "input_schema": {"type": "object", "properties": {"ignored": {"type": "string"}}},
            },
        ]
    )
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    tools = asyncio.run(runtime.list_tools())

    assert [tool.name for tool in tools] == ["list_users"]
    assert tools[0].input_schema["properties"] == {"keyword": {"type": "string"}}


def test_api_monitor_runtime_uses_legacy_request_body_schema(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "legacy_tool",
                "validation_status": "valid",
                "method": "POST",
                "url": "https://api.example.test/items",
                "request_body_schema": {"type": "object", "properties": {"page": {"type": "integer"}}},
            }
        ]
    )
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    tools = asyncio.run(runtime.list_tools())

    assert [tool.name for tool in tools] == ["legacy_tool"]
    assert tools[0].input_schema == {"type": "object", "properties": {"page": {"type": "integer"}}}


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
    def fake_async_client(**kwargs):
        client.kwargs = kwargs
        return client

    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", fake_async_client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    result = asyncio.run(runtime.call_tool("broken_tool", {}))

    assert result == {"success": False, "error": "API Monitor tool 'broken_tool' not found"}
    assert client.calls == []


def test_api_monitor_runtime_maps_arguments_headers_and_query(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "get_user",
                "validation_status": "valid",
                "method": "GET",
                "url": "/api/users/{{ id }}?access_token={{ access_token }}",
                "base_url": "https://captured.example",
                "header_mapping": {
                    "X-Request-Id": "{{ request_id }}",
                    "Authorization": "Bearer {{ user_token }}",
                },
                "query_mapping": {
                    "expand": "{{ expand }}",
                    "count": "{{ count }}",
                    "accessToken": "{{ access_token }}",
                },
            }
        ]
    )
    client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    def fake_api_monitor_client(**kwargs):
        client.kwargs = kwargs
        return client

    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", fake_api_monitor_client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(
            id="mcp_api_monitor",
            name="Example MCP",
            transport="api_monitor",
            scope="user",
            url="https://example.test/root?tenant=acme&credential=secret",
            headers={"Accept": "application/json", "X-Api-Key": "server-secret"},
            timeout_ms=45000,
        )
    )

    result = asyncio.run(
        runtime.call_tool(
            "get_user",
            {
                "id": "42",
                "expand": "profile",
                "count": 3,
                "request_id": "req-1",
                "user_token": "user-secret",
                "access_token": "query-secret",
            },
        )
    )

    assert result["success"] is True
    assert result["body"] == {"ok": True}
    assert client.calls == [
        (
            "GET",
            "https://example.test/api/users/42?access_token=query-secret",
            {
                "params": {
                    "tenant": "acme",
                    "credential": "secret",
                    "expand": "profile",
                    "count": 3,
                    "accessToken": "query-secret",
                },
                "headers": {
                    "Accept": "application/json",
                    "X-Api-Key": "server-secret",
                    "X-Request-Id": "req-1",
                    "Authorization": "Bearer user-secret",
                },
            },
        )
    ]
    assert client.kwargs["timeout"] == 45.0
    assert result["request_preview"] == {
        "method": "GET",
        "url": "https://example.test/api/users/42?access_token=***",
        "query": {
            "tenant": "acme",
            "credential": "***",
            "expand": "profile",
            "count": 3,
            "accessToken": "***",
        },
        "headers": {
            "Accept": "application/json",
            "X-Api-Key": "***",
            "X-Request-Id": "req-1",
            "Authorization": "***",
        },
        "body": None,
        "auth": {},
    }


def test_api_monitor_runtime_redacts_path_and_fragment_preview(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "get_session",
                "validation_status": "valid",
                "method": "GET",
                "url": "https://api.example.test/tokens/token-{{ access_token }}.json/users/{{ userId }}#access_token={{ access_token }}&state=x",
            }
        ]
    )
    client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    result = asyncio.run(
        runtime.call_tool(
            "get_session",
            {"accessToken": "session-secret", "userId": "42", "access_token": "frag-secret"},
        )
    )

    assert result["success"] is True
    assert client.calls == [
        (
            "GET",
            "https://api.example.test/tokens/token-frag-secret.json/users/42#access_token=frag-secret&state=x",
            {"params": {}, "headers": {}},
        )
    ]
    assert result["request_preview"]["url"] == "https://api.example.test/tokens/***/users/42#access_token=***&state=x"


def test_api_monitor_runtime_redacts_plain_fragment_preview(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "get_document",
                "validation_status": "valid",
                "method": "GET",
                "url": "https://example.test/#session-secret",
            }
        ]
    )
    client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    result = asyncio.run(runtime.call_tool("get_document", {}))

    assert result["success"] is True
    assert client.calls == [
        (
            "GET",
            "https://example.test/#session-secret",
            {"params": {}, "headers": {}},
        )
    ]
    assert result["request_preview"]["url"] == "https://example.test/#****"


def test_api_monitor_runtime_falls_back_to_tool_base_url(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "create_order",
                "validation_status": "valid",
                "method": "POST",
                "url": "/api/orders",
                "base_url": "https://captured.example",
            }
        ]
    )
    client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    result = asyncio.run(runtime.call_tool("create_order", {}))

    assert result["success"] is True
    assert client.calls == [
        (
            "POST",
            "https://captured.example/api/orders",
            {"params": {}, "headers": {}},
        )
    ]
    assert result["request_preview"]["url"] == "https://captured.example/api/orders"


def test_api_monitor_runtime_posts_rendered_body_mapping(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "create_items",
                "validation_status": "valid",
                "method": "POST",
                "url": "https://api.example.test/items",
                "body_mapping": {
                    "count": "{{ count }}",
                    "credentials": {
                        "apiKey": "{{ api_key }}",
                        "nested": [{"refreshToken": "{{ refresh_token }}"}],
                    },
                    "items": [{"name": "{{ name }}", "clientSecret": "{{ client_secret }}"}],
                },
            }
        ]
    )
    client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    result = asyncio.run(
        runtime.call_tool(
            "create_items",
            {
                "count": 2,
                "name": "cell",
                "api_key": "body-secret",
                "refresh_token": "refresh-secret",
                "client_secret": "client-secret",
            },
        )
    )

    assert result["success"] is True
    assert client.calls == [
        (
            "POST",
            "https://api.example.test/items",
            {
                "params": {},
                "headers": {},
                "json": {
                    "count": 2,
                    "credentials": {
                        "apiKey": "body-secret",
                        "nested": [{"refreshToken": "refresh-secret"}],
                    },
                    "items": [{"name": "cell", "clientSecret": "client-secret"}],
                },
            },
        )
    ]
    assert result["request_preview"]["body"] == {
        "count": 2,
        "credentials": {
            "apiKey": "***",
            "nested": [{"refreshToken": "***"}],
        },
        "items": [{"name": "cell", "clientSecret": "***"}],
    }


def test_api_monitor_runtime_redacts_camelcase_query_and_body_preview(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "update_profile",
                "validation_status": "valid",
                "method": "POST",
                "url": "https://api.example.test/profile",
                "query_mapping": {
                    "accessToken": "{{ access_token }}",
                },
                "body_mapping": {
                    "credentials": {
                        "clientSecret": "{{ client_secret }}",
                        "nested": [{"refreshToken": "{{ refresh_token }}"}],
                    }
                },
            }
        ]
    )
    client = _ApiMonitorAsyncClient()
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(id="mcp_api_monitor", name="Example MCP", transport="api_monitor", scope="user")
    )

    result = asyncio.run(
        runtime.call_tool(
            "update_profile",
            {
                "access_token": "query-secret",
                "client_secret": "body-secret",
                "refresh_token": "refresh-secret",
            },
        )
    )

    assert result["success"] is True
    assert client.calls == [
        (
            "POST",
            "https://api.example.test/profile",
            {
                "params": {"accessToken": "query-secret"},
                "headers": {},
                "json": {
                    "credentials": {
                        "clientSecret": "body-secret",
                        "nested": [{"refreshToken": "refresh-secret"}],
                    }
                },
            },
        )
    ]
    assert result["request_preview"]["query"] == {"accessToken": "***"}
    assert result["request_preview"]["body"] == {
        "credentials": {
            "clientSecret": "***",
            "nested": [{"refreshToken": "***"}],
        }
    }


def test_api_monitor_runtime_returns_structured_non_2xx(monkeypatch):
    repo = _MemoryRepo(
        [
            {
                "mcp_server_id": "mcp_api_monitor",
                "name": "get_user",
                "validation_status": "valid",
                "method": "GET",
                "url": "/api/users/{{ id }}",
            }
        ]
    )
    client = _ApiMonitorAsyncClient(
        response=_ApiResponse(status_code=404, json_body={"error": "not found"}, text='{"error": "not found"}')
    )
    monkeypatch.setattr(mcp_runtime, "get_repository", lambda collection_name: repo)
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: client)

    runtime = McpSdkRuntimeFactory().create_runtime(
        McpServerDefinition(
            id="mcp_api_monitor",
            name="Example MCP",
            transport="api_monitor",
            scope="user",
            url="https://example.test",
            headers={"Authorization": "Bearer server-secret"},
        )
    )

    result = asyncio.run(runtime.call_tool("get_user", {"id": "missing"}))

    assert result["success"] is False
    assert result["status_code"] == 404
    assert result["body"] == {"error": "not found"}
    assert result["request_preview"]["headers"] == {"Authorization": "***"}


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


# ── Token flow runtime tests ────────────────────────────────────────────


def test_token_flow_setup_uses_tool_base_url_when_server_url_empty(monkeypatch):
    """Relative setup URLs should use the API Monitor tool base_url fallback."""
    api_client = _ApiMonitorAsyncClient(
        response=_ApiResponse(json_body={"csrfToken": "extracted-token-value"})
    )
    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: api_client)
    monkeypatch.setattr(
        mcp_runtime,
        "get_repository",
        lambda collection_name: _MemoryRepo([
            {
                "_id": "tool_1",
                "mcp_server_id": "mcp_api_monitor",
                "name": "create_order",
                "description": "Create order",
                "method": "POST",
                "url": "/api/orders",
                "base_url": "https://captured.example",
                "input_schema": {"type": "object", "properties": {}},
                "validation_status": "valid",
            }
        ]),
    )

    class _Vault:
        async def resolve_credential_values(self, user_id, cred_id):
            return None

    monkeypatch.setattr(mcp_runtime, "get_vault", lambda: _Vault())

    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="user-1",
        name="CSRF API",
        transport="api_monitor",
        scope="user",
        url="",
        api_monitor_auth={
            "credential_type": "placeholder",
            "credential_id": "",
            "token_flows": [
                {
                    "id": "flow_1",
                    "name": "csrf_token",
                    "setup": [
                        {
                            "method": "GET",
                            "url": "/api/login",
                            "extract": {"from": "response.body", "path": "$.csrfToken"},
                        }
                    ],
                    "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                    "applies_to": [{"method": "POST", "url": "/api/orders"}],
                    "refresh_on_status": [401, 403, 419],
                    "confidence": "high",
                }
            ],
        },
    )

    result = asyncio.run(mcp_runtime.ApiMonitorMcpRuntime(server).call_tool("create_order", {}))

    assert result["success"] is True
    assert api_client.calls[0][0:2] == ("GET", "https://captured.example/api/login")
    assert api_client.calls[1][0:2] == ("POST", "https://captured.example/api/orders")


def test_token_flow_setup_extract_and_inject(monkeypatch):
    """Runtime executes setup request, extracts token, injects into target request."""
    setup_response = _ApiResponse(
        json_body={"csrfToken": "extracted-token-value"},
        headers={"content-type": "application/json"},
    )
    target_response = _ApiResponse(json_body={"created": True})

    call_count = 0

    class _SequencedClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                assert method == "GET"
                assert url == "https://api.example.test/api/session"
                return setup_response
            return target_response

    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: _SequencedClient(**kwargs))
    monkeypatch.setattr(
        mcp_runtime,
        "get_repository",
        lambda collection_name: _MemoryRepo([
            {
                "_id": "tool_1",
                "mcp_server_id": "mcp_api_monitor",
                "name": "create_order",
                "description": "Create order",
                "method": "POST",
                "url": "/api/orders",
                "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
                "body_mapping": {"name": "{{ name }}"},
                "validation_status": "valid",
            }
        ]),
    )

    class _Vault:
        async def resolve_credential_values(self, user_id, cred_id):
            return None

    monkeypatch.setattr(mcp_runtime, "get_vault", lambda: _Vault())

    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="user-1",
        name="CSRF API",
        transport="api_monitor",
        scope="user",
        url="https://api.example.test",
        api_monitor_auth={
            "credential_type": "placeholder",
            "credential_id": "",
            "token_flows": [
                {
                    "id": "flow_1",
                    "name": "csrf_token",
                    "setup": [
                        {
                            "method": "GET",
                            "url": "/api/session",
                            "extract": {"from": "response.body", "path": "$.csrfToken"},
                        }
                    ],
                    "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                    "applies_to": [{"method": "POST", "url": "/api/orders"}],
                    "refresh_on_status": [401, 403, 419],
                    "confidence": "high",
                }
            ],
        },
    )

    result = asyncio.run(mcp_runtime.ApiMonitorMcpRuntime(server).call_tool("create_order", {"name": "test"}))

    assert result["success"] is True
    assert call_count == 2
    # Verify token was injected into the target request (not leaked in preview)
    preview = result["request_preview"]
    assert preview["auth"]["token_flows"][0]["applied"] is True
    assert "extracted-token-value" not in str(result)


def test_token_flow_retries_on_auth_failure_status(monkeypatch):
    """Runtime refreshes token and retries when target returns 403."""
    setup_response = _ApiResponse(
        json_body={"csrfToken": "fresh-token"},
        headers={"content-type": "application/json"},
    )
    fail_response = _ApiResponse(
        status_code=403,
        json_body={"error": "csrf invalid"},
        text='{"error": "csrf invalid"}',
    )
    success_response = _ApiResponse(json_body={"ok": True})

    call_index = 0

    class _RetryClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, method, url, **kwargs):
            nonlocal call_index
            call_index += 1
            if call_index == 1:
                return setup_response
            if call_index == 2:
                return fail_response
            if call_index == 3:
                return setup_response  # Re-fetch token
            return success_response

    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: _RetryClient(**kwargs))
    monkeypatch.setattr(
        mcp_runtime,
        "get_repository",
        lambda collection_name: _MemoryRepo([
            {
                "_id": "tool_1",
                "mcp_server_id": "mcp_api_monitor",
                "name": "create_order",
                "description": "Create order",
                "method": "POST",
                "url": "/api/orders",
                "validation_status": "valid",
            }
        ]),
    )

    class _Vault:
        async def resolve_credential_values(self, user_id, cred_id):
            return None

    monkeypatch.setattr(mcp_runtime, "get_vault", lambda: _Vault())

    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="user-1",
        name="CSRF API",
        transport="api_monitor",
        scope="user",
        url="https://api.example.test",
        api_monitor_auth={
            "credential_type": "placeholder",
            "credential_id": "",
            "token_flows": [
                {
                    "id": "flow_1",
                    "name": "csrf_token",
                    "setup": [
                        {
                            "method": "GET",
                            "url": "/api/session",
                            "extract": {"from": "response.body", "path": "$.csrfToken"},
                        }
                    ],
                    "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                    "applies_to": [{"method": "POST", "url": "/api/orders"}],
                    "refresh_on_status": [401, 403, 419],
                    "confidence": "high",
                }
            ],
        },
    )

    result = asyncio.run(mcp_runtime.ApiMonitorMcpRuntime(server).call_tool("create_order", {}))

    assert result["success"] is True
    assert call_index == 4  # setup + fail + re-setup + success


def test_token_flow_no_retry_on_non_auth_failure(monkeypatch):
    """Runtime does NOT retry on 404 or other non-auth failures."""
    setup_response = _ApiResponse(
        json_body={"csrfToken": "token-val"},
        headers={"content-type": "application/json"},
    )
    not_found_response = _ApiResponse(
        status_code=404,
        json_body={"error": "not found"},
        text='{"error": "not found"}',
    )

    call_index = 0

    class _NoRetryClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, method, url, **kwargs):
            nonlocal call_index
            call_index += 1
            if call_index == 1:
                return setup_response
            return not_found_response

    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: _NoRetryClient(**kwargs))
    monkeypatch.setattr(
        mcp_runtime,
        "get_repository",
        lambda collection_name: _MemoryRepo([
            {
                "_id": "tool_1",
                "mcp_server_id": "mcp_api_monitor",
                "name": "get_order",
                "description": "Get order",
                "method": "GET",
                "url": "/api/orders/{{ id }}",
                "validation_status": "valid",
            }
        ]),
    )

    class _Vault:
        async def resolve_credential_values(self, user_id, cred_id):
            return None

    monkeypatch.setattr(mcp_runtime, "get_vault", lambda: _Vault())

    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="user-1",
        name="CSRF API",
        transport="api_monitor",
        scope="user",
        url="https://api.example.test",
        api_monitor_auth={
            "credential_type": "placeholder",
            "credential_id": "",
            "token_flows": [
                {
                    "id": "flow_1",
                    "name": "csrf_token",
                    "setup": [
                        {
                            "method": "GET",
                            "url": "/api/session",
                            "extract": {"from": "response.body", "path": "$.csrfToken"},
                        }
                    ],
                    "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                    "applies_to": [{"method": "GET", "url": "/api/orders/{{ id }}"}],
                    "refresh_on_status": [401, 403, 419],
                    "confidence": "high",
                }
            ],
        },
    )

    result = asyncio.run(mcp_runtime.ApiMonitorMcpRuntime(server).call_tool("get_order", {"id": "42"}))

    assert result["success"] is False
    assert result["status_code"] == 404
    assert call_index == 2  # setup + target, no retry


def test_token_flow_setup_failure_returns_error(monkeypatch):
    """Runtime returns structured error when setup request fails."""
    setup_fail = _ApiResponse(
        status_code=500,
        json_body={"error": "internal"},
        text='{"error": "internal"}',
    )

    class _SetupFailClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, method, url, **kwargs):
            return setup_fail

    monkeypatch.setattr(mcp_runtime.httpx, "AsyncClient", lambda **kwargs: _SetupFailClient(**kwargs))
    monkeypatch.setattr(
        mcp_runtime,
        "get_repository",
        lambda collection_name: _MemoryRepo([
            {
                "_id": "tool_1",
                "mcp_server_id": "mcp_api_monitor",
                "name": "create_order",
                "description": "Create order",
                "method": "POST",
                "url": "/api/orders",
                "validation_status": "valid",
            }
        ]),
    )

    class _Vault:
        async def resolve_credential_values(self, user_id, cred_id):
            return None

    monkeypatch.setattr(mcp_runtime, "get_vault", lambda: _Vault())

    server = McpServerDefinition(
        id="mcp_api_monitor",
        user_id="user-1",
        name="CSRF API",
        transport="api_monitor",
        scope="user",
        url="https://api.example.test",
        api_monitor_auth={
            "credential_type": "placeholder",
            "credential_id": "",
            "token_flows": [
                {
                    "id": "flow_1",
                    "name": "csrf_token",
                    "setup": [
                        {
                            "method": "GET",
                            "url": "/api/session",
                            "extract": {"from": "response.body", "path": "$.csrfToken"},
                        }
                    ],
                    "inject": {"headers": {"X-CSRF-Token": "{{ csrf_token }}"}},
                    "applies_to": [{"method": "POST", "url": "/api/orders"}],
                    "refresh_on_status": [401, 403, 419],
                    "confidence": "high",
                }
            ],
        },
    )

    result = asyncio.run(mcp_runtime.ApiMonitorMcpRuntime(server).call_tool("create_order", {}))

    assert result["success"] is False
    assert "Token flow 'csrf_token' setup got HTTP 500" in result.get("error", "")


# ── V2 runtime profile tests ─────────────────────────────────────────────


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
