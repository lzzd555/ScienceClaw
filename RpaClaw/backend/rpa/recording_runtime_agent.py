from __future__ import annotations

import inspect
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from backend.deepagent.engine import get_llm_model

from .assistant_runtime import build_page_snapshot
from .frame_selectors import build_frame_path
from .trace_models import RPAAcceptedTrace, RPAAIExecution, RPAPageState, RPATraceDiagnostic, RPATraceType


RECORDING_RUNTIME_SYSTEM_PROMPT = """You operate exactly one RPA recording command.
Return JSON only.
Schema:
{
  "description": "short user-facing action summary",
  "action_type": "run_python",
  "output_key": "optional_ascii_snake_case_result_key",
  "code": "async def run(page, results): ..."
}
Rules:
- Complete only the current user command, not the full SOP.
- Return action_type="run_python" unless a simple goto/click/fill action is clearly enough.
- If code is returned, it must define async def run(page, results).
- Use Python Playwright async APIs.
- Prefer Playwright locators and page.locator/query_selector_all over page.evaluate.
- Avoid page.evaluate unless the snippet is short, read-only, and necessary.
- Do not include shell, filesystem, network requests outside the current browser page, or infinite loops.
- Do not include a separate done-check.
- If extracting data, return structured JSON-serializable Python values.
"""


class RecordingAgentResult(BaseModel):
    success: bool
    trace: Optional[RPAAcceptedTrace] = None
    diagnostics: List[RPATraceDiagnostic] = Field(default_factory=list)
    output_key: Optional[str] = None
    output: Any = None
    message: str = ""


Planner = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
Executor = Callable[[Any, Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]


class RecordingRuntimeAgent:
    def __init__(
        self,
        planner: Optional[Planner] = None,
        executor: Optional[Executor] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ):
        self.planner = planner or self._default_planner
        self.executor = executor or self._default_executor
        self.model_config = model_config

    async def run(
        self,
        *,
        page: Any,
        instruction: str,
        runtime_results: Optional[Dict[str, Any]] = None,
    ) -> RecordingAgentResult:
        runtime_results = runtime_results if runtime_results is not None else {}
        before = await _page_state(page)
        snapshot = await _safe_page_snapshot(page)
        payload = {
            "instruction": instruction,
            "page": before.model_dump(mode="json"),
            "snapshot": _compact_snapshot(snapshot),
            "runtime_results": runtime_results,
        }

        first_plan = await self.planner(payload)
        first_result = await self.executor(page, first_plan, runtime_results)
        if first_result.get("success"):
            trace = await self._accepted_trace(
                page,
                instruction,
                first_plan,
                first_result,
                before,
                repair_attempted=False,
            )
            return RecordingAgentResult(
                success=True,
                trace=trace,
                output_key=trace.output_key,
                output=trace.output,
                message="Recording command completed.",
            )

        diagnostics = [
            RPATraceDiagnostic(
                source="ai",
                message=str(first_result.get("error") or "recording command failed"),
                raw={"plan": _safe_jsonable(first_plan), "result": _safe_jsonable(first_result)},
            )
        ]

        repair_payload = {
            **payload,
            "repair": {
                "error": first_result.get("error"),
                "failed_plan": first_plan,
            },
        }
        repair_plan = await self.planner(repair_payload)
        repair_result = await self.executor(page, repair_plan, runtime_results)
        if repair_result.get("success"):
            trace = await self._accepted_trace(
                page,
                instruction,
                repair_plan,
                repair_result,
                before,
                repair_attempted=True,
            )
            return RecordingAgentResult(
                success=True,
                trace=trace,
                diagnostics=diagnostics,
                output_key=trace.output_key,
                output=trace.output,
                message="Recording command completed after one repair.",
            )

        diagnostics.append(
            RPATraceDiagnostic(
                source="ai",
                message=str(repair_result.get("error") or "recording command repair failed"),
                raw={"plan": _safe_jsonable(repair_plan), "result": _safe_jsonable(repair_result)},
            )
        )
        return RecordingAgentResult(
            success=False,
            diagnostics=diagnostics,
            message="Recording command failed after one repair.",
        )

    async def _accepted_trace(
        self,
        page: Any,
        instruction: str,
        plan: Dict[str, Any],
        result: Dict[str, Any],
        before: RPAPageState,
        *,
        repair_attempted: bool,
    ) -> RPAAcceptedTrace:
        after = await _page_state(page)
        output = result.get("output")
        output_key = _normalize_result_key(plan.get("output_key"))
        return RPAAcceptedTrace(
            trace_type=RPATraceType.AI_OPERATION,
            source="ai",
            user_instruction=instruction,
            description=str(plan.get("description") or instruction),
            before_page=before,
            after_page=after,
            output_key=output_key,
            output=output,
            ai_execution=RPAAIExecution(
                language="python",
                code=str(plan.get("code") or ""),
                output=output,
                error=result.get("error"),
                repair_attempted=repair_attempted,
            ),
        )

    async def _default_planner(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        model = get_llm_model(config=self.model_config, streaming=False)
        response = await model.ainvoke(
            [
                SystemMessage(content=RECORDING_RUNTIME_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
            ]
        )
        return _parse_json_object(_extract_text(response))

    async def _default_executor(self, page: Any, plan: Dict[str, Any], runtime_results: Dict[str, Any]) -> Dict[str, Any]:
        action_type = str(plan.get("action_type") or "run_python").strip()
        try:
            if action_type == "goto":
                url = str(plan.get("url") or plan.get("target_url") or "")
                if not url:
                    return {"success": False, "error": "goto plan missing url", "output": ""}
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_load_state("domcontentloaded")
                return {"success": True, "output": {"url": getattr(page, "url", url)}}

            if action_type == "click":
                selector = str(plan.get("selector") or "")
                if not selector:
                    return {"success": False, "error": "click plan missing selector", "output": ""}
                await page.locator(selector).first.click()
                return {"success": True, "output": "clicked"}

            if action_type == "fill":
                selector = str(plan.get("selector") or "")
                value = plan.get("value", "")
                if not selector:
                    return {"success": False, "error": "fill plan missing selector", "output": ""}
                await page.locator(selector).first.fill(str(value))
                return {"success": True, "output": value}

            code = str(plan.get("code") or "")
            if "async def run(page, results)" not in code:
                return {"success": False, "error": "plan missing async def run(page, results)", "output": ""}
            namespace: Dict[str, Any] = {}
            exec(compile(code, "<recording_runtime_agent>", "exec"), namespace, namespace)
            runner = namespace.get("run")
            if not callable(runner):
                return {"success": False, "error": "No run(page, results) function defined", "output": ""}
            output = runner(page, runtime_results)
            if inspect.isawaitable(output):
                output = await output
            return {"success": True, "error": None, "output": output}
        except Exception as exc:
            return {"success": False, "error": str(exc), "output": ""}


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        if content:
            return content
        reasoning = getattr(response, "additional_kwargs", {}).get("reasoning_content") if hasattr(response, "additional_kwargs") else ""
        return str(reasoning or "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or item.get("thinking") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _parse_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Recording planner must return a JSON object")
    parsed.setdefault("action_type", "run_python")
    if parsed.get("action_type") == "run_python" and "async def run(page, results)" not in str(parsed.get("code") or ""):
        raise ValueError("Recording planner must return Python code defining async def run(page, results)")
    return parsed


def _normalize_result_key(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return None
    if text[0].isdigit():
        text = f"result_{text}"
    return text[:64]


async def _page_state(page: Any) -> RPAPageState:
    title = ""
    title_fn = getattr(page, "title", None)
    if callable(title_fn):
        value = title_fn()
        if inspect.isawaitable(value):
            value = await value
        title = str(value or "")
    return RPAPageState(url=str(getattr(page, "url", "") or ""), title=title)


async def _safe_page_snapshot(page: Any) -> Dict[str, Any]:
    try:
        return await build_page_snapshot(page, build_frame_path)
    except Exception:
        return {"url": getattr(page, "url", ""), "title": "", "frames": []}


def _compact_snapshot(snapshot: Dict[str, Any], limit: int = 80) -> Dict[str, Any]:
    compact_frames = []
    for frame in list(snapshot.get("frames") or [])[:5]:
        nodes = []
        for node in list(frame.get("elements") or [])[:limit]:
            nodes.append(
                {
                    "index": node.get("index"),
                    "tag": node.get("tag"),
                    "role": node.get("role"),
                    "name": node.get("name"),
                    "text": node.get("text"),
                    "href": node.get("href"),
                }
            )
        compact_frames.append(
            {
                "frame_hint": frame.get("frame_hint"),
                "url": frame.get("url"),
                "elements": nodes,
                "collections": frame.get("collections", [])[:10],
            }
        )
    return {
        "url": snapshot.get("url"),
        "title": snapshot.get("title"),
        "frames": compact_frames,
    }


def _safe_jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return str(value)

