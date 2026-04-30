# API Monitor Realtime Tool Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build realtime API Monitor tool candidates so captured API calls appear immediately, generate MCP tool drafts in a bounded background worker, and keep final adoption on the existing confidence/selected system.

**Architecture:** Keep `CapturedApiCall` as the source of truth. Add a session-local `ApiToolGenerationCandidate` layer for realtime placeholders, then run an idempotent, rate-limited async generator that turns candidates into existing `ApiToolDefinition` objects. The worker is recoverable from `session.captured_calls`, and publishing remains based only on generated tools with `selected == true`.

**Tech Stack:** FastAPI, Python 3.13, Pydantic v2, asyncio, Playwright, Vue 3, TypeScript, Vitest, pytest.

---

## File Structure

- Modify `RpaClaw/backend/rpa/api_monitor/models.py`
  - Add `GenerationStatus`, `ApiToolGenerationCandidate`, `generation_candidates`, and `generation_candidate_id`.
- Modify `RpaClaw/backend/rpa/api_monitor/manager.py`
  - Add candidate upsert/reconcile helpers, bounded background generation worker, rate-limit handling, event sink support, and integration points in recording/analysis flows.
- Modify `RpaClaw/backend/route/api_monitor.py`
  - Add candidate list/retry endpoints and pass SSE emit callbacks into manager analysis methods.
- Modify `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`
  - New focused backend tests for candidate dedup, worker success, 429, failure, and reconcile.
- Modify `RpaClaw/backend/tests/test_api_monitor_capture.py`
  - Keep existing capture and dedup tests passing during integration.
- Modify `RpaClaw/frontend/src/api/apiMonitor.ts`
  - Add candidate types, list/retry APIs, and adjust `stopRecording()` response typing to include candidates.
- Modify `RpaClaw/frontend/src/api/apiMonitor.test.ts`
  - Test candidate endpoints and new stop recording response compatibility.
- Modify `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`
  - Add candidate state, SSE event handlers, placeholder cards, retry action, and refreshed counts.
- Frontend verification relies on `src/api/apiMonitor.test.ts` plus `npm run build` for this pass.

Implementation should not introduce a persistent queue in the first pass. The manager owns in-memory queue state and can reconstruct candidates from captured calls.

---

### Task 1: Backend Models For Generation Candidates

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/models.py`
- Test: `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`

- [ ] **Step 1: Write failing model tests**

Create `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py` with the model-level tests first:

```python
from datetime import datetime

from backend.rpa.api_monitor.models import (
    ApiMonitorSession,
    ApiToolDefinition,
    ApiToolGenerationCandidate,
)


def test_generation_candidate_defaults_are_serializable():
    candidate = ApiToolGenerationCandidate(
        session_id="session-1",
        dedup_key="GET /api/orders",
        method="GET",
        url_pattern="/api/orders",
    )

    dumped = candidate.model_dump(mode="json")

    assert dumped["status"] == "pending"
    assert dumped["source_call_ids"] == []
    assert dumped["sample_call_ids"] == []
    assert dumped["tool_id"] is None
    assert dumped["attempts"] == 0
    assert dumped["retry_after"] is None
    assert dumped["capture_dom_context"] == {}
    assert isinstance(dumped["created_at"], str)
    assert isinstance(dumped["updated_at"], str)


def test_session_contains_generation_candidates_by_default():
    session = ApiMonitorSession(
        user_id="user-1",
        sandbox_session_id="sandbox-1",
    )

    assert session.generation_candidates == []


def test_tool_definition_can_reference_generation_candidate():
    tool = ApiToolDefinition(
        session_id="session-1",
        name="list_orders",
        description="List orders",
        method="GET",
        url_pattern="/api/orders",
        yaml_definition="name: list_orders",
        generation_candidate_id="candidate-1",
    )

    assert tool.generation_candidate_id == "candidate-1"
    assert tool.model_dump(mode="json")["generation_candidate_id"] == "candidate-1"
```

- [ ] **Step 2: Run model tests and verify failure**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py -q
```

Expected: fail with import or attribute errors for `ApiToolGenerationCandidate`, `generation_candidates`, or `generation_candidate_id`.

- [ ] **Step 3: Add model fields**

In `RpaClaw/backend/rpa/api_monitor/models.py`, update imports and add the model near `ApiToolDefinition`:

```python
from typing import Dict, List, Literal, Optional
```

Add:

```python
GenerationStatus = Literal[
    "pending",
    "running",
    "generated",
    "failed",
    "rate_limited",
    "stale",
]


class ApiToolGenerationCandidate(BaseModel):
    id: str = Field(default_factory=_gen_id)
    session_id: str
    dedup_key: str
    method: str
    url_pattern: str
    source_call_ids: List[str] = Field(default_factory=list)
    sample_call_ids: List[str] = Field(default_factory=list)
    status: GenerationStatus = "pending"
    tool_id: Optional[str] = None
    error: str = ""
    retry_after: Optional[datetime] = None
    attempts: int = 0
    capture_dom_context: Dict = Field(default_factory=dict)
    capture_page_url: str = ""
    capture_title: str = ""
    capture_dom_digest: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

Add to `ApiToolDefinition`:

```python
    generation_candidate_id: Optional[str] = None
```

Add to `ApiMonitorSession`:

```python
    generation_candidates: List[ApiToolGenerationCandidate] = Field(default_factory=list)
```

- [ ] **Step 4: Run model tests and verify pass**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py -q
```

Expected: all tests in the file pass.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/tests/test_api_monitor_realtime_generation.py
git commit -m "feat: add api monitor generation candidate model"
```

---

### Task 2: Candidate Upsert And Reconcile Helpers

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`

- [ ] **Step 1: Add failing helper tests**

Append these tests to `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`:

```python
from datetime import datetime

from backend.rpa.api_monitor.manager import ApiMonitorSessionManager
from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest, CapturedResponse


def _call(call_id: str, url: str = "https://example.com/api/orders?page=1") -> CapturedApiCall:
    return CapturedApiCall(
        id=call_id,
        request=CapturedRequest(
            request_id=call_id,
            url=url,
            method="GET",
            headers={},
            timestamp=datetime(2026, 1, 1),
            resource_type="fetch",
        ),
        response=CapturedResponse(
            status=200,
            status_text="OK",
            headers={"content-type": "application/json"},
            body='{"items":[{"id":1}]}',
            content_type="application/json",
            timestamp=datetime(2026, 1, 1),
        ),
        url_pattern="/api/orders?page={page}",
    )


def _manager_with_session() -> tuple[ApiMonitorSessionManager, str]:
    manager = ApiMonitorSessionManager()
    session = ApiMonitorSession(
        id="session-1",
        user_id="user-1",
        sandbox_session_id="sandbox-1",
        target_url="https://example.com/app",
    )
    manager.sessions[session.id] = session
    return manager, session.id


def test_upsert_generation_candidate_creates_placeholder():
    manager, session_id = _manager_with_session()
    call = _call("call-1")

    candidate, created = manager._upsert_generation_candidate(
        session_id,
        call,
        dom_context={"inputs": [{"name": "page"}]},
        page_url="https://example.com/app",
        title="Orders",
        dom_digest="digest-1",
    )

    assert created is True
    assert candidate.status == "pending"
    assert candidate.method == "GET"
    assert candidate.url_pattern == "/api/orders?page={page}"
    assert candidate.source_call_ids == ["call-1"]
    assert candidate.sample_call_ids == ["call-1"]
    assert candidate.capture_dom_context["inputs"][0]["name"] == "page"
    assert manager.sessions[session_id].generation_candidates == [candidate]


def test_upsert_generation_candidate_dedups_and_marks_generated_stale():
    manager, session_id = _manager_with_session()
    first = _call("call-1", "https://example.com/api/orders?page=1")
    second = _call("call-2", "https://example.com/api/orders?page=2")

    candidate, _ = manager._upsert_generation_candidate(session_id, first)
    candidate.status = "generated"
    candidate.tool_id = "tool-1"

    updated, created = manager._upsert_generation_candidate(session_id, second)

    assert created is False
    assert updated.id == candidate.id
    assert updated.status == "stale"
    assert updated.source_call_ids == ["call-1", "call-2"]
    assert updated.sample_call_ids == ["call-1", "call-2"]


def test_reconcile_generation_candidates_rebuilds_missing_candidate():
    manager, session_id = _manager_with_session()
    session = manager.sessions[session_id]
    session.captured_calls.append(_call("call-1"))

    candidates = manager.reconcile_generation_candidates(session_id, enqueue=False)

    assert len(candidates) == 1
    assert candidates[0].source_call_ids == ["call-1"]
    assert session.generation_candidates[0].dedup_key == "GET /api/orders"
```

- [ ] **Step 2: Run helper tests and verify failure**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py -q
```

Expected: fail because `_upsert_generation_candidate()` and `reconcile_generation_candidates()` do not exist.

- [ ] **Step 3: Implement upsert and reconcile helpers**

In `RpaClaw/backend/rpa/api_monitor/manager.py`, import the new model:

```python
from .models import ApiMonitorSession, ApiToolDefinition, ApiToolGenerationCandidate, CapturedApiCall, DirectedAnalysisTrace
```

Add helpers inside `ApiMonitorSessionManager`:

```python
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
```

Add a temporary no-op enqueue method so these tests pass before the worker task:

```python
    def _enqueue_generation_candidate(self, session_id: str, candidate_id: str) -> None:
        return None
```

- [ ] **Step 4: Run helper tests and verify pass**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py -q
```

Expected: all current realtime generation tests pass.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_realtime_generation.py
git commit -m "feat: add api monitor generation candidate helpers"
```

---

### Task 3: Background Worker Success Path

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`

- [ ] **Step 1: Add failing worker success test**

Append:

```python
import pytest


@pytest.mark.asyncio
async def test_generate_candidate_creates_tool_and_applies_confidence(monkeypatch):
    manager, session_id = _manager_with_session()
    session = manager.sessions[session_id]
    call = _call("call-1")
    session.captured_calls.append(call)
    candidate, _ = manager._upsert_generation_candidate(
        session_id,
        call,
        dom_context={"forms": [{"inputs": [{"name": "page"}]}]},
        page_url="https://example.com/app",
        title="Orders",
        dom_digest="digest-1",
    )

    async def fake_generate_tool_definition(**kwargs):
        assert kwargs["method"] == "GET"
        assert kwargs["url_pattern"] == "/api/orders?page={page}"
        assert "page" in kwargs["dom_context"]
        return (
            "name: list_orders\n"
            "description: List orders\n"
            "method: GET\n"
            "url: /api/orders\n"
            "parameters:\n"
            "  type: object\n"
            "  properties: {}\n"
            "response:\n"
            "  type: object\n"
            "  properties: {}\n"
        )

    monkeypatch.setattr(
        "backend.rpa.api_monitor.manager.generate_tool_definition",
        fake_generate_tool_definition,
    )

    tool = await manager._generate_tool_for_candidate(session_id, candidate.id)

    assert tool is not None
    assert tool.name == "list_orders"
    assert tool.generation_candidate_id == candidate.id
    assert tool.source_calls == ["call-1"]
    assert tool.confidence == "high"
    assert tool.selected is True
    assert candidate.status == "generated"
    assert candidate.tool_id == tool.id
    assert session.tool_definitions == [tool]
```

- [ ] **Step 2: Run worker test and verify failure**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py::test_generate_candidate_creates_tool_and_applies_confidence -q
```

Expected: fail because `_generate_tool_for_candidate()` does not exist.

- [ ] **Step 3: Implement candidate generation method**

In `RpaClaw/backend/rpa/api_monitor/manager.py`, add:

```python
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

        yaml_def = await generate_tool_definition(
            method=candidate.method,
            url_pattern=candidate.url_pattern,
            samples=samples,
            page_context=candidate.capture_page_url or session.target_url or "",
            dom_context=dom_context,
            model_config=model_config,
        )
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
```

- [ ] **Step 4: Run worker success test**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py::test_generate_candidate_creates_tool_and_applies_confidence -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_realtime_generation.py
git commit -m "feat: generate api monitor tools from candidates"
```

---

### Task 4: Worker Queue, Rate Limit, And Failure States

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`

- [ ] **Step 1: Add failing error-state tests**

Append:

```python
@pytest.mark.asyncio
async def test_candidate_generation_rate_limit_sets_retry_after(monkeypatch):
    manager, session_id = _manager_with_session()
    session = manager.sessions[session_id]
    call = _call("call-1")
    session.captured_calls.append(call)
    candidate, _ = manager._upsert_generation_candidate(session_id, call)

    async def fake_generate_tool_definition(**kwargs):
        raise RuntimeError("429 rate limit exceeded")

    monkeypatch.setattr(
        "backend.rpa.api_monitor.manager.generate_tool_definition",
        fake_generate_tool_definition,
    )

    tool = await manager._generate_tool_for_candidate(session_id, candidate.id)

    assert tool is None
    assert candidate.status == "rate_limited"
    assert candidate.attempts == 1
    assert candidate.retry_after is not None
    assert "429" in candidate.error


@pytest.mark.asyncio
async def test_candidate_generation_failure_keeps_captured_call(monkeypatch):
    manager, session_id = _manager_with_session()
    session = manager.sessions[session_id]
    call = _call("call-1")
    session.captured_calls.append(call)
    candidate, _ = manager._upsert_generation_candidate(session_id, call)

    async def fake_generate_tool_definition(**kwargs):
        raise ValueError("bad yaml")

    monkeypatch.setattr(
        "backend.rpa.api_monitor.manager.generate_tool_definition",
        fake_generate_tool_definition,
    )

    tool = await manager._generate_tool_for_candidate(session_id, candidate.id)

    assert tool is None
    assert candidate.status == "failed"
    assert candidate.attempts == 1
    assert candidate.error == "bad yaml"
    assert session.captured_calls == [call]
```

- [ ] **Step 2: Run error-state tests and verify failure**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py::test_candidate_generation_rate_limit_sets_retry_after tests/test_api_monitor_realtime_generation.py::test_candidate_generation_failure_keeps_captured_call -q
```

Expected: fail because exceptions are not converted into candidate states.

- [ ] **Step 3: Add queue state and error handling**

In `ApiMonitorSessionManager.__init__`, add:

```python
        self._generation_tasks: Dict[str, Dict[str, asyncio.Task[None]]] = defaultdict(dict)
        self._generation_semaphore = asyncio.Semaphore(2)
```

Replace the temporary no-op `_enqueue_generation_candidate()` with:

```python
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
```

Wrap `_generate_tool_for_candidate()` generation body in `try/except` and add helpers:

```python
    def _is_rate_limit_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return "429" in text or "rate limit" in text or "too many requests" in text

    def _retry_after_for_attempt(self, attempts: int) -> datetime:
        delay = min(300, 2 ** max(attempts - 1, 0))
        return datetime.now() + timedelta(seconds=delay)
```

Also import `timedelta`:

```python
from datetime import datetime, timedelta
```

In the `except Exception as exc` block:

```python
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
```

- [ ] **Step 4: Run realtime backend tests**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py -q
```

Expected: all realtime generation tests pass.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_realtime_generation.py
git commit -m "feat: add api monitor generation worker states"
```

---

### Task 5: Integrate Candidate Upsert Into Capture Drains

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`

- [ ] **Step 1: Add failing processing helper test**

Append:

```python
@pytest.mark.asyncio
async def test_process_captured_calls_appends_session_and_enqueues(monkeypatch):
    manager, session_id = _manager_with_session()
    enqueued: list[tuple[str, str]] = []

    def fake_enqueue(session_id_arg: str, candidate_id: str, **kwargs):
        enqueued.append((session_id_arg, candidate_id))

    monkeypatch.setattr(manager, "_enqueue_generation_candidate", fake_enqueue)

    calls = [_call("call-1")]
    candidates = await manager._process_captured_calls_for_generation(
        session_id,
        calls,
        dom_context={"inputs": [{"name": "q"}]},
        page_url="https://example.com/app",
        title="Orders",
        dom_digest="digest-1",
    )

    session = manager.sessions[session_id]
    assert session.captured_calls == calls
    assert len(candidates) == 1
    assert enqueued == [(session_id, candidates[0].id)]
```

- [ ] **Step 2: Run processing helper test and verify failure**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py::test_process_captured_calls_appends_session_and_enqueues -q
```

Expected: fail because `_process_captured_calls_for_generation()` does not exist.

- [ ] **Step 3: Implement captured-call processing helper**

Add to `ApiMonitorSessionManager`:

```python
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
```

- [ ] **Step 4: Replace direct `session.captured_calls.extend(...)` for newly drained calls**

In `start_recording()`, when `pre_calls` exists, call:

```python
await self._process_captured_calls_for_generation(session_id, pre_calls)
```

Because `start_recording()` is already async, this is a direct replacement for `session.captured_calls.extend(pre_calls)`.

In `_stop_recording_once()`, replace:

```python
session.captured_calls.extend(new_calls)
```

with:

```python
await self._process_captured_calls_for_generation(
    session_id,
    new_calls,
    model_config=model_config,
)
```

In `analyze_page()`, replace both pre-call and probed-call `session.captured_calls.extend(...)` sites with `_process_captured_calls_for_generation(...)`.

In `analyze_directed_page()`, replace each `session.captured_calls.extend(...)` for `pre_calls`, `failed_step_calls`, and `step_calls` with `_process_captured_calls_for_generation(...)`, while preserving the local `directed_calls.extend(...)` and trace `captured_call_ids`.

- [ ] **Step 5: Keep stop/analyze return behavior compatible**

In `_stop_recording_once()`, stop returning only synchronously generated tools. Return generated tools currently attached to candidates affected by `new_calls`:

```python
affected = await self._process_captured_calls_for_generation(
    session_id,
    new_calls,
    model_config=model_config,
)
tool_ids = {candidate.tool_id for candidate in affected if candidate.tool_id}
tools = [tool for tool in session.tool_definitions if tool.id in tool_ids]
self._last_recording_tools[session_id] = list(tools)
return tools
```

This keeps `stopRecording()` compatible while allowing async generation to continue.

- [ ] **Step 6: Run backend targeted tests**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py tests/test_api_monitor_capture.py -q
```

Expected: all targeted backend tests pass.

- [ ] **Step 7: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/tests/test_api_monitor_realtime_generation.py
git commit -m "feat: enqueue api monitor candidates from captured calls"
```

---

### Task 6: Candidate REST Endpoints And Retry

**Files:**
- Modify: `RpaClaw/backend/route/api_monitor.py`
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Modify: `RpaClaw/backend/tests/test_api_monitor_realtime_generation.py`

- [ ] **Step 1: Add manager retry test**

Append:

```python
def test_retry_generation_candidate_resets_failed_candidate(monkeypatch):
    manager, session_id = _manager_with_session()
    call = _call("call-1")
    manager.sessions[session_id].captured_calls.append(call)
    candidate, _ = manager._upsert_generation_candidate(session_id, call)
    candidate.status = "failed"
    candidate.error = "bad yaml"
    candidate.attempts = 2
    enqueued: list[str] = []

    monkeypatch.setattr(
        manager,
        "_enqueue_generation_candidate",
        lambda session_id_arg, candidate_id, **kwargs: enqueued.append(candidate_id),
    )

    result = manager.retry_generation_candidate(session_id, candidate.id)

    assert result.id == candidate.id
    assert result.status == "pending"
    assert result.error == ""
    assert result.retry_after is None
    assert enqueued == [candidate.id]
```

- [ ] **Step 2: Run retry test and verify failure**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py::test_retry_generation_candidate_resets_failed_candidate -q
```

Expected: fail because `retry_generation_candidate()` does not exist.

- [ ] **Step 3: Implement manager retry and list helpers**

Add:

```python
    def list_generation_candidates(self, session_id: str) -> list[ApiToolGenerationCandidate]:
        self.reconcile_generation_candidates(session_id, enqueue=False)
        return list(self._require_session(session_id).generation_candidates)

    def retry_generation_candidate(
        self,
        session_id: str,
        candidate_id: str,
        *,
        model_config: Optional[Dict] = None,
    ) -> ApiToolGenerationCandidate:
        session = self._require_session(session_id)
        candidate = next(
            (item for item in session.generation_candidates if item.id == candidate_id),
            None,
        )
        if candidate is None:
            raise ValueError("Generation candidate not found")
        candidate.status = "pending"
        candidate.error = ""
        candidate.retry_after = None
        candidate.updated_at = datetime.now()
        self._enqueue_generation_candidate(session_id, candidate.id, model_config=model_config)
        return candidate
```

- [ ] **Step 4: Add REST routes**

In `RpaClaw/backend/route/api_monitor.py`, after `list_tools()` add:

```python
@router.get("/session/{session_id}/generation-candidates")
async def list_generation_candidates(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    candidates = api_monitor_manager.list_generation_candidates(session_id)
    return {
        "status": "success",
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
    }


@router.post("/session/{session_id}/generation-candidates/{candidate_id}/retry")
async def retry_generation_candidate(
    session_id: str,
    candidate_id: str,
    current_user: User = Depends(get_current_user),
):
    session = api_monitor_manager.get_session(session_id)
    _verify_session_owner(session, current_user)
    model_config = await _resolve_user_model_config(str(current_user.id))
    try:
        candidate = api_monitor_manager.retry_generation_candidate(
            session_id,
            candidate_id,
            model_config=model_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "success", "candidate": candidate.model_dump(mode="json")}
```

- [ ] **Step 5: Run backend tests**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/route/api_monitor.py RpaClaw/backend/tests/test_api_monitor_realtime_generation.py
git commit -m "feat: expose api monitor generation candidates"
```

---

### Task 7: SSE Events For Candidates

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py`
- Modify: `RpaClaw/backend/route/api_monitor.py`

- [ ] **Step 1: Add event callback plumbing**

In `ApiMonitorSessionManager.__init__`, add:

```python
        self._analysis_event_sinks: Dict[str, Callable[[str, dict], None]] = {}
```

Add imports:

```python
from typing import Callable
```

Add methods:

```python
    def _emit_analysis_event(self, session_id: str, event: str, data: dict) -> None:
        sink = self._analysis_event_sinks.get(session_id)
        if sink:
            sink(event, data)

    def _candidate_event_payload(self, candidate: ApiToolGenerationCandidate) -> dict:
        return {
            "candidate_id": candidate.id,
            "dedup_key": candidate.dedup_key,
            "method": candidate.method,
            "url_pattern": candidate.url_pattern,
            "status": candidate.status,
            "source_call_count": len(candidate.source_call_ids),
            "tool_id": candidate.tool_id,
            "error": candidate.error,
            "retry_after": candidate.retry_after.isoformat() if candidate.retry_after else None,
        }
```

- [ ] **Step 2: Emit candidate events in helpers**

In `_process_captured_calls_for_generation()`, after each upsert:

```python
event_name = "api_candidate_created" if _created else "api_candidate_updated"
self._emit_analysis_event(session_id, event_name, self._candidate_event_payload(candidate))
```

In `_generate_tool_for_candidate()`:

- after setting `rate_limited`, emit `api_candidate_rate_limited`.
- after setting `failed`, emit `api_tool_generation_failed`.
- after generated, emit `api_tool_generated` with both candidate payload and a `tool` summary:

```python
self._emit_analysis_event(
    session_id,
    "api_tool_generated",
    {
        **self._candidate_event_payload(candidate),
        "tool": tool.model_dump(mode="json"),
    },
)
```

- [ ] **Step 3: Buffer manager events into SSE generators**

In `route/api_monitor.py`, inside `event_generator()`, create an `asyncio.Queue` and register a sink while analysis runs:

```python
import asyncio
```

Use this pattern:

```python
    async def event_generator():
        queue: asyncio.Queue[dict] = asyncio.Queue()

        def sink(event: str, data: dict) -> None:
            queue.put_nowait({
                "event": event,
                "data": json.dumps(data, ensure_ascii=False),
            })

        api_monitor_manager._analysis_event_sinks[session_id] = sink
        try:
            source = (
                api_monitor_manager.analyze_page(session_id, model_config=model_config)
                if mode_config.handler == "free"
                else api_monitor_manager.analyze_directed_page(
                    session_id,
                    instruction=instruction,
                    mode=mode_config.key,
                    business_safety=mode_config.business_safety,
                    model_config=model_config,
                )
            )
            async for event in source:
                yield event
                while not queue.empty():
                    yield await queue.get()
            while not queue.empty():
                yield await queue.get()
        finally:
            api_monitor_manager._analysis_event_sinks.pop(session_id, None)
```

This is intentionally local to active analysis SSE. Manual recording is updated by REST refresh in this implementation.

- [ ] **Step 4: Run syntax and backend targeted tests**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py tests/test_api_monitor_capture.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/route/api_monitor.py
git commit -m "feat: stream api monitor generation candidate events"
```

---

### Task 8: Frontend API Types And Helpers

**Files:**
- Modify: `RpaClaw/frontend/src/api/apiMonitor.ts`
- Modify: `RpaClaw/frontend/src/api/apiMonitor.test.ts`

- [ ] **Step 1: Add failing API tests**

Append to `RpaClaw/frontend/src/api/apiMonitor.test.ts`:

```typescript
describe('apiMonitor generation candidates', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  it('lists generation candidates', async () => {
    const { apiClient } = await import('@/api/client')
    const candidate = {
      id: 'candidate-1',
      session_id: 'session-1',
      dedup_key: 'GET /api/orders',
      method: 'GET',
      url_pattern: '/api/orders',
      source_call_ids: ['call-1'],
      sample_call_ids: ['call-1'],
      status: 'running',
      tool_id: null,
      error: '',
      retry_after: null,
      attempts: 0,
      capture_dom_context: {},
      capture_page_url: 'https://example.com/app',
      capture_title: 'Orders',
      capture_dom_digest: 'digest-1',
      created_at: '2026-04-30T00:00:00',
      updated_at: '2026-04-30T00:00:00',
    }
    vi.mocked(apiClient.get).mockResolvedValue({ data: { candidates: [candidate] } })
    const { listGenerationCandidates } = await import('./apiMonitor')

    await expect(listGenerationCandidates('session-1')).resolves.toEqual([candidate])
    expect(apiClient.get).toHaveBeenCalledWith('/api-monitor/session/session-1/generation-candidates')
  })

  it('retries generation candidates', async () => {
    const { apiClient } = await import('@/api/client')
    vi.mocked(apiClient.post).mockResolvedValue({ data: { candidate: { id: 'candidate-1' } } })
    const { retryGenerationCandidate } = await import('./apiMonitor')

    await expect(retryGenerationCandidate('session-1', 'candidate-1')).resolves.toEqual({ id: 'candidate-1' })
    expect(apiClient.post).toHaveBeenCalledWith(
      '/api-monitor/session/session-1/generation-candidates/candidate-1/retry',
    )
  })
})
```

- [ ] **Step 2: Run API tests and verify failure**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/api/apiMonitor.test.ts
```

Expected: fail because helpers do not exist.

- [ ] **Step 3: Add candidate types and helpers**

In `RpaClaw/frontend/src/api/apiMonitor.ts`, add:

```typescript
export type ApiToolGenerationStatus =
  | 'pending'
  | 'running'
  | 'generated'
  | 'failed'
  | 'rate_limited'
  | 'stale'

export interface ApiToolGenerationCandidate {
  id: string
  session_id: string
  dedup_key: string
  method: string
  url_pattern: string
  source_call_ids: string[]
  sample_call_ids: string[]
  status: ApiToolGenerationStatus
  tool_id?: string | null
  error: string
  retry_after?: string | null
  attempts: number
  capture_dom_context: Record<string, unknown>
  capture_page_url: string
  capture_title: string
  capture_dom_digest: string
  created_at: string
  updated_at: string
}
```

Add to `ApiToolDefinition`:

```typescript
  generation_candidate_id?: string | null
```

Add to `ApiMonitorSession`:

```typescript
  generation_candidates: ApiToolGenerationCandidate[]
```

Add helpers:

```typescript
export async function listGenerationCandidates(sessionId: string): Promise<ApiToolGenerationCandidate[]> {
  const response = await apiClient.get(`/api-monitor/session/${sessionId}/generation-candidates`)
  return response.data.candidates
}

export async function retryGenerationCandidate(
  sessionId: string,
  candidateId: string,
): Promise<ApiToolGenerationCandidate> {
  const response = await apiClient.post(
    `/api-monitor/session/${sessionId}/generation-candidates/${candidateId}/retry`,
  )
  return response.data.candidate
}
```

- [ ] **Step 4: Run API tests**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/api/apiMonitor.test.ts
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/frontend/src/api/apiMonitor.ts RpaClaw/frontend/src/api/apiMonitor.test.ts
git commit -m "feat: add api monitor candidate client"
```

---

### Task 9: Frontend Candidate Placeholder UI

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue`

- [ ] **Step 1: Import candidate APIs and types**

Update imports from `@/api/apiMonitor` to include:

```typescript
  listGenerationCandidates,
  retryGenerationCandidate,
  type ApiToolGenerationCandidate,
```

- [ ] **Step 2: Add candidate state and derived lists**

Near existing `tools` refs, add:

```typescript
const generationCandidates = ref<ApiToolGenerationCandidate[]>([]);
const visibleGenerationCandidates = computed(() =>
  generationCandidates.value.filter((candidate) => candidate.status !== 'generated' || !candidate.tool_id),
);
const detectedItemCount = computed(() => tools.value.length + visibleGenerationCandidates.value.length);
```

Add helpers:

```typescript
const upsertGenerationCandidate = (candidate: ApiToolGenerationCandidate) => {
  const idx = generationCandidates.value.findIndex((item) => item.id === candidate.id);
  if (idx >= 0) {
    generationCandidates.value[idx] = candidate;
  } else {
    generationCandidates.value.unshift(candidate);
  }
};

const getCandidateStatusLabel = (status: ApiToolGenerationCandidate['status']) => {
  if (status === 'pending') return '等待生成';
  if (status === 'running') return '生成中';
  if (status === 'rate_limited') return '限流重试中';
  if (status === 'failed') return '生成失败';
  if (status === 'stale') return '等待更新';
  return '已生成';
};

const getCandidateStatusClass = (status: ApiToolGenerationCandidate['status']) => {
  if (status === 'running' || status === 'pending') return 'border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-300';
  if (status === 'rate_limited') return 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300';
  if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300';
  return 'border-slate-200 bg-slate-50 text-slate-600 dark:border-white/10 dark:bg-white/5 dark:text-slate-300';
};
```

- [ ] **Step 3: Load candidates with existing session data**

Where existing tools load during session initialization, add:

```typescript
generationCandidates.value = await listGenerationCandidates(sessionId.value);
```

When refreshing after stop/analyze complete, call both:

```typescript
const [nextTools, nextCandidates] = await Promise.all([
  listTools(sessionId.value),
  listGenerationCandidates(sessionId.value),
]);
tools.value = nextTools;
generationCandidates.value = nextCandidates;
```

- [ ] **Step 4: Handle SSE candidate events**

In the `switch (event)` inside `analyzeSession()` handling, add:

```typescript
      case 'api_candidate_created':
      case 'api_candidate_updated':
      case 'api_candidate_rate_limited':
      case 'api_tool_generation_failed':
        upsertGenerationCandidate({
          id: data.candidate_id,
          session_id: sessionId.value,
          dedup_key: data.dedup_key,
          method: data.method,
          url_pattern: data.url_pattern,
          source_call_ids: [],
          sample_call_ids: [],
          status: data.status,
          tool_id: data.tool_id,
          error: data.error || '',
          retry_after: data.retry_after,
          attempts: 0,
          capture_dom_context: {},
          capture_page_url: '',
          capture_title: '',
          capture_dom_digest: '',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        });
        addLog('BUILD', `${data.method} ${data.url_pattern} ${getCandidateStatusLabel(data.status)}`);
        break;
      case 'api_tool_generated':
        if (data.tool) {
          const idx = tools.value.findIndex((tool) => tool.id === data.tool.id);
          if (idx >= 0) tools.value[idx] = data.tool;
          else tools.value.unshift(data.tool);
        }
        generationCandidates.value = generationCandidates.value.filter((item) => item.id !== data.candidate_id);
        addLog('BUILD', `工具已生成: ${data.tool?.name || data.url_pattern}`);
        break;
```

- [ ] **Step 5: Add retry handler**

Add:

```typescript
const handleRetryCandidate = async (candidate: ApiToolGenerationCandidate) => {
  if (!sessionId.value) return;
  try {
    const updated = await retryGenerationCandidate(sessionId.value, candidate.id);
    upsertGenerationCandidate(updated);
    addLog('BUILD', `已重新排队: ${candidate.method} ${candidate.url_pattern}`);
  } catch (err: any) {
    addLog('ERROR', `重试生成失败: ${err.message}`);
  }
};
```

- [ ] **Step 6: Render placeholder cards**

Change the tool count badge from `tools.length` to `detectedItemCount`.

Change the empty state condition from:

```vue
<div v-if="tools.length === 0" class="h-full flex flex-col items-center justify-center text-[var(--text-tertiary)]">
```

to:

```vue
<div v-if="detectedItemCount === 0" class="h-full flex flex-col items-center justify-center text-[var(--text-tertiary)]">
```

Before grouped tool cards, add:

```vue
<div v-if="visibleGenerationCandidates.length" class="space-y-2">
  <div class="flex items-center justify-between px-1 text-[11px] font-bold text-[var(--text-tertiary)]">
    <span>生成中</span>
    <span>{{ visibleGenerationCandidates.length }}</span>
  </div>
  <div
    v-for="candidate in visibleGenerationCandidates"
    :key="candidate.id"
    class="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 shadow-sm dark:border-white/10 dark:bg-white/[0.04]"
  >
    <div class="flex items-center gap-3">
      <span class="text-[10px] font-bold px-2 py-0.5 rounded-md" :class="getMethodClass(candidate.method)">
        {{ candidate.method }}
      </span>
      <span class="min-w-0 flex-1 truncate font-mono text-[11px] text-[var(--text-primary)]">
        {{ candidate.url_pattern }}
      </span>
      <span class="shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-bold" :class="getCandidateStatusClass(candidate.status)">
        {{ getCandidateStatusLabel(candidate.status) }}
      </span>
    </div>
    <div class="mt-2 flex items-center justify-between gap-3 text-[10px] text-[var(--text-tertiary)]">
      <span>样本 {{ candidate.source_call_ids?.length || 0 }}</span>
      <span v-if="candidate.retry_after">下次重试 {{ new Date(candidate.retry_after).toLocaleTimeString() }}</span>
      <span v-else-if="candidate.error" class="truncate text-red-500">{{ candidate.error }}</span>
      <button
        v-if="candidate.status === 'failed' || candidate.status === 'rate_limited'"
        class="rounded-lg border border-slate-200 px-2 py-1 font-bold text-[var(--text-secondary)] transition hover:bg-slate-100 dark:border-white/10 dark:hover:bg-white/10"
        @click="handleRetryCandidate(candidate)"
      >
        重试
      </button>
    </div>
  </div>
</div>
```

- [ ] **Step 7: Run frontend typecheck/tests**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/api/apiMonitor.test.ts
npm run build
```

Expected: API tests pass and production build succeeds.

- [ ] **Step 8: Commit**

```bash
git add RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue
git commit -m "feat: show realtime api monitor generation candidates"
```

---

### Task 10: Backend Regression Sweep

**Files:**
- No new files expected unless a failing test requires a scoped fix.

- [ ] **Step 1: Run API Monitor backend tests**

Run:

```bash
cd RpaClaw/backend
uv run pytest \
  tests/test_api_monitor_capture.py \
  tests/test_api_monitor_confidence.py \
  tests/test_api_monitor_analysis_modes.py \
  tests/test_api_monitor_publish_mcp.py \
  tests/test_api_monitor_realtime_generation.py \
  -q
```

Expected: all listed tests pass.

- [ ] **Step 2: Fix regressions without changing architecture**

If a test fails because existing call sites still expect synchronous tool generation, update the specific call site to read current `session.tool_definitions` plus `generation_candidates`; do not reintroduce end-of-flow full synchronous generation.

Example acceptable fix in route response:

```python
return {
    "status": "success",
    "tools": [tool.model_dump(mode="json") for tool in tools],
    "generation_candidates": [
        candidate.model_dump(mode="json")
        for candidate in api_monitor_manager.list_generation_candidates(session_id)
    ],
}
```

- [ ] **Step 3: Run full backend tests if targeted tests pass**

Run:

```bash
cd RpaClaw/backend
uv run pytest -q
```

Expected: pass. If failures appear, fix API Monitor regressions before continuing.

- [ ] **Step 4: Commit regression fixes**

If files changed:

```bash
git add RpaClaw/backend
git commit -m "test: cover api monitor realtime generation"
```

If no files changed, do not create an empty commit.

---

### Task 11: Frontend Regression Sweep

**Files:**
- No new files expected unless a failing test requires a scoped fix.

- [ ] **Step 1: Run frontend API tests**

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/api/apiMonitor.test.ts
```

Expected: pass.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd RpaClaw/frontend
npm run build
```

Expected: pass.

- [ ] **Step 3: Inspect API Monitor page manually in local dev if available**

Run dev server:

```bash
cd RpaClaw/frontend
npm run dev
```

Expected: Vite prints a local URL. Open API Monitor through the app and confirm the tools panel shows placeholder styling without text overflow. Stop the server after checking.

- [ ] **Step 4: Commit frontend fixes**

If files changed:

```bash
git add RpaClaw/frontend
git commit -m "test: verify api monitor candidate ui"
```

If no files changed, do not create an empty commit.

---

### Task 12: Final Verification And Handoff

**Files:**
- Review all changed files.

- [ ] **Step 1: Check git status**

Run:

```bash
git status --short
```

Expected: only intentional files are modified. Do not stage unrelated files such as existing work in `docs/superpowers/plans/2026-04-30-windows-system-truststore.md`.

- [ ] **Step 2: Run final targeted verification**

Run:

```bash
cd RpaClaw/backend
uv run pytest tests/test_api_monitor_realtime_generation.py tests/test_api_monitor_capture.py -q
```

Run:

```bash
cd RpaClaw/frontend
npm run test -- src/api/apiMonitor.test.ts
```

Expected: both commands pass.

- [ ] **Step 3: Review diff for architecture boundaries**

Run:

```bash
git diff --stat
git diff -- RpaClaw/backend/rpa/api_monitor/manager.py RpaClaw/backend/rpa/api_monitor/models.py RpaClaw/backend/route/api_monitor.py
```

Confirm:

- Captured calls are appended before candidate generation work.
- LLM calls happen only in background generation methods.
- `selected` is still owned by existing confidence scoring.
- Publish code still filters generated `tool_definitions`, not raw candidates.

- [ ] **Step 4: Commit final adjustments**

If final adjustments were needed:

```bash
git add RpaClaw/backend RpaClaw/frontend
git commit -m "fix: stabilize api monitor realtime generation"
```

If no final adjustments were needed, do not create an empty commit.

---

## Self-Review Notes

- Spec coverage: candidate model, realtime placeholders, worker success path, rate-limit/failure recovery, reconcile, REST/SSE events, frontend placeholders, and adoption via existing `selected` are all mapped to tasks.
- Scope check: this plan stays within API Monitor realtime generation. It does not redesign MCP publish auth, token flow, RPA trace compilation, or persistent queues.
- Type consistency: backend status values and frontend `ApiToolGenerationStatus` use the same string union: `pending`, `running`, `generated`, `failed`, `rate_limited`, `stale`.
- Verification: backend and frontend targeted tests are included before final handoff.
