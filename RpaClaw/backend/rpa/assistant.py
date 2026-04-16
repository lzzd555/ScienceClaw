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
7. For extract_text actions, include result_key as a short ASCII snake_case key such as latest_issue_title. Do not use Chinese, spaces, or hyphens.
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


REACT_SYSTEM_PROMPT = """You are an RPA automation agent.

You receive a goal and must iteratively observe the current page, decide the next atomic action, execute it, and continue until the goal is complete.

Return exactly one JSON object per turn, not wrapped in markdown.

Preferred format:
{
  "thought": "one short sentence about the current page and next step",
  "action": "execute|done|abort",
  "operation": "navigate|click|fill|extract_text|press|ai_command",
  "description": "short action summary",
  "result_key": "short_ascii_snake_case_key_for_extracted_value",
  "target_hint": {
    "role": "button|link|textbox|...",
    "name": "semantic label if known"
  },
  "collection_hint": {
    "kind": "search_results|table_rows|cards"
  },
  "ordinal": "first|last|1|2|3",
  "value": "text to fill or key to press when relevant, or extraction prompt for ai_command",
  "data_format": "text|json (required for ai_command, specifies output format)",
  "final_output": "required when action=done and the user asked for a final answer format",
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
7. For data extraction, data collection, or any task that requires reading and summarizing page content, prefer operation=ai_command. Set value to a clear description of what data to extract, data_format to "json" for structured output or "text" for plain text, and result_key to a short ASCII snake_case key.
8. Only use extract_text when you need to read a specific element's text as part of a navigation or action decision. For general data collection tasks, use ai_command instead.
9. Do not mark the task done just because the data is visible on the page.
10. Execute the extraction step first and return the extracted value.
11. Keep thought to a single short sentence. Do not use bullet lists, raw line breaks, or long prose in thought.
12. If the user requires a strict final format such as a JSON array, keep the control object wrapper and put the exact final answer in final_output.
13. If the user asks for all items, a table, multiple records, or a strict JSON array of objects, use operation=ai_command with data_format="json" to collect the full dataset.
14. When operation is present, omit the code field unless custom Playwright code is strictly necessary.
15. If the same action was already attempted (shown in Recent actions) and the page state did not change, do NOT repeat it. Try a different approach, adjust the target, or set action to "done" with whatever partial result is available.
16. Do NOT issue a navigate step immediately after a click that already caused a page navigation. If a link click changed the URL, the navigation is already complete — proceed to the next task instead of navigating again.
"""




class RPAReActAgent:
    """ReAct-based autonomous agent: Observe → Think → Act loop."""

    MAX_STEPS = 20

    def __init__(self):
        self._confirm_event: Optional[asyncio.Event] = None
        self._confirm_approved: bool = False
        self._aborted: bool = False
        self._history: List[Dict[str, str]] = []  # persists across turns
        self._recent_actions: List[str] = []

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
        self._recent_actions = []
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
            obs = self._build_observation(snapshot, steps_done, self._recent_actions[-5:])
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
            final_output = parsed.get("final_output")
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
                event_data = {"total_steps": steps_done}
                if final_output is not None:
                    event_data["final_output"] = final_output
                yield {"event": "agent_done", "data": event_data}
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

            # Duplicate action detection — skip if same as the last action
            if structured_intent:
                action_sig = self._action_signature(structured_intent)
            else:
                action_sig = f"code:{description[:50]}"
            if self._recent_actions and self._recent_actions[-1] == action_sig:
                self._history.append({"role": "user", "content":
                    f"WARNING: You just attempted '{action_sig}' and the page may not have changed. "
                    f"Try a different approach or declare done."})
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
            try:
                if structured_intent and structured_intent.get("action") == "ai_command":
                    result = await self._execute_ai_command(current_page, structured_intent, model_config)
                elif structured_intent:
                    resolved_intent = resolve_structured_intent(snapshot, structured_intent)
                    result = await execute_structured_intent(current_page, resolved_intent)
                else:
                    executable = self._wrap_code(code)
                    result = await _execute_on_page(current_page, executable)
            except Exception as act_exc:
                import traceback as _tb
                result = {"success": False, "error": f"{type(act_exc).__name__}: {act_exc}\n{_tb.format_exc()[:500]}"}
            if result["success"]:
                steps_done += 1
                # Track action for duplicate detection
                if structured_intent:
                    self._recent_actions.append(self._action_signature(structured_intent))
                else:
                    self._recent_actions.append(f"code:{description[:50]}")
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

    async def _execute_ai_command(
        self,
        page: Page,
        intent: Dict[str, Any],
        model_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute ai_command: extract data from page using LLM.

        Consistent with the runtime _ai_command helper in generated scripts,
        which reads page.inner_text("body") as context.
        """
        prompt = intent.get("value") or intent.get("description") or intent.get("prompt") or ""
        data_format = intent.get("data_format", "text")

        # Build page context (same as runtime _ai_command behavior)
        try:
            context = await page.inner_text("body")
            if len(context) > 50000:
                context = context[:50000]
        except Exception:
            context = ""

        # Build LLM messages
        from langchain_core.messages import HumanMessage, SystemMessage

        messages: List[Any] = []
        if context:
            system_text = f"以下是当前页面的文本内容：\n\n{context}"
            if data_format == "json":
                system_text += "\n请以合法 JSON 格式输出结果，不要包含 markdown 标记。"
            messages.append(SystemMessage(content=system_text))
        elif data_format == "json":
            messages.append(SystemMessage(content="请以合法 JSON 格式输出结果，不要包含 markdown 标记。"))
        messages.append(HumanMessage(content=prompt))

        model = get_llm_model(config=model_config, streaming=False)
        response = await model.ainvoke(messages)
        output = response.content if hasattr(response, "content") else str(response)
        output = output.strip()

        return {
            "success": True,
            "output": output,
            "step": {
                "action": "ai_command",
                "source": "ai",
                "value": None,
                "description": intent.get("description") or prompt,
                "prompt": intent.get("prompt") or prompt,
                "result_key": intent.get("result_key"),
                "data_format": data_format,
            },
        }

    @staticmethod
    def _action_signature(intent: Dict[str, Any]) -> str:
        operation = intent.get("action", "")
        target_hint = intent.get("target_hint") or {}
        name = str(target_hint.get("name") or target_hint.get("text") or target_hint.get("value") or "")[:30]
        value = str(intent.get("value") or "")[:30]
        return f"{operation}|{name}|{value}"

    @staticmethod
    def _build_observation(snapshot: Dict[str, Any], steps_done: int, recent_actions: Optional[List[str]] = None) -> str:
        frame_lines = _snapshot_frame_lines(snapshot)
        recent_section = ""
        if recent_actions:
            lines = [f"  {i+1}. {action}" for i, action in enumerate(recent_actions)]
            recent_section = "\nRecent actions:\n" + "\n".join(lines) + "\n"
        return f"""Current page state:
URL: {snapshot.get('url', '')}
Title: {snapshot.get('title', '')}
Completed steps: {steps_done}{recent_section}

Current page snapshot:
{chr(10).join(frame_lines) or "(no observable elements)"}

Return the next JSON action."""

    @staticmethod
    def _extract_structured_execute_intent(parsed: Dict[str, Any], prompt: str) -> Optional[Dict[str, Any]]:
        action = str(parsed.get("action", "") or "").strip().lower()
        operation = str(parsed.get("operation", "") or "").strip().lower()
        atomic_actions = {"navigate", "click", "fill", "extract_text", "press", "ai_command"}

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
        for key in ("target_hint", "collection_hint", "ordinal", "value", "result_key", "data_format"):
            value = parsed.get(key)
            if value is not None:
                intent[key] = value
        return intent

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        candidates: List[str] = [text]
        # Try extracting from code block
        m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            candidates.append(m.group(1).strip())
        balanced = RPAReActAgent._extract_balanced_json(text)
        if balanced:
            candidates.append(balanced)
        stripped_code = RPAReActAgent._drop_trailing_code_field(text)
        if stripped_code:
            candidates.append(stripped_code)

        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            coerced = RPAReActAgent._coerce_parsed_response(payload)
            if coerced:
                return coerced

        return RPAReActAgent._salvage_terminal_response(text)

    @staticmethod
    def _coerce_parsed_response(payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {
                "thought": "",
                "action": "done",
                "description": "Return final output",
                "final_output": payload,
                "risk": "none",
                "risk_reason": "",
            }
        return None

    @staticmethod
    def _extract_balanced_json(text: str) -> Optional[str]:
        start = -1
        opening = ""
        closing = ""
        for idx, char in enumerate(text):
            if char == "{":
                start = idx
                opening = "{"
                closing = "}"
                break
            if char == "[":
                start = idx
                opening = "["
                closing = "]"
                break
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        return None

    @staticmethod
    def _extract_simple_json_string(text: str, field: str) -> Optional[str]:
        pattern = rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"'
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(f'"{match.group(1)}"')
        except Exception:
            return match.group(1)

    @staticmethod
    def _drop_trailing_code_field(text: str) -> Optional[str]:
        if '"code"' not in text:
            return None
        sanitized = re.sub(r',\s*"code"\s*:\s*".*$', "}", text, flags=re.DOTALL)
        if sanitized != text:
            return sanitized
        sanitized = re.sub(r'"code"\s*:\s*".*$', "}", text, flags=re.DOTALL)
        if sanitized != text:
            return sanitized
        return None

    @staticmethod
    def _extract_balanced_field_value(text: str, field: str) -> Any:
        match = re.search(rf'"{re.escape(field)}"\s*:\s*', text)
        if not match:
            return None
        remainder = text[match.end():].lstrip()
        extracted = RPAReActAgent._extract_balanced_json(remainder)
        if not extracted:
            return None
        try:
            return json.loads(extracted)
        except Exception:
            return None

    @staticmethod
    def _salvage_terminal_response(text: str) -> Optional[Dict[str, Any]]:
        action = RPAReActAgent._extract_simple_json_string(text, "action")
        if action not in {"execute", "done", "abort"}:
            return None
        parsed: Dict[str, Any] = {
            "thought": RPAReActAgent._extract_simple_json_string(text, "thought") or "",
            "action": action,
            "description": RPAReActAgent._extract_simple_json_string(text, "description") or "",
            "risk": RPAReActAgent._extract_simple_json_string(text, "risk") or "none",
            "risk_reason": RPAReActAgent._extract_simple_json_string(text, "risk_reason") or "",
        }
        operation = RPAReActAgent._extract_simple_json_string(text, "operation")
        if operation:
            parsed["operation"] = operation
        result_key = RPAReActAgent._extract_simple_json_string(text, "result_key")
        if result_key:
            parsed["result_key"] = result_key
        value = RPAReActAgent._extract_simple_json_string(text, "value")
        if value:
            parsed["value"] = value
        final_output = RPAReActAgent._extract_balanced_field_value(text, "final_output")
        if final_output is not None:
            parsed["final_output"] = final_output
        target_hint = RPAReActAgent._extract_balanced_field_value(text, "target_hint")
        if isinstance(target_hint, dict):
            parsed["target_hint"] = target_hint
        collection_hint = RPAReActAgent._extract_balanced_field_value(text, "collection_hint")
        if isinstance(collection_hint, dict):
            parsed["collection_hint"] = collection_hint
        return parsed

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
        result, final_response, code, resolution, retry_notice = await self._execute_with_retry(
            page=page,
            page_provider=page_provider,
            snapshot=snapshot,
            full_response=full_response,
            messages=messages,
            model_config=model_config,
        )

        if retry_notice:
            yield {"event": "message_chunk", "data": {"text": retry_notice}}
        if resolution:
            yield {"event": "resolution", "data": {"intent": resolution}}

        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": final_response})
        self._trim_history(session_id)

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

        yield {
            "event": "result",
            "data": {
                "success": result["success"],
                "error": result.get("error"),
                "step": step_data,
                "output": result.get("output"),
            },
        }
        yield {"event": "done", "data": {}}

    async def _execute_with_retry(
        self,
        page: Page,
        page_provider: Optional[Callable[[], Optional[Page]]],
        snapshot: Dict[str, Any],
        full_response: str,
        messages: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], str, Optional[str], Optional[Dict[str, Any]], str]:
        current_page = page_provider() if page_provider else page
        if current_page is None:
            return {"success": False, "error": "No active page available", "output": ""}, full_response, None, None, ""

        try:
            result, code, resolution = await self._execute_single_response(current_page, snapshot, full_response)
            if result["success"]:
                return result, full_response, code, resolution, ""
        except Exception as exc:
            result = {"success": False, "error": str(exc), "output": ""}
            code = None
            resolution = None

        retry_messages = messages + [
            {"role": "assistant", "content": full_response},
            {"role": "user", "content": f"Execution error: {result['error']}\nPlease fix it and retry."},
        ]
        retry_response = ""
        async for chunk_text in self._stream_llm(retry_messages, model_config):
            retry_response += chunk_text

        current_page = page_provider() if page_provider else page
        if current_page is None:
            return {"success": False, "error": "No active page available", "output": ""}, retry_response, None, None, "\n\nExecution failed. Retrying.\n\n"

        retry_snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
        try:
            retry_result, retry_code, retry_resolution = await self._execute_single_response(
                current_page,
                retry_snapshot,
                retry_response,
            )
            return retry_result, retry_response, retry_code, retry_resolution, "\n\nExecution failed. Retrying.\n\n"
        except Exception as exc:
            return {"success": False, "error": str(exc), "output": ""}, retry_response, None, None, "\n\nExecution failed. Retrying.\n\n"

    async def _execute_single_response(
        self,
        current_page: Page,
        snapshot: Dict[str, Any],
        full_response: str,
    ) -> tuple[Dict[str, Any], Optional[str], Optional[Dict[str, Any]]]:
        structured_intent = self._extract_structured_intent(full_response)
        if structured_intent:
            resolved_intent = resolve_structured_intent(snapshot, structured_intent)
            result = await execute_structured_intent(current_page, resolved_intent)
            return result, None, resolved_intent

        code = self._extract_code(full_response)
        if not code:
            raise ValueError("Unable to extract structured intent or executable code from assistant response")
        result = await self._execute_on_page(current_page, code)
        return result, code, None

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
