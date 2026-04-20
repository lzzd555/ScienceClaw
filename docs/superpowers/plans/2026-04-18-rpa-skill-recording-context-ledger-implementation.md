# RPA Skill Recording Context Ledger Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the recorder's visible agent-mode switch and implement a task-scoped context ledger so exported skills can rebuild the minimum replay context, including user-requested extracted values and cross-page data passing.

**Architecture:** Keep the current FastAPI + Vue recorder pipeline, but add a formal task-context ledger to the session model, teach the assistant to persist context reads and writes, and upgrade the generator/exporter to emit a context-rebuild phase before business steps. Replay correctness is the primary acceptance rule, so tests lead each backend change.

**Tech Stack:** FastAPI, Pydantic v2 models, Playwright async Python, Vue 3 + TypeScript, pytest/unittest backend tests

---

## File Structure

### Existing files to modify

- `RpaClaw/backend/rpa/manager.py`
  Add task-context and context-ledger models, persist them in session state, and expose helper methods for step/context bookkeeping.
- `RpaClaw/backend/rpa/assistant.py`
  Remove the need for user-visible mode switching, classify extraction intent, and record context reads/writes plus rebuild actions.
- `RpaClaw/backend/rpa/generator.py`
  Generate a `rebuild_context(...)` phase and wire dependent steps to runtime context instead of relying on recording-time memory.
- `RpaClaw/backend/rpa/skill_exporter.py`
  Export the formal context contract in skill metadata alongside the generated script.
- `RpaClaw/backend/route/rpa.py`
  Adjust request/response wiring if the chat payload or save/export path needs to carry context metadata.
- `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
  Remove the visible `agentMode` toggle, keep one continuous AI recording flow, and surface lightweight context-capture status.
- `RpaClaw/backend/tests/test_rpa_manager.py`
  Add session/context-ledger persistence and rebuild-action tests.
- `RpaClaw/backend/tests/test_rpa_assistant.py`
  Add assistant context-promotion and read/write contract tests.
- `RpaClaw/backend/tests/test_rpa_generator.py`
  Add context rebuild and cross-page replay-generation tests.

### New files to create

- `RpaClaw/backend/rpa/context_ledger.py`
  Focused model/helper module for task-context state, value-promotion rules, and replay-contract utilities so `manager.py` and `assistant.py` do not become harder to reason about.
- `RpaClaw/backend/tests/test_rpa_context_ledger.py`
  Dedicated unit tests for context-promotion rules and replay-contract assembly.

---

## Chunk 1: Task Context Contract

### Task 1: Add failing tests for the context-ledger rules

**Files:**
- Create: `RpaClaw/backend/tests/test_rpa_context_ledger.py`
- Test: `RpaClaw/backend/tests/test_rpa_context_ledger.py`

- [ ] **Step 1: Write a failing test for explicit extraction always entering context**

```python
def test_explicit_user_requested_extraction_is_promoted():
    ledger = TaskContextLedger()
    result = ledger.should_promote_value(
        key="person_name",
        source="dom_extraction",
        user_explicit=True,
        runtime_required=False,
        consumed_later=False,
    )
    assert result is True
```

- [ ] **Step 2: Write a failing test for nonessential observations staying out of context**

```python
def test_nonessential_observation_is_not_promoted():
    ledger = TaskContextLedger()
    result = ledger.should_promote_value(
        key="sidebar_hint",
        source="observation",
        user_explicit=False,
        runtime_required=False,
        consumed_later=False,
    )
    assert result is False
```

- [ ] **Step 3: Write a failing test for runtime-required cross-page data being promoted**

```python
def test_cross_page_runtime_dependency_is_promoted():
    ledger = TaskContextLedger()
    result = ledger.should_promote_value(
        key="person_id",
        source="dom_extraction",
        user_explicit=False,
        runtime_required=True,
        consumed_later=True,
    )
    assert result is True
```

- [ ] **Step 4: Run the new test file to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_context_ledger.py -v`

Expected: FAIL because `TaskContextLedger` does not exist yet.

- [ ] **Step 5: Commit the failing-test scaffold**

```bash
git add RpaClaw/backend/tests/test_rpa_context_ledger.py
git commit -m "test: add context ledger promotion coverage"
```

### Task 2: Implement the context-ledger model and helpers

**Files:**
- Create: `RpaClaw/backend/rpa/context_ledger.py`
- Modify: `RpaClaw/backend/rpa/manager.py`
- Test: `RpaClaw/backend/tests/test_rpa_context_ledger.py`
- Test: `RpaClaw/backend/tests/test_rpa_manager.py`

- [ ] **Step 1: Create focused Pydantic models for context values and rebuild actions**

```python
class ContextValue(BaseModel):
    key: str
    value: Any = None
    user_explicit: bool = False
    runtime_required: bool = False
    source_step_id: str | None = None
    source_kind: str = "observation"


class ContextRebuildAction(BaseModel):
    action: str
    description: str
    step_ref: str | None = None
    writes: list[str] = Field(default_factory=list)
```

- [ ] **Step 2: Implement a small `TaskContextLedger` API**

```python
class TaskContextLedger(BaseModel):
    page_context: dict[str, Any] = Field(default_factory=dict)
    observed_values: dict[str, ContextValue] = Field(default_factory=dict)
    derived_values: dict[str, ContextValue] = Field(default_factory=dict)
    rebuild_actions: list[ContextRebuildAction] = Field(default_factory=list)

    def should_promote_value(...): ...
    def record_value(...): ...
    def record_rebuild_action(...): ...
```

- [ ] **Step 3: Extend `RPASession` to carry `task_context_id` and `context_ledger`**

```python
class RPASession(BaseModel):
    ...
    task_context_id: str | None = None
    context_ledger: TaskContextLedger = Field(default_factory=TaskContextLedger)
```

- [ ] **Step 4: Add manager helpers for context bookkeeping**

```python
def ensure_task_context(self, session_id: str) -> str: ...
def record_context_value(self, session_id: str, **kwargs) -> None: ...
def record_rebuild_action(self, session_id: str, **kwargs) -> None: ...
```

- [ ] **Step 5: Add manager tests for default ledger creation and rebuild-action persistence**

```python
async def test_session_initializes_task_context():
    session = await manager.create_session(...)
    assert session.task_context_id
    assert session.context_ledger.rebuild_actions == []
```

- [ ] **Step 6: Run the focused tests**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_context_ledger.py RpaClaw/backend/tests/test_rpa_manager.py -v`

Expected: PASS for the new context-ledger cases.

- [ ] **Step 7: Commit the contract layer**

```bash
git add RpaClaw/backend/rpa/context_ledger.py RpaClaw/backend/rpa/manager.py RpaClaw/backend/tests/test_rpa_context_ledger.py RpaClaw/backend/tests/test_rpa_manager.py
git commit -m "feat: add task-scoped context ledger state"
```

---

## Chunk 2: Assistant Context Promotion And Persistence

### Task 3: Add failing assistant tests for context reads and writes

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: Add a failing test for explicit extraction promotion**

```python
async def test_assistant_promotes_explicit_extraction_to_context():
    result = await assistant.handle_turn(
        session_id="session-1",
        message="提取当前人物姓名，后面要填写到另一个页面",
    )
    assert result.context_writes == ["person_name"]
```

- [ ] **Step 2: Add a failing test for runtime-only required values**

```python
async def test_assistant_promotes_runtime_required_cross_page_value():
    result = await assistant.handle_turn(
        session_id="session-1",
        message="打开详情后把编号填写到登记页",
    )
    assert "person_id" in result.context_writes
```

- [ ] **Step 3: Add a failing test for nonessential observations not being promoted**

```python
async def test_assistant_ignores_nonessential_observations():
    result = await assistant.handle_turn(
        session_id="session-1",
        message="继续下一步",
    )
    assert "sidebar_hint" not in result.context_writes
```

- [ ] **Step 4: Run the focused assistant tests to verify failure**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_assistant.py -k context -v`

Expected: FAIL because assistant results do not yet expose the contract.

- [ ] **Step 5: Commit the failing assistant tests**

```bash
git add RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "test: cover assistant context promotion rules"
```

### Task 4: Teach the assistant to manage one task context and persist the contract

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Modify: `RpaClaw/backend/rpa/manager.py`
- Modify: `RpaClaw/backend/route/rpa.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: Add a small extraction-intent detector that distinguishes explicit requests from incidental observations**

```python
def _is_explicit_extraction_request(message: str) -> bool:
    keywords = ["提取", "读取", "获取", "总结", "记录"]
    return any(word in message for word in keywords)
```

- [ ] **Step 2: Add a turn result shape that includes context reads and writes**

```python
{
    "step_payload": ...,
    "context_reads": ["person_name"],
    "context_writes": ["person_id"],
    "rebuild_actions": [...],
}
```

- [ ] **Step 3: Call session helpers so each turn reuses one `task_context_id`**

```python
task_context_id = rpa_manager.ensure_task_context(session_id)
```

- [ ] **Step 4: Promote eligible values into the ledger right after successful extraction or planning**

```python
if ledger.should_promote_value(...):
    rpa_manager.record_context_value(...)
```

- [ ] **Step 5: Persist per-step `context_reads` and `context_writes` on the saved step payload**

```python
step_payload["context_reads"] = context_reads
step_payload["context_writes"] = context_writes
```

- [ ] **Step 6: Keep transport changes minimal in `route/rpa.py` so the frontend can receive status without choosing a mode**

```python
body = ChatRequest(message=user_text, mode="chat")
```

- [ ] **Step 7: Run assistant coverage**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/backend/tests/test_rpa_manager.py -v`

Expected: PASS for context promotion and task-context continuity cases.

- [ ] **Step 8: Commit the assistant integration**

```bash
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/rpa/manager.py RpaClaw/backend/route/rpa.py RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/backend/tests/test_rpa_manager.py
git commit -m "feat: persist assistant task context contract"
```

---

## Chunk 3: Generator And Export Replay Contract

### Task 5: Add failing generator tests for context rebuild output

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`
- Test: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Add a failing test for `rebuild_context(...)` generation**

```python
def test_generator_emits_rebuild_context_function():
    script = PlaywrightGenerator().generate_script(steps=[...], params={...})
    assert "async def rebuild_context(page, context, **kwargs):" in script
```

- [ ] **Step 2: Add a failing test for cross-page value transfer**

```python
def test_generator_rebuilds_page_a_value_before_page_b_fill():
    script = PlaywrightGenerator().generate_script(steps=[...], params={...})
    assert 'context["person_name"]' in script
    assert script.index("await rebuild_context") < script.index("fill(")
```

- [ ] **Step 3: Add a failing test for `ai_script` still honoring context reads and writes**

```python
def test_ai_script_step_still_reads_runtime_context():
    script = PlaywrightGenerator().generate_script(steps=[...], params={...})
    assert "context.get(" in script
```

- [ ] **Step 4: Run generator tests to verify failure**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_generator.py -v`

Expected: FAIL because generator still emits action-only playback.

- [ ] **Step 5: Commit the failing generator tests**

```bash
git add RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "test: add replay context generation coverage"
```

### Task 6: Implement the replay rebuild phase and export contract

**Files:**
- Modify: `RpaClaw/backend/rpa/generator.py`
- Modify: `RpaClaw/backend/rpa/skill_exporter.py`
- Modify: `RpaClaw/backend/route/rpa.py`
- Test: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Add a helper that derives `required_context_outputs` and `context_rebuild_plan` from recorded steps**

```python
def _collect_context_contract(self, steps):
    return {
        "required_context_outputs": [...],
        "context_rebuild_plan": [...],
    }
```

- [ ] **Step 2: Emit `rebuild_context(...)` before `execute_skill(...)`**

```python
async def rebuild_context(page, context, **kwargs):
    ...
    return context
```

- [ ] **Step 3: Make `execute_skill(...)` initialize and hydrate runtime context**

```python
context = {}
context = await rebuild_context(page, context, **kwargs)
```

- [ ] **Step 4: When a step reads a context variable, generate code that reads from `context` instead of replay-time memory**

```python
person_name = context["person_name"]
await current_page.get_by_label("姓名").fill(person_name)
```

- [ ] **Step 5: Export the context contract in `SKILL.md` and any structured metadata payload**

```json
{
  "required_context_outputs": ["person_name"],
  "context_rebuild_plan": [...]
}
```

- [ ] **Step 6: Keep backward compatibility for old sessions by treating missing context metadata as an empty contract**

```python
context_reads = step.get("context_reads") or []
```

- [ ] **Step 7: Run generator and exporter tests**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_generator.py RpaClaw/backend/tests/test_rpa_assistant.py -v`

Expected: PASS for replay-contract coverage.

- [ ] **Step 8: Commit replay generation**

```bash
git add RpaClaw/backend/rpa/generator.py RpaClaw/backend/rpa/skill_exporter.py RpaClaw/backend/route/rpa.py RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "feat: generate replay context rebuild phase"
```

---

## Chunk 4: Recorder UI And End-To-End Verification

### Task 7: Add targeted UI checks for one continuous AI flow

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`

- [ ] **Step 1: Remove the visible `agentMode` state and toggle UI**

```ts
const agentRunning = ref(false)
```

- [ ] **Step 2: Always send one recorder chat mode from the frontend**

```ts
body: JSON.stringify({ message: userText, mode: 'chat' })
```

- [ ] **Step 3: Update copy so users see one AI recording flow instead of mode choices**

```vue
{{ agentRunning ? 'AI 正在执行录制任务...' : '已就绪 · AI 协助录制中' }}
```

- [ ] **Step 4: Add lightweight context-capture feedback**

```ts
chatMessages.value[msgIdx].diagnostics = ['已记录上下文变量：人物姓名']
```

- [ ] **Step 5: Smoke-check the page locally**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm run build`

Expected: PASS with no TypeScript or Vue compile errors.

- [ ] **Step 6: Commit the UI update**

```bash
git add RpaClaw/frontend/src/pages/rpa/RecorderPage.vue
git commit -m "feat: unify recorder ai flow"
```

### Task 8: Run end-to-end acceptance checks and document outcomes

**Files:**
- Modify: `docs/superpowers/specs/2026-04-18-rpa-skill-recording-context-ledger-design.md` (only if implementation-driven clarifications are needed)

- [ ] **Step 1: Run the focused backend test suite**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_context_ledger.py RpaClaw/backend/tests/test_rpa_manager.py RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/backend/tests/test_rpa_generator.py -v`

Expected: PASS.

- [ ] **Step 2: Manually verify the recorder no longer shows a mode switch**

Expected: the recorder exposes one continuous AI recording flow only.

- [ ] **Step 3: Manually verify page-A-to-page-B replay generation**

Expected:
- explicit extraction values appear in the context contract
- generated script rebuilds context before the dependent fill

- [ ] **Step 4: Manually verify unrelated observations are absent from exported context**

Expected: only user-requested extraction values and runtime-required values appear.

- [ ] **Step 5: If implementation revealed contract drift, update the design doc with precise clarifications**

```markdown
Clarify backward-compatibility behavior for older recordings with no context ledger.
```

- [ ] **Step 6: Commit final verification or doc clarifications**

```bash
git add RpaClaw/backend/tests/test_rpa_context_ledger.py RpaClaw/backend/tests/test_rpa_manager.py RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/backend/tests/test_rpa_generator.py RpaClaw/frontend/src/pages/rpa/RecorderPage.vue docs/superpowers/specs/2026-04-18-rpa-skill-recording-context-ledger-design.md
git commit -m "test: verify context-ledger replay flow"
```

---

## Notes For Execution

- Keep steps small and commit after each task block.
- Prefer adding `context_reads` / `context_writes` as formal step fields rather than hiding them in diagnostics.
- Preserve old recordings by treating absent context metadata as empty, not invalid.
- Do not broaden context promotion beyond:
  - user-explicit extraction outputs
  - runtime-required replay dependencies

## Suggested Execution Order

1. Chunk 1
2. Chunk 2
3. Chunk 3
4. Chunk 4

Plan complete and saved to `docs/superpowers/plans/2026-04-18-rpa-skill-recording-context-ledger-implementation.md`. Ready to execute?
