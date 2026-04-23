from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from backend.config import settings
from backend.mcp.models import McpServerDefinition
from backend.rpa.api_monitor_mcp_contract import (
    render_mapping,
    render_template_value,
    sanitize_headers,
)
from backend.storage import get_repository


@dataclass(frozen=True)
class McpToolDefinition:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class McpRuntime(Protocol):
    async def list_tools(self) -> Sequence[McpToolDefinition | Mapping[str, Any]]: ...

    async def call_tool(self, tool_name: str, arguments: Mapping[str, Any]) -> Any: ...


class McpRuntimeFactory(Protocol):
    def create_runtime(self, server: McpServerDefinition) -> McpRuntime: ...


class UnsupportedMcpRuntimeFactory:
    def create_runtime(self, server: McpServerDefinition) -> McpRuntime:
        raise RuntimeError(
            f"No MCP runtime factory is configured for server '{server.id}' "
            f"(transport={server.transport})"
        )


def _is_local_storage_backend() -> bool:
    return (settings.storage_backend or "").strip().lower() == "local"


def _timeout_seconds(server: McpServerDefinition) -> float:
    return max(server.timeout_ms / 1000.0, 0.001)


def _sse_read_timeout_seconds() -> float:
    return 60.0 * 5.0


def _normalize_server_headers(headers: Mapping[str, str] | None) -> dict[str, str] | None:
    if not headers:
        return None
    return dict(headers)


def _normalize_mcp_result(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="python", exclude_none=True)
    if isinstance(result, Mapping):
        return dict(result)

    payload: dict[str, Any] = {}
    if hasattr(result, "content"):
        payload["content"] = list(getattr(result, "content") or [])
    if hasattr(result, "structuredContent"):
        structured = getattr(result, "structuredContent")
        if structured is not None:
            payload["structuredContent"] = structured
    if hasattr(result, "isError"):
        payload["isError"] = bool(getattr(result, "isError"))
    return payload or result


def _normalize_tool(tool: Any) -> McpToolDefinition:
    if isinstance(tool, McpToolDefinition):
        return tool

    if isinstance(tool, Mapping):
        name = str(tool.get("name", "")).strip()
        description = str(tool.get("description", "") or "")
        input_schema = tool.get("input_schema") or tool.get("inputSchema") or {}
    else:
        name = str(getattr(tool, "name", "")).strip()
        description = str(getattr(tool, "description", "") or "")
        input_schema = getattr(tool, "inputSchema", {}) or {}

    if not isinstance(input_schema, dict):
        input_schema = {}

    return McpToolDefinition(name=name, description=description, input_schema=input_schema)


def _page_tools(page: Any) -> Sequence[Any]:
    if isinstance(page, Mapping):
        tools = page.get("tools") or []
    else:
        tools = getattr(page, "tools", []) or []
    return list(tools)


def _page_next_cursor(page: Any) -> str | None:
    if isinstance(page, Mapping):
        cursor = page.get("nextCursor")
    else:
        cursor = getattr(page, "nextCursor", None)
    if cursor is None:
        return None
    cursor_text = str(cursor).strip()
    return cursor_text or None


class McpSdkRuntime:
    def __init__(self, server: McpServerDefinition) -> None:
        self._server = server

    def _validate(self) -> None:
        if self._server.transport == "stdio" and not _is_local_storage_backend():
            raise ValueError("stdio MCP is only allowed in local mode")

    def _stdio_server_parameters(self) -> StdioServerParameters:
        if not self._server.command.strip():
            raise ValueError(f"stdio MCP server '{self._server.id}' requires a command")

        return StdioServerParameters(
            command=self._server.command,
            args=list(self._server.args),
            env=dict(self._server.env) if self._server.env else None,
            cwd=self._server.cwd or None,
        )

    @asynccontextmanager
    async def _open_transport(self):
        self._validate()
        timeout = _timeout_seconds(self._server)

        if self._server.transport == "stdio":
            params = self._stdio_server_parameters()
            async with stdio_client(params) as streams:
                yield streams
            return

        if self._server.transport == "sse":
            async with sse_client(
                self._server.url,
                headers=_normalize_server_headers(self._server.headers),
                timeout=timeout,
                sse_read_timeout=_sse_read_timeout_seconds(),
            ) as streams:
                yield streams
            return

        if self._server.transport == "streamable_http":
            http_client_kwargs: dict[str, Any] = {"timeout": timeout}
            normalized_headers = _normalize_server_headers(self._server.headers)
            if normalized_headers:
                http_client_kwargs["headers"] = normalized_headers
            async with httpx.AsyncClient(**http_client_kwargs) as http_client:
                async with streamable_http_client(
                    self._server.url,
                    http_client=http_client,
                ) as streams:
                    yield streams
            return

        raise ValueError(f"Unsupported MCP transport: {self._server.transport}")

    @asynccontextmanager
    async def _session(self):
        async with self._open_transport() as streams:
            read_stream, write_stream = streams[:2]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    async def list_tools(self) -> Sequence[McpToolDefinition | Mapping[str, Any]]:
        discovered_tools: list[McpToolDefinition] = []
        cursor: str | None = None

        async with self._session() as session:
            while True:
                page = await session.list_tools(cursor=cursor)
                discovered_tools.extend(_normalize_tool(tool) for tool in _page_tools(page))
                cursor = _page_next_cursor(page)
                if cursor is None:
                    break

        return discovered_tools

    async def call_tool(self, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        async with self._session() as session:
            result = await session.call_tool(tool_name, arguments=dict(arguments))
        return _normalize_mcp_result(result)


class ApiMonitorMcpRuntime:
    def __init__(self, server: McpServerDefinition) -> None:
        self._server = server
        self._tools = get_repository("api_monitor_mcp_tools")

    async def list_tools(self) -> Sequence[McpToolDefinition | Mapping[str, Any]]:
        docs = await self._tools.find_many({"mcp_server_id": self._server.id, "validation_status": "valid"})
        return [
            McpToolDefinition(
                name=str(doc.get("name", "")),
                description=str(doc.get("description", "") or ""),
                input_schema=_api_monitor_tool_input_schema(doc),
            )
            for doc in docs
            if str(doc.get("name", "")).strip()
        ]

    async def call_tool(self, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        docs = await self._tools.find_many(
            {"mcp_server_id": self._server.id, "name": tool_name, "validation_status": "valid"}
        )
        doc = next((item for item in docs if _api_monitor_tool_is_valid(item)), None)
        if not doc:
            return {"success": False, "error": f"API Monitor tool '{tool_name}' not found"}

        method = str(doc.get("method") or "GET").upper()
        rendered_arguments = dict(arguments)
        url = _build_api_monitor_url(
            _api_monitor_base_url(self._server),
            _api_monitor_tool_url(doc),
            rendered_arguments,
        )
        if not url:
            return {"success": False, "error": f"API Monitor tool '{tool_name}' has no callable URL"}

        request_query = _api_monitor_base_query(self._server)
        request_query.update(render_mapping(doc.get("query_mapping"), rendered_arguments))
        request_headers: dict[str, Any] = dict(self._server.headers)
        request_headers.update(render_mapping(doc.get("header_mapping"), rendered_arguments))
        request_body = render_mapping(doc.get("body_mapping"), rendered_arguments)
        json_body = request_body or None

        request_kwargs: dict[str, Any] = {
            "params": request_query,
            "headers": request_headers,
        }
        if json_body is not None:
            request_kwargs["json"] = json_body

        async with httpx.AsyncClient(timeout=_api_monitor_timeout_seconds(self._server)) as client:
            response = await client.request(method, url, **request_kwargs)

        content_type = response.headers.get("content-type", "")
        try:
            body: Any = response.json() if "json" in content_type else response.text
        except ValueError:
            body = response.text
        return {
            "success": response.is_success,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": body,
            "request_preview": {
                "method": method,
                "url": url,
                "query": request_query,
                "headers": sanitize_headers(request_headers),
                "body": json_body,
            },
        }


class McpSdkRuntimeFactory:
    def create_runtime(self, server: McpServerDefinition) -> McpRuntime:
        if server.transport == "api_monitor":
            return ApiMonitorMcpRuntime(server)
        if server.transport == "stdio" and not _is_local_storage_backend():
            raise ValueError("stdio MCP is only allowed in local mode")
        return McpSdkRuntime(server)


def _path_parameter_names(url_pattern: str) -> set[str]:
    names: set[str] = set()
    parts = url_pattern.split("{")
    for part in parts[1:]:
        name = part.split("}", 1)[0].strip()
        if name:
            names.add(name)
    return names


def _build_api_monitor_url(base_url: str, url_pattern: str, arguments: Mapping[str, Any]) -> str:
    rendered = str(render_template_value(url_pattern, dict(arguments)) or "")
    for key in _path_parameter_names(url_pattern):
        if key in arguments:
            rendered = rendered.replace("{" + key + "}", str(arguments[key]))
    if rendered.startswith(("http://", "https://")):
        return rendered
    if not base_url:
        return ""
    return urljoin(base_url.rstrip("/") + "/", rendered if rendered.startswith("/") else rendered.lstrip("/"))


def _api_monitor_tool_is_valid(doc: Mapping[str, Any]) -> bool:
    return doc.get("validation_status") == "valid"


def _api_monitor_tool_input_schema(doc: Mapping[str, Any]) -> dict[str, Any]:
    input_schema = doc.get("input_schema")
    if isinstance(input_schema, dict):
        return input_schema
    return {"type": "object", "properties": {}}


def _api_monitor_tool_url(doc: Mapping[str, Any]) -> str:
    url = str(doc.get("url") or "")
    if url:
        return url
    return str(doc.get("url_pattern") or "")


def _api_monitor_base_url(server: McpServerDefinition) -> str:
    parts = urlsplit(server.url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


def _api_monitor_base_query(server: McpServerDefinition) -> dict[str, Any]:
    return dict(parse_qsl(urlsplit(server.url).query, keep_blank_values=True))


def _api_monitor_timeout_seconds(server: McpServerDefinition) -> float:
    timeout_ms = server.timeout_ms
    if timeout_ms == 20000:
        timeout_ms = 30000
    return max(timeout_ms / 1000.0, 0.001)


def coerce_mcp_tool_definition(tool: McpToolDefinition | Mapping[str, Any]) -> McpToolDefinition:
    if isinstance(tool, McpToolDefinition):
        return tool

    if isinstance(tool, Mapping):
        name = str(tool.get("name", "")).strip()
        description = str(tool.get("description", "") or "")
        input_schema = tool.get("input_schema") or tool.get("inputSchema") or {}
    else:
        name = str(getattr(tool, "name", "")).strip()
        description = str(getattr(tool, "description", "") or "")
        input_schema = getattr(tool, "inputSchema", {}) or {}
    if not isinstance(input_schema, dict):
        input_schema = {}

    return McpToolDefinition(
        name=name,
        description=description,
        input_schema=input_schema,
    )
