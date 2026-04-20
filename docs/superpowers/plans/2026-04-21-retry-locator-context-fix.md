# 重试/Locator/生成脚本硬编码 修复

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复多 action 提取时 locator 严格模式冲突无容错、失败后无重试、导出脚本值硬编码三个问题

**Architecture:** 三层修复：(1) `execute_structured_intent` 加 `.first` 兜底防严格模式冲突 (2) `process_message` 多 action 循环中为失败 intent 加 LLM 重试 (3) `generator.py` 的 `_dehardcode_ai_script` 替换硬编码值（已实现，需验证集成）

**Tech Stack:** Python 3.13 (FastAPI), Playwright async API

---

## 已完成（前序 commit 中的改动）

以下改动已在上一次 commit 中完成，后续 task 不需要重复：

- `assistant_snapshot_runtime.py`：content node 检测 `data-field` 属性，生成 CSS locator `[data-field="xxx"]`，标注 `field_name`
- `assistant_runtime.py`：`_content_node_score` 加入 `field_name` 匹配和 +4 加分
- `generator.py`：`_dehardcode_ai_script` 方法已添加并在 ai_script 步骤生成中调用

---

### Task 1: Locator `.first` 容错

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_runtime.py:934-941`

**背景:** `execute_structured_intent` 中 `locator.inner_text()` 遇到严格模式冲突（多个元素匹配同文本）直接抛异常。加 `.first` 兜底取第一个匹配元素。

- [ ] **Step 1: 修改 `execute_structured_intent`**

在 `assistant_runtime.py` 找到（约 L934-941）：

```python
    locator_payload = resolved["locator"]
    locator = _locator_from_payload(scope, locator_payload)
    if action == "click":
        await locator.click()
    elif action == "extract_text":
        output = await locator.inner_text()
    elif action == "fill":
        await locator.fill(intent.get("value", ""))
    elif action == "press":
        await locator.press(intent.get("value", "Enter"))
    else:
        raise ValueError(f"Unsupported action: {action}")
```

替换为：

```python
    locator_payload = resolved["locator"]
    locator = _locator_from_payload(scope, locator_payload)
    if action == "click":
        await locator.first.click()
    elif action == "extract_text":
        output = await locator.first.inner_text()
    elif action == "fill":
        await locator.first.fill(intent.get("value", ""))
    elif action == "press":
        await locator.first.press(intent.get("value", "Enter"))
    else:
        raise ValueError(f"Unsupported action: {action}")
```

- [ ] **Step 2: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "
import ast
with open('RpaClaw/backend/rpa/assistant_runtime.py', 'r', encoding='utf-8-sig') as f:
    ast.parse(f.read())
print('OK')
"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/rpa/assistant_runtime.py
git commit -m "fix: add .first to locator calls to prevent strict mode violations"
```

---

### Task 2: 多 action 循环内单 intent LLM 重试

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py:339-402`

**背景:** 当前多 action 循环中单个 intent 失败后只记录错误继续下一个。需要在 locator 容错仍失败时，对该 intent 单独发一次 LLM 重试。

**关键约束：**
- `_execute_single_response` 是实例方法，可直接调用
- `_stream_llm` 也是实例方法，用于获取 LLM 重试响应
- `model_config` 来自 `process_message` 参数
- 重试只需要 1 次，不需要多轮

- [ ] **Step 1: 提取 intent 执行+ledger 写入为辅助方法**

在 `RPAAssistant` 类中，`_execute_single_response` 方法之后（约 L690），添加：

```python
    async def _execute_intent_with_ledger(
        self,
        intent_json: Dict[str, Any],
        current_page: Page,
        snapshot: Dict[str, Any],
        context_ledger: Optional[Any],
        message: str,
        rpa_manager: Any,
        session_id: str,
    ) -> tuple[Dict[str, Any], List[str]]:
        """Execute a single intent and promote context writes to ledger.

        Returns (result, context_reads).
        """
        intent_response = json.dumps(intent_json, ensure_ascii=False)
        result, _, resolution, intent_reads = await self._execute_single_response(
            current_page, snapshot, intent_response, context_ledger
        )
        if result.get("success") and resolution:
            step_data = result.get("step")
            if step_data:
                context_writes = self._compute_context_writes(
                    message=message, step_data=step_data, resolution=resolution,
                )
                self._promote_to_ledger(
                    rpa_manager=rpa_manager, session_id=session_id,
                    context_writes=context_writes, step_data=step_data,
                    output=result.get("output"),
                )
                step_data["context_reads"] = intent_reads
                step_data["context_writes"] = context_writes
                await rpa_manager.add_step(session_id, step_data)
        return result, intent_reads
```

- [ ] **Step 2: 重写多 action 首次执行循环**

在 `process_message` 中找到多 action 首次执行块（约 L341-402）。将整个 `if len(intents) > 1:` 块替换为：

```python
            if len(intents) > 1:
                # ── Multi-action: execute each intent with per-intent retry ──
                multi_results = []
                for i, intent_json in enumerate(intents):
                    # --- First attempt ---
                    try:
                        intent_result, intent_reads = await self._execute_intent_with_ledger(
                            intent_json, current_page, snapshot, context_ledger,
                            message, rpa_manager, session_id,
                        )
                        multi_results.append(intent_result)
                        context_reads.extend(intent_reads)
                    except Exception as exc:
                        intent_result = {"success": False, "error": str(exc), "output": ""}
                        multi_results.append(intent_result)

                    # --- Per-intent retry if failed ---
                    if not intent_result.get("success"):
                        intent_error = intent_result.get("error", "")
                        yield {
                            "event": "retry_start",
                            "data": {"original_error": intent_error, "intent_index": i},
                        }
                        retry_prompt = (
                            f"Original intent:\n{json.dumps(intent_json, ensure_ascii=False)}\n\n"
                            f"Execution error: {intent_error}\n\n"
                            f"Please fix the intent and output a single corrected JSON action."
                        )
                        retry_messages = [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": retry_prompt},
                        ]
                        retry_response = ""
                        async for chunk_text in self._stream_llm(retry_messages, model_config):
                            retry_response += chunk_text
                            yield {"event": "retry_chunk", "data": {"text": chunk_text, "intent_index": i}}

                        yield {"event": "retry_executing", "data": {"intent_index": i}}

                        # Parse the retry response as a single intent
                        retry_intent = self._extract_structured_intent(retry_response)
                        if retry_intent:
                            try:
                                retry_result, retry_reads = await self._execute_intent_with_ledger(
                                    retry_intent, current_page, snapshot, context_ledger,
                                    message, rpa_manager, session_id,
                                )
                                # Replace the failed result with retry result
                                multi_results[-1] = retry_result
                                context_reads.extend(retry_reads)
                            except Exception as exc2:
                                multi_results[-1] = {"success": False, "error": str(exc2), "output": ""}
                        else:
                            multi_results[-1] = {
                                "success": False,
                                "error": f"Retry failed: could not parse LLM response. Original: {intent_error}",
                                "output": "",
                            }

                        yield {
                            "event": "retry_result",
                            "data": {
                                "success": multi_results[-1].get("success", False),
                                "error": multi_results[-1].get("error"),
                                "intent_index": i,
                            },
                        }

                # Aggregate results
                result = multi_results[-1] if multi_results else {"success": False, "error": "No actions executed", "output": ""}
                code = None
                resolution = None

                all_success = all(r.get("success") for r in multi_results)
                result["success"] = all_success
                if not all_success:
                    errors = [r.get("error", "") for r in multi_results if not r.get("success")]
                    result["error"] = "; ".join(errors)

                history.append({"role": "user", "content": message})
                history.append({"role": "assistant", "content": full_response})
                self._trim_history(session_id)

                yield {
                    "event": "result",
                    "data": {
                        "success": result["success"],
                        "error": result.get("error"),
                        "step": None,
                        "output": result.get("output"),
                        "context_reads": context_reads,
                        "context_writes": [],
                        "retried": any("retry" in str(r.get("error", "")) for r in multi_results if not r.get("success")),
                        "original_error": None,
                        "multi_action_count": len(intents),
                    },
                }
                yield {"event": "done", "data": {}}
                return
```

- [ ] **Step 3: 同步更新重试路径中的多 action 块**

在 `process_message` 的重试路径中（`# ── 首次失败则重试` 后面），找到 `# ── Retry multi-action ──` 块（约 L444-509）。将其替换为复用 `_execute_intent_with_ledger`：

```python
                if len(retry_intents) > 1:
                    # ── Retry multi-action ──
                    multi_results = []
                    for i, intent_json in enumerate(retry_intents):
                        try:
                            intent_result, intent_reads = await self._execute_intent_with_ledger(
                                intent_json, current_page, retry_snapshot, context_ledger,
                                message, rpa_manager, session_id,
                            )
                            multi_results.append(intent_result)
                            context_reads.extend(intent_reads)
                        except Exception as exc:
                            multi_results.append({"success": False, "error": str(exc), "output": ""})

                        # Per-intent retry within retry path
                        if not intent_result.get("success"):
                            intent_error = intent_result.get("error", "")
                            retry_prompt = (
                                f"Original intent:\n{json.dumps(intent_json, ensure_ascii=False)}\n\n"
                                f"Execution error: {intent_error}\n\n"
                                f"Please fix the intent and output a single corrected JSON action."
                            )
                            retry_llm_msgs = [
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": retry_prompt},
                            ]
                            rr = ""
                            async for chunk_text in self._stream_llm(retry_llm_msgs, model_config):
                                rr += chunk_text
                                yield {"event": "retry_chunk", "data": {"text": chunk_text, "intent_index": i}}
                            retry_intent = self._extract_structured_intent(rr)
                            if retry_intent:
                                try:
                                    retry_result, retry_reads = await self._execute_intent_with_ledger(
                                        retry_intent, current_page, retry_snapshot, context_ledger,
                                        message, rpa_manager, session_id,
                                    )
                                    multi_results[-1] = retry_result
                                    context_reads.extend(retry_reads)
                                except Exception as exc2:
                                    multi_results[-1] = {"success": False, "error": str(exc2), "output": ""}

                    result = multi_results[-1] if multi_results else {"success": False, "error": "No actions executed", "output": ""}
                    code = None
                    resolution = None
                    all_success = all(r.get("success") for r in multi_results)
                    result["success"] = all_success
                    if not all_success:
                        errors = [r.get("error", "") for r in multi_results if not r.get("success")]
                        result["error"] = "; ".join(errors)

                    full_response = retry_response
                    yield {
                        "event": "retry_result",
                        "data": {
                            "success": result.get("success", False),
                            "error": result.get("error"),
                            "multi_action_count": len(retry_intents),
                        },
                    }
                    history.append({"role": "user", "content": message})
                    history.append({"role": "assistant", "content": full_response})
                    self._trim_history(session_id)
                    yield {
                        "event": "result",
                        "data": {
                            "success": result["success"],
                            "error": result.get("error"),
                            "step": None,
                            "output": result.get("output"),
                            "context_reads": context_reads,
                            "context_writes": [],
                            "retried": True,
                            "original_error": original_error,
                            "multi_action_count": len(retry_intents),
                        },
                    }
                    yield {"event": "done", "data": {}}
                    return
```

- [ ] **Step 4: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "
import ast
with open('RpaClaw/backend/rpa/assistant.py', 'r', encoding='utf-8-sig') as f:
    ast.parse(f.read())
print('OK')
"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/assistant.py
git commit -m "feat: add per-intent LLM retry in multi-action execution path"
```

---

### Task 3: 验证生成脚本硬编码修复

**Files:**
- Modify: `RpaClaw/backend/rpa/generator.py`（验证已有的 `_dehardcode_ai_script` 集成）

**背景:** `_dehardcode_ai_script` 方法已在前序 commit 中添加并集成到 ai_script 步骤生成。此 task 确认集成正确。

- [ ] **Step 1: 验证 `_dehardcode_ai_script` 已在 ai_script 步骤中调用**

在 `generator.py` 中确认 `ai_script` 处理块包含 `converted = self._dehardcode_ai_script(converted, context_value_map)` 调用。

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && grep -n "_dehardcode_ai_script" RpaClaw/backend/rpa/generator.py`
Expected: 至少出现方法定义和调用两处

- [ ] **Step 2: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "
import ast
with open('RpaClaw/backend/rpa/generator.py', 'r', encoding='utf-8-sig') as f:
    ast.parse(f.read())
print('OK')
"`
Expected: `OK`

- [ ] **Step 3: Commit（如有修改）**

仅当 Step 1 发现集成缺失时才需要修复并 commit。

---

## Self-Review

**Spec coverage:**
- 问题 1（生成脚本硬编码）：Task 3 验证 ✓
- 问题 2 locator 严格模式冲突：Task 1 `.first` + 前序 commit CSS locator ✓
- 问题 2 无重试：Task 2 单 intent 重试 ✓

**Placeholder scan:** 无 TBD/TODO，所有步骤含完整代码。

**Type consistency：** `_execute_intent_with_ledger` 返回 `tuple[Dict, List[str]]`，与各调用点的解包 `(result, reads)` 一致。`_execute_single_response` 返回 4-tuple，辅助方法只返回 2-tuple，不冲突。
