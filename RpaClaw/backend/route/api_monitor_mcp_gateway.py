from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from backend.deepagent.mcp_runtime import ApiMonitorMcpRuntime
from backend.mcp.models import McpServerDefinition
from backend.rpa.api_monitor_external_access import (
    CallerAuthError,
    build_caller_auth_requirements,
    build_external_tool_input_schema,
    extract_caller_auth_profile,
    verify_external_access_token,
    with_caller_auth_description,
)
from backend.storage import get_repository

router = APIRouter(tags=["api-monitor-mcp-gateway"])


def _is_json_rpc_request(body: Mapping[str, Any]) -> bool:
    return body.get("jsonrpc") == "2.0"


def _json_rpc_result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _json_rpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _tool_result_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        "structuredContent": result,
        "isError": not bool(result.get("success", True)),
    }


def _extract_external_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("X-RpaClaw-MCP-Token", "").strip()


def _is_api_monitor_mcp(doc: Mapping[str, Any]) -> bool:
    return doc.get("source_type") == "api_monitor" or doc.get("transport") == "api_monitor"


async def _load_external_server_doc(server_id: str, request: Request) -> tuple[dict[str, Any] | None, int, str]:
    repo = get_repository("user_mcp_servers")
    doc = await repo.find_one({"_id": server_id})
    if not doc or not _is_api_monitor_mcp(doc):
        return None, -32602, "API Monitor MCP not found"
    external_access = doc.get("external_access") if isinstance(doc.get("external_access"), dict) else {}
    if not external_access.get("enabled"):
        return None, -32001, "External access is disabled"
    token = _extract_external_token(request)
    if not verify_external_access_token(token, str(external_access.get("access_token_hash") or "")):
        return None, -32002, "Invalid external access token"
    return dict(doc), 0, ""


def _server_definition(server_doc: Mapping[str, Any]) -> McpServerDefinition:
    endpoint = server_doc.get("endpoint_config") if isinstance(server_doc.get("endpoint_config"), dict) else {}
    return McpServerDefinition(
        id=str(server_doc["_id"]),
        user_id=str(server_doc.get("user_id") or ""),
        name=str(server_doc.get("name") or "API Monitor MCP"),
        description=str(server_doc.get("description") or ""),
        transport="api_monitor",
        scope="user",
        enabled=bool(server_doc.get("enabled", True)),
        default_enabled=bool(server_doc.get("default_enabled", False)),
        url=str(endpoint.get("url") or endpoint.get("base_url") or ""),
        headers=dict(endpoint.get("headers") or {}),
        timeout_ms=int(endpoint.get("timeout_ms") or 20000),
        api_monitor_auth=dict(server_doc.get("api_monitor_auth") or {}),
    )


async def _load_tool_docs(server_doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    repo = get_repository("api_monitor_mcp_tools")
    docs = await repo.find_many(
        {
            "mcp_server_id": str(server_doc["_id"]),
            "user_id": str(server_doc.get("user_id") or ""),
        },
        sort=[("order", 1)],
    )
    return [
        dict(doc)
        for doc in docs
        if doc.get("validation_status") == "valid" and str(doc.get("name") or "").strip()
    ]


def _tool_descriptor(doc: Mapping[str, Any], server_doc: Mapping[str, Any]) -> dict[str, Any]:
    requirements = build_caller_auth_requirements(server_doc.get("api_monitor_auth") or {})
    description, extension = with_caller_auth_description(str(doc.get("description") or ""), requirements)
    schema = build_external_tool_input_schema(doc.get("input_schema") or {"type": "object", "properties": {}}, requirements)
    return {
        "name": str(doc.get("name") or ""),
        "description": description,
        "inputSchema": schema,
        "input_schema": schema,
        **extension,
    }


def _initialize_result(server_doc: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": str(server_doc.get("name") or "API Monitor MCP"), "version": "0.1.0"},
        "instructions": (
            "Provides externally callable API Monitor MCP tools. "
            "Target API credentials are supplied by the caller per tool contract."
        ),
    }


async def _mark_last_used(server_id: str) -> None:
    repo = get_repository("user_mcp_servers")
    await repo.update_one(
        {"_id": server_id},
        {"$set": {"external_access.last_used_at": datetime.now()}},
    )


@router.post("/api-monitor-mcp/{server_id}/mcp", response_model=None)
async def api_monitor_mcp_gateway(
    server_id: str,
    body: dict[str, Any],
    request: Request,
) -> dict[str, Any] | JSONResponse | Response:
    method = body.get("method")
    params = body.get("params") or {}
    request_id = body.get("id")
    is_json_rpc = _is_json_rpc_request(body)

    server_doc, auth_error_code, auth_error_message = await _load_external_server_doc(server_id, request)
    if auth_error_code:
        return _json_rpc_error(request_id, auth_error_code, auth_error_message)
    assert server_doc is not None

    if is_json_rpc and method == "initialize":
        await _mark_last_used(server_id)
        return _json_rpc_result(request_id, _initialize_result(server_doc))
    if is_json_rpc and method == "notifications/initialized":
        return Response(status_code=202)
    if is_json_rpc and method == "ping":
        await _mark_last_used(server_id)
        return _json_rpc_result(request_id, {})
    if method == "tools/list":
        await _mark_last_used(server_id)
        result = {"tools": [_tool_descriptor(doc, server_doc) for doc in await _load_tool_docs(server_doc)]}
        return _json_rpc_result(request_id, result) if is_json_rpc else {"result": result}
    if method == "tools/call":
        tool_name = str(params.get("name") or "").strip()
        docs = await _load_tool_docs(server_doc)
        if not any(str(doc.get("name") or "") == tool_name for doc in docs):
            return _json_rpc_error(request_id, -32602, "API Monitor tool not found")
        requirements = build_caller_auth_requirements(server_doc.get("api_monitor_auth") or {})
        try:
            cleaned_arguments, caller_profile, caller_preview = extract_caller_auth_profile(
                dict(params.get("arguments") or {}),
                requirements=requirements,
                request_headers=request.headers,
            )
        except CallerAuthError as exc:
            return _json_rpc_result(
                request_id,
                _tool_result_payload({"success": False, "error": str(exc)}),
            )
        result = await ApiMonitorMcpRuntime(
            _server_definition(server_doc),
            caller_only=True,
            caller_profile=caller_profile,
            caller_auth_preview=caller_preview,
        ).call_tool(tool_name, cleaned_arguments)
        await _mark_last_used(server_id)
        return _json_rpc_result(request_id, _tool_result_payload(result))
    if is_json_rpc:
        return _json_rpc_error(request_id, -32601, "Unsupported MCP method")
    raise HTTPException(status_code=400, detail="Unsupported MCP method")
