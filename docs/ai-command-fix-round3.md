# AI 指令模式生成脚本修复（第三轮）

## Context

第二轮修复后仍存在三个问题：

### 问题 1: execute 模式越俎代庖
execute prompt 不应包含分页/数据收集指令。execute 专注于操作，data 专注于收集数据，职责要分明。

### 问题 2: 分页合并方案设计有误
将分页步骤合并为一个 execute 调用是错误的。正确做法是保持自然的 "操作→收集→操作→收集" 流程。

### 问题 3: 缺少数据整合
多次 `_ai_command("data", ...)` 的结果是孤立的。用户要求收集到的**实际内容**整合为一个数组，而不是将各次回答简单拼接。

---

## 修复方案

### 理想生成的脚本

```python
async def execute_skill(page, **kwargs):
    _results = {}
    _collected = {}  # 中间数据暂存
    _ai_cmd_url = kwargs.get('_ai_command_url', _AI_COMMAND_URL)

    # 导航步骤（保留原步骤）
    await current_page.goto("https://github.com/.../pulls?q=is%3Apr")
    await current_page.wait_for_load_state("domcontentloaded")

    # 第1页数据收集 → data 模式，结果存入 _collected
    _collected["step_1"] = await _ai_command(
        "收集当前页面所有PR的标题和创建人", "data",
        current_page, kwargs.get("_ai_token", ""), url=_ai_cmd_url
    )

    # 翻页操作（保留原步骤）
    await current_page.get_by_role("link", name="Page 2", exact=True).click()
    await current_page.wait_for_timeout(500)

    # 第2页数据收集 → data 模式，结果存入 _collected
    _collected["step_2"] = await _ai_command(
        "收集当前页面所有PR的标题和创建人", "data",
        current_page, kwargs.get("_ai_token", ""), url=_ai_cmd_url
    )

    # 总结：整合所有分步收集的数据
    _summary_ctx = _json.dumps(_collected, ensure_ascii=False, default=str)
    _results["summary"] = await _ai_command(
        "请将以下分步收集的数据整合为一个完整的JSON数组，每项包含title和author字段",
        "data", current_page, kwargs.get("_ai_token", ""),
        url=_ai_cmd_url, context=_summary_ctx
    )

    return _results
```

### 关键设计

#### `_ai_command` 增加 `context` 参数

当前 `_ai_command` 总是从 `page.inner_text("body")` 获取页面上下文。但总结调用需要传的是**已收集的数据**而非当前页面文本。

修改 `_ai_command` 模板签名：
```python
async def _ai_command(prompt: str, mode: str, page, token: str, url: str = None, context: str = None):
    _target_url = url or _AI_COMMAND_URL
    _ctx = ""
    if context is not None:
        # 使用调用方传入的自定义上下文（如已收集的数据）
        _ctx = context
    else:
        # 默认从当前页面抓取
        try:
            _ctx = await page.inner_text("body")
            if len(_ctx) > 50000:
                _ctx = _ctx[:50000]
        except Exception:
            pass
    # ... 其余不变
```

后端 `AICommandRequest` 已有 `page_context` 字段，无需修改。`_ai_command` 将 `context` 传入 `page_context` 即可。

#### 总结调用的 prompt 构造

总结调用需要两个信息：
1. **原始目标**（如"收集所有PR的创建人和标题"）— 让 AI 知道该输出什么结构
2. **分步收集的原始数据** — 通过 `context` 参数传入

prompt 格式：
```
原始任务：{从首次 data 步骤的 step.prompt 提取}
请将以下分步收集的数据整合为最终的JSON格式结果
```

原始目标从步骤链中提取：扫描所有 `ai_script`/`ai_command` 步骤，取第一个有 `prompt` 字段的步骤的 `prompt` 值作为原始目标。

#### 总结调用的 context 传入

```python
_summary_ctx = _json.dumps(_collected, ensure_ascii=False, default=str)
```

这样后端 data 模式收到的 `page_context` 就是已收集的数据，AI 基于这些数据生成整合结果。

---

## 具体修改

### 1. 回退 execute prompt（`route/rpa.py`）

移除之前添加的两条规则，恢复 execute 模式为纯粹的操作指令。

### 2. 修改 `_ai_command` 模板（`generator.py`）

两个模板（DOCKER / LOCAL）中 `_ai_command` 函数增加 `context: str = None` 参数，优先使用传入的 context。

### 3. 移除分页合并逻辑（`generator.py`）

删除：
- `_is_data_collection_step()` 方法
- `_is_page_navigation_step()` 方法
- `_detect_pagination_ranges()` 方法
- `generate_script` 中的 `_merged_ranges` 相关代码

### 4. data 结果存入 _collected（`generator.py`）

- 当存在 data 模式调用时，在 `execute_skill` 开头添加 `_collected = {}`
- data 模式结果存入 `_collected[f"step_{step_index + 1}"]` 而非 `_results`
- execute 模式结果不做特殊处理（操作类，无需收集）

### 5. 添加总结步骤（`generator.py`）

在 `return _results` 前，当 `_collected` 非空时生成总结调用：
```python
if _collected:
    _summary_ctx = _json.dumps(_collected, ensure_ascii=False, default=str)
    _results["summary"] = await _ai_command(
        "{original_goal}\n请将以下分步收集的数据整合为最终的JSON格式结果",
        "data", current_page, kwargs.get("_ai_token", ""),
        url=_ai_cmd_url, context=_summary_ctx
    )
```

### 6. 回退 execute 模式返回值（`generator.py`）

将 `return await _ns["__ai"]()` 改回 `await _ns["__ai"](); return None`。
execute 模式不需要返回数据，返回值由总结步骤统一处理。

---

## 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `generator.py` | 1. `_ai_command` 增加 context 参数<br>2. 移除分页合并逻辑<br>3. data 结果存 _collected<br>4. 添加总结步骤<br>5. execute 恢复返回 None |
| `route/rpa.py` | 回退 execute prompt 的分页/数据收集指令 |
| `test_rpa_generator.py` | 更新/新增测试 |
