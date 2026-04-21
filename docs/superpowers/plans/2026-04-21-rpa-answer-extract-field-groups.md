# RPA Answer/Extract And Field Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM-level `answer` vs `extract_text` intent splitting for the recording assistant, plus field-group based extraction with `content_nodes` fallback for complex form DOM.

**Architecture:** Keep the current recorder pipeline intact and extend it incrementally. `assistant.py` learns a new non-persistent `answer` intent, `assistant_snapshot_runtime.py` emits `field_groups` as an additional snapshot view, and `assistant_runtime.py` resolves `extract_text` through `field_groups` first and falls back to `content_nodes` if the field-group anchor fails validation.

**Tech Stack:** Python 3.13, unittest/IsolatedAsyncioTestCase, Playwright async API, inline browser snapshot JavaScript.

---

## File Map

- Modify: `RpaClaw/backend/rpa/assistant.py`
  - Extend the structured intent prompt and execution flow to support `answer`.
  - Keep `answer` out of persisted steps and `context_writes`.
- Modify: `RpaClaw/backend/rpa/assistant_runtime.py`
  - Add `field_groups`-aware extraction resolution and runtime fallback.
  - Add execution support for `answer`.
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`
  - Extend `SNAPSHOT_V2_JS` to emit `field_groups` from form-like containers.
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
  - Add tests for snapshot field groups, `answer` intent execution, and mixed answer/extract behavior.
- Create: `RpaClaw/backend/tests/test_rpa_assistant_runtime.py`
  - Add focused runtime tests for field-group resolution, validation, and fallback.

## Task 1: Add Runtime Tests For Field Group Resolution And Fallback

**Files:**
- Create: `RpaClaw/backend/tests/test_rpa_assistant_runtime.py`
- Modify: `RpaClaw/backend/rpa/assistant_runtime.py`

- [ ] **Step 1: Write the failing runtime test file**

```python
import importlib
import unittest


RUNTIME = importlib.import_module("backend.rpa.assistant_runtime")


class _FakeLocator:
    def __init__(self, text="", should_fail=False):
        self._text = text
        self._should_fail = should_fail
        self.first = self

    async def inner_text(self):
        if self._should_fail:
            raise RuntimeError("locator failed")
        return self._text


class _FakeScope:
    def __init__(self, mapping):
        self.mapping = mapping

    def locator(self, selector):
        return self.mapping[selector]

    def get_by_text(self, value):
        return self.mapping[f"text:{value}"]


class ResolveFieldGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_text_prefers_field_group_value_locator(self):
        snapshot = {
            "field_groups": [
                {
                    "field_name": "期望完成时间 (UTC+08:00)",
                    "field_value": "2025-06-13",
                    "value_locator": {"method": "css", "value": "[data-field='expectedCompletionDate']"},
                    "value_node_id": "content-2",
                    "frame_path": [],
                    "confidence": 0.95,
                }
            ],
            "content_nodes": [],
        }
        intent = {"action": "extract_text", "target_hint": {"name": "期望完成时间"}}

        resolved = RUNTIME.resolve_structured_intent(snapshot, intent)

        self.assertEqual(
            resolved["resolved"]["locator"],
            {"method": "css", "value": "[data-field='expectedCompletionDate']"},
        )
        self.assertEqual(resolved["resolved"]["field_group"]["field_value"], "2025-06-13")

    async def test_extract_text_falls_back_to_content_nodes_when_field_group_locator_is_invalid(self):
        page = _FakeScope(
            {
                "[data-field='expectedCompletionDate']": _FakeLocator(should_fail=True),
                "text:2025-06-13": _FakeLocator(text="2025-06-13"),
            }
        )
        intent = {
            "action": "extract_text",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "css", "value": "[data-field='expectedCompletionDate']"},
                "fallback_locator": {"method": "text", "value": "2025-06-13"},
                "field_group": {"field_name": "期望完成时间"},
            },
        }

        result = await RUNTIME.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "2025-06-13")
        self.assertEqual(result["step"]["assistant_diagnostics"]["fallback_used"], "content_nodes")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend && python -m pytest tests/test_rpa_assistant_runtime.py -v
```

Expected:
- `ERROR` because `tests/test_rpa_assistant_runtime.py` does not exist yet, or
- `FAIL` because `resolve_structured_intent()` and `execute_structured_intent()` do not yet support `field_groups` and fallback metadata.

- [ ] **Step 3: Create the test file with the failing tests**

```python
import importlib
import unittest


RUNTIME = importlib.import_module("backend.rpa.assistant_runtime")


class _FakeLocator:
    def __init__(self, text="", should_fail=False):
        self._text = text
        self._should_fail = should_fail
        self.first = self

    async def inner_text(self):
        if self._should_fail:
            raise RuntimeError("locator failed")
        return self._text


class _FakeScope:
    def __init__(self, mapping):
        self.mapping = mapping

    def locator(self, selector):
        return self.mapping[selector]

    def get_by_text(self, value):
        return self.mapping[f"text:{value}"]


class ResolveFieldGroupTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_text_prefers_field_group_value_locator(self):
        snapshot = {
            "field_groups": [
                {
                    "field_name": "期望完成时间 (UTC+08:00)",
                    "field_value": "2025-06-13",
                    "value_locator": {"method": "css", "value": "[data-field='expectedCompletionDate']"},
                    "value_node_id": "content-2",
                    "frame_path": [],
                    "confidence": 0.95,
                }
            ],
            "content_nodes": [],
        }
        intent = {"action": "extract_text", "target_hint": {"name": "期望完成时间"}}

        resolved = RUNTIME.resolve_structured_intent(snapshot, intent)

        self.assertEqual(
            resolved["resolved"]["locator"],
            {"method": "css", "value": "[data-field='expectedCompletionDate']"},
        )
        self.assertEqual(resolved["resolved"]["field_group"]["field_value"], "2025-06-13")

    async def test_extract_text_falls_back_to_content_nodes_when_field_group_locator_is_invalid(self):
        page = _FakeScope(
            {
                "[data-field='expectedCompletionDate']": _FakeLocator(should_fail=True),
                "text:2025-06-13": _FakeLocator(text="2025-06-13"),
            }
        )
        intent = {
            "action": "extract_text",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "css", "value": "[data-field='expectedCompletionDate']"},
                "fallback_locator": {"method": "text", "value": "2025-06-13"},
                "field_group": {"field_name": "期望完成时间"},
            },
        }

        result = await RUNTIME.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "2025-06-13")
        self.assertEqual(result["step"]["assistant_diagnostics"]["fallback_used"], "content_nodes")
```

- [ ] **Step 4: Implement minimal runtime support for field-group resolution and fallback**

Add these focused structures to `RpaClaw/backend/rpa/assistant_runtime.py`:

```python
def _field_group_score(field_group: Dict[str, Any], intent: Dict[str, Any]) -> int:
    target_hint = intent.get("target_hint", {}) or {}
    expected_name = _normalize_hint(target_hint.get("name") or target_hint.get("text") or target_hint.get("value"))
    haystack = _normalize_hint(field_group.get("field_name") or "")
    if not expected_name or not haystack:
        return 0
    score = 0
    if expected_name in haystack:
        score += 8
    score += min(len(_tokenize_text(expected_name) & _tokenize_text(haystack)) * 2, 6)
    score += int((field_group.get("confidence") or 0) * 2)
    return score


def _resolve_field_group(snapshot: Dict[str, Any], intent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    field_groups = list(snapshot.get("field_groups") or [])
    if not field_groups:
        return None
    scored = [{"field_group": fg, "score": _field_group_score(fg, intent)} for fg in field_groups]
    scored = [item for item in scored if item["score"] > 0]
    if not scored:
        return None
    scored.sort(key=lambda item: -item["score"])
    return scored[0]["field_group"]
```

Then update the `extract_text` branch in `resolve_structured_intent()`:

```python
if action == "extract_text":
    field_group = _resolve_field_group(snapshot, intent)
    if field_group:
        fallback_node = _resolve_content_node(snapshot, intent)
        fallback_locator = None
        if fallback_node:
            fallback_locator = fallback_node.get("locator") or {"method": "text", "value": fallback_node.get("text", "")}
        return {
            **intent,
            "resolved": {
                "frame_path": list(field_group.get("frame_path") or []),
                "locator": dict(field_group.get("value_locator") or {"method": "text", "value": field_group.get("field_value", "")}),
                "fallback_locator": fallback_locator,
                "field_group": field_group,
                "locator_candidates": [],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "field_group",
            },
        }
```

Update the `extract_text` branch in `execute_structured_intent()`:

```python
elif action == "extract_text":
    fallback_used = ""
    try:
        output = await locator.first.inner_text()
        if not str(output).strip():
            raise RuntimeError("empty field group value")
    except Exception:
        fallback_payload = resolved.get("fallback_locator")
        if not fallback_payload:
            raise
        locator = _locator_from_payload(scope, fallback_payload)
        output = await locator.first.inner_text()
        fallback_used = "content_nodes"
```

And extend diagnostics in the returned step:

```python
"assistant_diagnostics": {
    **(resolved.get("assistant_diagnostics", {}) or {}),
    "resolved_frame_path": frame_path,
    "selected_locator_kind": resolved.get("selected_locator_kind", ""),
    "collection_kind": resolved.get("collection_hint", {}).get("kind", ""),
    "fallback_used": fallback_used,
},
```

- [ ] **Step 5: Run the runtime tests to verify they pass**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend && python -m pytest tests/test_rpa_assistant_runtime.py -v
```

Expected:
- `2 passed`

- [ ] **Step 6: Commit the runtime foundation**

```bash
git add RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/tests/test_rpa_assistant_runtime.py
git commit -m "test: add field group runtime coverage"
```

## Task 2: Emit `field_groups` From Snapshot V2

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`
- Modify: `RpaClaw/backend/rpa/assistant_runtime.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: Add a failing snapshot test for field groups**

Add this test to `RpaClaw/backend/tests/test_rpa_assistant.py`:

```python
    async def test_build_page_snapshot_v2_includes_field_groups(self):
        main = _FakeSnapshotFrame(name="main", url="https://example.com", frame_path=[], elements=[])
        page = _FakeSnapshotPage(main)

        with patch.object(
            ASSISTANT_RUNTIME_MODULE,
            "_extract_frame_snapshot_v2",
            new=AsyncMock(
                return_value={
                    "actionable_nodes": [],
                    "content_nodes": [
                        {"node_id": "content-1", "frame_path": [], "container_id": "container-1", "text": "期望完成时间 (UTC+08:00)", "locator": {"method": "text", "value": "期望完成时间 (UTC+08:00)"}, "bbox": {"x": 10, "y": 10}},
                        {"node_id": "content-2", "frame_path": [], "container_id": "container-1", "text": "2025-06-13", "locator": {"method": "css", "value": "[data-field='expectedCompletionDate']"}, "bbox": {"x": 300, "y": 10}},
                    ],
                    "containers": [
                        {"container_id": "container-1", "frame_path": [], "container_kind": "form_section", "name": "基础信息", "bbox": {"x": 0, "y": 0, "width": 500, "height": 60}, "child_actionable_ids": [], "child_content_ids": ["content-1", "content-2"]}
                    ],
                    "field_groups": [
                        {
                            "field_name": "期望完成时间 (UTC+08:00)",
                            "field_value": "2025-06-13",
                            "container_id": "container-1",
                            "label_node_id": "content-1",
                            "value_node_id": "content-2",
                            "value_locator": {"method": "css", "value": "[data-field='expectedCompletionDate']"},
                            "confidence": 0.95,
                        }
                    ],
                }
            ),
        ):
            snapshot = await ASSISTANT_MODULE.build_page_snapshot(page, frame_path_builder=lambda frame: frame._frame_path)

        self.assertIn("field_groups", snapshot)
        self.assertEqual(snapshot["field_groups"][0]["field_name"], "期望完成时间 (UTC+08:00)")
```

- [ ] **Step 2: Run the snapshot test to verify it fails**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend && python -m pytest tests/test_rpa_assistant.py::RPAAssistantFrameAwareSnapshotTests::test_build_page_snapshot_v2_includes_field_groups -v
```

Expected:
- `FAIL` because `build_page_snapshot()` and `_extract_frame_snapshot_v2()` do not preserve `field_groups`.

- [ ] **Step 3: Preserve `field_groups` in Python snapshot plumbing**

Update `_extract_frame_snapshot_v2()` in `RpaClaw/backend/rpa/assistant_runtime.py`:

```python
    if isinstance(data, dict):
        return {
            "actionable_nodes": list(data.get("actionable_nodes") or []),
            "content_nodes": list(data.get("content_nodes") or []),
            "containers": list(data.get("containers") or []),
            "field_groups": list(data.get("field_groups") or []),
        }
```

Update `build_page_snapshot()` to collect per-frame field groups:

```python
    field_groups: List[Dict[str, Any]] = []
```

Inside `walk(frame)`:

```python
        frame_field_groups = [
            {
                **field_group,
                "frame_path": list(field_group.get("frame_path") or frame_path),
            }
            for field_group in snapshot_v2.get("field_groups", [])
        ]
        field_groups.extend(frame_field_groups)
```

And in the returned snapshot:

```python
        "field_groups": field_groups,
```

- [ ] **Step 4: Emit `field_groups` in `SNAPSHOT_V2_JS`**

Extend the top-level result in `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`:

```javascript
const result = { actionable_nodes: [], content_nodes: [], containers: [], field_groups: [] };
```

Add a focused grouping helper near `ensureContainer()`:

```javascript
function buildFieldGroups() {
    const candidates = [];
    for (const container of result.containers) {
        const contentNodes = result.content_nodes.filter(node => node.container_id === container.container_id);
        const labels = contentNodes.filter(node => /label|heading|text/.test(node.semantic_kind || "") && /时间|标题|部门|人|日期|名称/.test(node.text || ""));
        const values = contentNodes.filter(node => (node.element_snapshot || {}).tag !== "label" && /\d{4}-\d{2}-\d{2}|.+/.test(node.text || ""));
        for (const label of labels) {
            const value = values.find(item => item.node_id !== label.node_id && (item.bbox.y === label.bbox.y || Math.abs((item.bbox.y || 0) - (label.bbox.y || 0)) <= 12));
            if (!value)
                continue;
            candidates.push({
                field_name: label.text,
                field_value: value.text,
                container_id: container.container_id,
                label_node_id: label.node_id,
                value_node_id: value.node_id,
                label_locator: label.locator,
                value_locator: value.locator,
                confidence: 0.8,
            });
        }
    }
    result.field_groups = candidates;
}
```

Call it just before returning JSON:

```javascript
buildFieldGroups();
return JSON.stringify(result);
```

- [ ] **Step 5: Run the snapshot test to verify it passes**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend && python -m pytest tests/test_rpa_assistant.py::RPAAssistantFrameAwareSnapshotTests::test_build_page_snapshot_v2_includes_field_groups -v
```

Expected:
- `1 passed`

- [ ] **Step 6: Commit the snapshot layer**

```bash
git add RpaClaw/backend/rpa/assistant_snapshot_runtime.py RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: add field groups to snapshot v2"
```

## Task 3: Add `answer` Intent Support Without Persisted Steps

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Modify: `RpaClaw/backend/rpa/assistant_runtime.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: Add failing assistant tests for `answer` and mixed answer/extract**

Add these tests to `RpaClaw/backend/tests/test_rpa_assistant.py`:

```python
class RPAAssistantAnswerIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_compute_context_writes_ignores_answer_action(self):
        writes = ASSISTANT_MODULE.RPAAssistant._compute_context_writes(
            "帮我看下期望完成时间是什么",
            {"action": "answer", "result_key": "expected_completion_date"},
            None,
        )
        self.assertEqual(writes, [])

    async def test_extract_structured_intents_parses_answer_and_extract(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        response = json.dumps(
            [
                {"action": "answer", "description": "Answer expected completion date", "target_hint": {"name": "期望完成时间"}},
                {"action": "extract_text", "description": "Extract expected completion date", "result_key": "expected_completion_date", "target_hint": {"name": "期望完成时间"}},
            ],
            ensure_ascii=False,
        )

        intents = assistant._extract_structured_intents(response)

        self.assertEqual([intent["action"] for intent in intents], ["answer", "extract_text"])
```

- [ ] **Step 2: Run the assistant tests to verify they fail**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend && python -m pytest tests/test_rpa_assistant.py::RPAAssistantAnswerIntentTests -v
```

Expected:
- `FAIL` because `answer` is not yet recognized as a first-class action contract.

- [ ] **Step 3: Extend the prompt and context-write rules for `answer`**

Update `SYSTEM_PROMPT` in `RpaClaw/backend/rpa/assistant.py`:

```python
{
  "action": "answer|navigate|click|fill|extract_text|press",
  "description": "short action summary",
  "prompt": "original user instruction",
  "result_key": "short_ascii_snake_case_key_for_extracted_value",
  "target_hint": {
    "role": "button|link|textbox|...",
    "name": "semantic label if known"
  },
  "value": "text to fill, key to press, or answer text when relevant"
}
```

Add explicit rules:

```text
10. When the user is asking what a visible field or page value is, use "action": "answer".
11. "answer" is for replying to the user and must not be used to persist a recorded extraction step.
12. A single user request may return both "answer" and "extract_text" actions when the user both asks a question and asks to save the value.
```

Keep `_compute_context_writes()` guarded:

```python
        if action != "extract_text":
            return []
```

- [ ] **Step 4: Support `answer` execution in the runtime**

Add an `answer` branch in `resolve_structured_intent()` in `RpaClaw/backend/rpa/assistant_runtime.py`:

```python
    if action == "answer":
        field_group = _resolve_field_group(snapshot, intent)
        if field_group:
            return {
                **intent,
                "resolved": {
                    "frame_path": list(field_group.get("frame_path") or []),
                    "locator": dict(field_group.get("value_locator") or {"method": "text", "value": field_group.get("field_value", "")}),
                    "fallback_locator": {"method": "text", "value": field_group.get("field_value", "")},
                    "field_group": field_group,
                    "locator_candidates": [],
                    "collection_hint": {},
                    "item_hint": {},
                    "ordinal": None,
                    "selected_locator_kind": "field_group_answer",
                },
            }
```

Add execution support in `execute_structured_intent()`:

```python
    elif action == "answer":
        output = await locator.first.inner_text()
```

Set the returned step metadata so `assistant.py` can drop it later:

```python
        "action": action,
        "source": "ai",
        "target": json.dumps(step_target, ensure_ascii=False),
        "record_step": action != "answer",
```

- [ ] **Step 5: Drop `answer` steps from persistence in `assistant.py`**

In `_execute_intent_with_ledger()` and the single-response path, only append/persist steps when `step_data.get("record_step", True)` is true:

```python
        if result.get("success") and resolution:
            step_data = result.get("step")
            if step_data and not step_data.get("record_step", True):
                return result, intent_reads
```

And in the single-intent save path:

```python
        if step_data and step_data.get("record_step", True):
            context_writes = self._compute_context_writes(message, step_data, resolution)
            ...
```

- [ ] **Step 6: Run the assistant tests to verify they pass**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend && python -m pytest tests/test_rpa_assistant.py::RPAAssistantAnswerIntentTests -v
```

Expected:
- `2 passed`

- [ ] **Step 7: Commit the `answer` intent support**

```bash
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: add answer intent for recording assistant"
```

## Task 4: Verify Mixed Flow End To End And Tighten Regression Coverage

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant_runtime.py`

- [ ] **Step 1: Add a failing mixed-flow regression test**

Add this test to `RpaClaw/backend/tests/test_rpa_assistant.py`:

```python
    async def test_mixed_answer_and_extract_only_records_extract_step(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        assistant._extract_structured_intents = lambda _response: [
            {"action": "answer", "description": "Answer expected completion date", "target_hint": {"name": "期望完成时间"}},
            {"action": "extract_text", "description": "Extract expected completion date", "result_key": "expected_completion_date", "target_hint": {"name": "期望完成时间"}},
        ]

        async def fake_execute(intent_json, *_args, **_kwargs):
            if intent_json["action"] == "answer":
                return {"success": True, "output": "期望完成时间是 2025-06-13", "step": {"action": "answer", "record_step": False}}, []
            return {"success": True, "output": "2025-06-13", "step": {"action": "extract_text", "id": "step-1", "result_key": "expected_completion_date", "record_step": True}}, []

        assistant._execute_intent_with_ledger = fake_execute
```

Assert:

```python
        events = []
        async for event in assistant.chat("session-1", _FakePage(), "看看期望完成时间是什么，并保存成参数", [], page_provider=lambda: _FakePage()):
            events.append(event)

        saved_steps = [event for event in events if event.get("event") == "step_recorded"]
        self.assertEqual(len(saved_steps), 1)
        self.assertEqual(saved_steps[0]["data"]["step"]["action"], "extract_text")
```

- [ ] **Step 2: Run the focused regression test and verify it fails**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend && python -m pytest tests/test_rpa_assistant.py::RPAAssistantAnswerIntentTests::test_mixed_answer_and_extract_only_records_extract_step -v
```

Expected:
- `FAIL` because the chat flow does not yet cleanly separate answer output from recorded steps.

- [ ] **Step 3: Adjust chat flow event handling until the mixed test passes**

Use this shape in `RpaClaw/backend/rpa/assistant.py` when aggregating multi-intent results:

```python
                    if intent_result.get("success") and intent_result.get("step", {}).get("record_step", True):
                        yield {
                            "event": "step_recorded",
                            "data": {"step": intent_result["step"]},
                        }
```

And build a final answer message from any non-empty `answer` outputs:

```python
                answer_outputs = [
                    item.get("output", "")
                    for item in multi_results
                    if item.get("success") and item.get("step", {}).get("action") == "answer"
                ]
                if answer_outputs:
                    yield {"event": "message_chunk", "data": {"text": "\n".join(answer_outputs) + "\n"}}
```

- [ ] **Step 4: Run the full assistant/runtime regression set**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend && python -m pytest tests/test_rpa_assistant.py tests/test_rpa_assistant_runtime.py -v
```

Expected:
- All assistant and runtime tests pass.

- [ ] **Step 5: Run the broader RPA smoke suite**

Run:

```bash
cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend && python -m pytest tests/test_rpa_assistant.py tests/test_rpa_assistant_runtime.py tests/test_rpa_generator.py tests/test_rpa_manager.py -v
```

Expected:
- No regressions in assistant, runtime, generator, or manager coverage.

- [ ] **Step 6: Commit the end-to-end regression pass**

```bash
git add RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/backend/tests/test_rpa_assistant_runtime.py RpaClaw/backend/rpa/assistant.py
git commit -m "test: cover mixed answer and extract flows"
```

## Self-Review Notes

- Spec coverage:
  - LLM-level `answer`/`extract_text` split is covered in Task 3.
  - `field_groups` snapshot structure is covered in Task 2.
  - `field_groups`-first extraction plus `content_nodes` fallback is covered in Task 1.
  - Mixed question + extraction behavior is covered in Task 4.
- Placeholder scan:
  - No `TODO`/`TBD`/“implement later” text remains.
  - Each task includes exact files, code shapes, commands, and expected outcomes.
- Type consistency:
  - `field_groups`, `fallback_locator`, `record_step`, and `fallback_used` are used consistently across tasks.
