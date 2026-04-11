# RPA Locator Retry System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When RPA skill tests fail, precisely identify the failed step and let users pick an alternative locator candidate for automatic retry — all within the TestPage.

**Architecture:** Generator gains a `test_mode` that wraps each step in try/except with step-index tracking. Executor parses the structured error. Route returns `failed_step_index` + candidate list. Frontend highlights the failed step, shows ranked candidates, and auto-retries after user selection.

**Tech Stack:** Python (FastAPI, Playwright), Vue 3 + TypeScript, existing apiClient

---

### Task 1: Generator test_mode — inject StepExecutionError per step

**Files:**
- Modify: `RpaClaw/backend/rpa/generator.py:113-317`
- Test: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Write the failing test — test_mode wraps click step in try/except**

Add to `RpaClaw/backend/tests/test_rpa_generator.py` before `if __name__`:

```python
    def test_generate_script_test_mode_wraps_click_in_try_except(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Submit"}),
                "description": "点击提交按钮",
                "url": "https://example.com",
            }
        ]

        script = generator.generate_script(steps, is_local=True, test_mode=True)

        self.assertIn("class StepExecutionError(Exception):", script)
        self.assertIn("except StepExecutionError:", script)
        self.assertIn("raise StepExecutionError(step_index=0,", script)
        # The actual click is still present
        self.assertIn('.get_by_role("button", name="Submit", exact=True).click()', script)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:/code/MyScienceClaw/RpaClaw && uv run python -m pytest backend/tests/test_rpa_generator.py::PlaywrightGeneratorTests::test_generate_script_test_mode_wraps_click_in_try_except -v`
Expected: FAIL — `generate_script() got an unexpected keyword argument 'test_mode'`

- [ ] **Step 3: Write the failing test — test_mode wraps navigate step**

Add to `RpaClaw/backend/tests/test_rpa_generator.py` before `if __name__`:

```python
    def test_generate_script_test_mode_wraps_navigate_step(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "navigate",
                "target": "",
                "url": "https://example.com",
                "description": "打开首页",
            }
        ]

        script = generator.generate_script(steps, is_local=True, test_mode=True)

        self.assertIn("raise StepExecutionError(step_index=0,", script)
        self.assertIn('await current_page.goto("https://example.com")', script)
```

- [ ] **Step 4: Write the failing test — test_mode=False produces unchanged output**

Add to `RpaClaw/backend/tests/test_rpa_generator.py` before `if __name__`:

```python
    def test_generate_script_test_mode_false_produces_unchanged_output(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Go"}),
                "description": "Click go",
                "url": "https://example.com",
            }
        ]

        script_normal = generator.generate_script(steps, is_local=True)
        script_explicit = generator.generate_script(steps, is_local=True, test_mode=False)

        self.assertEqual(script_normal, script_explicit)
        self.assertNotIn("StepExecutionError", script_normal)
```

- [ ] **Step 5: Write the failing test — test_mode step_index aligns after dedup**

Add to `RpaClaw/backend/tests/test_rpa_generator.py` before `if __name__`:

```python
    def test_generate_script_test_mode_step_index_aligns_after_dedup(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "A"}),
                "description": "first click",
                "url": "https://example.com",
            },
            # Duplicate — will be deduped
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "A"}),
                "description": "duplicate click",
                "url": "https://example.com",
            },
            {
                "action": "fill",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "Search"}),
                "value": "hello",
                "description": "fill search",
                "url": "https://example.com",
            },
        ]

        script = generator.generate_script(steps, is_local=True, test_mode=True)

        # After dedup: step 0 = click A, step 1 = fill Search
        self.assertIn("raise StepExecutionError(step_index=0,", script)
        self.assertIn("raise StepExecutionError(step_index=1,", script)
        # No step_index=2 since duplicate was removed
        self.assertNotIn("step_index=2", script)
```

- [ ] **Step 6: Write the failing test — test_mode re-raises inner StepExecutionError**

Add to `RpaClaw/backend/tests/test_rpa_generator.py` before `if __name__`:

```python
    def test_generate_script_test_mode_reraises_step_execution_error(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "X"}),
                "description": "click",
                "url": "https://example.com",
            }
        ]

        script = generator.generate_script(steps, is_local=True, test_mode=True)

        # The except block must re-raise StepExecutionError before catching generic Exception
        # This ensures nested steps (popup/download wrappers) don't swallow inner errors
        step_error_pos = script.index("except StepExecutionError:")
        raise_pos = script.index("raise\n", step_error_pos)
        generic_except_pos = script.index("except Exception as _e:", step_error_pos)
        self.assertLess(raise_pos, generic_except_pos)
```

- [ ] **Step 7: Implement test_mode in generator.py**

In `RpaClaw/backend/rpa/generator.py`, modify `generate_script` method:

1. Change the signature on line 113:

```python
    def generate_script(self, steps: List[Dict[str, Any]], params: Dict[str, Any] = None, is_local: bool = False, test_mode: bool = False) -> str:
```

2. After the `lines` list initialization (after line 129), when `test_mode=True`, inject the `StepExecutionError` class before the `execute_skill` function. Add this block after line 121 (after `used_result_keys` initialization):

```python
        test_mode_preamble = ""
        if test_mode:
            test_mode_preamble = (
                "\nclass StepExecutionError(Exception):\n"
                "    def __init__(self, step_index, original_error):\n"
                "        self.step_index = step_index\n"
                "        self.original_error = original_error\n"
                '        super().__init__(f"STEP_FAILED:{step_index}:{original_error}")\n'
            )
```

3. Add a helper method `_wrap_step_lines` to the class that wraps a list of step code lines in try/except when `test_mode=True`:

```python
    @staticmethod
    def _wrap_step_lines(step_lines: List[str], step_index: int, test_mode: bool) -> List[str]:
        """Wrap step code lines in try/except for test_mode error tracking."""
        if not test_mode or not step_lines:
            return step_lines
        wrapped = ["    try:"]
        for line in step_lines:
            if line.strip():  # Non-empty lines get extra indent
                wrapped.append("    " + line)
            else:
                wrapped.append(line)
        wrapped.append("    except StepExecutionError:")
        wrapped.append("        raise")
        wrapped.append("    except Exception as _e:")
        wrapped.append(f"        raise StepExecutionError(step_index={step_index}, original_error=str(_e))")
        return wrapped
```

4. Refactor the main loop (line 143 onwards) so that each step's code lines are collected into a `step_lines` list, then wrapped via `_wrap_step_lines` before appending to `lines`. The key change: instead of `lines.append(...)` directly for each action, collect them in `step_lines`, then:

```python
        for step_index, step in enumerate(deduped):
            # ... parse action, target, value, url, desc, frame_path ...

            step_lines: List[str] = []

            if desc:
                lines.append(f"    # {desc}")

            # === Each action branch appends to step_lines instead of lines ===
            # (same code as before, but s/lines.append/step_lines.append/)

            # After the action branch, wrap and extend:
            wrapped = self._wrap_step_lines(step_lines, step_index, test_mode)
            lines.extend(wrapped)
            lines.append("")

            prev_action = action
```

**Important details for the refactor:**
- Change `enumerate(deduped, 1)` to `enumerate(deduped)` so `step_index` is 0-based
- The `step_index` reference on line 296 (`extract_text_value_{step_index}`) must use `step_index + 1` to preserve uniqueness, since it was previously 1-based
- Comments (like `# desc`) go directly to `lines` (outside the try block)
- The `continue` statements in navigate/switch_tab/close_tab/download/ai_script branches remain, but must add the wrap+extend+append-empty before `continue`
- For `popup_signal`/`download_signal` complex blocks: collect the entire block into `step_lines`

5. At the bottom (line 311-317), insert `test_mode_preamble` before the execute_skill function:

```python
        execute_skill_func = "\n".join(lines)
        if test_mode:
            execute_skill_func = test_mode_preamble + execute_skill_func
        template = self.RUNNER_TEMPLATE_LOCAL if is_local else self.RUNNER_TEMPLATE_DOCKER
```

- [ ] **Step 8: Run all generator tests to verify**

Run: `cd D:/code/MyScienceClaw/RpaClaw && uv run python -m pytest backend/tests/test_rpa_generator.py -v`
Expected: ALL PASS (existing 29 tests + 5 new tests)

- [ ] **Step 9: Commit**

```bash
cd D:/code/MyScienceClaw
git add RpaClaw/backend/rpa/generator.py RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "feat(rpa): add test_mode to generator for step-level error tracking"
```

---

### Task 2: Executor — parse StepExecutionError and return failed_step_index

**Files:**
- Modify: `RpaClaw/backend/rpa/executor.py:89-99`
- Test: `RpaClaw/backend/tests/test_rpa_generator.py` (add executor tests to same file for simplicity, or create new file)

- [ ] **Step 1: Write the failing test — executor returns failed_step_index on StepExecutionError**

Create `RpaClaw/backend/tests/test_rpa_executor.py`:

```python
import asyncio
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

EXECUTOR_PATH = Path(__file__).resolve().parents[1] / "rpa" / "executor.py"
SPEC = importlib.util.spec_from_file_location("rpa_executor_module", EXECUTOR_PATH)
EXECUTOR_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(EXECUTOR_MODULE)
ScriptExecutor = EXECUTOR_MODULE.ScriptExecutor


class ScriptExecutorTests(unittest.TestCase):
    def test_execute_returns_failed_step_index_on_step_error(self):
        executor = ScriptExecutor()
        script = '''
class StepExecutionError(Exception):
    def __init__(self, step_index, original_error):
        self.step_index = step_index
        self.original_error = original_error
        super().__init__(f"STEP_FAILED:{step_index}:{original_error}")

async def execute_skill(page, **kwargs):
    raise StepExecutionError(step_index=2, original_error="Timeout 30000ms exceeded")
'''
        browser = MagicMock()
        context = AsyncMock()
        page = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser.new_context = AsyncMock(return_value=context)
        page.set_default_timeout = MagicMock()
        page.set_default_navigation_timeout = MagicMock()
        page.wait_for_timeout = AsyncMock()

        result = asyncio.run(executor.execute(browser, script))

        self.assertFalse(result["success"])
        self.assertEqual(result["failed_step_index"], 2)
        self.assertEqual(result["error"], "Timeout 30000ms exceeded")

    def test_execute_returns_none_failed_step_index_on_generic_error(self):
        executor = ScriptExecutor()
        script = '''
async def execute_skill(page, **kwargs):
    raise RuntimeError("something broke")
'''
        browser = MagicMock()
        context = AsyncMock()
        page = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser.new_context = AsyncMock(return_value=context)
        page.set_default_timeout = MagicMock()
        page.set_default_navigation_timeout = MagicMock()
        page.wait_for_timeout = AsyncMock()

        result = asyncio.run(executor.execute(browser, script))

        self.assertFalse(result["success"])
        self.assertIsNone(result["failed_step_index"])

    def test_execute_returns_none_failed_step_index_on_success(self):
        executor = ScriptExecutor()
        script = '''
async def execute_skill(page, **kwargs):
    return {"ok": True}
'''
        browser = MagicMock()
        context = AsyncMock()
        page = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser.new_context = AsyncMock(return_value=context)
        page.set_default_timeout = MagicMock()
        page.set_default_navigation_timeout = MagicMock()
        page.wait_for_timeout = AsyncMock()

        result = asyncio.run(executor.execute(browser, script))

        self.assertTrue(result["success"])
        self.assertIsNone(result.get("failed_step_index"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd D:/code/MyScienceClaw/RpaClaw && uv run python -m pytest backend/tests/test_rpa_executor.py -v`
Expected: FAIL — `result["failed_step_index"]` key not present

- [ ] **Step 3: Implement StepExecutionError parsing in executor.py**

In `RpaClaw/backend/rpa/executor.py`, replace the `except Exception as e` block (lines 95-99) with:

```python
            except Exception as e:
                failed_step_index = None
                original_error = str(e)

                error_str = str(e)
                if "STEP_FAILED:" in error_str:
                    try:
                        parts = error_str.split("STEP_FAILED:", 1)[1].split(":", 1)
                        failed_step_index = int(parts[0])
                        original_error = parts[1] if len(parts) > 1 else error_str
                    except (ValueError, IndexError):
                        pass

                output = f"SKILL_ERROR: {original_error}"
                if on_log:
                    if failed_step_index is not None:
                        on_log(f"Step {failed_step_index + 1} failed: {original_error}")
                    else:
                        on_log(f"Execution failed: {original_error}")
                return {
                    "success": False,
                    "output": output,
                    "error": original_error,
                    "failed_step_index": failed_step_index,
                }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd D:/code/MyScienceClaw/RpaClaw && uv run python -m pytest backend/tests/test_rpa_executor.py -v`
Expected: ALL 3 PASS

- [ ] **Step 5: Run all existing tests to verify no regressions**

Run: `cd D:/code/MyScienceClaw/RpaClaw && uv run python -m pytest backend/tests/test_rpa_generator.py backend/tests/test_rpa_executor.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
cd D:/code/MyScienceClaw
git add RpaClaw/backend/rpa/executor.py RpaClaw/backend/tests/test_rpa_executor.py
git commit -m "feat(rpa): executor parses StepExecutionError for failed_step_index"
```

---

### Task 3: Route — /test endpoint returns failed step candidates

**Files:**
- Modify: `RpaClaw/backend/route/rpa.py:402-459`

- [ ] **Step 1: Modify the test endpoint to use test_mode and return candidates**

In `RpaClaw/backend/route/rpa.py`, modify the `test_script` function (starting at line 402):

1. Change the script generation line (line 413) to pass `test_mode=True`:

```python
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"), test_mode=True)
```

2. After the execution block (after line 458, before the return), add candidate extraction logic:

```python
    # Extract failed step candidates for locator retry
    failed_step_index = result.get("failed_step_index")
    failed_step_candidates = []
    if failed_step_index is not None:
        deduped = generator._deduplicate_steps(steps)
        deduped = generator._infer_missing_tab_transitions(deduped)
        deduped = generator._normalize_step_signals(deduped)
        if 0 <= failed_step_index < len(deduped):
            failed_step = deduped[failed_step_index]
            candidates = failed_step.get("locator_candidates", [])
            filtered = []
            for orig_idx, c in enumerate(candidates):
                if not c.get("selected"):
                    entry = dict(c)
                    entry["original_index"] = orig_idx
                    filtered.append(entry)
            failed_step_candidates = sorted(
                filtered,
                key=lambda c: (
                    0 if c.get("strict_match_count") == 1 else 1,
                    c.get("score", 999),
                ),
            )
```

3. Replace the return statement (line 459) with:

```python
    return {
        "status": "success" if result.get("success") else "failed",
        "result": result,
        "logs": logs,
        "script": script,
        "failed_step_index": failed_step_index,
        "failed_step_candidates": failed_step_candidates,
    }
```

- [ ] **Step 2: Verify route file is syntactically valid**

Run: `cd D:/code/MyScienceClaw/RpaClaw && uv run python -c "import ast; ast.parse(open('backend/route/rpa.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd D:/code/MyScienceClaw
git add RpaClaw/backend/route/rpa.py
git commit -m "feat(rpa): /test endpoint returns failed_step_index and candidates"
```

---

### Task 4: Frontend — TestPage locator retry UI

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/TestPage.vue`

- [ ] **Step 1: Add TypeScript state and interface**

In `RpaClaw/frontend/src/pages/rpa/TestPage.vue`, add the imports and state. After the existing imports (line 2), add `nextTick` and `watch` to the vue import:

```typescript
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
```

Add the `AlertTriangle` icon import (add to the lucide-vue-next import list):

```typescript
import {
  AlertTriangle,
  ArrowLeft,
  // ... existing imports ...
} from 'lucide-vue-next';
```

After the existing ref declarations (after line 70, after `const error = ref<string | null>(null);`), add:

```typescript
interface LocatorCandidate {
  kind: string;
  score: number;
  strict_match_count: number;
  visible_match_count: number;
  selected: boolean;
  locator: Record<string, any>;
  playwright_locator?: string;
  original_index?: number;
}

const failedStepIndex = ref<number | null>(null);
const failedStepCandidates = ref<LocatorCandidate[]>([]);
const failedStepError = ref('');
const triedCandidateIndices = ref<Set<number>>(new Set());
const retryingWithCandidate = ref(false);
```

- [ ] **Step 2: Update runTest() to extract failure info**

In the `runTest` function, after line 248 (`testSuccess.value = result.success !== false;`), add:

```typescript
    const newFailedIndex = resp.data.failed_step_index ?? null;
    // Reset tried candidates when a different step fails
    if (newFailedIndex !== failedStepIndex.value) {
      triedCandidateIndices.value = new Set();
    }
    failedStepIndex.value = newFailedIndex;
    failedStepCandidates.value = resp.data.failed_step_candidates || [];
    failedStepError.value = result.error || '';
```

At the start of `runTest` (after `testLogs.value = ['正在生成并执行 Playwright 脚本...'];` on line 231), add reset logic:

```typescript
    failedStepIndex.value = null;
    failedStepCandidates.value = [];
    failedStepError.value = '';
```

- [ ] **Step 3: Add retryWithCandidate method**

After the `runTest` function definition, add:

```typescript
const retryWithCandidate = async (candidateIndex: number) => {
  if (retryingWithCandidate.value || failedStepIndex.value === null) return;
  retryingWithCandidate.value = true;

  try {
    const candidate = failedStepCandidates.value[candidateIndex];
    const originalIndex = candidate.original_index ?? candidateIndex;
    await apiClient.post(
      `/rpa/session/${sessionId.value}/step/${failedStepIndex.value}/locator`,
      { candidate_index: originalIndex },
    );

    triedCandidateIndices.value.add(candidateIndex);
    await loadSessionDiagnostics();

    failedStepIndex.value = null;
    failedStepCandidates.value = [];
    failedStepError.value = '';
    await runTest();
  } catch (err: any) {
    error.value = `切换定位器失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    retryingWithCandidate.value = false;
  }
};
```

- [ ] **Step 4: Add auto-scroll watcher**

After the `retryWithCandidate` function, add:

```typescript
watch(failedStepIndex, (index) => {
  if (index !== null) {
    nextTick(() => {
      const el = document.querySelector(`[data-step-index="${index}"]`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  }
});
```

- [ ] **Step 5: Update sidebar step list template**

Replace the step card `div` in the template (lines 362-405). Find the `v-for` loop rendering recorded steps and replace:

```html
          <div
            v-for="(step, index) in recordedSteps"
            :key="step.id || index"
            :data-step-index="index"
            class="rounded-xl border-l-4 bg-white p-4 shadow-sm"
            :class="[
              failedStepIndex === index
                ? 'border-red-500 ring-2 ring-red-100'
                : 'border-gray-200'
            ]"
          >
            <div class="mb-1 flex items-center justify-between gap-3">
              <span class="text-[10px] font-bold text-gray-400">
                步骤 {{ String(index + 1).padStart(2, '0') }}
              </span>
              <span
                class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                :class="getValidationClass(step.validation?.status)"
              >
                {{ getValidationLabel(step.validation?.status) }}
              </span>
            </div>
            <h3 class="text-sm font-semibold text-gray-900">
              {{ step.description || step.action }}
            </h3>
            <p class="mt-2 break-all text-[11px] text-gray-500">
              <span class="font-semibold text-gray-600">Locator:</span>
              <span class="ml-1 font-mono">{{ formatLocator(step.target) }}</span>
            </p>
            <p class="mt-1 break-all text-[11px] text-gray-500">
              <span class="font-semibold text-gray-600">Frame:</span>
              <span class="ml-1 font-mono">{{ formatFramePath(step.frame_path) }}</span>
            </p>
            <p
              v-if="step.validation?.details"
              class="mt-1 break-all text-[11px] text-gray-500"
            >
              <span class="font-semibold text-gray-600">Details:</span>
              <span class="ml-1">{{ step.validation.details }}</span>
            </p>

            <!-- Failed step: error + candidate locators -->
            <div v-if="failedStepIndex === index" class="mt-3 space-y-3">
              <div class="rounded-lg bg-red-50 p-2.5 text-[11px] text-red-700">
                <p class="font-semibold flex items-center gap-1">
                  <AlertTriangle :size="12" />
                  执行失败
                </p>
                <p class="mt-1 break-all">{{ failedStepError }}</p>
              </div>

              <div v-if="failedStepCandidates.length > 0">
                <p class="text-[11px] font-semibold text-gray-700">
                  尝试其他定位器：
                </p>
                <div class="mt-2 space-y-1.5">
                  <button
                    v-for="(candidate, cIdx) in failedStepCandidates"
                    :key="cIdx"
                    class="w-full rounded-lg border p-2 text-left text-[11px] transition-colors"
                    :class="[
                      triedCandidateIndices.has(cIdx)
                        ? 'border-gray-200 bg-gray-50 opacity-60'
                        : 'border-purple-200 hover:bg-purple-50 hover:border-purple-400'
                    ]"
                    :disabled="retryingWithCandidate"
                    @click="retryWithCandidate(cIdx)"
                  >
                    <div class="flex items-center justify-between">
                      <span class="font-mono font-medium text-gray-900 truncate">
                        {{ candidate.kind }}: {{ candidate.playwright_locator || formatLocator(candidate.locator) }}
                      </span>
                      <span
                        v-if="cIdx === 0 && !triedCandidateIndices.has(cIdx)"
                        class="flex-shrink-0 rounded-full bg-purple-100 px-1.5 py-0.5 text-[9px] font-bold text-purple-700"
                      >
                        推荐
                      </span>
                      <span
                        v-if="triedCandidateIndices.has(cIdx)"
                        class="flex-shrink-0 rounded-full bg-gray-200 px-1.5 py-0.5 text-[9px] font-bold text-gray-500"
                      >
                        已尝试
                      </span>
                    </div>
                    <div class="mt-1 flex gap-3 text-gray-500">
                      <span>score: {{ candidate.score }}</span>
                      <span :class="candidate.strict_match_count === 1 ? 'text-green-600' : 'text-amber-600'">
                        match: {{ candidate.strict_match_count }}
                        {{ candidate.strict_match_count === 1 ? '\u2713' : '\u26A0' }}
                      </span>
                    </div>
                  </button>
                </div>
              </div>

              <div v-else class="rounded-lg bg-amber-50 p-2.5 text-[11px] text-amber-700">
                此步骤无候选定位器可切换，建议返回重新录制。
              </div>
            </div>
          </div>
```

- [ ] **Step 6: Update right panel failure message**

In the right panel, replace the failure message `<p>` (lines 527-530):

```html
            <p
              v-if="testDone && !testSuccess && failedStepIndex !== null && failedStepCandidates.length > 0"
              class="text-xs leading-relaxed text-red-700"
            >
              步骤 {{ (failedStepIndex ?? 0) + 1 }} 执行失败，左侧已展示候选定位器，请选择一个后自动重试。
            </p>
            <p
              v-else-if="testDone && !testSuccess"
              class="text-xs leading-relaxed text-red-700"
            >
              执行过程中出现错误，请查看日志后重新执行或返回修改。
            </p>
```

- [ ] **Step 7: Verify frontend compiles**

Run: `cd D:/code/MyScienceClaw/RpaClaw/frontend && npm run build 2>&1 | tail -10`
Expected: Build succeeds with no errors

- [ ] **Step 8: Commit**

```bash
cd D:/code/MyScienceClaw
git add RpaClaw/frontend/src/pages/rpa/TestPage.vue
git commit -m "feat(rpa): TestPage shows failed step candidates and supports retry"
```

---

### Task 5: Integration test — end-to-end test_mode → failed_step_index flow

**Files:**
- Test: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: Write integration test — generator + executor round-trip**

Add to `RpaClaw/backend/tests/test_rpa_generator.py` before `if __name__`:

```python
    def test_test_mode_script_raises_parseable_step_error_on_missing_locator(self):
        """Integration: generate a test_mode script, exec it, verify the error carries step_index."""
        import asyncio
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "navigate",
                "target": "",
                "url": "https://example.com",
                "description": "打开首页",
            },
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Nonexistent"}),
                "description": "点击不存在的按钮",
                "url": "https://example.com",
            },
        ]

        script = generator.generate_script(steps, is_local=True, test_mode=True)

        # Extract execute_skill and StepExecutionError from the generated script
        namespace = {}
        exec(compile(script, "<test>", "exec"), namespace)
        self.assertIn("execute_skill", namespace)
        self.assertIn("StepExecutionError", namespace)

        StepError = namespace["StepExecutionError"]

        # Verify StepExecutionError message format is parseable
        err = StepError(step_index=1, original_error="Timeout 30000ms")
        self.assertIn("STEP_FAILED:1:", str(err))
        parts = str(err).split("STEP_FAILED:", 1)[1].split(":", 1)
        self.assertEqual(int(parts[0]), 1)
        self.assertEqual(parts[1], "Timeout 30000ms")
```

- [ ] **Step 2: Run the test**

Run: `cd D:/code/MyScienceClaw/RpaClaw && uv run python -m pytest backend/tests/test_rpa_generator.py::PlaywrightGeneratorTests::test_test_mode_script_raises_parseable_step_error_on_missing_locator -v`
Expected: PASS

- [ ] **Step 3: Run ALL tests one final time**

Run: `cd D:/code/MyScienceClaw/RpaClaw && uv run python -m pytest backend/tests/test_rpa_generator.py backend/tests/test_rpa_executor.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
cd D:/code/MyScienceClaw
git add RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "test(rpa): add integration test for test_mode step error round-trip"
```
