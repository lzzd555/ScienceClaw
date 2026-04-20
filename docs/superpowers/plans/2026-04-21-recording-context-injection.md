# 录制态 Context Dict 注入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在录制态 AI 代码执行时注入 context dict，使 LLM 生成的 `context.get()` 代码在录制态和生成态都能正确运行。

**Architecture:** 从 `context_ledger` 构建 context dict 注入到 `_execute_on_page` 的 exec 命名空间。执行后比对 context 前后差异，将新增 key 同步回 context_ledger。同步更新 SYSTEM_PROMPT 告知 LLM 使用 context API。

**Tech Stack:** Python 3.13, FastAPI, Playwright async API

---

### Task 1: 修改 `_execute_on_page` 接受并注入 context dict

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py:122-145`（模块级函数）
- Modify: `RpaClaw/backend/rpa/assistant.py:1000-1001`（实例方法代理）

**背景:** `_execute_on_page` 是模块级函数（L122），同时有一个实例方法代理（L1000）。两个都需要更新签名和逻辑。

- [ ] **Step 1: 修改模块级 `_execute_on_page` 函数**

在 `assistant.py` 找到（约 L122-145）：

```python
async def _execute_on_page(page: Page, code: str) -> Dict[str, Any]:
    """Execute AI-generated code directly on the page object."""
    try:
        await page.evaluate("window.__rpa_paused = true")
    except Exception:
        pass
    try:
        namespace: Dict[str, Any] = {"page": page}
        exec(compile(code, "<rpa_assistant>", "exec"), namespace)
        if "run" in namespace and callable(namespace["run"]):
            ret = await asyncio.wait_for(namespace["run"](page), timeout=EXECUTION_TIMEOUT_S)
            return {"success": True, "output": str(ret) if ret else "ok", "error": None}
        else:
            return {"success": False, "output": "", "error": "No run(page) function defined"}
    except asyncio.TimeoutError:
        return {"success": False, "output": "", "error": f"Command execution timed out ({EXECUTION_TIMEOUT_S:.0f}s)"}
    except Exception:
        import traceback
        return {"success": False, "output": "", "error": traceback.format_exc()}
    finally:
        try:
            await page.evaluate("window.__rpa_paused = false")
        except Exception:
            pass
```

替换为：

```python
async def _execute_on_page(
    page: Page, code: str, context: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Execute AI-generated code directly on the page object.

    Parameters
    ----------
    context : dict, optional
        A read-write context dict built from the session's context_ledger.
        Injected into the exec namespace so LLM-generated code can use
        ``context.get("key")`` to read previous-step values and
        ``context["key"] = value`` to write new values.
    """
    ctx = dict(context) if context else {}
    try:
        await page.evaluate("window.__rpa_paused = true")
    except Exception:
        pass
    try:
        namespace: Dict[str, Any] = {"page": page, "context": ctx}
        exec(compile(code, "<rpa_assistant>", "exec"), namespace)
        if "run" in namespace and callable(namespace["run"]):
            ret = await asyncio.wait_for(namespace["run"](page), timeout=EXECUTION_TIMEOUT_S)
            return {"success": True, "output": str(ret) if ret else "ok", "error": None, "context": ctx}
        else:
            return {"success": False, "output": "", "error": "No run(page) function defined", "context": ctx}
    except asyncio.TimeoutError:
        return {"success": False, "output": "", "error": f"Command execution timed out ({EXECUTION_TIMEOUT_S:.0f}s)", "context": ctx}
    except Exception:
        import traceback
        return {"success": False, "output": "", "error": traceback.format_exc(), "context": ctx}
    finally:
        try:
            await page.evaluate("window.__rpa_paused = false")
        except Exception:
            pass
```

关键点：
- `ctx = dict(context) if context else {}` — 浅拷贝，避免修改原始 dict
- `namespace = {"page": page, "context": ctx}` — 注入到 exec 命名空间
- 所有返回路径都包含 `"context": ctx`，以便调用方读取执行后的 context 状态

- [ ] **Step 2: 修改实例方法代理**

在 `assistant.py` 找到（约 L1000-1001）：

```python
    async def _execute_on_page(self, page: Page, code: str) -> Dict[str, Any]:
        return await _execute_on_page(page, code)
```

替换为：

```python
    async def _execute_on_page(self, page: Page, code: str, context: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return await _execute_on_page(page, code, context)
```

- [ ] **Step 3: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "
import ast
with open('RpaClaw/backend/rpa/assistant.py', 'r', encoding='utf-8-sig') as f:
    ast.parse(f.read())
print('OK')
"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/backend/rpa/assistant.py
git commit -m "refactor: accept and inject context dict in _execute_on_page"
```

---

### Task 2: 修改 `_execute_single_response` 构建 context 并同步回写

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py:764-787`

**背景:** `_execute_single_response` 是 AI 代码执行的入口。需要在 AI script 分支中：构建 context dict、传给 `_execute_on_page`、执行后比对 diff 并同步回 context_ledger。

- [ ] **Step 1: 修改 `_execute_single_response`**

在 `assistant.py` 找到（约 L764-787）：

```python
    async def _execute_single_response(
        self,
        current_page: Page,
        snapshot: Dict[str, Any],
        full_response: str,
        context_ledger: Optional[Any] = None,
    ) -> tuple[Dict[str, Any], Optional[str], Optional[Dict[str, Any]], List[str]]:
        """Execute a single LLM response.

        Returns (result, code, resolution, context_reads).
        """
        structured_intent = self._extract_structured_intent(full_response)
        if structured_intent:
            # Resolve ${key} references in the intent using context ledger
            context_reads = self._resolve_context_in_intent(structured_intent, context_ledger)
            resolved_intent = resolve_structured_intent(snapshot, structured_intent)
            result = await execute_structured_intent(current_page, resolved_intent)
            return result, None, resolved_intent, context_reads

        code = self._extract_code(full_response)
        if not code:
            raise ValueError("Unable to extract structured intent or executable code from assistant response")
        result = await self._execute_on_page(current_page, code)
        return result, code, None, []
```

替换为：

```python
    async def _execute_single_response(
        self,
        current_page: Page,
        snapshot: Dict[str, Any],
        full_response: str,
        context_ledger: Optional[Any] = None,
    ) -> tuple[Dict[str, Any], Optional[str], Optional[Dict[str, Any]], List[str], List[str]]:
        """Execute a single LLM response.

        Returns (result, code, resolution, context_reads, ai_context_writes).
        """
        structured_intent = self._extract_structured_intent(full_response)
        if structured_intent:
            # Resolve ${key} references in the intent using context ledger
            context_reads = self._resolve_context_in_intent(structured_intent, context_ledger)
            resolved_intent = resolve_structured_intent(snapshot, structured_intent)
            result = await execute_structured_intent(current_page, resolved_intent)
            return result, None, resolved_intent, context_reads, []

        code = self._extract_code(full_response)
        if not code:
            raise ValueError("Unable to extract structured intent or executable code from assistant response")

        # Build context dict from ledger for AI script execution
        pre_context = self._build_context_from_ledger(context_ledger)
        result = await self._execute_on_page(current_page, code, pre_context)

        # Detect new keys written by the AI script
        post_context = result.get("context", {})
        ai_context_writes = [k for k in post_context if k not in pre_context]

        # Sync new values back to context_ledger
        if ai_context_writes and context_ledger is not None:
            for key in ai_context_writes:
                val = post_context[key]
                context_ledger.observed_values[key] = ContextValue(
                    key=key, value=val, source_kind="ai_script"
                )

        return result, code, None, [], ai_context_writes
```

关键点：
- 返回类型从 4-tuple 变为 5-tuple，新增 `ai_context_writes`
- `_build_context_from_ledger` 是新辅助方法（Task 2 Step 2 添加）
- `ContextValue` 需要在文件顶部导入（Task 2 Step 3）
- 结构化 intent 分支返回 `[]` 作为 `ai_context_writes`（不需要）

- [ ] **Step 2: 添加 `_build_context_from_ledger` 静态方法**

在 `_resolve_context_in_intent` 方法之前（约 L930），添加：

```python
    @staticmethod
    def _build_context_from_ledger(context_ledger: Any) -> Dict[str, str]:
        """Build a flat context dict from the session's context ledger."""
        if context_ledger is None:
            return {}
        ctx: Dict[str, str] = {}
        for key, entry in context_ledger.observed_values.items():
            if entry.value is not None:
                ctx[key] = str(entry.value)
        for key, entry in context_ledger.derived_values.items():
            if entry.value is not None:
                ctx[key] = str(entry.value)
        return ctx
```

- [ ] **Step 3: 添加 `ContextValue` 导入**

在 `assistant.py` 文件顶部的 import 区域，添加：

```python
from backend.rpa.context_ledger import ContextValue
```

检查现有导入是否已有 `context_ledger` 相关导入：

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && grep -n "context_ledger" RpaClaw/backend/rpa/assistant.py | head -5`

如果已有 `from backend.rpa.context_ledger import ...`，在其后追加 `ContextValue`。如果没有，在合适位置（如其他 backend.rpa 导入附近）添加新行。

- [ ] **Step 4: 更新所有 `_execute_single_response` 调用点**

`_execute_single_response` 的返回值从 4-tuple 变为 5-tuple。需要更新所有调用点：

**调用点 1 — 单 action 路径（约 L442-449）：**

找到：
```python
                    result, code, resolution, context_reads = await self._execute_single_response(
                        current_page, snapshot, full_response, context_ledger
                    )
```
替换为：
```python
                    result, code, resolution, context_reads, _ai_writes = await self._execute_single_response(
                        current_page, snapshot, full_response, context_ledger
                    )
```

**调用点 2 — 重试单 action 路径（约 L578-581）：**

找到：
```python
                        result, code, resolution, context_reads = await self._execute_single_response(
                            current_page, retry_snapshot, retry_response, context_ledger
                        )
```
替换为：
```python
                        result, code, resolution, context_reads, _ai_writes = await self._execute_single_response(
                            current_page, retry_snapshot, retry_response, context_ledger
                        )
```

注意：`_execute_intent_with_ledger` 也调用了 `_execute_single_response`（约 L745）：

找到：
```python
            result, _, resolution, intent_reads = await self._execute_single_response(
                current_page, snapshot, intent_response, context_ledger
            )
```
替换为：
```python
            result, _, resolution, intent_reads, _ai_writes = await self._execute_single_response(
                current_page, snapshot, intent_response, context_ledger
            )
```

- [ ] **Step 5: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "
import ast
with open('RpaClaw/backend/rpa/assistant.py', 'r', encoding='utf-8-sig') as f:
    ast.parse(f.read())
print('OK')
"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/assistant.py
git commit -m "feat: build context from ledger in _execute_single_response and sync writes back"
```

---

### Task 3: 更新 SYSTEM_PROMPT 告知 LLM 使用 context API

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py:68-103`

**背景:** LLM 需要知道命名空间中有 `context` 可用，否则可能仍然使用 `globals()` 或硬编码值。

- [ ] **Step 1: 在 SYSTEM_PROMPT 的 Rules 末尾添加新规则**

在 `assistant.py` 找到 SYSTEM_PROMPT（约 L68-103），在现有规则 8 的 JSON 数组示例之后、闭合 `"""` 之前，添加：

```
9. When writing Python code, you have access to a `context` dict containing values extracted in previous steps. Use `context.get("key", "default")` to read previous values, and `context["key"] = value` to store new values. Do NOT use globals() or hardcoded values for data that comes from previous steps. The function signature remains `async def run(page)`.
```

具体地，找到这段（约 L98-102）：
```python
  {"action": "extract_text", "description": "Extract supplier", "prompt": "提取购买人、使用部门和供应商", "result_key": "supplier", "target_hint": {"name": "供应商"}}
]
"""
```

替换为：
```python
  {"action": "extract_text", "description": "Extract supplier", "prompt": "提取购买人、使用部门和供应商", "result_key": "supplier", "target_hint": {"name": "供应商"}}
]
9. When writing Python code, you have access to a `context` dict containing values extracted in previous steps. Use `context.get("key", "default")` to read previous values, and `context["key"] = value` to store new values. Do NOT use globals() or hardcoded values for data that comes from previous steps. The function signature remains `async def run(page)`.
"""
```

- [ ] **Step 2: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "
import ast
with open('RpaClaw/backend/rpa/assistant.py', 'r', encoding='utf-8-sig') as f:
    ast.parse(f.read())
print('OK')
"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/rpa/assistant.py
git commit -m "feat: update SYSTEM_PROMPT to inform LLM about context dict availability"
```

---

### Task 4: 单 action 路径中 AI 代码 context_writes 处理

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py:604-639`

**背景:** AI script 执行成功后，如果代码通过 `context["key"] = value` 写入了新值，这些值需要被纳入 `context_writes` 以便 `_promote_to_ledger` 和 generator 使用。当前 `_compute_context_writes` 只处理 `extract_text` 结构化 intent，不处理 `ai_script`。

- [ ] **Step 1: 在单 action 路径中合并 ai_context_writes**

在 `assistant.py` 找到（约 L604-639）：

```python
        step_data = None
        if result["success"]:
            if result.get("step"):
                step_data = result["step"]
            elif code:
                body = self._extract_function_body(code)
                step_data = {
                    "action": "ai_script",
                    "source": "ai",
                    "value": body,
                    "description": message,
                    "prompt": message,
                }

        # ── Compute context reads / writes ─────────────────────────
        context_writes: List[str] = []

        if result["success"] and step_data:
            context_writes = self._compute_context_writes(
                message=message,
                step_data=step_data,
                resolution=resolution,
            )

            # Persist promoted values into the session ledger
            self._promote_to_ledger(
                rpa_manager=rpa_manager,
                session_id=session_id,
                context_writes=context_writes,
                step_data=step_data,
                output=result.get("output"),
            )

        # Attach context lists to the step payload for downstream use
        if step_data is not None:
            step_data["context_reads"] = context_reads
            step_data["context_writes"] = context_writes
```

替换为：

```python
        step_data = None
        ai_context_writes: List[str] = []
        if result["success"]:
            if result.get("step"):
                step_data = result["step"]
            elif code:
                body = self._extract_function_body(code)
                step_data = {
                    "action": "ai_script",
                    "source": "ai",
                    "value": body,
                    "description": message,
                    "prompt": message,
                }
            # Collect new context keys written by AI script code
            ai_context_writes = list(result.get("context_writes_from_ai", []))

        # ── Compute context reads / writes ─────────────────────────
        context_writes: List[str] = []

        if result["success"] and step_data:
            context_writes = self._compute_context_writes(
                message=message,
                step_data=step_data,
                resolution=resolution,
            )
            # Merge AI script writes (context["key"] = value in code)
            for kw in ai_context_writes:
                if kw not in context_writes:
                    context_writes.append(kw)

            # Persist promoted values into the session ledger
            self._promote_to_ledger(
                rpa_manager=rpa_manager,
                session_id=session_id,
                context_writes=context_writes,
                step_data=step_data,
                output=result.get("output"),
            )

        # Attach context lists to the step payload for downstream use
        if step_data is not None:
            step_data["context_reads"] = context_reads
            step_data["context_writes"] = context_writes
```

注意：这里需要从上游（`_execute_single_response` 的返回值）获取 `ai_context_writes`。在 Task 2 Step 4 中，单 action 路径的调用点已经用 `_ai_writes` 接收了这个值。但这个变量名以 `_` 开头表示忽略。我们需要改为实际使用它。

回到 Task 2 Step 4 的调用点 1，修正为：

```python
                    result, code, resolution, context_reads, ai_context_writes_single = await self._execute_single_response(
                        current_page, snapshot, full_response, context_ledger
                    )
```

然后在 step_data 赋值区域上方添加传递：

```python
        ai_context_writes: List[str] = []
        if not result.get("success") and 'ai_context_writes_single' in dir():
            pass  # failed, no ai writes
        elif result.get("success") and 'ai_context_writes_single' in dir():
            ai_context_writes = ai_context_writes_single
```

**更清晰的方案**：不用局部变量传递，而是将 `ai_context_writes` 存入 `result` dict。回到 Task 2，在 `_execute_single_response` 的 AI script 分支中，将 `ai_context_writes` 也存入 result：

在 Task 2 Step 1 的代码中，`return result, code, None, [], ai_context_writes` 之前添加：

```python
        result["context_writes_from_ai"] = ai_context_writes
```

这样在 Task 4 中可以直接从 result 读取：

```python
            ai_context_writes = list(result.get("context_writes_from_ai", []))
```

这避免了跨调用点传递变量的复杂性。Task 2 Step 1 的代码已包含此行。

- [ ] **Step 2: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "
import ast
with open('RpaClaw/backend/rpa/assistant.py', 'r', encoding='utf-8-sig') as f:
    ast.parse(f.read())
print('OK')
"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/rpa/assistant.py
git commit -m "feat: merge AI script context writes into step context_writes"
```

---

## Self-Review

**Spec coverage:**
- 注入 context dict 到 exec 命名空间：Task 1 ✓
- 从 context_ledger 构建 context：Task 2 Step 2 ✓
- 执行后同步回 context_ledger：Task 2 Step 1 ✓
- 更新 SYSTEM_PROMPT：Task 3 ✓
- AI 代码 context_writes 处理：Task 4 ✓
- `_dehardcode_ai_script` 保留为兜底：不改 ✓

**Placeholder scan:** 无 TBD/TODO，所有步骤含完整代码。

**Type consistency:**
- `_execute_on_page` 签名：`(Page, str, Optional[Dict[str, str]])` → `Dict[str, Any]`
- `_execute_single_response` 返回：5-tuple，第 5 项 `List[str]`（ai_context_writes）
- `_build_context_from_ledger`：`(Any) → Dict[str, str]`
- 所有调用点的解包变量名一致
- `ContextValue` 从 `context_ledger.py` 导入，与 `TaskContextLedger.observed_values` dict 的 value 类型一致
