# RPA Trace-first Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace recording-time Contract-first complexity with a fast Trace-first RPA recording path that records accepted manual/AI/data/dataflow traces and compiles generalized Skills after recording.

**Architecture:** Recording stays close to the upstream direct assistant path and records factual traces plus lightweight runtime results. Natural-language commands use a bounded Micro-ReAct RecordingRuntimeAgent with Python Playwright-first code generation and at most one repair. Skill generation runs post-hoc from traces, using deterministic orchestration and targeted LLM repair only when needed.

**Tech Stack:** FastAPI, Python 3.13, Pydantic v2, Playwright async API, Vue 3 + TypeScript, existing RPA session manager/generator/executor/exporter.

---

## File Structure

### Backend files to create

- `RpaClaw/backend/rpa/trace_models.py`
  - Pydantic models for accepted traces, diagnostics, runtime results, dataflow mappings, and trace summaries.

- `RpaClaw/backend/rpa/trace_recorder.py`
  - Functions that convert accepted manual steps and AI execution events into trace objects.
  - Functions that infer dataflow refs by comparing filled values with `runtime_results`.

- `RpaClaw/backend/rpa/recording_runtime_agent.py`
  - Lightweight Micro-ReAct natural-language browser operator.
  - Uses existing snapshot helpers and LLM model access.
  - Generates Python Playwright-first scripts or structured browser actions.
  - Executes once, repairs at most once, and returns accepted trace payloads.

- `RpaClaw/backend/rpa/trace_skill_compiler.py`
  - Post-hoc trace-to-skill compiler.
  - Starts by converting manual traces and accepted AI script traces into replayable `skill.py`.
  - Adds deterministic generalizers for the first target scenarios.

### Backend files to modify

- `RpaClaw/backend/rpa/manager.py`
  - Extend `RPASession` with `traces`, `trace_diagnostics`, and `runtime_results`.
  - Add session manager methods to append accepted traces, append diagnostics, update runtime results, and expose trace summaries.

- `RpaClaw/backend/route/rpa.py`
  - Use `RecordingRuntimeAgent` for natural-language recording commands by default.
  - Include traces in session responses and step polling responses.
  - Use `TraceSkillCompiler` in `/generate`, `/test`, and `/save` when traces exist.
  - Keep the upstream generator fallback when traces are absent.

- `RpaClaw/backend/rpa/generator.py`
  - Keep as fallback.
  - Add only small helper support if the trace compiler needs to reuse script rendering utilities.

- `RpaClaw/backend/rpa/executor.py`
  - Keep execution behavior.
  - Ensure generated trace-based scripts can run through the existing executor.

### Frontend files to modify

- `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
  - Render accepted trace timeline when available.
  - Keep existing step display fallback.
  - Hide failed attempts from the primary timeline.
  - Allow expanding trace diagnostics.

### Test files to create or modify

- Create `RpaClaw/backend/tests/test_rpa_trace_models.py`
- Create `RpaClaw/backend/tests/test_rpa_trace_recorder.py`
- Create `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`
- Create `RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py`
- Modify `RpaClaw/backend/tests/test_rpa_manager.py`
- Modify `RpaClaw/backend/tests/test_rpa_assistant.py`
- Modify `RpaClaw/backend/tests/test_rpa_generator.py`
- Modify or add frontend tests only if an existing frontend test harness is present; otherwise validate via TypeScript build/manual UI E2E.

## Implementation Principles

- Do not add Contract-first as the recording-time path.
- Do not add open-ended ReAct loops.
- Do not use DeepAgent as the default recording-time controller.
- Do not freely generate complex JavaScript.
- Keep accepted traces separate from diagnostics.
- Preserve upstream recording behavior until a trace-based replacement is verified.
- Every task must include tests before implementation.
- Commit after each task.

---

### Task 1: Add Trace Models

**Files:**
- Create: `RpaClaw/backend/rpa/trace_models.py`
- Test: `RpaClaw/backend/tests/test_rpa_trace_models.py`

- [ ] **Step 1: Write failing model tests**

Create `RpaClaw/backend/tests/test_rpa_trace_models.py`:

```python
from backend.rpa.trace_models import (
    RPAAcceptedTrace,
    RPAAIExecution,
    RPAPageState,
    RPARuntimeResults,
    RPATraceType,
)


def test_ai_operation_trace_serializes_execution_and_page_state():
    trace = RPAAcceptedTrace(
        trace_id="trace-1",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="open the project with the highest star count",
        before_page=RPAPageState(url="https://github.com/trending", title="Trending"),
        after_page=RPAPageState(url="https://github.com/owner/repo", title="owner/repo"),
        ai_execution=RPAAIExecution(
            language="python",
            code="async def run(page, results):\n    return {'url': page.url}",
            output={"selected_project": {"url": "https://github.com/owner/repo"}},
        ),
        accepted=True,
    )

    payload = trace.model_dump()

    assert payload["trace_type"] == "ai_operation"
    assert payload["before_page"]["url"] == "https://github.com/trending"
    assert payload["ai_execution"]["language"] == "python"
    assert payload["accepted"] is True


def test_runtime_results_resolves_dotted_refs():
    results = RPARuntimeResults(
        values={
            "customer_info": {
                "name": "Alice Zhang",
                "email": "alice@example.com",
            }
        }
    )

    assert results.resolve_ref("customer_info.name") == "Alice Zhang"
    assert results.find_value_refs("Alice Zhang") == ["customer_info.name"]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_models.py -q
```

Expected: FAIL because `backend.rpa.trace_models` does not exist.

- [ ] **Step 3: Implement trace models**

Create `RpaClaw/backend/rpa/trace_models.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class RPATraceType(str, Enum):
    MANUAL_ACTION = "manual_action"
    AI_OPERATION = "ai_operation"
    DATA_CAPTURE = "data_capture"
    DATAFLOW_FILL = "dataflow_fill"
    NAVIGATION = "navigation"


class RPAPageState(BaseModel):
    url: str = ""
    title: str = ""
    snapshot_summary: Dict[str, Any] = Field(default_factory=dict)


class RPAAIExecution(BaseModel):
    language: str = "python"
    code: str = ""
    output: Any = None
    error: Optional[str] = None
    repair_attempted: bool = False


class RPATargetField(BaseModel):
    label: str = ""
    role: str = ""
    placeholder: str = ""
    locator_candidates: List[Dict[str, Any]] = Field(default_factory=list)


class RPADataflowMapping(BaseModel):
    target_field: RPATargetField = Field(default_factory=RPATargetField)
    value: Any = None
    source_ref_candidates: List[str] = Field(default_factory=list)
    selected_source_ref: Optional[str] = None
    confidence: str = ""


class RPAAcceptedTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: f"trace-{uuid4().hex}")
    trace_type: RPATraceType
    source: str = "record"
    user_instruction: Optional[str] = None
    action: Optional[str] = None
    description: str = ""
    before_page: RPAPageState = Field(default_factory=RPAPageState)
    after_page: RPAPageState = Field(default_factory=RPAPageState)
    locator_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    value: Any = None
    output_key: Optional[str] = None
    output: Any = None
    ai_execution: Optional[RPAAIExecution] = None
    dataflow: Optional[RPADataflowMapping] = None
    diagnostics_ref: Optional[str] = None
    accepted: bool = True
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime = Field(default_factory=datetime.now)


class RPATraceDiagnostic(BaseModel):
    diagnostic_id: str = Field(default_factory=lambda: f"diag-{uuid4().hex}")
    trace_id: Optional[str] = None
    source: str = "ai"
    message: str = ""
    raw: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


class RPARuntimeResults(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)

    def write(self, key: Optional[str], value: Any) -> None:
        if isinstance(key, str) and key.strip():
            self.values[key.strip()] = value

    def resolve_ref(self, ref: str) -> Any:
        current: Any = self.values
        for segment in str(ref or "").split("."):
            if isinstance(current, dict) and segment in current:
                current = current[segment]
                continue
            if isinstance(current, list) and segment.isdigit():
                index = int(segment)
                if 0 <= index < len(current):
                    current = current[index]
                    continue
            raise KeyError(ref)
        return current

    def find_value_refs(self, value: Any) -> List[str]:
        refs: List[str] = []

        def visit(node: Any, path: List[str]) -> None:
            if isinstance(node, dict):
                for key, item in node.items():
                    visit(item, path + [str(key)])
                return
            if isinstance(node, list):
                for index, item in enumerate(node):
                    visit(item, path + [str(index)])
                return
            if node == value and path:
                refs.append(".".join(path))

        visit(self.values, [])
        return refs
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_models.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```powershell
git add RpaClaw/backend/rpa/trace_models.py RpaClaw/backend/tests/test_rpa_trace_models.py
git commit -m "feat: add RPA trace models"
```

---

### Task 2: Add Trace Storage To RPA Sessions

**Files:**
- Modify: `RpaClaw/backend/rpa/manager.py`
- Test: `RpaClaw/backend/tests/test_rpa_manager.py`

- [ ] **Step 1: Write failing manager tests**

Append to `RpaClaw/backend/tests/test_rpa_manager.py`:

```python
from backend.rpa.manager import RPASession
from backend.rpa.trace_models import RPAAcceptedTrace, RPAPageState, RPATraceType


def test_rpa_session_stores_traces_and_runtime_results():
    session = RPASession(id="s1", user_id="u1", sandbox_session_id="sandbox")
    trace = RPAAcceptedTrace(
        trace_id="trace-1",
        trace_type=RPATraceType.NAVIGATION,
        source="manual",
        before_page=RPAPageState(url="about:blank"),
        after_page=RPAPageState(url="https://example.test"),
    )

    session.traces.append(trace)
    session.runtime_results.write("selected_project", {"url": "https://github.com/owner/repo"})

    assert session.traces[0].trace_id == "trace-1"
    assert session.runtime_results.resolve_ref("selected_project.url") == "https://github.com/owner/repo"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_manager.py::test_rpa_session_stores_traces_and_runtime_results -q
```

Expected: FAIL because `RPASession` has no `traces` or `runtime_results`.

- [ ] **Step 3: Extend `RPASession`**

Modify imports in `RpaClaw/backend/rpa/manager.py`:

```python
from .trace_models import RPAAcceptedTrace, RPATraceDiagnostic, RPARuntimeResults
```

Add fields to `RPASession`:

```python
    traces: List[RPAAcceptedTrace] = Field(default_factory=list)
    trace_diagnostics: List[RPATraceDiagnostic] = Field(default_factory=list)
    runtime_results: RPARuntimeResults = Field(default_factory=RPARuntimeResults)
```

- [ ] **Step 4: Add session manager helpers**

Add methods to `RPASessionManager`:

```python
    async def append_trace(self, session_id: str, trace: RPAAcceptedTrace) -> List[RPAAcceptedTrace]:
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        session.traces.append(trace)
        await self.broadcast_update(session_id, {"type": "trace_added", "trace": trace.model_dump(mode="json")})
        return session.traces

    async def append_trace_diagnostic(self, session_id: str, diagnostic: RPATraceDiagnostic) -> List[RPATraceDiagnostic]:
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        session.trace_diagnostics.append(diagnostic)
        return session.trace_diagnostics

    def write_runtime_result(self, session_id: str, key: str | None, value: Any) -> None:
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        session.runtime_results.write(key, value)
```

- [ ] **Step 5: Run manager tests**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_manager.py -q
```

Expected: all manager tests pass.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/backend/rpa/manager.py RpaClaw/backend/tests/test_rpa_manager.py
git commit -m "feat: store RPA accepted traces"
```

---

### Task 3: Convert Manual Steps Into Accepted Traces

**Files:**
- Create: `RpaClaw/backend/rpa/trace_recorder.py`
- Modify: `RpaClaw/backend/rpa/manager.py`
- Test: `RpaClaw/backend/tests/test_rpa_trace_recorder.py`

- [ ] **Step 1: Write failing trace recorder tests**

Create `RpaClaw/backend/tests/test_rpa_trace_recorder.py`:

```python
from backend.rpa.trace_recorder import manual_step_to_trace


def test_manual_navigation_step_becomes_navigation_trace():
    trace = manual_step_to_trace(
        {
            "id": "step-1",
            "action": "navigate",
            "source": "record",
            "description": "Open GitHub Trending",
            "url": "https://github.com/trending",
            "target": "https://github.com/trending",
        }
    )

    assert trace.trace_type == "navigation"
    assert trace.source == "manual"
    assert trace.after_page.url == "https://github.com/trending"


def test_manual_fill_step_records_value_and_locator_candidates():
    trace = manual_step_to_trace(
        {
            "id": "step-2",
            "action": "fill",
            "source": "record",
            "description": "Fill customer name",
            "target": '{"method":"role","role":"textbox","name":"Customer Name"}',
            "value": "Alice Zhang",
            "locator_candidates": [{"kind": "role", "locator": {"method": "role", "role": "textbox"}}],
        }
    )

    assert trace.trace_type == "manual_action"
    assert trace.value == "Alice Zhang"
    assert trace.locator_candidates[0]["kind"] == "role"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_recorder.py -q
```

Expected: FAIL because `trace_recorder.py` does not exist.

- [ ] **Step 3: Implement manual trace conversion**

Create `RpaClaw/backend/rpa/trace_recorder.py`:

```python
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from .trace_models import (
    RPAAcceptedTrace,
    RPADataflowMapping,
    RPAPageState,
    RPATargetField,
    RPATraceType,
    RPARuntimeResults,
)


def manual_step_to_trace(step: Dict[str, Any]) -> RPAAcceptedTrace:
    action = str(step.get("action") or "").strip()
    source = "manual"
    target = step.get("url") or step.get("target") or ""
    locator_candidates = step.get("locator_candidates") if isinstance(step.get("locator_candidates"), list) else []
    description = str(step.get("description") or action or "Manual action")

    if action == "navigate":
        trace_type = RPATraceType.NAVIGATION
        after_page = RPAPageState(url=str(target or ""))
    else:
        trace_type = RPATraceType.MANUAL_ACTION
        after_page = RPAPageState(url=str(step.get("page_url") or ""))

    return RPAAcceptedTrace(
        trace_id=f"trace-{step.get('id') or action or 'manual'}",
        trace_type=trace_type,
        source=source,
        action=action,
        description=description,
        after_page=after_page,
        locator_candidates=locator_candidates,
        value=step.get("value"),
    )


def infer_dataflow_for_fill(trace: RPAAcceptedTrace, runtime_results: RPARuntimeResults) -> RPAAcceptedTrace:
    if trace.action != "fill":
        return trace
    refs = runtime_results.find_value_refs(trace.value)
    if not refs:
        return trace
    target_field = RPATargetField(locator_candidates=list(trace.locator_candidates or []))
    trace.dataflow = RPADataflowMapping(
        target_field=target_field,
        value=trace.value,
        source_ref_candidates=refs,
        selected_source_ref=refs[0],
        confidence="exact_value_match",
    )
    trace.trace_type = RPATraceType.DATAFLOW_FILL
    return trace
```

- [ ] **Step 4: Run trace recorder tests**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_recorder.py -q
```

Expected: tests pass.

- [ ] **Step 5: Hook manual trace append after recorded steps**

In `RpaClaw/backend/rpa/manager.py`, find the method that appends captured browser events to `session.steps`. After creating an `RPAStep`, call:

```python
from .trace_recorder import infer_dataflow_for_fill, manual_step_to_trace

trace = manual_step_to_trace(step.model_dump())
trace = infer_dataflow_for_fill(trace, session.runtime_results)
session.traces.append(trace)
```

Do not broadcast failed or intermediate browser events as traces. Only append after the existing recorder has accepted the step.

- [ ] **Step 6: Add manager integration test**

Add a test in `RpaClaw/backend/tests/test_rpa_manager.py` using a direct manager helper if available. If the current append path is difficult to instantiate, test the helper function and the session append method together:

```python
from backend.rpa.trace_recorder import manual_step_to_trace


def test_manual_trace_can_be_added_to_session():
    session = RPASession(id="s1", user_id="u1", sandbox_session_id="sandbox")
    trace = manual_step_to_trace({"id": "step-1", "action": "navigate", "url": "https://example.test"})

    session.traces.append(trace)

    assert session.traces[0].trace_type == "navigation"
    assert session.traces[0].after_page.url == "https://example.test"
```

- [ ] **Step 7: Run tests**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_recorder.py RpaClaw\backend\tests\test_rpa_manager.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```powershell
git add RpaClaw/backend/rpa/trace_recorder.py RpaClaw/backend/rpa/manager.py RpaClaw/backend/tests/test_rpa_trace_recorder.py RpaClaw/backend/tests/test_rpa_manager.py
git commit -m "feat: record manual RPA traces"
```

---

### Task 4: Implement RecordingRuntimeAgent With Micro-ReAct

**Files:**
- Create: `RpaClaw/backend/rpa/recording_runtime_agent.py`
- Modify: `RpaClaw/backend/route/rpa.py`
- Test: `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`

- [ ] **Step 1: Write failing Micro-ReAct tests**

Create `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`:

```python
import pytest

from backend.rpa.recording_runtime_agent import RecordingRuntimeAgent


class FakePage:
    url = "https://github.com/trending"

    async def title(self):
        return "Trending"


@pytest.mark.asyncio
async def test_recording_agent_repairs_once_after_execution_failure():
    calls = {"planner": 0, "executor": 0}

    async def planner(prompt):
        calls["planner"] += 1
        if calls["planner"] == 1:
            return {
                "action_type": "run_python",
                "description": "bad script",
                "code": "async def run(page, results):\n    raise RuntimeError('bad selector')",
                "output_key": "selected_project",
            }
        return {
            "action_type": "run_python",
            "description": "fixed script",
            "code": "async def run(page, results):\n    return {'url': 'https://github.com/owner/repo'}",
            "output_key": "selected_project",
        }

    async def executor(page, code, results):
        calls["executor"] += 1
        if calls["executor"] == 1:
            return {"success": False, "error": "bad selector", "output": ""}
        return {"success": True, "error": None, "output": {"url": "https://github.com/owner/repo"}}

    agent = RecordingRuntimeAgent(planner=planner, executor=executor)
    result = await agent.run(FakePage(), "open the highest star repo", runtime_results={})

    assert result.success is True
    assert calls["planner"] == 2
    assert calls["executor"] == 2
    assert len(result.diagnostics) == 1
    assert result.trace.ai_execution.repair_attempted is True


@pytest.mark.asyncio
async def test_recording_agent_does_not_repair_successful_execution():
    calls = {"planner": 0}

    async def planner(prompt):
        calls["planner"] += 1
        return {
            "action_type": "run_python",
            "description": "good script",
            "code": "async def run(page, results):\n    return {'ok': True}",
            "output_key": "result",
        }

    async def executor(page, code, results):
        return {"success": True, "error": None, "output": {"ok": True}}

    agent = RecordingRuntimeAgent(planner=planner, executor=executor)
    result = await agent.run(FakePage(), "do something", runtime_results={})

    assert result.success is True
    assert calls["planner"] == 1
    assert result.trace.output_key == "result"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_recording_runtime_agent.py -q
```

Expected: FAIL because `recording_runtime_agent.py` does not exist.

- [ ] **Step 3: Implement `RecordingRuntimeAgent`**

Create `RpaClaw/backend/rpa/recording_runtime_agent.py`:

```python
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .trace_models import RPAAcceptedTrace, RPAAIExecution, RPAPageState, RPATraceDiagnostic, RPATraceType


Planner = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
Executor = Callable[[Any, str, Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass
class RecordingAgentResult:
    success: bool
    trace: Optional[RPAAcceptedTrace] = None
    diagnostics: List[RPATraceDiagnostic] = field(default_factory=list)
    message: str = ""
    output_key: Optional[str] = None
    output: Any = None


class RecordingRuntimeAgent:
    def __init__(self, planner: Optional[Planner] = None, executor: Optional[Executor] = None):
        self.planner = planner or self._default_planner
        self.executor = executor or self._default_executor

    async def run(self, page: Any, instruction: str, runtime_results: Dict[str, Any]) -> RecordingAgentResult:
        before = await _page_state(page)
        diagnostics: List[RPATraceDiagnostic] = []

        first_plan = await self.planner({"instruction": instruction, "runtime_results": runtime_results, "repair": None})
        first_result = await self.executor(page, str(first_plan.get("code") or ""), runtime_results)
        if first_result.get("success"):
            trace = await self._accepted_trace(page, instruction, first_plan, first_result, before, repair_attempted=False)
            return RecordingAgentResult(True, trace=trace, diagnostics=diagnostics, output_key=trace.output_key, output=trace.output)

        diagnostics.append(
            RPATraceDiagnostic(
                source="ai",
                message=str(first_result.get("error") or "recording command failed"),
                raw={"plan": first_plan, "result": first_result},
            )
        )

        repair_plan = await self.planner(
            {
                "instruction": instruction,
                "runtime_results": runtime_results,
                "repair": {"error": first_result.get("error"), "failed_plan": first_plan},
            }
        )
        repair_result = await self.executor(page, str(repair_plan.get("code") or ""), runtime_results)
        if repair_result.get("success"):
            trace = await self._accepted_trace(page, instruction, repair_plan, repair_result, before, repair_attempted=True)
            return RecordingAgentResult(True, trace=trace, diagnostics=diagnostics, output_key=trace.output_key, output=trace.output)

        diagnostics.append(
            RPATraceDiagnostic(
                source="ai",
                message=str(repair_result.get("error") or "recording command repair failed"),
                raw={"plan": repair_plan, "result": repair_result},
            )
        )
        return RecordingAgentResult(False, trace=None, diagnostics=diagnostics, message="Recording command failed after one repair.")

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
        output_key = plan.get("output_key")
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
                repair_attempted=repair_attempted,
            ),
        )

    async def _default_planner(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage
        from backend.deepagent.engine import get_llm_model
        import json

        model = get_llm_model(streaming=False)
        response = await model.ainvoke(
            [
                SystemMessage(content=RECORDING_RUNTIME_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
            ]
        )
        return _parse_json_object(_extract_text(response))

    async def _default_executor(self, page: Any, code: str, runtime_results: Dict[str, Any]) -> Dict[str, Any]:
        namespace: Dict[str, Any] = {}
        try:
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


async def _page_state(page: Any) -> RPAPageState:
    title = ""
    title_fn = getattr(page, "title", None)
    if callable(title_fn):
        value = title_fn()
        if inspect.isawaitable(value):
            value = await value
        title = str(value or "")
    return RPAPageState(url=str(getattr(page, "url", "") or ""), title=title)
```

- [ ] **Step 4: Run tests**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_recording_runtime_agent.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Add default LLM planner in bounded Python-first style**

Add `RECORDING_RUNTIME_SYSTEM_PROMPT`, `_extract_text`, and `_parse_json_object` to `recording_runtime_agent.py`. The prompt must contain these hard requirements:

```text
Return JSON only.
Return action_type="run_python" unless a simple navigate/click/fill action is clearly enough.
If code is returned, it must define async def run(page, results).
Use Python Playwright async APIs.
Avoid page.evaluate unless the snippet is short, read-only, and necessary.
Do not perform multi-step SOP planning.
Do not include a done-check.
```

Implementation details:

```python
RECORDING_RUNTIME_SYSTEM_PROMPT = """You operate one RPA recording command.
Return JSON only.
Schema:
{
  "description": "short user-facing action summary",
  "action_type": "run_python",
  "output_key": "optional_result_key",
  "code": "async def run(page, results): ..."
}
Rules:
- Return action_type="run_python" unless a simple browser action is clearly enough.
- If code is returned, it must define async def run(page, results).
- Use Python Playwright async APIs.
- Avoid page.evaluate unless the snippet is short, read-only, and necessary.
- Do not perform multi-step SOP planning.
- Do not include a done-check.
"""


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _parse_json_object(text: str) -> Dict[str, Any]:
    import json
    import re

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
    if "code" not in parsed or "async def run(page, results)" not in str(parsed.get("code")):
        raise ValueError("Recording planner must return Python code defining async def run(page, results)")
    parsed.setdefault("action_type", "run_python")
    return parsed


async def _default_planner(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    from langchain_core.messages import HumanMessage, SystemMessage
    from backend.deepagent.engine import get_llm_model
    import json

    model = get_llm_model(streaming=False)
    response = await model.ainvoke([
        SystemMessage(content=RECORDING_RUNTIME_SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ])
    text = _extract_text(response)
    return _parse_json_object(text)
```

Also add a unit test that passes fenced JSON to `_parse_json_object` and asserts it extracts the code field. This keeps response parsing deterministic before route wiring.

- [ ] **Step 6: Add route integration test with injected fake agent**

Add a small unit-level test around a helper function instead of full FastAPI SSE. Extract this helper in `route/rpa.py`:

```python
async def _apply_recording_agent_result(session, result):
    ...
```

Test that successful result appends trace and writes runtime result.

- [ ] **Step 7: Commit**

```powershell
git add RpaClaw/backend/rpa/recording_runtime_agent.py RpaClaw/backend/route/rpa.py RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py
git commit -m "feat: add bounded RPA recording runtime agent"
```

---

### Task 5: Wire Accepted AI Traces Into Chat Route

**Files:**
- Modify: `RpaClaw/backend/route/rpa.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: Write failing route helper test**

Add to `RpaClaw/backend/tests/test_rpa_assistant.py`:

```python
import pytest

from backend.rpa.manager import RPASession
from backend.rpa.recording_runtime_agent import RecordingAgentResult
from backend.rpa.trace_models import RPAAcceptedTrace, RPAPageState, RPATraceType
from backend.route.rpa import _apply_recording_agent_result


@pytest.mark.asyncio
async def test_apply_recording_agent_result_appends_trace_and_runtime_result():
    session = RPASession(id="s1", user_id="u1", sandbox_session_id="sandbox")
    trace = RPAAcceptedTrace(
        trace_id="trace-ai",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        output_key="selected_project",
        output={"url": "https://github.com/owner/repo"},
        before_page=RPAPageState(url="https://github.com/trending"),
        after_page=RPAPageState(url="https://github.com/owner/repo"),
    )
    result = RecordingAgentResult(success=True, trace=trace, output_key="selected_project", output=trace.output)

    await _apply_recording_agent_result(session, result)

    assert session.traces[0].trace_id == "trace-ai"
    assert session.runtime_results.resolve_ref("selected_project.url") == "https://github.com/owner/repo"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_assistant.py::test_apply_recording_agent_result_appends_trace_and_runtime_result -q
```

Expected: FAIL because helper is missing.

- [ ] **Step 3: Implement route helper**

In `RpaClaw/backend/route/rpa.py`, add:

```python
async def _apply_recording_agent_result(session, result) -> None:
    if not getattr(result, "success", False):
        for diagnostic in getattr(result, "diagnostics", []) or []:
            session.trace_diagnostics.append(diagnostic)
        return
    trace = getattr(result, "trace", None)
    if trace is not None:
        session.traces.append(trace)
    for diagnostic in getattr(result, "diagnostics", []) or []:
        session.trace_diagnostics.append(diagnostic)
    output_key = getattr(result, "output_key", None)
    if output_key:
        session.runtime_results.write(output_key, getattr(result, "output", None))
```

- [ ] **Step 4: Update chat route**

In `/session/{session_id}/chat`, default natural-language recording should use `RecordingRuntimeAgent` unless the user explicitly requests legacy mode.

Add:

```python
from backend.rpa.recording_runtime_agent import RecordingRuntimeAgent
```

In the event generator, call:

```python
agent = RecordingRuntimeAgent()
result = await agent.run(page, request.message, session.runtime_results.values)
await _apply_recording_agent_result(session, result)
```

Yield concise SSE events:

```python
yield {"event": "agent_message", "data": json.dumps({"message": "Executing recording command"}, ensure_ascii=False)}
if result.success:
    yield {"event": "agent_step_done", "data": json.dumps({"output": result.output, "trace": result.trace.model_dump(mode="json")}, ensure_ascii=False)}
    yield {"event": "agent_recorded_steps", "data": json.dumps({"steps": _json_ready_step_payloads(session.steps), "traces": [t.model_dump(mode="json") for t in session.traces]}, ensure_ascii=False)}
else:
    yield {"event": "agent_aborted", "data": json.dumps({"message": result.message}, ensure_ascii=False)}
```

Keep legacy `RPAAssistant` and `RPAReActAgent` behind explicit request modes such as `legacy_chat` and `legacy_react`.

- [ ] **Step 5: Run route/helper tests**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_assistant.py RpaClaw\backend\tests\test_rpa_recording_runtime_agent.py -q
```

Expected: tests pass.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/backend/route/rpa.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: record AI browser commands as traces"
```

---

### Task 6: Expose Trace Timeline To Frontend

**Files:**
- Modify: `RpaClaw/backend/route/rpa.py`
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: Add backend payload test**

Add to `RpaClaw/backend/tests/test_rpa_assistant.py`:

```python
from backend.route.rpa import _json_ready_trace_payloads


def test_json_ready_trace_payloads_contains_timeline_fields():
    trace = RPAAcceptedTrace(
        trace_id="trace-1",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        description="Open highest star repo",
        output_key="selected_project",
        output={"name": "owner/repo"},
    )

    payload = _json_ready_trace_payloads([trace])

    assert payload[0]["trace_id"] == "trace-1"
    assert payload[0]["trace_type"] == "ai_operation"
    assert payload[0]["description"] == "Open highest star repo"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_assistant.py::test_json_ready_trace_payloads_contains_timeline_fields -q
```

Expected: FAIL because helper is missing.

- [ ] **Step 3: Implement backend trace payload helper**

In `route/rpa.py`, add:

```python
def _json_ready_trace_payloads(traces) -> list[Dict[str, Any]]:
    payloads = []
    for trace in traces or []:
        if hasattr(trace, "model_dump"):
            payloads.append(trace.model_dump(mode="json"))
        elif isinstance(trace, dict):
            payloads.append(trace)
    return payloads
```

Include `traces` in:

- session start response
- step polling response if present
- chat `agent_recorded_steps` events

- [ ] **Step 4: Update frontend state**

In `RecorderPage.vue`, add:

```ts
interface RpaTrace {
  trace_id: string;
  trace_type: string;
  source: string;
  description?: string;
  user_instruction?: string;
  output_key?: string;
  output?: unknown;
  before_page?: { url?: string; title?: string };
  after_page?: { url?: string; title?: string };
  diagnostics_ref?: string;
}

const traces = ref<RpaTrace[]>([]);
```

Add mapper:

```ts
const mapTraceTimeline = (serverTraces: RpaTrace[]) => serverTraces.map((trace, i) => ({
  id: trace.trace_id || String(i + 1),
  title: trace.description || trace.user_instruction || trace.trace_type,
  description: formatTraceDescription(trace),
  status: 'completed',
  source: trace.source,
  traceType: trace.trace_type,
}));

const formatTraceDescription = (trace: RpaTrace) => {
  if (trace.output_key) return `Output: ${trace.output_key}`;
  if (trace.after_page?.url) return trace.after_page.url;
  return trace.trace_type.replace(/_/g, ' ');
};
```

When polling/chat returns traces:

```ts
if (Array.isArray(payload.traces)) {
  traces.value = payload.traces;
  steps.value = [
    { id: '0', title: 'Environment ready', description: 'Playwright browser is ready', status: 'completed' },
    ...mapTraceTimeline(traces.value),
  ];
}
```

Keep old `mapServerSteps` fallback if no traces exist.

- [ ] **Step 5: Run backend test and frontend build**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_assistant.py::test_json_ready_trace_payloads_contains_timeline_fields -q
```

Run:

```powershell
cd RpaClaw\frontend
npm run build
```

Expected: backend test passes; frontend build succeeds.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/backend/route/rpa.py RpaClaw/frontend/src/pages/rpa/RecorderPage.vue RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: show RPA trace timeline"
```

---

### Task 7: Implement Initial Trace Skill Compiler

**Files:**
- Create: `RpaClaw/backend/rpa/trace_skill_compiler.py`
- Modify: `RpaClaw/backend/route/rpa.py`
- Test: `RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py`

- [ ] **Step 1: Write failing compiler tests**

Create `RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py`:

```python
from backend.rpa.trace_models import RPAAcceptedTrace, RPAAIExecution, RPAPageState, RPATraceType
from backend.rpa.trace_skill_compiler import TraceSkillCompiler


def test_compiler_renders_manual_navigation_trace():
    compiler = TraceSkillCompiler()
    script = compiler.generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-nav",
                trace_type=RPATraceType.NAVIGATION,
                source="manual",
                after_page=RPAPageState(url="https://github.com/trending"),
            )
        ],
        params={},
    )

    assert 'await current_page.goto("https://github.com/trending"' in script
    assert "SKILL_SUCCESS" in script


def test_compiler_renders_ai_python_trace_and_stores_result():
    compiler = TraceSkillCompiler()
    script = compiler.generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-ai",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                output_key="selected_project",
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    return {'url': 'https://github.com/owner/repo'}",
                ),
            )
        ],
        params={},
    )

    assert "async def run(page, results):" in script
    assert '_results["selected_project"] = _result' in script
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py -q
```

Expected: FAIL because compiler is missing.

- [ ] **Step 3: Implement initial compiler**

Create `RpaClaw/backend/rpa/trace_skill_compiler.py`:

```python
from __future__ import annotations

from textwrap import indent
from typing import Any, Dict, Iterable, List

from .trace_models import RPAAcceptedTrace, RPATraceType


class TraceSkillCompiler:
    def generate_script(self, traces: Iterable[RPAAcceptedTrace], params: Dict[str, Any] | None = None) -> str:
        body = [
            "import asyncio",
            "import json as _json",
            "import sys",
            "from playwright.async_api import async_playwright",
            "",
            "",
            "async def execute_skill(page, **kwargs):",
            "    _results = {}",
            "    current_page = page",
        ]
        for index, trace in enumerate(traces):
            body.extend(self._render_trace(index, trace))
        body.extend(
            [
                "    return _results",
                "",
                "",
                "async def main():",
                "    pw = await async_playwright().start()",
                "    browser = await pw.chromium.launch(headless=False)",
                "    context = await browser.new_context(no_viewport=True, accept_downloads=True)",
                "    page = await context.new_page()",
                "    page.set_default_timeout(60000)",
                "    page.set_default_navigation_timeout(60000)",
                "    try:",
                "        result = await execute_skill(page)",
                "        if result:",
                "            print('SKILL_DATA:' + _json.dumps(result, ensure_ascii=False, default=str))",
                "        print('SKILL_SUCCESS')",
                "    except Exception as exc:",
                "        print(f'SKILL_ERROR: {exc}', file=sys.stderr)",
                "        sys.exit(1)",
                "    finally:",
                "        await context.close()",
                "        await browser.close()",
                "        await pw.stop()",
                "",
                "",
                "if __name__ == '__main__':",
                "    asyncio.run(main())",
            ]
        )
        return "\n".join(body) + "\n"

    def _render_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        lines = [f"", f"    # trace {index}: {trace.trace_id}"]
        if trace.trace_type == RPATraceType.NAVIGATION:
            url = trace.after_page.url
            lines.append(f"    await current_page.goto({url!r}, wait_until='domcontentloaded')")
            lines.append("    await current_page.wait_for_load_state('domcontentloaded')")
            return lines
        if trace.trace_type == RPATraceType.AI_OPERATION and trace.ai_execution and trace.ai_execution.code:
            lines.append(indent(trace.ai_execution.code.strip(), "    "))
            lines.append("    _result = await run(current_page, _results)")
            if trace.output_key:
                lines.append(f"    _results[{trace.output_key!r}] = _result")
            return lines
        lines.append(f"    # Unsupported trace type preserved as no-op: {trace.trace_type.value}")
        return lines
```

- [ ] **Step 4: Run compiler tests**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py -q
```

Expected: tests pass.

- [ ] **Step 5: Wire compiler into generate/test/save routes**

In `route/rpa.py`, import:

```python
from backend.rpa.trace_skill_compiler import TraceSkillCompiler
```

Add module-level:

```python
trace_compiler = TraceSkillCompiler()
```

In `/generate`, `/test`, and `/save`, before old step generation:

```python
if getattr(session, "traces", None):
    script = trace_compiler.generate_script(session.traces, request.params)
else:
    steps = [step.model_dump() for step in session.steps]
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"))
```

Use the same script path for test and save.

- [ ] **Step 6: Run route and generator tests**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py RpaClaw\backend\tests\test_rpa_generator.py RpaClaw\backend\tests\test_rpa_executor.py -q
```

Expected: tests pass.

- [ ] **Step 7: Commit**

```powershell
git add RpaClaw/backend/rpa/trace_skill_compiler.py RpaClaw/backend/route/rpa.py RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py
git commit -m "feat: compile RPA traces into skills"
```

---

### Task 8: Add Deterministic Generalization For First Target Scenarios

**Files:**
- Modify: `RpaClaw/backend/rpa/trace_skill_compiler.py`
- Test: `RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py`

- [ ] **Step 1: Write failing test for highest-star generalization**

Add to `test_rpa_trace_skill_compiler.py`:

```python
def test_compiler_generalizes_highest_star_trace_instead_of_hardcoding_url():
    compiler = TraceSkillCompiler()
    script = compiler.generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-star",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="open the project with the highest star count",
                output_key="selected_project",
                output={"url": "https://github.com/recorded/repo"},
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    return {'url': 'https://github.com/recorded/repo'}",
                ),
            )
        ],
        params={},
    )

    assert "stargazers" in script
    assert "max_stars" in script
    assert "https://github.com/recorded/repo" not in script
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py::test_compiler_generalizes_highest_star_trace_instead_of_hardcoding_url -q
```

Expected: FAIL because compiler currently embeds trace code.

- [ ] **Step 3: Implement highest-star pattern**

In `TraceSkillCompiler._render_trace`, before embedding AI code:

```python
        instruction = (trace.user_instruction or trace.description or "").lower()
        if "highest star" in instruction or "most stars" in instruction or "star count" in instruction:
            return self._render_highest_star_navigation_trace(index, trace)
```

Add method:

```python
    def _render_highest_star_navigation_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        return [
            "",
            f"    # trace {index}: generalized highest-star repository selection",
            "    async def run_highest_star_repo(page, results):",
            "        rows = await page.locator('article.Box-row').all()",
            "        max_stars = -1",
            "        selected = None",
            "        for row in rows:",
            "            try:",
            "                star_text = (await row.locator('a[href*=\"/stargazers\"]').first.inner_text()).strip()",
            "                stars = int(star_text.replace(',', '').strip())",
            "                link = row.locator('h2 a').first",
            "                href = await link.get_attribute('href')",
            "                name = (await link.inner_text()).strip()",
            "                if href and stars > max_stars:",
            "                    max_stars = stars",
            "                    selected = {'name': name, 'url': 'https://github.com' + href, 'stars': stars}",
            "            except Exception:",
            "                continue",
            "        if not selected:",
            "            raise RuntimeError('No repository rows with star counts were found')",
            "        await page.goto(selected['url'], wait_until='domcontentloaded')",
            "        await page.wait_for_load_state('domcontentloaded')",
            "        return selected",
            "    _result = await run_highest_star_repo(current_page, _results)",
            f"    _results[{(trace.output_key or 'selected_project')!r}] = _result",
        ]
```

- [ ] **Step 4: Add PR extraction generalization test**

Add:

```python
def test_compiler_preserves_pr_record_extraction_as_python_playwright():
    compiler = TraceSkillCompiler()
    script = compiler.generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-prs",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="collect the first 10 PRs in the current repository",
                output_key="top10_prs",
                output=[{"title": "Fix bug", "creator": "alice"}],
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    return [{'title': 'Fix bug', 'creator': 'alice'}]",
                ),
            )
        ],
        params={},
    )

    assert "top10_prs" in script
    assert "page.evaluate" not in script
```

- [ ] **Step 5: Run compiler tests**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py -q
```

Expected: tests pass.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/backend/rpa/trace_skill_compiler.py RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py
git commit -m "feat: generalize deterministic RPA traces"
```

---

### Task 9: Add Dataflow Inference And Compilation

**Files:**
- Modify: `RpaClaw/backend/rpa/trace_recorder.py`
- Modify: `RpaClaw/backend/rpa/trace_skill_compiler.py`
- Test: `RpaClaw/backend/tests/test_rpa_trace_recorder.py`
- Test: `RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py`

- [ ] **Step 1: Add failing dataflow inference test**

Add to `test_rpa_trace_recorder.py`:

```python
from backend.rpa.trace_models import RPARuntimeResults
from backend.rpa.trace_recorder import infer_dataflow_for_fill, manual_step_to_trace


def test_fill_trace_links_literal_value_to_runtime_result_ref():
    runtime_results = RPARuntimeResults(values={"customer_info": {"name": "Alice Zhang"}})
    trace = manual_step_to_trace(
        {
            "id": "fill-1",
            "action": "fill",
            "source": "record",
            "description": "Fill customer name",
            "value": "Alice Zhang",
        }
    )

    updated = infer_dataflow_for_fill(trace, runtime_results)

    assert updated.trace_type == "dataflow_fill"
    assert updated.dataflow.selected_source_ref == "customer_info.name"
```

- [ ] **Step 2: Run test and verify pass or failure**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_recorder.py::test_fill_trace_links_literal_value_to_runtime_result_ref -q
```

Expected: pass if Task 3 implementation already covered it; otherwise fail and fix `infer_dataflow_for_fill`.

- [ ] **Step 3: Add failing compiler dataflow test**

Add to `test_rpa_trace_skill_compiler.py`:

```python
from backend.rpa.trace_models import RPADataflowMapping, RPATargetField


def test_compiler_uses_source_ref_for_dataflow_fill():
    compiler = TraceSkillCompiler()
    trace = RPAAcceptedTrace(
        trace_id="fill-1",
        trace_type=RPATraceType.DATAFLOW_FILL,
        source="manual",
        action="fill",
        value="Alice Zhang",
        dataflow=RPADataflowMapping(
            target_field=RPATargetField(
                label="Customer Name",
                locator_candidates=[{"locator": {"method": "role", "role": "textbox", "name": "Customer Name"}}],
            ),
            value="Alice Zhang",
            source_ref_candidates=["customer_info.name"],
            selected_source_ref="customer_info.name",
            confidence="exact_value_match",
        ),
    )

    script = compiler.generate_script([trace], params={})

    assert "customer_info" in script
    assert "Alice Zhang" not in script
    assert "get_by_role" in script
```

- [ ] **Step 4: Implement dataflow fill rendering**

In `TraceSkillCompiler._render_trace`, handle `RPATraceType.DATAFLOW_FILL`:

```python
        if trace.trace_type == RPATraceType.DATAFLOW_FILL and trace.dataflow:
            return self._render_dataflow_fill_trace(index, trace)
```

Add helper methods:

```python
    def _render_dataflow_fill_trace(self, index: int, trace: RPAAcceptedTrace) -> List[str]:
        ref = trace.dataflow.selected_source_ref
        locator = self._best_locator(trace.dataflow.target_field.locator_candidates)
        if not ref or not locator:
            return [f"", f"    # trace {index}: unresolved dataflow fill skipped"]
        return [
            "",
            f"    # trace {index}: dataflow fill {ref}",
            f"    _value = _resolve_result_ref(_results, {ref!r})",
            f"    _locator = {_locator_expression('current_page', locator)}",
            "    await _locator.fill(str(_value))",
        ]
```

Add runtime helper to generated script:

```python
def _resolve_result_ref(results, ref):
    current = results
    for segment in ref.split('.'):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
            continue
        if isinstance(current, list) and segment.isdigit():
            current = current[int(segment)]
            continue
        raise KeyError(ref)
    return current
```

Implement `_locator_expression` for role locator:

```python
def _locator_expression(scope: str, locator: Dict[str, Any]) -> str:
    if locator.get("method") == "role":
        role = locator.get("role", "textbox")
        name = locator.get("name")
        if name:
            return f"{scope}.get_by_role({role!r}, name={name!r})"
        return f"{scope}.get_by_role({role!r})"
    return f"{scope}.locator({locator.get('value', 'body')!r})"
```

- [ ] **Step 5: Run dataflow tests**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_recorder.py RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py -q
```

Expected: tests pass.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/backend/rpa/trace_recorder.py RpaClaw/backend/rpa/trace_skill_compiler.py RpaClaw/backend/tests/test_rpa_trace_recorder.py RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py
git commit -m "feat: infer RPA trace dataflow"
```

---

### Task 10: Add Replay Validation For Trace-generated Skills

**Files:**
- Modify: `RpaClaw/backend/rpa/trace_skill_compiler.py`
- Test: `RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py`

- [ ] **Step 1: Add failing validation rendering test**

Add:

```python
def test_compiler_adds_non_empty_array_validation_for_record_outputs():
    compiler = TraceSkillCompiler()
    script = compiler.generate_script(
        [
            RPAAcceptedTrace(
                trace_id="trace-prs",
                trace_type=RPATraceType.AI_OPERATION,
                source="ai",
                user_instruction="collect the first 10 PRs",
                output_key="top10_prs",
                output=[{"title": "Fix bug", "creator": "alice"}],
                ai_execution=RPAAIExecution(
                    language="python",
                    code="async def run(page, results):\n    return []",
                ),
            )
        ],
        params={},
    )

    assert "AI trace output top10_prs is empty" in script
```

- [ ] **Step 2: Implement validation helper in generated script**

In `TraceSkillCompiler.generate_script`, add helper:

```python
def _validate_non_empty_records(key, value):
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"AI trace output {key} is empty")
```

When a trace output is a non-empty list at recording time and `output_key` exists, render:

```python
_validate_non_empty_records("top10_prs", _result)
```

- [ ] **Step 3: Run compiler tests**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py -q
```

Expected: tests pass.

- [ ] **Step 4: Commit**

```powershell
git add RpaClaw/backend/rpa/trace_skill_compiler.py RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py
git commit -m "feat: validate trace skill replay outputs"
```

---

### Task 11: End-to-end Verification Scenarios

**Files:**
- Create: `RpaClaw/backend/tests/test_rpa_trace_e2e.py`
- Optional create: `tmp` scripts only under test temp directories, not repo root.

- [ ] **Step 1: Add E2E test for generated highest-star Skill using local HTML**

Create `RpaClaw/backend/tests/test_rpa_trace_e2e.py`:

```python
import asyncio

import pytest
from playwright.async_api import async_playwright

from backend.rpa.trace_models import RPAAcceptedTrace, RPAAIExecution, RPATraceType
from backend.rpa.trace_skill_compiler import TraceSkillCompiler


@pytest.mark.asyncio
async def test_generated_highest_star_skill_recomputes_current_page():
    trace = RPAAcceptedTrace(
        trace_id="trace-star",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="open the project with the highest star count",
        output_key="selected_project",
        output={"url": "https://github.com/recorded/repo"},
        ai_execution=RPAAIExecution(
            language="python",
            code="async def run(page, results):\n    return {'url': 'https://github.com/recorded/repo'}",
        ),
    )
    script = TraceSkillCompiler().generate_script([trace], params={})
    namespace = {}
    exec(script, namespace, namespace)

    html = '''
    <html><body>
      <article class="Box-row">
        <h2><a href="/small/repo">small / repo</a></h2>
        <a href="/small/repo/stargazers">10</a>
      </article>
      <article class="Box-row">
        <h2><a href="/big/repo">big / repo</a></h2>
        <a href="/big/repo/stargazers">99</a>
      </article>
    </body></html>
    '''

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    result = await namespace["execute_skill"](page)
    await browser.close()
    await pw.stop()

    assert result["selected_project"]["url"] == "https://github.com/big/repo"
```

- [ ] **Step 2: Run E2E test**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_e2e.py -q
```

Expected: test passes.

- [ ] **Step 3: Add manual + PR extraction E2E**

Add a local HTML PR list test that compiles:

- manual navigation trace to `/pulls`
- AI extraction trace for `top10_prs`

Expected:

- generated script returns non-empty `top10_prs`
- no `page.evaluate` is present for the extraction trace unless the trace itself used a small read-only extraction snippet

- [ ] **Step 4: Add A-to-B dataflow E2E**

Use local HTML page with input label `Customer Name`, compile:

- data capture trace with `customer_info.name`
- dataflow fill trace mapping `customer_info.name -> Customer Name`

Expected:

- generated script fills the form with runtime result value
- literal recording-time value is not embedded in fill call

- [ ] **Step 5: Run full RPA trace test suite**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_models.py RpaClaw\backend\tests\test_rpa_trace_recorder.py RpaClaw\backend\tests\test_rpa_recording_runtime_agent.py RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py RpaClaw\backend\tests\test_rpa_trace_e2e.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add RpaClaw/backend/tests/test_rpa_trace_e2e.py
git commit -m "test: verify trace-first RPA skill replay"
```

---

### Task 12: Manual Product E2E Checklist

**Files:**
- Create: `docs/superpowers/plans/2026-04-20-rpa-trace-first-recording-e2e-checklist.md`

- [ ] **Step 1: Write manual checklist**

Create `docs/superpowers/plans/2026-04-20-rpa-trace-first-recording-e2e-checklist.md`:

~~~markdown
# RPA Trace-first Manual E2E Checklist

## Startup

Backend:

```powershell
$env:PYTHONPATH="RpaClaw"
python -m uvicorn backend.main:app --app-dir .\RpaClaw --host 0.0.0.0 --port 8000 --reload --reload-dir .\RpaClaw\backend
```

Frontend:

```powershell
$env:BACKEND_URL = "http://localhost:8000"
npm run dev
```

## Scenario 1: Manual-only

- Start recording.
- Manually open a public test page.
- Click and fill a simple field.
- Confirm left timeline shows accepted manual traces only.
- Generate script.
- Test replay.

## Scenario 2: Highest-star deterministic operation

- Start recording.
- Open `https://github.com/trending`.
- Natural language: `open the project with the highest star count`.
- Confirm command completes without multi-minute delay.
- Confirm timeline shows one accepted AI operation trace.
- Generate script.
- Confirm generated script recomputes star counts and does not hard-code the recorded repo URL.
- Test replay.

## Scenario 3: Semantic selection

- Start recording.
- Open `https://github.com/trending`.
- Natural language: `open the project most related to Python`.
- Confirm selected target and reason are recorded.
- Generate script.
- Confirm only this semantic step uses runtime AI.

## Scenario 4: Manual PR navigation + AI extraction

- Open a repository.
- Manually enter Pull requests page.
- Natural language: `collect the first 10 PR titles and creators`.
- Confirm timeline shows manual navigation and data capture.
- Generate and replay.
- Confirm result array is non-empty and contains title/creator.

## Scenario 5: A-to-B dataflow

- Extract structured data from page A.
- Navigate to page B.
- Fill fields manually or with natural language from captured data.
- Confirm timeline shows data capture and data fill mappings.
- Generate and replay.
- Confirm B fields are filled from extracted values, not recording-time literals.
~~~

- [ ] **Step 2: Commit checklist**

```powershell
git add docs/superpowers/plans/2026-04-20-rpa-trace-first-recording-e2e-checklist.md
git commit -m "docs: add trace-first RPA E2E checklist"
```

---

### Task 13: Final Verification And Push

**Files:**
- No new files unless fixes are required.

- [ ] **Step 1: Run backend focused suite**

Run:

```powershell
$env:PYTHONPATH='RpaClaw'; .\.venv\Scripts\python.exe -m pytest RpaClaw\backend\tests\test_rpa_trace_models.py RpaClaw\backend\tests\test_rpa_trace_recorder.py RpaClaw\backend\tests\test_rpa_recording_runtime_agent.py RpaClaw\backend\tests\test_rpa_trace_skill_compiler.py RpaClaw\backend\tests\test_rpa_trace_e2e.py RpaClaw\backend\tests\test_rpa_manager.py RpaClaw\backend\tests\test_rpa_assistant.py RpaClaw\backend\tests\test_rpa_generator.py RpaClaw\backend\tests\test_rpa_executor.py -q
```

Expected: all pass.

- [ ] **Step 2: Run frontend build**

Run:

```powershell
cd RpaClaw\frontend
npm run build
```

Expected: build succeeds.

- [ ] **Step 3: Check git status**

Run:

```powershell
git status --short
```

Expected: clean working tree.

- [ ] **Step 4: Push branch**

Run:

```powershell
git push
```

Expected: branch `codex/rpa-trace-first-recording` is updated on `origin`.

---

## Self-review

Spec coverage:

- Trace-first recording runtime: Tasks 1 through 6.
- Micro-ReAct RecordingRuntimeAgent: Task 4.
- Python Playwright-first and JS guardrail: Tasks 4 and 8.
- Runtime results and A-to-B dataflow: Tasks 1, 3, and 9.
- Left-side accepted trace timeline: Task 6.
- Post-hoc Skill compilation: Tasks 7 through 10.
- Replay/E2E validation: Tasks 11 through 13.
- DeepAgent non-default: documented in Task 4 and omitted from default implementation.

Known implementation risk:

- The exact manual-step append hook in `manager.py` must be located during implementation because upstream event-capture code is large. If the accepted-step hook is not clean, add a focused helper method and test it before wiring browser events.
- Frontend test infrastructure may be limited. If no frontend unit test runner exists, `npm run build` plus manual E2E checklist is the required verification gate for timeline UI changes.
