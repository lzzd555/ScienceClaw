# API Monitor - API 提取改进设计

**日期**: 2026-04-23
**状态**: Draft
**范围**: `RpaClaw/backend/rpa/api_monitor/`

## 问题概述

当前 API Monitor 的 API 提取功能存在三个核心问题：

1. **参数缺失** — 用户在搜索时未填写表单字段，导致捕获到的请求没有查询参数。LLM 无法推断出完整的参数 schema（如"订单名称"、"订单ID"等搜索字段）。
2. **工具重复** — "搜索"和"重置"按钮调用同一 API（`GET /api/orders`），生成重复的工具定义。
3. **噪音过滤不足** — `.glb`、`.wasm` 等非 API 资源通过 `fetch()` 加载时被误捕获。

### 根因

- **参数缺失**：LLM 生成工具定义时只看到网络请求数据，看不到页面 DOM 中的表单结构（输入框、标签、placeholder 等）。
- **工具重复**：`dedup_key` 函数对 URL 的规范化不够，`/api/orders?` 和 `/api/orders` 被视为不同的 key。
- **噪音**：`STATIC_EXTENSIONS` 集合缺少 `.glb`、`.gltf`、`.wasm`、`.bin` 等现代 Web 资源类型。

## 设计方案

### 1. DOM 上下文注入

在 LLM 生成工具定义时，扫描页面 DOM 并传入上下文，让 LLM 结合网络请求和 DOM 结构推断完整参数。

#### 1.1 DOM 扫描 JS 脚本

在 `manager.py` 中新增 `_SCAN_DOM_CONTEXT_JS`，扫描页面的表单和输入元素：

```javascript
() => {
    const result = { forms: [], inputs: [], buttons: [] };

    // 扫描所有表单
    for (const form of document.querySelectorAll('form')) {
        const inputs = [];
        for (const input of form.querySelectorAll('input, select, textarea')) {
            const label = form.querySelector(`label[for="${input.id}"]`)
                || input.closest('.search-item, .form-group, .field')?.querySelector('label')
                || input.previousElementSibling?.tagName === 'LABEL' ? input.previousElementSibling : null;
            inputs.push({
                name: input.name || input.id || '',
                type: input.type || input.tagName.toLowerCase(),
                label: label?.textContent?.trim() || '',
                placeholder: input.placeholder || '',
                required: input.required || false,
                options: input.tagName === 'SELECT'
                    ? [...input.options].map(o => ({ value: o.value, text: o.textContent.trim() }))
                    : undefined,
            });
        }
        result.forms.push({
            action: form.action || '',
            method: (form.method || 'GET').toUpperCase(),
            inputs,
            submitText: form.querySelector('button[type="submit"], input[type="submit"]')?.textContent?.trim() || '',
        });
    }

    // 扫描不在 form 内的独立输入框（关联到最近的按钮）
    const standaloneInputs = document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"])');
    for (const input of standaloneInputs) {
        if (input.closest('form')) continue;  // 已在 form 中
        const container = input.closest('.search-item, .form-group, .field, .input-group')
            || input.parentElement;
        const label = container?.querySelector('label')
            || input.previousElementSibling?.tagName === 'LABEL' ? input.previousElementSibling : null;
        result.inputs.push({
            id: input.id || '',
            name: input.name || '',
            type: input.type || 'text',
            label: label?.textContent?.trim() || '',
            placeholder: input.placeholder || '',
        });
    }

    // 扫描按钮（带 onclick 或事件绑定）
    for (const btn of document.querySelectorAll('button, [role="button"]')) {
        if (btn.closest('form')) continue;  // 已在 form 中
        const text = btn.textContent?.trim() || '';
        const onclick = btn.getAttribute('onclick') || '';
        if (text) {
            result.buttons.push({ text, onclick });
        }
    }

    return result;
}
```

#### 1.2 Manager 层面集成

在 `manager.py` 的 `_generate_tools_from_calls` 方法中：

1. 在调用 `generate_tool_definition` 之前，通过 `page.evaluate(_SCAN_DOM_CONTEXT_JS)` 扫描 DOM
2. 将扫描结果序列化为 JSON 字符串
3. 传递给 `generate_tool_definition` 的新参数 `dom_context`

```python
async def _generate_tools_from_calls(self, session_id, calls, source="auto", model_config=None):
    page = self._pages.get(session_id)
    dom_context = ""
    if page:
        try:
            dom_data = await page.evaluate(_SCAN_DOM_CONTEXT_JS)
            dom_context = json.dumps(dom_data, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[ApiMonitor] DOM scan failed: %s", exc)

    # ... existing grouping logic ...

    for key, group_calls in groups.items():
        yaml_def = await generate_tool_definition(
            method=method,
            url_pattern=url_pattern,
            samples=samples,
            page_context=session.target_url or "",
            dom_context=dom_context,  # 新参数
            model_config=model_config,
        )
```

#### 1.3 LLM Prompt 更新

更新 `llm_analyzer.py` 中的 `generate_tool_definition` 函数和 prompt：

**`TOOL_GEN_SYSTEM` 新增指引**：

```
DOM Context Guidelines:
- If the captured API request has missing or empty parameters but the DOM context shows
  related form inputs/fields, include those as optional parameters in the tool definition
- Use the label text and placeholder text from form inputs to generate parameter descriptions
- Map input types to JSON schema types: text -> string, number -> integer/number,
  date -> string (format: date), checkbox -> boolean, select -> enum
- If the same API endpoint is triggered by multiple buttons (e.g., "Search" and "Reset"),
  generate only ONE tool that covers all use cases, with optional parameters
```

**`TOOL_GEN_USER` 模板更新**：

```
Endpoint: {method} {url_pattern}
Page context: {page_context}

DOM context (form structure):
{dom_context}

API call samples:
{samples_json}

Generate the YAML tool definition. Use DOM context to infer parameters not present in samples.
```

### 2. 智能 URL 去重

#### 2.1 URL 规范化函数

在 `network_capture.py` 中新增 `_normalize_url_for_dedup` 函数：

```python
def _normalize_url_for_dedup(url: str) -> str:
    """Normalize URL for deduplication purposes."""
    parsed = urlparse(url)
    path = parsed.path.rstrip('?')

    # 去除空查询参数并排序
    if parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=True)
        # 过滤掉空值参数
        filtered = {k: v for k, v in qs.items() if any(vv for vv in v)}
        if filtered:
            sorted_params = sorted(filtered.items())
            query = urlencode(sorted_params, doseq=True)
            return f"{path}?{query}"
    return path
```

#### 2.2 修改 `dedup_key` 函数

```python
def dedup_key(call: CapturedApiCall) -> str:
    """Return a deduplication key for grouping similar API calls."""
    # 先参数化 URL，再规范化
    pattern = call.url_pattern or parameterize_url(call.request.url)
    # 参数化后的 URL 再做一次规范化
    normalized = _normalize_url_for_dedup(pattern)
    return f"{call.request.method} {normalized}"
```

### 3. 噪音过滤增强

扩展 `STATIC_EXTENSIONS` 集合，仅添加**页面渲染资源**类型的扩展名。

**不纳入过滤的文件类型**：`.pdf`、`.doc`、`.docx`、`.xls`、`.xlsx`、`.zip`、`.tar`、`.gz` 等。这些文件类型的下载 URL 本身就是有效的 API 端点（如"下载报告"、"导出数据"），过滤它们会导致漏掉下载类工具。

```python
STATIC_EXTENSIONS: Set[str] = {
    # 现有的 — 页面渲染资源
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff",
    ".woff2", ".ttf", ".eot", ".ico", ".map", ".webp", ".avif",
    ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".flac", ".otf",
    # 新增 — 页面资源/3D模型/WebAssembly（噪音）
    ".glb", ".gltf", ".wasm", ".bin", ".proto", ".pb",
    ".obj", ".fbx", ".usdz", ".hdr",
    # 注意：不包含 .pdf/.doc/.zip 等用户可下载格式，
    # 因为下载 URL 本身是有效的 API 端点（如 "下载报告" 工具）
}
```

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `network_capture.py` | 修改 | 新增 `_normalize_url_for_dedup`，修改 `dedup_key`，扩展 `STATIC_EXTENSIONS` |
| `llm_analyzer.py` | 修改 | 更新 `TOOL_GEN_SYSTEM`、`TOOL_GEN_USER` prompt，`generate_tool_definition` 增加 `dom_context` 参数 |
| `manager.py` | 修改 | 新增 `_SCAN_DOM_CONTEXT_JS`，`_generate_tools_from_calls` 增加 DOM 扫描和传参 |

## 测试验证

### 场景 1：订单管理页面

- 打开包含搜索表单的订单管理页面
- 点击"搜索"（不填参数）
- 验证生成的工具定义包含 `name`、`id`、`user`、`date` 四个可选参数
- 验证只有一个 `search_orders` 工具（不重复）

### 场景 2：GitHub 仓库页面

- 打开 GitHub 仓库页面
- 点击包含 GLB 模型的页面
- 验证 `.glb` 文件请求不被捕获
- 验证其他 XHR/fetch API 正常捕获

### 场景 3：去重测试

- 点击"搜索"和"重置"（两者调用同一 API）
- 验证只生成一个工具定义
- 验证工具描述说明该 API 支持搜索和重置两种用途
