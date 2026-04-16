# 修复 AI 指令模式生成脚本的两大问题

## Context

AI 指令模式在生成 Playwright 重放脚本时存在两个严重 Bug：
1. `ai_script` 步骤直接嵌入录制时的硬编码，对于数据收集/分页等动态场景未使用 `_ai_command` 运行时动态调用
2. `_inject_result_capture` 将结果捕获代码注入到了多行 JS 字符串内部，导致语法错误

---

## Bug 1: `ai_script` 步骤未智能使用 `_ai_command`

### 根因
`generator.py:276-286`，所有 `ai_script` 步骤一律直接嵌入 `step.value` 原始代码。对于数据收集、分页判断等需要根据运行时页面状态动态决策的场景，硬编码会导致重放失败。

### 判断策略
不是所有 `ai_script` 都需要转成 `_ai_command`，应智能判断：

| 场景 | 判断依据 | 处理方式 |
|------|----------|----------|
| 数据收集 | 有 `output_variable`，或有意义的 `data_value`，或代码含 `page.evaluate(` | `_ai_command("<prompt>", "data", ...)` + 捕获结果 |
| 动态操作（分页/条件判断） | 代码含 `for`/`while`/`if` 循环或条件逻辑 | `_ai_command("<prompt>", "execute", ...)` |
| 简单确定性操作 | 简单的 click/fill/navigate 等 | 保持嵌入原始代码（旧行为） |

### 实现方法

在 `generator.py` 中新增 `_should_use_ai_command(step)` 方法，分析步骤数据和代码内容：

```python
@classmethod
def _should_use_ai_command(cls, step: Dict[str, Any]) -> bool:
    """判断 ai_script 步骤是否应使用 _ai_command 动态重放。"""
    code = step.get("value", "")

    # 有明确的输出变量或数据值 → 数据收集，需动态
    output_var = step.get("output_variable") or ""
    data_value = step.get("data_value")
    if output_var and data_value and str(data_value).strip() not in {"", "ok", "None"}:
        return True

    # 代码含 page.evaluate → 数据提取，需动态
    if "page.evaluate(" in code or ".evaluate(" in code:
        return True

    # 代码含循环/条件 → 动态操作（分页等），需动态
    for pattern in ("for ", "while ", "if "):
        for line in code.split("\n"):
            stripped = line.strip()
            if stripped.startswith(pattern) and not stripped.startswith("#"):
                return True

    return False
```

修改 `ai_script` 处理逻辑（第 276-286 行）：

```python
if action == "ai_script":
    ai_code = step.get("value", "")
    if ai_code and self._should_use_ai_command(step):
        # 动态场景：使用 _ai_command
        effective_prompt = step.get("description") or step.get("prompt") or ""
        if effective_prompt:
            output_var = step.get("output_variable") or ""
            data_value = step.get("data_value")
            is_data = bool(output_var) and bool(data_value) and str(data_value).strip() not in {"", "ok", "None"}

            if is_data:
                result_key = output_var or f"ai_script_{step_index + 1}"
                step_lines.append(f'    {output_var} = await _ai_command("{self._escape(effective_prompt)}", "data", current_page, kwargs.get("_ai_token", ""), url=_ai_cmd_url)')
                step_lines.append(f'    _results["{result_key}"] = {output_var}')
            else:
                step_lines.append(f'    await _ai_command("{self._escape(effective_prompt)}", "execute", current_page, kwargs.get("_ai_token", ""), url=_ai_cmd_url)')

            lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
            lines.append("")
            continue

    # 向后兼容：简单操作或无 prompt 时嵌入原始代码
    if ai_code:
        converted = self._sync_to_async(ai_code)
        converted = self._inject_result_capture(converted)
        converted = self._strip_locator_result_capture(converted)
        for code_line in converted.split("\n"):
            step_lines.append(f"    {code_line}" if code_line.strip() else "")
    lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
    lines.append("")
    continue
```

---

## Bug 2: `_inject_result_capture` 破坏多行表达式

### 根因
`generator.py:976-995`，`_inject_result_capture` 逐行处理。当匹配到 `pr_items = await page.evaluate('''() => {` 后立即注入 `_results["pr_items"] = pr_items`，但 `page.evaluate(...)` 跨越多行，导致注入落到 JS 字符串内部。

### 修改方案
重写 `_inject_result_capture`，增加多行表达式状态跟踪：

1. **跟踪三引号状态**：检测 `'''` 和 `"""`，标记是否在多行字符串内
2. **跟踪括号平衡**：`paren_depth` 计数未闭合的 `(` 括号
3. **延迟注入**：赋值行处于未完成多行表达式时（`paren_depth > 0` 或 `in_triple_quote`），将变量名放入 `pending_var` 缓冲
4. **完成时注入**：表达式完成（`paren_depth <= 0` 且不在三引号内）时注入结果捕获

单行表达式保持原有即时注入行为不变。

---

## 不需要修改的文件

- `route/rpa.py` — 步骤数据已携带 `prompt`、`description`、`output_variable`、`data_value`
- `assistant.py` — ReAct agent 已在 `ai_script` 步骤中存储 `prompt` 和 `description`

---

## 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `RpaClaw/backend/rpa/generator.py` | 1. 新增 `_should_use_ai_command` 方法<br>2. 修改 `ai_script` 处理逻辑（智能判断是否用 `_ai_command`）<br>3. 重写 `_inject_result_capture` 处理多行表达式 |
| `RpaClaw/backend/tests/test_rpa_generator.py` | 更新受影响的测试 + 添加新测试 |

## 验证方式

1. `python -m pytest RpaClaw/backend/tests/test_rpa_generator.py -v`
2. 手动验证用户场景（收集 GitHub PR），确认：
   - 含 `page.evaluate` 的步骤生成 `_ai_command(...)` 调用
   - 简单操作步骤保持嵌入原始代码
   - 无 `_results["..."]` 注入到 JS 字符串内部
