import json
import logging
import re
import asyncio
from typing import Dict, List, Any, AsyncGenerator, Optional, Callable

from playwright.async_api import Page
from backend.deepagent.engine import get_llm_model
from backend.rpa.assistant_runtime import (
    build_frame_path_from_frame,
    build_page_snapshot,
    execute_structured_intent,
    resolve_structured_intent,
    resolve_collection_target,
)
from backend.rpa.session_context_service import SessionContextService

logger = logging.getLogger(__name__)

ELEMENT_EXTRACTION_TIMEOUT_S = 5.0
EXECUTION_TIMEOUT_S = 60.0
THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
THINK_CONTENT_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


# JS to extract interactive elements from the page
EXTRACT_ELEMENTS_JS = r"""() => {
    const INTERACTIVE = 'a,button,input,textarea,select,[role=button],[role=link],[role=menuitem],[role=menuitemradio],[role=tab],[role=checkbox],[role=radio],[contenteditable=true]';
    const els = document.querySelectorAll(INTERACTIVE);
    const results = [];
    let index = 1;
    const seen = new Set();
    for (const el of els) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        if (el.disabled) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;

        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        const name = (el.getAttribute('aria-label') || el.innerText || '').trim().substring(0, 80);
        const placeholder = el.getAttribute('placeholder') || '';
        const href = el.getAttribute('href') || '';
        const value = el.value || '';
        const type = el.getAttribute('type') || '';

        const key = tag + role + name + placeholder + href;
        if (seen.has(key)) continue;
        seen.add(key);

        const info = { index, tag };
        if (role) info.role = role;
        if (name) info.name = name.replace(/\s+/g, ' ');
        if (placeholder) info.placeholder = placeholder;
        if (href) info.href = href.substring(0, 120);
        if (value && tag !== 'input') info.value = value.substring(0, 80);
        if (type) info.type = type;
        const checked = el.checked;
        if (checked !== undefined) info.checked = checked;

        results.push(info);
        index++;
        if (index > 150) break;
    }
    return JSON.stringify(results);
}"""

SYSTEM_PROMPT = """You are an RPA recording assistant.

Prefer returning a structured JSON action instead of raw Playwright code.

For common atomic actions, respond with JSON in this shape:
{
  "action": "navigate|click|fill|extract_text|answer|press",
  "description": "short action summary",
  "prompt": "original user instruction",
  "result_key": "short_ascii_snake_case_key_for_extracted_value",
  "target_hint": {
    "role": "button|link|textbox|...",
    "name": "semantic label if known"
  },
  "collection_hint": {
    "kind": "search_results|table_rows|cards"
  },
  "ordinal": "first|last|1|2|3",
  "value": "text to fill or key to press when relevant"
}

Rules:
1. If the user says first or nth, use collection semantics and avoid hard-coded dynamic content.
2. Prefer role, label, placeholder, and structural hints over concrete titles or dynamic href values.
3. For opening a website or navigating to a known URL, prefer `"action": "navigate"` with the URL in `value`. Do not model browser chrome such as the address bar as a page textbox.
4. The backend resolves frame context automatically, so do not invent iframe selectors unless the user explicitly names a frame.
5. Only output Python code for genuinely complex custom logic that cannot be expressed as one atomic structured action.
6. If you output Python, define async def run(page): and use Playwright async API.
7. For extract_text and answer actions, include result_key as a short ASCII snake_case key such as latest_issue_title. Do not use Chinese, spaces, or hyphens.
8. When the user asks to extract multiple values (e.g. "提取购买人、使用部门和供应商"), output a JSON array of actions. Each action should have its own `result_key` and `target_hint`. Example:
[
  {"action": "extract_text", "description": "Extract requestor", "prompt": "提取购买人、使用部门和供应商", "result_key": "requestor", "target_hint": {"name": "购买人"}},
  {"action": "extract_text", "description": "Extract department", "prompt": "提取购买人、使用部门和供应商", "result_key": "department", "target_hint": {"name": "使用部门"}},
  {"action": "extract_text", "description": "Extract supplier", "prompt": "提取购买人、使用部门和供应商", "result_key": "supplier", "target_hint": {"name": "供应商"}}
]
9. When writing Python code, you have access to a `context` dict containing values extracted in previous steps. Use `context.get("key", "default")` to read previous values, and `context["key"] = value` to store new values. Do NOT use globals() or hardcoded values for data that comes from previous steps. The function signature remains `async def run(page)`.
"""

async def _get_page_elements(page: Page) -> str:
    """Extract interactive elements directly from the page."""
    try:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass
        result = await asyncio.wait_for(
            page.evaluate(EXTRACT_ELEMENTS_JS),
            timeout=ELEMENT_EXTRACTION_TIMEOUT_S,
        )
        return result if isinstance(result, str) else json.dumps(result)
    except Exception as e:
        logger.warning(f"Failed to extract elements from {page.url!r}: {e}")
        return "[]"


async def _execute_on_page(
    page: Page, code: str, context: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Execute AI-generated code directly on the page object.

    Parameters
    ----------
    context : dict, optional
        A read-write context dict built from the session's context_ledger.
        Injected into the exec namespace so LLM-generated code can use
        ``context.get("key")`` to read previous-step values and
        ``context["key"] = value`` to write new values.
    """
    ctx = dict(context) if context else {}
    try:
        await page.evaluate("window.__rpa_paused = true")
    except Exception:
        pass
    try:
        namespace: Dict[str, Any] = {"page": page, "context": ctx}
        exec(compile(code, "<rpa_assistant>", "exec"), namespace)
        if "run" in namespace and callable(namespace["run"]):
            ret = await asyncio.wait_for(namespace["run"](page), timeout=EXECUTION_TIMEOUT_S)
            return {"success": True, "output": str(ret) if ret else "ok", "error": None, "context": ctx}
        else:
            return {"success": False, "output": "", "error": "No run(page) function defined", "context": ctx}
    except asyncio.TimeoutError:
        return {"success": False, "output": "", "error": f"Command execution timed out ({EXECUTION_TIMEOUT_S:.0f}s)", "context": ctx}
    except Exception:
        import traceback
        return {"success": False, "output": "", "error": traceback.format_exc(), "context": ctx}
    finally:
        try:
            await page.evaluate("window.__rpa_paused = false")
        except Exception:
            pass


def _extract_llm_response_text(response: Any) -> str:
    """Normalize LangChain AIMessage content into a plain text response."""
    content = getattr(response, "content", "")
    additional_kwargs = getattr(response, "additional_kwargs", {}) or {}

    reasoning = additional_kwargs.get("reasoning_content", "")
    fallback_text = reasoning.strip() if isinstance(reasoning, str) else ""

    if isinstance(content, list):
        text_parts: List[str] = []
        thinking_parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "thinking":
                    thinking_parts.append(str(block.get("thinking", "")).strip())
                    continue
                text = block.get("text") or block.get("content")
                if text:
                    text_parts.append(str(text))
            elif isinstance(block, str):
                text_parts.append(block)
            elif block is not None:
                text_parts.append(str(block))
        clean = "\n".join(part.strip() for part in text_parts if str(part).strip()).strip()
        if clean:
            return clean
        thoughts = "\n".join(part for part in thinking_parts if part).strip()
        return thoughts or fallback_text

    if isinstance(content, str):
        clean = THINK_TAG_RE.sub("", content).strip()
        if clean:
            return clean
        if not fallback_text:
            matches = THINK_CONTENT_RE.findall(content)
            fallback_text = "\n".join(match.strip() for match in matches if match.strip()).strip()
        return fallback_text

    if content is None:
        return fallback_text

    text = str(content).strip()
    return text or fallback_text


def _extract_llm_chunk_text(chunk: Any) -> str:
    """Extract displayable text from a streamed chunk."""
    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        text_parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "thinking":
                    continue
                text = block.get("text") or block.get("content")
                if text:
                    text_parts.append(str(text))
            elif isinstance(block, str):
                text_parts.append(block)
            elif block is not None:
                text_parts.append(str(block))
        return "".join(text_parts)
    if isinstance(content, str):
        return THINK_TAG_RE.sub("", content)
    return ""


def _extract_llm_chunk_fallback_text(chunk: Any) -> str:
    """Extract reasoning/thinking fallback text from a streamed chunk."""
    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
    reasoning = additional_kwargs.get("reasoning_content", "")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()

    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        thoughts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                thought = str(block.get("thinking", "")).strip()
                if thought:
                    thoughts.append(thought)
        return "\n".join(thoughts).strip()

    if isinstance(content, str):
        matches = THINK_CONTENT_RE.findall(content)
        return "\n".join(match.strip() for match in matches if match.strip()).strip()

    return ""


def _snapshot_frame_lines(snapshot: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for container in snapshot.get("containers", []):
        lines.append(
            "Container: "
            f"{container.get('container_kind', 'container')} "
            f"{container.get('name', '')} "
            f"(actionable={len(container.get('child_actionable_ids') or [])}, "
            f"content={len(container.get('child_content_ids') or [])})"
        )
    for frame in snapshot.get("frames", []):
        lines.append(f"Frame: {frame.get('frame_hint', 'main document')}")
        for collection in frame.get("collections", []):
            lines.append(
                f"  Collection: {collection.get('kind', 'collection')} ({collection.get('item_count', 0)} items)"
            )
        for element in frame.get("elements", []):
            parts = [f"[{element.get('index', '?')}]"]
            if element.get("role"):
                parts.append(element["role"])
            parts.append(element.get("tag", "element"))
            if element.get("name"):
                parts.append(f'"{element["name"]}"')
            if element.get("placeholder"):
                parts.append(f'placeholder="{element["placeholder"]}"')
            if element.get("href"):
                parts.append(f'href="{element["href"]}"')
            if element.get("type"):
                parts.append(f'type={element["type"]}')
            lines.append("  " + " ".join(parts))
    return lines


def _is_explicit_extraction_request(message: str) -> bool:
    """Return True when the user message explicitly asks to extract / read / record a value."""
    keywords = ["提取", "读取", "获取", "总结", "记录"]
    return any(word in message for word in keywords)

class RPAAssistant:
    """Frame-aware AI recording assistant."""

    def __init__(self):
        self._histories: Dict[str, List[Dict[str, str]]] = {}

    def _get_history(self, session_id: str) -> List[Dict[str, str]]:
        if session_id not in self._histories:
            self._histories[session_id] = []
        return self._histories[session_id]

    def _trim_history(self, session_id: str, max_rounds: int = 10):
        hist = self._get_history(session_id)
        max_msgs = max_rounds * 2
        if len(hist) > max_msgs:
            self._histories[session_id] = hist[-max_msgs:]

    async def chat(
        self,
        session_id: str,
        page: Page,
        message: str,
        steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Page]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        # ── Ensure a task context exists for this session ──────────
        from backend.rpa.manager import rpa_manager
        try:
            rpa_manager.ensure_task_context(session_id)
        except ValueError:
            pass  # session not managed; skip context tracking

        yield {"event": "message_chunk", "data": {"text": "正在分析当前页面......\n\n"}}
        current_page = page_provider() if page_provider else page
        if current_page is None:
            yield {"event": "error", "data": {"message": "No active page available"}}
            yield {"event": "done", "data": {}}
            return

        snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
        history = self._get_history(session_id)
        context_service = None
        get_context_service = getattr(rpa_manager, "get_session_context_service", None)
        if callable(get_context_service):
            try:
                context_service = get_context_service(session_id)
            except ValueError:
                context_service = None
        elif session_id in getattr(rpa_manager, "sessions", {}):
            fallback_session = rpa_manager.sessions.get(session_id)
            fallback_ledger = getattr(fallback_session, "context_ledger", None)
            context_service = self._get_context_service(context_ledger=fallback_ledger)
        messages = self._build_messages(
            message,
            steps,
            snapshot,
            history,
            context_service=context_service,
        )

        full_response = ""
        async for chunk_text in self._stream_llm(messages, model_config):
            full_response += chunk_text
            yield {"event": "message_chunk", "data": {"text": chunk_text}}

        yield {"event": "executing", "data": {}}
        _session = rpa_manager.sessions.get(session_id)
        context_ledger = _session.context_ledger if _session else None

        # ── 首次执行 ──────────────────────────────────────────
        current_page = page_provider() if page_provider else page
        result = {"success": False, "error": "No active page available", "output": ""}
        code = None
        resolution = None
        context_reads: List[str] = []

        if current_page is not None:
            intents = self._extract_structured_intents(full_response)
            if len(intents) > 1:
                # ── Multi-action: execute each intent with per-intent retry ──
                multi_results = []
                multi_context_writes: List[str] = []
                for i, intent_json in enumerate(intents):
                    # --- First attempt ---
                    try:
                        intent_result, intent_reads = await self._execute_intent_with_ledger(
                            intent_json, current_page, snapshot, context_ledger,
                            message, rpa_manager, session_id,
                        )
                        multi_results.append(intent_result)
                        context_reads.extend(intent_reads)
                        step_data = intent_result.get("step")
                        if step_data:
                            for key in step_data.get("context_writes", []) or []:
                                if key not in multi_context_writes:
                                    multi_context_writes.append(key)
                    except Exception as exc:
                        intent_result = {"success": False, "error": str(exc), "output": ""}
                        multi_results.append(intent_result)

                    # --- Per-intent retry if failed ---
                    if not intent_result.get("success"):
                        intent_error = intent_result.get("error", "")
                        yield {
                            "event": "retry_start",
                            "data": {"original_error": intent_error, "intent_index": i},
                        }
                        retry_prompt = (
                            f"Original intent:\n{json.dumps(intent_json, ensure_ascii=False)}\n\n"
                            f"Execution error: {intent_error}\n\n"
                            f"Please fix the intent and output a single corrected JSON action."
                        )
                        retry_messages = [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": retry_prompt},
                        ]
                        retry_response = ""
                        async for chunk_text in self._stream_llm(retry_messages, model_config):
                            retry_response += chunk_text
                            yield {"event": "retry_chunk", "data": {"text": chunk_text, "intent_index": i}}

                        yield {"event": "retry_executing", "data": {"intent_index": i}}

                        # Parse the retry response as a single intent
                        retry_intent = self._extract_structured_intent(retry_response)
                        if retry_intent:
                            try:
                                retry_result, retry_reads = await self._execute_intent_with_ledger(
                                    retry_intent, current_page, snapshot, context_ledger,
                                    message, rpa_manager, session_id,
                                )
                                # Replace the failed result with retry result
                                multi_results[-1] = retry_result
                                context_reads.extend(retry_reads)
                                step_data = retry_result.get("step")
                                if step_data:
                                    for key in step_data.get("context_writes", []) or []:
                                        if key not in multi_context_writes:
                                            multi_context_writes.append(key)
                            except Exception as exc2:
                                multi_results[-1] = {"success": False, "error": str(exc2), "output": ""}
                        else:
                            multi_results[-1] = {
                                "success": False,
                                "error": f"Retry failed: could not parse LLM response. Original: {intent_error}",
                                "output": "",
                            }

                        yield {
                            "event": "retry_result",
                            "data": {
                                "success": multi_results[-1].get("success", False),
                                "error": multi_results[-1].get("error"),
                                "intent_index": i,
                            },
                                }

                # Aggregate results
                result = self._select_primary_multi_result(multi_results)
                code = None
                resolution = None

                all_success = all(r.get("success") for r in multi_results)
                result["success"] = all_success
                if not all_success:
                    errors = [r.get("error", "") for r in multi_results if not r.get("success")]
                    result["error"] = "; ".join(errors)

                history.append({"role": "user", "content": message})
                history.append({"role": "assistant", "content": full_response})
                self._trim_history(session_id)

                yield {
                    "event": "result",
                    "data": {
                        "success": result["success"],
                        "error": result.get("error"),
                        "step": None,
                        "output": result.get("output"),
                        "context_reads": context_reads,
                        "context_writes": multi_context_writes,
                        "retried": any("retry" in str(r.get("error", "")) for r in multi_results if not r.get("success")),
                        "original_error": None,
                        "multi_action_count": len(intents),
                    },
                }
                yield {"event": "done", "data": {}}
                return
            else:
                # ── Single action: original flow ──
                try:
                    result, code, resolution, context_reads, _ai_writes = await self._execute_single_response(
                        current_page, snapshot, full_response, context_ledger
                    )
                except Exception as exc:
                    result = {"success": False, "error": str(exc), "output": ""}
                    code = None
                    resolution = None

        # ── 首次失败则重试 ──────────────────────────────────
        retried = False
        original_error = ""

        if not result.get("success"):
            original_error = result.get("error", "")
            retried = True

            yield {
                "event": "retry_start",
                "data": {"original_error": original_error},
            }

            retry_messages = messages + [
                {"role": "assistant", "content": full_response},
                {"role": "user", "content": f"Execution error: {result['error']}\nPlease fix it and retry."},
            ]
            retry_response = ""
            async for chunk_text in self._stream_llm(retry_messages, model_config):
                retry_response += chunk_text
                yield {"event": "retry_chunk", "data": {"text": chunk_text}}

            yield {"event": "retry_executing", "data": {}}

            current_page = page_provider() if page_provider else page
            if current_page is None:
                result = {"success": False, "error": "No active page available", "output": ""}
            else:
                retry_snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
                retry_intents = self._extract_structured_intents(retry_response)
                if len(retry_intents) > 1:
                    # ── Retry multi-action with per-intent retry ──
                    multi_results = []
                    multi_context_writes: List[str] = []
                    for i, intent_json in enumerate(retry_intents):
                        try:
                            intent_result, intent_reads = await self._execute_intent_with_ledger(
                                intent_json, current_page, retry_snapshot, context_ledger,
                                message, rpa_manager, session_id,
                            )
                            multi_results.append(intent_result)
                            context_reads.extend(intent_reads)
                            step_data = intent_result.get("step")
                            if step_data:
                                for key in step_data.get("context_writes", []) or []:
                                    if key not in multi_context_writes:
                                        multi_context_writes.append(key)
                        except Exception as exc:
                            intent_result = {"success": False, "error": str(exc), "output": ""}
                            multi_results.append(intent_result)

                        if not intent_result.get("success"):
                            intent_error = intent_result.get("error", "")
                            yield {
                                "event": "retry_start",
                                "data": {"original_error": intent_error, "intent_index": i},
                            }
                            retry_prompt = (
                                f"Original intent:\n{json.dumps(intent_json, ensure_ascii=False)}\n\n"
                                f"Execution error: {intent_error}\n\n"
                                f"Please fix the intent and output a single corrected JSON action."
                            )
                            retry_llm_msgs = [
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": retry_prompt},
                            ]
                            rr = ""
                            async for chunk_text in self._stream_llm(retry_llm_msgs, model_config):
                                rr += chunk_text
                                yield {"event": "retry_chunk", "data": {"text": chunk_text, "intent_index": i}}

                            yield {"event": "retry_executing", "data": {"intent_index": i}}

                            retry_intent = self._extract_structured_intent(rr)
                            if retry_intent:
                                try:
                                    retry_result, retry_reads = await self._execute_intent_with_ledger(
                                        retry_intent, current_page, retry_snapshot, context_ledger,
                                        message, rpa_manager, session_id,
                                    )
                                    multi_results[-1] = retry_result
                                    context_reads.extend(retry_reads)
                                    step_data = retry_result.get("step")
                                    if step_data:
                                        for key in step_data.get("context_writes", []) or []:
                                            if key not in multi_context_writes:
                                                multi_context_writes.append(key)
                                except Exception as exc2:
                                    multi_results[-1] = {"success": False, "error": str(exc2), "output": ""}

                            yield {
                                "event": "retry_result",
                                "data": {
                                    "success": multi_results[-1].get("success", False),
                                    "error": multi_results[-1].get("error"),
                                    "intent_index": i,
                                },
                            }

                    result = self._select_primary_multi_result(multi_results)
                    code = None
                    resolution = None
                    all_success = all(r.get("success") for r in multi_results)
                    result["success"] = all_success
                    if not all_success:
                        errors = [r.get("error", "") for r in multi_results if not r.get("success")]
                        result["error"] = "; ".join(errors)

                    full_response = retry_response
                    yield {
                        "event": "retry_result",
                        "data": {
                            "success": result.get("success", False),
                            "error": result.get("error"),
                            "multi_action_count": len(retry_intents),
                        },
                    }
                    history.append({"role": "user", "content": message})
                    history.append({"role": "assistant", "content": full_response})
                    self._trim_history(session_id)
                    yield {
                        "event": "result",
                        "data": {
                            "success": result["success"],
                            "error": result.get("error"),
                            "step": None,
                            "output": result.get("output"),
                            "context_reads": context_reads,
                            "context_writes": multi_context_writes,
                            "retried": True,
                            "original_error": original_error,
                            "multi_action_count": len(retry_intents),
                        },
                    }
                    yield {"event": "done", "data": {}}
                    return
                else:
                    # ── Retry single-action ──
                    try:
                        result, code, resolution, context_reads, _ai_writes = await self._execute_single_response(
                            current_page, retry_snapshot, retry_response, context_ledger
                        )
                    except Exception as exc:
                        result = {"success": False, "error": str(exc), "output": ""}
                        code = None
                        resolution = None

            full_response = retry_response

            yield {
                "event": "retry_result",
                "data": {
                    "success": result.get("success", False),
                    "error": result.get("error"),
                },
            }
        if resolution:
            yield {"event": "resolution", "data": {"intent": resolution}}

        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": full_response})
        self._trim_history(session_id)

        step_data = None
        ai_context_writes: List[str] = []
        if result["success"]:
            if result.get("step"):
                step_data = result["step"]
            elif code:
                body = self._extract_function_body(code)
                step_data = {
                    "action": "ai_script",
                    "source": "ai",
                    "value": body,
                    "description": message,
                    "prompt": message,
                }
            # Collect new context keys written by AI script code
            ai_context_writes = list(result.get("context_writes_from_ai", []))

        # ── Compute context reads / writes ─────────────────────────
        context_writes: List[str] = []
        resolved_context_service = self._get_context_service(
            context_ledger=context_ledger,
            context_service=context_service,
        )
        normalized_context_reads = self._normalize_step_context_reads(
            context_service=resolved_context_service,
            context_reads=context_reads,
            step_data=step_data,
        )

        if result["success"] and step_data:
            promoted_context_writes = self._compute_context_writes(
                message=message,
                step_data=step_data,
                resolution=resolution,
            )

            if promoted_context_writes:
                # Only extraction-style promotions flow through the generic ledger promotion path.
                self._promote_to_ledger(
                    rpa_manager=rpa_manager,
                    session_id=session_id,
                    context_writes=promoted_context_writes,
                    step_data=step_data,
                    output=result.get("output"),
                )

            context_writes = list(promoted_context_writes)
            for key in ai_context_writes:
                if key not in context_writes:
                    context_writes.append(key)

        # Attach context lists to the step payload for downstream use
        if step_data is not None:
            step_data["context_reads"] = normalized_context_reads
            step_data["context_writes"] = context_writes

        yield {
            "event": "result",
            "data": {
                "success": result["success"],
                "error": result.get("error"),
                "step": step_data,
                "output": result.get("output"),
                "context_reads": normalized_context_reads,
                "context_writes": context_writes,
                "retried": retried,
                "original_error": original_error if retried else None,
            },
        }
        yield {"event": "done", "data": {}}

    @staticmethod
    def _compute_context_writes(
        message: str,
        step_data: Dict[str, Any],
        resolution: Optional[Dict[str, Any]],
    ) -> List[str]:
        """Determine which context keys should be written after this step.

        An extracted value is promoted to context when:
        * The action is ``extract_text`` with a ``result_key``, AND
        * The user explicitly requested extraction, OR
        * The prompt references cross-page transfer (e.g. "fill into", "copy to").
        """
        action = step_data.get("action", "")
        result_key = step_data.get("result_key")
        if not result_key:
            return []

        if action != "extract_text":
            return []

        # Rule 1: user explicitly asked to extract / read / record
        if _is_explicit_extraction_request(message):
            return [result_key]

        # Rule 2: prompt or description hints at cross-page transfer
        transfer_keywords = ["填写", "填入", "复制", "copy", "fill", "transfer", "另一个", "其它", "其他"]
        description = step_data.get("description", "")
        if any(kw in message for kw in transfer_keywords) or any(kw in description for kw in transfer_keywords):
            return [result_key]

        return []

    @staticmethod
    def _select_primary_multi_result(multi_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Prefer the latest successful answer output in a mixed multi-action response."""
        for candidate in reversed(multi_results):
            internal_step = candidate.get("internal_step") or {}
            if candidate.get("success") and str(internal_step.get("action", "")).lower() == "answer":
                return dict(candidate)
        return dict(multi_results[-1]) if multi_results else {"success": False, "error": "No actions executed", "output": ""}

    @staticmethod
    def _promote_to_ledger(
        rpa_manager: Any,
        session_id: str,
        context_writes: List[str],
        step_data: Dict[str, Any],
        output: Optional[str],
    ) -> None:
        """Persist promoted values into the session's TaskContextLedger."""
        if not context_writes:
            return

        session = rpa_manager.sessions.get(session_id)
        if session is None:
            return

        ledger = session.context_ledger
        for key in context_writes:
            user_explicit = _is_explicit_extraction_request(step_data.get("prompt", ""))
            is_cross_page = any(
                kw in (step_data.get("description", "") + step_data.get("prompt", ""))
                for kw in ["填写", "填入", "复制", "copy", "fill", "transfer", "另一个", "其它", "其他"]
            )
            if ledger.should_promote_value(
                key=key,
                source="observation",
                user_explicit=user_explicit,
                runtime_required=is_cross_page,
                consumed_later=is_cross_page,
            ):
                rpa_manager.record_context_value(
                    session_id,
                    category="observed",
                    key=key,
                    value=output,
                    user_explicit=user_explicit,
                    runtime_required=is_cross_page,
                    source_step_id=step_data.get("id"),
                    source_kind="assistant_extraction",
                )

    async def _execute_intent_with_ledger(
        self,
        intent_json: Dict[str, Any],
        current_page: Page,
        snapshot: Dict[str, Any],
        context_ledger: Optional[Any],
        message: str,
        rpa_manager: Any,
        session_id: str,
    ) -> tuple[Dict[str, Any], List[str]]:
        """Execute a single intent and promote context writes to ledger.

        Returns (result, context_reads).
        """
        intent_response = json.dumps(intent_json, ensure_ascii=False)
        result, _, resolution, intent_reads, _ai_writes = await self._execute_single_response(
            current_page, snapshot, intent_response, context_ledger
        )
        context_service = self._get_context_service(context_ledger=context_ledger)
        if result.get("success") and resolution:
            step_data = result.get("step")
            if step_data and step_data.get("action") != "answer":
                normalized_reads = self._normalize_step_context_reads(
                    context_service=context_service,
                    context_reads=intent_reads,
                    step_data=step_data,
                )
                context_writes = self._compute_context_writes(
                    message=message, step_data=step_data, resolution=resolution,
                )
                self._promote_to_ledger(
                    rpa_manager=rpa_manager, session_id=session_id,
                    context_writes=context_writes, step_data=step_data,
                    output=result.get("output"),
                )
                step_data["context_reads"] = normalized_reads
                step_data["context_writes"] = context_writes
                await rpa_manager.add_step(session_id, step_data)
                return result, normalized_reads
        normalized_reads = self._normalize_step_context_reads(
            context_service=context_service,
            context_reads=intent_reads,
            step_data=result.get("step"),
        )
        return result, normalized_reads

    async def _execute_single_response(
        self,
        current_page: Page,
        snapshot: Dict[str, Any],
        full_response: str,
        context_ledger: Optional[Any] = None,
    ) -> tuple[Dict[str, Any], Optional[str], Optional[Dict[str, Any]], List[str], List[str]]:
        """Execute a single LLM response.

        Returns (result, code, resolution, context_reads, ai_context_writes).
        """
        structured_intent = self._extract_structured_intent(full_response)
        if structured_intent:
            context_answer = self._answer_from_context_service(
                structured_intent,
                context_ledger=context_ledger,
            )
            if context_answer is not None:
                return (
                    {"success": True, "output": context_answer["text"]},
                    None,
                    structured_intent,
                    list(context_answer["values"].keys()),
                    [],
                )

            # Resolve ${key} references in the intent using context ledger
            context_reads = self._resolve_context_in_intent(structured_intent, context_ledger)
            resolved_intent = resolve_structured_intent(snapshot, structured_intent)
            result = await execute_structured_intent(current_page, resolved_intent)
            if str(resolved_intent.get("action", "")).lower() == "answer" and "step" in result:
                # Keep answer output available to the caller, but do not let it
                # flow through the persistable recorded-step contract.
                result = {**result, "internal_step": result.get("step")}
                result.pop("step", None)
            return result, None, resolved_intent, context_reads, []

        code = self._extract_code(full_response)
        if not code:
            raise ValueError("Unable to extract structured intent or executable code from assistant response")

        # Build context dict from ledger for AI script execution
        pre_context = self._build_context_from_ledger(context_ledger)
        result = await self._execute_on_page(current_page, code, pre_context)

        context_service = self._get_context_service(context_ledger=context_ledger)
        post_context = result.get("context", {})
        if context_service is not None:
            runtime_contract = context_service.capture_runtime_contract(
                before_context=pre_context,
                after_context=post_context,
            )
            ai_context_writes = list(runtime_contract.writes)
            if runtime_contract.updates:
                context_service.apply_contract_writes(
                    runtime_contract,
                    category="observed",
                    source_kind="ai_script",
                )
        else:
            ai_context_writes = [k for k in post_context if k not in pre_context]

        result["context_writes_from_ai"] = ai_context_writes
        return result, code, None, [], ai_context_writes

    def _build_messages(
        self,
        user_message: str,
        steps: List[Dict[str, Any]],
        snapshot: Dict[str, Any],
        history: List[Dict[str, str]],
        context_ledger: Optional[Any] = None,
        context_service: Optional[SessionContextService] = None,
    ) -> List[Dict[str, str]]:
        steps_text = ""
        if steps:
            lines = []
            for i, step in enumerate(steps, 1):
                source = step.get("source", "record")
                desc = step.get("description", step.get("action", ""))
                lines.append(f"{i}. [{source}] {desc}")
            steps_text = "\n".join(lines)

        frame_lines = _snapshot_frame_lines(snapshot)
        resolved_context_service = self._get_context_service(
            context_ledger=context_ledger,
            context_service=context_service,
        )
        current_context_lines = self._build_current_context_lines(resolved_context_service)

        context = f"""## History Steps
{steps_text or "(none)"}

## Current Context
{chr(10).join(current_context_lines) if current_context_lines else "(none)"}

## Current Page Snapshot
{chr(10).join(frame_lines) or "(no observable elements)"}

## User Instruction
{user_message}"""

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": context})
        return messages

    async def _stream_llm(
        self,
        messages: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        model = get_llm_model(config=model_config, streaming=True)
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        lc_messages = []
        for message in messages:
            if message["role"] == "system":
                lc_messages.append(SystemMessage(content=message["content"]))
            elif message["role"] == "user":
                lc_messages.append(HumanMessage(content=message["content"]))
            elif message["role"] == "assistant":
                lc_messages.append(AIMessage(content=message["content"]))

        async for chunk in model.astream(lc_messages):
            text = _extract_llm_chunk_text(chunk)
            if text:
                yield text
                continue
            fallback = _extract_llm_chunk_fallback_text(chunk)
            if fallback:
                yield fallback

    @staticmethod
    def _extract_structured_intent(text: str) -> Optional[Dict[str, Any]]:
        stripped = text.strip()
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("action"):
            return parsed

        # Try markdown code blocks (```json or bare ```)
        for pattern in [
            r"```json\s*\n(.*?)```",
            r"```\s*\n(.*?)```",
        ]:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                try:
                    parsed = json.loads(match.group(1).strip())
                except Exception:
                    continue
                if isinstance(parsed, dict) and parsed.get("action"):
                    return parsed
        return None

    @staticmethod
    def _extract_structured_intents(text: str) -> List[Dict[str, Any]]:
        """Extract one or more structured intents from LLM response.

        Supports both single JSON object and JSON array of objects.
        Returns a list (empty if no valid intent found).
        """
        stripped = text.strip()

        # Try direct JSON parse
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                results = [item for item in parsed if isinstance(item, dict) and item.get("action")]
                if results:
                    return results
            if isinstance(parsed, dict) and parsed.get("action"):
                return [parsed]
        except Exception:
            pass

        # Try extracting from markdown code block (```json or bare ```)
        for pattern in [
            r"```json\s*\n(.*?)```",
            r"```\s*\n(.*?)```",
        ]:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                try:
                    parsed = json.loads(match.group(1).strip())
                    if isinstance(parsed, list):
                        results = [item for item in parsed if isinstance(item, dict) and item.get("action")]
                        if results:
                            return results
                    if isinstance(parsed, dict) and parsed.get("action"):
                        return [parsed]
                except Exception:
                    pass

        # Fallback: scan for a JSON array [...] anywhere in the text
        decoder = json.JSONDecoder()
        idx = text.find("[")
        while idx >= 0:
            try:
                parsed, _ = decoder.raw_decode(text, idx)
                if isinstance(parsed, list):
                    results = [item for item in parsed if isinstance(item, dict) and item.get("action")]
                    if results:
                        return results
            except Exception:
                pass
            idx = text.find("[", idx + 1)

        # Fallback to single intent extraction
        single = RPAAssistant._extract_structured_intent(text)
        if single:
            return [single]
        return []

    @staticmethod
    def _build_context_from_ledger(context_ledger: Any) -> Dict[str, str]:
        """Build a flat context dict from the session's context ledger."""
        service = RPAAssistant._get_context_service(context_ledger=context_ledger)
        if service is None:
            return {}
        return {
            key: str(value)
            for key, value in service.build_current_context().items()
            if value is not None
        }

    @staticmethod
    def _normalize_step_context_reads(
        *,
        context_service: Optional[SessionContextService],
        context_reads: List[str] | None,
        step_data: Optional[Dict[str, Any]],
    ) -> List[str]:
        if context_service is None:
            deduped: List[str] = []
            seen: set[str] = set()
            for read in context_reads or []:
                normalized = str(read).strip()
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    deduped.append(normalized)
            return deduped

        legacy_parts: List[str] = []
        for field in ("value", "prompt", "description", "target"):
            value = (step_data or {}).get(field)
            if isinstance(value, str) and value.strip():
                legacy_parts.append(value)

        legacy_text = "\n".join(legacy_parts) if legacy_parts else None
        return context_service.collect_declared_reads(
            context_reads or [],
            legacy_text=legacy_text,
        )

    @staticmethod
    def _resolve_context_in_intent(
        intent: Dict[str, Any],
        context_ledger: Optional[Any],
    ) -> List[str]:
        """Replace ${key} references in intent values with context ledger values.

        Returns the list of context keys that were read.
        """
        service = RPAAssistant._get_context_service(context_ledger=context_ledger)
        if service is None:
            return []

        import re as _re
        context_reads: List[str] = []
        current_context = service.build_current_context()

        def _replace_ref(match: Any) -> str:
            key = match.group(1)
            value = current_context.get(key)
            if value is not None:
                if key not in context_reads:
                    context_reads.append(key)
                return str(value)
            return match.group(0)  # leave unresolved references as-is

        for field in ("value",):
            val = intent.get(field)
            if isinstance(val, str) and "${" in val:
                intent[field] = _re.sub(r"\$\{(\w+)\}", _replace_ref, val)

        return context_reads

    @staticmethod
    def _get_context_service(
        *,
        context_ledger: Optional[Any] = None,
        context_service: Optional[SessionContextService] = None,
    ) -> Optional[SessionContextService]:
        if context_service is not None:
            return context_service
        if context_ledger is None:
            return None
        return SessionContextService(context_ledger)

    @staticmethod
    def _build_current_context_lines(
        context_service: Optional[SessionContextService],
    ) -> List[str]:
        if context_service is None:
            return []
        return [
            f"{key}: {value}"
            for key, value in context_service.build_current_context().items()
        ]

    @staticmethod
    def _extract_context_query_text(intent: Dict[str, Any]) -> str:
        for field in ("prompt", "description", "value"):
            value = intent.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @classmethod
    def _answer_from_context_service(
        cls,
        intent: Dict[str, Any],
        *,
        context_ledger: Optional[Any],
    ) -> Optional[Dict[str, Any]]:
        context_service = cls._get_context_service(context_ledger=context_ledger)
        if context_service is None:
            return None
        if str(intent.get("action", "")).lower() != "answer":
            return None

        query = cls._extract_context_query_text(intent)
        if not query:
            return None

        return context_service.maybe_answer_context_query(query)

    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        pattern = r"```python\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        pattern2 = r"(async def run\(page\):.*)"
        match2 = re.search(pattern2, text, re.DOTALL)
        if match2:
            return match2.group(1).strip()
        pattern3 = r"(def run\(page\):.*)"
        match3 = re.search(pattern3, text, re.DOTALL)
        if match3:
            return match3.group(1).strip()
        return None

    @staticmethod
    def _extract_function_body(code: str) -> str:
        lines = code.split("\n")
        body_lines = []
        in_body = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("async def run(") or stripped.startswith("def run("):
                in_body = True
                continue
            if in_body:
                if line.startswith("    "):
                    body_lines.append(line[4:])
                elif line.strip() == "":
                    body_lines.append("")
                else:
                    body_lines.append(line)
        return "\n".join(body_lines).strip()

    async def _get_page_elements(self, page: Page) -> str:
        return await _get_page_elements(page)

    async def _execute_on_page(self, page: Page, code: str, context: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return await _execute_on_page(page, code, context)
