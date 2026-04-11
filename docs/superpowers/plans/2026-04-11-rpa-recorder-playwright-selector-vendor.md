# RPA Recorder Playwright Selector Vendor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make recorder locator generation and candidate validation follow Playwright upstream selector semantics.

**Architecture:** Vendor a minimal Playwright injected selector subset into the backend and call it from the recorder injection script. Preserve the current Python-side locator JSON contract, but source candidate truth from Playwright-backed selector generation and matching results instead of custom approximations.

**Tech Stack:** Python 3.13, FastAPI backend, Playwright async API, injected browser JavaScript, unittest

---

### Task 1: Lock the regression with tests

**Files:**
- Modify: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_manager.py`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_manager.py`

- [ ] **Step 1: Write the failing tests**

Add tests that assert:
- a candidate with `playwright_locator='get_by_text("操作", exact=True)'` can be promoted over an invalid selected role candidate
- parsing and normalization preserve a `text` locator as the selected target when it is the best strict candidate

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager.RPASessionManagerTabTests`
Expected: FAIL because the current recorder logic still favors the stale selected role candidate or lacks the new adapter-backed behavior.

- [ ] **Step 3: Write minimal implementation**

Implement only enough recorder-side normalization and selector translation changes to make the new tests pass.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager.RPASessionManagerTabTests`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/tests/test_rpa_manager.py RpaClaw/backend/rpa/manager.py
git commit -m "fix: align recorder locator normalization with playwright semantics"
```

### Task 2: Vendor the Playwright selector subset

**Files:**
- Create: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/vendor/playwright_selector_adapter.js`
- Modify: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/manager.py`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_manager.py`

- [ ] **Step 1: Write the failing test**

Add a focused test that inspects `CAPTURE_JS` or the recorder adapter contract to confirm the injected bundle exposes the selector adapter hook used by the recorder.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager`
Expected: FAIL because no vendor adapter is present yet.

- [ ] **Step 3: Write minimal implementation**

Add the vendored adapter file and wire `manager.py` to inject it before recorder logic.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/vendor/playwright_selector_adapter.js RpaClaw/backend/rpa/manager.py RpaClaw/backend/tests/test_rpa_manager.py
git commit -m "fix: vendor playwright selector adapter for recorder"
```

### Task 3: Replace recorder-local candidate truth with adapter-backed results

**Files:**
- Modify: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/manager.py`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_manager.py`

- [ ] **Step 1: Write the failing test**

Add a regression test covering the anchor-without-href case so that the selected candidate resolves to `{"method": "text", "value": "操作"}` instead of a role locator.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager`
Expected: FAIL because the legacy role mapping still produces `role=link`.

- [ ] **Step 3: Write minimal implementation**

Remove recorder-local final-truth checks from the injection path and translate adapter-produced selectors into recorder candidates and validation fields.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/manager.py RpaClaw/backend/tests/test_rpa_manager.py
git commit -m "fix: use playwright-backed recorder candidate validation"
```

### Task 4: Run focused verification

**Files:**
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_manager.py`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Run recorder manager tests**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager`
Expected: PASS

- [ ] **Step 2: Run generator tests**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_generator`
Expected: PASS

- [ ] **Step 3: Review for scope creep**

Confirm no unrelated RPA execution or frontend behavior changed.

- [ ] **Step 4: Commit final verification state**

```bash
git add D:/code/MyScienceClaw/docs/superpowers/specs/2026-04-11-rpa-recorder-playwright-selector-vendor-design.md D:/code/MyScienceClaw/docs/superpowers/plans/2026-04-11-rpa-recorder-playwright-selector-vendor.md
git commit -m "chore: document playwright-aligned recorder selector plan"
```
