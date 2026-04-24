# API Monitor Confidence Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 API Monitor 采集结果增加置信度、证据原因和采用状态，高置信候选 API 默认采用，中低置信默认不采用，发布 MCP 时只发布用户采用的 API。

**Architecture:** 后端把原始网络请求保留为样本，在分组生成 `ApiToolDefinition` 时计算 `confidence`、`confidence_reasons`、`source_evidence` 和 `selected`。采集层补充 CDP initiator、页面 fetch/XHR stack、动作时间窗口证据；路由层提供采用状态更新接口；发布层只发布 `selected=true` 的候选工具。前端把工具列表拆成“采用/不采用”两组，展示置信度和原因，并允许用户切换采用状态。

**Tech Stack:** FastAPI, Pydantic v2, Playwright/CDP, pytest, Vue 3, TypeScript, Vite, Tailwind CSS.

---

## File Map

- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
  - Add confidence/selection/evidence fields to captured calls and tool definitions.
  - Add request schema for updating selection.
- Create: `RpaClaw/backend/rpa/api_monitor/confidence.py`
  - Rule-based confidence scorer and evidence helpers.
- Modify: `RpaClaw/backend/rpa/api_monitor/network_capture.py`
  - Store optional source evidence and action-window match on captured calls.
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
  - Track action windows.
  - Attach CDP initiator evidence.
  - Inject fetch/XHR stack recorder.
  - Score grouped tool definitions.
- Modify: `RpaClaw/backend/route/api_monitor.py`
  - Add `PATCH /session/{session_id}/tools/{tool_id}/selection`.
- Modify: `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`
  - Publish selected tools only and keep `tool_count` consistent.
- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts`
  - Add confidence/selection types and `updateToolSelection()`.
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
  - Group tools by adoption state.
  - Render confidence badges/reasons.
  - Toggle selected state.
  - Disable publish when no adopted tools exist.
- Test: `RpaClaw/backend/tests/test_api_monitor_confidence.py`
  - New unit tests for scorer.
- Test: `RpaClaw/backend/tests/test_api_monitor_capture.py`
  - Add evidence/action-window capture tests.
- Test: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`
  - Add selected-only publish tests.
- Test: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`
  - Add selection endpoint test alongside existing API Monitor route tests in that file.
- Test: frontend validation uses `npm run build` from `RpaClaw/frontend`.

---

### Task 1: Add Data Fields and Confidence Scorer

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
- Create: `RpaClaw/backend/rpa/api_monitor/confidence.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_confidence.py`

- [ ] **Step 1: Write failing confidence scorer tests**

Create `RpaClaw/backend/tests/test_api_monitor_confidence.py`:

```python
from datetime import datetime

from backend.rpa.api_monitor.confidence import classify_api_candidate
from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest, CapturedResponse


def _call(
    url: str,
    *,
    action_window_matched: bool = True,
    initiator_urls: list[str] | None = None,
    js_stack_urls: list[str] | None = None,
    body: str = '{"items":[]}',
) -> CapturedApiCall:
    return CapturedApiCall(
        request=CapturedRequest(
            request_id="req_1",
            url=url,
            method="GET",
            headers={"accept": "application/json"},
            timestamp=datetime(2026, 1, 1),
            resource_type="fetch",
        ),
        response=CapturedResponse(
            status=200,
            status_text="OK",
            headers={"content-type": "application/json"},
            body=body,
            content_type="application/json",
            timestamp=datetime(2026, 1, 1),
        ),
        source_evidence={
            "initiator_type": "script",
            "initiator_urls": initiator_urls or [],
            "js_stack_urls": js_stack_urls or [],
            "frame_url": "https://example.com/app",
            "action_window_matched": action_window_matched,
        },
    )


def test_business_api_with_page_script_is_high_and_selected():
    result = classify_api_candidate([
        _call(
            "https://example.com/api/orders",
            initiator_urls=["https://example.com/app/assets/main.js"],
        )
    ])

    assert result.confidence == "high"
    assert result.selected is True
    assert "由用户动作触发" in result.reasons
    assert "由页面业务脚本发起" in result.reasons


def test_config_query_from_injected_stack_is_low_and_not_selected():
    result = classify_api_candidate([
        _call(
            "https://example.com/hicweb/services/hic.config.queryConfig?class_code=his.evaluation.modelAlias",
            initiator_urls=["chrome-extension://abc/injected.js"],
            js_stack_urls=["chrome-extension://abc/injected.js"],
        )
    ])

    assert result.confidence == "low"
    assert result.selected is False
    assert "路径疑似配置或后台请求" in result.reasons
    assert "来源疑似注入脚本或扩展" in result.reasons


def test_missing_source_evidence_is_medium_and_not_selected():
    result = classify_api_candidate([
        _call("https://example.com/api/orders", initiator_urls=[], js_stack_urls=[])
    ])

    assert result.confidence == "medium"
    assert result.selected is False
    assert "缺少 initiator 或 JS 调用栈" in result.reasons
```

- [ ] **Step 2: Run scorer tests to verify they fail**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest backend/tests/test_api_monitor_confidence.py -q
```

Expected: FAIL because `backend.rpa.api_monitor.confidence` does not exist.

- [ ] **Step 3: Add model fields**

Modify `RpaClaw/backend/rpa/api_monitor/models.py`:

```python
from typing import Dict, List, Literal, Optional
```

Add near captured models:

```python
ConfidenceLevel = Literal["high", "medium", "low"]
```

Update `CapturedApiCall`:

```python
class CapturedApiCall(BaseModel):
    id: str = Field(default_factory=_gen_id)
    request: CapturedRequest
    response: Optional[CapturedResponse] = None
    trigger_element: Optional[Dict] = None
    url_pattern: Optional[str] = None
    duration_ms: Optional[float] = None
    source_evidence: Dict = Field(default_factory=dict)
```

Update `ApiToolDefinition`:

```python
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
    source: str = "auto"
    confidence: ConfidenceLevel = "medium"
    selected: bool = False
    confidence_reasons: List[str] = Field(default_factory=list)
    source_evidence: Dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

Add request schema:

```python
class UpdateToolSelectionRequest(BaseModel):
    selected: bool
```

- [ ] **Step 4: Implement confidence scorer**

Create `RpaClaw/backend/rpa/api_monitor/confidence.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from .models import CapturedApiCall

ConfidenceLevel = Literal["high", "medium", "low"]

NOISE_PATH_MARKERS = (
    "config",
    "queryconfig",
    "telemetry",
    "collect",
    "track",
    "metrics",
    "heartbeat",
    "ping",
    "log",
    "rum",
    "modelalias",
)

BUSINESS_PATH_MARKERS = (
    "/api/",
    "/biz/",
    "/v1/",
    "/v2/",
    "/graphql",
)

INJECTED_SOURCE_MARKERS = (
    "chrome-extension://",
    "moz-extension://",
    "safari-extension://",
    "userscript",
    "injected",
    "eval",
    "webpack://",
    "VM",
)


@dataclass(frozen=True)
class ConfidenceResult:
    confidence: ConfidenceLevel
    selected: bool
    reasons: list[str]
    evidence_summary: dict


def classify_api_candidate(calls: list[CapturedApiCall]) -> ConfidenceResult:
    first = calls[0]
    evidence = _merge_evidence(calls)
    reasons: list[str] = []

    path = urlparse(first.request.url).path.lower()
    body = (first.response.body if first.response else "") or ""
    content_type = ((first.response.content_type if first.response else "") or "").lower()
    action_window_matched = bool(evidence.get("action_window_matched"))
    source_urls = [
        *evidence.get("initiator_urls", []),
        *evidence.get("js_stack_urls", []),
    ]

    has_source = bool(source_urls)
    injected_source = any(_contains_marker(url, INJECTED_SOURCE_MARKERS) for url in source_urls)
    noise_path = any(marker in path for marker in NOISE_PATH_MARKERS)
    business_path = any(marker in path for marker in BUSINESS_PATH_MARKERS)
    json_response = "json" in content_type or body.strip().startswith(("{", "["))

    if action_window_matched:
        reasons.append("由用户动作触发")
    else:
        reasons.append("不在动作时间窗口内")

    if injected_source:
        reasons.append("来源疑似注入脚本或扩展")
    elif has_source:
        reasons.append("由页面业务脚本发起")
    else:
        reasons.append("缺少 initiator 或 JS 调用栈")

    if noise_path:
        reasons.append("路径疑似配置或后台请求")
    elif business_path:
        reasons.append("路径疑似业务接口")

    if json_response:
        reasons.append("响应疑似 JSON 业务数据")

    if injected_source or noise_path or not action_window_matched:
        return ConfidenceResult("low", False, _dedupe(reasons), evidence)

    if action_window_matched and has_source and business_path and json_response:
        return ConfidenceResult("high", True, _dedupe(reasons), evidence)

    return ConfidenceResult("medium", False, _dedupe(reasons), evidence)


def _merge_evidence(calls: list[CapturedApiCall]) -> dict:
    initiator_urls: list[str] = []
    js_stack_urls: list[str] = []
    action_window_matched = False
    frame_url = ""
    initiator_type = ""

    for call in calls:
        evidence = call.source_evidence or {}
        initiator_urls.extend(str(url) for url in evidence.get("initiator_urls", []) if url)
        js_stack_urls.extend(str(url) for url in evidence.get("js_stack_urls", []) if url)
        action_window_matched = action_window_matched or bool(evidence.get("action_window_matched"))
        frame_url = frame_url or str(evidence.get("frame_url") or "")
        initiator_type = initiator_type or str(evidence.get("initiator_type") or "")

    return {
        "initiator_type": initiator_type,
        "initiator_urls": _dedupe(initiator_urls),
        "js_stack_urls": _dedupe(js_stack_urls),
        "frame_url": frame_url,
        "action_window_matched": action_window_matched,
    }


def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
    lower = value.lower()
    return any(marker.lower() in lower for marker in markers)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
```

- [ ] **Step 5: Run scorer tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest backend/tests/test_api_monitor_confidence.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/rpa/api_monitor/confidence.py RpaClaw/backend/tests/test_api_monitor_confidence.py
git commit -m "feat: add api monitor confidence model"
```

---

### Task 2: Attach Capture Evidence and Action Windows

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/network_capture.py`
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_capture.py`

- [ ] **Step 1: Write failing capture evidence test**

Append to `RpaClaw/backend/tests/test_api_monitor_capture.py`:

```python
class _Request:
    url = "https://example.com/api/orders"
    method = "GET"
    headers = {"accept": "application/json"}
    resource_type = "fetch"
    post_data = None


class TestCaptureEvidence:
    def test_on_request_stores_source_evidence(self):
        from backend.rpa.api_monitor.network_capture import NetworkCaptureEngine

        engine = NetworkCaptureEngine(
            page_url_provider=lambda: "https://example.com/app",
            evidence_provider=lambda request: {
                "initiator_type": "script",
                "initiator_urls": ["https://example.com/app/assets/main.js"],
                "js_stack_urls": ["https://example.com/app/assets/main.js"],
                "frame_url": "https://example.com/app",
                "action_window_matched": True,
            },
        )

        request = _Request()
        engine.on_request(request)

        stored = engine._in_flight[id(request)]["request"]
        evidence = engine._in_flight[id(request)]["source_evidence"]

        assert stored.url == "https://example.com/api/orders"
        assert evidence["initiator_type"] == "script"
        assert evidence["action_window_matched"] is True
```

- [ ] **Step 2: Run capture test to verify it fails**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest backend/tests/test_api_monitor_capture.py::TestCaptureEvidence::test_on_request_stores_source_evidence -q
```

Expected: FAIL because `NetworkCaptureEngine` has no `evidence_provider`.

- [ ] **Step 3: Update NetworkCaptureEngine to accept evidence provider**

Modify `RpaClaw/backend/rpa/api_monitor/network_capture.py`:

```python
class NetworkCaptureEngine:
    def __init__(
        self,
        page_url_provider: Optional[Callable[[], str]] = None,
        evidence_provider: Optional[Callable[[object], Dict]] = None,
    ) -> None:
        self._in_flight: Dict[int, Dict] = {}
        self._captured_calls: List[CapturedApiCall] = []
        self._page_url_provider = page_url_provider
        self._evidence_provider = evidence_provider
        self.on_log: Optional[Callable[[str, str], None]] = None
```

In `on_request()`, after `captured_req` creation:

```python
        source_evidence = self._source_evidence(request)

        self._in_flight[id(request)] = {
            "request": captured_req,
            "start_time": time.monotonic(),
            "source_evidence": source_evidence,
        }
```

Add helper:

```python
    def _source_evidence(self, request) -> Dict:
        if not self._evidence_provider:
            return {}
        try:
            return self._evidence_provider(request) or {}
        except Exception as exc:
            logger.debug("[ApiMonitor] Failed to read request evidence: %s", exc)
            return {}
```

In `on_response()`, read evidence and attach to call:

```python
        source_evidence: Dict = info.get("source_evidence") or {}
```

```python
        call = CapturedApiCall(
            request=captured_req,
            response=captured_resp,
            url_pattern=parameterize_url(captured_req.url),
            duration_ms=round(duration_ms, 1),
            source_evidence=source_evidence,
        )
```

- [ ] **Step 4: Add manager evidence stores**

Modify `RpaClaw/backend/rpa/api_monitor/manager.py`:

Add instance state in `ApiMonitorSessionManager.__init__`:

```python
self._request_evidence: Dict[str, Dict[str, Dict]] = {}
self._last_action_at: Dict[str, float] = {}
```

In `create_session()`, initialize:

```python
self._request_evidence[session_id] = {}
```

Create capture engine with evidence provider:

```python
capture = NetworkCaptureEngine(
    page_url_provider=_capture_page_url,
    evidence_provider=lambda request: self._evidence_for_request(session_id, request),
)
```

In `stop_session()`, cleanup:

```python
self._request_evidence.pop(session_id, None)
self._last_action_at.pop(session_id, None)
```

Add helpers:

```python
def _mark_action(self, session_id: str) -> None:
    self._last_action_at[session_id] = time.monotonic()

def _action_window_matched(self, session_id: str, window_seconds: float = 2.0) -> bool:
    last_action_at = self._last_action_at.get(session_id)
    return last_action_at is not None and (time.monotonic() - last_action_at) <= window_seconds

def _evidence_for_request(self, session_id: str, request) -> Dict:
    by_url = self._request_evidence.get(session_id, {})
    evidence = dict(by_url.get(request.url) or {})
    evidence.setdefault("frame_url", self.sessions.get(session_id).target_url if self.sessions.get(session_id) else "")
    evidence["action_window_matched"] = self._action_window_matched(session_id)
    return evidence
```

Call `_mark_action(session_id)` at the start of manual recording and before each probe click:

```python
await api_monitor_manager.start_recording(session_id)
```

should remain route-level; in manager `start_recording()`, add:

```python
self._mark_action(session_id)
```

In `_probe_element()`, before `locator.click()`:

```python
session_id = self._session_id_from_page(page) or ""
self._mark_action(session_id)
```

- [ ] **Step 5: Run capture tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest backend/tests/test_api_monitor_capture.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/rpa/api_monitor/network_capture.py RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_capture.py
git commit -m "feat: attach api monitor request evidence"
```

---

### Task 3: Capture CDP Initiator and Fetch/XHR Stack Evidence

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_capture.py`

- [ ] **Step 1: Write helper tests for evidence normalization**

Append to `RpaClaw/backend/tests/test_api_monitor_capture.py`:

```python
class TestSourceEvidenceHelpers:
    def test_extract_initiator_urls_from_cdp_stack(self):
        from backend.rpa.api_monitor.manager import _initiator_to_evidence

        evidence = _initiator_to_evidence({
            "type": "script",
            "stack": {
                "callFrames": [
                    {"url": "https://example.com/app/assets/main.js", "functionName": "load"},
                    {"url": "chrome-extension://abc/injected.js", "functionName": "run"},
                ]
            },
        })

        assert evidence["initiator_type"] == "script"
        assert evidence["initiator_urls"] == [
            "https://example.com/app/assets/main.js",
            "chrome-extension://abc/injected.js",
        ]

    def test_extract_stack_urls_from_js_error_stack(self):
        from backend.rpa.api_monitor.manager import _stack_to_urls

        urls = _stack_to_urls(
            "Error\n"
            " at fetchData (https://example.com/app/assets/main.js:10:1)\n"
            " at run (chrome-extension://abc/injected.js:2:3)\n"
        )

        assert "https://example.com/app/assets/main.js" in urls
        assert "chrome-extension://abc/injected.js" in urls
```

- [ ] **Step 2: Run helper tests to verify they fail**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest backend/tests/test_api_monitor_capture.py::TestSourceEvidenceHelpers -q
```

Expected: FAIL because helpers do not exist.

- [ ] **Step 3: Add helper functions**

Add near the top-level helper section in `RpaClaw/backend/rpa/api_monitor/manager.py`:

```python
_STACK_URL_RE = re.compile(r"(?:https?|chrome-extension|moz-extension|safari-extension)://[^\s)]+")


def _initiator_to_evidence(initiator: Dict) -> Dict:
    urls: List[str] = []
    stack = initiator.get("stack") or {}
    for frame in stack.get("callFrames") or []:
        url = frame.get("url")
        if url:
            urls.append(url)
    return {
        "initiator_type": initiator.get("type") or "",
        "initiator_urls": _dedupe_strings(urls),
    }


def _stack_to_urls(stack: str) -> List[str]:
    return _dedupe_strings(_STACK_URL_RE.findall(stack or ""))


def _dedupe_strings(values: List[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
```

Ensure imports include:

```python
import re
import time
```

- [ ] **Step 4: Install CDP listener in create_session**

In `create_session()`, after page creation and before navigation:

```python
await self._install_source_evidence_capture(session_id, context, page)
```

Add method:

```python
async def _install_source_evidence_capture(self, session_id: str, context, page: Page) -> None:
    try:
        cdp = await context.new_cdp_session(page)
        await cdp.send("Network.enable")

        def on_request_will_be_sent(event: Dict) -> None:
            request = event.get("request") or {}
            url = request.get("url") or ""
            if not url:
                return
            evidence = _initiator_to_evidence(event.get("initiator") or {})
            evidence["frame_url"] = page.url
            self._request_evidence.setdefault(session_id, {})[url] = evidence

        cdp.on("Network.requestWillBeSent", on_request_will_be_sent)
    except Exception as exc:
        logger.debug("[ApiMonitor] CDP source evidence capture unavailable: %s", exc)

    try:
        await page.add_init_script(_FETCH_XHR_STACK_CAPTURE_JS)
    except Exception as exc:
        logger.debug("[ApiMonitor] Fetch/XHR stack capture injection failed: %s", exc)
```

Add JS constant:

```python
_FETCH_XHR_STACK_CAPTURE_JS = r"""
(() => {
  if (window.__apiMonitorStackCaptureInstalled) return;
  window.__apiMonitorStackCaptureInstalled = true;
  window.__apiMonitorStacks = [];

  const record = (method, url) => {
    try {
      window.__apiMonitorStacks.push({
        method: String(method || 'GET').toUpperCase(),
        url: String(url || ''),
        timestamp: Date.now(),
        stack: new Error().stack || '',
        frameUrl: window.location.href,
      });
      if (window.__apiMonitorStacks.length > 500) {
        window.__apiMonitorStacks.splice(0, window.__apiMonitorStacks.length - 500);
      }
    } catch (_) {}
  };

  const originalFetch = window.fetch;
  window.fetch = function(input, init) {
    const url = typeof input === 'string' ? input : input && input.url;
    const method = init && init.method ? init.method : input && input.method ? input.method : 'GET';
    record(method, url);
    return originalFetch.apply(this, arguments);
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__apiMonitorMethod = method;
    this.__apiMonitorUrl = url;
    return originalOpen.apply(this, arguments);
  };

  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function() {
    record(this.__apiMonitorMethod || 'GET', this.__apiMonitorUrl || '');
    return originalSend.apply(this, arguments);
  };
})();
"""
```

- [ ] **Step 5: Correlate JS stack evidence during response handling**

Extend `NetworkCaptureEngine` in `RpaClaw/backend/rpa/api_monitor/network_capture.py` with an async evidence provider:

```python
from typing import Awaitable
```

```python
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
        self.on_log: Optional[Callable[[str, str], None]] = None
```

Add helper:

```python
    async def _async_source_evidence(self, request) -> Dict:
        if not self._async_evidence_provider:
            return {}
        try:
            return await self._async_evidence_provider(request) or {}
        except Exception as exc:
            logger.debug("[ApiMonitor] Failed to read async request evidence: %s", exc)
            return {}
```

In `on_response()`, merge synchronous CDP evidence with async JS stack evidence before creating `CapturedApiCall`:

```python
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
```

Pass the async provider from `create_session()`:

```python
capture = NetworkCaptureEngine(
    page_url_provider=_capture_page_url,
    evidence_provider=lambda request: self._evidence_for_request(session_id, request),
    async_evidence_provider=lambda request: self._async_evidence_for_request(session_id, request),
)
```

Add manager method:

```python
async def _async_evidence_for_request(self, session_id: str, request) -> Dict:
    page = self._pages.get(session_id)
    if not page:
        return {}
    stack_record = await page.evaluate(
        """({url, method}) => {
          const records = window.__apiMonitorStacks || [];
          for (let i = records.length - 1; i >= 0; i--) {
            const item = records[i];
            if (item.url === url && item.method === method.toUpperCase()) return item;
          }
          return null;
        }""",
        {"url": request.url, "method": request.method},
    )
    if not stack_record:
        return {}
    return {
        "js_stack_urls": _stack_to_urls(stack_record.get("stack") or ""),
        "frame_url": stack_record.get("frameUrl") or page.url,
    }
```

This keeps all Playwright `page.evaluate()` work inside async response handling and avoids blocking the synchronous `page.on("request")` callback.

- [ ] **Step 6: Run helper tests and capture tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest backend/tests/test_api_monitor_capture.py backend/tests/test_api_monitor_confidence.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/rpa/api_monitor/network_capture.py RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_capture.py
git commit -m "feat: capture api monitor initiator evidence"
```

---

### Task 4: Score Generated API Tool Candidates

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_confidence.py`

- [ ] **Step 1: Write failing manager scoring test**

Append to `RpaClaw/backend/tests/test_api_monitor_confidence.py`:

```python
def test_apply_confidence_to_tool_definition():
    from backend.rpa.api_monitor.manager import _apply_confidence_to_tool
    from backend.rpa.api_monitor.models import ApiToolDefinition

    tool = ApiToolDefinition(
        session_id="session_1",
        name="list_orders",
        description="List orders",
        method="GET",
        url_pattern="/api/orders",
        yaml_definition="name: list_orders\nmethod: GET\nurl: /api/orders\n",
        source_calls=["call_1"],
    )

    call = _call(
        "https://example.com/api/orders",
        initiator_urls=["https://example.com/app/assets/main.js"],
    )
    updated = _apply_confidence_to_tool(tool, [call])

    assert updated.confidence == "high"
    assert updated.selected is True
    assert updated.confidence_reasons
    assert updated.source_evidence["action_window_matched"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest backend/tests/test_api_monitor_confidence.py::test_apply_confidence_to_tool_definition -q
```

Expected: FAIL because `_apply_confidence_to_tool` does not exist.

- [ ] **Step 3: Add scoring helper**

In `RpaClaw/backend/rpa/api_monitor/manager.py`, import:

```python
from .confidence import classify_api_candidate
```

Add top-level helper:

```python
def _apply_confidence_to_tool(
    tool: ApiToolDefinition,
    calls: List[CapturedApiCall],
) -> ApiToolDefinition:
    result = classify_api_candidate(calls)
    tool.confidence = result.confidence
    tool.selected = result.selected
    tool.confidence_reasons = result.reasons
    tool.source_evidence = result.evidence_summary
    return tool
```

- [ ] **Step 4: Apply scoring during tool generation**

In `_generate_tools_from_calls()`, after creating `tool = ApiToolDefinition(...)` and before append:

```python
tool = _apply_confidence_to_tool(tool, samples)
```

- [ ] **Step 5: Run confidence and publish tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest backend/tests/test_api_monitor_confidence.py backend/tests/test_api_monitor_publish_mcp.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_confidence.py
git commit -m "feat: score api monitor tool candidates"
```

---

### Task 5: Add Selection API and Selected-Only Publish

**Files:**
- Modify: `RpaClaw/backend/route/api_monitor.py`
- Modify: `RpaClaw/backend/rpa/api_monitor_mcp_registry.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`

- [ ] **Step 1: Write failing selected-only publish test**

Append to `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py`:

```python
async def test_publish_session_includes_selected_tools_only():
    server_repo = _MemoryRepo([])
    tool_repo = _MemoryRepo([])
    registry = ApiMonitorMcpRegistry(server_repository=server_repo, tool_repository=tool_repo)
    session = _build_session()
    session.tool_definitions[0].selected = True
    skipped = session.tool_definitions[0].model_copy(deep=True)
    skipped.id = "tool_skipped"
    skipped.name = "skipped_tool"
    skipped.selected = False
    session.tool_definitions.append(skipped)

    result = await registry.publish_session(
        session=session,
        user_id="user_1",
        mcp_name="Example MCP",
        description="",
        overwrite=False,
    )

    tools = await tool_repo.find_many({"mcp_server_id": result["server_id"], "user_id": "user_1"})
    assert len(tools) == 1
    assert tools[0]["name"] == session.tool_definitions[0].name
    assert result["tool_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest backend/tests/test_api_monitor_publish_mcp.py::test_publish_session_includes_selected_tools_only -q
```

Expected: FAIL because publish currently includes all tools.

- [ ] **Step 3: Filter selected tools in registry**

Modify `RpaClaw/backend/rpa/api_monitor_mcp_registry.py` in `publish_session()`:

```python
selected_tools = [tool for tool in session.tool_definitions if getattr(tool, "selected", False)]
```

Use `selected_tools` for:

```python
"tool_count": len(selected_tools),
```

and:

```python
session_tools=[tool.model_dump(mode="python") for tool in selected_tools],
```

Return:

```python
"tool_count": len(selected_tools),
```

- [ ] **Step 4: Add selection update route**

Modify imports in `RpaClaw/backend/route/api_monitor.py`:

```python
UpdateToolSelectionRequest,
```

Add route after `update_tool()`:

```python
@router.patch("/session/{session_id}/tools/{tool_id}/selection")
async def update_tool_selection(
    session_id: str,
    tool_id: str,
    request: UpdateToolSelectionRequest,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)

    for tool in session.tool_definitions:
        if tool.id == tool_id:
            tool.selected = request.selected
            from datetime import datetime
            tool.updated_at = datetime.now()
            return {"status": "success", "tool": tool.model_dump()}

    raise HTTPException(status_code=404, detail="Tool not found")
```

- [ ] **Step 5: Add route test**

Add to `RpaClaw/backend/tests/test_api_monitor_publish_mcp.py` or a dedicated route test file using the existing client fixture style in this file:

```python
def test_update_tool_selection(monkeypatch):
    session = _build_session()
    monkeypatch.setattr(api_monitor_route.api_monitor_manager, "get_session", lambda session_id: session)

    response = client.patch(
        f"/api/v1/api-monitor/session/{session.id}/tools/{session.tool_definitions[0].id}/selection",
        json={"selected": False},
    )

    assert response.status_code == 200
    assert response.json()["tool"]["selected"] is False
    assert session.tool_definitions[0].selected is False
```

If the existing file does not expose a reusable `client`, follow its current fixture pattern instead of inventing a new one.

- [ ] **Step 6: Run publish/route tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest backend/tests/test_api_monitor_publish_mcp.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/route/api_monitor.py RpaClaw/backend/rpa/api_monitor_mcp_registry.py RpaClaw/backend/tests/test_api_monitor_publish_mcp.py
git commit -m "feat: publish selected api monitor tools only"
```

---

### Task 6: Update Frontend API Types and Selection Client

**Files:**
- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts`
- Test: use existing frontend test command from `RpaClaw/frontend/package.json`

- [ ] **Step 1: Add TypeScript fields**

Modify `ApiToolDefinition` in `RpaClaw/frontend/src/api/apiMonitor.ts`:

```ts
export type ApiToolConfidence = 'high' | 'medium' | 'low'

export interface ApiToolDefinition {
  id: string
  session_id: string
  name: string
  description: string
  method: string
  url_pattern: string
  yaml_definition: string
  source_calls: string[]
  source: 'auto' | 'manual'
  confidence: ApiToolConfidence
  selected: boolean
  confidence_reasons: string[]
  source_evidence: Record<string, unknown>
  created_at: string
  updated_at: string
}
```

- [ ] **Step 2: Add selection API function**

Append near `updateTool()`:

```ts
export async function updateToolSelection(
  sessionId: string,
  toolId: string,
  selected: boolean,
): Promise<ApiToolDefinition> {
  const response = await apiClient.patch(
    `/api-monitor/session/${sessionId}/tools/${toolId}/selection`,
    { selected },
  )
  return response.data.tool
}
```

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend
npm run build
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/frontend/src/api/apiMonitor.ts
git commit -m "feat: add api monitor selection client"
```

---

### Task 7: Update API Monitor UI Grouping and Toggles

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`

- [ ] **Step 1: Import selection API and computed**

Modify imports:

```ts
import { ref, reactive, onMounted, onBeforeUnmount, nextTick, computed } from 'vue';
```

Add API import:

```ts
updateToolSelection as apiUpdateToolSelection,
```

- [ ] **Step 2: Add computed groups and badge helpers**

Add after `tools` state:

```ts
const adoptedTools = computed(() => tools.value.filter((tool) => tool.selected));
const notAdoptedTools = computed(() => tools.value.filter((tool) => !tool.selected));
const adoptedToolCount = computed(() => adoptedTools.value.length);
const toolGroups = computed(() => [
  { key: 'adopted', title: '采用', items: adoptedTools.value },
  { key: 'not-adopted', title: '不采用', items: notAdoptedTools.value },
]);
```

Add helpers near method colors:

```ts
const confidenceLabels: Record<string, string> = {
  high: '高置信',
  medium: '中置信',
  low: '低置信',
};

const confidenceClasses: Record<string, string> = {
  high: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/25 dark:text-emerald-300',
  medium: 'bg-amber-500/15 text-amber-600 border-amber-500/25 dark:text-amber-300',
  low: 'bg-slate-500/15 text-slate-600 border-slate-500/25 dark:text-slate-300',
};

const getConfidenceLabel = (confidence: string) => confidenceLabels[confidence] || '中置信';
const getConfidenceClass = (confidence: string) => confidenceClasses[confidence] || confidenceClasses.medium;
```

- [ ] **Step 3: Add selection toggle handler**

Add near tool management methods:

```ts
const toggleToolSelection = async (tool: ApiToolDefinition, selected: boolean) => {
  if (!sessionId.value) return;
  try {
    const updated = await apiUpdateToolSelection(sessionId.value, tool.id, selected);
    const idx = tools.value.findIndex((item) => item.id === tool.id);
    if (idx >= 0) {
      tools.value[idx] = updated;
    }
    addLog('INFO', `${selected ? '已采用' : '已取消采用'}: ${tool.name || tool.url_pattern}`);
  } catch (err: any) {
    addLog('ERROR', `更新采用状态失败: ${err.message}`);
  }
};
```

- [ ] **Step 4: Replace single tool list with two sections**

In the tool cards area, replace `v-for="tool in tools"` with two sections:

```vue
<template v-for="group in toolGroups" :key="group.key">
  <div v-if="group.items.length" class="space-y-2">
    <div class="flex items-center justify-between px-1 text-[11px] font-bold text-[var(--text-tertiary)]">
      <span>{{ group.title }}</span>
      <span>{{ group.items.length }}</span>
    </div>
    <div
      v-for="tool in group.items"
      :key="tool.id"
      class="rounded-2xl border border-slate-200 bg-slate-50/80 shadow-sm overflow-hidden dark:border-white/10 dark:bg-white/[0.04]"
    >
      <div
        class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-colors"
        @click="toggleToolExpand(tool.id)"
      >
        <button
          class="shrink-0 rounded-lg border px-2 py-1 text-[10px] font-bold transition"
          :class="tool.selected ? 'border-emerald-400 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300' : 'border-slate-300 bg-white text-slate-500 dark:border-white/10 dark:bg-white/5 dark:text-slate-300'"
          @click.stop="toggleToolSelection(tool, !tool.selected)"
        >
          {{ tool.selected ? '采用' : '不采用' }}
        </button>
        <span class="text-[10px] font-bold px-2 py-0.5 rounded-md" :class="getMethodClass(tool.method)">
          {{ tool.method }}
        </span>
        <span class="text-[11px] font-mono text-[var(--text-primary)] flex-1 truncate">{{ tool.url_pattern }}</span>
        <span class="shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-bold" :class="getConfidenceClass(tool.confidence)">
          {{ getConfidenceLabel(tool.confidence) }}
        </span>
        <ChevronDown :size="16" class="text-[var(--text-tertiary)] transition-transform" :class="expandedToolId === tool.id ? 'rotate-180' : ''" />
      </div>

      <div v-if="expandedToolId === tool.id" class="border-t border-slate-100 dark:border-white/10 px-4 py-4 bg-white dark:bg-transparent">
        <p class="text-xs text-[var(--text-secondary)] mb-2 font-medium">{{ tool.description }}</p>
        <div v-if="tool.confidence_reasons?.length" class="mb-3 flex flex-wrap gap-1.5">
          <span
            v-for="reason in tool.confidence_reasons"
            :key="reason"
            class="rounded-md bg-slate-100 px-2 py-1 text-[10px] font-medium text-[var(--text-secondary)] dark:bg-white/10"
          >
            {{ reason }}
          </span>
        </div>
        <textarea
          v-model="toolEdits[tool.id]"
          class="w-full h-40 bg-[#f8fafc] dark:bg-black/20 border border-slate-200 dark:border-white/10 rounded-xl text-[11px] font-mono text-[var(--text-primary)] p-3 outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 resize-y transition-shadow"
          spellcheck="false"
        ></textarea>
        <div class="flex justify-end gap-2 mt-3">
          <button
            @click="handleDeleteTool(tool.id)"
            class="rounded-xl border border-red-200 px-3 py-1.5 text-xs font-bold text-red-600 transition hover:bg-red-50 dark:border-red-500/20 dark:text-red-400 dark:hover:bg-red-500/10"
          >
            删除
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 5: Update counts and publish disabled state**

Change visible tool count label to include adopted count:

```vue
{{ adoptedToolCount }}/{{ tools.length }}
```

Change publish button disabled conditions from `!tools.length` to `!adoptedToolCount`:

```vue
:disabled="!sessionId || !adoptedToolCount || isPublishing"
```

Update `openPublishDialog()` and `submitPublish()` guards:

```ts
if (!sessionId.value || !adoptedToolCount.value) return;
```

Update publish dialog copy:

```vue
<p class="mt-1 text-sm text-[var(--text-tertiary)]">将采用的 API 接口打包成 MCP</p>
```

- [ ] **Step 6: Run frontend build/test**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend
npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue
git commit -m "feat: show api monitor adopted candidates"
```

---

### Task 8: Full Verification

**Files:**
- No production file changes unless verification reveals a bug.

- [ ] **Step 1: Run backend API monitor tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw
backend/.venv/bin/python -m pytest \
  backend/tests/test_api_monitor_capture.py \
  backend/tests/test_api_monitor_confidence.py \
  backend/tests/test_api_monitor_publish_mcp.py \
  backend/tests/test_mcp_route.py
```

Expected: all tests PASS.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend
npm run build
```

Expected: build succeeds.

- [ ] **Step 3: Manual smoke test**

Start the app using the project’s normal development flow. In API Monitor:

1. Start a session with a page that emits same-origin API requests.
2. Run analysis or recording.
3. Confirm generated candidates show confidence badges and reasons.
4. Confirm high-confidence candidates appear under “采用”.
5. Confirm medium/low-confidence candidates appear under “不采用”.
6. Toggle one candidate from “不采用” to “采用”.
7. Publish MCP.
8. Confirm published tool count equals adopted candidate count.

- [ ] **Step 4: Final status check**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git status --short
```

Expected: only intentional changes are present.

- [ ] **Step 5: Commit verification fixes if needed**

If verification required code fixes:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add <changed-files>
git commit -m "fix: stabilize api monitor confidence selection"
```

If no fixes were needed, do not create an empty commit.

---

## Self-Review

- Spec coverage:
  - Candidate preservation is covered by Tasks 1, 2, and 4.
  - Confidence fields and reasons are covered by Tasks 1 and 4.
  - CDP initiator and JS stack evidence are covered by Task 3.
  - Action-window metadata is covered by Task 2.
  - Selection update API is covered by Task 5.
  - Selected-only MCP publish is covered by Task 5.
  - Frontend adopted/not-adopted grouping is covered by Tasks 6 and 7.
  - Tests and verification are covered by all task-level test steps and Task 8.
- Placeholder scan:
  - No `TBD`, `TODO`, or "fill later" placeholders.
- Type consistency:
  - Backend uses `confidence`, `selected`, `confidence_reasons`, and `source_evidence`.
  - Frontend `ApiToolDefinition` uses the same field names.
  - Selection endpoint path matches the design: `/api-monitor/session/{session_id}/tools/{tool_id}/selection`.
