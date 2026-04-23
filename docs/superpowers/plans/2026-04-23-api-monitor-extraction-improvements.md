# API Monitor API 提取改进 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 API Monitor 的参数缺失、工具重复、噪音过滤不足三个问题。

**Architecture:** 在网络捕获层增加 URL 规范化去重和噪音过滤；在工具生成层注入 DOM 上下文让 LLM 推断完整参数。

**Tech Stack:** Python 3.13, Playwright (page.evaluate for DOM scanning), LangChain LLM

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `RpaClaw/backend/tests/test_api_monitor_capture.py` | **新建** — URL 去重和噪音过滤的单元测试 |
| `RpaClaw/backend/rpa/api_monitor/network_capture.py` | **修改** — 新增 `_normalize_url_for_dedup`，修改 `dedup_key`，扩展 `STATIC_EXTENSIONS` |
| `RpaClaw/backend/rpa/api_monitor/llm_analyzer.py` | **修改** — 更新 prompt，`generate_tool_definition` 增加 `dom_context` 参数 |
| `RpaClaw/backend/rpa/api_monitor/manager.py` | **修改** — 新增 `_SCAN_DOM_CONTEXT_JS`，`_generate_tools_from_calls` 增加 DOM 扫描 |

---

### Task 1: URL 去重规范化

**Files:**
- Create: `RpaClaw/backend/tests/test_api_monitor_capture.py`
- Modify: `RpaClaw/backend/rpa/api_monitor/network_capture.py:100-104`

- [ ] **Step 1: 写 `_normalize_url_for_dedup` 的失败测试**

创建 `RpaClaw/backend/tests/test_api_monitor_capture.py`：

```python
from backend.rpa.api_monitor.network_capture import (
    _normalize_url_for_dedup,
    dedup_key,
    parameterize_url,
)
from backend.rpa.api_monitor.models import CapturedApiCall, CapturedRequest


def _make_call(method: str, url: str) -> CapturedApiCall:
    return CapturedApiCall(
        request=CapturedRequest(
            request_id="test",
            url=url,
            method=method,
            headers={},
            timestamp="2026-01-01T00:00:00",
            resource_type="fetch",
        ),
    )


class TestNormalizeUrlForDedup:
    def test_removes_trailing_question_mark(self):
        assert _normalize_url_for_dedup("/api/orders?") == "/api/orders"

    def test_removes_empty_query_params(self):
        assert _normalize_url_for_dedup("/api/orders?&") == "/api/orders"

    def test_sorts_query_params(self):
        result = _normalize_url_for_dedup("/api/orders?b=2&a=1")
        assert result == "/api/orders?a=1&b=2"

    def test_removes_empty_value_params(self):
        result = _normalize_url_for_dedup("/api/orders?name=&id=123")
        assert result == "/api/orders?id=123"

    def test_plain_path_unchanged(self):
        assert _normalize_url_for_dedup("/api/orders") == "/api/orders"

    def test_full_url_strips_to_path_and_sorted_query(self):
        result = _normalize_url_for_dedup("https://example.com/api/orders?page=1&name=test")
        assert result == "/api/orders?name=test&page=1"

    def test_preserves_path_with_param_placeholders(self):
        result = _normalize_url_for_dedup("/api/users/{id}/orders")
        assert result == "/api/users/{id}/orders"


class TestDedupKeyNormalization:
    def test_same_endpoint_with_and_without_trailing_question(self):
        call1 = _make_call("GET", "https://example.com/api/orders?")
        call2 = _make_call("GET", "https://example.com/api/orders")
        assert dedup_key(call1) == dedup_key(call2)

    def test_same_endpoint_different_param_order(self):
        call1 = _make_call("GET", "https://example.com/api/orders?b=2&a=1")
        call2 = _make_call("GET", "https://example.com/api/orders?a=1&b=2")
        assert dedup_key(call1) == dedup_key(call2)

    def test_same_endpoint_with_empty_params_deduped(self):
        call1 = _make_call("GET", "https://example.com/api/orders?name=")
        call2 = _make_call("GET", "https://example.com/api/orders")
        assert dedup_key(call1) == dedup_key(call2)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd RpaClaw/backend && python -m pytest tests/test_api_monitor_capture.py -v`
Expected: FAIL — `ImportError: cannot import name '_normalize_url_for_dedup'`

- [ ] **Step 3: 实现 `_normalize_url_for_dedup` 并修改 `dedup_key`**

在 `RpaClaw/backend/rpa/api_monitor/network_capture.py` 的 `dedup_key` 函数之前（约第 99 行）添加：

```python
def _normalize_url_for_dedup(url: str) -> str:
    """Normalize a URL for deduplication: strip empty params, sort remaining."""
    parsed = urlparse(url)
    path = parsed.path

    if parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=True)
        # 过滤掉所有值为空的参数
        filtered = {k: v for k, v in qs.items() if any(vv.strip() for vv in v)}
        if filtered:
            sorted_params = sorted(filtered.items())
            query = urlencode(sorted_params, doseq=True)
            return f"{path}?{query}"
    return path
```

替换 `dedup_key` 函数（第 100-104 行）：

```python
def dedup_key(call: CapturedApiCall) -> str:
    """Return a deduplication key for grouping similar API calls."""
    pattern = call.url_pattern or parameterize_url(call.request.url)
    normalized = _normalize_url_for_dedup(pattern)
    return f"{call.request.method} {normalized}"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd RpaClaw/backend && python -m pytest tests/test_api_monitor_capture.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
cd RpaClaw && git add backend/tests/test_api_monitor_capture.py backend/rpa/api_monitor/network_capture.py
git commit -m "feat(api-monitor): add URL normalization for smart dedup of API calls"
```

---

### Task 2: 噪音过滤增强

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/network_capture.py:20-24`
- Modify: `RpaClaw/backend/tests/test_api_monitor_capture.py`

- [ ] **Step 1: 写噪音过滤的失败测试**

在 `RpaClaw/backend/tests/test_api_monitor_capture.py` 末尾添加：

```python
from backend.rpa.api_monitor.network_capture import should_capture


class TestShouldCaptureNoiseFilter:
    def test_glb_model_filtered(self):
        assert should_capture("https://example.com/models/car.glb", "fetch") is False

    def test_gltf_model_filtered(self):
        assert should_capture("https://example.com/models/scene.gltf", "fetch") is False

    def test_wasm_filtered(self):
        assert should_capture("https://example.com/app.wasm", "fetch") is False

    def test_bin_data_filtered(self):
        assert should_capture("https://example.com/data.bin", "fetch") is False

    def test_pdf_download_not_filtered(self):
        assert should_capture("https://example.com/api/report.pdf", "fetch") is True

    def test_docx_download_not_filtered(self):
        assert should_capture("https://example.com/api/document.docx", "fetch") is True

    def test_zip_download_not_filtered(self):
        assert should_capture("https://example.com/api/export.zip", "fetch") is True

    def test_xlsx_download_not_filtered(self):
        assert should_capture("https://example.com/api/data.xlsx", "fetch") is True

    def test_normal_api_not_filtered(self):
        assert should_capture("https://example.com/api/orders", "fetch") is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd RpaClaw/backend && python -m pytest tests/test_api_monitor_capture.py::TestShouldCaptureNoiseFilter -v`
Expected: FAIL — `.glb` tests return True instead of False

- [ ] **Step 3: 扩展 `STATIC_EXTENSIONS`**

替换 `RpaClaw/backend/rpa/api_monitor/network_capture.py` 第 20-24 行的 `STATIC_EXTENSIONS`：

```python
STATIC_EXTENSIONS: Set[str] = {
    # 页面渲染资源
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff",
    ".woff2", ".ttf", ".eot", ".ico", ".map", ".webp", ".avif",
    ".mp4", ".webm", ".mp3", ".ogg", ".wav", ".flac", ".otf",
    # 3D 模型 / WebAssembly / 二进制资源
    ".glb", ".gltf", ".wasm", ".bin", ".proto", ".pb",
    ".obj", ".fbx", ".usdz", ".hdr",
    # 注意：不包含 .pdf/.doc/.zip 等用户可下载格式
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd RpaClaw/backend && python -m pytest tests/test_api_monitor_capture.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
cd RpaClaw && git add backend/rpa/api_monitor/network_capture.py backend/tests/test_api_monitor_capture.py
git commit -m "feat(api-monitor): expand static extension filter to block 3D/WASM noise"
```

---

### Task 3: LLM prompt 更新 + `dom_context` 参数

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/llm_analyzer.py:57-101, 167-211`

此任务不适用 TDD（LLM 调用），但通过手动测试验证。

- [ ] **Step 1: 更新 `TOOL_GEN_SYSTEM` prompt**

替换 `RpaClaw/backend/rpa/api_monitor/llm_analyzer.py` 第 57-91 行的 `TOOL_GEN_SYSTEM`：

```python
TOOL_GEN_SYSTEM = """\
You are an API tool definition generator. Given HTTP API call samples captured from a web application, \
generate an OpenAI function calling format tool definition in YAML.

The YAML must have this structure:
```yaml
name: <snake_case_function_name>
description: <clear description of what this API endpoint does>
method: <HTTP method>
url: <parameterized URL path>
parameters:
  type: object
  properties:
    <param_name>:
      type: <string|integer|boolean|array|object>
      description: <what this parameter does>
      in: <query|path|body|header>
  required:
    - <required_param_names>
response:
  type: object
  properties:
    <field_name>:
      type: <type>
      description: <what this field contains>
```

Guidelines:
- Function names should be descriptive snake_case (e.g., list_users, create_order, search_products)
- Parameterize URL path segments that look like IDs: /users/123 -> /users/{user_id}
- Include all visible query parameters and request body fields
- Mark parameters as required only if they appear in every sample or seem essential
- Infer response schema from the captured response bodies
- Only return valid YAML, no markdown fences, no extra commentary

DOM Context Guidelines:
- If the captured API request has missing or empty parameters but the DOM context shows \
  related form inputs/fields, include those as optional parameters in the tool definition
- Use the label text and placeholder text from form inputs to generate parameter descriptions
- Map input types to JSON schema types: text -> string, number -> integer/number, \
  date -> string (format: date), checkbox -> boolean, select -> enum
- If the same API endpoint is triggered by multiple buttons (e.g., "Search" and "Reset"), \
  generate only ONE tool that covers all use cases, with optional parameters
"""
```

- [ ] **Step 2: 更新 `TOOL_GEN_USER` prompt**

替换 `RpaClaw/backend/rpa/api_monitor/llm_analyzer.py` 第 93-101 行的 `TOOL_GEN_USER`：

```python
TOOL_GEN_USER = """\
Endpoint: {method} {url_pattern}
Page context: {page_context}

{dom_context_section}

API call samples:
{samples_json}

Generate the YAML tool definition. Use DOM context to infer parameters not present in samples.
"""
```

- [ ] **Step 3: 更新 `generate_tool_definition` 函数签名和实现**

替换 `RpaClaw/backend/rpa/api_monitor/llm_analyzer.py` 第 167-211 行的 `generate_tool_definition`：

```python
async def generate_tool_definition(
    method: str,
    url_pattern: str,
    samples: List[CapturedApiCall],
    page_context: str = "",
    dom_context: str = "",
    model_config: Optional[Dict] = None,
) -> str:
    """Generate an OpenAI YAML tool definition from captured API call samples.

    Returns the raw YAML string.
    """
    sample_data = []
    for call in samples[:5]:
        entry: Dict = {
            "request_body": None,
            "response_status": None,
            "response_body": None,
        }
        if call.request.body:
            try:
                entry["request_body"] = json.loads(call.request.body)
            except (json.JSONDecodeError, TypeError):
                entry["request_body"] = call.request.body
        if call.response:
            entry["response_status"] = call.response.status
            if call.response.body:
                try:
                    entry["response_body"] = json.loads(call.response.body)
                except (json.JSONDecodeError, TypeError):
                    entry["response_body"] = call.response.body
        sample_data.append(entry)

    dom_context_section = ""
    if dom_context:
        dom_context_section = f"DOM context (form structure):\n{dom_context}"

    user_prompt = TOOL_GEN_USER.format(
        method=method,
        url_pattern=url_pattern,
        page_context=page_context or "Unknown page",
        dom_context_section=dom_context_section,
        samples_json=json.dumps(sample_data, indent=2, ensure_ascii=False),
    )

    raw = await _call_llm(TOOL_GEN_SYSTEM, user_prompt, model_config)

    raw = re.sub(r"^```(?:yaml)?\s*", "", raw)
    raw = re.sub(r"\s*```\s*$", "", raw)

    return raw.strip()
```

- [ ] **Step 4: 提交**

```bash
cd RpaClaw && git add backend/rpa/api_monitor/llm_analyzer.py
git commit -m "feat(api-monitor): update LLM prompts with DOM context support for parameter inference"
```

---

### Task 4: DOM 上下文扫描集成

**Files:**
- Modify: `RpaClaw/backend/rpa/api_monitor/manager.py:29-95, 412-480`

- [ ] **Step 1: 在 `_SCAN_INTERACTIVE_JS` 之后添加 `_SCAN_DOM_CONTEXT_JS`**

在 `RpaClaw/backend/rpa/api_monitor/manager.py` 第 95 行（`_SCAN_INTERACTIVE_JS` 结束的三引号之后）添加：

```python
# ── DOM context scanner (for LLM parameter inference) ────────────────

_SCAN_DOM_CONTEXT_JS = """
() => {
    const result = { forms: [], inputs: [], buttons: [] };

    // 扫描所有表单
    for (const form of document.querySelectorAll('form')) {
        const inputs = [];
        for (const input of form.querySelectorAll('input, select, textarea')) {
            if (input.type === 'hidden' || input.type === 'submit' || input.type === 'button') continue;
            // 尝试多种方式找到关联的 label
            let label = form.querySelector('label[for="' + input.id + '"]');
            if (!label) {
                const container = input.closest('.search-item, .form-group, .field, .input-group, .mb-3, .mb-4');
                if (container) label = container.querySelector('label');
            }
            if (!label && input.previousElementSibling && input.previousElementSibling.tagName === 'LABEL') {
                label = input.previousElementSibling;
            }
            const entry = {
                name: input.name || input.id || '',
                type: input.type || input.tagName.toLowerCase(),
                label: label ? label.textContent.trim() : '',
                placeholder: input.placeholder || '',
                required: input.required || false,
            };
            if (input.tagName === 'SELECT') {
                entry.type = 'select';
                entry.options = [...input.options].map(o => ({ value: o.value, text: o.textContent.trim() }));
            }
            inputs.push(entry);
        }
        result.forms.push({
            action: form.action || '',
            method: (form.method || 'GET').toUpperCase(),
            inputs,
            submitText: form.querySelector('button[type="submit"], input[type="submit"]')
                ? (form.querySelector('button[type="submit"], input[type="submit"]').textContent || '').trim()
                : '',
        });
    }

    // 扫描不在 form 内的独立输入框
    for (const input of document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"])')) {
        if (input.closest('form')) continue;
        let label = null;
        const container = input.closest('.search-item, .form-group, .field, .input-group');
        if (container) label = container.querySelector('label');
        if (!label && input.previousElementSibling && input.previousElementSibling.tagName === 'LABEL') {
            label = input.previousElementSibling;
        }
        result.inputs.push({
            id: input.id || '',
            name: input.name || '',
            type: input.type || 'text',
            label: label ? label.textContent.trim() : '',
            placeholder: input.placeholder || '',
        });
    }

    // 扫描不在 form 内的按钮
    for (const btn of document.querySelectorAll('button, [role="button"]')) {
        if (btn.closest('form')) continue;
        const text = (btn.textContent || '').trim();
        if (text) {
            result.buttons.push({
                text,
                onclick: btn.getAttribute('onclick') || '',
            });
        }
    }

    return result;
}
"""
```

- [ ] **Step 2: 修改 `_generate_tools_from_calls` 添加 DOM 扫描**

替换 `RpaClaw/backend/rpa/api_monitor/manager.py` 中整个 `_generate_tools_from_calls` 方法（第 412-480 行）：

```python
    async def _generate_tools_from_calls(
        self,
        session_id: str,
        calls: List[CapturedApiCall],
        source: str = "auto",
        model_config: Optional[Dict] = None,
    ) -> List[ApiToolDefinition]:
        """Group calls by dedup_key, generate YAML tool definition per group."""
        if not calls:
            return []

        session = self.sessions.get(session_id)
        if not session:
            return []

        # Scan DOM context for parameter inference
        dom_context = ""
        page = self._pages.get(session_id)
        if page:
            try:
                dom_data = await page.evaluate(_SCAN_DOM_CONTEXT_JS)
                dom_context = json.dumps(dom_data, ensure_ascii=False, indent=2)
                logger.debug("[ApiMonitor] DOM context scanned: %d forms, %d inputs, %d buttons",
                             len(dom_data.get("forms", [])),
                             len(dom_data.get("inputs", [])),
                             len(dom_data.get("buttons", [])))
            except Exception as exc:
                logger.warning("[ApiMonitor] DOM context scan failed: %s", exc)

        # Group by dedup key
        groups: Dict[str, List[CapturedApiCall]] = defaultdict(list)
        for call in calls:
            key = dedup_key(call)
            groups[key].append(call)

        tools: List[ApiToolDefinition] = []

        for key, group_calls in groups.items():
            # Take up to 5 samples per group
            samples = group_calls[:5]
            first = samples[0]
            method = first.request.method
            url_pattern = first.url_pattern or first.request.url

            try:
                yaml_def = await generate_tool_definition(
                    method=method,
                    url_pattern=url_pattern,
                    samples=samples,
                    page_context=session.target_url or "",
                    dom_context=dom_context,
                    model_config=model_config,
                )

                # Parse the YAML to extract name/description
                name, description = self._parse_yaml_metadata(yaml_def)

                tool = ApiToolDefinition(
                    session_id=session_id,
                    name=name,
                    description=description,
                    method=method,
                    url_pattern=url_pattern,
                    yaml_definition=yaml_def,
                    source_calls=[c.id for c in samples],
                    source=source,
                )

                session.tool_definitions.append(tool)
                tools.append(tool)

                logger.info(
                    "[ApiMonitor] Generated tool '%s' for %s %s",
                    name, method, url_pattern,
                )

            except Exception as exc:
                logger.warning(
                    "[ApiMonitor] Failed to generate tool for %s: %s",
                    key, exc,
                )

        return tools
```

- [ ] **Step 3: 验证语法正确**

Run: `cd RpaClaw/backend && python -c "from backend.rpa.api_monitor.manager import api_monitor_manager; print('OK')"`
Expected: `OK`

- [ ] **Step 4: 提交**

```bash
cd RpaClaw && git add backend/rpa/api_monitor/manager.py
git commit -m "feat(api-monitor): inject DOM context into tool generation for parameter inference"
```

---

### Task 5: 集成验证

此任务通过手动测试验证所有改动协同工作。

- [ ] **Step 1: 确保所有单元测试通过**

Run: `cd RpaClaw/backend && python -m pytest tests/test_api_monitor_capture.py -v`
Expected: ALL PASS

- [ ] **Step 2: 启动后端验证 import 无误**

Run: `cd RpaClaw/backend && python -c "from backend.rpa.api_monitor.network_capture import should_capture, dedup_key, _normalize_url_for_dedup; from backend.rpa.api_monitor.llm_analyzer import generate_tool_definition; from backend.rpa.api_monitor.manager import api_monitor_manager; print('All imports OK')"`

Expected: `All imports OK`

- [ ] **Step 3: 验证去重逻辑**

Run: `cd RpaClaw/backend && python -c "
from backend.rpa.api_monitor.network_capture import _normalize_url_for_dedup
# 验证搜索和重置按钮的去重
print('/api/orders? =>', _normalize_url_for_dedup('/api/orders?'))
print('/api/orders  =>', _normalize_url_for_dedup('/api/orders'))
assert _normalize_url_for_dedup('/api/orders?') == _normalize_url_for_dedup('/api/orders')
print('Dedup OK')
"`
Expected: 两个结果相同，`Dedup OK`

- [ ] **Step 4: 验证噪音过滤**

Run: `cd RpaClaw/backend && python -c "
from backend.rpa.api_monitor.network_capture import should_capture
assert should_capture('https://x.com/model.glb', 'fetch') is False, 'glb should be filtered'
assert should_capture('https://x.com/api/report.pdf', 'fetch') is True, 'pdf should NOT be filtered'
assert should_capture('https://x.com/api/orders', 'fetch') is True, 'api should not be filtered'
print('Filter OK')
"`
Expected: `Filter OK`
