# RPA 录制系统四项修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 RPA 录制系统的 DOM 快照、上下文鸿沟、对话框体验和重试机制

**Architecture:** 四项独立修复按优先级顺序实施：对话框 → 重试机制 → DOM 快照 → 上下文账本。每项改动独立可测试。

**Tech Stack:** Python 3.13 (FastAPI, Pydantic v2), Vue 3 + TypeScript, Playwright

---

## Task 1: 录制对话框 — 替换 input 为 textarea 并添加 IME 支持

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue:982-999` (模板)
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue:147` (script — 新增变量)

- [ ] **Step 1: 在 `<script setup>` 中添加 IME 相关变量和方法**

在 `RecorderPage.vue` 中，找到 `const sending = ref(false);` (约 L148)，在其后添加：

```typescript
const isComposing = ref(false);
const messageInputRef = ref<HTMLTextAreaElement | null>(null);

const handleMessageKeydown = (event: KeyboardEvent) => {
  if (isComposing.value) return;
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
};
```

在同一文件中，找到 `const sendMessage` 定义之前，添加 auto-resize watcher：

```typescript
watch(newMessage, () => {
  nextTick(() => {
    const el = messageInputRef.value;
    if (el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    }
  });
});
```

- [ ] **Step 2: 替换模板中的 `<input>` 为 `<textarea>`**

在模板中找到（约 L984-991）：

```vue
<input
  v-model="newMessage"
  @keyup.enter="sendMessage"
  :disabled="sending || agentRunning"
  class="w-full bg-white dark:bg-[#272728] border border-gray-200 dark:border-gray-700 rounded-2xl py-3 pl-4 pr-12 text-xs focus:ring-2 focus:ring-[#831bd7] focus:border-transparent shadow-sm placeholder:text-gray-400 outline-none disabled:opacity-50"
  :placeholder="agentRunning ? 'AI 正在执行录制任务...' : (sending ? 'AI 正在处理...' : '向助手提问...')"
  type="text"
/>
```

替换为：

```vue
<textarea
  ref="messageInputRef"
  v-model="newMessage"
  @compositionstart="isComposing = true"
  @compositionend="isComposing = false"
  @keydown="handleMessageKeydown"
  :disabled="sending || agentRunning"
  rows="1"
  class="w-full bg-white dark:bg-[#272728] border border-gray-200 dark:border-gray-700 rounded-2xl py-3 pl-4 pr-12 text-xs resize-none overflow-hidden focus:ring-2 focus:ring-[#831bd7] focus:border-transparent shadow-sm placeholder:text-gray-400 outline-none disabled:opacity-50"
  :placeholder="agentRunning ? 'AI 正在执行录制任务...' : (sending ? 'AI 正在处理...' : '向助手提问...')"
/>
```

- [ ] **Step 3: 验证 TypeScript 编译**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: 无新增错误

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/frontend/src/pages/rpa/RecorderPage.vue
git commit -m "fix: replace input with textarea in recorder dialog for IME and multi-line support"
```

---

## Task 2: 重试机制 — 后端 SSE 事件推送

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant.py:768-813` (移除 `_execute_with_retry`)
- Modify: `RpaClaw/backend/rpa/assistant.py:614-692` (内联重试逻辑到 `process_message`)

- [ ] **Step 1: 将 `_execute_with_retry` 的逻辑内联到 `process_message` 中**

在 `assistant.py` 的 `process_message` 方法中，找到（约 L622-692）：

```python
        yield {"event": "executing", "data": {}}
        _session = rpa_manager.sessions.get(session_id)
        result, final_response, code, resolution, retry_notice, context_reads = await self._execute_with_retry(
            page=page,
            page_provider=page_provider,
            snapshot=snapshot,
            full_response=full_response,
            messages=messages,
            model_config=model_config,
            context_ledger=_session.context_ledger if _session else None,
        )

        if retry_notice:
            yield {"event": "message_chunk", "data": {"text": retry_notice}}
```

替换为：

```python
        yield {"event": "executing", "data": {}}
        _session = rpa_manager.sessions.get(session_id)
        context_ledger = _session.context_ledger if _session else None

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

        # ── 首次失败则重试 ──────────────────────────────────
        retried = False
        original_error = ""

        if not result.get("success"):
            original_error = result.get("error", "")
            retried = True

            yield {
                "event": "retry_start",
                "data": {"original_error": original_error},
            }

            retry_messages = messages + [
                {"role": "assistant", "content": full_response},
                {"role": "user", "content": f"Execution error: {result['error']}\nPlease fix it and retry."},
            ]
            retry_response = ""
            async for chunk_text in self._stream_llm(retry_messages, model_config):
                retry_response += chunk_text
                yield {"event": "retry_chunk", "data": {"text": chunk_text}}

            yield {"event": "retry_executing", "data": {}}

            current_page = page_provider() if page_provider else page
            if current_page is None:
                result = {"success": False, "error": "No active page available", "output": ""}
            else:
                retry_snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
                try:
                    result, code, resolution, context_reads = await self._execute_single_response(
                        current_page, retry_snapshot, retry_response, context_ledger
                    )
                except Exception as exc:
                    result = {"success": False, "error": str(exc), "output": ""}
                    code = None
                    resolution = None

            full_response = retry_response

            yield {
                "event": "retry_result",
                "data": {
                    "success": result.get("success", False),
                    "error": result.get("error"),
                },
            }
```

- [ ] **Step 2: 更新 result 事件，添加重试元信息**

在同一个方法中，找到（约 L681-692）：

```python
        yield {
            "event": "result",
            "data": {
                "success": result["success"],
                "error": result.get("error"),
                "step": step_data,
                "output": result.get("output"),
                "context_reads": context_reads,
                "context_writes": context_writes,
            },
        }
```

替换为：

```python
        yield {
            "event": "result",
            "data": {
                "success": result["success"],
                "error": result.get("error"),
                "step": step_data,
                "output": result.get("output"),
                "context_reads": context_reads,
                "context_writes": context_writes,
                "retried": retried,
                "original_error": original_error if retried else None,
            },
        }
```

- [ ] **Step 3: 删除不再需要的 `_execute_with_retry` 方法**

删除 `assistant.py` 中整个 `_execute_with_retry` 方法（约 L768-813）。

- [ ] **Step 4: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "import ast; ast.parse(open('RpaClaw/backend/rpa/assistant.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/assistant.py
git commit -m "feat: inline retry logic into process_message with SSE events for full visibility"
```

---

## Task 3: 重试机制 — 前端处理 retry 事件

**Files:**
- Modify: `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue:605-670` (SSE 事件处理)

- [ ] **Step 1: 在 SSE 事件处理中添加 retry 事件处理**

在 `RecorderPage.vue` 的 SSE 事件处理 `for` 循环中（约 L636），找到：

```typescript
            } else if (eventType === 'agent_thought') {
```

在其前面添加：

```typescript
            } else if (eventType === 'retry_start') {
              const errMsg = data.original_error || '未知错误';
              chatMessages.value[msgIdx].text += `\n\n执行失败: ${errMsg}`;
              chatMessages.value[msgIdx].status = 'retrying';
              chatMessages.value[msgIdx].text += '\n\n正在修正并重试...';
            } else if (eventType === 'retry_chunk') {
              chatMessages.value[msgIdx].text += data.text || '';
            } else if (eventType === 'retry_executing') {
              chatMessages.value[msgIdx].status = 'executing';
              chatMessages.value[msgIdx].text += '\n\n修正完成，重新执行中...';
            } else if (eventType === 'retry_result') {
              if (data.success) {
                chatMessages.value[msgIdx].text += '\n重试成功';
              } else {
                chatMessages.value[msgIdx].text += `\n重试失败: ${data.error || ''}`;
              }
```

- [ ] **Step 2: 更新 result 事件处理，显示重试元信息**

在同一事件循环中找到（约 L627）：

```typescript
            } else if (eventType === 'result') {
              chatMessages.value[msgIdx].status = data.success ? 'done' : 'error';
              if (data.error) chatMessages.value[msgIdx].error = data.error;
              if (data.output && data.output !== 'ok' && data.output !== 'None') {
                chatMessages.value[msgIdx].text += `${chatMessages.value[msgIdx].text ? '\n' : ''}输出: ${data.output}`;
              }
              if (Array.isArray(data.context_writes) && data.context_writes.length > 0) {
                chatMessages.value[msgIdx].text += `\n📋 已记录上下文变量：${data.context_writes.join(', ')}`;
              }
```

替换为：

```typescript
            } else if (eventType === 'result') {
              chatMessages.value[msgIdx].status = data.success ? 'done' : 'error';
              if (data.error) chatMessages.value[msgIdx].error = data.error;
              if (data.output && data.output !== 'ok' && data.output !== 'None') {
                chatMessages.value[msgIdx].text += `${chatMessages.value[msgIdx].text ? '\n' : ''}输出: ${data.output}`;
              }
              if (Array.isArray(data.context_writes) && data.context_writes.length > 0) {
                chatMessages.value[msgIdx].text += `\n📋 已记录上下文变量：${data.context_writes.join(', ')}`;
              }
              if (data.retried) {
                const retryStatus = data.success ? '重试后成功' : '重试后仍然失败';
                chatMessages.value[msgIdx].text += `\n[${retryStatus}]`;
              }
```

- [ ] **Step 3: 验证 TypeScript 编译**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw/RpaClaw/frontend && npx vue-tsc --noEmit 2>&1 | head -20`
Expected: 无新增错误

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/frontend/src/pages/rpa/RecorderPage.vue
git commit -m "feat: handle retry SSE events in recorder frontend for visible retry process"
```

---

## Task 4: DOM 快照 — 取消层深限制

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_runtime.py:78-95` (`EXTRACT_ELEMENTS_JS`)

- [ ] **Step 1: 修改 `findUniqueAnchor` 和 `findCollectionContext` 的深度限制**

在 `assistant_runtime.py` 中，找到（L80）：

```javascript
        for (let depth = 0; current && current !== document.body && depth < 4; depth++, current = current.parentElement) {
```

替换为：

```javascript
        for (let depth = 0; current && current !== document.body && depth < 20; depth++, current = current.parentElement) {
```

找到（L89）：

```javascript
        for (let depth = 0; current && current !== document.body && depth < 6; depth++, current = current.parentElement) {
```

替换为：

```javascript
        for (let depth = 0; current && current !== document.body && depth < 20; depth++, current = current.parentElement) {
```

- [ ] **Step 2: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "import ast; ast.parse(open('RpaClaw/backend/rpa/assistant_runtime.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/rpa/assistant_runtime.py
git commit -m "fix: increase DOM ancestor traversal depth limit from 4/6 to 20"
```

---

## Task 5: DOM 快照 — 节点上限自适应

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_runtime.py:145` (`EXTRACT_ELEMENTS_JS`)
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py:233-326` (`SNAPSHOT_V2_JS`)

- [ ] **Step 1: 修改 `EXTRACT_ELEMENTS_JS` 的节点上限**

在 `assistant_runtime.py` 中，在 `for (const el of els) {` 循环之前（约 L110 前一行），添加：

```javascript
    const ELEMENT_CAP = Math.min(els.length, 200);
```

找到（L145）：

```javascript
        if (results.length >= 80) break;
```

替换为：

```javascript
        if (results.length >= ELEMENT_CAP) break;
```

- [ ] **Step 2: 修改 `SNAPSHOT_V2_JS` 的节点上限**

在 `assistant_snapshot_runtime.py` 中，在 `const actionableSeen = new Set();` 之前（约 L235 前一行），添加：

```javascript
    const totalActionable = document.querySelectorAll(ACTIONABLE).length;
    const totalContent = document.querySelectorAll(CONTENT).length;
    const ACTIONABLE_CAP = Math.min(totalActionable, 300);
    const CONTENT_CAP = Math.min(totalContent, 400);
```

找到（L286）：

```javascript
        if (result.actionable_nodes.length >= 120)
```

替换为：

```javascript
        if (result.actionable_nodes.length >= ACTIONABLE_CAP)
```

找到（L324）：

```javascript
        if (result.content_nodes.length >= 160)
```

替换为：

```javascript
        if (result.content_nodes.length >= CONTENT_CAP)
```

- [ ] **Step 3: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "import ast; ast.parse(open('RpaClaw/backend/rpa/assistant_runtime.py').read()); ast.parse(open('RpaClaw/backend/rpa/assistant_snapshot_runtime.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/rpa/assistant_snapshot_runtime.py
git commit -m "fix: make DOM node caps adaptive to page complexity"
```

---

## Task 6: DOM 快照 — 过滤噪声内容

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_runtime.py:45-53` (`EXTRACT_ELEMENTS_JS` 的 `stableSelector`)
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py:273-279` (`SNAPSHOT_V2_JS` 的 `element_snapshot`)

- [ ] **Step 1: 精简 `stableSelector`，移除 class 分支**

在 `assistant_runtime.py` 中，找到（L45-53）：

```javascript
    function stableSelector(el) {
        const tag = el.tagName.toLowerCase();
        if (el.id && !isGuidLike(el.id)) return `${tag}#${cssEsc(el.id)}`;
        const classes = Array.from(el.classList || []).filter(cls => cls && !isGuidLike(cls)).slice(0, 2);
        if (classes.length) return `${tag}.${classes.map(cssEsc).join('.')}`;
        const role = el.getAttribute('role');
        if (role) return `${tag}[role="${cssEsc(role)}"]`;
        return tag;
    }
```

替换为：

```javascript
    function stableSelector(el) {
        const tag = el.tagName.toLowerCase();
        if (el.id && !isGuidLike(el.id)) return `${tag}#${cssEsc(el.id)}`;
        const role = el.getAttribute('role');
        if (role) return `${tag}[role="${cssEsc(role)}"]`;
        return tag;
    }
```

- [ ] **Step 2: 精简 `element_snapshot`，只保留语义字段**

在 `assistant_snapshot_runtime.py` 中，找到（L273-279）：

```javascript
            element_snapshot: {
                tag: el.tagName.toLowerCase(),
                text,
                title,
                href: normalizeText(el.getAttribute('href') || '', 120),
            },
```

替换为：

```javascript
            element_snapshot: {
                tag: el.tagName.toLowerCase(),
                text,
                role,
                name,
                href: normalizeText(el.getAttribute('href') || '', 120),
            },
```

- [ ] **Step 3: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "import ast; ast.parse(open('RpaClaw/backend/rpa/assistant_runtime.py').read()); ast.parse(open('RpaClaw/backend/rpa/assistant_snapshot_runtime.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/rpa/assistant_snapshot_runtime.py
git commit -m "fix: filter CSS noise from DOM snapshots, keep only semantic fields"
```

---

## Task 7: 上下文账本 — Ledger 新增 `get_rebuild_sequence` 方法

**Files:**
- Modify: `RpaClaw/backend/rpa/context_ledger.py` — 新增方法
- Test: `RpaClaw/backend/tests/test_rpa_context_ledger.py`

- [ ] **Step 1: 在 `context_ledger.py` 中新增 `get_rebuild_sequence` 方法**

在 `context_ledger.py` 的 `TaskContextLedger` 类中，`record_rebuild_action` 方法之后（约 L171），添加：

```python
    def get_rebuild_sequence(self) -> list[dict]:
        """按依赖顺序返回重建动作列表，供代码生成器消费。"""
        sequence: list[dict] = []
        seen_keys: set[str] = set()

        for action in self.rebuild_actions:
            entry = {
                "action": action.action,
                "description": action.description,
                "writes": list(action.writes),
                "source_step_id": action.step_ref,
            }
            if action.action == "navigate":
                entry["url"] = action.description
            sequence.append(entry)
            seen_keys.update(action.writes)

        for key, cv in self.observed_values.items():
            if key not in seen_keys and cv.user_explicit:
                sequence.append({
                    "action": "observe",
                    "description": f"Observed value: {key}",
                    "writes": [key],
                    "source_step_id": cv.source_step_id,
                    "value": cv.value,
                })
                seen_keys.add(key)

        for key, cv in self.derived_values.items():
            if key not in seen_keys and cv.runtime_required:
                sequence.append({
                    "action": "derive",
                    "description": f"Derived value: {key}",
                    "writes": [key],
                    "source_step_id": cv.source_step_id,
                    "value": cv.value,
                })

        return sequence
```

- [ ] **Step 2: 编写测试**

在 `tests/test_rpa_context_ledger.py` 中追加：

```python
def test_get_rebuild_sequence():
    ledger = TaskContextLedger()
    ledger.rebuild_actions.append(ContextRebuildAction(
        action="navigate", description="https://example.com", writes=[], step_ref="step-1",
    ))
    ledger.rebuild_actions.append(ContextRebuildAction(
        action="extract_text", description="Extract title", writes=["title"], step_ref="step-2",
    ))
    ledger.record_value("observed", "token", "abc123", user_explicit=True, source_step_id="step-3")
    ledger.record_value("derived", "computed_id", "id-456", runtime_required=True, source_step_id="step-4")
    # Non-promoted observation (no flags)
    ledger.record_value("observed", "noise", "xxx", source_step_id="step-5")

    seq = ledger.get_rebuild_sequence()

    assert len(seq) == 4
    assert seq[0]["action"] == "navigate"
    assert seq[0]["url"] == "https://example.com"
    assert seq[1]["action"] == "extract_text"
    assert seq[1]["writes"] == ["title"]
    assert seq[2]["action"] == "observe"
    assert seq[2]["value"] == "abc123"
    assert seq[3]["action"] == "derive"
    assert seq[3]["value"] == "id-456"
    # "noise" should not appear (no flags set)
    assert all("noise" not in entry.get("writes", []) for entry in seq)
```

- [ ] **Step 3: 运行测试**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_context_ledger.py -v`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/backend/rpa/context_ledger.py RpaClaw/backend/tests/test_rpa_context_ledger.py
git commit -m "feat: add get_rebuild_sequence to TaskContextLedger for generator consumption"
```

---

## Task 8: 上下文账本 — 生成器消费 Ledger 构建两阶段 rebuild_context

**Files:**
- Modify: `RpaClaw/backend/rpa/generator.py:145` (方法签名)
- Modify: `RpaClaw/backend/rpa/generator.py:373-456` (rebuild_context 构建)
- Modify: `RpaClaw/backend/route/rpa.py:398,413,514` (调用点传入 ledger)

- [ ] **Step 1: 修改 `generate_script` 方法签名**

在 `generator.py` 中，找到（L145）：

```python
    def generate_script(self, steps: List[Dict[str, Any]], params: Dict[str, Any] = None, is_local: bool = False, test_mode: bool = False) -> str:
```

替换为：

```python
    def generate_script(self, steps: List[Dict[str, Any]], params: Dict[str, Any] = None, is_local: bool = False, test_mode: bool = False, context_ledger: Any = None) -> str:
```

在文件顶部 import 区域添加：

```python
from __future__ import annotations

from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    from backend.rpa.context_ledger import TaskContextLedger
```

- [ ] **Step 2: 替换 rebuild_context 构建为两阶段**

在 `generator.py` 中，找到 rebuild_context 构建区域（约 L373-456），即从 `# Build rebuild_context function body` 开始到 `rebuild_lines.append("    return context")` 的整个区块。

将其替换为：

```python
        # Build rebuild_context function body
        step_urls: Dict[int, str] = {}
        running_url = ""
        for i, step in enumerate(deduped):
            action = step.get("action", "")
            step_url = step.get("url", "")
            if action in ("navigate", "goto") and step_url:
                running_url = step_url
            step_urls[i] = running_url

        rebuild_lines: List[str] = [
            "",
            "async def rebuild_context(page, context, **kwargs):",
            '    """Rebuild runtime context by navigating to prerequisite pages and extracting values."""',
            f'    tabs = {{"{root_tab_id}": page}}',
            "    current_page = page",
        ]
        rebuild_tab_id = root_tab_id
        rebuild_prev_url = None

        if context_ledger:
            # ── Phase A: Build from context ledger metadata ──
            rebuild_sequence = context_ledger.get_rebuild_sequence()
            already_written: set[str] = set()
            for entry in rebuild_sequence:
                already_written.update(entry.get("writes", []))

            for idx, entry in enumerate(rebuild_sequence):
                entry_action = entry["action"]
                writes = entry.get("writes", [])

                if entry_action == "navigate":
                    url = entry.get("url", "")
                    if url and url != rebuild_prev_url:
                        rebuild_lines.append(f'    await current_page.goto("{url}")')
                        rebuild_lines.append('    await current_page.wait_for_load_state("domcontentloaded")')
                        rebuild_prev_url = url

                elif entry_action == "extract_text":
                    source_step_id = entry.get("source_step_id")
                    matching_step = next(
                        (s for s in deduped if s.get("id") == source_step_id), None
                    )
                    if matching_step:
                        target = matching_step.get("target", "")
                        frame_path = matching_step.get("frame_path") or []
                        scope_var = "current_page"
                        if frame_path:
                            scope_var = "frame_scope"
                            frame_parent = "current_page"
                            for frame_selector in frame_path:
                                rebuild_lines.append(
                                    f'    frame_scope = {frame_parent}.frame_locator("{self._escape(frame_selector)}")'
                                )
                                frame_parent = "frame_scope"
                        locator = self._build_locator_for_page(target, scope_var)
                        result_var = f"rebuild_var_ledger_{idx}"
                        rebuild_lines.append(f"    {result_var} = await {locator}.inner_text()")
                        for ctx_key in writes:
                            ctx_key_safe = ctx_key.replace("'", "\\'")
                            rebuild_lines.append(f'    context["{ctx_key_safe}"] = {result_var}')

                elif entry_action in ("observe", "derive"):
                    value = entry.get("value")
                    for ctx_key in writes:
                        ctx_key_safe = ctx_key.replace("'", "\\'")
                        rebuild_lines.append(f'    context["{ctx_key_safe}"] = {repr(value)}')

            # ── Phase B: Supplement from step context_writes (backward compat) ──
            for rebuild_idx, step in enumerate(deduped):
                context_writes = step.get("context_writes") or []
                new_writes = [k for k in context_writes if k not in already_written]
                if not new_writes:
                    continue
                action = step.get("action", "")
                url = step.get("url", "") or step_urls.get(rebuild_idx, "")
                step_tab_id = step.get("tab_id") or rebuild_tab_id
                if step_tab_id != rebuild_tab_id:
                    rebuild_tab_id = step_tab_id
                if url and url != rebuild_prev_url:
                    rebuild_lines.append(f'    await current_page.goto("{url}")')
                    rebuild_lines.append('    await current_page.wait_for_load_state("domcontentloaded")')
                    rebuild_prev_url = url
                if action == "extract_text":
                    target = step.get("target", "")
                    frame_path = step.get("frame_path") or []
                    scope_var = "current_page"
                    if frame_path:
                        scope_var = "frame_scope"
                        frame_parent = "current_page"
                        for frame_selector in frame_path:
                            rebuild_lines.append(
                                f'    frame_scope = {frame_parent}.frame_locator("{self._escape(frame_selector)}")'
                            )
                            frame_parent = "frame_scope"
                    locator = self._build_locator_for_page(target, scope_var)
                    result_var = f"rebuild_var_fallback_{rebuild_idx}"
                    rebuild_lines.append(f"    {result_var} = await {locator}.inner_text()")
                    for ctx_key in new_writes:
                        ctx_key_safe = ctx_key.replace("'", "\\'")
                        rebuild_lines.append(f'    context["{ctx_key_safe}"] = {result_var}')
                elif action == "fill":
                    target = step.get("target", "")
                    value = step.get("value", "")
                    frame_path = step.get("frame_path") or []
                    scope_var = "current_page"
                    if frame_path:
                        scope_var = "frame_scope"
                        frame_parent = "current_page"
                        for frame_selector in frame_path:
                            rebuild_lines.append(
                                f'    frame_scope = {frame_parent}.frame_locator("{self._escape(frame_selector)}")'
                            )
                            frame_parent = "frame_scope"
                    locator = self._build_locator_for_page(target, scope_var)
                    for ctx_key in new_writes:
                        ctx_key_safe = ctx_key.replace("'", "\\'")
                        rebuild_lines.append(f'    context["{ctx_key_safe}"] = kwargs.get("{ctx_key_safe}", "")')
                    fill_value = self._maybe_parameterize(value, params)
                    rebuild_lines.append(f"    await {locator}.fill({fill_value})")
        else:
            # ── Fallback: original step-only approach ──
            for rebuild_idx, step in enumerate(deduped):
                context_writes = step.get("context_writes") or []
                if not context_writes:
                    continue
                action = step.get("action", "")
                url = step.get("url", "") or step_urls.get(rebuild_idx, "")
                step_tab_id = step.get("tab_id") or rebuild_tab_id
                if step_tab_id != rebuild_tab_id:
                    rebuild_tab_id = step_tab_id
                if url and url != rebuild_prev_url:
                    rebuild_lines.append(f'    await current_page.goto("{url}")')
                    rebuild_lines.append('    await current_page.wait_for_load_state("domcontentloaded")')
                    rebuild_prev_url = url
                if action == "extract_text":
                    target = step.get("target", "")
                    frame_path = step.get("frame_path") or []
                    scope_var = "current_page"
                    if frame_path:
                        scope_var = "frame_scope"
                        frame_parent = "current_page"
                        for frame_selector in frame_path:
                            rebuild_lines.append(
                                f'    frame_scope = {frame_parent}.frame_locator("{self._escape(frame_selector)}")'
                            )
                            frame_parent = "frame_scope"
                    locator = self._build_locator_for_page(target, scope_var)
                    result_var = f"rebuild_var_{rebuild_idx}"
                    rebuild_lines.append(f"    {result_var} = await {locator}.inner_text()")
                    for ctx_key in context_writes:
                        ctx_key_safe = ctx_key.replace("'", "\\'")
                        rebuild_lines.append(f'    context["{ctx_key_safe}"] = {result_var}')
                elif action == "fill":
                    target = step.get("target", "")
                    value = step.get("value", "")
                    frame_path = step.get("frame_path") or []
                    scope_var = "current_page"
                    if frame_path:
                        scope_var = "frame_scope"
                        frame_parent = "current_page"
                        for frame_selector in frame_path:
                            rebuild_lines.append(
                                f'    frame_scope = {frame_parent}.frame_locator("{self._escape(frame_selector)}")'
                            )
                            frame_parent = "frame_scope"
                    locator = self._build_locator_for_page(target, scope_var)
                    for ctx_key in context_writes:
                        ctx_key_safe = ctx_key.replace("'", "\\'")
                        rebuild_lines.append(f'    context["{ctx_key_safe}"] = kwargs.get("{ctx_key_safe}", "")')
                    fill_value = self._maybe_parameterize(value, params)
                    rebuild_lines.append(f"    await {locator}.fill({fill_value})")

        rebuild_lines.append("    return context")
```

- [ ] **Step 3: 更新调用点传入 context_ledger**

在 `route/rpa.py` 中，有三处调用 `generator.generate_script`：

找到（约 L398）：
```python
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"))
```
替换为：
```python
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"), context_ledger=None)
```

找到（约 L413）：
```python
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"), test_mode=True)
```
替换为：
```python
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"), test_mode=True, context_ledger=None)
```

找到（约 L514）：
```python
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"))
```
在其前面添加 ledger 获取：
```python
    _session_obj = await rpa_manager.get_session(session_id)
    _context_ledger = _session_obj.context_ledger if _session_obj else None
    script = generator.generate_script(steps, request.params, is_local=(settings.storage_backend == "local"), context_ledger=_context_ledger)
```

- [ ] **Step 4: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "import ast; ast.parse(open('RpaClaw/backend/rpa/generator.py').read()); ast.parse(open('RpaClaw/backend/route/rpa.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/generator.py RpaClaw/backend/route/rpa.py
git commit -m "feat: two-phase rebuild_context from context ledger with fallback"
```

---

## Task 9: 上下文账本 — 反硬编码机制

**Files:**
- Modify: `RpaClaw/backend/rpa/generator.py` (新增 `_build_context_value_map`，修改 fill 步骤生成)

- [ ] **Step 1: 添加 `_build_context_value_map` 方法**

在 `generator.py` 的 `_maybe_parameterize` 方法之后（约 L867），添加：

```python
    @staticmethod
    def _build_context_value_map(steps: List[Dict[str, Any]]) -> Dict[str, str]:
        """构建上下文 key 到录制值的映射，用于防止硬编码。"""
        value_map: Dict[str, str] = {}
        for step in steps:
            for ctx_key in step.get("context_writes", []):
                output = step.get("output", "")
                if output:
                    value_map[ctx_key] = output
        return value_map
```

- [ ] **Step 2: 在 fill 步骤生成中使用 context_value_map**

在 `generate_script` 方法的 fill 步骤处理中（搜索 `elif action == "fill":`），找到类似：

```python
                    fill_value = self._maybe_parameterize(value, params)
                    step_lines.append(f"    await {locator}.fill({fill_value})")
```

在这个 `generate_script` 方法的开头（params 初始化之后，约 L147），添加：

```python
        context_value_map = self._build_context_value_map(deduped)
```

然后在 fill 步骤中，将：
```python
                    fill_value = self._maybe_parameterize(value, params)
```

替换为：

```python
                    ctx_fill_match = None
                    for ctx_key, ctx_val in context_value_map.items():
                        if value == ctx_val:
                            ctx_fill_match = ctx_key
                            break
                    if ctx_fill_match:
                        fill_value = f"context.get('{ctx_fill_match}', '{value.replace(chr(39), chr(92)+chr(39))}')"
                    else:
                        fill_value = self._maybe_parameterize(value, params)
```

注意：此修改只影响 `execute_skill` 函数体内的 fill 步骤生成，不影响 `rebuild_context` 中的 fill（那个已经在 Task 8 中处理）。

- [ ] **Step 3: 验证 Python 语法**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -c "import ast; ast.parse(open('RpaClaw/backend/rpa/generator.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add RpaClaw/backend/rpa/generator.py
git commit -m "feat: anti-hardcoding mechanism for fill steps using context value map"
```

---

## Self-Review

**Spec coverage:**
- Section 1 (DOM): Task 4 (层深), Task 5 (上限), Task 6 (噪声) ✓
- Section 2 (上下文鸿沟): Task 7 (get_rebuild_sequence), Task 8 (两阶段 rebuild), Task 9 (反硬编码) ✓
- Section 3 (对话框): Task 1 (textarea + IME) ✓
- Section 4 (重试): Task 2 (后端 SSE), Task 3 (前端事件处理) ✓

**Placeholder scan:** 无 TBD/TODO。

**Type consistency:** `context_ledger` 参数在 `generate_script` 中为 `Any`（避免循环导入），通过 `TYPE_CHECKING` 导入 `TaskContextLedger` 用于类型提示。`get_rebuild_sequence` 返回 `list[dict]`，与 generator 中的消费方式一致。
