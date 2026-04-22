"""Network traffic capture engine for API Monitor.

Handles request/response correlation, static resource filtering,
URL pattern parameterization, and deduplication.
"""

import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse, parse_qs, urlencode

from .models import CapturedApiCall, CapturedRequest, CapturedResponse

logger = logging.getLogger(__name__)

# ── Filtering ────────────────────────────────────────────────────────

STATIC_EXTENSIONS: Set[str] = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff",
    ".woff2", ".ttf", ".eot", ".ico", ".map", ".webp", ".avif",
    ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".flac", ".otf",
}

CAPTURE_RESOURCE_TYPES: Set[str] = {"xhr", "fetch"}

MAX_REQUEST_BODY_SIZE = 10 * 1024  # 10KB
MAX_RESPONSE_BODY_SIZE = 50 * 1024  # 50KB
RESPONSE_BODY_TIMEOUT_S = 5.0


def should_capture(url: str, resource_type: str) -> bool:
    """Return True if this request should be captured."""
    if resource_type not in CAPTURE_RESOURCE_TYPES:
        return False

    parsed = urlparse(url)
    path_lower = parsed.path.lower()

    if parsed.scheme in ("data",):
        return False

    if parsed.scheme in ("ws", "wss"):
        return False

    for ext in STATIC_EXTENSIONS:
        if path_lower.endswith(ext):
            return False

    return True


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


def dedup_key(call: CapturedApiCall) -> str:
    """Return a deduplication key for grouping similar API calls."""
    pattern = call.url_pattern or parameterize_url(call.request.url)
    return f"{call.request.method} {pattern}"


# ── Capture engine ───────────────────────────────────────────────────


class NetworkCaptureEngine:
    """Manages in-flight request tracking and creates CapturedApiCall objects."""

    def __init__(self) -> None:
        self._in_flight: Dict[int, Dict] = {}
        self._captured_calls: List[CapturedApiCall] = []

    @property
    def captured_calls(self) -> List[CapturedApiCall]:
        return list(self._captured_calls)

    def clear(self) -> None:
        self._in_flight.clear()
        self._captured_calls.clear()

    def on_request(self, request) -> None:
        """Called by page.on('request')."""
        if not should_capture(request.url, request.resource_type):
            return

        body = None
        content_type = None
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = request.post_data
                if body and len(body) > MAX_REQUEST_BODY_SIZE:
                    body = body[:MAX_REQUEST_BODY_SIZE] + "...[truncated]"
            except Exception:
                pass
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

        self._in_flight[id(request)] = {
            "request": captured_req,
            "start_time": time.monotonic(),
        }

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
        except Exception:
            pass

        captured_resp = CapturedResponse(
            status=response.status,
            status_text=response.status_text,
            headers=dict(response.headers),
            body=resp_body,
            content_type=resp_content_type,
            timestamp=datetime.now(),
        )

        call = CapturedApiCall(
            request=captured_req,
            response=captured_resp,
            url_pattern=parameterize_url(captured_req.url),
            duration_ms=round(duration_ms, 1),
        )

        self._captured_calls.append(call)
        logger.debug(
            "[ApiMonitor] Captured %s %s -> %d (%.0fms)",
            captured_req.method,
            captured_req.url[:80],
            response.status,
            duration_ms,
        )

    def drain_new_calls(self) -> List[CapturedApiCall]:
        """Return all captured calls and clear the internal list."""
        calls = self._captured_calls
        self._captured_calls = []
        return calls
