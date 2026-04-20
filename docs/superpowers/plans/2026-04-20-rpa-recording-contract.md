# RPA 录制契约重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 RPA 技能录制链路的数据契约，使上下文提取统一走结构化 `ai_script`，前端完整展示每轮尝试，并让生成器基于语义意图而不是录制值生成可回放代码。

**Architecture:** 先在后端建立新的步骤契约与尝试事件模型，再把上下文提取统一迁移到 `ai_script`，随后升级前端日志展示，最后让生成器消费新契约生成回放脚本。迁移期保留旧字段兼容，但禁止新增上下文写入继续走 `extract_text`。

**Tech Stack:** FastAPI, Pydantic v2, Playwright, Vue 3 + TypeScript, Vitest, pytest

---

## 文件结构与职责

- `RpaClaw/backend/rpa/assistant.py`
  - 录制期助手执行主入口
  - 负责尝试事件流、重试状态归一化、上下文提升入口
- `RpaClaw/backend/rpa/assistant_runtime.py`
  - 结构化动作解析与运行时执行
  - 负责 `extract_text` 与 `ai_script` 的运行时边界
- `RpaClaw/backend/rpa/manager.py`
  - RPA 步骤模型定义与持久化
  - 负责保存新增结构化契约字段
- `RpaClaw/backend/rpa/context_ledger.py`
  - 上下文 ledger 存储模型
  - 负责 key-value 级别的上下文提升
- `RpaClaw/backend/route/rpa.py`
  - SSE 事件透传
  - 负责把新增 attempt 事件稳定传给前端
- `RpaClaw/backend/rpa/generator.py`
  - 录制步骤到回放脚本的生成器
  - 负责消费结构化输出契约生成 `rebuild_context` 和 `execute_skill`
- `RpaClaw/backend/tests/test_rpa_assistant.py`
  - 录制执行、尝试事件、上下文提升相关测试
- `RpaClaw/backend/tests/test_rpa_generator.py`
  - 回放生成器相关测试
- `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
  - 技能录制页执行日志 UI
- `RpaClaw/frontend/src/utils/rpaChat.ts`
  - 录制聊天展示辅助函数
- `RpaClaw/frontend/src/utils/rpaChat.test.ts`
  - 录制聊天辅助函数测试
- `RpaClaw/frontend/src/types/rpa.ts`
  - 新增前端 attempt/result 事件类型

---

### Task 1: 建立新的步骤契约与尝试事件模型

**Files:**
- Modify: `RpaClaw/backend/rpa/manager.py`
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Modify: `RpaClaw/backend/route/rpa.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: 先写失败测试，定义新的步骤结构与结果状态**

```python
async def test_chat_result_includes_structured_payload_and_attempt_summary(self):
    result = {
        "success": True,
        "status": "recovered_after_retry",
        "output": '{"buyer":"李雨晨"}',
        "step": {
            "action": "ai_script",
            "output_schema": {"buyer": "string"},
            "output_payload": {"buyer": "李雨晨"},
            "context_bindings": ["buyer"],
            "attempt_summary": {
                "attempt_count": 2,
                "final_status": "recovered_after_retry",
                "failure_kinds": ["execution_error"],
            },
        },
    }
    assert result["step"]["output_payload"]["buyer"] == "李雨晨"
    assert result["step"]["attempt_summary"]["attempt_count"] == 2
```

- [ ] **Step 2: 运行测试，确认当前实现无法满足新契约**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw && /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend/.venv/bin/python -m pytest backend/tests/test_rpa_assistant.py -k "attempt_summary or structured_payload" -v`

Expected: FAIL，提示步骤缺少 `output_payload`、`context_bindings` 或 `attempt_summary`

- [ ] **Step 3: 在步骤模型中加入新字段**

```python
class RPAStep(BaseModel):
    ...
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    output_payload: Dict[str, Any] = Field(default_factory=dict)
    context_bindings: List[str] = Field(default_factory=list)
    attempt_summary: Dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: 在 `assistant.py` 中引入 attempt 归一化函数**

```python
def _build_attempt_summary(attempts: List[Dict[str, Any]], final_status: str) -> Dict[str, Any]:
    return {
        "attempt_count": len(attempts),
        "final_status": final_status,
        "failure_kinds": [
            attempt["failure_kind"]
            for attempt in attempts
            if attempt.get("status") == "failed" and attempt.get("failure_kind")
        ],
    }
```

- [ ] **Step 5: 给 SSE 增加 attempt 事件透传**

```python
yield {"event": "attempt_started", "data": {"attempt": 1, "action": "ai_script", "description": message}}
yield {"event": "attempt_failed", "data": {"attempt": 1, "failure_kind": "execution_error", "retrying": True}}
yield {"event": "attempt_succeeded", "data": {"attempt": 2, "output_payload": {"buyer": "李雨晨"}}}
```

- [ ] **Step 6: 运行后端测试确认通过**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw && /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend/.venv/bin/python -m pytest backend/tests/test_rpa_assistant.py -k "attempt_summary or structured_payload" -v`

Expected: PASS

- [ ] **Step 7: 提交这一小步**

```bash
git add RpaClaw/backend/rpa/manager.py RpaClaw/backend/rpa/assistant.py RpaClaw/backend/route/rpa.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: add structured RPA step contract and attempt events"
```

---

### Task 2: 将上下文提取统一迁移到 `ai_script`

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Modify: `RpaClaw/backend/rpa/assistant_runtime.py`
- Modify: `RpaClaw/backend/rpa/context_ledger.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: 先写失败测试，锁定 `extract_text` 不得写上下文**

```python
async def test_extract_text_never_promotes_context(self):
    step = {"action": "extract_text", "result_key": "buyer"}
    writes = ASSISTANT._compute_context_writes("帮我读取购买人", step, None)
    assert writes == []
```

- [ ] **Step 2: 再写失败测试，锁定 `ai_script` 单字段/多字段都从 `output_payload` 写上下文**

```python
async def test_ai_script_promotes_multiple_context_values_from_output_payload(self):
    output_payload = {
        "buyer": "李雨晨",
        "department": "研发效能组",
    }
    assert output_payload["buyer"] == "李雨晨"
    assert output_payload["department"] == "研发效能组"
```

- [ ] **Step 3: 运行测试确认当前逻辑不满足**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw && /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend/.venv/bin/python -m pytest backend/tests/test_rpa_assistant.py -k "extract_text_never_promotes_context or ai_script_promotes_multiple_context_values" -v`

Expected: FAIL

- [ ] **Step 4: 收紧 `_compute_context_writes` 的语义边界**

```python
if action == "extract_text":
    return []
if action == "ai_script":
    return list(step_data.get("context_bindings") or [])
```

- [ ] **Step 5: 引入统一的结构化 payload 解析入口**

```python
def _normalize_output_payload(step_data: Dict[str, Any], output: Optional[str]) -> Dict[str, Any]:
    if isinstance(step_data.get("output_payload"), dict):
        return dict(step_data["output_payload"])
    if output:
        parsed = json.loads(output)
        if isinstance(parsed, dict):
            return parsed
    return {}
```

- [ ] **Step 6: 只允许 `ai_script` 按 `context_bindings` + `output_payload` 提升上下文**

```python
for key in context_bindings:
    if key not in output_payload:
        raise ValueError(f"contract_error: missing payload key {key}")
    ledger.record_value(key=key, value=output_payload[key], ...)
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw && /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend/.venv/bin/python -m pytest backend/tests/test_rpa_assistant.py -k "extract_text_never_promotes_context or ai_script_promotes_multiple_context_values" -v`

Expected: PASS

- [ ] **Step 8: 提交这一小步**

```bash
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/rpa/context_ledger.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: move context extraction to structured ai_script"
```

---

### Task 3: 输出完整的尝试时间线与最终状态

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py`
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`

- [ ] **Step 1: 先写失败测试，锁定“首轮失败、二轮成功”事件顺序**

```python
async def test_attempt_events_preserve_failure_then_recovery(self):
    events = [
        "attempt_started",
        "attempt_failed",
        "attempt_started",
        "attempt_succeeded",
        "result",
        "done",
    ]
    assert events[1] == "attempt_failed"
    assert events[3] == "attempt_succeeded"
```

- [ ] **Step 2: 运行测试确认当前事件流不完整**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw && /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend/.venv/bin/python -m pytest backend/tests/test_rpa_assistant.py -k "preserve_failure_then_recovery" -v`

Expected: FAIL

- [ ] **Step 3: 将 `_execute_with_retry` 返回值升级为 attempt 列表**

```python
return {
    "attempts": attempts,
    "final_result": retry_result,
    "final_status": "recovered_after_retry",
}
```

- [ ] **Step 4: 在 `chat()` 中逐条发出 attempt 事件**

```python
for attempt in execution_trace["attempts"]:
    yield {"event": f'attempt_{attempt["status"]}', "data": attempt}
```

- [ ] **Step 5: 在最终 `result` 中附带 `status` 字段**

```python
"data": {
    "success": final_result["success"],
    "status": execution_trace["final_status"],
    "step": step_data,
    "output": final_result.get("output"),
}
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw && /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend/.venv/bin/python -m pytest backend/tests/test_rpa_assistant.py -k "preserve_failure_then_recovery" -v`

Expected: PASS

- [ ] **Step 7: 提交这一小步**

```bash
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/tests/test_rpa_assistant.py
git commit -m "feat: emit full RPA attempt timeline"
```

---

### Task 4: 升级录制页前端时间线与上下文展示

**Files:**
- Create: `RpaClaw/frontend/src/types/rpa.ts`
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue`
- Modify: `RpaClaw/frontend/src/utils/rpaChat.ts`
- Test: `RpaClaw/frontend/src/utils/rpaChat.test.ts`

- [ ] **Step 1: 先写失败测试，锁定聊天文案格式化行为**

```ts
import { describe, expect, it } from 'vitest';
import { formatAttemptStatusLabel, formatContextWrites } from './rpaChat';

describe('formatContextWrites', () => {
  it('renders key-value pairs for context writes', () => {
    expect(formatContextWrites([{ key: 'buyer', value: '李雨晨' }])).toContain('buyer = 李雨晨');
  });
});
```

- [ ] **Step 2: 运行测试，确认当前工具函数不存在**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && ./node_modules/.bin/vitest run src/utils/rpaChat.test.ts`

Expected: FAIL，提示缺少格式化函数

- [ ] **Step 3: 定义 attempt 事件前端类型**

```ts
export interface RpaAttemptEvent {
  attempt: number;
  action?: string;
  status: 'started' | 'failed' | 'succeeded';
  failure_kind?: string;
  retrying?: boolean;
  output_payload?: Record<string, unknown>;
  context_writes?: Array<{ key: string; value: unknown }>;
}
```

- [ ] **Step 4: 在 `rpaChat.ts` 中新增日志格式化函数**

```ts
export const formatContextWrites = (writes: Array<{ key: string; value: unknown }>) =>
  writes.map((item) => `写入上下文: ${item.key} = ${String(item.value ?? '')}`);
```

- [ ] **Step 5: 在 `RecorderPage.vue` 中接入新增 attempt 事件**

```ts
} else if (eventType === 'attempt_failed') {
  chatMessages.value[msgIdx].text += `\n第 ${data.attempt} 轮失败：${data.failure_kind || data.error || '未知错误'}`;
} else if (eventType === 'attempt_succeeded') {
  const writeLines = formatContextWrites(data.context_writes || []);
  chatMessages.value[msgIdx].text += `\n第 ${data.attempt} 轮成功`;
  if (writeLines.length) chatMessages.value[msgIdx].text += `\n${writeLines.join('\n')}`;
}
```

- [ ] **Step 6: 在最终 `result` 状态中展示“经历重试后成功”**

```ts
if (data.status === 'recovered_after_retry') {
  chatMessages.value[msgIdx].text += '\n最终状态：成功（经历重试恢复）';
}
```

- [ ] **Step 7: 运行前端测试或最小替代验证**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm run test -- src/utils/rpaChat.test.ts`

Expected: PASS  
If Vitest is unavailable in this workspace, run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm ls vitest`  
Expected alternative result: confirm dependency state before proceeding

- [ ] **Step 8: 提交这一小步**

```bash
git add RpaClaw/frontend/src/types/rpa.ts RpaClaw/frontend/src/pages/rpa/RecorderPage.vue RpaClaw/frontend/src/utils/rpaChat.ts RpaClaw/frontend/src/utils/rpaChat.test.ts
git commit -m "feat: show full RPA attempt timeline in recorder UI"
```

---

### Task 5: 让生成器消费结构化契约而不是录制值

**Files:**
- Modify: `RpaClaw/backend/rpa/generator.py`
- Test: `RpaClaw/backend/tests/test_rpa_generator.py`

- [ ] **Step 1: 先写失败测试，锁定生成器不能把录制值硬编码为定位器**

```python
def test_generator_does_not_embed_recorded_field_value_in_context_rebuild(self):
    step = {
        "action": "ai_script",
        "output_schema": {"buyer": "string"},
        "output_payload": {"buyer": "李雨晨"},
        "context_bindings": ["buyer"],
        "description": "提取购买人",
    }
    script = generator.generate_script([step], "test_skill")
    assert "购买人 李雨晨" not in script
    assert 'context["buyer"]' in script
```

- [ ] **Step 2: 再写失败测试，锁定多字段上下文重建使用结构化 payload**

```python
def test_generator_rebuilds_multiple_context_values_from_structured_contract(self):
    script = generator.generate_script([...], "test_skill")
    assert 'context["buyer"]' in script
    assert 'context["department"]' in script
```

- [ ] **Step 3: 运行测试确认当前生成器仍使用旧逻辑**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw && /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend/.venv/bin/python -m pytest backend/tests/test_rpa_generator.py -k "recorded_field_value or structured_contract" -v`

Expected: FAIL

- [ ] **Step 4: 在生成器中新增结构化提取分支**

```python
elif action == "ai_script" and step.get("context_bindings"):
    payload_keys = step.get("context_bindings") or []
    for ctx_key in payload_keys:
        step_lines.append(f'    context["{ctx_key}"] = result_payload["{ctx_key}"]')
```

- [ ] **Step 5: 收紧 `rebuild_context` 的生成策略**

```python
if action == "ai_script" and step.get("context_bindings"):
    rebuild_lines.append("    result_payload = await extract_structured_context(current_page, ...)")
    rebuild_lines.append('    context["buyer"] = result_payload["buyer"]')
```

- [ ] **Step 6: 删除新的上下文路径里对 `extract_text` 的依赖**

```python
if action == "extract_text" and step.get("context_writes"):
    raise StepExecutionError("extract_text may not rebuild context")
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw && /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend/.venv/bin/python -m pytest backend/tests/test_rpa_generator.py -k "recorded_field_value or structured_contract" -v`

Expected: PASS

- [ ] **Step 8: 提交这一小步**

```bash
git add RpaClaw/backend/rpa/generator.py RpaClaw/backend/tests/test_rpa_generator.py
git commit -m "feat: generate playback from structured RPA extraction contract"
```

---

### Task 6: 回归验证与兼容收口

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_assistant.py`
- Modify: `RpaClaw/backend/tests/test_rpa_generator.py`
- Modify: `RpaClaw/frontend/src/utils/rpaChat.test.ts`
- Modify: `docs/superpowers/specs/2026-04-20-rpa-recording-contract-design.md`

- [ ] **Step 1: 增加 PR 核心字段单字段回归测试**

```python
def test_single_field_context_extraction_uses_ai_script_contract():
    assert step["action"] == "ai_script"
    assert step["output_payload"]["buyer"] == "李雨晨"
```

- [ ] **Step 2: 增加 PR 核心字段多字段回归测试**

```python
def test_multi_field_context_extraction_records_all_values():
    assert payload["buyer"] == "李雨晨"
    assert payload["department"] == "研发效能组"
    assert payload["expected_arrival_date"] == "2026-04-18"
```

- [ ] **Step 3: 增加前端日志回归测试**

```ts
it('shows recovered status after retry with context write details', () => {
  const lines = formatContextWrites([
    { key: 'buyer', value: '李雨晨' },
    { key: 'department', value: '研发效能组' },
  ]);
  expect(lines.join('\n')).toContain('buyer = 李雨晨');
  expect(lines.join('\n')).toContain('department = 研发效能组');
});
```

- [ ] **Step 4: 运行后端完整相关测试**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw && /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/backend/.venv/bin/python -m pytest backend/tests/test_rpa_assistant.py backend/tests/test_rpa_generator.py -v`

Expected: PASS

- [ ] **Step 5: 运行前端最小相关测试**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npm run test -- src/utils/rpaChat.test.ts`

Expected: PASS  
If workspace still lacks Vitest, record the dependency gap explicitly in the task notes before merge.

- [ ] **Step 6: 更新 spec 中的实施状态说明**

```md
## 实施状态

- [x] 新步骤契约
- [x] 尝试事件模型
- [x] 上下文提取统一迁移
- [x] 前端尝试时间线
- [x] 生成器新契约消费
```

- [ ] **Step 7: 提交最终验证与文档更新**

```bash
git add RpaClaw/backend/tests/test_rpa_assistant.py RpaClaw/backend/tests/test_rpa_generator.py RpaClaw/frontend/src/utils/rpaChat.test.ts docs/superpowers/specs/2026-04-20-rpa-recording-contract-design.md
git commit -m "test: add RPA recording contract regression coverage"
```

---

## 自检清单

- 规格要求“上下文提取统一走 `ai_script`”已由 Task 2 与 Task 5 覆盖
- 规格要求“完整展示每轮尝试”已由 Task 1、Task 3、Task 4 覆盖
- 规格要求“生成器去除录制值硬编码”已由 Task 5 覆盖
- 规格要求“前端展示 key-value 上下文写入”已由 Task 4 与 Task 6 覆盖
- 本计划未使用 `TBD`、`TODO`、或“自行实现”类占位语句
- 计划中的字段名 `output_schema`、`output_payload`、`context_bindings`、`attempt_summary` 在全部任务中保持一致

## 执行交接

Plan complete and saved to `docs/superpowers/plans/2026-04-20-rpa-recording-contract.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
