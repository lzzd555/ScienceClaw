from __future__ import annotations

import os
from pathlib import Path
from typing import List

import yaml

from backend.config import settings
from backend.mcp.models import McpServerDefinition, McpToolPolicy


def _is_local_storage_backend() -> bool:
    storage_backend = (os.environ.get("STORAGE_BACKEND") or settings.storage_backend or "").strip().lower()
    return storage_backend == "local"


def load_system_mcp_servers(config_path: str | Path | None = None) -> List[McpServerDefinition]:
    path = Path(config_path or settings.system_mcp_config_path)
    if not path.exists():
        return []

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_servers = payload.get("servers") or []
    servers: List[McpServerDefinition] = []

    for item in raw_servers:
        item = dict(item)
        item["scope"] = "system"
        headers = item.get("headers")
        if headers is None:
            headers = item.pop("http_headers", None)
        else:
            item.pop("http_headers", None)
        if headers is None:
            headers = item.pop("http_header", None)
        else:
            item.pop("http_header", None)
        if headers is not None:
            item["headers"] = headers
        item["tool_policy"] = McpToolPolicy(
            allowed_tools=item.pop("allowed_tools", []) or [],
            blocked_tools=item.pop("blocked_tools", []) or [],
        )
        server = McpServerDefinition(**item)
        if server.transport == "stdio" and not _is_local_storage_backend():
            raise ValueError("stdio MCP is only allowed in local mode")
        servers.append(server)

    return servers
