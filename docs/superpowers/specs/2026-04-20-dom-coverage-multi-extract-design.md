# DOM 内容覆盖扩展 + 多值提取 + Agent 死代码清理

日期: 2026-04-20

## 概述

三项关联修复：

1. **DOM 内容选择器扩展** — 让 `<span>`/`<div>` 等数据容器可被快照捕获
2. **Chat 模式 prompt 多值提取指导** — 指导 LLM 对多值提取拆分为多步
3. **Agent 模式死代码删除** — 移除未使用的 `RPAReActAgent` 及相关代码

---

## 1. DOM 内容选择器扩展

### 问题

`SNAPSHOT_V2_JS` 的 `CONTENT` 选择器为：
```
h1,h2,h3,h4,h5,h6,th,td,dt,dd,li,p,label,[role=heading],[role=cell],[role=rowheader],[role=columnheader]
```

`<span>` 和 `<div>` 等常见数据容器不在其中。例如页面中的：
```html
<span class="field-value" data-field="requestor">联想华南直营服务中心</span>
<span class="field-value" data-field="department">华南大区</span>
```
完全不可见，LLM 无法定位和提取这些值。

### 方案

扩展 `CONTENT` 选择器，添加 `span`、`div`、`dd`、`figcaption`、`caption`、`time`、`mark`、`strong`、`em`。

同时增加过滤条件控制体积：
- 只收集有非空 `innerText` 且文本长度 ≥ 2 的节点（已在现有逻辑中通过 `if (!text)` 检查）
- `span`/`div` 类节点增加去重：如果文本与已有 content_node 完全相同则跳过
- 对 `span`/`div` 节点设置较低的上限权重，确保不会挤掉语义更重要的 heading/table 内容

### 涉及文件

- `RpaClaw/backend/rpa/assistant_snapshot_runtime.py` — `SNAPSHOT_V2_JS` 的 `CONTENT` 常量

---

## 2. Chat 模式 Prompt 多值提取指导

### 问题

`SYSTEM_PROMPT` 指导 LLM 每次返回一个 JSON action。当用户说"提取购买人、使用部门和供应商"时，LLM 只能生成一个 `extract_text` + 单个 `result_key`，无法一次提取多个值。

### 方案

在 `SYSTEM_PROMPT`（chat 模式）中添加规则：

> 8. When the user asks to extract multiple values (e.g. "提取购买人、使用部门和供应商"), output an array of JSON actions instead of a single object. Each action should have its own `result_key` and `target_hint`. Example:
> ```json
> [
>   {"action": "extract_text", "result_key": "requestor", "target_hint": {"name": "购买人"}, ...},
>   {"action": "extract_text", "result_key": "department", "target_hint": {"name": "使用部门"}, ...},
>   {"action": "extract_text", "result_key": "supplier", "target_hint": {"name": "供应商"}, ...}
> ]
> ```

后端 `process_message` 需要处理 LLM 返回数组的情况。当前 `_extract_structured_intent` 只解析单个 JSON 对象，需要增加数组解析路径：遍历数组中的每个 action，依次执行。

### 涉及文件

- `RpaClaw/backend/rpa/assistant.py` — `SYSTEM_PROMPT` 添加规则
- `RpaClaw/backend/rpa/assistant.py` — `_extract_structured_intent` 或 `process_message` 处理数组

---

## 3. Agent 模式死代码删除

### 问题

前端硬编码 `mode: 'chat'`（RecorderPage.vue:593），没有任何 UI 入口触发 `mode: 'react'`。`RPAReActAgent` 类、`REACT_SYSTEM_PROMPT`、`_active_agents` 字典、路由中的 react 分支和相关端点全部是死代码。

### 方案

删除以下内容：

**assistant.py:**
- `REACT_SYSTEM_PROMPT` 常量（约 L277-314）
- `RPAReActAgent` 类（约 L319-571）
- `_active_agents` 字典（L18）
- 导出中移除 `RPAReActAgent` 和 `_active_agents`

**route/rpa.py:**
- 删除 `RPAReActAgent` 和 `_active_agents` 的 import
- 删除 chat 端点中的 `if request.mode == "react"` 分支（约 L558-585）
- 删除 `/session/{session_id}/agent/confirm` 端点（约 L613-621）
- 删除 `/session/{session_id}/agent/abort` 端点（约 L625-630）

### 涉及文件

- `RpaClaw/backend/rpa/assistant.py` — 删除 agent 相关代码
- `RpaClaw/backend/route/rpa.py` — 删除 agent 路由和 import

---

## 实施优先级

1. **Agent 死代码删除**（第 3 节）— 最简单，减少后续改动干扰
2. **DOM 选择器扩展**（第 1 节）— 独立改动，立即改善提取能力
3. **多值提取 prompt + 后端**（第 2 节）— 依赖 DOM 选择器扩展后的快照质量

## 测试要点

- **DOM**: 用用户提供的 PR 单据页面验证 `<span data-field="requestor">` 等字段出现在 content_nodes 中
- **多值**: 发送"提取购买人、使用部门和供应商"，验证生成多个独立 extract_text 步骤
- **Agent 删除**: 验证 chat 模式不受影响，所有 API 端点正常
