from __future__ import annotations

from typing import Dict, Optional

from backend.config import settings


def get_sandbox_base_url() -> str:
    return settings.sandbox_base_url.rstrip("/")


def get_sandbox_mcp_url() -> str:
    return settings.sandbox_mcp_url.rstrip("/")


def build_sandbox_headers(session_id: Optional[str] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if settings.sandbox_api_token:
        headers["Authorization"] = f"Bearer {settings.sandbox_api_token}"
    if session_id:
        headers["X-Session-ID"] = session_id
    return headers


def get_sandbox_vnc_url() -> str:
    if settings.sandbox_vnc_url:
        return settings.sandbox_vnc_url
    return (
        f"{get_sandbox_base_url()}/vnc/index.html"
        "?autoconnect=true&resize=scale&view_only=false"
    )
