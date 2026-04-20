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

# Active ReAct agent instances keyed by session_id
_active_agents: Dict[str, "RPAReActAgent"] = {}

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
  "action": "navigate|click|fill|extract_text|press",
  "description": "short action summary",
  "prompt": "original user instruction",
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
7. extract_text is for temporary page reading only; it must not write context and must not declare result_key or result_keys.
8. Any context-producing extraction, whether single-field or multi-field, must use ai_script / code path and declare context_bindings as the exact ASCII snake_case keys to write, then return an output_payload JSON object containing those keys.
9. Do not use result_key or result_keys for context writes.
10. When extracting multiple values in one step via Python code, print a JSON object mapping each declared context_bindings key to its value.
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


async def _execute_on_page(page: Page, code: str) -> Dict[str, Any]:
    """Execute AI-generated code directly on the page object."""
    try:
        await page.evaluate("window.__rpa_paused = true")
    except Exception:
        pass
    try:
        namespace: Dict[str, Any] = {"page": page}
        exec(compile(code, "<rpa_assistant>", "exec"), namespace)
        if "run" in namespace and callable(namespace["run"]):
            ret = await asyncio.wait_for(namespace["run"](page), timeout=EXECUTION_TIMEOUT_S)
            return {"success": True, "output": str(ret) if ret else "ok", "error": None}
        else:
            return {"success": False, "output": "", "error": "No run(page) function defined"}
    except asyncio.TimeoutError:
        return {"success": False, "output": "", "error": f"Command execution timed out ({EXECUTION_TIMEOUT_S:.0f}s)"}
    except Exception:
        import traceback
        return {"success": False, "output": "", "error": traceback.format_exc()}
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

    # Build lookup: node_id -> content_node for rendering inside containers
    content_by_id: Dict[str, Dict[str, Any]] = {}
    for cn in snapshot.get("content_nodes", []):
        nid = cn.get("node_id")
        if nid:
            content_by_id[nid] = cn

    # Group content nodes by container_id
    container_content: Dict[str, List[Dict[str, Any]]] = {}
    for cn in snapshot.get("content_nodes", []):
        cid = cn.get("container_id")
        if cid:
            container_content.setdefault(cid, []).append(cn)

    for container in snapshot.get("containers", []):
        cid = container.get("container_id", "")
        content_ids = container.get("child_content_ids") or []
        lines.append(
            "Container: "
            f"{container.get('container_kind', 'container')} "
            f"{container.get('name', '')} "
            f"(actionable={len(container.get('child_actionable_ids') or [])}, "
            f"content={len(content_ids)})"
        )
        # Render content nodes inside this container so the LLM sees field values
        for cn in container_content.get(cid, []):
            kind = cn.get("semantic_kind", "text")
            text = cn.get("text", "")
            if text:
                lines.append(f"  - {kind}: {text}")

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


REACT_SYSTEM_PROMPT = """You are an RPA automation agent.

You receive a goal and must iteratively observe the current page, decide the next atomic action, execute it, and continue until the goal is complete.

Return exactly one JSON object per turn, not wrapped in markdown.

Preferred format:
{
  "thought": "brief reasoning about the current page and next step",
  "action": "execute|done|abort",
  "operation": "navigate|click|fill|extract_text|press",
  "description": "short action summary",
  "target_hint": {
    "role": "button|link|textbox|...",
    "name": "semantic label if known"
  },
  "collection_hint": {
    "kind": "search_results|table_rows|cards"
  },
  "ordinal": "first|last|1|2|3",
  "value": "text to fill or key to press when relevant",
  "risk": "none|high",
  "risk_reason": "required when risk is high"
}

Rules:
1. Prefer structured atomic actions with operation/target_hint/collection_hint over raw Playwright code.
2. Use collection semantics for first, last, and nth requests. Do not hard-code dynamic titles or href values.
3. For opening a website or jumping to a known URL, use operation=navigate with the URL in value. Do not refer to the browser address bar as a page textbox.
4. The backend resolves iframe context automatically from the snapshot. Do not invent iframe selectors unless the user explicitly names a frame.
5. Only use the code field for custom Playwright code when the action cannot be expressed as one atomic structured action.
6. For irreversible operations such as submit, delete, pay, or authorize, set risk to high.
7. extract_text is for temporary page reading only; it must not write context.
8. Any context-producing extraction, whether single-field or multi-field, must use ai_script / code path and declare context_bindings as the exact ASCII snake_case keys to write, then return an output_payload JSON object containing those keys.
9. Do not use result_key or result_keys for context writes.
10. When the extraction output must be written to context, do not rely on extract_text; use ai_script or Python code with explicit context_bindings and output_payload.
11. When extracting multiple values in one step via Python code, print a JSON object mapping each declared context_bindings key to its value.
"""




class RPAReActAgent:
    """ReAct-based autonomous agent: Observe → Think → Act loop."""

    MAX_STEPS = 20

    def __init__(self):
        self._confirm_event: Optional[asyncio.Event] = None
        self._confirm_approved: bool = False
        self._aborted: bool = False
        self._history: List[Dict[str, str]] = []  # persists across turns

    def resolve_confirm(self, approved: bool) -> None:
        self._confirm_approved = approved
        if self._confirm_event:
            self._confirm_event.set()

    def abort(self) -> None:
        self._aborted = True
        if self._confirm_event:
            self._confirm_event.set()

    async def run(
        self,
        session_id: str,
        page: Page,
        goal: str,
        existing_steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Page]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self._aborted = False
        steps_done = 0

        # Append new user goal to persistent history
        steps_summary = ""
        if existing_steps:
            lines = [f"{i+1}. {s.get('description', s.get('action', ''))}" for i, s in enumerate(existing_steps)]
            steps_summary = "\nExisting steps:\n" + "\n".join(lines) + "\n"
        self._history.append({"role": "user", "content": f"Goal: {goal}{steps_summary}"})

        for iteration in range(self.MAX_STEPS):
            if self._aborted:
                yield {"event": "agent_aborted", "data": {"reason": "用户中止"}}
                return

            # Observe
            current_page = page_provider() if page_provider else page
            if current_page is None:
                yield {"event": "agent_aborted", "data": {"reason": "No active page available"}}
                return
            snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
            obs = self._build_observation(snapshot, steps_done)
            self._history.append({"role": "user", "content": obs})

            # Think — stream LLM response
            full_response = ""
            async for chunk in self._stream_llm(self._history, model_config):
                full_response += chunk

            self._history.append({"role": "assistant", "content": full_response})

            # Parse JSON
            parsed = self._parse_json(full_response)
            if not parsed:
                yield {"event": "agent_aborted", "data": {"reason": f"Unable to parse agent response: {full_response[:200]}"}}
                return

            thought = parsed.get("thought", "")
            action = parsed.get("action", "execute")
            structured_intent = self._extract_structured_execute_intent(parsed, goal)
            code = parsed.get("code", "")
            description = parsed.get("description", "Execute step")
            risk = parsed.get("risk", "none")
            risk_reason = parsed.get("risk_reason", "")
            action_payload = code or ""
            if structured_intent:
                action_payload = json.dumps(structured_intent, ensure_ascii=False)

            yield {"event": "agent_thought", "data": {"text": thought}}

            if action == "done":
                yield {"event": "agent_done", "data": {"total_steps": steps_done}}
                return

            if action == "abort":
                yield {"event": "agent_aborted", "data": {"reason": thought}}
                return

            # High-risk confirmation
            if risk == "high":
                self._confirm_event = asyncio.Event()
                self._confirm_approved = False
                yield {"event": "confirm_required", "data": {
                    "description": description,
                    "risk_reason": risk_reason,
                    "code": action_payload,
                }}
                await self._confirm_event.wait()
                self._confirm_event = None
                if self._aborted:
                    yield {"event": "agent_aborted", "data": {"reason": "User aborted"}}
                    return
                if not self._confirm_approved:
                    self._history.append({"role": "user", "content": "User rejected that step. Continue with a safer next step or finish."})
                    continue

            # Act
            yield {
                "event": "agent_action",
                "data": {
                    "description": description,
                    "code": action_payload,
                },
            }
            current_page = page_provider() if page_provider else page
            if current_page is None:
                yield {"event": "agent_aborted", "data": {"reason": "No active page available"}}
                return
            if structured_intent:
                resolved_intent = resolve_structured_intent(snapshot, structured_intent)
                result = await execute_structured_intent(current_page, resolved_intent)
            else:
                executable = self._wrap_code(code)
                result = await _execute_on_page(current_page, executable)
            if result["success"]:
                steps_done += 1
                step_data = result.get("step") or {
                    "action": "ai_script",
                    "source": "ai",
                    "value": code,
                    "description": description,
                    "prompt": goal,
                }
                output = result.get("output", "")
                # If there's meaningful output, append to description for visibility
                if output and output != "ok" and output != "None":
                    yield {"event": "agent_step_done", "data": {"step": step_data, "output": output}}
                    self._history.append({"role": "user", "content": f"Step succeeded: {description}\nOutput: {output}"})
                else:
                    yield {"event": "agent_step_done", "data": {"step": step_data}}
                    self._history.append({"role": "user", "content": f"Step succeeded: {description}"})
            else:
                error_msg = result.get("error", "Unknown error")
                self._history.append({"role": "user", "content": f"Execution failed: {error_msg[:500]}\nAnalyze the failure and adjust the strategy."})

        yield {"event": "agent_done", "data": {"total_steps": steps_done}}

    @staticmethod
    def _build_observation(snapshot: Dict[str, Any], steps_done: int) -> str:
        frame_lines = _snapshot_frame_lines(snapshot)
        return f"""Current page state:
URL: {snapshot.get('url', '')}
Title: {snapshot.get('title', '')}
Completed steps: {steps_done}

Current page snapshot:
{chr(10).join(frame_lines) or "(no observable elements)"}

Return the next JSON action."""

    @staticmethod
    def _extract_structured_execute_intent(parsed: Dict[str, Any], prompt: str) -> Optional[Dict[str, Any]]:
        action = str(parsed.get("action", "") or "").strip().lower()
        operation = str(parsed.get("operation", "") or "").strip().lower()
        atomic_actions = {"navigate", "click", "fill", "extract_text", "press"}

        if action in atomic_actions:
            operation = action
        if action not in {"", "execute"} and action not in atomic_actions:
            return None
        if operation not in atomic_actions:
            return None

        intent: Dict[str, Any] = {
            "action": operation,
            "description": parsed.get("description", operation),
            "prompt": prompt,
        }
        for key in ("target_hint", "collection_hint", "ordinal", "value", "result_key", "result_keys"):
            value = parsed.get(key)
            if value is not None:
                intent[key] = value
        return intent

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        # Try raw JSON first
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass
        # Try extracting from code block
        m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass
        # Try finding { ... } block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return None

    @staticmethod
    def _wrap_code(code: str) -> str:
        """Wrap bare code in async def run(page) if not already wrapped."""
        stripped = code.strip()
        if stripped.startswith("async def run(") or stripped.startswith("def run("):
            return stripped
        indented = "\n".join("    " + line for line in stripped.splitlines())
        return f"async def run(page):\n{indented}"

    @staticmethod
    async def _stream_llm(
        history: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
        model = get_llm_model(config=model_config, streaming=True)
        lc_messages = [SystemMessage(content=REACT_SYSTEM_PROMPT)]
        for m in history:
            if m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_messages.append(AIMessage(content=m["content"]))
        if hasattr(model, "astream"):
            text_parts: List[str] = []
            fallback_parts: List[str] = []
            async for chunk in model.astream(lc_messages):
                text = _extract_llm_chunk_text(chunk)
                if text:
                    text_parts.append(text)
                    continue
                fallback = _extract_llm_chunk_fallback_text(chunk)
                if fallback:
                    fallback_parts.append(fallback)
            full_text = "".join(text_parts)
            if full_text.strip():
                yield full_text
                return
            fallback_text = "\n".join(part for part in fallback_parts if part).strip()
            if fallback_text:
                yield fallback_text
                return

        response = await model.ainvoke(lc_messages)
        yield _extract_llm_response_text(response)


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
        messages = self._build_messages(message, steps, snapshot, history)

        full_response = ""
        async for chunk_text in self._stream_llm(messages, model_config):
            full_response += chunk_text
            yield {"event": "message_chunk", "data": {"text": chunk_text}}

        yield {"event": "executing", "data": {}}
        _session = rpa_manager.sessions.get(session_id)
        result, final_response, code, resolution, retry_notice, context_reads, execution_trace = await self._execute_with_retry(
            page=page,
            page_provider=page_provider,
            snapshot=snapshot,
            full_response=full_response,
            messages=messages,
            model_config=model_config,
            context_ledger=_session.context_ledger if _session else None,
        )

        if retry_notice:
            yield {"event": "message_chunk", "data": {"text": retry_notice}}
        if resolution:
            yield {"event": "resolution", "data": {"intent": resolution}}

        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": final_response})
        self._trim_history(session_id)

        final_status = self._normalize_final_status(result, retry_happened=bool(execution_trace.get("retry_happened")))

        step_data = None
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

        # ── Compute context reads / writes ─────────────────────────
        context_writes: List[str] = []

        if result["success"] and step_data:
            output_payload = self._normalize_output_payload(step_data, result.get("output"))
            step_data["output_payload"] = output_payload
            step_data["output_schema"] = self._normalize_output_schema(step_data, output_payload)
            context_writes = self._compute_context_writes(
                message=message,
                step_data=step_data,
                resolution=resolution,
            )

            # Persist promoted values into the session ledger
            self._promote_to_ledger(
                rpa_manager=rpa_manager,
                session_id=session_id,
                context_writes=context_writes,
                step_data=step_data,
                output=result.get("output"),
            )

        # Attach context lists to the step payload for downstream use
        if step_data is not None:
            step_data["context_reads"] = context_reads
            step_data["context_writes"] = context_writes
            step_data["context_bindings"] = self._normalize_context_bindings(step_data, context_writes)
            step_data["attempt_summary"] = self._build_attempt_summary(
                execution_trace.get("attempts", []),
                final_status,
            )
            step_data["extraction_source"] = self._normalize_extraction_source(step_data, resolution)
            attempt_context_writes = self._build_context_write_entries(context_writes, output_payload, result.get("output"))
            for attempt_event in execution_trace.get("events", []):
                if attempt_event.get("event") == "attempt_succeeded":
                    attempt_event.setdefault("data", {})["context_writes"] = attempt_context_writes

        for attempt_event in execution_trace.get("events", []):
            yield attempt_event

        yield {
            "event": "result",
            "data": {
                "success": result["success"],
                "status": final_status,
                "error": result.get("error"),
                "step": step_data,
                "output": result.get("output"),
                "context_reads": context_reads,
                "context_writes": context_writes,
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

        ``extract_text`` never promotes context. ``ai_script`` promotes only
        the declared ``context_bindings`` and requires that each binding exists
        in ``output_payload``.
        """
        action = step_data.get("action", "")
        if action == "extract_text" or action != "ai_script":
            return []

        output_payload = step_data.get("output_payload")
        if not isinstance(output_payload, dict):
            output_payload = {}

        context_bindings = step_data.get("context_bindings")
        if isinstance(context_bindings, list):
            keys = [k for k in context_bindings if isinstance(k, str) and k]
        else:
            keys = []

        if not keys:
            return []

        missing_keys = [key for key in keys if key not in output_payload]
        if missing_keys:
            raise ValueError(f"contract_error: missing payload key(s): {', '.join(missing_keys)}")

        return keys

    @staticmethod
    def _extract_action_name(response_text: str) -> str:
        try:
            parsed = json.loads(response_text)
        except (TypeError, json.JSONDecodeError):
            return "unknown"
        if isinstance(parsed, dict):
            action = parsed.get("action")
            if isinstance(action, str) and action:
                return action
        return "unknown"

    @staticmethod
    def _extract_attempt_summary(response_text: str) -> str:
        try:
            parsed = json.loads(response_text)
        except (TypeError, json.JSONDecodeError):
            return ""
        if not isinstance(parsed, dict):
            return ""
        for key in ("description", "thought", "summary"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _build_attempt_event(
        event_type: str,
        *,
        attempt_number: int,
        action: str,
        summary: str = "",
        error: Optional[str] = None,
        retrying: bool = False,
        output_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "attempt": attempt_number,
            "action": action,
        }
        if summary:
            data["summary"] = summary
        if event_type == "attempt_failed":
            data["failure_kind"] = "execution_error"
            data["error"] = error
            data["retrying"] = retrying
        elif event_type == "attempt_succeeded":
            data["output_payload"] = output_payload or {}
        return {"event": event_type, "data": data}

    @staticmethod
    def _build_context_write_entries(
        context_writes: List[str],
        output_payload: Dict[str, Any],
        output: Optional[str],
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for key in context_writes or []:
            if not isinstance(output_payload, dict) or key not in output_payload:
                continue
            value = output_payload[key]
            entries.append({"key": key, "value": value})
        return entries

    @staticmethod
    def _build_attempt_output_event(
        *,
        attempt_number: int,
        plan_source: Dict[str, Any],
        response_text: str,
        fallback_action: str,
    ) -> Dict[str, Any]:
        action = ""
        if isinstance(plan_source.get("action"), str) and plan_source["action"]:
            action = plan_source["action"]
        if not action:
            action = fallback_action

        summary = ""
        for key in ("summary", "description", "prompt", "thought"):
            value = plan_source.get(key)
            if isinstance(value, str) and value.strip():
                summary = value.strip()
                break
        if not summary:
            summary = RPAAssistant._extract_attempt_summary(response_text)

        expected_output_keys = RPAAssistant._collect_expected_output_keys(plan_source)
        data: Dict[str, Any] = {
            "attempt": attempt_number,
            "action": action,
            "summary": summary,
            "expected_output_keys": expected_output_keys,
        }
        return {"event": "attempt_output", "data": data}

    @staticmethod
    def _collect_expected_output_keys(plan_source: Dict[str, Any]) -> List[str]:
        keys: List[str] = []
        output_schema = plan_source.get("output_schema")
        if isinstance(output_schema, dict):
            for key in output_schema.keys():
                if isinstance(key, str) and key and key not in keys:
                    keys.append(key)
        output_payload = plan_source.get("output_payload")
        if isinstance(output_payload, dict):
            for key in output_payload.keys():
                if isinstance(key, str) and key and key not in keys:
                    keys.append(key)
        context_bindings = plan_source.get("context_bindings")
        if isinstance(context_bindings, list):
            for key in context_bindings:
                if isinstance(key, str) and key and key not in keys:
                    keys.append(key)
        result_key = plan_source.get("result_key")
        if isinstance(result_key, str) and result_key and result_key not in keys:
            keys.append(result_key)
        result_keys = plan_source.get("result_keys")
        if isinstance(result_keys, list):
            for key in result_keys:
                if isinstance(key, str) and key and key not in keys:
                    keys.append(key)
        return keys

    @staticmethod
    def _build_attempt_record(
        *,
        attempt_number: int,
        result: Dict[str, Any],
        code: Optional[str],
        resolution: Optional[Dict[str, Any]],
        context_reads: List[str],
        retrying: bool,
    ) -> Dict[str, Any]:
        success = bool(result.get("success"))
        return {
            "attempt": attempt_number,
            "status": "succeeded" if success else "failed",
            "failure_kind": None if success else "execution_error",
            "error": result.get("error"),
            "output": result.get("output"),
            "code": code,
            "resolution": resolution,
            "context_reads": context_reads,
            "retrying": retrying,
        }

    @staticmethod
    def _normalize_output_payload(step_data: Dict[str, Any], output: Optional[str]) -> Dict[str, Any]:
        existing = step_data.get("output_payload")
        if isinstance(existing, dict) and existing:
            return dict(existing)
        if isinstance(output, str) and output.strip():
            try:
                parsed = json.loads(output)
            except (TypeError, json.JSONDecodeError):
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}

    @staticmethod
    def _normalize_output_schema(step_data: Dict[str, Any], output_payload: Dict[str, Any]) -> Dict[str, Any]:
        existing = step_data.get("output_schema")
        if isinstance(existing, dict) and existing:
            return dict(existing)
        if output_payload:
            return {key: "string" for key in output_payload.keys()}
        return {}

    @staticmethod
    def _normalize_context_bindings(step_data: Dict[str, Any], context_writes: List[str]) -> List[str]:
        bindings = step_data.get("context_bindings")
        if isinstance(bindings, list):
            normalized = [key for key in bindings if isinstance(key, str) and key]
            if normalized:
                return normalized
        return list(context_writes or [])

    @staticmethod
    def _build_attempt_summary(attempts: List[Dict[str, Any]], final_status: str) -> Dict[str, Any]:
        if not attempts:
            return {}
        failure_kinds = [
            attempt["failure_kind"]
            for attempt in attempts
            if attempt.get("status") == "failed" and attempt.get("failure_kind")
        ]
        return {
            "attempt_count": len(attempts),
            "final_status": final_status,
            "failure_kinds": failure_kinds,
        }

    @staticmethod
    def _normalize_final_status(result: Dict[str, Any], retry_happened: bool) -> str:
        explicit_status = result.get("status")
        if explicit_status == "partial_success":
            return "partial_success"
        if retry_happened and result.get("success"):
            return "recovered_after_retry"
        if isinstance(explicit_status, str) and explicit_status in {
            "success",
            "failed",
            "recovered_after_retry",
            "partial_success",
        }:
            return explicit_status
        return "success" if result.get("success") else "failed"

    @staticmethod
    def _normalize_extraction_source(
        step_data: Dict[str, Any],
        resolution: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        kind = "ai_script"
        if isinstance(resolution, dict):
            if resolution.get("selected_locator_kind") or resolution.get("locator"):
                kind = "structured_intent"

        source: Dict[str, Any] = {
            "kind": kind,
            "action": step_data.get("action", kind if kind != "structured_intent" else "ai_script"),
        }

        frame_path = None
        if isinstance(step_data.get("frame_path"), list) and step_data["frame_path"]:
            frame_path = list(step_data["frame_path"])
        elif isinstance(resolution, dict) and isinstance(resolution.get("frame_path"), list) and resolution["frame_path"]:
            frame_path = list(resolution["frame_path"])
        if frame_path:
            source["frame_path"] = frame_path

        if isinstance(step_data.get("assistant_diagnostics"), dict):
            diagnostics = step_data["assistant_diagnostics"]
            selected_locator_kind = diagnostics.get("selected_locator_kind")
            if isinstance(selected_locator_kind, str) and selected_locator_kind:
                source["locator_kind"] = selected_locator_kind

        return source

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

        output_payload = step_data.get("output_payload")
        if not isinstance(output_payload, dict):
            output_payload = {}

        missing_keys = [key for key in context_writes if key not in output_payload]
        if missing_keys:
            raise ValueError(f"contract_error: missing payload key(s): {', '.join(missing_keys)}")

        if step_data.get("action") == "ai_script":
            user_explicit = _is_explicit_extraction_request(step_data.get("prompt", ""))
            for key in context_writes:
                rpa_manager.record_context_value(
                    session_id,
                    category="observed",
                    key=key,
                    value=output_payload[key],
                    user_explicit=user_explicit,
                    runtime_required=False,
                    source_step_id=step_data.get("id"),
                    source_kind="assistant_extraction",
                )
            return

        user_explicit = _is_explicit_extraction_request(step_data.get("prompt", ""))
        is_cross_page = any(
            kw in (step_data.get("description", "") + step_data.get("prompt", ""))
            for kw in ["填写", "填入", "复制", "copy", "fill", "transfer", "另一个", "其它", "其他"]
        )

        for key in context_writes:
            if ledger.should_promote_value(
                key=key,
                source="observation",
                user_explicit=user_explicit,
                runtime_required=is_cross_page,
                consumed_later=is_cross_page,
            ):
                value = output_payload[key]
                rpa_manager.record_context_value(
                    session_id,
                    category="observed",
                    key=key,
                    value=value,
                    user_explicit=user_explicit,
                    runtime_required=is_cross_page,
                    source_step_id=step_data.get("id"),
                    source_kind="assistant_extraction",
                )

    async def _execute_with_retry(
        self,
        page: Page,
        page_provider: Optional[Callable[[], Optional[Page]]],
        snapshot: Dict[str, Any],
        full_response: str,
        messages: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]],
        context_ledger: Optional[Any] = None,
    ) -> tuple[Dict[str, Any], str, Optional[str], Optional[Dict[str, Any]], str, List[str], Dict[str, Any]]:
        attempts: List[Dict[str, Any]] = []
        attempt_events: List[Dict[str, Any]] = []
        current_page = page_provider() if page_provider else page
        if current_page is None:
            execution_trace = {"attempts": attempts, "events": attempt_events, "final_status": "failed", "retry_happened": False}
            return {"success": False, "error": "No active page available", "output": ""}, full_response, None, None, "", [], execution_trace

        try:
            plan_source = self._extract_structured_intent(full_response) or {}
            attempt_events.append(
                self._build_attempt_event(
                    "attempt_started",
                    attempt_number=1,
                    action=self._extract_action_name(full_response),
                    summary=self._extract_attempt_summary(full_response),
                )
            )
            attempt_events.append(
                self._build_attempt_output_event(
                    attempt_number=1,
                    plan_source=plan_source,
                    response_text=full_response,
                    fallback_action=self._extract_action_name(full_response),
                )
            )
            result, code, resolution, context_reads = await self._execute_single_response(current_page, snapshot, full_response, context_ledger)
            attempts.append(
                self._build_attempt_record(
                    attempt_number=1,
                    result=result,
                    code=code,
                    resolution=resolution,
                    context_reads=context_reads,
                    retrying=not result["success"],
                )
            )
            if result["success"]:
                attempt_events.append(
                    self._build_attempt_event(
                        "attempt_succeeded",
                        attempt_number=1,
                        action=self._extract_action_name(full_response),
                        output_payload=self._normalize_output_payload(result.get("step") or {}, result.get("output")),
                    )
                )
                execution_trace = {"attempts": attempts, "events": attempt_events, "final_status": "success", "retry_happened": False}
                return result, full_response, code, resolution, "", context_reads, execution_trace
            attempt_events.append(
                self._build_attempt_event(
                    "attempt_failed",
                    attempt_number=1,
                    action=self._extract_action_name(full_response),
                    error=result.get("error"),
                    retrying=True,
                )
            )
        except Exception as exc:
            result = {"success": False, "error": str(exc), "output": ""}
            code = None
            resolution = None
            attempts.append(
                self._build_attempt_record(
                    attempt_number=1,
                    result=result,
                    code=code,
                    resolution=resolution,
                    context_reads=[],
                    retrying=True,
                )
            )
            attempt_events.append(
                self._build_attempt_event(
                    "attempt_failed",
                    attempt_number=1,
                    action=self._extract_action_name(full_response),
                    error=str(exc),
                    retrying=True,
                )
            )

        retry_messages = messages + [
            {"role": "assistant", "content": full_response},
            {"role": "user", "content": f"Execution error: {result['error']}\nPlease fix it and retry."},
        ]
        retry_response = ""
        async for chunk_text in self._stream_llm(retry_messages, model_config):
            retry_response += chunk_text

        current_page = page_provider() if page_provider else page
        if current_page is None:
            execution_trace = {"attempts": attempts, "events": attempt_events, "final_status": "failed", "retry_happened": True}
            return {"success": False, "error": "No active page available", "output": ""}, retry_response, None, None, "\n\nExecution failed. Retrying.\n\n", [], execution_trace

        retry_snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
        attempt_events.append(
            self._build_attempt_event(
                "attempt_started",
                attempt_number=2,
                action=self._extract_action_name(retry_response),
                summary=self._extract_attempt_summary(retry_response),
            )
        )
        try:
            retry_plan_source = self._extract_structured_intent(retry_response) or {}
            attempt_events.append(
                self._build_attempt_output_event(
                    attempt_number=2,
                    plan_source=retry_plan_source,
                    response_text=retry_response,
                    fallback_action=self._extract_action_name(retry_response),
                )
            )
            retry_result, retry_code, retry_resolution, retry_reads = await self._execute_single_response(
                current_page,
                retry_snapshot,
                retry_response,
                context_ledger,
            )
            attempts.append(
                self._build_attempt_record(
                    attempt_number=2,
                    result=retry_result,
                    code=retry_code,
                    resolution=retry_resolution,
                    context_reads=retry_reads,
                    retrying=False,
                )
            )
            if retry_result["success"]:
                attempt_events.append(
                    self._build_attempt_event(
                        "attempt_succeeded",
                        attempt_number=2,
                        action=self._extract_action_name(retry_response),
                        output_payload=self._normalize_output_payload(retry_result.get("step") or {}, retry_result.get("output")),
                    )
                )
                final_status = self._normalize_final_status(retry_result, retry_happened=True)
            else:
                attempt_events.append(
                    self._build_attempt_event(
                        "attempt_failed",
                        attempt_number=2,
                        action=self._extract_action_name(retry_response),
                        error=retry_result.get("error"),
                        retrying=False,
                    )
                )
                final_status = self._normalize_final_status(retry_result, retry_happened=True)
            execution_trace = {
                "attempts": attempts,
                "events": attempt_events,
                "final_status": final_status,
                "retry_happened": True,
            }
            return retry_result, retry_response, retry_code, retry_resolution, "\n\nExecution failed. Retrying.\n\n", retry_reads, execution_trace
        except Exception as exc:
            attempts.append(
                self._build_attempt_record(
                    attempt_number=2,
                    result={"success": False, "error": str(exc), "output": ""},
                    code=None,
                    resolution=None,
                    context_reads=[],
                    retrying=False,
                )
            )
            attempt_events.append(
                self._build_attempt_event(
                    "attempt_failed",
                    attempt_number=2,
                    action=self._extract_action_name(retry_response),
                    error=str(exc),
                    retrying=False,
                )
            )
            execution_trace = {"attempts": attempts, "events": attempt_events, "final_status": "failed", "retry_happened": True}
            return {"success": False, "error": str(exc), "output": ""}, retry_response, None, None, "\n\nExecution failed. Retrying.\n\n", [], execution_trace

    async def _execute_single_response(
        self,
        current_page: Page,
        snapshot: Dict[str, Any],
        full_response: str,
        context_ledger: Optional[Any] = None,
    ) -> tuple[Dict[str, Any], Optional[str], Optional[Dict[str, Any]], List[str]]:
        """Execute a single LLM response.

        Returns (result, code, resolution, context_reads).
        """
        structured_intent = self._extract_structured_intent(full_response)
        if structured_intent:
            action = str(structured_intent.get("action", "") or "").strip().lower()
            if action == "ai_script":
                script = structured_intent.get("code") or structured_intent.get("value") or ""
                if not isinstance(script, str) or not script.strip():
                    raise ValueError("ai_script envelope must include executable code in code or value")

                executable = RPAReActAgent._wrap_code(script)
                result = await self._execute_on_page(current_page, executable)
                runtime_output = result.get("output")
                runtime_output_payload: Dict[str, Any] = {}
                runtime_output_is_dict = False
                if isinstance(runtime_output, dict):
                    runtime_output_payload = dict(runtime_output)
                    runtime_output_is_dict = True
                elif isinstance(runtime_output, str) and runtime_output.strip():
                    try:
                        parsed_runtime_output = json.loads(runtime_output)
                    except (TypeError, json.JSONDecodeError):
                        parsed_runtime_output = None
                    if isinstance(parsed_runtime_output, dict):
                        runtime_output_payload = dict(parsed_runtime_output)
                        runtime_output_is_dict = True

                step: Dict[str, Any] = {
                    "action": "ai_script",
                    "source": "ai",
                    "value": script,
                    "description": structured_intent.get("description", "Execute ai_script"),
                    "prompt": structured_intent.get("prompt"),
                }
                for key in ("context_bindings", "output_schema", "output_payload"):
                    value = structured_intent.get(key)
                    if value is not None:
                        step[key] = value
                if runtime_output_is_dict:
                    step["output_payload"] = runtime_output_payload
                else:
                    step["output_payload"] = {}
                output_payload = dict(step["output_payload"])
                step["output_schema"] = self._normalize_output_schema(step, output_payload)
                result["step"] = step
                return result, executable, None, []

            # Resolve ${key} references in the intent using context ledger
            context_reads = self._resolve_context_in_intent(structured_intent, context_ledger)
            resolved_intent = resolve_structured_intent(snapshot, structured_intent)
            result = await execute_structured_intent(current_page, resolved_intent)
            return result, None, resolved_intent, context_reads

        code = self._extract_code(full_response)
        if not code:
            raise ValueError("Unable to extract structured intent or executable code from assistant response")
        result = await self._execute_on_page(current_page, code)
        return result, code, None, []

    def _build_messages(
        self,
        user_message: str,
        steps: List[Dict[str, Any]],
        snapshot: Dict[str, Any],
        history: List[Dict[str, str]],
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

        context = f"""## History Steps
{steps_text or "(none)"}

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

        match = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
            except Exception:
                return None
            if isinstance(parsed, dict) and parsed.get("action"):
                return parsed
        return None

    @staticmethod
    def _resolve_context_in_intent(
        intent: Dict[str, Any],
        context_ledger: Optional[Any],
    ) -> List[str]:
        """Replace ${key} references in intent values with context ledger values.

        Returns the list of context keys that were read.
        """
        if context_ledger is None:
            return []

        import re as _re
        context_reads: List[str] = []

        def _replace_ref(match: Any) -> str:
            key = match.group(1)
            has_value, value = RPAAssistant._lookup_context_value(context_ledger, key)
            if has_value:
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
    def _lookup_context_value(context_ledger: Any, key: str) -> tuple[bool, Any]:
        for store_name in ("observed_values", "derived_values"):
            store = getattr(context_ledger, store_name, None)
            if not isinstance(store, dict) or key not in store:
                continue
            entry = store[key]
            if isinstance(entry, dict):
                return True, entry.get("value")
            if hasattr(entry, "value"):
                return True, getattr(entry, "value")
            return True, entry
        return False, None

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

    async def _execute_on_page(self, page: Page, code: str) -> Dict[str, Any]:
        return await _execute_on_page(page, code)
