"""Session manager for API Monitor.

Manages browser contexts, network capture, recording, and
orchestrates the automatic page analysis workflow.
"""

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import AsyncGenerator, Dict, List, Optional

from playwright.async_api import BrowserContext, Page

from backend.rpa.cdp_connector import get_cdp_connector
from backend.rpa.playwright_security import get_context_kwargs
from backend.rpa.screencast import SessionScreencastController

from .llm_analyzer import analyze_elements, generate_tool_definition
from .models import ApiMonitorSession, ApiToolDefinition, CapturedApiCall
from .network_capture import NetworkCaptureEngine, dedup_key

logger = logging.getLogger(__name__)

PAGE_TIMEOUT_MS = 60_000

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


# ── Manager ──────────────────────────────────────────────────────────


class ApiMonitorSessionManager:
    """Core session manager for API Monitor.

    Owns browser contexts, network capture engines, and orchestrates
    the automatic page analysis + recording workflows.
    """

    def __init__(self) -> None:
        self.sessions: Dict[str, ApiMonitorSession] = {}
        self._contexts: Dict[str, BrowserContext] = {}
        self._pages: Dict[str, Page] = {}
        self._captures: Dict[str, NetworkCaptureEngine] = {}
        self._screencasts: Dict[str, SessionScreencastController] = {}

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
        page = await context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT_MS)
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)

        self._contexts[session_id] = context
        self._pages[session_id] = page

        # Install network capture. During initial navigation page.url can still be
        # about:blank, so fall back to the intended target URL for origin filtering.
        def _capture_page_url() -> str:
            current_url = page.url
            if current_url and current_url != "about:blank":
                return current_url
            return session.target_url or target_url

        capture = NetworkCaptureEngine(page_url_provider=_capture_page_url)
        self._captures[session_id] = capture
        self._install_listeners(session_id, page, capture)

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

    # ── Getters ──────────────────────────────────────────────────────

    def get_session(self, session_id: str) -> Optional[ApiMonitorSession]:
        return self.sessions.get(session_id)

    def get_page(self, session_id: str) -> Optional[Page]:
        return self._pages.get(session_id)

    def list_tabs(self, session_id: str) -> List[Dict]:
        """Return tab info for the session (used by screencast controller)."""
        page = self._pages.get(session_id)
        session = self.sessions.get(session_id)
        if not page or not session:
            return []

        return [
            {
                "tab_id": session.id,
                "title": "",
                "url": session.target_url or "",
                "active": True,
            }
        ]

    # ── Recording ────────────────────────────────────────────────────

    async def start_recording(self, session_id: str) -> None:
        """Clear capture buffer and set session status to recording."""
        self._require_session(session_id)

        capture = self._captures.get(session_id)
        if capture:
            capture.clear()

        session = self.sessions[session_id]
        session.captured_calls.clear()
        session.status = "recording"
        session.updated_at = datetime.now()
        logger.info("[ApiMonitor] Recording started for session %s", session_id)

    async def stop_recording(
        self,
        session_id: str,
        model_config: Optional[Dict] = None,
    ) -> List[ApiToolDefinition]:
        """Stop recording, drain captured calls, generate tool definitions."""
        session = self._require_session(session_id)

        capture = self._captures.get(session_id)
        new_calls: List[CapturedApiCall] = []
        if capture:
            new_calls = capture.drain_new_calls()

        session.captured_calls.extend(new_calls)
        session.status = "idle"
        session.updated_at = datetime.now()

        if new_calls:
            tools = await self._generate_tools_from_calls(
                session_id, new_calls, source="manual", model_config=model_config,
            )
            return tools

        logger.info("[ApiMonitor] Recording stopped for session %s, %d calls captured", session_id, len(new_calls))
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
                    capture.clear()

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

            session.captured_calls.extend(all_probed_calls)

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

    # ── Tool generation ──────────────────────────────────────────────

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
                dom_data = await page.evaluate(_SCAN_DOM_CONTEXT_JS)
                dom_context = json.dumps(dom_data, ensure_ascii=False, indent=2)
                logger.debug("[ApiMonitor] DOM context scanned: %d forms, %d inputs, %d buttons",
                             len(dom_data.get("forms", [])),
                             len(dom_data.get("inputs", [])),
                             len(dom_data.get("buttons", [])))
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

        return tools

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

    # ── Internal helpers ─────────────────────────────────────────────

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

    @staticmethod
    def _parse_yaml_metadata(yaml_str: str) -> tuple:
        """Extract name and description from generated YAML.

        Returns (name, description). Falls back to defaults on parse failure.
        """
        name = "unnamed_tool"
        description = "Auto-generated API tool"

        try:
            import re
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
