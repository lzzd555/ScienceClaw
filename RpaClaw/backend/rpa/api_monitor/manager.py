"""Session manager for API Monitor.

Manages browser contexts, network capture, recording, and
orchestrates the automatic page analysis workflow.
"""

import asyncio
import hashlib
import json
import logging
import uuid
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import AsyncGenerator, Dict, List, Optional, Set

from playwright.async_api import BrowserContext, Page

from backend.rpa.cdp_connector import get_cdp_connector
from backend.rpa.playwright_security import get_context_kwargs
from backend.rpa.screencast import SessionScreencastController

from backend.rpa.assistant_runtime import build_page_snapshot
from backend.rpa.frame_selectors import build_frame_path
from backend.rpa.snapshot_compression import compact_recording_snapshot

from .analysis_modes import AnalysisBusinessSafety
from .directed_analyzer import (
    build_directed_step_decision,
    execute_directed_action,
    filter_action_for_business_safety,
    describe_action,
    describe_locator_code,
)
from .directed_trace import (
    build_directed_retry_context,
    captured_call_ids,
    decision_snapshot,
    directed_action_fingerprint,
    execution_snapshot,
    observation_from_payload,
    retry_guard_skip_reason,
)

from .confidence import dedup_key_for_tool, score_api_candidate
from .llm_analyzer import analyze_elements, generate_tool_definition
from .models import ApiMonitorSession, ApiToolDefinition, ApiToolGenerationCandidate, CapturedApiCall, DirectedAnalysisTrace
from .network_capture import NetworkCaptureEngine, dedup_key

logger = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 60_000
DOM_CONTEXT_SCAN_TIMEOUT_S = 2.0

# ── Interactive element scanner ──────────────────────────────────────

_SCAN_INTERACTIVE_JS = """
() => {
    const interactiveSelectors = [
        'a[href]',
        'button',
        'input[type="submit"]',
        'input[type="button"]',
        'input[type="text"]',
        'input[type="search"]',
        'input[type="email"]',
        'input[type="number"]',
        'input[type="tel"]',
        'input[type="url"]',
        'select',
        'textarea',
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="menuitem"]',
        '[role="option"]',
        '[role="switch"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[onclick]',
        '[data-action]',
    ];
    const all = document.querySelectorAll(interactiveSelectors.join(', '));
    const results = [];
    const seen = new Set();

    for (const el of all) {
        if (seen.has(el)) continue;
        seen.add(el);

        // Skip hidden / disabled elements
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
        if (el.disabled) continue;

        // Compute a simple descriptor
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;

        const tag = el.tagName.toLowerCase();
        const text = (el.innerText || el.value || el.placeholder || '').trim().slice(0, 80);
        const href = el.getAttribute('href') || '';
        const role = el.getAttribute('role') || '';
        const type = el.getAttribute('type') || '';
        const name = el.getAttribute('name') || '';
        const ariaLabel = el.getAttribute('aria-label') || '';

        results.push({
            index: results.length,
            tag,
            text,
            href,
            role,
            type,
            name,
            ariaLabel,
            rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
        });
    }
    return results;
}
"""

# ── DOM context scanner (for LLM parameter inference) ────────────────

_SCAN_DOM_CONTEXT_JS = """
() => {
    const result = { forms: [], inputs: [], buttons: [] };

    // Scan all forms
    for (const form of document.querySelectorAll('form')) {
        const inputs = [];
        for (const input of form.querySelectorAll('input, select, textarea')) {
            if (input.type === 'hidden' || input.type === 'submit' || input.type === 'button') continue;
            let label = form.querySelector('label[for="' + input.id + '"]');
            if (!label) {
                const container = input.closest('.search-item, .form-group, .field, .input-group, .mb-3, .mb-4');
                if (container) label = container.querySelector('label');
            }
            if (!label && input.previousElementSibling && input.previousElementSibling.tagName === 'LABEL') {
                label = input.previousElementSibling;
            }
            const entry = {
                name: input.name || input.id || '',
                type: input.type || input.tagName.toLowerCase(),
                label: label ? label.textContent.trim() : '',
                placeholder: input.placeholder || '',
                required: input.required || false,
            };
            if (input.tagName === 'SELECT') {
                entry.type = 'select';
                entry.options = [...input.options].map(o => ({ value: o.value, text: o.textContent.trim() }));
            }
            inputs.push(entry);
        }
        result.forms.push({
            action: form.action || '',
            method: (form.method || 'GET').toUpperCase(),
            inputs,
            submitText: form.querySelector('button[type="submit"], input[type="submit"]')
                ? (form.querySelector('button[type="submit"], input[type="submit"]').textContent || '').trim()
                : '',
        });
    }

    // Scan standalone inputs (not inside a form)
    for (const input of document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"])')) {
        if (input.closest('form')) continue;
        let label = null;
        const container = input.closest('.search-item, .form-group, .field, .input-group');
        if (container) label = container.querySelector('label');
        if (!label && input.previousElementSibling && input.previousElementSibling.tagName === 'LABEL') {
            label = input.previousElementSibling;
        }
        result.inputs.push({
            id: input.id || '',
            name: input.name || '',
            type: input.type || 'text',
            label: label ? label.textContent.trim() : '',
            placeholder: input.placeholder || '',
        });
    }

    // Scan standalone buttons (not inside a form)
    for (const btn of document.querySelectorAll('button, [role="button"]')) {
        if (btn.closest('form')) continue;
        const text = (btn.textContent || '').trim();
        if (text) {
            result.buttons.push({
                text,
                onclick: btn.getAttribute('onclick') || '',
            });
        }
    }

    return result;
}
"""

# ── Helper ───────────────────────────────────────────────────────────


def should_process_request(request) -> bool:
    """Bridge filter for page event callbacks.

    Returns True if the request looks like an XHR/fetch API call
    (not a static resource, data URI, or WebSocket).
    """
    from .network_capture import should_capture

    return should_capture(request.url, request.resource_type)


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

_STACK_URL_RE = re.compile(
    r"((?:https?|chrome-extension|moz-extension|safari-extension)://[^\s)]+?)(?::\d+)*(:\d+)?(?:\))"
)


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
    return _dedupe_strings([m.group(1) for m in _STACK_URL_RE.finditer(stack or "")])


def _dedupe_strings(values: List[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


# ── Manager ──────────────────────────────────────────────────────────


def _apply_confidence_to_tool(
    tool: ApiToolDefinition,
    calls: List[CapturedApiCall],
) -> ApiToolDefinition:
    result = score_api_candidate(calls)
    tool.confidence = result.confidence
    tool.score = result.score
    tool.selected = result.selected
    tool.confidence_reasons = result.reasons
    tool.source_evidence = result.evidence_summary
    return tool


def _richness_score(tool: ApiToolDefinition) -> int:
    evidence = tool.source_evidence or {}
    breakdown = evidence.get("breakdown") or {}
    try:
        return int(breakdown.get("response_richness", 0))
    except (AttributeError, TypeError, ValueError):
        return 0


class ApiMonitorSessionManager:
    """Core session manager for API Monitor.

    Owns browser contexts, network capture engines, and orchestrates
    the automatic page analysis + recording workflows.
    """

    def __init__(self) -> None:
        self.sessions: Dict[str, ApiMonitorSession] = {}
        self._contexts: Dict[str, BrowserContext] = {}
        self._pages: Dict[str, Page] = {}
        self._session_pages: Dict[str, List[Page]] = {}
        self._listener_pages: Set[tuple[str, int]] = set()
        self._captures: Dict[str, NetworkCaptureEngine] = {}
        self._screencasts: Dict[str, SessionScreencastController] = {}
        self._request_evidence: Dict[str, Dict[str, Dict]] = {}
        self._last_action_at: Dict[str, float] = {}
        self._stop_recording_tasks: Dict[str, asyncio.Task[List[ApiToolDefinition]]] = {}
        self._last_recording_tools: Dict[str, List[ApiToolDefinition]] = {}
        self._last_recording_calls: Dict[str, List[CapturedApiCall]] = {}
        self._generation_tasks: Dict[str, Dict[str, asyncio.Task[None]]] = defaultdict(dict)
        self._generation_semaphore = asyncio.Semaphore(2)

    def register_screencast(self, session_id: str, controller: SessionScreencastController) -> None:
        """Register an active screencast controller so capture logs can be forwarded."""
        self._screencasts[session_id] = controller
        # Wire up capture engine's on_log callback
        capture = self._captures.get(session_id)
        if capture and not capture.on_log:
            capture.on_log = self._make_log_forwarder(session_id)

    def unregister_screencast(self, session_id: str) -> None:
        self._screencasts.pop(session_id, None)

    def _make_log_forwarder(self, session_id: str):
        """Create a callback that forwards capture logs to the screencast WS."""
        import asyncio as _asyncio

        def _forward(level: str, message: str) -> None:
            ctrl = self._screencasts.get(session_id)
            if ctrl:
                try:
                    loop = _asyncio.get_running_loop()
                    loop.create_task(ctrl.send_monitor_log(level, message))
                except RuntimeError:
                    pass

        return _forward

    # ── Session lifecycle ────────────────────────────────────────────

    async def create_session(
        self,
        user_id: str,
        target_url: str,
        sandbox_session_id: Optional[str] = None,
    ) -> ApiMonitorSession:
        """Create a new API Monitor session with its own browser context."""
        session_id = str(uuid.uuid4())
        effective_sandbox_id = sandbox_session_id or session_id
        session = ApiMonitorSession(
            id=session_id,
            user_id=user_id,
            sandbox_session_id=effective_sandbox_id,
            target_url=target_url,
        )
        self.sessions[session_id] = session

        # Create browser context via CDP connector
        browser = await get_cdp_connector().get_browser(
            session_id=effective_sandbox_id,
            user_id=user_id,
        )
        context = await browser.new_context(**get_context_kwargs())
        await context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = await context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)

        self._request_evidence[session_id] = {}
        self._contexts[session_id] = context

        # Install network capture. During initial navigation page.url can still be
        # about:blank, so fall back to the intended target URL for origin filtering.
        def _capture_page_url() -> str:
            current_page = self._pages.get(session_id) or page
            current_url = current_page.url
            if current_url and current_url != "about:blank":
                return current_url
            return session.target_url or target_url

        capture = NetworkCaptureEngine(
            page_url_provider=_capture_page_url,
            evidence_provider=lambda request: self._evidence_for_request(session_id, request),
            async_evidence_provider=lambda request: self._async_evidence_for_request(session_id, request),
        )
        self._captures[session_id] = capture
        self._adopt_page(session_id, page, make_active=True)

        def _on_context_page(new_page: Page) -> None:
            self._adopt_page(session_id, new_page, make_active=True)

        context.on("page", _on_context_page)

        # Navigate in background — don't block the HTTP response
        if target_url:
            async def _navigate() -> None:
                try:
                    await page.goto(target_url, wait_until="domcontentloaded")
                    session.target_url = page.url
                    session.updated_at = datetime.now()
                    logger.info("[ApiMonitor] Navigation complete for %s: %s", session_id, page.url)
                except Exception as exc:
                    logger.warning("[ApiMonitor] Navigation failed for %s: %s", session_id, exc)
            asyncio.create_task(_navigate())

        logger.info("[ApiMonitor] Session %s created, target URL=%s", session_id, target_url)
        return session

    async def stop_session(self, session_id: str) -> None:
        """Close browser context and clean up session resources."""
        session = self.sessions.pop(session_id, None)
        if session:
            session.status = "stopped"

        self._captures.pop(session_id, None)
        self._request_evidence.pop(session_id, None)
        self._last_action_at.pop(session_id, None)
        self._stop_recording_tasks.pop(session_id, None)
        self._last_recording_tools.pop(session_id, None)
        self._last_recording_calls.pop(session_id, None)
        self._session_pages.pop(session_id, None)
        self._listener_pages = {
            key for key in self._listener_pages
            if key[0] != session_id
        }
        self._pages.pop(session_id, None)
        self._screencasts.pop(session_id, None)

        context = self._contexts.pop(session_id, None)
        if context:
            try:
                await context.close()
            except Exception as exc:
                logger.warning("[ApiMonitor] Error closing context for %s: %s", session_id, exc)

        logger.info("[ApiMonitor] Session %s stopped", session_id)

    # ── Navigation ───────────────────────────────────────────────────

    async def navigate(self, session_id: str, url: str) -> str:
        """Navigate the session's page to a new URL."""
        page = self._require_page(session_id)
        await page.goto(url, wait_until="domcontentloaded")

        session = self.sessions[session_id]
        session.target_url = page.url
        session.updated_at = datetime.now()
        return session.target_url

    async def _observe_directed_page(self, page: Page, instruction: str) -> Dict:
        raw_snapshot = await build_page_snapshot(page, build_frame_path)
        compact_snapshot = compact_recording_snapshot(raw_snapshot, instruction)
        title = ""
        try:
            title = await page.title()
        except Exception:
            title = str(raw_snapshot.get("title") or "")
        url = getattr(page, "url", "") or str(raw_snapshot.get("url") or "")
        return {
            "url": url,
            "title": title,
            "raw_snapshot": raw_snapshot,
            "compact_snapshot": compact_snapshot,
            "dom_digest": self._build_directed_dom_digest(compact_snapshot),
        }

    def _build_directed_dom_digest(self, compact_snapshot: Dict) -> str:
        action_nodes = compact_snapshot.get("actionable_nodes") or compact_snapshot.get("actions") or []
        digest_payload = {
            "url": compact_snapshot.get("url") or "",
            "title": compact_snapshot.get("title") or "",
            "actionable": [
                {
                    "role": node.get("role") or "",
                    "name": node.get("name") or node.get("label") or "",
                    "text": node.get("text") or "",
                    "ref": node.get("ref") or node.get("internal_ref") or "",
                }
                for node in action_nodes[:80]
                if isinstance(node, dict)
            ],
        }
        encoded = json.dumps(digest_payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    async def _wait_for_directed_settle(
        self,
        page: Page,
        *,
        previous_digest: str,
        instruction: str,
        timeout_ms: int = 1500,
    ) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=500)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=500)
        except Exception:
            pass
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        last_digest = previous_digest
        stable_count = 0
        while time.monotonic() < deadline:
            try:
                observation = await self._observe_directed_page(page, instruction)
                current_digest = observation["dom_digest"]
            except Exception:
                return
            if current_digest == last_digest:
                stable_count += 1
                if stable_count >= 2:
                    return
            else:
                stable_count = 0
                last_digest = current_digest
            await page.wait_for_timeout(150)

    # ── Getters ──────────────────────────────────────────────────────

    def get_session(self, session_id: str) -> Optional[ApiMonitorSession]:
        return self.sessions.get(session_id)

    def get_page(self, session_id: str) -> Optional[Page]:
        return self._pages.get(session_id)

    def list_tabs(self, session_id: str) -> List[Dict]:
        """Return tab info for the session (used by screencast controller)."""
        session = self.sessions.get(session_id)
        if not session:
            return []

        active_page = self._pages.get(session_id)
        pages = self._session_pages.get(session_id) or ([active_page] if active_page else [])
        return [
            {
                "tab_id": f"{session.id}:{idx}",
                "title": "",
                "url": getattr(page, "url", "") or session.target_url or "",
                "active": page is active_page,
            }
            for idx, page in enumerate(pages)
            if page is not None
        ]

    # ── Recording ────────────────────────────────────────────────────

    async def start_recording(self, session_id: str) -> None:
        """Clear capture buffer and set session status to recording."""
        self._require_session(session_id)

        session = self.sessions[session_id]

        capture = self._captures.get(session_id)
        if capture:
            # Drain calls captured before recording (e.g. page-load XHR responses
            # containing CSRF tokens) into session history so token flow analysis
            # can find them later.  drain_new_calls() already clears the internal
            # buffer, so a separate capture.clear() is not needed.
            pre_calls = capture.drain_new_calls()
            if pre_calls:
                await self._process_captured_calls_for_generation(session_id, pre_calls)
                logger.info(
                    "[ApiMonitor] Drained %d pre-recording calls for session %s",
                    len(pre_calls), session_id,
                )

        self._mark_action(session_id)
        task = self._stop_recording_tasks.get(session_id)
        if task and task.done():
            self._stop_recording_tasks.pop(session_id, None)
        self._last_recording_tools.pop(session_id, None)
        self._last_recording_calls.pop(session_id, None)

        # Keep session.captured_calls intact — the full session history is needed
        # for token flow analysis.  Only the capture engine buffer was cleared.
        session.status = "recording"
        session.updated_at = datetime.now()
        logger.info("[ApiMonitor] Recording started for session %s", session_id)

    async def stop_recording(
        self,
        session_id: str,
        model_config: Optional[Dict] = None,
    ) -> List[ApiToolDefinition]:
        """Stop recording, drain captured calls, generate tool definitions.

        Stop is idempotent because callers may retry after a transient HTTP
        disconnect while the first stop is still generating tools.
        """
        session = self._require_session(session_id)

        existing_task = self._stop_recording_tasks.get(session_id)
        if existing_task:
            try:
                return await asyncio.shield(existing_task)
            finally:
                if existing_task.done():
                    self._stop_recording_tasks.pop(session_id, None)

        if session.status != "recording":
            cached_tools = self._last_recording_tools.get(session_id)
            if cached_tools is not None:
                return list(cached_tools)
            cached_calls = self._last_recording_calls.get(session_id)
            if cached_calls:
                tools = await self._generate_tools_from_calls(
                    session_id,
                    list(cached_calls),
                    source="manual",
                    model_config=model_config,
                )
                self._last_recording_tools[session_id] = list(tools)
                return tools
            return []

        task = asyncio.create_task(
            self._stop_recording_once(session_id, model_config=model_config)
        )
        self._stop_recording_tasks[session_id] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._stop_recording_tasks.pop(session_id, None)

    async def _stop_recording_once(
        self,
        session_id: str,
        model_config: Optional[Dict] = None,
    ) -> List[ApiToolDefinition]:
        """Perform the single authoritative stop for a recording window."""
        session = self._require_session(session_id)

        capture = self._captures.get(session_id)
        new_calls: List[CapturedApiCall] = []
        if capture:
            new_calls = capture.drain_new_calls()

        await self._process_captured_calls_for_generation(
            session_id,
            new_calls,
            model_config=model_config,
        )
        self._last_recording_calls[session_id] = list(new_calls)
        session.status = "idle"
        session.updated_at = datetime.now()

        if new_calls:
            tools = await self._generate_tools_from_calls(
                session_id, new_calls, source="manual", model_config=model_config,
            )
            self._last_recording_tools[session_id] = list(tools)
            return tools

        logger.info("[ApiMonitor] Recording stopped for session %s, %d calls captured", session_id, len(new_calls))
        self._last_recording_tools[session_id] = []
        return []

    # ── Page analysis (async generator) ──────────────────────────────

    async def analyze_page(
        self,
        session_id: str,
        model_config: Optional[Dict] = None,
    ) -> AsyncGenerator[Dict, None]:
        """Automatic page analysis: scan DOM, probe elements, generate tools.

        Yields SSE event dicts like {"event": <name>, "data": json.dumps({...})}.
        """
        session = self._require_session(session_id)
        page = self._require_page(session_id)
        session.status = "analyzing"
        session.updated_at = datetime.now()

        yield {
            "event": "analysis_started",
            "data": json.dumps({"session_id": session_id, "url": session.target_url}),
        }

        try:
            # Step 1: Scan interactive elements
            yield {
                "event": "progress",
                "data": json.dumps({"step": "scanning", "message": "Scanning page for interactive elements..."}),
            }

            elements = await self._scan_interactive_elements(page)

            yield {
                "event": "elements_found",
                "data": json.dumps({"count": len(elements)}),
            }

            if not elements:
                session.status = "idle"
                yield {
                    "event": "analysis_complete",
                    "data": json.dumps({"tools_generated": 0, "message": "No interactive elements found."}),
                }
                return

            # Step 2: Classify elements via LLM
            yield {
                "event": "progress",
                "data": json.dumps({"step": "classifying", "message": "Classifying elements via LLM..."}),
            }

            classification = await analyze_elements(
                url=session.target_url or "",
                elements=elements,
                model_config=model_config,
            )

            safe_indices = classification.get("safe", [])
            safe_elements = [elements[i] for i in safe_indices if i < len(elements)]

            yield {
                "event": "elements_classified",
                "data": json.dumps({
                    "safe": len(safe_elements),
                    "skipped": len(elements) - len(safe_elements),
                }),
            }

            if not safe_elements:
                session.status = "idle"
                yield {
                    "event": "analysis_complete",
                    "data": json.dumps({"tools_generated": 0, "message": "No safe elements to probe."}),
                }
                return

            # Step 3: Probe each safe element
            all_probed_calls: List[CapturedApiCall] = []

            for idx, elem in enumerate(safe_elements):
                yield {
                    "event": "progress",
                    "data": json.dumps({
                        "step": "probing",
                        "message": f"Probing element {idx + 1}/{len(safe_elements)}: {elem.get('tag', '')} {elem.get('text', '')[:30]}",
                        "current": idx + 1,
                        "total": len(safe_elements),
                    }),
                }

                capture = self._captures.get(session_id)
                if capture:
                    # Drain any calls accumulated since the last probe (including
                    # initial page-load calls on the first iteration) into session
                    # history so they are available for token flow analysis.
                    pre_calls = capture.drain_new_calls()
                    if pre_calls:
                        await self._process_captured_calls_for_generation(session_id, pre_calls)

                probed_calls = await self._probe_element(page, elem)

                if probed_calls:
                    all_probed_calls.extend(probed_calls)
                    yield {
                        "event": "calls_captured",
                        "data": json.dumps({
                            "element_index": idx,
                            "calls": len(probed_calls),
                        }),
                    }

            await self._process_captured_calls_for_generation(session_id, all_probed_calls)

            # Step 4: Generate tool definitions
            yield {
                "event": "progress",
                "data": json.dumps({"step": "generating", "message": "Generating tool definitions via LLM..."}),
            }

            tools = await self._generate_tools_from_calls(
                session_id, all_probed_calls, source="auto", model_config=model_config,
            )

            session.status = "idle"
            session.updated_at = datetime.now()

            yield {
                "event": "analysis_complete",
                "data": json.dumps({
                    "tools_generated": len(tools),
                    "total_calls": len(all_probed_calls),
                }),
            }

        except Exception as exc:
            session.status = "idle"
            session.updated_at = datetime.now()
            logger.error("[ApiMonitor] Analysis failed for session %s: %s", session_id, exc, exc_info=True)
            yield {
                "event": "analysis_error",
                "data": json.dumps({"error": str(exc)}),
            }

    # ── Directed page analysis ───────────────────────────────────────

    async def analyze_directed_page(
        self,
        session_id: str,
        *,
        instruction: str,
        mode: str,
        business_safety: AnalysisBusinessSafety,
        model_config: Optional[Dict] = None,
    ) -> AsyncGenerator[Dict, None]:
        """Directed analysis: dynamically plan one action from the current DOM each step."""
        session = self._require_session(session_id)
        page = self._require_page(session_id)
        session.status = "analyzing"
        session.updated_at = datetime.now()

        yield {
            "event": "analysis_started",
            "data": json.dumps(
                {
                    "session_id": session_id,
                    "url": session.target_url or getattr(page, "url", ""),
                    "mode": mode,
                    "has_instruction": bool(instruction.strip()),
                },
                ensure_ascii=False,
            ),
        }

        try:
            capture = self._captures.get(session_id)
            if capture:
                pre_calls = capture.drain_new_calls()
                if pre_calls:
                    await self._process_captured_calls_for_generation(session_id, pre_calls)

            max_failures = 20
            max_steps = 40
            failed_steps = 0
            run_history: List[Dict] = []
            directed_calls: List[CapturedApiCall] = []
            stop_reason = ""

            for step_index in range(1, max_steps + 1):
                yield {
                    "event": "progress",
                    "data": json.dumps(
                        {
                            "step": "snapshot",
                            "message": f"正在构建第 {step_index} 轮页面 DOM...",
                            "current": step_index,
                            "total": max_steps,
                        },
                        ensure_ascii=False,
                    ),
                }
                observation = await self._observe_directed_page(page, instruction)
                before_observation = observation_from_payload(observation)
                trace = DirectedAnalysisTrace(
                    step=step_index,
                    instruction=instruction,
                    mode=mode,
                    before=before_observation,
                )
                session.directed_traces.append(trace)
                yield {
                    "event": "directed_trace_added",
                    "data": json.dumps(trace.model_dump(mode="json"), ensure_ascii=False),
                }
                observation_for_prompt = {
                    "url": observation["url"],
                    "title": observation["title"],
                    "dom_digest": observation["dom_digest"],
                    "new_call_count": len(directed_calls),
                    "last_result": run_history[-1] if run_history else None,
                }
                completed_traces = session.directed_traces[:-1]
                retry_context = build_directed_retry_context(
                    completed_traces,
                    captured_api_summary=self._summarize_directed_calls(directed_calls),
                )
                observation_for_prompt["retry_context"] = retry_context
                yield {
                    "event": "directed_step_snapshot",
                    "data": json.dumps(
                        {
                            "step": step_index,
                            "url": observation["url"],
                            "title": observation["title"],
                            "dom_digest": observation["dom_digest"],
                        },
                        ensure_ascii=False,
                    ),
                }

                try:
                    decision = await build_directed_step_decision(
                        instruction=instruction,
                        compact_snapshot=observation["compact_snapshot"],
                        run_history=run_history,
                        observation=observation_for_prompt,
                        retry_context=retry_context,
                        model_config=model_config,
                    )
                except Exception as planner_exc:
                    error_text = str(planner_exc)
                    trace.execution = execution_snapshot(
                        result="planner_failed",
                        error=error_text,
                        before=trace.before,
                        after=trace.before,
                    )
                    trace.after = trace.before
                    trace.updated_at = datetime.now()
                    run_history.append(
                        {
                            "step": step_index,
                            "result": "planner_failed",
                            "error": error_text,
                            "url": observation["url"],
                            "title": observation["title"],
                            "dom_digest": observation["dom_digest"],
                        }
                    )
                    failed_steps += 1
                    yield {
                        "event": "directed_replan",
                        "data": json.dumps(
                            {
                                "step": step_index,
                                "description": "planner_failed",
                                "error": error_text,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    yield {
                        "event": "directed_trace_updated",
                        "data": json.dumps(trace.model_dump(mode="json"), ensure_ascii=False),
                    }
                    if failed_steps >= max_failures:
                        stop_reason = f"Reached max directed planner failures: {max_failures}"
                        break
                    continue
                trace.decision = decision_snapshot(decision)
                if decision.next_action is not None:
                    trace.action_fingerprint = directed_action_fingerprint(decision.next_action)
                trace.updated_at = datetime.now()
                yield {
                    "event": "directed_step_planned",
                    "data": json.dumps(
                        {
                            "step": step_index,
                            "goal_status": decision.goal_status,
                            "summary": decision.summary,
                            "expected_change": decision.expected_change,
                            "done_reason": decision.done_reason,
                        },
                        ensure_ascii=False,
                    ),
                }

                if decision.goal_status in ("done", "blocked"):
                    stop_reason = decision.done_reason or decision.summary or decision.goal_status
                    trace.execution = execution_snapshot(
                        result=decision.goal_status,
                        before=trace.before,
                        after=trace.before,
                    )
                    trace.after = trace.before
                    trace.updated_at = datetime.now()
                    yield {
                        "event": "directed_trace_updated",
                        "data": json.dumps(trace.model_dump(mode="json"), ensure_ascii=False),
                    }
                    yield {
                        "event": "directed_done",
                        "data": json.dumps(
                            {
                                "step": step_index,
                                "goal_status": decision.goal_status,
                                "reason": stop_reason,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    break

                action = decision.next_action
                if action is None:
                    stop_reason = "Planner did not return a next action"
                    break

                filtered = filter_action_for_business_safety(action, business_safety)
                if filtered.skipped:
                    skipped = filtered.skipped
                    trace.execution = execution_snapshot(
                        result="skipped",
                        error=skipped.reason,
                        before=trace.before,
                        after=trace.before,
                    )
                    trace.after = trace.before
                    trace.updated_at = datetime.now()
                    run_history.append(
                        {
                            "step": step_index,
                            "result": "skipped",
                            "description": skipped.description,
                            "reason": skipped.reason,
                            "risk": skipped.risk,
                        }
                    )
                    yield {
                        "event": "directed_action_skipped",
                        "data": json.dumps(
                            {
                                "step": step_index,
                                "description": skipped.description,
                                "reason": skipped.reason,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    yield {
                        "event": "directed_trace_updated",
                        "data": json.dumps(trace.model_dump(mode="json"), ensure_ascii=False),
                    }
                    continue

                allowed_action = filtered.allowed
                if allowed_action is None:
                    stop_reason = "No allowed directed action returned"
                    break

                skip_reason = retry_guard_skip_reason(trace.action_fingerprint or "", completed_traces)
                if skip_reason:
                    trace.execution = execution_snapshot(
                        result="retry_guard_skipped",
                        error=skip_reason,
                        before=trace.before,
                        after=trace.before,
                    )
                    trace.after = trace.before
                    trace.retry_advice = {
                        "reason": skip_reason,
                        "blocked_actions": retry_context.get("blocked_actions", []),
                        "block_steps": retry_context.get("block_steps", []),
                    }
                    trace.updated_at = datetime.now()
                    run_history.append(
                        {
                            "step": step_index,
                            "result": "retry_guard_skipped",
                            "description": allowed_action.description,
                            "code": describe_locator_code(allowed_action),
                            "error": skip_reason,
                            "expected_change": decision.expected_change,
                        }
                    )
                    failed_steps += 1
                    yield {
                        "event": "directed_trace_updated",
                        "data": json.dumps(trace.model_dump(mode="json"), ensure_ascii=False),
                    }
                    yield {
                        "event": "directed_replan",
                        "data": json.dumps(
                            {
                                "step": step_index,
                                "description": allowed_action.description,
                                "error": skip_reason,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    if failed_steps >= max_failures:
                        stop_reason = f"Reached max directed action failures: {max_failures}"
                        break
                    continue

                yield {
                    "event": "directed_action_detail",
                    "data": json.dumps(
                        {
                            "index": step_index,
                            "description": describe_action(allowed_action),
                            "code": describe_locator_code(allowed_action),
                            "risk": allowed_action.risk,
                        },
                        ensure_ascii=False,
                    ),
                }
                self._mark_action(session_id)
                try:
                    await execute_directed_action(page, allowed_action)
                except Exception as action_exc:
                    error_text = str(action_exc)
                    failed_step_calls: List[CapturedApiCall] = []
                    if capture:
                        failed_step_calls = capture.drain_new_calls()
                        if failed_step_calls:
                            directed_calls.extend(failed_step_calls)
                            await self._process_captured_calls_for_generation(
                                session_id,
                                failed_step_calls,
                                model_config=model_config,
                            )
                    try:
                        after_payload = await self._observe_directed_page(page, instruction)
                        trace.after = observation_from_payload(after_payload)
                    except Exception:
                        trace.after = trace.before
                    trace.execution = execution_snapshot(
                        result="failed",
                        error=error_text,
                        before=trace.before,
                        after=trace.after,
                    )
                    trace.captured_call_ids = captured_call_ids(failed_step_calls)
                    trace.updated_at = datetime.now()
                    run_history.append(
                        {
                            "step": step_index,
                            "result": "failed",
                            "description": allowed_action.description,
                            "code": describe_locator_code(allowed_action),
                            "error": error_text,
                            "expected_change": decision.expected_change,
                        }
                    )
                    failed_steps += 1
                    yield {
                        "event": "directed_replan",
                        "data": json.dumps(
                            {
                                "step": step_index,
                                "description": allowed_action.description,
                                "error": error_text,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    yield {
                        "event": "directed_trace_updated",
                        "data": json.dumps(trace.model_dump(mode="json"), ensure_ascii=False),
                    }
                    if failed_steps >= max_failures:
                        stop_reason = f"Reached max directed action failures: {max_failures}"
                        break
                    continue

                run_history.append(
                    {
                        "step": step_index,
                        "result": "executed",
                        "description": allowed_action.description,
                        "code": describe_locator_code(allowed_action),
                        "expected_change": decision.expected_change,
                    }
                )
                yield {
                    "event": "directed_step_executed",
                    "data": json.dumps(
                        {
                            "step": step_index,
                            "description": allowed_action.description,
                            "code": describe_locator_code(allowed_action),
                        },
                        ensure_ascii=False,
                    ),
                }
                yield {
                    "event": "directed_action_executed",
                    "data": json.dumps(
                        {
                            "code": describe_locator_code(allowed_action),
                            "description": allowed_action.description,
                        },
                        ensure_ascii=False,
                    ),
                }

                await self._wait_for_directed_settle(
                    page,
                    previous_digest=observation["dom_digest"],
                    instruction=instruction,
                )

                step_calls: List[CapturedApiCall] = []
                if capture:
                    step_calls = capture.drain_new_calls()
                if step_calls:
                    directed_calls.extend(step_calls)
                    await self._process_captured_calls_for_generation(
                        session_id,
                        step_calls,
                        model_config=model_config,
                    )
                    if run_history:
                        run_history[-1]["new_calls"] = self._summarize_directed_calls(step_calls)
                trace.after = trace.before
                trace.execution = execution_snapshot(
                    result="executed",
                    before=trace.before,
                    after=trace.after,
                )
                trace.captured_call_ids = captured_call_ids(step_calls)
                trace.updated_at = datetime.now()
                yield {
                    "event": "directed_trace_updated",
                    "data": json.dumps(trace.model_dump(mode="json"), ensure_ascii=False),
                }
                if step_calls:
                    yield {
                        "event": "calls_captured",
                        "data": json.dumps(
                            {
                                "mode": mode,
                                "step": step_index,
                                "calls": len(step_calls),
                            },
                            ensure_ascii=False,
                        ),
                    }
                yield {
                    "event": "directed_step_observed",
                    "data": json.dumps(
                        {
                            "step": step_index,
                            "new_calls": len(step_calls),
                            "total_directed_calls": len(directed_calls),
                        },
                        ensure_ascii=False,
                    ),
                }
                if step_calls:
                    completion_observation = {
                        "url": observation["url"],
                        "title": observation["title"],
                        "dom_digest": observation["dom_digest"],
                        "new_call_count": len(directed_calls),
                        "last_result": run_history[-1] if run_history else None,
                        "completion_check": True,
                    }
                    try:
                        completion_decision = await build_directed_step_decision(
                            instruction=instruction,
                            compact_snapshot=observation["compact_snapshot"],
                            run_history=run_history,
                            observation=completion_observation,
                            retry_context=build_directed_retry_context(
                                session.directed_traces,
                                captured_api_summary=self._summarize_directed_calls(directed_calls),
                            ),
                            model_config=model_config,
                        )
                    except Exception as planner_exc:
                        yield {
                            "event": "directed_replan",
                            "data": json.dumps(
                                {
                                    "step": step_index,
                                    "description": "completion_check_failed",
                                    "error": str(planner_exc),
                                },
                                ensure_ascii=False,
                            ),
                        }
                        continue
                    yield {
                        "event": "directed_step_planned",
                        "data": json.dumps(
                            {
                                "step": step_index,
                                "goal_status": completion_decision.goal_status,
                                "summary": completion_decision.summary,
                                "expected_change": completion_decision.expected_change,
                                "done_reason": completion_decision.done_reason,
                                "completion_check": True,
                            },
                            ensure_ascii=False,
                        ),
                    }
                    if completion_decision.goal_status in ("done", "blocked"):
                        stop_reason = (
                            completion_decision.done_reason
                            or completion_decision.summary
                            or completion_decision.goal_status
                        )
                        yield {
                            "event": "directed_done",
                            "data": json.dumps(
                                {
                                    "step": step_index,
                                    "goal_status": completion_decision.goal_status,
                                    "reason": stop_reason,
                                },
                                ensure_ascii=False,
                            ),
                        }
                        break
            else:
                stop_reason = f"Reached max directed steps: {max_steps}"

            yield {
                "event": "progress",
                "data": json.dumps(
                    {"step": "generating", "message": "Generating tool definitions via LLM..."},
                    ensure_ascii=False,
                ),
            }

            tools = await self._generate_tools_from_calls(
                session_id,
                directed_calls,
                source="auto",
                model_config=model_config,
            )

            session.status = "idle"
            session.updated_at = datetime.now()

            yield {
                "event": "analysis_complete",
                "data": json.dumps(
                    {
                        "mode": mode,
                        "tools_generated": len(tools),
                        "total_calls": len(directed_calls),
                        "steps": len(run_history),
                        "stop_reason": stop_reason,
                    },
                    ensure_ascii=False,
                ),
            }

        except Exception as exc:
            session.status = "idle"
            session.updated_at = datetime.now()
            logger.error("[ApiMonitor] Directed analysis failed for session %s: %s", session_id, exc, exc_info=True)
            yield {
                "event": "analysis_error",
                "data": json.dumps({"error": str(exc)}, ensure_ascii=False),
            }

    # ── Tool generation ──────────────────────────────────────────────

    def _summarize_directed_calls(self, calls: List[CapturedApiCall]) -> List[Dict]:
        summaries: List[Dict] = []
        for call in calls[:10]:
            response = call.response
            summaries.append(
                {
                    "method": call.request.method,
                    "url": call.request.url,
                    "url_pattern": call.url_pattern or "",
                    "status": response.status if response else None,
                    "content_type": response.content_type if response else None,
                }
            )
        return summaries

    async def _generate_tools_from_calls(
        self,
        session_id: str,
        calls: List[CapturedApiCall],
        source: str = "auto",
        model_config: Optional[Dict] = None,
    ) -> List[ApiToolDefinition]:
        """Group calls by dedup_key, generate YAML tool definition per group."""
        if not calls:
            return []

        session = self.sessions.get(session_id)
        if not session:
            return []

        # Scan DOM context for parameter inference
        dom_context = ""
        page = self._pages.get(session_id)
        if page:
            try:
                dom_data = await asyncio.wait_for(
                    page.evaluate(_SCAN_DOM_CONTEXT_JS),
                    timeout=DOM_CONTEXT_SCAN_TIMEOUT_S,
                )
                dom_context = json.dumps(dom_data, ensure_ascii=False, indent=2)
                logger.debug("[ApiMonitor] DOM context scanned: %d forms, %d inputs, %d buttons",
                             len(dom_data.get("forms", [])),
                             len(dom_data.get("inputs", [])),
                             len(dom_data.get("buttons", [])))
            except asyncio.TimeoutError:
                logger.warning(
                    "[ApiMonitor] DOM context scan timed out after %.1fs; generating API tools without DOM context",
                    DOM_CONTEXT_SCAN_TIMEOUT_S,
                )
            except Exception as exc:
                logger.warning("[ApiMonitor] DOM context scan failed: %s", exc)

        # Group by dedup key
        groups: Dict[str, List[CapturedApiCall]] = defaultdict(list)
        for call in calls:
            key = dedup_key(call)
            groups[key].append(call)

        tools: List[ApiToolDefinition] = []

        for key, group_calls in groups.items():
            # Take up to 5 samples per group
            samples = group_calls[:5]
            first = samples[0]
            method = first.request.method
            url_pattern = first.url_pattern or first.request.url

            try:
                yaml_def = await generate_tool_definition(
                    method=method,
                    url_pattern=url_pattern,
                    samples=samples,
                    page_context=session.target_url or "",
                    dom_context=dom_context,
                    model_config=model_config,
                )

                # Parse the YAML to extract name/description
                name, description = self._parse_yaml_metadata(yaml_def)

                tool = ApiToolDefinition(
                    session_id=session_id,
                    name=name,
                    description=description,
                    method=method,
                    url_pattern=url_pattern,
                    yaml_definition=yaml_def,
                    source_calls=[c.id for c in samples],
                    source=source,
                )
                tool = _apply_confidence_to_tool(tool, samples)

                session.tool_definitions.append(tool)
                tools.append(tool)

                logger.info(
                    "[ApiMonitor] Generated tool '%s' for %s %s",
                    name, method, url_pattern,
                )

            except Exception as exc:
                logger.warning(
                    "[ApiMonitor] Failed to generate tool for %s: %s",
                    key, exc,
                )

        self._dedup_session_tools(session_id, tools)

        return tools

    def _dedup_session_tools(
        self,
        session_id: str,
        new_tools: List[ApiToolDefinition],
    ) -> None:
        """Keep only the best scoring tool for each method + parameterized path."""
        session = self.sessions.get(session_id)
        if not session:
            return

        new_ids = {tool.id for tool in new_tools}
        existing_tools = [tool for tool in session.tool_definitions if tool.id not in new_ids]
        grouped: Dict[str, List[ApiToolDefinition]] = defaultdict(list)

        for tool in [*existing_tools, *new_tools]:
            grouped[dedup_key_for_tool(tool.method, tool.url_pattern)].append(tool)

        deduped: List[ApiToolDefinition] = []
        for group in grouped.values():
            group.sort(
                key=lambda tool: (
                    tool.score,
                    _richness_score(tool),
                    tool.created_at.isoformat() if tool.created_at else "",
                ),
                reverse=True,
            )
            deduped.append(group[0])

        session.tool_definitions = deduped
        survivor_ids = {tool.id for tool in deduped}
        new_tools[:] = [tool for tool in new_tools if tool.id in survivor_ids]

    # ── DOM scanning ─────────────────────────────────────────────────

    async def _scan_interactive_elements(self, page: Page) -> List[Dict]:
        """Inject JS to find clickable/interactive elements on the page."""
        try:
            elements = await page.evaluate(_SCAN_INTERACTIVE_JS)
            logger.info("[ApiMonitor] Found %d interactive elements", len(elements))
            return elements
        except Exception as exc:
            logger.warning("[ApiMonitor] Failed to scan elements: %s", exc)
            return []

    # ── Element probing ──────────────────────────────────────────────

    async def _probe_element(self, page: Page, elem: Dict) -> List[CapturedApiCall]:
        """Click an element, capture API calls, and navigate back if needed."""
        capture = self._captures.get(self._session_id_from_page(page) or "")
        if not capture:
            return []

        url_before = page.url
        calls: List[CapturedApiCall] = []

        try:
            # Build a locator for the element
            tag = elem.get("tag", "")
            text = elem.get("text", "")
            index = elem.get("index", 0)

            # Try various locator strategies
            locator = None
            if tag == "a" and text:
                locator = page.get_by_role("link", name=text, exact=False)
            elif tag == "button" or elem.get("role") == "button":
                if text:
                    locator = page.get_by_role("button", name=text, exact=False)
                else:
                    locator = page.get_by_role("button")
            elif tag in ("input", "select", "textarea"):
                name_attr = elem.get("name", "")
                aria_label = elem.get("ariaLabel", "")
                placeholder = ""
                if name_attr:
                    locator = page.get_by_label(name_attr)
                elif aria_label:
                    locator = page.get_by_label(aria_label)
                elif placeholder:
                    locator = page.get_by_placeholder(placeholder)
                else:
                    locator = page.locator(f"{tag}:nth-child({index + 1})")
            elif text:
                locator = page.get_by_text(text, exact=False)
            else:
                # Fallback: use CSS selector with tag and index
                locator = page.locator(f"{tag} >> nth={index}")

            if locator:
                session_id = self._session_id_from_page(page) or ""
                self._mark_action(session_id)
                # Brief wait to settle
                await page.wait_for_timeout(300)

                try:
                    await locator.click(timeout=5000)
                except Exception:
                    # Click failed (element may be obscured, detached, etc.)
                    return []

                # Wait for network activity
                await page.wait_for_timeout(1500)

                # Drain captured calls
                calls = capture.drain_new_calls()

                # If navigation occurred, go back
                current_url = page.url
                if current_url != url_before:
                    try:
                        await page.go_back(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                        await page.wait_for_timeout(500)
                    except Exception as back_exc:
                        logger.warning("[ApiMonitor] go_back failed: %s", back_exc)

        except Exception as exc:
            logger.debug("[ApiMonitor] Probe failed for element %s: %s", elem.get("tag"), exc)

        return calls

    # ── Network listener installation ────────────────────────────────

    def _install_listeners(
        self,
        session_id: str,
        page: Page,
        capture: NetworkCaptureEngine,
    ) -> None:
        """Install page.on('request') and page.on('response') listeners."""
        listener_key = (session_id, id(page))
        if listener_key in self._listener_pages:
            return
        self._listener_pages.add(listener_key)

        def on_request(request) -> None:
            logger.debug(
                "[ApiMonitor] page.on('request') fired: resource_type=%s url=%s",
                request.resource_type,
                request.url[:120],
            )
            if should_process_request(request):
                capture.on_request(request)

        async def on_response(response) -> None:
            logger.debug(
                "[ApiMonitor] page.on('response') fired: status=%d url=%s",
                response.status,
                response.url[:120],
            )
            await capture.on_response(response)

        page.on("request", on_request)
        page.on("response", on_response)

        logger.info("[ApiMonitor] Network listeners installed for session %s", session_id)

    def _adopt_page(self, session_id: str, page: Page, *, make_active: bool) -> None:
        """Track a page in the session and install API capture hooks on it."""
        session = self._require_session(session_id)
        pages = self._session_pages.setdefault(session_id, [])
        if page not in pages:
            pages.append(page)

        if make_active:
            self._pages[session_id] = page
            session.active_tab_id = f"{session.id}:{pages.index(page)}"
            if getattr(page, "url", ""):
                session.target_url = page.url
            session.updated_at = datetime.now()

        page.set_default_timeout(PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)

        capture = self._captures.get(session_id)
        if capture:
            self._install_listeners(session_id, page, capture)

        async def _install_page_evidence() -> None:
            context = self._contexts.get(session_id) or getattr(page, "context", None)
            if context is not None:
                await self._install_source_evidence_capture(session_id, context, page)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            loop.create_task(_install_page_evidence())

        def _on_close() -> None:
            current_pages = self._session_pages.get(session_id, [])
            if page in current_pages:
                current_pages.remove(page)
            self._listener_pages.discard((session_id, id(page)))
            if self._pages.get(session_id) is page:
                fallback = current_pages[-1] if current_pages else None
                if fallback is not None:
                    self._pages[session_id] = fallback
                    session.active_tab_id = f"{session.id}:{current_pages.index(fallback)}"
                    if getattr(fallback, "url", ""):
                        session.target_url = fallback.url
                else:
                    self._pages.pop(session_id, None)
                    session.active_tab_id = None

        page.on("close", _on_close)

    # ── Internal helpers ─────────────────────────────────────────────

    def _candidate_dedup_key(self, call: CapturedApiCall) -> str:
        return dedup_key(call)

    def _candidate_url_pattern(self, call: CapturedApiCall) -> str:
        return call.url_pattern or call.request.url

    def _find_generation_candidate(
        self,
        session: ApiMonitorSession,
        dedup_key_value: str,
    ) -> ApiToolGenerationCandidate | None:
        for candidate in session.generation_candidates:
            if candidate.dedup_key == dedup_key_value:
                return candidate
        return None

    def _upsert_generation_candidate(
        self,
        session_id: str,
        call: CapturedApiCall,
        *,
        dom_context: dict | None = None,
        page_url: str = "",
        title: str = "",
        dom_digest: str = "",
    ) -> tuple[ApiToolGenerationCandidate, bool]:
        session = self._require_session(session_id)
        key = self._candidate_dedup_key(call)
        candidate = self._find_generation_candidate(session, key)
        created = candidate is None
        now = datetime.now()

        if candidate is None:
            candidate = ApiToolGenerationCandidate(
                session_id=session_id,
                dedup_key=key,
                method=call.request.method,
                url_pattern=self._candidate_url_pattern(call),
                capture_dom_context=dom_context or {},
                capture_page_url=page_url,
                capture_title=title,
                capture_dom_digest=dom_digest,
            )
            session.generation_candidates.append(candidate)

        if call.id not in candidate.source_call_ids:
            candidate.source_call_ids.append(call.id)
        if call.id not in candidate.sample_call_ids and len(candidate.sample_call_ids) < 5:
            candidate.sample_call_ids.append(call.id)

        if not candidate.capture_dom_context and dom_context:
            candidate.capture_dom_context = dom_context
            candidate.capture_page_url = page_url
            candidate.capture_title = title
            candidate.capture_dom_digest = dom_digest

        if not created and candidate.status in ("generated", "running"):
            candidate.status = "stale"

        candidate.updated_at = now
        session.updated_at = now
        return candidate, created

    def _enqueue_generation_candidate(
        self,
        session_id: str,
        candidate_id: str,
        *,
        model_config: Optional[Dict] = None,
    ) -> None:
        session_tasks = self._generation_tasks.setdefault(session_id, {})
        existing = session_tasks.get(candidate_id)
        if existing and not existing.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self._run_generation_candidate(session_id, candidate_id, model_config=model_config)
        )
        session_tasks[candidate_id] = task

    async def _run_generation_candidate(
        self,
        session_id: str,
        candidate_id: str,
        *,
        model_config: Optional[Dict] = None,
    ) -> None:
        async with self._generation_semaphore:
            await self._generate_tool_for_candidate(
                session_id,
                candidate_id,
                model_config=model_config,
            )

    def _calls_for_candidate(
        self,
        session: ApiMonitorSession,
        candidate: ApiToolGenerationCandidate,
    ) -> list[CapturedApiCall]:
        by_id = {call.id: call for call in session.captured_calls}
        calls = [by_id[call_id] for call_id in candidate.sample_call_ids if call_id in by_id]
        if calls:
            return calls
        return [call for call in session.captured_calls if self._candidate_dedup_key(call) == candidate.dedup_key][:5]

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return "429" in text or "rate limit" in text or "too many requests" in text

    def _retry_after_for_attempt(self, attempts: int) -> datetime:
        delay = min(300, 2 ** max(attempts - 1, 0))
        return datetime.now() + timedelta(seconds=delay)

    async def _generate_tool_for_candidate(
        self,
        session_id: str,
        candidate_id: str,
        *,
        model_config: Optional[Dict] = None,
    ) -> ApiToolDefinition | None:
        session = self._require_session(session_id)
        candidate = next(
            (item for item in session.generation_candidates if item.id == candidate_id),
            None,
        )
        if candidate is None:
            return None

        samples = self._calls_for_candidate(session, candidate)
        if not samples:
            candidate.status = "failed"
            candidate.error = "No captured calls available for this candidate"
            candidate.updated_at = datetime.now()
            return None

        candidate.status = "running"
        candidate.error = ""
        candidate.updated_at = datetime.now()
        dom_context = json.dumps(candidate.capture_dom_context, ensure_ascii=False, indent=2)

        try:
            yaml_def = await generate_tool_definition(
                method=candidate.method,
                url_pattern=candidate.url_pattern,
                samples=samples,
                page_context=candidate.capture_page_url or session.target_url or "",
                dom_context=dom_context,
                model_config=model_config,
            )
        except Exception as exc:
            candidate.attempts += 1
            candidate.error = str(exc)
            if self._is_rate_limit_error(exc):
                candidate.status = "rate_limited"
                candidate.retry_after = self._retry_after_for_attempt(candidate.attempts)
            else:
                candidate.status = "failed"
                candidate.retry_after = None
            candidate.updated_at = datetime.now()
            session.updated_at = datetime.now()
            return None

        name, description = self._parse_yaml_metadata(yaml_def)

        existing = next(
            (tool for tool in session.tool_definitions if tool.generation_candidate_id == candidate.id),
            None,
        )
        if existing is None:
            tool = ApiToolDefinition(
                session_id=session_id,
                name=name,
                description=description,
                method=candidate.method,
                url_pattern=candidate.url_pattern,
                yaml_definition=yaml_def,
                source_calls=[call.id for call in samples],
                source="auto",
                generation_candidate_id=candidate.id,
            )
            session.tool_definitions.append(tool)
        else:
            tool = existing
            tool.name = name
            tool.description = description
            tool.method = candidate.method
            tool.url_pattern = candidate.url_pattern
            tool.yaml_definition = yaml_def
            tool.source_calls = [call.id for call in samples]
            tool.updated_at = datetime.now()

        tool = _apply_confidence_to_tool(tool, samples)
        new_tools = [tool]
        self._dedup_session_tools(session_id, new_tools)

        if tool.id in {item.id for item in session.tool_definitions}:
            candidate.status = "generated"
            candidate.tool_id = tool.id
        else:
            candidate.status = "generated"
            candidate.tool_id = None
        candidate.error = ""
        candidate.updated_at = datetime.now()
        session.updated_at = datetime.now()
        return tool

    async def _capture_generation_dom_context(self, session_id: str) -> tuple[dict, str, str, str]:
        page = self._pages.get(session_id)
        if not page:
            return {}, "", "", ""
        try:
            dom_data = await asyncio.wait_for(
                page.evaluate(_SCAN_DOM_CONTEXT_JS),
                timeout=DOM_CONTEXT_SCAN_TIMEOUT_S,
            )
        except Exception:
            dom_data = {}
        try:
            observation = await self._observe_directed_page(page, "")
            return dom_data, observation.get("url", ""), observation.get("title", ""), observation.get("dom_digest", "")
        except Exception:
            return dom_data, getattr(page, "url", "") or "", "", ""

    async def _process_captured_calls_for_generation(
        self,
        session_id: str,
        calls: list[CapturedApiCall],
        *,
        dom_context: dict | None = None,
        page_url: str = "",
        title: str = "",
        dom_digest: str = "",
        model_config: Optional[Dict] = None,
    ) -> list[ApiToolGenerationCandidate]:
        if not calls:
            return []
        session = self._require_session(session_id)
        existing_ids = {call.id for call in session.captured_calls}
        for call in calls:
            if call.id not in existing_ids:
                session.captured_calls.append(call)
                existing_ids.add(call.id)

        if dom_context is None:
            dom_context, page_url, title, dom_digest = await self._capture_generation_dom_context(session_id)

        changed: list[ApiToolGenerationCandidate] = []
        for call in calls:
            candidate, _created = self._upsert_generation_candidate(
                session_id,
                call,
                dom_context=dom_context,
                page_url=page_url,
                title=title,
                dom_digest=dom_digest,
            )
            changed.append(candidate)
            if candidate.status in ("pending", "stale", "failed"):
                self._enqueue_generation_candidate(session_id, candidate.id, model_config=model_config)
        return changed

    def reconcile_generation_candidates(
        self,
        session_id: str,
        *,
        enqueue: bool = True,
    ) -> list[ApiToolGenerationCandidate]:
        session = self._require_session(session_id)
        changed: list[ApiToolGenerationCandidate] = []

        for call in session.captured_calls:
            candidate, created = self._upsert_generation_candidate(session_id, call)
            if created or candidate.status in ("pending", "failed", "rate_limited", "stale"):
                changed.append(candidate)

        if enqueue:
            for candidate in changed:
                self._enqueue_generation_candidate(session_id, candidate.id)

        return changed

    def _require_session(self, session_id: str) -> ApiMonitorSession:
        """Get session or raise ValueError."""
        session = self.sessions.get(session_id)
        if session is None:
            raise ValueError(f"API Monitor session {session_id} not found")
        return session

    def _require_page(self, session_id: str) -> Page:
        """Get page or raise ValueError."""
        page = self._pages.get(session_id)
        if page is None:
            raise ValueError(f"No page for API Monitor session {session_id}")
        return page

    def _session_id_from_page(self, page: Page) -> Optional[str]:
        """Reverse lookup: find session_id from a Page object."""
        for sid, p in self._pages.items():
            if p is page:
                return sid
        return None

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

    @staticmethod
    def _parse_yaml_metadata(yaml_str: str) -> tuple:
        """Extract name and description from generated YAML.

        Returns (name, description). Falls back to defaults on parse failure.
        """
        name = "unnamed_tool"
        description = "Auto-generated API tool"

        try:
            # Extract name field
            name_match = re.search(r"^name:\s*(.+)$", yaml_str, re.MULTILINE)
            if name_match:
                name = name_match.group(1).strip().strip("'\"")

            # Extract description field
            desc_match = re.search(r"^description:\s*(.+)$", yaml_str, re.MULTILINE)
            if desc_match:
                description = desc_match.group(1).strip().strip("'\"")
        except Exception:
            pass

        return name, description


# ── Global singleton ─────────────────────────────────────────────────

api_monitor_manager = ApiMonitorSessionManager()
