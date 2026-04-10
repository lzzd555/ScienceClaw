# RPA Recorder Playwright-CRX Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the Python-owned RPA recorder with `playwright-crx` semantics so fast interactions, locator selection, and generated playback code become deterministic and replayable.

**Architecture:** Keep the current FastAPI and Vue product shell, but refactor the injected recorder pipeline in `manager.py`, extend the step model with recorder-order metadata, upgrade locator candidate generation to strict DOM-validated selection with `nth` support, and update the Python generator to preserve navigation and ambiguity semantics. Lock the behavior down with backend regression tests before and during implementation.

**Tech Stack:** FastAPI, Playwright async API, Python unittest, Vue 3 + TypeScript

---

### Task 1: Lock Down Recorder Ordering Regressions

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_manager.py`
- Modify: `RpaClaw/backend/rpa/manager.py`

- [ ] **Step 1: Write failing tests for recorder-side ordering and immediate input capture**

Add tests that model the current broken cases directly in `RpaClaw/backend/tests/test_rpa_manager.py`:

```python
    async def test_handle_event_orders_steps_by_sequence(self):
        session = RPASession(id="s1", user_id="u1", sandbox_session_id="sbx")
        self.manager.sessions["s1"] = session

        fill_evt = {
            "action": "fill",
            "value": "hello",
            "timestamp": 2000,
            "sequence": 2,
            "locator": {"method": "role", "role": "textbox", "name": "Search"},
            "tab_id": "tab-1",
        }
        press_evt = {
            "action": "press",
            "value": "Enter",
            "timestamp": 1500,
            "sequence": 1,
            "locator": {"method": "role", "role": "textbox", "name": "Search"},
            "tab_id": "tab-1",
        }

        await self.manager._handle_event("s1", fill_evt)
        await self.manager._handle_event("s1", press_evt)

        self.assertEqual([step.action for step in session.steps], ["press", "fill"])
```

```python
    async def test_enter_navigation_upgrades_press_not_detached_goto(self):
        session = RPASession(id="s1", user_id="u1", sandbox_session_id="sbx")
        self.manager.sessions["s1"] = session

        await self.manager._handle_event("s1", {
            "action": "press",
            "value": "Enter",
            "timestamp": 1000,
            "sequence": 1,
            "locator": {"method": "role", "role": "textbox", "name": "Search"},
            "tab_id": "tab-1",
            "url": "https://example.com/search",
        })
        await self.manager._handle_event("s1", {
            "action": "navigate",
            "timestamp": 1100,
            "sequence": 2,
            "tab_id": "tab-1",
            "url": "https://example.com/results?q=hello",
        })

        self.assertEqual(session.steps[0].action, "navigate_press")
        self.assertEqual(len(session.steps), 1)
```

- [ ] **Step 2: Run the targeted manager tests and verify they fail for the current implementation**

Run:

```powershell
python -m unittest RpaClaw.backend.tests.test_rpa_manager -v
```

Expected:

- the new ordering test fails because steps are appended in arrival order
- the new navigation upgrade test fails because only `click` is upgraded to navigation-aware action

- [ ] **Step 3: Implement step ordering by recorder `sequence` and add `navigate_press` handling**

Update `RpaClaw/backend/rpa/manager.py` so `RPAStep` carries `sequence`, the normalized step payload includes that field, and insertion is ordered by `sequence` first and timestamp second.

Implementation target:

```python
class RPAStep(BaseModel):
    id: str
    action: str
    target: Optional[str] = None
    sequence: Optional[int] = None
    frame_path: List[str] = Field(default_factory=list)
    ...
```

```python
    def _insert_step_ordered(self, session: RPASession, step: RPAStep) -> None:
        if step.sequence is None or not session.steps:
            session.steps.append(step)
            return

        insert_at = len(session.steps)
        for index, existing in enumerate(session.steps):
            existing_seq = existing.sequence if existing.sequence is not None else float("inf")
            if existing_seq > step.sequence:
                insert_at = index
                break
        session.steps.insert(insert_at, step)
```

```python
        step_data = {
            "action": evt.get("action", "unknown"),
            "sequence": evt.get("sequence"),
            ...
        }
```

```python
                if last_step.action in ("click", "press", "fill"):
                    ...
                    if last_step.action == "click":
                        last_step.action = "navigate_click"
                        ...
                        return
                    if last_step.action == "press":
                        last_step.action = "navigate_press"
                        last_step.url = evt.get("url", last_step.url)
                        await self._broadcast_step(session_id, last_step)
                        return
```

- [ ] **Step 4: Re-run the targeted manager tests until the new ordering and navigation-upgrade tests pass**

Run:

```powershell
python -m unittest RpaClaw.backend.tests.test_rpa_manager -v
```

Expected:

- the new tests pass
- existing manager tests remain green

- [ ] **Step 5: Commit the ordering regression slice**

Run:

```powershell
git add RpaClaw/backend/tests/test_rpa_manager.py RpaClaw/backend/rpa/manager.py
git commit -m "fix: stabilize recorder event ordering"
```

### Task 2: Replace Debounced Input Recording With Playwright-Like Immediate Action Emission

**Files:**
- Modify: `RpaClaw/backend/rpa/manager.py`
- Extend: `RpaClaw/backend/tests/test_rpa_manager.py`

- [ ] **Step 1: Write failing tests for immediate fill capture semantics**

Add a focused unit test that verifies the normalized event pipeline no longer depends on delayed fill flushes:

```python
    async def test_handle_event_keeps_fill_before_press_for_same_target(self):
        session = RPASession(id="s1", user_id="u1", sandbox_session_id="sbx")
        self.manager.sessions["s1"] = session

        locator = {"method": "role", "role": "textbox", "name": "Search"}
        await self.manager._handle_event("s1", {
            "action": "fill",
            "value": "playwright",
            "timestamp": 1000,
            "sequence": 10,
            "locator": locator,
            "tab_id": "tab-1",
        })
        await self.manager._handle_event("s1", {
            "action": "press",
            "value": "Enter",
            "timestamp": 1001,
            "sequence": 11,
            "locator": locator,
            "tab_id": "tab-1",
        })

        self.assertEqual(
            [(step.action, step.value) for step in session.steps],
            [("fill", "playwright"), ("press", "Enter")],
        )
```

- [ ] **Step 2: Run the focused manager test and verify the current browser-script assumptions are still inadequate**

Run:

```powershell
python -m unittest RpaClaw.backend.tests.test_rpa_manager.RPASessionManagerTests.test_handle_event_keeps_fill_before_press_for_same_target -v
```

Expected:

- the test exposes that the backend can preserve order only if the browser emits actions immediately and consistently

- [ ] **Step 3: Refactor the injected `CAPTURE_JS` to use recorder state instead of `1500ms` debounced input**

Update `RpaClaw/backend/rpa/manager.py` inside `CAPTURE_JS`:

- add a recorder sequence counter
- add active-target helpers
- remove the `setTimeout(..., 1500)` input debounce
- record trusted `fill` immediately on `input`
- keep `press` generation in `keydown`
- stop blanket same-locator click suppression

Implementation target:

```javascript
    var _sequence = 0;
    var _activeElement = null;

    function nextSequence() {
        _sequence += 1;
        return _sequence;
    }

    function rememberActive(el) {
        _activeElement = retarget(el || document.activeElement || el);
        return _activeElement;
    }

    function actionTarget(eventTarget) {
        return rememberActive(eventTarget || _activeElement || document.activeElement);
    }

    function emit(evt) {
        evt.sequence = nextSequence();
        evt.timestamp = Date.now();
        evt.url = location.href;
        evt.frame_path = getFramePath();
        window.__rpa_emit(JSON.stringify(evt));
    }
```

```javascript
    document.addEventListener('focus', function(e) {
        if (!e.isTrusted || window.__rpa_paused) return;
        rememberActive(e.target);
    }, true);
```

```javascript
    document.addEventListener('input', function(e) {
        if (!e.isTrusted || window.__rpa_paused) return;
        var el = actionTarget(e.target);
        if (!el) return;
        var isPassword = (el.type === 'password');
        var locatorBundle = buildLocatorBundle(el);
        emit({
            action: 'fill',
            locator: locatorBundle.primary,
            locator_candidates: locatorBundle.candidates,
            validation: locatorBundle.validation,
            element_snapshot: buildElementSnapshot(el),
            value: isPassword ? '{{credential}}' : (el.isContentEditable ? el.innerText : (el.value || '')),
            tag: el.tagName,
            sensitive: isPassword
        });
    }, true);
```

- [ ] **Step 4: Re-run the manager tests and verify immediate fill ordering remains green**

Run:

```powershell
python -m unittest RpaClaw.backend.tests.test_rpa_manager -v
```

Expected:

- the new fill-before-press test passes
- no existing manager regression appears

- [ ] **Step 5: Commit the recorder-capture slice**

Run:

```powershell
git add RpaClaw/backend/rpa/manager.py RpaClaw/backend/tests/test_rpa_manager.py
git commit -m "fix: align recorder input capture with playwright"
```

### Task 3: Replace Relaxed Locator Selection With Strict Candidate Validation

**Files:**
- Modify: `RpaClaw/backend/rpa/manager.py`
- Modify: `RpaClaw/backend/rpa/action_models.py`
- Modify: `RpaClaw/backend/tests/test_rpa_manager.py`

- [ ] **Step 1: Write failing tests for ambiguous locator selection**

Add tests that model repeated elements and require a strict disambiguated locator:

```python
    def test_build_locator_bundle_marks_ambiguous_candidates_honestly(self):
        script = CAPTURE_JS
        self.assertIn("strict_match_count", script)
        self.assertIn("nth", script)
```

```python
    async def test_select_step_locator_candidate_can_choose_nth_locator(self):
        session = RPASession(id="s1", user_id="u1", sandbox_session_id="sbx")
        step = RPAStep(
            id="step-1",
            action="click",
            target='{"method":"role","role":"button","name":"Save"}',
            locator_candidates=[
                {
                    "kind": "role",
                    "score": 100,
                    "strict_match_count": 2,
                    "visible_match_count": 2,
                    "selected": False,
                    "reason": "strict matches = 2",
                    "locator": {"method": "role", "role": "button", "name": "Save"},
                },
                {
                    "kind": "nth",
                    "score": 120,
                    "strict_match_count": 1,
                    "visible_match_count": 1,
                    "selected": True,
                    "reason": "strict unique match with nth",
                    "locator": {"method": "nth", "base": {"method": "role", "role": "button", "name": "Save"}, "index": 1},
                },
            ],
        )
        session.steps.append(step)
        self.manager.sessions["s1"] = session

        updated = await self.manager.select_step_locator_candidate("s1", 0, 1)
        self.assertEqual(json.loads(updated.target)["method"], "nth")
```

- [ ] **Step 2: Run the locator-focused manager tests and verify they fail with the current candidate model**

Run:

```powershell
python -m unittest RpaClaw.backend.tests.test_rpa_manager -v
```

Expected:

- tests fail because there is no `nth` candidate shape and the current code assumes fallback strictness

- [ ] **Step 3: Refactor candidate generation and data models to support strict validation and `nth`**

Update `RpaClaw/backend/rpa/action_models.py` and the browser-side candidate builder in `manager.py`.

Implementation target:

```python
class LocatorCandidate(BaseModel):
    kind: str
    score: int
    strict_match_count: int
    visible_match_count: int
    selected: bool = False
    reason: str = ""
    locator: Dict[str, Any] = Field(default_factory=dict)
```

```javascript
    function withNth(locator, index) {
        return { method: 'nth', base: locator, index: index };
    }

    function chooseStrictCandidate(targetEl, candidates) {
        for (var i = 0; i < candidates.length; i++) {
            var c = candidates[i];
            var matches = resolveCandidateMatches(c);
            var index = matches.indexOf(targetEl);
            if (index === -1) continue;
            if (matches.length === 1) {
                return { locator: formatCandidate(c), strict_match_count: 1, reason: 'strict unique match' };
            }
            if (matches.length <= 5) {
                return { locator: withNth(formatCandidate(c), index), strict_match_count: 1, reason: 'strict unique match with nth' };
            }
        }
        return null;
    }
```

```javascript
    function buildLocatorBundle(el) {
        var strictChoice = chooseStrictCandidate(el, candidates);
        ...
        validation: {
            status: selectedPayload.strict_match_count === 1 ? 'ok' : 'ambiguous',
            details: selectedPayload.reason
        }
    }
```

- [ ] **Step 4: Re-run the manager tests and verify locator candidates now report ambiguity truthfully**

Run:

```powershell
python -m unittest RpaClaw.backend.tests.test_rpa_manager -v
```

Expected:

- the new locator tests pass
- selected fallback candidates no longer pretend to have strict uniqueness when they do not

- [ ] **Step 5: Commit the locator strictness slice**

Run:

```powershell
git add RpaClaw/backend/rpa/manager.py RpaClaw/backend/rpa/action_models.py RpaClaw/backend/tests/test_rpa_manager.py
git commit -m "fix: make recorder locator selection strict"
```

### Task 4: Teach The Generator To Preserve `nth` And Navigation-Aware `press`

**Files:**
- Modify: `RpaClaw/backend/rpa/generator.py`
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Write failing generator tests for `navigate_press` and `nth` locators**

Add tests like these in `RpaClaw/backend/tests/test_rpa_generator.py`:

```python
    def test_generate_script_wraps_navigate_press_in_expect_navigation(self):
        script = self.generator.generate_script(
            [{
                "action": "navigate_press",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "Search"}),
                "value": "Enter",
                "url": "https://example.com/results",
                "tab_id": "tab-1",
            }],
            {},
            is_local=True,
        )
        self.assertIn("expect_navigation", script)
        self.assertIn('.press("Enter")', script)
```

```python
    def test_build_locator_supports_nth_locator_payload(self):
        locator = self.generator._build_locator(json.dumps({
            "method": "nth",
            "base": {"method": "role", "role": "button", "name": "Save"},
            "index": 1,
        }))
        self.assertEqual(locator, 'page.get_by_role("button", name="Save", exact=True).nth(1)')
```

- [ ] **Step 2: Run the generator tests and verify they fail before code changes**

Run:

```powershell
python -m unittest RpaClaw.backend.tests.test_rpa_generator -v
```

Expected:

- tests fail because `navigate_press` and `nth` are not supported

- [ ] **Step 3: Implement `nth` locator generation and navigation-aware press emission**

Update `RpaClaw/backend/rpa/generator.py`:

```python
        if method == "nth":
            base = loc.get("base", {"method": "css", "value": "body"})
            index = int(loc.get("index", 0))
            base_loc = self._build_locator(json.dumps(base) if isinstance(base, dict) else str(base))
            return f"{base_loc}.nth({index})"
```

```python
            if action == "navigate_press":
                lines.append(f"    async with current_page.expect_navigation(wait_until='domcontentloaded', timeout={RPA_NAVIGATION_TIMEOUT_MS}):")
                lines.append(f'        await {locator}.press("{value}")')
```

- [ ] **Step 4: Re-run the generator tests until the new semantics pass**

Run:

```powershell
python -m unittest RpaClaw.backend.tests.test_rpa_generator -v
```

Expected:

- the new `navigate_press` and `nth` tests pass
- existing generator tests remain green

- [ ] **Step 5: Commit the generator slice**

Run:

```powershell
git add RpaClaw/backend/rpa/generator.py RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "fix: preserve recorder press and nth semantics"
```

### Task 5: Update Frontend Locator Rendering For The New Candidate Shapes

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- Modify: `RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue`

- [ ] **Step 1: Extend locator-formatting helpers to render `nth` and honest validation states**

Update both pages' formatting helpers:

```ts
if (locator.method === 'nth') {
  return `${formatLocator(locator.base)} >> nth=${locator.index}`;
}
```

```ts
const getValidationLabel = (status?: string) => {
  if (status === 'ok') return '严格匹配';
  if (status === 'ambiguous') return '存在歧义';
  return status || '';
};
```

- [ ] **Step 2: Update candidate summaries so the selected candidate reflects actual strictness**

Use the backend payload directly instead of assuming the selected candidate is always safe:

```ts
const getCandidateSummary = (step: StepItem) => {
  const selected = getSelectedCandidate(step);
  if (!selected) return '';
  const total = step.locator_candidates?.length || 0;
  const strict = selected.strict_match_count === 1 ? 'strict' : `matches=${selected.strict_match_count}`;
  return `${selected.kind} · ${strict} · ${total} candidates`;
};
```

- [ ] **Step 3: Run a frontend syntax check**

Run:

```powershell
npx vue-tsc --noEmit
```

Expected:

- no new type errors from the locator-shape updates

- [ ] **Step 4: Commit the frontend rendering slice**

Run:

```powershell
git add RpaClaw/frontend/src/pages/rpa/RecorderPage.vue RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue
git commit -m "fix: render strict recorder locator states"
```

### Task 6: End-To-End Verification, Final Commit Cleanup, And Push

**Files:**
- Modify as needed from Tasks 1-5 only

- [ ] **Step 1: Run the full targeted backend verification suite**

Run:

```powershell
python -m unittest RpaClaw.backend.tests.test_rpa_manager RpaClaw.backend.tests.test_rpa_generator -v
```

Expected:

- all targeted backend tests pass

- [ ] **Step 2: Run the frontend syntax verification again after any fixes**

Run:

```powershell
npx vue-tsc --noEmit
```

Expected:

- clean type-check output

- [ ] **Step 3: Review the worktree status and keep only intended recorder-alignment changes**

Run:

```powershell
git status --short
```

Expected:

- only recorder-alignment files are modified or committed

- [ ] **Step 4: Create the final integration commit if any verification fixes remain uncommitted**

Run:

```powershell
git add RpaClaw/backend/rpa/manager.py RpaClaw/backend/rpa/action_models.py RpaClaw/backend/rpa/generator.py RpaClaw/backend/tests/test_rpa_manager.py RpaClaw/backend/tests/test_rpa_generator.py RpaClaw/frontend/src/pages/rpa/RecorderPage.vue RpaClaw/frontend/src/pages/rpa/ConfigurePage.vue
git commit -m "feat: align recorder behavior with playwright crx"
```

Expected:

- git reports a clean worktree after commit

- [ ] **Step 5: Push the branch to the remote requested by the user**

Run:

```powershell
git push -u origin feature-recorder
```

Expected:

- remote branch `origin/feature-recorder` is created or updated successfully

### Self-Review

- Spec coverage: this plan covers recorder event ordering, immediate input capture, strict locator generation, generator semantics, frontend candidate rendering, and final push steps.
- Placeholder scan: no `TODO`, `TBD`, or implicit “handle this somehow” items remain.
- Type consistency: the plan consistently uses `sequence`, `navigate_press`, and `nth` as the new cross-layer concepts.
