# DOM 覆盖扩展 + 多值提取 + Agent 死代码清理

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 DOM 快照无法捕获 span/div 内容的问题，支持多值提取，清理未使用的 agent 模式代码

**Architecture:** 三项独立改动：(1) 扩展 JS 选择器让 span/div 等数据容器进入快照 (2) 修改 LLM prompt + 后端支持多 action 数组执行 (3) 删除 RPAReActAgent 死代码

**Tech Stack:** Python 3.13 (FastAPI), JavaScript (DOM 快照), LLM Prompt Engineering

---

### Task 1: 删除 Agent 模式死代码

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py` — 删除 `_active_agents`, `REACT_SYSTEM_PROMPT`, `RPAReActAgent` 类
- Modify: `RpaClaw/backend/route/rpa.py` — 删除 agent import、react 分支、agent 端点

- [ ] **Step 1: 在 assistant.py 中删除 agent 相关代码**

删除以下内容（按行号范围）：

1. 第 18 行：`_active_agents: Dict[str, "RPAReActAgent"] = {}` → 整行删除

2. 第 277-314 行：`REACT_SYSTEM_PROMPT = """..."""` → 整段删除

3. 第 319-571 行：`class RPAReActAgent:` → 整个类定义删除（从 `class RPAReActAgent:` 到 `class RPAAssistant:` 之前的空行为止）

- [ ] **Step 2: 在 route/rpa.py 中删除 agent 相关代码**

1. 第 19 行，将：
```python
from backend.rpa.assistant import RPAAssistant, RPAReActAgent, _active_agents
```
替换为：
```python
from backend.rpa.assistant import RPAAssistant
```

2. 第 558-585 行，删除 chat 端点中的 react 分支：
```python
            if request.mode == "react":
                # Reuse existing agent for this session to preserve history across turns
                ...整个 if 块...
                except Exception:
                    _active_agents.pop(session_id, None)
                    raise
```
只保留 `else:` 后面的 `async for event in assistant.chat(...)` 分支，去掉 `else:` 缩进。

3. 第 613-633 行，删除两个 agent 端点：
```python
@router.post("/session/{session_id}/agent/confirm")
...
    return {"ok": True}


@router.post("/session/{session_id}/agent/abort")
...
    return {"ok": True}
```

- [ ] **Step 3: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "import ast; ast.parse(open('RpaClaw/backend/rpa/assistant.py').read()); ast.parse(open('RpaClaw/backend/route/rpa.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/backend/rpa/assistant.py RpaClaw/backend/route/rpa.py
git commit -m "chore: remove unused RPAReActAgent and related agent mode code"
```

---

### Task 2: 扩展 DOM 内容选择器

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py:6` — `CONTENT` 常量

- [ ] **Step 1: 扩展 CONTENT 选择器**

在 `assistant_snapshot_runtime.py` 中，找到第 6 行：

```javascript
    const CONTENT = 'h1,h2,h3,h4,h5,h6,th,td,dt,dd,li,p,label,[role=heading],[role=cell],[role=rowheader],[role=columnheader]';
```

替换为：

```javascript
    const CONTENT = 'h1,h2,h3,h4,h5,h6,th,td,dt,dd,li,p,label,span,div,figcaption,caption,time,mark,strong,em,[role=heading],[role=cell],[role=rowheader],[role=columnheader]';
```

- [ ] **Step 2: 添加 span/div 去重过滤**

在 `contentSeen` 的去重逻辑中（约 L298），找到：

```javascript
        const key = [text, bbox(rect).x, bbox(rect).y].join('|');
        if (contentSeen.has(key))
            continue;
        contentSeen.add(key);
```

在这段之前添加文本去重：

```javascript
        const key = [text, bbox(rect).x, bbox(rect).y].join('|');
        if (contentSeen.has(key))
            continue;
        // Deduplicate span/div text that is already captured by a heading or paragraph
        const tag = el.tagName.toLowerCase();
        if (tag === 'span' || tag === 'div') {
            const isDupText = result.content_nodes.some(n => n.text === text);
            if (isDupText) continue;
        }
        contentSeen.add(key);
```

- [ ] **Step 3: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "import ast; ast.parse(open('RpaClaw/backend/rpa/assistant_snapshot_runtime.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/backend/rpa/assistant_snapshot_runtime.py
git commit -m "fix: extend CONTENT selector to capture span/div data containers"
```

---

### Task 3: Chat Prompt 多值提取指导

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py` — `SYSTEM_PROMPT`（约 L71-100）

- [ ] **Step 1: 在 SYSTEM_PROMPT Rules 中添加多值提取规则**

在 `SYSTEM_PROMPT` 的 Rules 列表末尾（第 99 行 `7. For extract_text actions...` 之后），添加：

```
8. When the user asks to extract multiple values (e.g. "提取购买人、使用部门和供应商"), output a JSON array of actions. Each action should have its own `result_key` and `target_hint`. Example:
[
  {"action": "extract_text", "description": "Extract requestor", "prompt": "提取购买人、使用部门和供应商", "result_key": "requestor", "target_hint": {"name": "购买人"}},
  {"action": "extract_text", "description": "Extract department", "prompt": "提取购买人、使用部门和供应商", "result_key": "department", "target_hint": {"name": "使用部门"}},
  {"action": "extract_text", "description": "Extract supplier", "prompt": "提取购买人、使用部门和供应商", "result_key": "supplier", "target_hint": {"name": "供应商"}}
]
```

注意：这加在 triple-quoted string 内部。找到规则 7 的结尾行，在其后追加规则 8。

- [ ] **Step 2: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "import ast; ast.parse(open('RpaClaw/backend/rpa/assistant.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/rpa/assistant.py
git commit -m "feat: add multi-value extraction guidance to SYSTEM_PROMPT"
```

---

### Task 4: 后端支持多 action 数组执行

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py` — `_extract_structured_intent` 方法 + `process_message` 方法

- [ ] **Step 1: 新增 `_extract_structured_intents` 方法（返回列表）**

在 `_extract_structured_intent` 方法之后（约 L925），添加：

```python
    @staticmethod
    def _extract_structured_intents(text: str) -> List[Dict[str, Any]]:
        """Extract one or more structured intents from LLM response.

        Supports both single JSON object and JSON array of objects.
        Returns a list (empty if no valid intent found).
        """
        stripped = text.strip()

        # Try direct JSON parse
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                results = [item for item in parsed if isinstance(item, dict) and item.get("action")]
                if results:
                    return results
            if isinstance(parsed, dict) and parsed.get("action"):
                return [parsed]
        except Exception:
            pass

        # Try extracting from markdown code block
        match = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
                if isinstance(parsed, list):
                    results = [item for item in parsed if isinstance(item, dict) and item.get("action")]
                    if results:
                        return results
                if isinstance(parsed, dict) and parsed.get("action"):
                    return [parsed]
            except Exception:
                pass

        # Fallback to single intent extraction
        single = RPAAssistant._extract_structured_intent(text)
        if single:
            return [single]
        return []
```

- [ ] **Step 2: 修改 process_message 支持多 action 循环**

在 `process_message` 中，找到首次执行的代码块。当前的逻辑是调用 `_execute_single_response` 一次。需要改为：先尝试解析多个 intent，如果有多个则循环执行。

找到（在 `yield {"event": "executing", "data": {}}` 之后）：

```python
        # ── 首次执行 ──────────────────────────────────────────
        current_page = page_provider() if page_provider else page
        result = {"success": False, "error": "No active page available", "output": ""}
        code = None
        resolution = None
        context_reads: List[str] = []

        if current_page is not None:
            try:
                result, code, resolution, context_reads = await self._execute_single_response(
                    current_page, snapshot, full_response, context_ledger
                )
            except Exception as exc:
                result = {"success": False, "error": str(exc), "output": ""}
                code = None
                resolution = None
```

替换为：

```python
        # ── 首次执行 ──────────────────────────────────────────
        current_page = page_provider() if page_provider else page
        result = {"success": False, "error": "No active page available", "output": ""}
        code = None
        resolution = None
        context_reads: List[str] = []

        if current_page is not None:
            intents = self._extract_structured_intents(full_response)
            if len(intents) > 1:
                # ── Multi-action: execute each intent sequentially ──
                multi_results = []
                for i, intent_json in enumerate(intents):
                    intent_response = json.dumps(intent_json, ensure_ascii=False)
                    try:
                        intent_result, _, intent_resolution, intent_reads = await self._execute_single_response(
                            current_page, snapshot, intent_response, context_ledger
                        )
                        multi_results.append(intent_result)
                        context_reads.extend(intent_reads)
                        if intent_result.get("success") and intent_resolution:
                            step_data = intent_result.get("step")
                            if step_data:
                                context_writes_multi = self._compute_context_writes(
                                    message=message,
                                    step_data=step_data,
                                    resolution=intent_resolution,
                                )
                                self._promote_to_ledger(
                                    rpa_manager=rpa_manager,
                                    session_id=session_id,
                                    context_writes=context_writes_multi,
                                    step_data=step_data,
                                    output=intent_result.get("output"),
                                )
                                step_data["context_reads"] = intent_reads
                                step_data["context_writes"] = context_writes_multi
                                await rpa_manager.add_step(session_id, step_data)
                    except Exception as exc:
                        multi_results.append({"success": False, "error": str(exc), "output": ""})

                # Use the last result as the overall result
                result = multi_results[-1] if multi_results else {"success": False, "error": "No actions executed", "output": ""}
                code = None
                resolution = None

                # Check if all succeeded
                all_success = all(r.get("success") for r in multi_results)
                result["success"] = all_success
                if not all_success:
                    errors = [r.get("error", "") for r in multi_results if not r.get("success")]
                    result["error"] = "; ".join(errors)

                # Skip normal retry and step processing — already done per-intent
                # Jump directly to the result yield
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
                        "retried": False,
                        "original_error": None,
                        "multi_action_count": len(intents),
                    },
                }
                yield {"event": "done", "data": {}}
                return
            else:
                # ── Single action: original flow ──
                try:
                    result, code, resolution, context_reads = await self._execute_single_response(
                        current_page, snapshot, full_response, context_ledger
                    )
                except Exception as exc:
                    result = {"success": False, "error": str(exc), "output": ""}
                    code = None
                    resolution = None
```

- [ ] **Step 3: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "import ast; ast.parse(open('RpaClaw/backend/rpa/assistant.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/backend/rpa/assistant.py
git commit -m "feat: support multi-action array execution for batch value extraction"
```

---

## Self-Review

**Spec coverage:**
- Section 1 (DOM CONTENT 扩展): Task 2 ✓
- Section 2 (多值提取 prompt + 后端): Task 3 (prompt) + Task 4 (后端) ✓
- Section 3 (Agent 死代码删除): Task 1 ✓

**Placeholder scan:** 无 TBD/TODO。所有步骤包含完整代码。

**Type consistency:** `_extract_structured_intents` 返回 `List[Dict[str, Any]]`，与 `_extract_structured_intent` 的 `Optional[Dict[str, Any]]` 一致但为列表形式。多 action 路径中 `rpa_manager` 和 `session_id` 来自 `process_message` 的外层参数。
