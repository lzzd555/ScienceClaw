# AI 指令模式生成脚本修复（第二轮）

## Context

第一轮修复后，生成的脚本仍存在三个问题：

### 问题 1: 数据收集步骤使用了错误的 mode
```
await _ai_command("收集当前页面所有PR的标题和创建人", "execute", ...)
```
这是数据提取，应该用 `"data"` 模式，但代码中走了 `"execute"` 路径。原因：`is_data` 判断要求同时有 `output_variable` 和有意义的 `data_value`，但 ReAct agent 产生的 `ai_script` 步骤不一定有这两个字段。

### 问题 2: 收集到的数据没有整合到 `_results`
两次 `_ai_command` 调用都没有捕获返回值，`_results` 始终为 `{}`。因为走了 "execute" 路径，execute 模式返回 `None`，且没有赋值给变量。

### 问题 3: 分页逻辑硬编码
脚本硬编码了 "点击 Page 2" 和 "收集第二页数据"。当 PR 增多到 3 页以上时失效。需要动态分页。

---

## 已实施的修复

### 修复 1 & 2: 数据收集步骤自动使用 "data" 模式并捕获结果

**文件**: `generator.py` — `ai_script` 处理逻辑

含 `page.evaluate` 的步骤现在自动按 data 模式处理，无需 `output_variable`：
```python
has_evaluate = "page.evaluate(" in ai_code or ".evaluate(" in ai_code
is_data = has_evaluate or (bool(output_var) and bool(data_value) ...)
```

自动生成变量名和结果键：
```python
result_var = output_var or f"ai_result_{step_index + 1}"
result_key = output_var or f"ai_data_{step_index + 1}"
```

### 修复 3a: `_ai_command` execute 模式返回数据

**文件**: `generator.py` — `RUNNER_TEMPLATE_DOCKER` / `RUNNER_TEMPLATE_LOCAL`

execute 模式不再丢弃返回值：
```python
# Before: return None
# After:  return await _ns["__ai"]()
```

**文件**: `route/rpa.py` — `/rpa/ai-command` execute 模式 prompt

新增两条规则：
```
- 如果任务涉及数据收集，代码最后用 return 返回收集到的数据
- 如果需要翻页收集数据，自动循环翻页直到没有下一页
```

### 修复 3b: 生成器合并分页步骤

**文件**: `generator.py` — `generate_script` + `_detect_pagination_ranges`

新增三个辅助方法：
- `_is_data_collection_step()` — 判断步骤是否为数据收集
- `_is_page_navigation_step()` — 判断步骤是否为翻页操作（描述含 "page 2"、"下一页" 等关键词）
- `_detect_pagination_ranges()` — 检测 "数据收集 → 翻页 → 数据收集" 模式并返回合并映射

合并后生成单个调用：
```python
ai_result_1 = await _ai_command(
    "收集PR信息，如果有下一页请自动点击翻页并继续收集，直到没有下一页为止，返回所有数据的JSON数组",
    "execute", current_page, kwargs.get("_ai_token", ""), url=_ai_cmd_url
)
if ai_result_1:
    _results["ai_data_1"] = ai_result_1
```

---

## 测试结果

40 个测试全部通过，新增 2 个测试：
- `test_generate_script_merges_pagination_pattern_into_single_execute` — 分页合并
- `test_generate_script_ai_script_with_evaluate_uses_data_mode` — data 模式自动判断
