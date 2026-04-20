# RPA 上下文账本、DOM 快照与对话框修复

日期: 2026-04-20

## 概述

RPA 录制系统的四项关联修复：

1. **DOM 快照精简** - 移除层深限制与噪声内容
2. **上下文账本录制/生成态鸿沟** - 修复重建准确性与硬编码问题
3. **录制对话框体验** - 添加多行支持与输入法感知
4. **录制态重试机制完善** - 重试过程可视，最终结果真实反映执行情况

---

## 1. DOM 快照精简

### 问题

- `findUniqueAnchor` (assistant_runtime.py:80) 限制祖先遍历深度为 4 层，深层嵌套元素找不到唯一锚点
- `findCollectionContext` (assistant_runtime.py:89) 限制为 6 层，深层列表/表格无法正确归组
- 节点数量硬上限（120 可操作、160 内容、80 元素）导致复杂页面数据被截断丢失
- CSS 类名、样式属性等非语义内容污染快照，增加 LLM 推断难度

### 方案

#### 1.1 取消层深硬限制

在 `assistant_runtime.py` (`EXTRACT_ELEMENTS_JS`) 中：

**`findUniqueAnchor`** (L78-85):
- 移除 `depth < 4` 条件
- 持续向上遍历直到 `document.body`
- 保留安全上限 20 层，防止畸形 DOM 无限循环

```javascript
function findUniqueAnchor(start) {
    let current = start;
    for (let depth = 0; current && current !== document.body && depth < 20; depth++, current = current.parentElement) {
        const sel = stableSelector(current);
        if (selectorIsUnique(sel)) return { node: current, selector: sel };
    }
    return null;
}
```

**`findCollectionContext`** (L86-109):
- 移除 `depth < 6` 条件
- 同样设置 20 层安全上限

```javascript
function findCollectionContext(el) {
    let repeatedRoot = null;
    let current = el.parentElement;
    for (let depth = 0; current && current !== document.body && depth < 20; depth++, current = current.parentElement) {
        if (countSiblingMatches(current) >= 2) {
            repeatedRoot = current;
            break;
        }
    }
    // ... 其余逻辑不变
}
```

#### 1.2 节点上限自适应

在 `assistant_snapshot_runtime.py` (`SNAPSHOT_V2_JS`) 中：

在主循环之前统计页面元素总量：
```javascript
const totalActionable = document.querySelectorAll(ACTIONABLE).length;
const totalContent = document.querySelectorAll(CONTENT).length;
const ACTIONABLE_CAP = Math.min(totalActionable, 300);
const CONTENT_CAP = Math.min(totalContent, 400);
```

替换硬编码上限：
- L286: `120` → `ACTIONABLE_CAP`
- L324: `160` → `CONTENT_CAP`

在 `assistant_runtime.py` (`EXTRACT_ELEMENTS_JS`) 中：
```javascript
const totalInteractive = document.querySelectorAll(INTERACTIVE).length;
const ELEMENT_CAP = Math.min(totalInteractive, 200);
```
- L145: `80` → `ELEMENT_CAP`

#### 1.3 过滤快照噪声

在 `SNAPSHOT_V2_JS` 中：

可操作节点的 `element_snapshot` (L273-279) - 只保留语义字段：
```javascript
element_snapshot: {
    tag: el.tagName.toLowerCase(),
    text,
    role,
    name,
    href: normalizeText(el.getAttribute('href') || '', 120),
},
```

`locator_candidates` 中 CSS 类名型选择器降权：
- `buildFallbackLocator` (L152-188): CSS 回退策略移至最后备选
- 候选项 `method` 为 `css` 且值包含 `.`（类选择器）时，标记 `priority: -1`

在 `EXTRACT_ELEMENTS_JS` 中：

`stableSelector` 函数 (L46-53)：
- 移除基于 class 的分支（`classes.length` 判断）
- 只保留 `#id` 和 `[role="..."]` 模式
- 回退为纯标签名而非类名组合

```javascript
function stableSelector(el) {
    const tag = el.tagName.toLowerCase();
    if (el.id && !isGuidLike(el.id)) return `${tag}#${cssEsc(el.id)}`;
    const role = el.getAttribute('role');
    if (role) return `${tag}[role="${cssEsc(role)}"]`;
    return tag;
}
```

### 涉及文件

- `RpaClaw/backend/rpa/assistant_runtime.py` — `EXTRACT_ELEMENTS_JS`
- `RpaClaw/backend/rpa/assistant_snapshot_runtime.py` — `SNAPSHOT_V2_JS`

---

## 2. 上下文账本录制态/生成态鸿沟

### 问题

- `rebuild_context()` (generator.py:394) 仅从步骤的 `context_writes` 构建，完全忽略 `TaskContextLedger.rebuild_actions` 和元数据
- 录制时 LLM 能隐式理解的值（如"把 CSRF token 带到下一页"）在生成代码时丢失
- 生成的代码中出现硬编码字面量，而非从 `context` 参数读取

### 方案

#### 2.1 生成器消费 Ledger 元数据

`generator.py` 方法签名变更：

```python
def generate_script(
    self,
    steps: List[Dict[str, Any]],
    params: Dict[str, str],
    *,
    context_ledger: Optional[TaskContextLedger] = None,
    # ... 其余现有参数
) -> str:
```

`skill_exporter.py` 调用时传入 ledger：
```python
session = rpa_manager.sessions.get(session_id)
context_ledger = session.context_ledger if session else None
script = generator.generate_script(steps, params, context_ledger=context_ledger, ...)
```

#### 2.2 Ledger 新增辅助方法

在 `context_ledger.py` 中新增：

```python
def get_rebuild_sequence(self) -> list[dict]:
    """按依赖顺序返回重建动作列表，供代码生成器消费。

    每个条目包含：
    - action: 动作类型（navigate, extract_text 等）
    - description: 可读描述
    - writes: 此动作产生的上下文 key
    - source_step_id: 产生原始值的步骤 ID
    - value: 观察/推导的值（如可用）
    - url: 对于 navigate 类型，页面 URL
    """
    sequence = []
    seen_keys = set()

    # 第一优先级：rebuild_actions（录制时由 assistant 显式记录）
    for action in self.rebuild_actions:
        entry = {
            "action": action.action,
            "description": action.description,
            "writes": list(action.writes),
            "source_step_id": action.step_ref,
        }
        # navigate 动作从 description 提取 URL
        if action.action == "navigate":
            entry["url"] = action.description
        sequence.append(entry)
        seen_keys.update(action.writes)

    # 第二优先级：rebuild_actions 未覆盖的用户显式观察值
    for key, cv in self.observed_values.items():
        if key not in seen_keys and cv.user_explicit:
            sequence.append({
                "action": "observe",
                "description": f"观察值: {key}",
                "writes": [key],
                "source_step_id": cv.source_step_id,
                "value": cv.value,
            })
            seen_keys.add(key)

    # 第三优先级：未覆盖的运行时必需推导值
    for key, cv in self.derived_values.items():
        if key not in seen_keys and cv.runtime_required:
            sequence.append({
                "action": "derive",
                "description": f"推导值: {key}",
                "writes": [key],
                "source_step_id": cv.source_step_id,
                "value": cv.value,
            })

    return sequence
```

#### 2.3 rebuild_context 两阶段构建

在 `generator.py` 中替换当前 rebuild_context 循环：

```python
# 阶段 A：从 context ledger 构建（如果可用）
if context_ledger:
    rebuild_sequence = context_ledger.get_rebuild_sequence()
    for idx, entry in enumerate(rebuild_sequence):
        action = entry["action"]
        writes = entry.get("writes", [])

        if action == "navigate":
            url = entry.get("url", "")
            if url and url != rebuild_prev_url:
                rebuild_lines.append(f'    await current_page.goto("{url}")')
                rebuild_lines.append('    await current_page.wait_for_load_state("domcontentloaded")')
                rebuild_prev_url = url

        elif action == "extract_text":
            # 通过 source_step_id 查找对应步骤获取定位器信息
            source_step_id = entry.get("source_step_id")
            matching_step = next(
                (s for s in deduped if s.get("id") == source_step_id), None
            )
            if matching_step:
                # 复用现有提取逻辑（target, frame_path, locator）
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

        elif action in ("observe", "derive"):
            # 录制时的静态值 — 直接注入
            value = entry.get("value")
            for ctx_key in writes:
                ctx_key_safe = ctx_key.replace("'", "\\'")
                rebuild_lines.append(f'    context["{ctx_key_safe}"] = {repr(value)}')

    # 阶段 B：从步骤 context_writes 补充（向后兼容）
    already_written = set()
    for entry in context_ledger.get_rebuild_sequence():
        already_written.update(entry.get("writes", []))

    for rebuild_idx, step in enumerate(deduped):
        context_writes = step.get("context_writes") or []
        # 只添加 Ledger 未覆盖的写入
        new_writes = [k for k in context_writes if k not in already_written]
        if not new_writes:
            continue
        # ... 使用现有的导航和提取逻辑 ...
```

如果 Ledger 不可用，回退到现有行为（仅从步骤构建）。

#### 2.4 反硬编码机制

在 `generate_script` 中添加预处理步骤：

```python
def _build_context_value_map(self, steps: List[Dict[str, Any]]) -> Dict[str, str]:
    """构建上下文 key 到录制值的映射，用于防止硬编码。"""
    value_map = {}
    for step in steps:
        for ctx_key in step.get("context_writes", []):
            output = step.get("output", "")
            if output:
                value_map[ctx_key] = output
    return value_map
```

在 `_maybe_parameterize` 或步骤值插入时检查此映射：
- 如果步骤的 `value` 匹配已知上下文值，生成 `context.get("key", "fallback")` 而非字面量
- 对于 `fill` 步骤：如果要填入的值对应某个上下文 key，使用 `context.get("key", default_value)`

### 涉及文件

- `RpaClaw/backend/rpa/context_ledger.py` — 新增 `get_rebuild_sequence()`
- `RpaClaw/backend/rpa/generator.py` — 两阶段重建、反硬编码
- `RpaClaw/backend/rpa/assistant.py` — 确保 rebuild_actions 完整填充
- `RpaClaw/backend/rpa/skill_exporter.py` — 传递 ledger 给生成器

---

## 3. 录制对话框多行 + 输入法支持

### 问题

- `RecorderPage.vue` (L984-991) 使用 `<input type="text">` — 不支持多行输入
- `@keyup.enter="sendMessage"` 在输入法合成期间触发，导致未输入完成就发送消息
- 无 `compositionstart`/`compositionend` 事件处理

### 方案

#### 3.1 替换 input 为 textarea

在 `RecorderPage.vue` (L984-991) 中替换为：

```vue
<textarea
  ref="messageInputRef"
  v-model="newMessage"
  @compositionstart="isComposing = true"
  @compositionend="isComposing = false"
  @keydown="handleMessageKeydown"
  :disabled="sending || agentRunning"
  rows="1"
  class="w-full bg-white dark:bg-[#272728] border border-gray-200 dark:border-gray-700
         rounded-2xl py-3 pl-4 pr-12 text-xs resize-none overflow-hidden
         focus:ring-2 focus:ring-[#831bd7] focus:border-transparent shadow-sm
         placeholder:text-gray-400 outline-none disabled:opacity-50"
  :placeholder="agentRunning ? 'AI 正在执行录制任务...' : (sending ? 'AI 正在处理...' : '向助手提问...')"
/>
```

#### 3.2 输入法 + 按键处理

在 `<script setup>` 中新增：

```typescript
const isComposing = ref(false)
const messageInputRef = ref<HTMLTextAreaElement | null>(null)

function handleMessageKeydown(event: KeyboardEvent) {
  if (isComposing.value) return  // 输入法合成中不处理
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    sendMessage()
  }
  // Shift+Enter 默认行为即换行
}
```

#### 3.3 自动调整高度

```typescript
watch(newMessage, () => {
  nextTick(() => {
    const el = messageInputRef.value
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 120) + 'px'
    }
  })
})
```

最大高度 120px，超出后出现滚动条。参考 ChatBox 组件的成熟模式。

### 涉及文件

- `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue` — 输入区域 (L982-999) + script 变量

---

## 4. 录制态 AI 步骤重试机制完善

### 问题

当前 `assistant.py` 的 `_execute_with_retry` (L768-813) 存在三个缺陷：

1. **重试过程不可见**：首次执行失败后，LLM 的修正推理流式生成但不转发给前端；前端只收到静态文本 `"Execution failed. Retrying."`
2. **重试日志缺失**：修正后的代码、重新执行的状态、执行错误信息均未展示
3. **最终结果不真实**：无论首次成功还是重试成功，前端只看到一个 `"done"` 状态；重试也失败时只返回最后错误，丢失首次失败原因

### 现有流程

```
首次执行 → 失败
  → 构造修正消息发给 LLM（流式生成但不转发前端）
  → 用修正响应重新执行
  → 返回 retry_notice 静态文本 + 最终结果
```

前端收到的事件序列：
```
message_chunk: "原始 LLM 响应..."
executing: {}
message_chunk: "\n\nExecution failed. Retrying.\n\n"  ← 只有这行，无任何修正细节
result: { success: true/false, ... }
```

### 方案

#### 4.1 后端：重试过程通过 SSE 事件推送

将 `_execute_with_retry` 从返回 `retry_notice` 字符串改为通过生成器 yield 事件。

**`assistant.py` 的 `process_message` 方法改造**：

`_execute_with_retry` 当前是 `await` 的普通异步方法。需要将其拆分为一个可中途 yield 事件的流程。核心思路：

```python
# 在 process_message 中，替换现有的 _execute_with_retry 调用
# 将重试逻辑内联，以便在重试时 yield 事件

async def process_message(self, ..., stream_callback=None):
    # ... 首次 LLM 响应流式输出（不变） ...
    yield {"event": "executing", "data": {}}

    # 首次执行
    current_page = page_provider() if page_provider else page
    try:
        result, code, resolution, context_reads = await self._execute_single_response(
            current_page, snapshot, full_response, context_ledger
        )
        if result["success"]:
            # 首次成功，直接返回
            retry_notice = ""
    except Exception as exc:
        result = {"success": False, "error": str(exc), "output": ""}
        code = None
        resolution = None

    # 首次失败 → 进入重试流程
    if not result.get("success"):
        # ① 通知前端：首次执行失败
        yield {
            "event": "retry_start",
            "data": {"original_error": result.get("error", "")}
        }

        # ② 流式推送 LLM 修正推理
        retry_messages = messages + [
            {"role": "assistant", "content": full_response},
            {"role": "user", "content": f"Execution error: {result['error']}\nPlease fix it and retry."},
        ]
        retry_response = ""
        async for chunk_text in self._stream_llm(retry_messages, model_config):
            retry_response += chunk_text
            yield {"event": "retry_chunk", "data": {"text": chunk_text}}

        # ③ 通知前端：开始重新执行
        yield {"event": "retry_executing", "data": {}}

        current_page = page_provider() if page_provider else page
        if current_page is None:
            result = {"success": False, "error": "No active page", "output": ""}
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

        final_response = retry_response

        # ④ 通知前端：重试结果
        yield {
            "event": "retry_result",
            "data": {
                "success": result.get("success", False),
                "error": result.get("error"),
            }
        }
    else:
        final_response = full_response

    # ... 后续 context_writes 计算和 result 事件（不变） ...
```

#### 4.2 新增 SSE 事件类型

| 事件 | 数据 | 说明 |
|------|------|------|
| `retry_start` | `{ original_error: string }` | 首次执行失败，包含错误信息 |
| `retry_chunk` | `{ text: string }` | LLM 修正推理的流式文本 |
| `retry_executing` | `{}` | 修正完成，开始重新执行 |
| `retry_result` | `{ success: bool, error?: string }` | 重试执行结果 |

#### 4.3 前端：处理重试事件

在 `RecorderPage.vue` 的 SSE 事件处理循环中（L605 附近），新增事件处理：

```typescript
} else if (eventType === 'retry_start') {
    // 显示首次失败信息和重试开始
    const errMsg = data.original_error || '未知错误';
    chatMessages.value[msgIdx].text += `\n\n执行失败: ${errMsg}`;
    chatMessages.value[msgIdx].status = 'retrying';
    chatMessages.value[msgIdx].text += '\n\n正在修正并重试...';
} else if (eventType === 'retry_chunk') {
    // 流式追加 LLM 修正推理
    chatMessages.value[msgIdx].text += data.text || '';
} else if (eventType === 'retry_executing') {
    // 重试执行中
    chatMessages.value[msgIdx].status = 'executing';
    chatMessages.value[msgIdx].text += '\n\n修正完成，重新执行中...';
} else if (eventType === 'retry_result') {
    // 重试结果（注意：最终 result 事件仍会正常发送）
    if (data.success) {
        chatMessages.value[msgIdx].text += '\n重试成功';
    } else {
        chatMessages.value[msgIdx].text += `\n重试失败: ${data.error || ''}`;
    }
}
```

#### 4.4 结果事件增强

现有的 `result` 事件中增加重试元信息，确保前端能区分首次成功和重试成功：

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
        "retried": retried,          # 新增：是否经过了重试
        "original_error": original_error,  # 新增：首次失败原因（如重试）
    },
}
```

前端 `result` 事件处理也相应更新：

```typescript
} else if (eventType === 'result') {
    chatMessages.value[msgIdx].status = data.success ? 'done' : 'error';
    if (data.error) chatMessages.value[msgIdx].error = data.error;
    // 显示执行结果（重试场景下追加在修正文本之后）
    if (data.output && data.output !== 'ok' && data.output !== 'None') {
        chatMessages.value[msgIdx].text += `\n输出: ${data.output}`;
    }
    if (Array.isArray(data.context_writes) && data.context_writes.length > 0) {
        chatMessages.value[msgIdx].text += `\n已记录上下文变量: ${data.context_writes.join(', ')}`;
    }
    // 重试元信息
    if (data.retried) {
        const retryStatus = data.success ? '重试后成功' : '重试后仍然失败';
        chatMessages.value[msgIdx].text += `\n[${retryStatus}]`;
    }
}
```

### 涉及文件

- `RpaClaw/backend/rpa/assistant.py` — `process_message` 重试逻辑内联化、新增 SSE 事件
- `RpaClaw/frontend/src/pages/rpa/RecorderPage.vue` — SSE 事件处理新增 `retry_*` 类型

---

## 实施优先级

1. **对话框修复**（第 3 节）— 改动最小，体验提升最大，独立于其他改动
2. **重试机制完善**（第 4 节）— 改动较小，体验提升显著，与第 3 节独立
3. **DOM 精简**（第 1 节）— 改动中等，为录制和生成提供更好的推断基础
4. **上下文鸿沟修复**（第 2 节）— 改动最大，依赖 DOM 精简后更好的快照质量

## 测试要点

- **DOM**: 在复杂页面（深层嵌套、大量元素）验证快照完整性
- **上下文**: 录制跨页面数据传递的技能，验证生成的 `rebuild_context` 准确性
- **对话框**: 测试中文/日文输入法，确认合成期间 Enter 不触发发送，Shift+Enter 正常换行
- **重试**: 故意触发 AI 步骤执行失败（如访问不存在的元素），验证前端展示完整重试流程（失败信息 → LLM 修正推理 → 重新执行 → 真实结果）
