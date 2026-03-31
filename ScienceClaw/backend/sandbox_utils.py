from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from backend.config import parse_sandbox_extra_headers, settings


def get_sandbox_base_url() -> str:
    return settings.sandbox_base_url.rstrip("/")


def get_sandbox_mcp_url() -> str:
    return settings.sandbox_mcp_url.rstrip("/")


def build_sandbox_auth_headers(session_id: Optional[str] = None) -> Dict[str, str]:
    headers: Dict[str, str] = parse_sandbox_extra_headers(settings.sandbox_extra_headers_raw)
    if settings.sandbox_api_token:
        headers["Authorization"] = f"Bearer {settings.sandbox_api_token}"
    if session_id:
        headers["X-Session-ID"] = session_id
    return headers


def build_sandbox_headers(session_id: Optional[str] = None) -> Dict[str, str]:
    headers = build_sandbox_auth_headers(session_id)
    headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    return headers


def get_sandbox_vnc_url() -> str:
    if settings.sandbox_vnc_url:
        return settings.sandbox_vnc_url
    return (
        f"{get_sandbox_base_url()}/vnc/index.html"
        "?autoconnect=true&resize=scale&view_only=false"
    )


def get_sandbox_vnc_proxy_url() -> str:
    return "/api/v1/rpa/sandbox/vnc/index.html"


def build_sandbox_vnc_target_url(
    asset_path: str,
    extra_query_items: Optional[Iterable[Tuple[str, str]]] = None,
    *,
    websocket: bool = False,
) -> str:
    entry_url = get_sandbox_vnc_url()
    split = urlsplit(entry_url)
    target_url = urljoin(entry_url, asset_path)

    base_query = parse_qsl(split.query, keep_blank_values=True)
    merged_query = list(base_query)
    if extra_query_items:
        merged_query.extend((k, v) for k, v in extra_query_items)

    target_split = urlsplit(target_url)
    scheme = target_split.scheme
    if websocket:
        if scheme == "https":
            scheme = "wss"
        elif scheme == "http":
            scheme = "ws"

    return urlunsplit((
        scheme,
        target_split.netloc,
        target_split.path,
        urlencode(merged_query, doseq=True),
        target_split.fragment,
    ))
