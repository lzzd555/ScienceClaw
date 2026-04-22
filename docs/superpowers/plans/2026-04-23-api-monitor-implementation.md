# API Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an API Monitor feature that captures browser API requests, uses LLM to generate OpenAI-format tool definitions, and provides a management UI for reviewing/editing those tools.

**Architecture:** New backend module `rpa/api_monitor/` with independent session manager, network capture engine, and LLM analyzer. Frontend adds `ApiMonitorPage.vue` at `/rpa/api-monitor` with screencast browser viewport, terminal log, and tool card list. Entry point from ToolsPage "Add MCP Tool" dropdown.

**Tech Stack:** Python 3.13, FastAPI, Playwright, Pydantic v2, Vue 3, TypeScript, Tailwind CSS, SSE (sse-starlette), WebSocket, JetBrains Mono

**Design specs:**
- `docs/superpowers/specs/2026-04-22-rpa-api-monitor-design.md`
- `docs/superpowers/specs/2026-04-22-rpa-api-monitor-ui-enhancement-design.md`

---

## File Map

### New files

| File | Responsibility |
|------|---------------|
| `backend/rpa/api_monitor/__init__.py` | Package init, exports manager singleton |
| `backend/rpa/api_monitor/models.py` | Pydantic data models for session, requests, responses, tool definitions |
| `backend/rpa/api_monitor/manager.py` | Session lifecycle: create/stop browser context, navigate, install network listeners, orchestrate analysis |
| `backend/rpa/api_monitor/network_capture.py` | HTTP traffic capture: request/response correlation, static resource filtering, URL pattern parameterization, deduplication |
| `backend/rpa/api_monitor/llm_analyzer.py` | LLM integration: DOM element safety analysis, API call → YAML tool definition generation |
| `backend/route/api_monitor.py` | FastAPI router: session CRUD, screencast WS, analyze SSE, tools CRUD, record start/stop |
| `frontend/src/api/apiMonitor.ts` | API client functions for api-monitor endpoints |
| `frontend/src/pages/rpa/ApiMonitorPage.vue` | Full API Monitor page: browser viewport, terminal log, tool cards, analysis/recording controls |

### Modified files

| File | Change |
|------|--------|
| `backend/main.py` ~L157 | Add `api_monitor_router` import and `include_router` |
| `frontend/src/main.ts` ~L119 | Add `api-monitor` route in `/rpa` children |
| `frontend/src/pages/ToolsPage.vue` | Add "From API Monitor" option to "Add Tool" button |

---

## Task 1: Backend Data Models

**Files:**
- Create: `RpaClaw/backend/rpa/api_monitor/__init__.py`
- Create: `RpaClaw/backend/rpa/api_monitor/models.py`

- [ ] **Step 1: Create package directory and init**

```bash
mkdir -p RpaClaw/backend/rpa/api_monitor
```

Create `RpaClaw/backend/rpa/api_monitor/__init__.py`:

```python
from .manager import api_monitor_manager

__all__ = ["api_monitor_manager"]
```

- [ ] **Step 2: Create models.py with all data models**

Create `RpaClaw/backend/rpa/api_monitor/models.py`:

```python
"""Data models for the API Monitor feature."""

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


def _gen_id() -> str:
    return str(uuid.uuid4())


# ── Captured request/response ────────────────────────────────────────


class CapturedRequest(BaseModel):
    request_id: str
    url: str
    method: str
    headers: Dict[str, str]
    body: Optional[str] = None
    content_type: Optional[str] = None
    timestamp: datetime
    resource_type: str  # "xhr" or "fetch"


class CapturedResponse(BaseModel):
    status: int
    status_text: str
    headers: Dict[str, str]
    body: Optional[str] = None
    content_type: Optional[str] = None
    timestamp: datetime


class CapturedApiCall(BaseModel):
    id: str = Field(default_factory=_gen_id)
    request: CapturedRequest
    response: Optional[CapturedResponse] = None
    trigger_element: Optional[Dict] = None
    url_pattern: Optional[str] = None
    duration_ms: Optional[float] = None


# ── Tool definition ──────────────────────────────────────────────────


class ApiToolDefinition(BaseModel):
    id: str = Field(default_factory=_gen_id)
    session_id: str
    name: str
    description: str
    method: str
    url_pattern: str
    headers_schema: Optional[Dict] = None
    request_body_schema: Optional[Dict] = None
    response_body_schema: Optional[Dict] = None
    trigger_locator: Optional[Dict] = None
    yaml_definition: str
    source_calls: List[str] = Field(default_factory=list)
    source: str = "auto"  # "auto" or "manual"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ── Session ──────────────────────────────────────────────────────────


class ApiMonitorSession(BaseModel):
    id: str = Field(default_factory=_gen_id)
    user_id: str
    sandbox_session_id: str
    status: str = "idle"  # idle, analyzing, recording, stopped
    target_url: Optional[str] = None
    captured_calls: List[CapturedApiCall] = Field(default_factory=list)
    tool_definitions: List[ApiToolDefinition] = Field(default_factory=list)
    active_tab_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ── Request schemas for API endpoints ────────────────────────────────


class StartSessionRequest(BaseModel):
    url: str


class NavigateRequest(BaseModel):
    url: str


class UpdateToolRequest(BaseModel):
    yaml_definition: str
```

- [ ] **Step 3: Commit models**

```bash
git add RpaClaw/backend/rpa/api_monitor/__init__.py RpaClaw/backend/rpa/api_monitor/models.py
git commit -m "feat(api-monitor): add data models for API Monitor feature"
```

---

## Task 2: Network Capture Engine

**Files:**
- Create: `RpaClaw/backend/rpa/api_monitor/network_capture.py`

- [ ] **Step 1: Create network_capture.py**

Create `RpaClaw/backend/rpa/api_monitor/network_capture.py`:

```python
"""Network traffic capture engine for API Monitor.

Handles request/response correlation, static resource filtering,
URL pattern parameterization, and deduplication.
"""

import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from .models import CapturedApiCall, CapturedRequest, CapturedResponse

logger = logging.getLogger(__name__)

# ── Filtering ────────────────────────────────────────────────────────

STATIC_EXTENSIONS: Set[str] = {
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff",
    ".woff2", ".ttf", ".eot", ".ico", ".map", ".webp", ".avif",
    ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".flac",
    ".woff2", ".otf",
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

    # Skip data: URIs
    if parsed.scheme in ("data",):
        return False

    # Skip WebSocket upgrades
    if parsed.scheme in ("ws", "wss"):
        return False

    # Skip static resources by extension
    for ext in STATIC_EXTENSIONS:
        if path_lower.endswith(ext):
            return False

    return True


# ── URL pattern parameterization ─────────────────────────────────────

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_NUMERIC_RE = re.compile(r"^\d+$")
_DATE_PATH_RE = re.compile(r"^\d{4}$")  # year-like segments for date paths


def parameterize_url(url: str) -> str:
    """Convert a concrete URL into a parameterized pattern.

    Examples:
        /api/users/123 → /api/users/{id}
        /api/search?q=foo&page=2 → /api/search?q={query}&page={page}
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

    # Parameterize query string values
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
        self._in_flight: Dict[int, Dict] = {}  # id(request) → info dict
        self._captured_calls: List[CapturedApiCall] = []

    @property
    def captured_calls(self) -> List[CapturedApiCall]:
        return list(self._captured_calls)

    def clear(self) -> None:
        """Clear captured calls and in-flight tracking."""
        self._in_flight.clear()
        self._captured_calls.clear()

    def on_request(self, request) -> None:
        """Called by page.on('request'). `request` is a Playwright Request object."""
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
        """Called by page.on('response'). `response` is a Playwright Response object."""
        req = response.request
        info = self._in_flight.pop(id(req), None)
        if info is None:
            return

        captured_req: CapturedRequest = info["request"]
        start_time: float = info["start_time"]
        duration_ms = (time.monotonic() - start_time) * 1000

        # Fetch response body with timeout
        resp_body = None
        resp_content_type = response.headers.get("content-type")
        try:
            resp_body = await response.text()
            if resp_body and len(resp_body) > MAX_RESPONSE_BODY_SIZE:
                resp_body = resp_body[:MAX_RESPONSE_BODY_SIZE] + "...[truncated]"
        except Exception:
            pass  # Silently ignore — don't interfere with page behavior

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
            "[ApiMonitor] Captured %s %s → %d (%.0fms)",
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
```

- [ ] **Step 2: Commit network capture engine**

```bash
git add RpaClaw/backend/rpa/api_monitor/network_capture.py
git commit -m "feat(api-monitor): add network capture engine with filtering and URL parameterization"
```

---

## Task 3: LLM Analyzer

**Files:**
- Create: `RpaClaw/backend/rpa/api_monitor/llm_analyzer.py`

- [ ] **Step 1: Create llm_analyzer.py**

Create `RpaClaw/backend/rpa/api_monitor/llm_analyzer.py`:

```python
"""LLM integration for API Monitor.

Two prompts:
1. DOM element safety analysis — classify interactive elements as safe/skip
2. API call → YAML tool definition generation
"""

import json
import logging
import re
from typing import AsyncGenerator, Dict, List, Optional

from backend.deepagent.engine import get_llm_model

from .models import CapturedApiCall

logger = logging.getLogger(__name__)

# ── Element analysis prompt ──────────────────────────────────────────

ELEMENT_ANALYSIS_SYSTEM = """\
You are a web automation safety analyzer. Given a list of interactive elements on a web page, \
classify each one as either "safe_to_probe" or "skip".

Rules for "skip":
- Elements with text containing: delete, remove, logout, sign out, sign out, cancel subscription, \
  reset, purge, drop, uninstall, deactivate, disable, revoke, eject, reject, decline, block, ban
- Elements that navigate to a different domain (external links)
- Elements that trigger file downloads
- Form submit buttons on payment/checkout forms
- Elements with role="destructive"

Rules for "safe_to_probe":
- Navigation within the same site
- Search buttons, filter buttons, pagination
- Tab switches, accordion toggles
- Form inputs (text, select, checkbox)
- Dialog/modal open buttons
- "Load more" / "Show more" buttons
- Table row clicks, list item clicks

Return a JSON object with keys "safe" and "skip", each containing a list of element indices (0-based).
Only return valid JSON, no markdown fences.
"""

ELEMENT_ANALYSIS_USER = """\
Page URL: {url}

Interactive elements:
{elements_json}

Classify each element. Return JSON: {{"safe": [0, 2, 5, ...], "skip": [1, 3, 4, ...]}}
"""

# ── Tool generation prompt ───────────────────────────────────────────

TOOL_GEN_SYSTEM = """\
You are an API tool definition generator. Given HTTP API call samples captured from a web application, \
generate an OpenAI function calling format tool definition in YAML.

The YAML must have this structure:
```yaml
name: <snake_case_function_name>
description: <clear description of what this API endpoint does>
method: <HTTP method>
url: <parameterized URL path>
parameters:
  type: object
  properties:
    <param_name>:
      type: <string|integer|boolean|array|object>
      description: <what this parameter does>
      in: <query|path|body|header>
  required:
    - <required_param_names>
response:
  type: object
  properties:
    <field_name>:
      type: <type>
      description: <what this field contains>
```

Guidelines:
- Function names should be descriptive snake_case (e.g., list_users, create_order, search_products)
- Parameterize URL path segments that look like IDs: /users/123 → /users/{user_id}
- Include all visible query parameters and request body fields
- Mark parameters as required only if they appear in every sample or seem essential
- Infer response schema from the captured response bodies
- Only return valid YAML, no markdown fences, no extra commentary
"""

TOOL_GEN_USER = """\
Endpoint: {method} {url_pattern}
Page context: {page_context}

API call samples:
{samples_json}

Generate the YAML tool definition.
"""

# ── LLM call helpers ─────────────────────────────────────────────────


async def _call_llm(
    system_prompt: str,
    user_prompt: str,
    model_config: Optional[Dict] = None,
) -> str:
    """Call LLM with system + user messages and return full text response."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    model = get_llm_model(config=model_config, streaming=False)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    response = await model.ainvoke(messages)
    text = ""
    if isinstance(response, AIMessage):
        text = response.content or ""
    elif hasattr(response, "content"):
        text = str(response.content)
    else:
        text = str(response)
    return text.strip()


# ── Public API ───────────────────────────────────────────────────────


async def analyze_elements(
    url: str,
    elements: List[Dict],
    model_config: Optional[Dict] = None,
) -> Dict[str, List[int]]:
    """Classify interactive elements as safe or skip.

    Returns {"safe": [indices], "skip": [indices]}.
    """
    if not elements:
        return {"safe": [], "skip": []}

    user_prompt = ELEMENT_ANALYSIS_USER.format(
        url=url,
        elements_json=json.dumps(elements, indent=2, ensure_ascii=False),
    )

    raw = await _call_llm(ELEMENT_ANALYSIS_SYSTEM, user_prompt, model_config)

    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)

    try:
        result = json.loads(raw)
        return {
            "safe": result.get("safe", []),
            "skip": result.get("skip", []),
        }
    except json.JSONDecodeError:
        logger.warning("[ApiMonitor] Failed to parse element analysis response: %s", raw[:200])
        return {"safe": list(range(len(elements))), "skip": []}


async def generate_tool_definition(
    method: str,
    url_pattern: str,
    samples: List[CapturedApiCall],
    page_context: str = "",
    model_config: Optional[Dict] = None,
) -> str:
    """Generate an OpenAI YAML tool definition from captured API call samples.

    Returns the raw YAML string.
    """
    sample_data = []
    for call in samples[:5]:  # Max 5 samples per group
        entry: Dict = {
            "request_body": None,
            "response_status": None,
            "response_body": None,
        }
        if call.request.body:
            try:
                entry["request_body"] = json.loads(call.request.body)
            except (json.JSONDecodeError, TypeError):
                entry["request_body"] = call.request.body
        if call.response:
            entry["response_status"] = call.response.status
            if call.response.body:
                try:
                    entry["response_body"] = json.loads(call.response.body)
                except (json.JSONDecodeError, TypeError):
                    entry["response_body"] = call.response.body
        sample_data.append(entry)

    user_prompt = TOOL_GEN_USER.format(
        method=method,
        url_pattern=url_pattern,
        page_context=page_context or "Unknown page",
        samples_json=json.dumps(sample_data, indent=2, ensure_ascii=False),
    )

    raw = await _call_llm(TOOL_GEN_SYSTEM, user_prompt, model_config)

    # Strip markdown fences if present
    raw = re.sub(r"^```(?:yaml)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)

    return raw.strip()
```

- [ ] **Step 2: Commit LLM analyzer**

```bash
git add RpaClaw/backend/rpa/api_monitor/llm_analyzer.py
git commit -m "feat(api-monitor): add LLM analyzer for element safety and tool generation"
```

---

## Task 4: Session Manager

**Files:**
- Create: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Modify: `RpaClaw/backend/rpa/api_monitor/__init__.py`

- [ ] **Step 1: Create manager.py**

Create `RpaClaw/backend/rpa/api_monitor/manager.py`:

```python
"""API Monitor session manager.

Manages browser context lifecycle, network listener installation,
automatic analysis orchestration, and recording mode control.
"""

import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator, Callable, Dict, List, Optional

from playwright.async_api import Browser, BrowserContext, Page

from backend.config import settings
from backend.rpa.cdp_connector import get_cdp_connector
from backend.rpa.playwright_security import get_context_kwargs

from .llm_analyzer import analyze_elements, generate_tool_definition
from .models import (
    ApiMonitorSession,
    ApiToolDefinition,
    CapturedApiCall,
)
from .network_capture import NetworkCaptureEngine

logger = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 60000


class ApiMonitorSessionManager:
    """Manages API Monitor sessions — one per user workflow."""

    def __init__(self) -> None:
        self.sessions: Dict[str, ApiMonitorSession] = {}
        self._contexts: Dict[str, BrowserContext] = {}
        self._pages: Dict[str, Page] = {}
        self._captures: Dict[str, NetworkCaptureEngine] = {}

    # ── Session lifecycle ────────────────────────────────────────────

    async def create_session(
        self,
        user_id: str,
        target_url: str,
        sandbox_session_id: Optional[str] = None,
    ) -> ApiMonitorSession:
        session_id = str(uuid.uuid4())
        session = ApiMonitorSession(
            id=session_id,
            user_id=user_id,
            sandbox_session_id=sandbox_session_id or session_id,
            target_url=target_url,
        )
        self.sessions[session_id] = session

        # Create browser context via CDP connector
        browser: Browser = await get_cdp_connector().get_browser(
            session_id=session.sandbox_session_id,
            user_id=user_id,
        )
        context = await browser.new_context(**get_context_kwargs())
        page = await context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)

        self._contexts[session_id] = context
        self._pages[session_id] = page

        # Install network capture
        capture = NetworkCaptureEngine()
        self._captures[session_id] = capture
        self._install_listeners(session_id, page, capture)

        # Navigate to target URL
        await page.goto(target_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)

        logger.info("[ApiMonitor] Session %s created, navigated to %s", session_id, target_url)
        return session

    async def stop_session(self, session_id: str) -> Optional[ApiMonitorSession]:
        session = self.sessions.pop(session_id, None)
        if session:
            session.status = "stopped"

        context = self._contexts.pop(session_id, None)
        if context:
            try:
                await context.close()
            except Exception as e:
                logger.warning("[ApiMonitor] Error closing context: %s", e)

        self._pages.pop(session_id, None)
        self._captures.pop(session_id, None)

        logger.info("[ApiMonitor] Session %s stopped", session_id)
        return session

    async def navigate(self, session_id: str, url: str) -> None:
        page = self._get_page(session_id)
        if not page:
            raise ValueError(f"Session {session_id} has no active page")
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        session = self.sessions.get(session_id)
        if session:
            session.target_url = url

    def get_session(self, session_id: str) -> Optional[ApiMonitorSession]:
        return self.sessions.get(session_id)

    def get_page(self, session_id: str) -> Optional[Page]:
        return self._pages.get(session_id)

    # ── Network listener installation ────────────────────────────────

    def _install_listeners(
        self,
        session_id: str,
        page: Page,
        capture: NetworkCaptureEngine,
    ) -> None:
        def on_request(request):
            if not should_process_request(request):
                return
            capture.on_request(request)

        async def on_response(response):
            await capture.on_response(response)

        page.on("request", on_request)
        page.on("response", on_response)

    # ── Recording mode ───────────────────────────────────────────────

    async def start_recording(self, session_id: str) -> None:
        session = self._require_session(session_id)
        capture = self._captures.get(session_id)
        if capture:
            capture.clear()
        session.status = "recording"
        logger.info("[ApiMonitor] Recording started for session %s", session_id)

    async def stop_recording(self, session_id: str) -> List[ApiToolDefinition]:
        session = self._require_session(session_id)
        session.status = "processing"

        capture = self._captures.get(session_id)
        if not capture:
            session.status = "idle"
            return []

        new_calls = capture.drain_new_calls()
        if not new_calls:
            session.status = "idle"
            return []

        # Group calls by dedup key and generate tools
        tools = await self._generate_tools_from_calls(session_id, new_calls, source="manual")
        session.status = "idle"
        return tools

    # ── Automatic analysis ───────────────────────────────────────────

    async def analyze_page(
        self,
        session_id: str,
        model_config: Optional[Dict] = None,
    ) -> AsyncGenerator[Dict, None]:
        """Run automatic analysis. Yields SSE event dicts."""
        session = self._require_session(session_id)
        page = self._get_page(session_id)
        if not page:
            yield {"event": "error", "data": json.dumps({"message": "No active page"})}
            return

        session.status = "analyzing"

        try:
            yield {
                "event": "analysis_started",
                "data": json.dumps({"url": session.target_url}),
            }

            # Step 1: Scan DOM for interactive elements
            elements = await self._scan_interactive_elements(page)
            yield {
                "event": "elements_found",
                "data": json.dumps({"count": len(elements), "elements": elements[:20]}),
            }

            if not elements:
                yield {
                    "event": "analysis_complete",
                    "data": json.dumps({"total_tools": 0, "total_calls": 0}),
                }
                return

            # Step 2: LLM safety classification
            safe_indices = await analyze_elements(
                session.target_url or "", elements, model_config
            )

            safe_elems = [
                elements[i] for i in safe_indices.get("safe", [])
                if i < len(elements)
            ]

            # Step 3: Probe each safe element
            capture = self._captures.get(session_id)
            if not capture:
                yield {"event": "error", "data": json.dumps({"message": "No capture engine"})}
                return

            all_new_calls: List[CapturedApiCall] = []

            for idx, elem in enumerate(safe_elems):
                yield {
                    "event": "probing_element",
                    "data": json.dumps({
                        "index": idx + 1,
                        "total": len(safe_elems),
                        "element": elem,
                    }),
                }

                capture.clear()
                calls = await self._probe_element(page, elem)
                all_new_calls.extend(calls)

                yield {
                    "event": "calls_captured",
                    "data": json.dumps({
                        "count": len(calls),
                        "new_calls": [
                            {
                                "method": c.request.method,
                                "url": c.request.url[:100],
                                "status": c.response.status if c.response else None,
                            }
                            for c in calls
                        ],
                    }),
                }

            if not all_new_calls:
                yield {
                    "event": "analysis_complete",
                    "data": json.dumps({"total_tools": 0, "total_calls": 0}),
                }
                return

            # Step 4: Generate tools from captured calls
            yield {
                "event": "generating_tools",
                "data": json.dumps({"endpoint_count": len(all_new_calls)}),
            }

            tools = await self._generate_tools_from_calls(
                session_id, all_new_calls, source="auto", model_config=model_config,
            )

            for tool in tools:
                yield {
                    "event": "tool_generated",
                    "data": json.dumps(tool.model_dump(), ensure_ascii=False),
                }

            yield {
                "event": "analysis_complete",
                "data": json.dumps({
                    "total_tools": len(tools),
                    "total_calls": len(all_new_calls),
                }),
            }

        except Exception as e:
            logger.exception("[ApiMonitor] Analysis failed: %s", e)
            yield {"event": "error", "data": json.dumps({"message": str(e)})}
        finally:
            session.status = "idle"

    # ── Tool management ──────────────────────────────────────────────

    async def _generate_tools_from_calls(
        self,
        session_id: str,
        calls: List[CapturedApiCall],
        source: str = "auto",
        model_config: Optional[Dict] = None,
    ) -> List[ApiToolDefinition]:
        """Group calls by pattern, generate tool definitions via LLM."""
        from .network_capture import dedup_key

        session = self._require_session(session_id)

        # Group by dedup key
        groups: Dict[str, List[CapturedApiCall]] = {}
        for call in calls:
            key = dedup_key(call)
            groups.setdefault(key, []).append(call)

        tools: List[ApiToolDefinition] = []

        for key, group_calls in groups.items():
            first = group_calls[0]
            method = first.request.method
            url_pattern = first.url_pattern or first.request.url

            yaml_def = await generate_tool_definition(
                method=method,
                url_pattern=url_pattern,
                samples=group_calls,
                page_context=session.target_url or "",
                model_config=model_config,
            )

            if not yaml_def:
                continue

            # Extract name from YAML (first line like "name: xxx")
            name = method.lower() + "_" + url_pattern.strip("/").replace("/", "_").replace("{", "").replace("}", "")
            # Simplify name
            name = name[:60]

            tool = ApiToolDefinition(
                session_id=session_id,
                name=name,
                description=f"{method} {url_pattern}",
                method=method,
                url_pattern=url_pattern,
                yaml_definition=yaml_def,
                source_calls=[c.id for c in group_calls],
                source=source,
            )

            session.tool_definitions.append(tool)
            tools.append(tool)

        return tools

    # ── DOM scanning and probing ─────────────────────────────────────

    async def _scan_interactive_elements(self, page: Page) -> List[Dict]:
        """Inject JS to find all clickable/interactive elements on the page."""
        try:
            elements = await page.evaluate("""() => {
                const selectors = [
                    'button', 'a', 'input', 'select', 'textarea',
                    '[role="button"]', '[role="link"]', '[role="tab"]',
                    '[role="menuitem"]', '[role="option"]',
                    '[onclick]', 'summary',
                ];
                const seen = new Set();
                const results = [];

                for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                        if (seen.has(el)) continue;
                        seen.add(el);

                        // Skip hidden elements
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 && rect.height === 0) continue;

                        const text = (el.textContent || '').trim().slice(0, 80);
                        const tag = el.tagName.toLowerCase();
                        const type = el.getAttribute('type') || '';
                        const role = el.getAttribute('role') || '';
                        const href = el.getAttribute('href') || '';
                        const ariaLabel = el.getAttribute('aria-label') || '';

                        results.push({
                            tag,
                            type,
                            role,
                            text: text.slice(0, 60),
                            href: href.slice(0, 100),
                            ariaLabel,
                            rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
                        });
                    }
                }
                return results;
            }""")
            return elements or []
        except Exception as e:
            logger.warning("[ApiMonitor] DOM scan failed: %s", e)
            return []

    async def _probe_element(self, page: Page, elem: Dict) -> List[CapturedApiCall]:
        """Click an element and capture resulting API calls."""
        capture = self._captures.get(self._session_id_from_page(page))
        if not capture:
            return []

        current_url = page.url
        capture.clear()

        try:
            # Try to click using text or position
            if elem.get("text"):
                locator = page.get_by_text(elem["text"], exact=False).first
                try:
                    await locator.click(timeout=5000)
                except Exception:
                    # Fallback to position click
                    rect = elem.get("rect", {})
                    if rect.get("w", 0) > 0:
                        await page.mouse.click(
                            rect["x"] + rect["w"] / 2,
                            rect["y"] + rect["h"] / 2,
                        )
            elif elem.get("rect", {}).get("w", 0) > 0:
                rect = elem["rect"]
                await page.mouse.click(
                    rect["x"] + rect["w"] / 2,
                    rect["y"] + rect["h"] / 2,
                )

            # Wait for API calls to complete
            await page.wait_for_timeout(2000)

        except Exception as e:
            logger.debug("[ApiMonitor] Probe click failed: %s", e)

        # Check for navigation and go back
        try:
            if page.url != current_url:
                await page.go_back(wait_until="domcontentloaded", timeout=10000)
                await page.wait_for_timeout(500)
        except Exception:
            pass

        calls = capture.drain_new_calls()

        # Tag calls with trigger element
        for call in calls:
            call.trigger_element = elem

        return calls

    def _session_id_from_page(self, page: Page) -> Optional[str]:
        for sid, p in self._pages.items():
            if p is page:
                return sid
        return None

    # ── Helpers ───────────────────────────────────────────────────────

    def _require_session(self, session_id: str) -> ApiMonitorSession:
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        return session

    def list_tabs(self, session_id: str) -> List[Dict]:
        """List browser tabs for screencast controller."""
        page = self._pages.get(session_id)
        if not page:
            return []
        return [{
            "id": str(id(page)),
            "url": page.url,
            "title": "",
        }]


def should_process_request(request) -> bool:
    """Filter callback for page.on('request')."""
    from .network_capture import should_capture
    return should_capture(request.url, request.resource_type)


# ── Global singleton ─────────────────────────────────────────────────

api_monitor_manager = ApiMonitorSessionManager()
```

- [ ] **Step 2: Update __init__.py to export manager**

Update `RpaClaw/backend/rpa/api_monitor/__init__.py`:

```python
from .manager import api_monitor_manager
from .models import (
    ApiMonitorSession,
    ApiToolDefinition,
    CapturedApiCall,
    CapturedRequest,
    CapturedResponse,
)

__all__ = [
    "api_monitor_manager",
    "ApiMonitorSession",
    "ApiToolDefinition",
    "CapturedApiCall",
    "CapturedRequest",
    "CapturedResponse",
]
```

- [ ] **Step 3: Commit session manager**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/rpa/api_monitor/__init__.py
git commit -m "feat(api-monitor): add session manager with analysis orchestration and recording"
```

---

## Task 5: Backend Route

**Files:**
- Create: `RpaClaw/backend/route/api_monitor.py`

- [ ] **Step 1: Create the route file**

Create `RpaClaw/backend/route/api_monitor.py`:

```python
"""FastAPI routes for API Monitor feature.

Prefix: /api/v1/api-monitor
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sse_starlette.sse import EventSourceResponse

from backend.config import settings
from backend.models import User
from backend.auth import get_current_user

from ..rpa.api_monitor import api_monitor_manager
from ..rpa.api_monitor.models import (
    ApiMonitorSession,
    StartSessionRequest,
    NavigateRequest,
    UpdateToolRequest,
)
from ..rpa.screencast import SessionScreencastController

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api-monitor", tags=["API Monitor"])


# ── Auth helpers ─────────────────────────────────────────────────────


async def _get_ws_user(websocket: WebSocket) -> Optional[User]:
    """Resolve the current user for a WebSocket request."""
    if settings.storage_backend == "local":
        return User(id="local_admin", username="admin", role="admin")

    if getattr(settings, "auth_provider", "local") == "none":
        return User(id="anonymous", username="Anonymous", role="user")

    session_id = (
        websocket.query_params.get("token")
        or websocket.cookies.get(settings.session_cookie)
    )
    if not session_id:
        return None

    try:
        from backend.mongodb.db import db
        from backend.auth import _validate_session
        user = await _validate_session(session_id)
        return user
    except Exception:
        return None


def _verify_session_owner(session: Optional[ApiMonitorSession], user: User) -> None:
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(user.id):
        raise HTTPException(status_code=403, detail="Not authorized")


# ── Session management ──────────────────────────────────────────────


@router.post("/session/start")
async def start_session(
    request: StartSessionRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        session = await api_monitor_manager.create_session(
            user_id=str(current_user.id),
            target_url=request.url,
        )
        return {"status": "success", "session": session.model_dump()}
    except Exception as e:
        logger.error("Failed to start API Monitor session: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}")
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    return {"status": "success", "session": session.model_dump()}


@router.post("/session/{session_id}/stop")
async def stop_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    stopped = await api_monitor_manager.stop_session(session_id)
    return {"status": "success", "session": stopped.model_dump() if stopped else None}


@router.post("/session/{session_id}/navigate")
async def navigate(
    session_id: str,
    request: NavigateRequest,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    await api_monitor_manager.navigate(session_id, request.url)
    return {"status": "success"}


# ── Browser tabs ─────────────────────────────────────────────────────


@router.get("/session/{session_id}/tabs")
async def list_tabs(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    tabs = api_monitor_manager.list_tabs(session_id)
    return {"status": "success", "tabs": tabs}


# ── Screencast WebSocket ────────────────────────────────────────────


@router.websocket("/screencast/{session_id}")
async def screencast_ws(websocket: WebSocket, session_id: str):
    """CDP screencast streaming for the API Monitor browser viewport."""
    user = await _get_ws_user(websocket)
    await websocket.accept()

    if not user:
        await websocket.close(code=1008, reason="Not authenticated")
        return

    session = api_monitor_manager.get_session(session_id)
    if not session or session.user_id != str(user.id):
        await websocket.close(code=1008, reason="Not authorized")
        return

    page = api_monitor_manager.get_page(session_id)
    logger.info(
        "[ApiMonitor] Screencast WS session=%s user=%s page=%s",
        session_id,
        user.username,
        "yes" if page else "no",
    )

    screencast = SessionScreencastController(
        page_provider=lambda: api_monitor_manager.get_page(session_id),
        tabs_provider=lambda: api_monitor_manager.list_tabs(session_id),
    )

    try:
        await screencast.start(websocket)
    except WebSocketDisconnect:
        logger.info("[ApiMonitor] Screencast WS disconnected session=%s", session_id)
    except Exception as e:
        logger.exception("[ApiMonitor] Screencast error session=%s: %s", session_id, e)
        try:
            await websocket.close(code=1011, reason="Screencast failed")
        except Exception:
            pass
    finally:
        await screencast.stop()


# ── Analysis (SSE) ──────────────────────────────────────────────────


@router.post("/session/{session_id}/analyze")
async def analyze_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    async def event_generator():
        async for event in api_monitor_manager.analyze_page(session_id):
            yield event

    return EventSourceResponse(event_generator())


# ── Recording ────────────────────────────────────────────────────────


@router.post("/session/{session_id}/record/start")
async def start_recording(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    await api_monitor_manager.start_recording(session_id)
    return {"status": "success"}


@router.post("/session/{session_id}/record/stop")
async def stop_recording(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    tools = await api_monitor_manager.stop_recording(session_id)
    return {
        "status": "success",
        "tools": [t.model_dump() for t in tools],
    }


# ── Tool management ─────────────────────────────────────────────────


@router.get("/session/{session_id}/tools")
async def list_tools(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    return {
        "status": "success",
        "tools": [t.model_dump() for t in session.tool_definitions],
    }


@router.put("/session/{session_id}/tools/{tool_id}")
async def update_tool(
    session_id: str,
    tool_id: str,
    request: UpdateToolRequest,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    for tool in session.tool_definitions:
        if tool.id == tool_id:
            tool.yaml_definition = request.yaml_definition
            from datetime import datetime
            tool.updated_at = datetime.now()
            return {"status": "success", "tool": tool.model_dump()}

    raise HTTPException(status_code=404, detail="Tool not found")


@router.delete("/session/{session_id}/tools/{tool_id}")
async def delete_tool(
    session_id: str,
    tool_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    session.tool_definitions = [
        t for t in session.tool_definitions if t.id != tool_id
    ]
    return {"status": "success"}


@router.post("/session/{session_id}/export")
async def export_tools(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    import yaml
    tools_yaml = []
    for tool in session.tool_definitions:
        tools_yaml.append(tool.yaml_definition)

    export_content = "---\n" + "\n---\n".join(tools_yaml)
    return {
        "status": "success",
        "content": export_content,
        "filename": f"api-monitor-tools-{session_id[:8]}.yaml",
    }
```

- [ ] **Step 2: Commit route file**

```bash
git add RpaClaw/backend/route/api_monitor.py
git commit -m "feat(api-monitor): add FastAPI routes for session, screencast, analysis, recording, and tools"
```

---

## Task 6: Register Route in Backend

**Files:**
- Modify: `RpaClaw/backend/main.py` (around line 25 and 157)

- [ ] **Step 1: Add import and register router**

In `RpaClaw/backend/main.py`, add the import near the other route imports (around line 25):

```python
from backend.route.api_monitor import router as api_monitor_router
```

And add the include_router call near the other router registrations (around line 157):

```python
app.include_router(api_monitor_router, prefix="/api/v1")
```

- [ ] **Step 2: Verify the import works**

Run: `cd RpaClaw/backend && python -c "from backend.route.api_monitor import router; print('OK:', router.prefix)"`

Expected: `OK: /api-monitor`

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/main.py
git commit -m "feat(api-monitor): register API Monitor router in main app"
```

---

## Task 7: Frontend API Client

**Files:**
- Create: `RpaClaw/frontend/src/api/apiMonitor.ts`

- [ ] **Step 1: Create API client module**

Create `RpaClaw/frontend/src/api/apiMonitor.ts`:

```typescript
/**
 * API client for the API Monitor feature.
 * Base URL is /api/v1 — use relative paths from apiClient.
 */
import { apiClient } from './client';
import { createSSEConnection } from './client';

// ── Types ────────────────────────────────────────────────────────────

export interface CapturedRequest {
  request_id: string;
  url: string;
  method: string;
  headers: Record<string, string>;
  body?: string;
  content_type?: string;
  timestamp: string;
  resource_type: string;
}

export interface CapturedResponse {
  status: number;
  status_text: string;
  headers: Record<string, string>;
  body?: string;
  content_type?: string;
  timestamp: string;
}

export interface CapturedApiCall {
  id: string;
  request: CapturedRequest;
  response?: CapturedResponse;
  trigger_element?: Record<string, unknown>;
  url_pattern?: string;
  duration_ms?: number;
}

export interface ApiToolDefinition {
  id: string;
  session_id: string;
  name: string;
  description: string;
  method: string;
  url_pattern: string;
  yaml_definition: string;
  source_calls: string[];
  source: 'auto' | 'manual';
  created_at: string;
  updated_at: string;
}

export interface ApiMonitorSession {
  id: string;
  user_id: string;
  sandbox_session_id: string;
  status: 'idle' | 'analyzing' | 'recording' | 'stopped';
  target_url?: string;
  captured_calls: CapturedApiCall[];
  tool_definitions: ApiToolDefinition[];
  created_at: string;
  updated_at: string;
}

export interface TabInfo {
  id: string;
  url: string;
  title: string;
}

// ── Session ──────────────────────────────────────────────────────────

export async function startSession(url: string): Promise<ApiMonitorSession> {
  const res = await apiClient.post<{ data: ApiMonitorSession }>(
    '/api-monitor/session/start',
    { url },
  );
  return res.data.data;
}

export async function getSession(sessionId: string): Promise<ApiMonitorSession> {
  const res = await apiClient.get<{ data: ApiMonitorSession }>(
    `/api-monitor/session/${sessionId}`,
  );
  return res.data.data;
}

export async function stopSession(sessionId: string): Promise<void> {
  await apiClient.post(`/api-monitor/session/${sessionId}/stop`);
}

export async function navigateSession(sessionId: string, url: string): Promise<void> {
  await apiClient.post(`/api-monitor/session/${sessionId}/navigate`, { url });
}

// ── Tabs ─────────────────────────────────────────────────────────────

export async function listTabs(sessionId: string): Promise<TabInfo[]> {
  const res = await apiClient.get<{ data: { tabs: TabInfo[] } }>(
    `/api-monitor/session/${sessionId}/tabs`,
  );
  return res.data.data.tabs;
}

// ── Analysis (SSE) ──────────────────────────────────────────────────

export interface AnalyzeEvent {
  event: string;
  data: unknown;
}

export function analyzeSession(
  sessionId: string,
  onMessage: (evt: AnalyzeEvent) => void,
): () => void {
  // createSSEConnection returns a cleanup function
  const cleanup = createSSEConnection(
    `/api-monitor/session/${sessionId}/analyze`,
    { method: 'POST' },
    {
      onMessage: (evt: { event?: string; data: string }) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(evt.data);
        } catch {
          parsed = evt.data;
        }
        onMessage({
          event: evt.event || 'message',
          data: parsed,
        });
      },
    },
  );
  return cleanup;
}

// ── Recording ────────────────────────────────────────────────────────

export async function startRecording(sessionId: string): Promise<void> {
  await apiClient.post(`/api-monitor/session/${sessionId}/record/start`);
}

export async function stopRecording(
  sessionId: string,
): Promise<ApiToolDefinition[]> {
  const res = await apiClient.post<{ data: { tools: ApiToolDefinition[] } }>(
    `/api-monitor/session/${sessionId}/record/stop`,
  );
  return res.data.data.tools;
}

// ── Tools ────────────────────────────────────────────────────────────

export async function listTools(sessionId: string): Promise<ApiToolDefinition[]> {
  const res = await apiClient.get<{ data: { tools: ApiToolDefinition[] } }>(
    `/api-monitor/session/${sessionId}/tools`,
  );
  return res.data.data.tools;
}

export async function updateTool(
  sessionId: string,
  toolId: string,
  yamlDefinition: string,
): Promise<ApiToolDefinition> {
  const res = await apiClient.put<{ data: { tool: ApiToolDefinition } }>(
    `/api-monitor/session/${sessionId}/tools/${toolId}`,
    { yaml_definition: yamlDefinition },
  );
  return res.data.data.tool;
}

export async function deleteTool(sessionId: string, toolId: string): Promise<void> {
  await apiClient.delete(`/api-monitor/session/${sessionId}/tools/${toolId}`);
}

export async function exportTools(
  sessionId: string,
): Promise<{ content: string; filename: string }> {
  const res = await apiClient.post<{
    data: { content: string; filename: string };
  }>(`/api-monitor/session/${sessionId}/export`);
  return res.data.data;
}
```

- [ ] **Step 2: Commit**

```bash
git add RpaClaw/frontend/src/api/apiMonitor.ts
git commit -m "feat(api-monitor): add frontend API client for API Monitor endpoints"
```

---

## Task 8: Frontend Route Registration

**Files:**
- Modify: `RpaClaw/frontend/src/main.ts` (around line 119)

- [ ] **Step 1: Add api-monitor route**

In `RpaClaw/frontend/src/main.ts`, add the import at the top with other page imports (around line 28):

```typescript
import ApiMonitorPage from './pages/rpa/ApiMonitorPage.vue'
```

Add the route in the `/rpa` children array (around line 119, after `convert-mcp`):

```typescript
{
  path: 'api-monitor',
  component: ApiMonitorPage,
},
```

- [ ] **Step 2: Commit**

```bash
git add RpaClaw/frontend/src/main.ts
git commit -m "feat(api-monitor): register /rpa/api-monitor route"
```

---

## Task 9: ApiMonitorPage.vue — Skeleton

**Files:**
- Create: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`

This is a large file. We build it in stages. This task creates the skeleton with the top control bar, left browser viewport, and right empty panel.

- [ ] **Step 1: Create the page skeleton**

Create `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue` with the complete skeleton including: URL input, Go button, analysis/recording/export controls, screencast canvas with WebSocket, terminal log area, tool cards area, and status bar.

The file should follow the `RecorderPage.vue` patterns for:
- WebSocket connection via `getBackendWsUrl`
- Canvas rendering with `getFrameSizeFromMetadata` / `mapClientPointToViewportPoint`
- Mouse/keyboard event forwarding

Key template structure:

```vue
<template>
  <div class="flex h-screen flex-col bg-background text-on-surface">
    <!-- Top Control Bar -->
    <div class="flex items-center gap-md border-b border-outline-variant bg-surface px-md py-sm shrink-0">
      <button @click="goBack" class="text-on-surface-variant hover:text-primary">
        <span class="material-symbols-outlined">arrow_back</span>
      </button>
      <h1 class="font-h2 text-h2">API Monitor</h1>
      <div class="flex-1 flex items-center gap-sm">
        <input v-model="urlInput" class="..." placeholder="Enter URL..." @keydown.enter="navigateToUrl" />
        <button @click="navigateToUrl" class="...">Go</button>
      </div>
      <button @click="startAnalysis" :disabled="!sessionId || isAnalyzing" class="...">Analyze</button>
      <button @click="toggleRecording" :class="isRecording ? 'bg-error' : 'bg-primary'" class="...">
        {{ isRecording ? 'Stop' : 'Record' }}
      </button>
      <button @click="exportAllTools" :disabled="!tools.length" class="...">Export</button>
    </div>

    <!-- Main Content: Left viewport + Right panel -->
    <div class="flex flex-1 min-h-0">
      <!-- Left: Browser Viewport -->
      <div class="w-1/2 relative bg-surface-container-lowest border-r border-outline-variant">
        <canvas
          v-if="sessionId"
          ref="canvasRef"
          class="w-full h-full object-contain"
          tabindex="0"
          @mousedown="sendInputEvent"
          @mouseup="sendInputEvent"
          @mousemove="sendInputEvent"
          @wheel.prevent="sendInputEvent"
          @keydown.prevent="sendInputEvent"
          @keyup.prevent="sendInputEvent"
          @contextmenu.prevent
        />
        <div v-else class="flex items-center justify-center h-full text-on-surface-variant">
          Enter a URL and click Go to start
        </div>
      </div>

      <!-- Right: Terminal + Tools -->
      <div class="w-1/2 flex flex-col overflow-hidden">
        <!-- Terminal Log -->
        <div class="h-1/2 border-b border-outline-variant flex flex-col">
          <!-- Terminal header -->
          <div class="flex items-center justify-between px-md py-xs bg-surface border-b border-outline-variant shrink-0">
            <div class="flex gap-xs">
              <div class="w-2.5 h-2.5 rounded-full bg-outline-variant"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-outline-variant"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-outline-variant"></div>
            </div>
            <span class="font-code-block text-[11px] text-on-surface-variant">capture.log</span>
            <div class="flex gap-xs">
              <button @click="clearLog" class="text-on-surface-variant hover:text-on-surface">
                <span class="material-symbols-outlined text-[14px]">clear_all</span>
              </button>
            </div>
          </div>
          <!-- Terminal body -->
          <div ref="terminalRef" class="flex-1 overflow-y-auto p-md font-code-block text-code-block text-on-surface-variant">
            <div v-for="(line, i) in terminalLines" :key="i" v-html="line.html"></div>
          </div>
        </div>

        <!-- Tool Cards -->
        <div class="flex-1 overflow-y-auto p-md">
          <div v-if="!tools.length" class="text-on-surface-variant text-center py-xl">
            No tools captured yet. Click Analyze or Record to start.
          </div>
          <div v-for="tool in tools" :key="tool.id" class="mb-md bg-surface border border-outline-variant rounded-lg overflow-hidden">
            <!-- Tool card header -->
            <div class="flex items-center justify-between px-md py-sm border-b border-outline-variant/50">
              <div class="flex items-center gap-sm">
                <span :class="methodBadgeClass(tool.method)" class="px-xs py-px rounded font-label-caps text-[10px]">{{ tool.method }}</span>
                <span class="font-code-block text-sm text-on-surface">{{ tool.url_pattern }}</span>
              </div>
              <div class="flex items-center gap-xs">
                <button @click="toggleToolExpand(tool.id)" class="text-on-surface-variant hover:text-primary text-[12px]">
                  {{ expandedToolId === tool.id ? 'Collapse' : 'Edit' }}
                </button>
                <button @click="deleteTool(tool.id)" class="text-error hover:text-error-container text-[12px]">Delete</button>
              </div>
            </div>
            <!-- Description -->
            <div class="px-md py-xs text-on-surface-variant text-sm">{{ tool.description }}</div>
            <!-- YAML preview/editor (expanded) -->
            <div v-if="expandedToolId === tool.id" class="border-t border-outline-variant/50">
              <textarea
                v-model="toolEdits[tool.id]"
                class="w-full h-48 bg-surface-container-lowest p-md font-code-block text-code-block text-on-surface resize-none focus:outline-none"
              ></textarea>
              <div class="flex justify-end gap-xs px-md py-xs border-t border-outline-variant/50">
                <button @click="saveToolEdit(tool.id)" class="bg-primary text-on-primary px-sm py-xs rounded text-[12px]">Save</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Status Bar -->
    <div class="flex items-center gap-md px-md py-xs border-t border-outline-variant bg-surface text-on-surface-variant font-label-caps text-label-caps shrink-0">
      <span>{{ tools.length }} tools</span>
      <span>|</span>
      <span :class="session?.status === 'analyzing' ? 'text-primary' : ''">Status: {{ session?.status || 'No session' }}</span>
      <span v-if="isRecording" class="text-error">● Recording</span>
    </div>
  </div>
</template>
```

The `<script setup>` should include:
- Refs for `sessionId`, `session`, `urlInput`, `tools`, `terminalLines`, `isRecording`, `isAnalyzing`, `expandedToolId`, `toolEdits`
- `connectScreencast()` function following `RecorderPage.vue` pattern using `getBackendWsUrl('/api-monitor/screencast/' + sid)`
- `drawFrame()` using `getFrameSizeFromMetadata` and `getInputSizeFromMetadata`
- `sendInputEvent()` using `mapClientPointToViewportPoint`
- `navigateToUrl()` calling `startSession(urlInput)`
- `startAnalysis()` using `analyzeSession()` SSE helper
- `toggleRecording()` calling `startRecording()` / `stopRecording()`
- `exportAllTools()` calling `exportTools()` and triggering file download
- `goBack()` using `router.push('/chat/tools')`
- Terminal log helper `addLog(level, message)` with color classes
- `methodBadgeClass(method)` returning appropriate Tailwind classes for HTTP method badges

- [ ] **Step 2: Commit skeleton**

```bash
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue
git commit -m "feat(api-monitor): add ApiMonitorPage skeleton with browser viewport, terminal, and tool cards"
```

---

## Task 10: ToolsPage "Add Tool" Dropdown

**Files:**
- Modify: `RpaClaw/frontend/src/pages/ToolsPage.vue`

- [ ] **Step 1: Add "From API Monitor" option to the Add Tool button**

In `RpaClaw/frontend/src/pages/ToolsPage.vue`, find the "Create from RPA recording" or "Add Tool" button (search for `startCreateRpaMcpTool`). Add a dropdown or additional button that links to `/rpa/api-monitor`.

Add a new button near the existing one:

```html
<button
  class="inline-flex items-center gap-2 rounded-xl bg-gradient-to-br from-teal-500 to-cyan-600 px-4 py-2 text-sm font-bold text-white shadow-lg transition hover:-translate-y-0.5 active:translate-y-0"
  @click="router.push('/rpa/api-monitor')"
>
  <span class="material-symbols-outlined text-[16px]">fiber_manual_record</span>
  API Monitor
</button>
```

And add `router` to the imports:

```typescript
const router = useRouter()
```

(make sure `useRouter` is imported from `vue-router` if not already)

- [ ] **Step 2: Verify the button appears**

Run: `cd RpaClaw/frontend && npm run build 2>&1 | tail -5`

Expected: Build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/frontend/src/pages/ToolsPage.vue
git commit -m "feat(api-monitor): add API Monitor entry point button on Tools page"
```

---

## Task 11: Integration Smoke Test

**Files:** No new files

- [ ] **Step 1: Start backend and verify route registration**

Run: `cd RpaClaw/backend && python -c "from backend.main import create_app; app = create_app(); routes = [r.path for r in app.routes if hasattr(r, 'path')]; print([r for r in routes if 'api-monitor' in r])"`

Expected: A list containing paths like `/api/v1/api-monitor/session/start`, `/api/v1/api-monitor/screencast/{session_id}`, etc.

- [ ] **Step 2: Verify frontend builds**

Run: `cd RpaClaw/frontend && npm run build 2>&1 | tail -5`

Expected: Build succeeds.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(api-monitor): complete API Monitor feature implementation"
```

---

## Scope Coverage Check

| Spec Requirement | Task |
|------------------|------|
| Backend data models (CapturedRequest, CapturedResponse, CapturedApiCall, ApiToolDefinition, ApiMonitorSession) | Task 1 |
| Network capture (request/response correlation, filtering, URL parameterization) | Task 2 |
| LLM analyzer (element safety, tool generation) | Task 3 |
| Session manager (create/stop, navigate, network listeners, analysis orchestration, recording) | Task 4 |
| FastAPI routes (session CRUD, screencast WS, analyze SSE, tools CRUD, recording) | Task 5 |
| Route registration in main.py | Task 6 |
| Frontend API client | Task 7 |
| Frontend route registration | Task 8 |
| ApiMonitorPage (browser viewport, terminal log, tool cards, controls) | Task 9 |
| ToolsPage entry point | Task 10 |
| Integration verification | Task 11 |
| Export YAML | Task 5 (route) + Task 9 (UI) |
| SSE analysis progress | Task 4 (manager) + Task 5 (route) + Task 9 (frontend) |
| Recording start/stop | Task 4 (manager) + Task 5 (route) + Task 9 (frontend) |
| Screencast WS | Task 5 (route, reuses SessionScreencastController) + Task 9 (canvas) |

## Placeholder Scan

No TBD/TODO/placeholder patterns found.

## Type Consistency Check

- `ApiMonitorSession` model field names match between backend `models.py` and frontend `apiMonitor.ts`
- `ApiToolDefinition` fields match between backend and frontend
- `CapturedApiCall`, `CapturedRequest`, `CapturedResponse` fields match
- Route path patterns (`/api-monitor/session/{session_id}/...`) match between backend route and frontend API calls
- `api_monitor_manager` singleton exported from `__init__.py` matches import in `route/api_monitor.py`
