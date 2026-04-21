# RPA Session Context Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single session-level context service that keeps recording-time reasoning, recording-time execution, and exported replay scripts on the same context contract.

**Architecture:** Keep `TaskContextLedger` as the durable fact store, add a focused `SessionContextService` that materializes the current session context and captures read/write contracts, then route `assistant.py` and `generator.py` through that service. Treat legacy `context:...` placeholders as read-only compatibility input, not as a normal output format.

**Tech Stack:** Python 3.13, FastAPI backend, Pydantic v2 models, Playwright-based RPA generator, `unittest` test suite.

---

## File Map

### New Files

- `RpaClaw/backend/rpa/session_context_service.py`
  Session-level context API for building the current context view, answering context queries, normalizing legacy placeholders, recording reads/writes, and exporting the rebuild contract.

- `RpaClaw/backend/tests/test_rpa_session_context_service.py`
  Focused tests for context-view construction, query answering, contract capture, and legacy placeholder normalization.

### Modified Files

- `RpaClaw/backend/rpa/context_ledger.py`
  Add small helper methods and typed export structures needed by the new service, while keeping the ledger itself as the source of truth.

- `RpaClaw/backend/rpa/assistant.py`
  Replace ad hoc context assembly with `SessionContextService`, route `ai_script`, structured actions, prompt context summary, and context-query answers through one service.

- `RpaClaw/backend/rpa/generator.py`
  Consume exported context contracts instead of guessing from step text; lower old placeholder logic to compatibility-only behavior.

- `RpaClaw/backend/rpa/manager.py`
  Add a lightweight accessor for creating the per-session context service from an `RPASession`.

- `RpaClaw/backend/tests/test_rpa_assistant.py`
  Add regression coverage for context query answers, prompt context summary, and write-back behavior through the new service.

- `RpaClaw/backend/tests/test_rpa_generator.py`
  Add generator coverage for contract-first context rebuilding and explicit failure/compatibility cases.

---

### Task 1: Create the Session Context Service

**Files:**
- Create: `RpaClaw/backend/rpa/session_context_service.py`
- Modify: `RpaClaw/backend/rpa/context_ledger.py`
- Test: `RpaClaw/backend/tests/test_rpa_session_context_service.py`

- [ ] **Step 1: Write the failing service tests**

Add these tests to `RpaClaw/backend/tests/test_rpa_session_context_service.py`:

```python
import unittest

from backend.rpa.context_ledger import TaskContextLedger
from backend.rpa.session_context_service import SessionContextService


class SessionContextServiceTests(unittest.TestCase):
    def test_build_current_context_merges_observed_and_derived_values(self):
        ledger = TaskContextLedger()
        ledger.record_value("observed", "buyer", "Alice", source_kind="dom_extraction")
        ledger.record_value("derived", "request_code", "PR-001", source_kind="ai_script")

        service = SessionContextService(ledger)

        self.assertEqual(
            service.build_current_context(),
            {"buyer": "Alice", "request_code": "PR-001"},
        )

    def test_answer_context_query_returns_values_without_page_lookup(self):
        ledger = TaskContextLedger()
        ledger.record_value("observed", "buyer", "Alice", source_kind="dom_extraction")
        ledger.record_value("observed", "department", "Procurement", source_kind="dom_extraction")

        service = SessionContextService(ledger)
        result = service.answer_context_query("现在上下文中的所有内容有哪些")

        self.assertEqual(result["mode"], "context")
        self.assertEqual(result["values"]["buyer"], "Alice")
        self.assertEqual(result["values"]["department"], "Procurement")

    def test_normalize_legacy_placeholder_extracts_context_reads(self):
        ledger = TaskContextLedger()
        service = SessionContextService(ledger)

        reads = service.collect_declared_reads(
            action="fill",
            value="context:buyer",
            explicit_reads=[],
        )

        self.assertEqual(reads, ["buyer"])
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
python -m pytest RpaClaw/backend/tests/test_rpa_session_context_service.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.rpa.session_context_service'`.

- [ ] **Step 3: Implement the new service module**

Create `RpaClaw/backend/rpa/session_context_service.py` with the minimal service and typed helpers:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.rpa.context_ledger import TaskContextLedger


@dataclass
class StepContextContract:
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    updates: list[str] = field(default_factory=list)


class SessionContextService:
    def __init__(self, ledger: TaskContextLedger):
        self.ledger = ledger

    def build_current_context(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for bucket in (self.ledger.observed_values, self.ledger.derived_values):
            for key, entry in bucket.items():
                if entry.value is not None:
                    values[key] = str(entry.value)
        return values

    def answer_context_query(self, _query: str) -> dict[str, Any]:
        return {
            "mode": "context",
            "values": self.build_current_context(),
        }

    def collect_declared_reads(
        self,
        *,
        action: str,
        value: Any,
        explicit_reads: list[str] | None,
    ) -> list[str]:
        reads = list(explicit_reads or [])
        if isinstance(value, str) and value.startswith("context:"):
            key = value.split(":", 1)[1].strip()
            if key and key not in reads:
                reads.append(key)
        return reads
```

- [ ] **Step 4: Add ledger export helpers used by the service**

Extend `RpaClaw/backend/rpa/context_ledger.py` with narrow helpers instead of spreading ledger iteration across the codebase:

```python
    def iter_context_values(self):
        for key, entry in self.observed_values.items():
            yield "observed", key, entry
        for key, entry in self.derived_values.items():
            yield "derived", key, entry

    def build_value_map(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for _, key, entry in self.iter_context_values():
            if entry.value is not None:
                values[key] = str(entry.value)
        return values
```

- [ ] **Step 5: Re-run the service tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
python -m pytest RpaClaw/backend/tests/test_rpa_session_context_service.py -v
```

Expected: PASS for the three new tests.

- [ ] **Step 6: Commit the service skeleton**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/rpa/context_ledger.py RpaClaw/backend/rpa/session_context_service.py RpaClaw/backend/tests/test_rpa_session_context_service.py
git commit -m "feat: add session context service foundation"
```

### Task 2: Route Assistant Reads and Writes Through the Service

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Modify: `RpaClaw/backend/rpa/manager.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Test: `RpaClaw/backend/tests/test_rpa_session_context_service.py`

- [ ] **Step 1: Add failing assistant tests for context query and prompt summary**

Append tests like these to `RpaClaw/backend/tests/test_rpa_assistant.py`:

```python
    async def test_build_messages_includes_context_summary(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        ledger = ASSISTANT_MODULE.TaskContextLedger()
        ledger.record_value("observed", "buyer", "Alice", source_kind="dom_extraction")

        messages = assistant._build_messages(
            "填写采购表单",
            [],
            {"frames": []},
            [],
            context_ledger=ledger,
        )

        self.assertIn("## Current Context", messages[-1]["content"])
        self.assertIn("buyer = Alice", messages[-1]["content"])

    async def test_execute_single_response_answers_context_query_without_page_lookup(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        ledger = ASSISTANT_MODULE.TaskContextLedger()
        ledger.record_value("observed", "buyer", "Alice", source_kind="dom_extraction")

        result, _, _, context_reads, _ = await assistant._execute_single_response(
            _FakeActionPage(),
            {"frames": []},
            '{"action":"answer","description":"查看上下文","prompt":"现在上下文中的所有内容有哪些","result_key":"context_contents"}',
            ledger,
        )

        self.assertTrue(result["success"])
        self.assertIn("buyer", result["output"])
        self.assertEqual(context_reads, ["buyer"])
```

- [ ] **Step 2: Run the assistant tests and verify the new failures**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
python -m pytest RpaClaw/backend/tests/test_rpa_assistant.py -k "context_summary or context_query" -v
```

Expected: FAIL because `_build_messages()` does not accept `context_ledger` and `answer` still goes through the page-action path.

- [ ] **Step 3: Add a manager accessor for the per-session service**

In `RpaClaw/backend/rpa/manager.py`, add a narrow helper near the existing context helpers:

```python
from .session_context_service import SessionContextService

    def get_context_service(self, session_id: str) -> SessionContextService:
        session = self.sessions[session_id]
        return SessionContextService(session.context_ledger)
```

- [ ] **Step 4: Refactor assistant context assembly to use the service**

In `RpaClaw/backend/rpa/assistant.py`, replace `_build_context_from_ledger()` usage with the service and thread `context_ledger` through `_build_messages()`:

```python
from backend.rpa.session_context_service import SessionContextService

    @staticmethod
    def _build_messages(
        user_message: str,
        steps: List[Dict[str, Any]],
        snapshot: Dict[str, Any],
        history: List[Dict[str, str]],
        context_ledger: Optional[Any] = None,
    ) -> List[Dict[str, str]]:
        context_summary = ""
        if context_ledger is not None:
            service = SessionContextService(context_ledger)
            current_context = service.build_current_context()
            if current_context:
                lines = [f"{key} = {value}" for key, value in sorted(current_context.items())]
                context_summary = "\n".join(lines)

        context = f\"\"\"## History Steps
{steps_text or "(none)"}

## Current Context
{context_summary or "(none)"}

## Current Page Snapshot
{chr(10).join(frame_lines) or "(no observable elements)"}

## User Instruction
{user_message}\"\"\"
```

- [ ] **Step 5: Short-circuit context query answers before page execution**

In `RpaClaw/backend/rpa/assistant.py`, update `_execute_single_response()` to use the service for `answer` intents that are clearly asking about session context:

```python
        if structured_intent:
            service = SessionContextService(context_ledger) if context_ledger is not None else None
            if (
                service is not None
                and str(structured_intent.get("action", "")).lower() == "answer"
                and service.is_context_query(str(structured_intent.get("prompt", "")))
            ):
                answer = service.answer_context_query(str(structured_intent.get("prompt", "")))
                return {
                    "success": True,
                    "output": answer["text"],
                    "error": None,
                    "internal_step": structured_intent,
                }, None, structured_intent, sorted(answer["values"].keys()), []
```

- [ ] **Step 6: Capture AI-script writes and updates through the service**

Still in `assistant.py`, replace manual context diff handling with the service:

```python
        service = SessionContextService(context_ledger) if context_ledger is not None else None
        pre_context = service.build_current_context() if service is not None else {}
        result = await self._execute_on_page(current_page, code, pre_context)
        post_context = result.get("context", {})
        contract = service.capture_runtime_contract(
            before=pre_context,
            after=post_context,
        ) if service is not None else None
        if contract is not None:
            service.apply_runtime_writes(contract, step_source="ai_script")
            result["context_writes_from_ai"] = contract.writes
```

- [ ] **Step 7: Re-run focused assistant tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
python -m pytest RpaClaw/backend/tests/test_rpa_assistant.py -k "context_summary or context_query" -v
```

Expected: PASS for the new tests.

- [ ] **Step 8: Commit assistant integration**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/rpa/manager.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: route assistant context reads through service"
```

### Task 3: Make Step Contracts Contract-First Instead of Placeholder-First

**Files:**
- Modify: `RpaClaw/backend/rpa/session_context_service.py`
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Modify: `RpaClaw/backend/tests/test_rpa_session_context_service.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: Add failing tests for runtime writes and legacy placeholder normalization**

Add to `RpaClaw/backend/tests/test_rpa_session_context_service.py`:

```python
    def test_capture_runtime_contract_marks_new_and_updated_keys(self):
        ledger = TaskContextLedger()
        ledger.record_value("observed", "buyer", "Alice", source_kind="dom_extraction")
        service = SessionContextService(ledger)

        contract = service.capture_runtime_contract(
            before={"buyer": "Alice"},
            after={"buyer": "Bob", "supplier": "Acme"},
        )

        self.assertEqual(contract.writes, ["supplier", "buyer"])
        self.assertEqual(contract.updates, ["buyer"])

    def test_collect_declared_reads_prefers_explicit_contract_but_keeps_legacy_compat(self):
        ledger = TaskContextLedger()
        service = SessionContextService(ledger)

        reads = service.collect_declared_reads(
            action="fill",
            value="context:buyer",
            explicit_reads=["buyer"],
        )

        self.assertEqual(reads, ["buyer"])
```

- [ ] **Step 2: Run the service tests and confirm the new failures**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
python -m pytest RpaClaw/backend/tests/test_rpa_session_context_service.py -v
```

Expected: FAIL because `capture_runtime_contract()` and `updates` do not exist yet.

- [ ] **Step 3: Implement contract capture and write application**

Extend `RpaClaw/backend/rpa/session_context_service.py`:

```python
    def capture_runtime_contract(self, *, before: dict[str, str], after: dict[str, str]) -> StepContextContract:
        writes: list[str] = []
        updates: list[str] = []
        for key, value in after.items():
            if key not in before:
                writes.append(key)
            elif before[key] != value:
                writes.append(key)
                updates.append(key)
        return StepContextContract(reads=[], writes=writes, updates=updates)

    def apply_runtime_writes(self, contract: StepContextContract, *, values: dict[str, str] | None = None, step_source: str) -> None:
        context_values = values or self.build_current_context()
        for key in contract.writes:
            self.ledger.record_value(
                "observed",
                key,
                context_values.get(key),
                source_kind=step_source,
            )
```

- [ ] **Step 4: Use the normalized read collection in assistant step persistence**

In `RpaClaw/backend/rpa/assistant.py`, replace direct `step.get("context_reads")` handling with the service helper:

```python
        service = SessionContextService(context_ledger) if context_ledger is not None else None
        normalized_reads = service.collect_declared_reads(
            action=step_data.get("action", ""),
            value=step_data.get("value"),
            explicit_reads=step_data.get("context_reads") or [],
        ) if service is not None else list(step_data.get("context_reads") or [])
        step_data["context_reads"] = normalized_reads
```

- [ ] **Step 5: Re-run the service and assistant tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
python -m pytest RpaClaw/backend/tests/test_rpa_session_context_service.py RpaClaw/backend/tests/test_rpa_assistant.py -k "context or runtime_contract" -v
```

Expected: PASS for the new contract tests and no regressions in the earlier context-query tests.

- [ ] **Step 6: Commit the contract logic**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/rpa/session_context_service.py RpaClaw/backend/rpa/assistant.py RpaClaw/backend/tests/test_rpa_session_context_service.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: make session context contracts explicit"
```

### Task 4: Make Generator Consume Exported Context Contracts

**Files:**
- Modify: `RpaClaw/backend/rpa/session_context_service.py`
- Modify: `RpaClaw/backend/rpa/generator.py`
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Add failing generator tests for contract-first replay**

Add to `RpaClaw/backend/tests/test_rpa_generator.py`:

```python
    def test_generate_script_uses_contract_first_context_reads_for_fill(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "fill",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "购买人"}),
                "value": "context:buyer",
                "description": "Fill buyer",
                "context_reads": ["buyer"],
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('context.get("buyer", kwargs.get("buyer", ""))', script)
        self.assertNotIn('"context:buyer"', script)

    def test_generate_script_keeps_legacy_placeholder_as_compat_input_only(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "fill",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "购买人"}),
                "value": "context:buyer",
                "description": "Legacy fill buyer",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('context.get("buyer"', script)
```

- [ ] **Step 2: Run the focused generator tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
python -m pytest RpaClaw/backend/tests/test_rpa_generator.py -k "contract_first_context_reads or legacy_placeholder" -v
```

Expected: FAIL because the current generator still emits the old placeholder in some fill paths.

- [ ] **Step 3: Export a formal rebuild contract from the service**

Extend `RpaClaw/backend/rpa/session_context_service.py` with a compact export API:

```python
    def export_contract(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "current_context": self.build_current_context(),
            "required_reads": sorted({key for step in steps for key in (step.get("context_reads") or [])}),
            "required_writes": sorted({key for step in steps for key in (step.get("context_writes") or [])}),
            "rebuild_sequence": self.ledger.get_rebuild_sequence(),
        }
```

- [ ] **Step 4: Consume the exported contract in generator fill and rebuild paths**

In `RpaClaw/backend/rpa/generator.py`, stop guessing context reads from raw strings before checking contracts:

```python
        contract = None
        if context_ledger is not None:
            from backend.rpa.session_context_service import SessionContextService

            contract = SessionContextService(context_ledger).export_contract(deduped)

        ...
        elif action == "fill":
            context_reads = step.get("context_reads") or []
            if not context_reads and isinstance(value, str) and value.startswith("context:"):
                context_reads = [value.split(":", 1)[1].strip()]
            if context_reads:
                ctx_key = context_reads[0]
                fill_value = f'context.get("{ctx_key}", kwargs.get("{ctx_key}", ""))'
            else:
                fill_value = self._maybe_parameterize(value, params)
```

- [ ] **Step 5: Re-run the generator tests**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
python -m pytest RpaClaw/backend/tests/test_rpa_generator.py -k "context" -v
```

Expected: PASS for the new tests and all existing context-rebuild generator tests.

- [ ] **Step 6: Commit generator contract integration**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/rpa/session_context_service.py RpaClaw/backend/rpa/generator.py RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "feat: make generator consume session context contracts"
```

### Task 5: Lock the End-to-End Behavior With Regression Tests

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`
- Modify: `RpaClaw/backend/tests/test_rpa_session_context_service.py`

- [ ] **Step 1: Add the final regression tests**

Add one end-to-end-style unit test per failure class:

```python
    async def test_answer_context_query_does_not_fall_back_to_page_locator(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        ledger = ASSISTANT_MODULE.TaskContextLedger()
        ledger.record_value("observed", "expected_arrival", "2026-04-30", source_kind="dom_extraction")

        result, _, _, reads, _ = await assistant._execute_single_response(
            _FakeActionPage(),
            {"frames": []},
            '{"action":"answer","description":"查看当前上下文","prompt":"现在上下文中的所有内容有哪些","result_key":"context_contents"}',
            ledger,
        )

        self.assertTrue(result["success"])
        self.assertIn("2026-04-30", result["output"])
        self.assertEqual(reads, ["expected_arrival"])
```

```python
    def test_generator_does_not_emit_context_placeholder_for_new_steps(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "fill",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "供应商"}),
                "value": "Acme",
                "description": "Fill supplier",
                "context_reads": ["supplier"],
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertNotIn("context:supplier", script)
        self.assertIn('context.get("supplier"', script)
```

- [ ] **Step 2: Run the full targeted backend test slice**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
python -m pytest \
  RpaClaw/backend/tests/test_rpa_session_context_service.py \
  RpaClaw/backend/tests/test_rpa_assistant.py \
  RpaClaw/backend/tests/test_rpa_generator.py -v
```

Expected: PASS for all tests touching session context, assistant context answers, and generator context rebuilding.

- [ ] **Step 3: Run a second pass on the most relevant context-specific subset**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
python -m pytest RpaClaw/backend/tests/test_rpa_assistant.py -k "context" -v
python -m pytest RpaClaw/backend/tests/test_rpa_generator.py -k "context" -v
```

Expected: PASS, confirming no hidden ordering issue in the focused context tests.

- [ ] **Step 4: Commit the regression suite**

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw
git add RpaClaw/backend/tests/test_rpa_session_context_service.py RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "test: lock session context service regressions"
```

---

## Self-Review

### Spec Coverage

- `SessionContextService` as the single context layer: covered by Tasks 1-3.
- Shared reads across `ai_script`, structured actions, and `answer`: covered by Task 2 and Task 3.
- Generator and `rebuild_context()` consuming exported contracts: covered by Task 4.
- Legacy placeholder compatibility without keeping it as normal output: covered by Task 4.
- Explicit error/behavior regression coverage: covered by Task 5.

No spec section is left without a matching task.

### Placeholder Scan

- No `TODO`, `TBD`, or “implement later” placeholders remain.
- Every task includes exact files, commands, and concrete code snippets.
- Compatibility handling is described explicitly instead of “handle edge cases”.

### Type Consistency

- The plan consistently uses `SessionContextService`, `build_current_context()`, `answer_context_query()`, `collect_declared_reads()`, `capture_runtime_contract()`, and `export_contract()`.
- `StepContextContract` is introduced in Task 1 and reused with the same name in later tasks.
- The plan keeps `TaskContextLedger` as the durable store throughout.
