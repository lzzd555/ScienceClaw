# 生成脚本硬编码 + 多 action locator/重试修复

日期: 2026-04-21

## 概述

两个独立问题，涉及不同文件：

1. **生成脚本值硬编码** — 导出 skill 脚本中 ai_script 步骤的 fill 值写死了录制时的实际值，没有用 `context.get()`
2. **多 action locator 失败 + 无重试** — 多 action 提取时 `locator.inner_text()` 遇严格模式冲突直接异常，且循环内单个 intent 失败后无容错也无重试

---

## 1. 生成脚本值硬编码

### 问题

用户录制流程：GitHub trending 提取 3 个项目名 → 百度搜索填入。导出脚本中 fill 步骤是 ai_script，LLM 生成的代码直接写死了值：

```python
first_repo = "Fincept-Corporation/FinceptTerminal"
second_repo = "ruvnet/RuView"
third_repo = "thunderbird/thunderbolt"
combined_value = f"{first_repo};{second_repo};{third_repo}"
await page.get_by_role("textbox").fill(combined_value)
```

应生成：

```python
first_repo = context.get("first_repo_name", "Fincept-Corporation/FinceptTerminal")
second_repo = context.get("second_repo_name", "ruvnet/RuView")
third_repo = context.get("third_repo_name", "thunderbird/thunderbolt")
```

### 方案

在 `generator.py` 中增加 `_dehardcode_ai_script` 方法，处理 ai_script 步骤时扫描代码中的字符串赋值模式，匹配 `context_value_map` 中的已知值并替换为 `context.get()`。

**匹配规则**：仅匹配赋值模式 `= "value"` 或 `= 'value'`，避免误替换 URL 或注释中的内容。按值长度降序替换（更长的值优先），防止短值是长值的子串。

### 涉及文件

- `RpaClaw/backend/rpa/generator.py` — 新增 `_dehardcode_ai_script` 方法，在 ai_script 步骤生成时调用

---

## 2. 多 action locator 容错 + 单 intent 重试

### 问题

用户在 PR 单据页面同时提取 5 个字段，LLM 输出 5 个 extract_text intent。执行时：

1. 解析器找到正确的 value 节点（如 `<span data-field="requestor">李雨晨</span>`）
2. 但该节点的 locator 是 `get_by_text("李雨晨")`，页面上有 3 个元素匹配（value span + 2 个 td），触发 Playwright 严格模式冲突
3. `execute_structured_intent` 直接抛异常，`inner_text()` 从未执行
4. 多 action 循环中 catch 了异常但直接跳到下一个 intent，无容错无重试

### 方案：分层容错

**第一层 — Locator 容错**（`assistant_runtime.py`）：

在 `execute_structured_intent` 中，对 `extract_text`/`click`/`fill` 的 locator 调用加 `.first` 兜底。当 Playwright 严格模式冲突时，自动取第一个匹配元素。

```python
# 修改前
if action == "extract_text":
    output = await locator.inner_text()

# 修改后
if action == "extract_text":
    output = await locator.first.inner_text()
```

对 `click` 和 `fill` 同理加 `.first`。

**第二层 — Locator 优先用 CSS data-field**（`assistant_snapshot_runtime.py`）：

对带 `data-field` 属性的 value 节点，生成 CSS locator `[data-field="xxx"]` 代替 text locator。这比 `.first` 更精确，避免选到同文本的其他元素。

已在上一轮部分实现，确认逻辑完整。

**第三层 — 单 intent LLM 重试**（`assistant.py`）：

多 action 循环中，单个 intent 执行失败后（即使 locator 容错后仍失败）：

1. 收集错误信息
2. 对该 intent 构造重试消息（原始 intent + 错误信息），发送给 LLM 获取修正后的 intent
3. 用新 intent 重新执行一次
4. 仍然失败则记录错误继续下一个 intent

成功 intent 的结果保留，只重试失败的。

### 涉及文件

- `RpaClaw/backend/rpa/assistant_runtime.py` — `execute_structured_intent` 加 `.first`
- `RpaClaw/backend/rpa/assistant_snapshot_runtime.py` — data-field CSS locator（已实现）
- `RpaClaw/backend/rpa/assistant.py` — 多 action 循环内加单 intent 重试逻辑

---

## 实施优先级

1. **Locator 容错**（问题 2 第一层）— 最小改动，直接消除大部分严格模式冲突
2. **单 intent 重试**（问题 2 第三层）— 补充容错无法解决的场景
3. **生成脚本硬编码**（问题 1）— 独立改动，改善导出质量

## 测试要点

- **Locator 容错**：PR 单据页面提取 5 个字段，验证不再严格模式冲突
- **单 intent 重试**：模拟某个 intent 的 locator 无法解析，验证会触发 LLM 重试
- **生成脚本**：录制 GitHub trending → 百度搜索流程，验证导出脚本使用 `context.get()` 而非硬编码值
