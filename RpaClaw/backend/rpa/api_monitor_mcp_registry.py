from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from backend.rpa.api_monitor.models import ApiMonitorSession
from backend.rpa.api_monitor_mcp_contract import ApiMonitorToolContract, parse_api_monitor_tool_yaml
from backend.storage import get_repository


class ApiMonitorMcpRegistry:
    def __init__(self, server_repository=None, tool_repository=None) -> None:
        self._servers = server_repository or get_repository("user_mcp_servers")
        self._tools = tool_repository or get_repository("api_monitor_mcp_tools")

    async def find_by_name(self, *, user_id: str, mcp_name: str) -> dict[str, Any] | None:
        return await self._servers.find_one(
            {
                "user_id": user_id,
                "name": mcp_name,
                "source_type": "api_monitor",
            }
        )

    async def publish_session(
        self,
        *,
        session: ApiMonitorSession,
        user_id: str,
        mcp_name: str,
        description: str,
        overwrite: bool,
        existing_server_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now()
        selected_tools = [tool for tool in session.tool_definitions if getattr(tool, "selected", False)]
        server_id = existing_server_id or f"mcp_{uuid.uuid4().hex[:12]}"
        existing_server = None
        if existing_server_id:
            existing_server = await self._servers.find_one({"_id": server_id, "user_id": user_id})
        endpoint_config = (existing_server or {}).get("endpoint_config") or {}
        credential_binding = (existing_server or {}).get("credential_binding") or {}
        server_doc = {
            "user_id": user_id,
            "name": mcp_name,
            "description": description,
            "transport": "api_monitor",
            "enabled": True,
            "default_enabled": True,
            "source_type": "api_monitor",
            "endpoint_config": endpoint_config,
            "credential_binding": credential_binding,
            "tool_policy": (existing_server or {}).get("tool_policy") or {},
            "tool_count": len(selected_tools),
            "updated_at": now,
        }
        await self._servers.update_one(
            {"_id": server_id, "user_id": user_id},
            {
                "$set": server_doc,
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )
        await self.replace_tools(
            mcp_server_id=server_id,
            user_id=user_id,
            base_url=_origin_from_url(session.target_url or ""),
            session_tools=[tool.model_dump(mode="python") for tool in selected_tools],
        )
        return {
            "saved": True,
            "server_id": server_id,
            "server": {"_id": server_id, **server_doc},
            "tool_count": len(selected_tools),
            "overwritten": overwrite,
        }

    async def replace_tools(
        self,
        *,
        mcp_server_id: str,
        user_id: str,
        session_tools: list[dict[str, Any]],
        base_url: str = "",
    ) -> None:
        now = datetime.now()
        contract_docs = _parse_tools_with_duplicate_validation(session_tools)
        tool_docs: list[dict[str, Any]] = []
        for index, (tool, contract) in enumerate(zip(session_tools, contract_docs, strict=True)):
            tool_id = f"{mcp_server_id}_{index}_{uuid.uuid4().hex[:8]}"
            tool_docs.append(
                {
                    "_id": tool_id,
                    "user_id": user_id,
                    "mcp_server_id": mcp_server_id,
                    "source": "api_monitor",
                    "source_session_id": tool.get("session_id"),
                    "order": index,
                    "base_url": base_url,
                    "url_pattern": tool.get("url_pattern"),
                    "headers_schema": tool.get("headers_schema"),
                    "request_body_schema": tool.get("request_body_schema"),
                    "response_body_schema": tool.get("response_body_schema"),
                    "trigger_locator": tool.get("trigger_locator"),
                    "source_calls": tool.get("source_calls", []),
                    "created_at": tool.get("created_at") or now,
                    "updated_at": now,
                    **contract.to_document(),
                }
            )
        await self._tools.delete_many({"mcp_server_id": mcp_server_id, "user_id": user_id})
        for tool_doc in tool_docs:
            await self._tools.insert_one(tool_doc)

    async def list_tools_for_server(self, *, mcp_server_id: str, user_id: str) -> list[dict[str, Any]]:
        return await self._tools.find_many(
            {"mcp_server_id": mcp_server_id, "user_id": user_id},
            sort=[("updated_at", -1)],
        )

    async def delete_tools_for_server(self, *, mcp_server_id: str, user_id: str) -> int:
        return await self._tools.delete_many({"mcp_server_id": mcp_server_id, "user_id": user_id})


def _origin_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_tools_with_duplicate_validation(tools: list[Any]) -> list[ApiMonitorToolContract]:
    parsed = [
        parse_api_monitor_tool_yaml(str(_tool_value(tool, "yaml_definition", "") or ""))
        for tool in tools
    ]
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
                    raw_definition=contract.raw_definition,
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


def _tool_value(tool: Any, key: str, default: Any = None) -> Any:
    if isinstance(tool, dict):
        return tool.get(key, default)
    return getattr(tool, key, default)
