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

import logging
logger = logging.getLogger(__name__)
from backend.rpa.api_monitor_mcp_contract import (
    render_mapping,
    render_template_value,
    sanitize_headers,
    sanitize_preview_mapping,
    sanitize_preview_url,
)
from backend.rpa.api_monitor_auth import apply_api_monitor_auth_to_request, apply_api_monitor_auth_to_profile
from backend.rpa.api_monitor_runtime_profile import ApiMonitorRuntimeProfile, ApiMonitorRuntimeProfileError
from backend.credential.vault import get_vault
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

        # Resolve mappings: use stored mappings if present, otherwise auto-derive from input_schema.
        query_mapping = doc.get("query_mapping") or {}
        body_mapping = doc.get("body_mapping") or {}
        header_mapping = doc.get("header_mapping") or {}
        path_mapping = doc.get("path_mapping") or {}
        has_explicit_mapping = query_mapping or body_mapping or header_mapping or path_mapping

        if not has_explicit_mapping:
            query_mapping, body_mapping, header_mapping, path_mapping = _auto_derive_mappings(
                method, doc.get("input_schema") or {},
            )

        request_base_url = _api_monitor_request_base_url(self._server, doc)
        url = _build_api_monitor_url(
            request_base_url,
            _api_monitor_tool_url(doc),
            rendered_arguments,
        )
        if not url:
            return {"success": False, "error": f"API Monitor tool '{tool_name}' has no callable URL"}

        has_api_monitor_auth = bool(self._server.api_monitor_auth)
        token_flows_config = (self._server.api_monitor_auth or {}).get("token_flows", [])
        is_v2_flows = bool(token_flows_config) and any(
            "producer" in f and "consumers" in f for f in token_flows_config
        )

        if is_v2_flows:
            return await self._call_tool_v2(
                doc=doc, method=method, url=url, rendered_arguments=rendered_arguments,
                request_base_url=request_base_url, query_mapping=query_mapping,
                body_mapping=body_mapping, header_mapping=header_mapping,
            )

        # V1 legacy path
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

        # V1 Token flow: setup/extract/inject with retry
        matching_flows = _matching_token_flows(token_flows_config, method, url, doc)

        async with httpx.AsyncClient(timeout=_api_monitor_timeout_seconds(self._server)) as client:
            token_previews: list[dict[str, Any]] = []
            token_cache: dict[str, str] = {}
            for flow in matching_flows:
                token_name = flow.get("name", "")
                flow_id = flow.get("id", "")
                inject_config = flow.get("inject", {})
                extracted_value, setup_error = await _execute_token_flow_setup(
                    client, flow, self._server,
                    base_url=request_base_url,
                    auth_headers=request_headers,
                    auth_query=request_query,
                )
                if extracted_value is None:
                    return {"success": False, "error": setup_error}
                token_cache[token_name] = extracted_value
                _inject_token_values(inject_config, token_cache, request_headers, request_query)
                token_previews.append({
                    "name": token_name,
                    "id": flow_id,
                    "applied": True,
                    "source": _token_flow_source_summary(flow),
                    "injected": list(inject_config.get("headers", {}).keys()),
                })

            request_kwargs: dict[str, Any] = {"params": request_query, "headers": request_headers}
            if json_body is not None:
                request_kwargs["json"] = json_body
            response = await client.request(method, url, **request_kwargs)

            if matching_flows and response.status_code in _all_refresh_statuses(matching_flows):
                token_cache.clear()
                for flow in matching_flows:
                    token_name = flow.get("name", "")
                    extracted_value, _ = await _execute_token_flow_setup(
                        client, flow, self._server,
                        base_url=request_base_url,
                        auth_headers=request_headers,
                        auth_query=request_query,
                    )
                    if extracted_value is not None:
                        token_cache[token_name] = extracted_value
                        _inject_token_values(
                            flow.get("inject", {}), token_cache, request_headers, request_query
                        )
                request_kwargs["params"] = request_query
                request_kwargs["headers"] = request_headers
                response = await client.request(method, url, **request_kwargs)

        content_type = response.headers.get("content-type", "")
        try:
            body: Any = response.json() if "json" in content_type else response.text
        except ValueError:
            body = response.text

        auth_preview = dict(auth_application.preview) if auth_application.preview else {}
        if token_previews:
            auth_preview["token_flows"] = token_previews

        return {
            "success": response.is_success,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": body,
            "request_preview": {
                "method": method,
                "url": sanitize_preview_url(url, url_template=_api_monitor_tool_url(doc), arguments=rendered_arguments),
                "query": sanitize_preview_mapping(request_query),
                "headers": sanitize_headers(request_headers),
                "body": sanitize_preview_mapping(json_body) if json_body is not None else None,
                "auth": auth_preview,
            },
        }

    async def _call_tool_v2(
        self,
        *,
        doc: Mapping[str, Any],
        method: str,
        url: str,
        rendered_arguments: dict[str, Any],
        request_base_url: str,
        query_mapping: dict[str, Any],
        body_mapping: dict[str, Any],
        header_mapping: dict[str, Any],
    ) -> dict[str, Any]:
        profile = ApiMonitorRuntimeProfile(base_url=request_base_url)

        def _build_target_request_parts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
            target_headers = {**profile.headers, **render_mapping(header_mapping, rendered_arguments)}
            target_query = render_mapping(query_mapping, rendered_arguments)
            target_body = render_mapping(body_mapping, rendered_arguments)
            return target_headers, target_query, target_body

        async with httpx.AsyncClient(timeout=_api_monitor_timeout_seconds(self._server)) as client:
            # Step 1: Auth -> profile
            auth_application = await apply_api_monitor_auth_to_profile(
                user_id=self._server.user_id,
                auth_config=self._server.api_monitor_auth,
                profile=profile,
                client=client,
                vault=get_vault(),
            )
            if auth_application.error:
                return {"success": False, "error": auth_application.error}

            # Step 2: Match V2 token flows
            token_flows_config = (self._server.api_monitor_auth or {}).get("token_flows", [])
            tool_url = _api_monitor_tool_url(doc)
            matching_flows = _matching_v2_token_flows(token_flows_config, method, tool_url, url)

            # Step 3: Execute producers
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

            # Step 4: Build target request and apply consumers
            request_headers, request_query, request_body = _build_target_request_parts()
            token_previews = _apply_v2_token_consumers(
                profile=profile,
                flows=matching_flows,
                method=method,
                tool_url=tool_url,
                absolute_url=url,
                headers=request_headers,
                query=request_query,
                body=request_body,
            )
            consumer_error = next((p.get("error") for p in token_previews if p.get("error")), "")
            if consumer_error:
                return {"success": False, "error": consumer_error}

            # Step 5: Send target request
            request_kwargs: dict[str, Any] = {"params": request_query, "headers": request_headers}
            if request_body:
                request_kwargs["json"] = request_body
            response = await client.request(method, url, **request_kwargs)

            # Step 6: Refresh on auth failure
            refresh_statuses = _v2_refresh_statuses(matching_flows)
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
                    tool_url=tool_url,
                    absolute_url=url,
                    headers=request_headers,
                    query=request_query,
                    body=request_body,
                )
                for p in token_previews:
                    p["refreshed"] = True
                consumer_error = next((p.get("error") for p in token_previews if p.get("error")), "")
                if consumer_error:
                    return {"success": False, "error": consumer_error}
                request_kwargs = {"params": request_query, "headers": request_headers}
                if request_body:
                    request_kwargs["json"] = request_body
                response = await client.request(method, url, **request_kwargs)

        content_type = response.headers.get("content-type", "")
        try:
            body: Any = response.json() if "json" in content_type else response.text
        except ValueError:
            body = response.text

        auth_preview = dict(auth_application.preview) if auth_application.preview else {}
        auth_preview["profile"] = profile.preview()
        auth_preview["token_flows"] = token_previews

        return {
            "success": response.is_success,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": body,
            "request_preview": {
                "method": method,
                "url": sanitize_preview_url(url, url_template=_api_monitor_tool_url(doc), arguments=rendered_arguments),
                "query": sanitize_preview_mapping(request_query),
                "headers": sanitize_headers(request_headers),
                "body": sanitize_preview_mapping(request_body) if request_body else None,
                "auth": auth_preview,
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
    legacy_schema = doc.get("request_body_schema")
    if isinstance(legacy_schema, dict):
        return legacy_schema
    return {"type": "object", "properties": {}}


def _api_monitor_tool_url(doc: Mapping[str, Any]) -> str:
    url = str(doc.get("url") or "")
    if url:
        return url
    return str(doc.get("url_pattern") or "")


def _api_monitor_base_url(server: McpServerDefinition) -> str:
    parts = urlsplit(server.url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))


def _api_monitor_request_base_url(server: McpServerDefinition, doc: Mapping[str, Any]) -> str:
    base_url = _api_monitor_base_url(server)
    if base_url:
        return base_url
    return str(doc.get("base_url") or "")


def _auto_derive_mappings(
    method: str,
    input_schema: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Auto-derive query/body/header/path mappings from input_schema parameter 'in' annotations."""
    properties = input_schema.get("properties") if isinstance(input_schema, dict) else None
    if not isinstance(properties, dict):
        return {}, {}, {}, {}

    auto_query: dict[str, Any] = {}
    auto_body: dict[str, Any] = {}
    auto_header: dict[str, Any] = {}
    auto_path: dict[str, Any] = {}

    for prop_name, prop_value in properties.items():
        if not isinstance(prop_value, dict):
            continue
        location = str(prop_value.get("in", "")).lower().strip()
        template = "{{" + prop_name + "}}"
        if location == "path":
            auto_path[prop_name] = template
        elif location == "query":
            auto_query[prop_name] = template
        elif location == "body":
            auto_body[prop_name] = template
        elif location == "header":
            auto_header[prop_name] = template
        else:
            # Default: query for GET/DELETE, body for POST/PUT/PATCH
            if method in ("POST", "PUT", "PATCH"):
                auto_body[prop_name] = template
            else:
                auto_query[prop_name] = template

    return auto_query, auto_body, auto_header, auto_path


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


# ── Token flow runtime helpers ──────────────────────────────────────────


# ── V2 helpers ──────────────────────────────────────────────────────────


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


def _matching_v2_token_flows(
    token_flows: list[dict[str, Any]], method: str, tool_url: str, absolute_url: str
) -> list[dict[str, Any]]:
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


def _v2_refresh_statuses(flows: list[dict[str, Any]]) -> set[int]:
    statuses: set[int] = set()
    for flow in flows:
        for code in flow.get("refresh_on_status") or [401, 403, 419]:
            if isinstance(code, int):
                statuses.add(code)
    return statuses


# ── V1 legacy helpers ───────────────────────────────────────────────────


def _matching_token_flows(
    token_flows: list[dict[str, Any]],
    method: str,
    url: str,
    doc: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return token flows that apply to the given tool request."""
    if not token_flows:
        return []
    tool_url = str(doc.get("url") or doc.get("url_pattern") or "")
    results: list[dict[str, Any]] = []
    for flow in token_flows:
        applies_to = flow.get("applies_to", [])
        if not applies_to:
            results.append(flow)
            continue
        for target in applies_to:
            target_method = str(target.get("method", "")).upper()
            target_url = str(target.get("url", ""))
            if target_method == method and (
                target_url == tool_url or url.endswith(target_url) or tool_url.endswith(target_url)
            ):
                results.append(flow)
                break
    return results


async def _execute_token_flow_setup(
    client: httpx.AsyncClient,
    flow: dict[str, Any],
    server: McpServerDefinition,
    *,
    base_url: str = "",
    auth_headers: dict[str, Any] | None = None,
    auth_query: dict[str, Any] | None = None,
) -> tuple[str | None, str]:
    """Execute setup requests for a token flow and return (extracted_value, error).

    On success returns (value, "").  On failure returns (None, error_message).
    """
    flow_name = flow.get("name", "unknown")
    setup_steps = flow.get("setup", [])
    if not setup_steps:
        return None, f"Token flow '{flow_name}' has no setup steps"

    base_url = base_url or _api_monitor_base_url(server)
    last_value: str | None = None
    setup_headers: dict[str, str] = dict(auth_headers) if auth_headers else {}
    setup_params: dict[str, str] = dict(auth_query) if auth_query else {}

    for step in setup_steps:
        setup_method = str(step.get("method", "GET")).upper()
        setup_url = str(step.get("url", ""))
        extract_config = step.get("extract", {})

        if not setup_url.startswith(("http://", "https://")):
            if base_url:
                setup_url = urljoin(base_url.rstrip("/") + "/", setup_url.lstrip("/"))
            else:
                return None, f"Token flow '{flow_name}' setup URL is relative but no base URL configured: {setup_url}"

        logger.info(
            "[TokenFlow] setup request: %s %s (headers=%s)",
            setup_method, setup_url, list(setup_headers.keys()),
        )
        try:
            setup_response = await client.request(
                setup_method, setup_url,
                headers=setup_headers,
                params=setup_params,
            )
        except httpx.HTTPError as exc:
            return None, f"Token flow '{flow_name}' setup HTTP error: {exc}"

        logger.info(
            "[TokenFlow] setup response: status=%d content-type=%s",
            setup_response.status_code,
            setup_response.headers.get("content-type", ""),
        )
        if not setup_response.is_success:
            return None, (
                f"Token flow '{flow_name}' setup got HTTP {setup_response.status_code} "
                f"from {setup_method} {_token_safe_url(setup_url)}"
            )

        last_value = _extract_token_from_response(setup_response, extract_config)
        if last_value is None:
            return None, (
                f"Token flow '{flow_name}' could not extract token from "
                f"{extract_config.get('from', '?')} path={extract_config.get('path', '?')}"
            )

    return last_value, ""


def _extract_token_from_response(
    response: httpx.Response,
    extract_config: dict[str, Any],
) -> str | None:
    """Extract a token value from a response using the extract configuration."""
    source = extract_config.get("from", "")
    path = extract_config.get("path", "")

    if source == "response.body":
        try:
            data = response.json()
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict) or not path:
            return None
        return _resolve_json_path(data, path)

    if source == "response.headers":
        return response.headers.get(path)

    if source in ("cookie", "set-cookie"):
        set_cookie = response.headers.get("set-cookie", "")
        for cookie_name, cookie_value in _parse_set_cookie_header(set_cookie):
            if cookie_name == path:
                return cookie_value
        return None

    return None


def _resolve_json_path(data: dict[str, Any], path: str) -> str | None:
    """Resolve a simple JSON path like $.csrfToken or $.data.nonce."""
    if not path.startswith("$."):
        return None
    parts = path[2:].split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return str(current) if current is not None else None


def _inject_token_values(
    inject_config: dict[str, Any],
    token_cache: dict[str, str],
    headers: dict[str, Any],
    query: dict[str, Any],
) -> None:
    """Inject cached token values into request headers and query params."""
    for target_name, template in inject_config.get("headers", {}).items():
        value = _render_token_template(template, token_cache)
        if value is not None:
            headers[target_name] = value
    for target_name, template in inject_config.get("query", {}).items():
        value = _render_token_template(template, token_cache)
        if value is not None:
            query[target_name] = value


def _render_token_template(template: str, token_cache: dict[str, str]) -> str | None:
    """Render a template like '{{ csrf_token }}' using cached token values."""
    import re as _re
    match = _re.match(r"^\{\{\s*(\w+)\s*\}\}$", template.strip())
    if match:
        token_name = match.group(1)
        return token_cache.get(token_name)
    return None


def _token_flow_source_summary(flow: dict[str, Any]) -> str:
    """Build a masked summary of a token flow's source."""
    summary = flow.get("summary", {})
    if summary:
        return summary.get("producer", "")
    setup_steps = flow.get("setup", [])
    if setup_steps:
        step = setup_steps[0]
        return f"{step.get('method', 'GET')} {step.get('url', '')} {step.get('extract', {}).get('from', '')}"
    return ""


def _all_refresh_statuses(flows: list[dict[str, Any]]) -> set[int]:
    """Collect all refresh-on-status codes from matching flows."""
    statuses: set[int] = set()
    for flow in flows:
        for code in flow.get("refresh_on_status", [401, 403, 419]):
            statuses.add(code)
    return statuses


def _token_safe_url(url: str) -> str:
    """Mask query parameters for safe logging."""
    parsed = urlsplit(url)
    if parsed.query:
        return url[:url.index("?")] + "?..."
    return url


def _parse_set_cookie_header(header_value: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for part in header_value.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        name_value = part.split(";")[0].strip()
        eq_idx = name_value.index("=")
        name = name_value[:eq_idx].strip()
        value = name_value[eq_idx + 1:].strip()
        if name and value:
            results.append((name, value))
    return results
