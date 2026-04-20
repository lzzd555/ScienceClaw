# 录制态 Context Dict 注入

日期: 2026-04-21

## 概述

录制态 AI 生成的 Python 代码执行时，命名空间中只有 `page`，无法访问之前步骤提取的上下文值。LLM 不得不使用 `globals().get(...)` 绕过，导致生成的脚本中出现错误的上下文访问代码。

本方案在录制态执行 AI 代码时，从 `context_ledger` 构建可读写的 `context` dict 注入到 `exec` 命名空间，使 LLM 生成的 `context.get()` 代码在录制态和生成态都能正确运行。

---

## 问题

### 当前行为

1. `_execute_on_page`（`assistant.py:129`）命名空间：`{"page": page}`
2. LLM 尝试 `context.get("first_repo_name", "")` → `NameError: name 'context' is not defined`
3. LLM 重试时用 `globals().get("first_repo_name", "")` 绕过 → 执行成功
4. `_extract_function_body` 将函数体原样存入 `step_data["value"]`
5. Generator 原样嵌入这段代码到导出脚本
6. 导出脚本中包含 `globals().get(...)`，而非 `context.get(...)`

### 根因

录制态和生成态在上下文访问上存在架构断裂：
- **录制态**：结构化 intent 有 `${key}` 解析机制，但 AI 生成的 Python 代码没有上下文访问
- **生成态**：`rebuild_context()` 构建完整的 context dict，AI 代码应该用 `context.get()`

两态代码需要使用相同的 `context.get()` API，但目前录制态没有提供 `context` 对象。

---

## 方案

### 核心改动：注入 context dict 到 exec 命名空间

在 `_execute_on_page` 执行前，从 `context_ledger` 构建一个 `context` dict，注入到 `exec` 命名空间。执行后将新增值同步回 `context_ledger`。

**数据流：**

```
录制态:
  context_ledger.observed_values + derived_values
    → 构建 context dict
    → namespace = {"page": page, "context": context}
    → exec(code, namespace)
    → 比对执行前后 context diff
    → 新增 key 同步回 context_ledger

生成态:
  rebuild_context(page, context)
    → context dict 包含相同的 key
    → AI 代码使用 context.get() 正常工作
```

### 涉及文件和改动

#### 1. `RpaClaw/backend/rpa/assistant.py` — `_execute_on_page`

**当前**（L122-145）：
```python
async def _execute_on_page(page: Page, code: str) -> Dict[str, Any]:
    namespace: Dict[str, Any] = {"page": page}
    exec(compile(code, "<rpa_assistant>", "exec"), namespace)
    if "run" in namespace and callable(namespace["run"]):
        ret = await asyncio.wait_for(namespace["run"](page), timeout=EXECUTION_TIMEOUT_S)
        return {"success": True, "output": str(ret) if ret else "ok", "error": None}
```

**改为：**
- 新增参数 `context: Optional[Dict[str, str]] = None`
- 注入到 namespace：`namespace = {"page": page, "context": context or {}}`
- `run(page)` 调用不变（context 通过闭包/命名空间可见，不改变函数签名）
- 返回值新增 `"context": context` 字段

#### 2. `RpaClaw/backend/rpa/assistant.py` — `_execute_single_response`

**当前**（L764-787）：
AI script 分支直接调用 `self._execute_on_page(current_page, code)`，不传 context。

**改为：**
- 从 `context_ledger` 的 `observed_values` 和 `derived_values` 构建 context dict
- 传给 `_execute_on_page`
- 执行后，比对 context 前后差异，提取新增 key
- 返回值增加 context_writes（AI 代码中写入的新 key 列表）

构建 context dict 的逻辑：
```python
context = {}
if context_ledger:
    for key, entry in context_ledger.observed_values.items():
        if entry.value is not None:
            context[key] = str(entry.value)
    for key, entry in context_ledger.derived_values.items():
        if entry.value is not None:
            context[key] = str(entry.value)
```

#### 3. `RpaClaw/backend/rpa/assistant.py` — `process_message` 中 context_writes 处理

AI script 执行成功后，`_execute_single_response` 返回的 context_writes 需要同步回 context_ledger。在单 action 路径（L604-639）中，对 ai_script 步骤的 context_writes 补充 AI 代码写入的新 key。

#### 4. `RpaClaw/backend/rpa/assistant.py` — `SYSTEM_PROMPT` 更新

在现有规则后增加：
```
9. When writing Python code, you have access to `context` dict containing values extracted in previous steps. Use `context.get("key", "default")` to read previous values, and `context["key"] = value` to store new values. The function signature remains `async def run(page)`.
```

#### 5. `RpaClaw/backend/rpa/generator.py` — `_dehardcode_ai_script` 保留为兜底

由于 LLM 偶尔仍可能硬编码值（不通过 context），`_dehardcode_ai_script` 保留作为最后防线。不需要修改。

### 不涉及的改动

- **结构化 intent 路径**：已有 `${key}` 解析机制，不受影响
- **context_ledger 数据结构**：不改变
- **generator 的 rebuild_context 逻辑**：不改变
- **generator 的 context_reads 注入**：保持不变，处理结构化 intent 的上下文

---

## 执行流程对比

### 改动前

```
Step 1: extract_text "Fincept..." → context_ledger["first_repo_name"] = "Fincept..."
Step 2: LLM 生成 context.get("first_repo_name") → NameError
        LLM 重试 globals().get("first_repo_name") → 成功
        存储代码包含 globals().get(...)
        生成脚本包含 globals().get(...) ← 错误
```

### 改动后

```
Step 1: extract_text "Fincept..." → context_ledger["first_repo_name"] = "Fincept..."
Step 2: 构建 context = {"first_repo_name": "Fincept..."}
        namespace = {"page": page, "context": context}
        LLM 生成 context.get("first_repo_name", "") → 成功
        存储代码包含 context.get(...)
        生成脚本包含 context.get(...) ← 正确
```

---

## 测试要点

1. **基本流程**：录制提取 → 引用，验证 LLM 生成 `context.get()` 而非 `globals().get()`
2. **导出脚本验证**：导出的 skill 脚本中 AI 代码使用 `context.get()`，在独立运行时通过 `rebuild_context` 正确获取值
3. **无 context 场景**：首次步骤没有 context_ledger 值时，LLM 代码不应依赖 context
4. **context 写入**：AI 代码通过 `context["key"] = value` 写入的值被同步回 context_ledger
