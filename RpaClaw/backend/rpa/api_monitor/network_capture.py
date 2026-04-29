"""Network traffic capture engine for API Monitor.

Handles request/response correlation, static resource filtering,
URL pattern parameterization, and deduplication.
"""

import logging
import re
import time
from datetime import datetime
from typing import Awaitable, Callable, Dict, List, Optional, Set
from urllib.parse import urlparse, parse_qs, urlencode

from .models import CapturedApiCall, CapturedRequest, CapturedResponse

logger = logging.getLogger(__name__)

# ── Filtering ────────────────────────────────────────────────────────

STATIC_EXTENSIONS: Set[str] = {
    # Page rendering resources
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff",
    ".woff2", ".ttf", ".eot", ".ico", ".map", ".webp", ".avif",
    ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".flac", ".otf",
    # 3D models / WebAssembly / binary resources (noise)
    ".glb", ".gltf", ".wasm", ".bin", ".proto", ".pb",
    ".obj", ".fbx", ".usdz", ".hdr",
    # NOTE: .pdf/.doc/.zip etc. are NOT included because download URLs
    # are valid API endpoints (e.g., "download report" tool)
}

CAPTURE_RESOURCE_TYPES: Set[str] = {"xhr", "fetch"}

MAX_REQUEST_BODY_SIZE = 10 * 1024  # 10KB
MAX_RESPONSE_BODY_SIZE = 50 * 1024  # 50KB
RESPONSE_BODY_TIMEOUT_S = 5.0


def should_capture(url: str, resource_type: str, page_url: Optional[str] = None) -> bool:
    """Return True if this request should be captured."""
    if resource_type not in CAPTURE_RESOURCE_TYPES:
        return False

    parsed = urlparse(url)
    path_lower = parsed.path.lower()

    if parsed.scheme in ("data",):
        return False

    if parsed.scheme in ("ws", "wss"):
        return False

    if page_url and not _is_same_origin_or_relative(parsed, page_url):
        return False

    for ext in STATIC_EXTENSIONS:
        if path_lower.endswith(ext):
            return False

    return True


def _is_same_origin_or_relative(parsed_url, page_url: str) -> bool:
    """Return True when a request URL is relative or matches the page origin."""
    if not parsed_url.scheme and not parsed_url.netloc:
        return True

    page = urlparse(page_url)
    if not page.scheme or not page.hostname or not parsed_url.hostname:
        return True

    return (
        parsed_url.scheme.lower(),
        parsed_url.hostname.lower(),
        _normalized_port(parsed_url),
    ) == (
        page.scheme.lower(),
        page.hostname.lower(),
        _normalized_port(page),
    )


def _normalized_port(parsed_url) -> Optional[int]:
    if parsed_url.port is not None:
        return parsed_url.port
    if parsed_url.scheme == "http":
        return 80
    if parsed_url.scheme == "https":
        return 443
    return None


# ── URL pattern parameterization ─────────────────────────────────────

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_NUMERIC_RE = re.compile(r"^\d+$")
_DATE_PATH_RE = re.compile(r"^\d{4}$")


def parameterize_url(url: str) -> str:
    """Convert a concrete URL into a parameterized pattern.

    Examples:
        /api/users/123 -> /api/users/{id}
        /api/search?q=foo&page=2 -> /api/search?q={query}&page={page}
    """
    parsed = urlparse(url)
    path_segments = parsed.path.strip("/").split("/")
    param_segments: List[str] = []

    for seg in path_segments:
        if not seg:
            continue
        if _UUID_RE.match(seg):
            param_segments.append("{id}")
        elif _NUMERIC_RE.match(seg) and len(seg) < 10:
            param_segments.append("{id}")
        elif _DATE_PATH_RE.match(seg) and len(seg) == 4:
            param_segments.append("{year}")
        else:
            param_segments.append(seg)

    param_path = "/" + "/".join(param_segments) if param_segments else "/"

    if parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=True)
        param_qs: Dict[str, str] = {}
        for key in qs:
            param_qs[key] = "{" + key + "}"
        param_path += "?" + urlencode(param_qs, doseq=True)

    return param_path


# ── Deduplication key ────────────────────────────────────────────────


def _normalize_url_for_dedup(url: str) -> str:
    """Normalize a URL for deduplication using only the URL path."""
    parsed = urlparse(url)
    return parsed.path


def dedup_key(call: CapturedApiCall) -> str:
    """Return a deduplication key for grouping similar API calls."""
    if call.url_pattern:
        normalized = _normalize_url_for_dedup(call.url_pattern)
    else:
        # Normalize the raw URL first, then parameterize numeric/UUID path segments.
        normalized = _normalize_url_for_dedup(call.request.url)
        normalized = parameterize_url(normalized)
    return f"{call.request.method} {normalized}"


# ── Capture engine ───────────────────────────────────────────────────


class NetworkCaptureEngine:
    """Manages in-flight request tracking and creates CapturedApiCall objects."""

    def __init__(
        self,
        page_url_provider: Optional[Callable[[], str]] = None,
        evidence_provider: Optional[Callable[[object], Dict]] = None,
        async_evidence_provider: Optional[Callable[[object], Awaitable[Dict]]] = None,
    ) -> None:
        self._in_flight: Dict[int, Dict] = {}
        self._captured_calls: List[CapturedApiCall] = []
        self._page_url_provider = page_url_provider
        self._evidence_provider = evidence_provider
        self._async_evidence_provider = async_evidence_provider
        # Optional callback invoked when a request/response is captured or skipped.
        # Signature: (level: str, message: str) -> None
        self.on_log: Optional[Callable[[str, str], None]] = None

    @property
    def captured_calls(self) -> List[CapturedApiCall]:
        return list(self._captured_calls)

    def clear(self) -> None:
        self._in_flight.clear()
        self._captured_calls.clear()

    def on_request(self, request) -> None:
        """Called by page.on('request')."""
        page_url = self._current_page_url(request)
        if not should_capture(request.url, request.resource_type, page_url=page_url):
            logger.debug(
                "[ApiMonitor] Skipped request: resource_type=%s page_url=%s url=%s",
                request.resource_type,
                page_url,
                request.url[:120],
            )
            return

        logger.info(
            "[ApiMonitor] Detected API request: %s %s (resource_type=%s)",
            request.method,
            request.url[:120],
            request.resource_type,
        )
        if self.on_log:
            self.on_log("RECV", f"检测到请求: {request.method} {request.url[:100]}")

        body = None
        content_type = None
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = request.post_data
                if body and len(body) > MAX_REQUEST_BODY_SIZE:
                    body = body[:MAX_REQUEST_BODY_SIZE] + "...[truncated]"
            except Exception as exc:
                logger.warning("[ApiMonitor] Failed to read request body: %s", exc)
                if self.on_log:
                    self.on_log("ERROR", f"读取请求体失败: {exc}")
            content_type = request.headers.get("content-type")

        captured_req = CapturedRequest(
            request_id=str(id(request)),
            url=request.url,
            method=request.method,
            headers=dict(request.headers),
            body=body,
            content_type=content_type,
            timestamp=datetime.now(),
            resource_type=request.resource_type,
        )

        source_evidence = self._source_evidence(request)

        self._in_flight[id(request)] = {
            "request": captured_req,
            "start_time": time.monotonic(),
            "source_evidence": source_evidence,
        }

    def _current_page_url(self, request=None) -> Optional[str]:
        if request is not None:
            try:
                frame_url = getattr(getattr(request, "frame", None), "url", "")
                if frame_url and frame_url != "about:blank":
                    return frame_url
            except Exception as exc:
                logger.debug("[ApiMonitor] Failed to read request frame URL: %s", exc)

        if not self._page_url_provider:
            return None
        try:
            return self._page_url_provider()
        except Exception as exc:
            logger.debug("[ApiMonitor] Failed to read page URL for capture filter: %s", exc)
            return None

    def _source_evidence(self, request) -> Dict:
        if not self._evidence_provider:
            return {}
        try:
            return self._evidence_provider(request) or {}
        except Exception as exc:
            logger.debug("[ApiMonitor] Failed to read request evidence: %s", exc)
            return {}

    async def _async_source_evidence(self, request) -> Dict:
        if not self._async_evidence_provider:
            return {}
        try:
            return await self._async_evidence_provider(request) or {}
        except Exception as exc:
            logger.debug("[ApiMonitor] Failed to read async request evidence: %s", exc)
            return {}

    async def on_response(self, response) -> None:
        """Called by page.on('response')."""
        req = response.request
        info = self._in_flight.pop(id(req), None)
        if info is None:
            return

        captured_req: CapturedRequest = info["request"]
        start_time: float = info["start_time"]
        duration_ms = (time.monotonic() - start_time) * 1000

        resp_body = None
        resp_content_type = response.headers.get("content-type")
        try:
            resp_body = await response.text()
            if resp_body and len(resp_body) > MAX_RESPONSE_BODY_SIZE:
                resp_body = resp_body[:MAX_RESPONSE_BODY_SIZE] + "...[truncated]"
        except Exception as exc:
            logger.warning(
                "[ApiMonitor] Failed to read response body for %s %s: %s",
                captured_req.method,
                captured_req.url[:80],
                exc,
            )

        captured_resp = CapturedResponse(
            status=response.status,
            status_text=response.status_text,
            headers=dict(response.headers),
            body=resp_body,
            content_type=resp_content_type,
            timestamp=datetime.now(),
        )

        source_evidence: Dict = dict(info.get("source_evidence") or {})
        async_evidence = await self._async_source_evidence(req)
        for key, value in async_evidence.items():
            if key in ("initiator_urls", "js_stack_urls"):
                source_evidence[key] = list(dict.fromkeys([
                    *source_evidence.get(key, []),
                    *value,
                ]))
            elif value and not source_evidence.get(key):
                source_evidence[key] = value

        call = CapturedApiCall(
            request=captured_req,
            response=captured_resp,
            url_pattern=parameterize_url(captured_req.url),
            duration_ms=round(duration_ms, 1),
            source_evidence=source_evidence,
        )

        self._captured_calls.append(call)
        logger.info(
            "[ApiMonitor] Captured %s %s -> %d (%.0fms)",
            captured_req.method,
            captured_req.url[:80],
            response.status,
            duration_ms,
        )
        if self.on_log:
            status_label = response.status
            self.on_log(
                "RECV",
                f"已捕获: {captured_req.method} {captured_req.url[:80]} -> {status_label} ({duration_ms:.0f}ms)",
            )

    def drain_new_calls(self) -> List[CapturedApiCall]:
        """Return all captured calls and clear the internal list."""
        calls = self._captured_calls
        self._captured_calls = []
        return calls
