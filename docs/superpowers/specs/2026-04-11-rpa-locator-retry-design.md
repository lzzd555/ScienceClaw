# RPA 智能定位器重试系统设计

## 概述

当 RPA 技能测试执行失败时，系统能精确识别失败步骤，向用户展示该步骤的候选定位器列表，用户选择后自动持久化并整体重新执行。整个流程在 TestPage 内闭环完成。

## 决策记录

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 失败步骤识别 | generator test_mode 注入步骤索引 | 精确到步骤，零误判，不影响生产脚本 |
| 重试范围 | 整体重新执行 | 浏览器状态完全一致，更可靠 |
| 交互位置 | TestPage 内完成 | 不离开测试页，流程闭环 |
| 定位器持久化 | 自动持久化到 session | 复用现有 API，后续测试/导出均使用新定位器 |

## 架构

### 数据流

```
测试失败
  → executor 捕获 StepExecutionError(step_index, error)
  → route 返回 { failed_step_index, error, failed_step_candidates[] }
  → 前端高亮失败步骤，展示候选定位器列表
  → 用户选择候选
  → POST /rpa/session/{id}/step/{idx}/locator 持久化
  → 自动调用 runTest() 整体重新执行
```

### 涉及文件

| 文件 | 改动 |
|------|------|
| `backend/rpa/generator.py` | `generate_script()` 新增 `test_mode` 参数，该模式下为每个步骤包裹 try/except |
| `backend/rpa/executor.py` | 识别 `StepExecutionError`，返回结果增加 `failed_step_index` |
| `backend/route/rpa.py` | `/test` 端点使用 test_mode 生成脚本，返回失败步骤的候选定位器 |
| `frontend/src/pages/rpa/TestPage.vue` | 失败时高亮步骤、展示候选列表、选择后自动重试 |

## 后端设计

### 1. generator.py — test_mode 包裹

`generate_script()` 签名变更：

```python
def generate_script(self, steps, params=None, is_local=False, test_mode=False) -> str:
```

当 `test_mode=True` 时：

1. 在生成的脚本顶部注入 `StepExecutionError` 类定义
2. 维护一个 `original_step_index` 计数器，跟踪原始步骤索引（去重前）
3. 给每个步骤的代码行包裹 `try/except`，异常时抛出 `StepExecutionError`

注入的异常类定义：

```python
class StepExecutionError(Exception):
    def __init__(self, step_index, original_error):
        self.step_index = step_index
        self.original_error = original_error
        super().__init__(f"STEP_FAILED:{step_index}:{original_error}")
```

生成示例（test_mode=True）：

```python
async def execute_skill(page, **kwargs):
    _results = {}
    tabs = {"tab-1": page}
    current_page = page

    # Step 1: navigate
    try:
        await current_page.goto("https://example.com")
        await current_page.wait_for_load_state("domcontentloaded")
    except StepExecutionError:
        raise
    except Exception as _e:
        raise StepExecutionError(step_index=0, original_error=str(_e))

    # Step 2: click
    try:
        await current_page.get_by_role("button", name="Submit", exact=True).click()
        await current_page.wait_for_timeout(500)
    except StepExecutionError:
        raise
    except Exception as _e:
        raise StepExecutionError(step_index=1, original_error=str(_e))

    return _results
```

关键细节：

- `step_index` 使用 0-based，与前端 `recordedSteps` 数组下标一致
- 每个 try/except 先 `except StepExecutionError: raise` 以避免嵌套步骤（如 popup/download 包裹）吞掉内层步骤的错误
- `navigate`、`switch_tab`、`close_tab` 等非 locator 步骤同样包裹，保证任何失败都能定位
- 生产模式（`test_mode=False`）生成的脚本完全不变

#### 步骤索引对齐

generator 内部会 `_deduplicate_steps` 和 `_infer_missing_tab_transitions`，导致生成的步骤数可能与原始 `recordedSteps` 不同。解决方案：

- 在 `generate_script` 中，为去重后的每个步骤记录其在原始 steps 列表中的索引
- 每个原始步骤都带有唯一 `id` 字段（`RecordedActionV2.id`），在去重后保留该 id
- `StepExecutionError` 中存的 `step_index` 使用原始步骤在 deduped 后列表中的位置索引
- route 层使用该索引在原始 steps 列表中查找对应步骤的 `locator_candidates`

具体做法：在 `generate_script` 的循环中，用 `enumerate(deduped)` 的索引作为 step_index。route 层同样对 steps 做相同的 deduplicate 处理，使用相同索引查找。

### 2. executor.py — 结构化错误返回

在 `_run()` 函数的 `except Exception as e` 分支中，增加 `StepExecutionError` 识别：

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

注意：通过解析异常消息字符串而非 isinstance 检查来识别 StepExecutionError，因为该类是在 `exec()` 的动态 namespace 中定义的，无法直接 import。

### 3. route/rpa.py — 增强 /test 端点

`POST /session/{session_id}/test` 端点改动：

1. 生成脚本时传入 `test_mode=True`：

```python
script = generator.generate_script(steps, params, is_local=is_local, test_mode=True)
```

2. 执行完成后，如果有 `failed_step_index`，提取候选定位器：

```python
failed_step_index = result.get("failed_step_index")
failed_step_candidates = []

if failed_step_index is not None:
    # 对 steps 做与 generator 完全相同的预处理
    deduped = generator._deduplicate_steps(steps)
    deduped = generator._infer_missing_tab_transitions(deduped)
    deduped = generator._normalize_step_signals(deduped)
    if 0 <= failed_step_index < len(deduped):
        failed_step = deduped[failed_step_index]
        candidates = failed_step.get("locator_candidates", [])
        # 排除当前选中的，按 score 升序排列
        # 每个候选携带 original_index（在原始 locator_candidates 数组中的索引）
        # 以便前端调用 /step/{idx}/locator API 时传入正确的索引
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

3. 返回值增加字段：

```python
return JSONResponse({
    "status": "success" if result["success"] else "failed",
    "result": result,
    "logs": logs,
    "script": script,
    "failed_step_index": failed_step_index,
    "failed_step_candidates": failed_step_candidates,
})
```

## 前端设计

### TestPage.vue 状态新增

```typescript
interface LocatorCandidate {
  kind: string;
  score: number;
  strict_match_count: number;
  visible_match_count: number;
  selected: boolean;
  locator: Record<string, any>;
  playwright_locator?: string;
}

const failedStepIndex = ref<number | null>(null);
const failedStepCandidates = ref<LocatorCandidate[]>([]);
const failedStepError = ref<string>('');
const triedCandidateIndices = ref<Set<number>>(new Set());
const retryingWithCandidate = ref(false);
```

### runTest() 改动

在 `resp` 返回后，提取失败信息：

```typescript
const resp = await testPromise;
const result = resp.data.result || {};
// ... 现有逻辑 ...

// 新增：提取失败步骤信息
failedStepIndex.value = resp.data.failed_step_index ?? null;
failedStepCandidates.value = resp.data.failed_step_candidates || [];
failedStepError.value = result.error || '';
```

### 新增 retryWithCandidate() 方法

```typescript
const retryWithCandidate = async (candidateIndex: number) => {
  if (retryingWithCandidate.value || failedStepIndex.value === null) return;
  retryingWithCandidate.value = true;

  try {
    // 1. 持久化到 session（使用候选在原始数组中的索引）
    const candidate = failedStepCandidates.value[candidateIndex];
    const originalIndex = candidate.original_index ?? candidateIndex;
    await apiClient.post(
      `/rpa/session/${sessionId.value}/step/${failedStepIndex.value}/locator`,
      { candidate_index: originalIndex }
    );

    // 2. 记录已尝试
    triedCandidateIndices.value.add(candidateIndex);

    // 3. 重新加载步骤数据
    await loadSessionDiagnostics();

    // 4. 重置失败状态并重新执行
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

### 侧边栏步骤列表 UI 改动

失败步骤卡片增加红色高亮和候选列表展开区域：

```html
<div
  v-for="(step, index) in recordedSteps"
  :key="step.id || index"
  class="rounded-xl border-l-4 bg-white p-4 shadow-sm"
  :class="[
    failedStepIndex === index
      ? 'border-red-500 ring-2 ring-red-100'
      : 'border-gray-200'
  ]"
>
  <!-- 现有步骤信息 -->
  ...

  <!-- 失败步骤：错误信息 + 候选定位器 -->
  <div v-if="failedStepIndex === index" class="mt-3 space-y-3">
    <div class="rounded-lg bg-red-50 p-2.5 text-[11px] text-red-700">
      <p class="font-semibold">执行失败</p>
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
            <span class="font-mono font-medium text-gray-900">
              {{ candidate.kind }}: {{ candidate.playwright_locator || formatLocator(candidate.locator) }}
            </span>
            <span
              v-if="cIdx === 0 && !triedCandidateIndices.has(cIdx)"
              class="rounded-full bg-purple-100 px-1.5 py-0.5 text-[9px] font-bold text-purple-700"
            >
              推荐
            </span>
            <span
              v-if="triedCandidateIndices.has(cIdx)"
              class="rounded-full bg-gray-200 px-1.5 py-0.5 text-[9px] font-bold text-gray-500"
            >
              已尝试
            </span>
          </div>
          <div class="mt-1 flex gap-3 text-gray-500">
            <span>score: {{ candidate.score }}</span>
            <span :class="candidate.strict_match_count === 1 ? 'text-green-600' : 'text-amber-600'">
              match: {{ candidate.strict_match_count }}
              {{ candidate.strict_match_count === 1 ? '✓' : '⚠' }}
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

### 右侧面板失败提示

在失败状态区增加引导文案：

```html
<p v-if="testDone && !testSuccess && failedStepIndex !== null && failedStepCandidates.length > 0"
   class="text-xs leading-relaxed text-red-700">
  步骤 {{ failedStepIndex + 1 }} 执行失败，左侧已展示候选定位器，请选择一个后自动重试。
</p>
<p v-else-if="testDone && !testSuccess"
   class="text-xs leading-relaxed text-red-700">
  执行过程中出现错误，请查看日志后重新执行或返回修改。
</p>
```

### 自动滚动

失败后自动滚动到失败步骤卡片：

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

需要给步骤卡片增加 `data-step-index` 属性。

## 边界情况

1. **非 locator 失败**（navigate、timeout）：`failed_step_index` 有值但 `failed_step_candidates` 为空数组。前端显示错误信息，不显示候选列表，展示 "此步骤无候选定位器可切换" 提示。

2. **ai_script 步骤失败**：同上，无候选定位器。

3. **所有候选均已尝试**：前端标记所有候选为 "已尝试"（通过 `triedCandidateIndices`），不禁用按钮（允许再次尝试），但提示用户 "所有候选定位器均已尝试，建议返回重新录制此步骤"。

4. **步骤索引对齐**：route 层对 steps 做与 generator 相同的 `_deduplicate_steps` + `_infer_missing_tab_transitions` 处理，使用处理后的索引查找候选。generator 中 `step_index` 对应的是处理后列表的索引。前端 `recordedSteps` 也需要展示处理后的步骤列表以保持一致。

5. **并发控制**：`retryingWithCandidate` ref 控制 UI 锁定，按钮在重试过程中 disabled，避免并发请求。

6. **重试后不同步骤失败**：重置 `triedCandidateIndices`（因为新步骤的候选是独立的），更新 `failedStepIndex` 为新的失败步骤。

## 不涉及的范围

- 不修改生产模式（`test_mode=False`）的脚本生成逻辑
- 不修改 ConfigurePage 的候选定位器选择功能
- 不增加自动候选轮询（自动尝试所有候选）— 保持用户手动选择
- 不修改技能导出流程
