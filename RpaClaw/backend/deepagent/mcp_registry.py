from __future__ import annotations

import logging
from typing import Dict, List

from backend.deepagent.mcp_config_loader import load_system_mcp_servers
from backend.deepagent.mcp_credentials import (
    McpCredentialResolutionError,
    append_query_params,
    resolve_mcp_credential_config,
)
from backend.mcp.models import McpServerDefinition
from backend.storage import get_repository

logger = logging.getLogger(__name__)


async def _load_user_mcp_servers(user_id: str) -> List[McpServerDefinition]:
    repo = get_repository("user_mcp_servers")
    docs = await repo.find_many({"user_id": user_id})
    servers: List[McpServerDefinition] = []
    for doc in docs:
        endpoint = doc.get("endpoint_config") or {}
        servers.append(
            McpServerDefinition(
                id=str(doc["_id"]),
                name=doc["name"],
                description=doc.get("description", ""),
                transport=doc["transport"],
                scope="user",
                enabled=doc.get("enabled", True),
                default_enabled=doc.get("default_enabled", False),
                url=endpoint.get("url", ""),
                command=endpoint.get("command", ""),
                args=endpoint.get("args", []),
                cwd=endpoint.get("cwd", ""),
                headers=endpoint.get("headers", {}),
                env=endpoint.get("env", {}),
                timeout_ms=endpoint.get("timeout_ms", 20000),
                credential_binding=doc.get("credential_binding") or {},
                tool_policy=doc.get("tool_policy", {}),
            )
        )
    return servers


async def _load_session_mcp_bindings(session_id: str, user_id: str) -> Dict[str, str]:
    repo = get_repository("session_mcp_bindings")
    docs = await repo.find_many({"session_id": session_id, "user_id": user_id})
    return {doc["server_key"]: doc["mode"] for doc in docs}


async def build_effective_mcp_servers(session_id: str, user_id: str) -> List[McpServerDefinition]:
    merged = load_system_mcp_servers() + await _load_user_mcp_servers(user_id)
    bindings = await _load_session_mcp_bindings(session_id, user_id)
    effective: List[McpServerDefinition] = []

    for server in merged:
        if not server.enabled:
            continue
        key = f"{server.scope}:{server.id}"
        mode = bindings.get(key, "inherit")
        if mode == "disabled":
            continue
        if mode == "enabled" or server.default_enabled:
            if server.scope == "user":
                try:
                    server = await apply_mcp_credentials(server, user_id)
                except McpCredentialResolutionError as exc:
                    logger.warning("Skipping MCP server %s because credentials failed: %s", server.id, exc)
                    continue
            effective.append(server)
    return effective


async def apply_mcp_credentials(server: McpServerDefinition, user_id: str) -> McpServerDefinition:
    resolved = await resolve_mcp_credential_config(user_id, server.credential_binding)
    return server.model_copy(
        update={
            "headers": {**server.headers, **resolved.headers},
            "env": {**server.env, **resolved.env},
            "url": append_query_params(server.url, resolved.query),
        }
    )
