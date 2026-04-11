# RPA Recorder Playwright Action Vendor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make recorder action classification follow Playwright upstream semantics so one user gesture records one logical step.

**Architecture:** Vendor a focused Playwright recorder action runtime into the backend RPA module, keep the existing selector runtime, and reduce the current capture script to an adapter that serializes normalized logical actions into the existing backend payload.

**Tech Stack:** Python 3.13, FastAPI backend, Playwright async API, injected browser JavaScript, unittest

---

### Task 1: Lock the new behavior with failing tests

**Files:**
- Modify: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_manager.py`
- Modify: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_generator.py`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_manager.py`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Write the failing tests**

Add tests that assert:
- the injected recorder setup includes a dedicated action runtime file before the capture bridge
- manager descriptions support `check` and `uncheck`
- generator emits `.check()` and `.uncheck()` for those actions

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager RpaClaw.backend.tests.test_rpa_generator`
Expected: FAIL because the current recorder has no vendored action runtime and no `check` / `uncheck` generation path.

- [ ] **Step 3: Write minimal implementation**

Add only the smallest code needed to support the new runtime contract and new action names.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager RpaClaw.backend.tests.test_rpa_generator`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/tests/test_rpa_manager.py RpaClaw/backend/tests/test_rpa_generator.py RpaClaw/backend/rpa/manager.py RpaClaw/backend/rpa/generator.py
git commit -m "fix: add recorder logical action coverage"
```

### Task 2: Vendor the Playwright action-classification runtime

**Files:**
- Create: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/vendor/playwright_recorder_actions.js`
- Modify: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/manager.py`
- Modify: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/vendor/playwright_recorder_capture.js`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_manager.py`

- [ ] **Step 1: Write the failing test**

Add a focused manager test that checks the context init scripts include the new action runtime path before the capture script.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager.RPASessionManagerTabTests`
Expected: FAIL because only the selector runtime and capture script are injected today.

- [ ] **Step 3: Write minimal implementation**

Add the vendored runtime and update `manager.py` to inject it before `playwright_recorder_capture.js`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager.RPASessionManagerTabTests`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/vendor/playwright_recorder_actions.js RpaClaw/backend/rpa/vendor/playwright_recorder_capture.js RpaClaw/backend/rpa/manager.py RpaClaw/backend/tests/test_rpa_manager.py
git commit -m "fix: vendor playwright recorder action runtime"
```

### Task 3: Move capture classification to the vendored runtime

**Files:**
- Modify: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/vendor/playwright_recorder_capture.js`
- Modify: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/manager.py`
- Modify: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/generator.py`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_manager.py`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Write the failing test**

Add generator and manager tests for:
- `check` description rendering
- `uncheck` script generation
- `set_input_files` script generation if emitted by the action runtime

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager RpaClaw.backend.tests.test_rpa_generator`
Expected: FAIL because these logical actions are not yet recognized end-to-end.

- [ ] **Step 3: Write minimal implementation**

Switch capture to call the vendored action runtime, translate normalized logical actions into recorder events, and teach manager/generator the new action names.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager RpaClaw.backend.tests.test_rpa_generator`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/vendor/playwright_recorder_capture.js RpaClaw/backend/rpa/manager.py RpaClaw/backend/rpa/generator.py RpaClaw/backend/tests/test_rpa_manager.py RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "fix: record logical actions with playwright semantics"
```

### Task 4: Run focused verification

**Files:**
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_manager.py`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/tests/test_rpa_generator.py`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/vendor/playwright_recorder_actions.js`
- Test: `D:/code/MyScienceClaw/RpaClaw/backend/rpa/vendor/playwright_recorder_capture.js`

- [ ] **Step 1: Run manager tests**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_manager`
Expected: PASS

- [ ] **Step 2: Run generator tests**

Run: `python -m unittest RpaClaw.backend.tests.test_rpa_generator`
Expected: PASS

- [ ] **Step 3: Run JS syntax checks**

Run: `node --check D:/code/MyScienceClaw/RpaClaw/backend/rpa/vendor/playwright_recorder_actions.js`
Expected: exit 0

Run: `node --check D:/code/MyScienceClaw/RpaClaw/backend/rpa/vendor/playwright_recorder_capture.js`
Expected: exit 0

- [ ] **Step 4: Review scope**

Confirm the change is limited to recorder action classification, backend action handling, and documentation.

- [ ] **Step 5: Commit documentation**

```bash
git add D:/code/MyScienceClaw/docs/superpowers/specs/2026-04-11-rpa-recorder-playwright-action-vendor-design.md D:/code/MyScienceClaw/docs/superpowers/plans/2026-04-11-rpa-recorder-playwright-action-vendor.md
git commit -m "chore: document recorder action vendor plan"
```
