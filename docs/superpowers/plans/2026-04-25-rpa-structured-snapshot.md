# RPA Structured Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured `table_views` and `detail_views` to RPA recording snapshots, raise the compact budget to 60000, and add a guarded table ordinal lane without breaking trace-first recording.

**Architecture:** The browser snapshot runtime will collect structured page facts in addition to existing nodes and containers. Snapshot compression will pass those facts through while keeping the old region payload during rollout. The recording runtime will prefer structured facts in prompt guidance and only use deterministic table grounding when evidence is high confidence.

**Tech Stack:** Python, FastAPI backend modules, Playwright browser context JavaScript, Pydantic trace models, pytest.

---

## File Structure

- Modify `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`
  - Add browser-side extraction of `table_views` and `detail_views`.
  - Keep existing `actionable_nodes`, `content_nodes`, and `containers`.
- Modify `RpaClaw/backend/rpa/assistant_runtime.py`
  - Preserve `table_views` and `detail_views` returned by each frame.
  - Attach `frame_path` to structured views.
- Modify `RpaClaw/backend/rpa/snapshot_compression.py`
  - Raise `compact_recording_snapshot(... char_budget=60000)`.
  - Add structured views to clean and tiered compact payloads.
  - Add lightweight trimming so views do not dominate the payload.
- Modify `RpaClaw/backend/rpa/recording_runtime_agent.py`
  - Update prompt rules to prefer structured views.
  - Add structured-view debug metrics.
  - Add table-aware ordinal plan building before the existing generic ordinal overlay.
- Modify `RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py`
  - Add string-level regression tests for the browser snapshot JS surface.
- Modify `RpaClaw/backend/tests/test_rpa_snapshot_compression.py`
  - Add compact snapshot tests for table/detail view preservation and budget.
- Modify `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`
  - Add prompt, planner payload, diagnostics, and table ordinal lane tests.
- Keep `RpaClaw/backend/rpa/trace_skill_compiler.py` unchanged in Phase 1 unless tests prove a small metadata adaptation is required.

---

### Task 1: Collect Structured Views In Browser Snapshot

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`
- Modify: `RpaClaw/backend/rpa/assistant_runtime.py`
- Test: `RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py`

- [ ] **Step 1: Write failing tests for the JS contract**

Add this test to `RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py`:

```python
def test_snapshot_v2_js_collects_structured_table_and_detail_views():
    assert "table_views: []" in SNAPSHOT_V2_JS
    assert "detail_views: []" in SNAPSHOT_V2_JS
    assert "function collectTableViews()" in SNAPSHOT_V2_JS
    assert "function collectDetailViews()" in SNAPSHOT_V2_JS
    assert "data-colid" in SNAPSHOT_V2_JS
    assert "row_count_observed" in SNAPSHOT_V2_JS
    assert "column_header" in SNAPSHOT_V2_JS
    assert "row_local_actions" in SNAPSHOT_V2_JS
    assert "aui-form-item" in SNAPSHOT_V2_JS
    assert "data_prop" in SNAPSHOT_V2_JS
    assert "value_kind" in SNAPSHOT_V2_JS
```

Add this test to the same file:

```python
def test_snapshot_v2_js_marks_non_row_table_text_as_auxiliary():
    assert "auxiliary_text" in SNAPSHOT_V2_JS
    assert "empty_state" in SNAPSHOT_V2_JS
    assert "tooltip" in SNAPSHOT_V2_JS
    assert "outside_rows" in SNAPSHOT_V2_JS
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py -q
```

Expected: the new assertions fail because `SNAPSHOT_V2_JS` does not yet contain structured view collection.

- [ ] **Step 3: Implement table/detail view extraction in `SNAPSHOT_V2_JS`**

In `SNAPSHOT_V2_JS`, change the result initialization from:

```javascript
const result = { actionable_nodes: [], content_nodes: [], containers: [] };
```

to:

```javascript
const result = { actionable_nodes: [], content_nodes: [], containers: [], table_views: [], detail_views: [] };
```

Add helpers near the existing DOM helper functions:

```javascript
function textOf(el, limit) {
    return normalizeText(el ? (el.innerText || el.textContent || '') : '', limit || 200);
}

function classText(el) {
    return normalizeText(el ? (el.className || '') : '', 160);
}

function attr(el, name, limit) {
    return normalizeText(el ? el.getAttribute(name) || '' : '', limit || 120);
}

function isHiddenByStyle(el) {
    if (!el)
        return true;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return !style || style.display === 'none' || style.visibility === 'hidden' || rect.width <= 0 || rect.height <= 0;
}

function columnRole(header, colId, sampleTexts, hasCheckbox, hasLink) {
    const text = normalizeText([header, colId, sampleTexts.join(' ')].join(' '), 240).toLowerCase();
    if (hasCheckbox || /selection|select/.test(text))
        return 'selection';
    if (/index|序号|编号/.test(text) || sampleTexts.every(value => /^\d+$/.test(value)))
        return 'row_index';
    if (hasLink || /\.xlsx|\.xls|\.csv|file|文件/.test(text))
        return 'file_link';
    if (/finish|success|failed|status|状态/.test(text))
        return 'status';
    if (/\d{4}-\d{2}-\d{2}|time|date|时间|日期/.test(text))
        return 'datetime';
    return 'text';
}

function valueKind(text) {
    const value = normalizeText(text || '', 120);
    if (!value || value === '-')
        return 'empty';
    if (/^-?\d+(?:\.\d+)?$/.test(value))
        return 'number';
    if (/^\d{4}-\d{2}-\d{2}/.test(value))
        return 'date';
    if (/^(finish|success|failed|approved|pending)$/i.test(value))
        return 'status';
    return 'text';
}
```

Add `collectTableViews()` before the final `return JSON.stringify(result);`:

```javascript
function collectTableViews() {
    const views = [];
    const gridRoots = Array.from(document.querySelectorAll('.aui-grid, [role=grid], table'))
        .map(el => el.closest('.aui-grid') || el)
        .filter((el, index, arr) => el && arr.indexOf(el) === index);

    for (const root of gridRoots.slice(0, 8)) {
        const headerCells = Array.from(root.querySelectorAll('thead th,[role=columnheader]'));
        const bodyRows = Array.from(root.querySelectorAll('tbody tr,[role=row]'))
            .filter(row => row.querySelector('td,[role=cell]'));
        if (!bodyRows.length)
            continue;

        const headerByColId = new Map();
        const headers = [];
        headerCells.forEach((cell, index) => {
            const colId = attr(cell, 'data-colid', 80) || Array.from(cell.classList || []).find(cls => /^col_/.test(cls)) || '';
            const header = textOf(cell, 120);
            const record = { index, column_id: colId, header, role: '' };
            headers.push(record);
            if (colId)
                headerByColId.set(colId, record);
        });

        const rows = [];
        const columnSamples = new Map();
        for (const row of bodyRows.slice(0, 10)) {
            const rowIndex = rows.length;
            const cells = [];
            const cellEls = Array.from(row.querySelectorAll('td,[role=cell]'));
            cellEls.forEach((cell, cellIndex) => {
                const colId = attr(cell, 'data-colid', 80) || Array.from(cell.classList || []).find(cls => /^col_/.test(cls)) || '';
                const headerRecord = colId ? headerByColId.get(colId) : headers[cellIndex];
                const text = textOf(cell, 200);
                const actions = Array.from(cell.querySelectorAll('a,button,input[type=checkbox],[role=button],[role=link]')).slice(0, 4).map(action => {
                    const tag = action.tagName.toLowerCase();
                    const role = getRole(action) || tag;
                    const label = getAccessibleName(action) || textOf(action, 120) || role;
                    const selector = colId
                        ? `td[data-colid="${escapeCssAttributeValue(colId)}"] ${tag}`
                        : `td:nth-child(${cellIndex + 1}) ${tag}`;
                    return {
                        kind: role,
                        label,
                        locator: { method: 'relative_css', scope: 'row', value: selector },
                    };
                });
                const record = {
                    column_id: colId,
                    column_index: cellIndex,
                    column_header: headerRecord ? headerRecord.header : '',
                    text,
                    value_kind: valueKind(text),
                    row_local_actions: actions,
                    actions,
                };
                cells.push(record);
                const key = colId || `index:${cellIndex}`;
                if (!columnSamples.has(key))
                    columnSamples.set(key, { texts: [], hasCheckbox: false, hasLink: false });
                const sample = columnSamples.get(key);
                if (text)
                    sample.texts.push(text);
                sample.hasCheckbox = sample.hasCheckbox || Boolean(cell.querySelector('input[type=checkbox]'));
                sample.hasLink = sample.hasLink || Boolean(cell.querySelector('a,[role=link]'));
            });
            rows.push({
                index: rowIndex,
                cells,
                locator_hints: [
                    {
                        kind: 'playwright',
                        expression: "page.locator('tbody tr').nth(" + rowIndex + ")",
                    },
                ],
            });
        }

        const columns = [];
        const maxCells = Math.max(...rows.map(row => row.cells.length));
        for (let index = 0; index < maxCells; index++) {
            const firstCell = rows.map(row => row.cells[index]).find(Boolean) || {};
            const colId = firstCell.column_id || (headers[index] || {}).column_id || '';
            const header = (headers.find(item => item.column_id && item.column_id === colId) || headers[index] || {}).header || '';
            const sample = columnSamples.get(colId || `index:${index}`) || { texts: [], hasCheckbox: false, hasLink: false };
            columns.push({
                index,
                column_id: colId,
                header,
                role: columnRole(header, colId, sample.texts.slice(0, 5), sample.hasCheckbox, sample.hasLink),
                sample_values: sample.texts.slice(0, 3),
            });
        }

        const auxiliaryText = [];
        for (const empty of Array.from(root.querySelectorAll('.aui-grid__empty-text')).slice(0, 3)) {
            const text = textOf(empty, 120);
            if (text)
                auxiliaryText.push({ kind: 'empty_state', text, outside_rows: true });
        }
        for (const tip of Array.from(root.parentElement ? root.parentElement.querySelectorAll('[role=tooltip]') : []).slice(0, 3)) {
            const text = textOf(tip, 120);
            if (text)
                auxiliaryText.push({ kind: 'tooltip', text, outside_rows: true });
        }

        views.push({
            kind: 'table_view',
            framework_hint: classText(root).includes('aui-grid') ? 'aui-grid' : '',
            title: attr(root, 'aria-label', 120) || attr(root, 'title', 120),
            row_count_observed: bodyRows.length,
            columns,
            rows,
            auxiliary_text: auxiliaryText,
        });
    }
    return views;
}
```

Add `collectDetailViews()`:

```javascript
function collectDetailViews() {
    const views = [];
    const sections = Array.from(document.querySelectorAll('.aui-collapse-item, section, article, form, fieldset,[role=region],[role=group]'));
    for (const section of sections.slice(0, 12)) {
        const titleEl = section.querySelector('.aui-collapse-item__word-overflow,legend,h1,h2,h3,h4,[role=heading]');
        const sectionTitle = textOf(titleEl, 120) || attr(section, 'aria-label', 120) || attr(section, 'title', 120);
        const fieldEls = Array.from(section.querySelectorAll('.aui-form-item,[data-prop],dt'))
            .filter((field, index, arr) => arr.indexOf(field) === index);
        if (!sectionTitle && fieldEls.length < 2)
            continue;

        const fields = [];
        for (const field of fieldEls.slice(0, 40)) {
            const labelEl = field.querySelector('.aui-form-item__label,.field-header .label,label,dt');
            const contentEl = field.querySelector('.aui-form-item__content,dd') || field;
            const label = textOf(labelEl, 120).replace(/^\*\s*/, '');
            if (!label)
                continue;
            const visible = !isHiddenByStyle(field);
            const dataProp = attr(field, 'data-prop', 120) || attr(contentEl, 'prop', 120);
            const required = classText(field).includes('is-required') || Boolean(field.querySelector('.required'));
            const displayValueEl = contentEl.querySelector('.aui-input-display-only__content,.aui-numeric-display-only__value,.aui-range-editor-display-only,.aui-input-display-only,.no-value,input,textarea,select');
            let value = textOf(displayValueEl, 200);
            if (!value && displayValueEl && ('value' in displayValueEl))
                value = normalizeText(displayValueEl.value || '', 200);
            fields.push({
                label,
                value,
                data_prop: dataProp,
                required,
                visible,
                hidden_reason: visible ? '' : 'hidden',
                value_kind: valueKind(value),
                locator_hints: dataProp ? [
                    {
                        kind: 'field_container',
                        expression: `page.locator('[data-prop="${escapeCssAttributeValue(dataProp)}"]')`,
                    },
                ] : [],
            });
        }
        if (fields.length) {
            views.push({
                kind: 'detail_view',
                section_title: sectionTitle,
                section_locator: sectionTitle ? { method: 'text', value: sectionTitle } : {},
                fields,
            });
        }
    }
    return views;
}
```

Before the final return, assign the collected views:

```javascript
result.table_views = collectTableViews();
result.detail_views = collectDetailViews();
```

- [ ] **Step 4: Preserve structured views in `assistant_runtime.py`**

In `_extract_frame_snapshot_v2`, include the new keys:

```python
return {
    "actionable_nodes": list(data.get("actionable_nodes") or []),
    "content_nodes": list(data.get("content_nodes") or []),
    "containers": list(data.get("containers") or []),
    "table_views": list(data.get("table_views") or []),
    "detail_views": list(data.get("detail_views") or []),
}
```

In `build_page_snapshot`, initialize and extend top-level lists:

```python
table_views: List[Dict[str, Any]] = []
detail_views: List[Dict[str, Any]] = []
```

After `frame_containers`, add:

```python
frame_table_views = [
    {
        **view,
        "frame_path": list(view.get("frame_path") or frame_path),
    }
    for view in list(snapshot_v2.get("table_views") or [])
]
frame_detail_views = [
    {
        **view,
        "frame_path": list(view.get("frame_path") or frame_path),
    }
    for view in list(snapshot_v2.get("detail_views") or [])
]
```

Extend after existing node/container extension:

```python
table_views.extend(frame_table_views)
detail_views.extend(frame_detail_views)
```

Return them:

```python
return {
    "url": page.url,
    "title": await page.title(),
    "frames": frames,
    "actionable_nodes": actionable_nodes,
    "content_nodes": content_nodes,
    "containers": containers,
    "table_views": table_views,
    "detail_views": detail_views,
}
```

- [ ] **Step 5: Run tests to verify Task 1 passes**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py -q
```

Expected: all tests in that file pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add RpaClaw/backend/rpa/assistant_snapshot_runtime.py RpaClaw/backend/rpa/assistant_runtime.py RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py
git commit -m "feat: collect structured rpa snapshot views"
```

---

### Task 2: Preserve Structured Views In Compact Snapshot

**Files:**
- Modify: `RpaClaw/backend/rpa/snapshot_compression.py`
- Test: `RpaClaw/backend/tests/test_rpa_snapshot_compression.py`

- [ ] **Step 1: Write failing compact snapshot tests**

Add this fixture and tests to `RpaClaw/backend/tests/test_rpa_snapshot_compression.py`:

```python
def _structured_view_snapshot() -> dict:
    snapshot = _build_snapshot()
    snapshot["table_views"] = [
        {
            "kind": "table_view",
            "title": "",
            "framework_hint": "aui-grid",
            "row_count_observed": 10,
            "columns": [
                {"index": 0, "column_id": "col_23", "header": "", "role": "row_index", "sample_values": ["1", "2"]},
                {"index": 1, "column_id": "col_24", "header": "", "role": "selection", "sample_values": []},
                {"index": 2, "column_id": "col_25", "header": "文件名称", "role": "file_link", "sample_values": ["File_189.xlsx"]},
            ],
            "rows": [
                {
                    "index": 0,
                    "cells": [
                        {"column_id": "col_23", "column_index": 0, "column_header": "", "text": "1", "value_kind": "number", "actions": []},
                        {"column_id": "col_25", "column_index": 2, "column_header": "文件名称", "text": "File_189.xlsx", "value_kind": "text", "actions": [{"kind": "link", "label": "File_189.xlsx", "locator": {"method": "relative_css", "scope": "row", "value": "td[data-colid='col_25'] a"}}]},
                    ],
                    "locator_hints": [{"kind": "playwright", "expression": "page.locator('tbody tr').nth(0)"}],
                }
            ],
            "auxiliary_text": [{"kind": "empty_state", "text": "暂无数据", "outside_rows": True}],
        }
    ]
    snapshot["detail_views"] = [
        {
            "kind": "detail_view",
            "section_title": "采购信息",
            "fields": [
                {"label": "预计总金额(含税)", "value": "100.00", "data_prop": "amount", "required": True, "visible": True, "value_kind": "number"},
                {"label": "隐藏字段", "value": "secret", "data_prop": "hidden", "required": False, "visible": False, "hidden_reason": "display_none", "value_kind": "text"},
            ],
        }
    ]
    return snapshot


def test_compact_recording_snapshot_preserves_structured_views():
    compact = compact_recording_snapshot(_structured_view_snapshot(), "点击第一行的文件名称", char_budget=100000)

    assert compact["mode"] == "clean_snapshot"
    assert compact["table_views"][0]["columns"][2]["header"] == "文件名称"
    assert compact["table_views"][0]["rows"][0]["cells"][1]["actions"][0]["locator"]["scope"] == "row"
    assert compact["table_views"][0]["auxiliary_text"][0]["outside_rows"] is True
    assert compact["detail_views"][0]["section_title"] == "采购信息"
    assert compact["detail_views"][0]["fields"][0]["data_prop"] == "amount"


def test_default_structured_snapshot_budget_is_60000():
    compact = compact_recording_snapshot(_structured_view_snapshot(), "提取采购信息")

    assert compact["mode"] == "clean_snapshot"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_snapshot_compression.py::test_compact_recording_snapshot_preserves_structured_views RpaClaw/backend/tests/test_rpa_snapshot_compression.py::test_default_structured_snapshot_budget_is_60000 -q
```

Expected: failures because compact payload does not include `table_views` and `detail_views`, and the default budget is still 20000.

- [ ] **Step 3: Implement structured view trimming and budget**

Change the function signature:

```python
def compact_recording_snapshot(snapshot: Dict[str, Any], instruction: str, *, char_budget: int = 60000) -> Dict[str, Any]:
```

Add helpers near `_build_clean_payload`:

```python
def _compact_table_views(snapshot: Dict[str, Any], *, row_limit: int = 10, cell_limit: int = 12) -> List[Dict[str, Any]]:
    views: List[Dict[str, Any]] = []
    for view in list(snapshot.get("table_views") or [])[:8]:
        rows = []
        for row in list(view.get("rows") or [])[:row_limit]:
            cells = []
            for cell in list(row.get("cells") or [])[:cell_limit]:
                cells.append(
                    {
                        "column_id": cell.get("column_id", ""),
                        "column_index": cell.get("column_index"),
                        "column_header": cell.get("column_header", ""),
                        "text": cell.get("text", ""),
                        "value_kind": cell.get("value_kind", ""),
                        "actions": list(cell.get("actions") or cell.get("row_local_actions") or [])[:4],
                    }
                )
            rows.append(
                {
                    "index": row.get("index"),
                    "cells": cells,
                    "locator_hints": list(row.get("locator_hints") or [])[:2],
                }
            )
        views.append(
            {
                "kind": "table_view",
                "title": view.get("title", ""),
                "framework_hint": view.get("framework_hint", ""),
                "frame_path": list(view.get("frame_path") or []),
                "row_count_observed": view.get("row_count_observed", 0),
                "columns": list(view.get("columns") or [])[:cell_limit],
                "rows": rows,
                "auxiliary_text": list(view.get("auxiliary_text") or [])[:5],
            }
        )
    return views


def _compact_detail_views(snapshot: Dict[str, Any], *, field_limit: int = 40) -> List[Dict[str, Any]]:
    views: List[Dict[str, Any]] = []
    for view in list(snapshot.get("detail_views") or [])[:12]:
        fields = []
        for field in list(view.get("fields") or [])[:field_limit]:
            fields.append(
                {
                    "label": field.get("label", ""),
                    "value": field.get("value", ""),
                    "data_prop": field.get("data_prop", ""),
                    "required": bool(field.get("required")),
                    "visible": bool(field.get("visible", True)),
                    "hidden_reason": field.get("hidden_reason", ""),
                    "value_kind": field.get("value_kind", ""),
                    "locator_hints": list(field.get("locator_hints") or [])[:2],
                }
            )
        views.append(
            {
                "kind": "detail_view",
                "section_title": view.get("section_title", ""),
                "section_locator": view.get("section_locator") or {},
                "frame_path": list(view.get("frame_path") or []),
                "fields": fields,
            }
        )
    return views
```

Modify `_build_clean_payload` to include:

```python
"table_views": _compact_table_views(snapshot),
"detail_views": _compact_detail_views(snapshot),
```

Modify the tiered return payload to also include:

```python
"table_views": _compact_table_views(snapshot),
"detail_views": _compact_detail_views(snapshot),
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_snapshot_compression.py::test_compact_recording_snapshot_preserves_structured_views RpaClaw/backend/tests/test_rpa_snapshot_compression.py::test_default_structured_snapshot_budget_is_60000 -q
```

Expected: both tests pass.

- [ ] **Step 5: Run existing snapshot compression tests**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_snapshot_compression.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

```powershell
git add RpaClaw/backend/rpa/snapshot_compression.py RpaClaw/backend/tests/test_rpa_snapshot_compression.py
git commit -m "feat: preserve structured rpa snapshot views"
```

---

### Task 3: Prefer Structured Views In Planner Prompt And Diagnostics

**Files:**
- Modify: `RpaClaw/backend/rpa/recording_runtime_agent.py`
- Test: `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`

- [ ] **Step 1: Write failing prompt and payload tests**

Add this test near existing prompt tests:

```python
def test_recording_runtime_prompt_prefers_structured_snapshot_views():
    assert "table_views" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "detail_views" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "row-relative" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "column-relative" in RECORDING_RUNTIME_SYSTEM_PROMPT
    assert "Do not use observed row text as the primary selector when the instruction is ordinal" in RECORDING_RUNTIME_SYSTEM_PROMPT
```

Add this async test:

```python
async def test_recording_runtime_agent_forwards_structured_views_to_planner(monkeypatch):
    snapshot = {
        "url": "https://example.test/grid",
        "title": "Grid",
        "frames": [],
        "actionable_nodes": [],
        "content_nodes": [],
        "containers": [],
        "table_views": [
            {
                "kind": "table_view",
                "columns": [{"index": 0, "column_id": "col_25", "header": "文件名称", "role": "file_link"}],
                "rows": [{"index": 0, "cells": [{"column_id": "col_25", "column_header": "文件名称", "text": "File_189.xlsx", "actions": []}]}],
            }
        ],
        "detail_views": [],
    }
    calls = []

    async def fake_build_page_snapshot(_page, _build_frame_path):
        return snapshot

    async def fake_planner(payload):
        calls.append(payload)
        return {
            "description": "Extract grid",
            "action_type": "run_python",
            "expected_effect": "extract",
            "code": "async def run(page, results):\n    return 'ok'",
            "output_key": "grid_result",
        }

    monkeypatch.setattr("backend.rpa.recording_runtime_agent.build_page_snapshot", fake_build_page_snapshot)

    agent = RecordingRuntimeAgent(planner=fake_planner)
    result = await agent.run(page=_FakePage(), instruction="提取第一行文件名称", runtime_results={})

    assert result.success is True
    assert calls[0]["snapshot"]["table_views"][0]["columns"][0]["header"] == "文件名称"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_recording_runtime_prompt_prefers_structured_snapshot_views RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_recording_runtime_agent_forwards_structured_views_to_planner -q
```

Expected: prompt test fails until prompt text is added. Payload test may pass after Task 2, but keep it as regression coverage.

- [ ] **Step 3: Update runtime prompt**

In `RECORDING_RUNTIME_SYSTEM_PROMPT`, after the current snapshot contract rules, add:

```text
- Structured snapshot views:
  - For table/list/grid tasks, inspect `snapshot.table_views` before generic `expanded_regions`.
  - `table_views[].columns` describes column ids, headers, and inferred roles.
  - `table_views[].rows[].cells` describes row-local cell text and row-local actions.
  - For ordinal table tasks, prefer row-relative and column-relative Playwright locators.
  - Do not use observed row text as the primary selector when the instruction is ordinal.
  - For detail extraction, inspect `snapshot.detail_views` before scanning generic text or tables.
  - `detail_views[].fields` preserves label, value, data_prop, required, visible, and value_kind.
  - Treat hidden fields as diagnostic unless the user explicitly asks for hidden/default/internal values.
```

- [ ] **Step 4: Add structured debug metrics**

In `_build_snapshot_debug_metrics`, add:

```python
table_views = list(compact_snapshot.get("table_views") or [])
detail_views = list(compact_snapshot.get("detail_views") or [])
```

Inside `"compact_snapshot"` metrics, add:

```python
"table_view_count": len(table_views),
"detail_view_count": len(detail_views),
"table_view_titles": _region_titles(table_views),
"detail_view_titles": [
    str(view.get("section_title") or view.get("title") or "").strip()[:120]
    for view in table_views[:0]
],
```

Then correct the detail titles line to use detail views:

```python
"detail_view_titles": [
    str(view.get("section_title") or view.get("title") or "").strip()[:120]
    for view in detail_views[:20]
    if str(view.get("section_title") or view.get("title") or "").strip()
],
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_recording_runtime_prompt_prefers_structured_snapshot_views RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_recording_runtime_agent_forwards_structured_views_to_planner -q
```

Expected: both tests pass.

- [ ] **Step 6: Commit Task 3**

```powershell
git add RpaClaw/backend/rpa/recording_runtime_agent.py RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py
git commit -m "feat: prefer structured views in rpa planner"
```

---

### Task 4: Add Guarded Table Ordinal Lane

**Files:**
- Modify: `RpaClaw/backend/rpa/recording_runtime_agent.py`
- Test: `RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py`

- [ ] **Step 1: Write failing table ordinal tests**

Add helper:

```python
def _table_view_snapshot():
    return {
        "url": "https://example.test/grid",
        "title": "Grid",
        "frames": [],
        "actionable_nodes": [],
        "content_nodes": [],
        "containers": [],
        "table_views": [
            {
                "kind": "table_view",
                "framework_hint": "aui-grid",
                "columns": [
                    {"index": 0, "column_id": "col_23", "header": "", "role": "row_index"},
                    {"index": 1, "column_id": "col_24", "header": "", "role": "selection"},
                    {"index": 2, "column_id": "col_25", "header": "文件名称", "role": "file_link"},
                    {"index": 3, "column_id": "col_28", "header": "导出状态", "role": "status"},
                ],
                "rows": [
                    {
                        "index": 0,
                        "cells": [
                            {"column_id": "col_25", "column_index": 2, "column_header": "文件名称", "text": "File_189.xlsx", "actions": [{"kind": "link", "label": "File_189.xlsx", "locator": {"method": "relative_css", "scope": "row", "value": "td[data-colid='col_25'] a"}}]},
                            {"column_id": "col_28", "column_index": 3, "column_header": "导出状态", "text": "FINISH", "actions": []},
                        ],
                        "locator_hints": [{"kind": "playwright", "expression": "page.locator('table.aui-grid__body tbody tr').nth(0)"}],
                    },
                    {
                        "index": 1,
                        "cells": [
                            {"column_id": "col_25", "column_index": 2, "column_header": "文件名称", "text": "File_380.xlsx", "actions": [{"kind": "link", "label": "File_380.xlsx", "locator": {"method": "relative_css", "scope": "row", "value": "td[data-colid='col_25'] a"}}]},
                            {"column_id": "col_28", "column_index": 3, "column_header": "导出状态", "text": "FINISH", "actions": []},
                        ],
                        "locator_hints": [{"kind": "playwright", "expression": "page.locator('table.aui-grid__body tbody tr').nth(1)"}],
                    },
                ],
            }
        ],
        "detail_views": [],
    }
```

Add tests:

```python
def test_table_ordinal_lane_clicks_first_row_named_column_link():
    build_plan = getattr(recording_runtime_agent, "_build_table_ordinal_overlay_plan")

    plan = build_plan("点击第一行的文件名称", _table_view_snapshot())

    assert plan is not None
    assert plan["table_ordinal_overlay"] is True
    assert "table.aui-grid__body tbody tr" in plan["code"]
    assert "td[data-colid='col_25'] a" in plan["code"]
    assert "File_189.xlsx" not in plan["code"]


def test_table_ordinal_lane_extracts_second_row_status():
    build_plan = getattr(recording_runtime_agent, "_build_table_ordinal_overlay_plan")

    plan = build_plan("提取第二行的导出状态", _table_view_snapshot())

    assert plan is not None
    assert "nth(1)" in plan["code"]
    assert "td[data-colid='col_28']" in plan["code"]
    assert plan["expected_effect"] == "extract"


def test_table_ordinal_lane_falls_back_without_column_match():
    build_plan = getattr(recording_runtime_agent, "_build_table_ordinal_overlay_plan")

    plan = build_plan("点击第一行的审批按钮", _table_view_snapshot())

    assert plan is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_table_ordinal_lane_clicks_first_row_named_column_link RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_table_ordinal_lane_extracts_second_row_status RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_table_ordinal_lane_falls_back_without_column_match -q
```

Expected: failures because `_build_table_ordinal_overlay_plan` is not implemented.

- [ ] **Step 3: Call table ordinal lane before generic ordinal overlay**

In `RecordingRuntimeAgent.run`, replace:

```python
first_plan = _build_ordinal_overlay_plan(instruction, snapshot)
```

with:

```python
first_plan = _build_table_ordinal_overlay_plan(instruction, snapshot)
if not first_plan:
    first_plan = _build_ordinal_overlay_plan(instruction, snapshot)
```

- [ ] **Step 4: Implement table ordinal helper functions**

Add before `_build_ordinal_overlay_plan`:

```python
def _build_table_ordinal_overlay_plan(instruction: str, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    intent = _detect_ordinal_intent(instruction)
    if not intent:
        return None
    action = _detect_ordinal_action(instruction)
    if action not in {"click_primary", "extract_title"}:
        return None

    table = _select_table_view(snapshot, instruction)
    if not table:
        return None
    rows = list(table.get("rows") or [])
    if not rows:
        return None
    index = _ordinal_index_from_intent(intent, len(rows))
    if index is None:
        return None
    column = _select_table_column(table, instruction)
    if not column:
        return None

    row_selector = _table_row_selector(table)
    if not row_selector:
        return None
    column_id = str(column.get("column_id") or "")
    if column_id:
        cell_selector = f"td[data-colid={column_id!r}]"
    else:
        col_index = int(column.get("index") or 0) + 1
        cell_selector = f"td:nth-child({col_index})"

    if action == "click_primary":
        action_selector = _table_column_action_selector(table, index, column)
        if not action_selector:
            return None
        code = (
            "async def run(page, results):\n"
            f"    _row = page.locator({row_selector!r}).nth({index})\n"
            f"    await _row.locator({action_selector!r}).click()\n"
            "    return {'action_performed': True}"
        )
        return {
            "description": "Click table row column action",
            "action_type": "run_python",
            "expected_effect": "none",
            "output_key": "table_row_action",
            "code": code,
            "table_ordinal_overlay": True,
        }

    code = (
        "async def run(page, results):\n"
        f"    _row = page.locator({row_selector!r}).nth({index})\n"
        f"    return (await _row.locator({cell_selector!r}).inner_text()).strip()"
    )
    return {
        "description": "Extract table row column value",
        "action_type": "run_python",
        "expected_effect": "extract",
        "output_key": "table_row_value",
        "code": code,
        "table_ordinal_overlay": True,
    }
```

Add supporting helpers:

```python
def _ordinal_index_from_intent(intent: Dict[str, int | str], row_count: int) -> Optional[int]:
    kind = str(intent.get("kind") or "")
    if kind == "last":
        return row_count - 1 if row_count else None
    if kind == "first_n":
        return None
    index = int(intent.get("index") or 0)
    return index if 0 <= index < row_count else None


def _select_table_view(snapshot: Dict[str, Any], instruction: str) -> Optional[Dict[str, Any]]:
    tables = [table for table in list(snapshot.get("table_views") or []) if table.get("rows")]
    if not tables:
        return None
    return max(tables, key=lambda table: len(table.get("rows") or []))


def _select_table_column(table: Dict[str, Any], instruction: str) -> Optional[Dict[str, Any]]:
    text = str(instruction or "").lower()
    columns = list(table.get("columns") or [])
    scored: List[tuple[int, Dict[str, Any]]] = []
    for column in columns:
        header = str(column.get("header") or "").lower()
        role = str(column.get("role") or "").lower()
        score = 0
        if header and header in text:
            score += 6
        if any(token and token in text for token in header.replace("_", " ").split()):
            score += 3
        if role and role in text:
            score += 3
        if role == "file_link" and any(term in text for term in ("file", "文件", "名称", "名字")):
            score += 5
        if role == "status" and any(term in text for term in ("status", "状态")):
            score += 5
        if role == "selection" and any(term in text for term in ("checkbox", "勾选", "选择")):
            score += 5
        if score:
            scored.append((score, column))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _table_row_selector(table: Dict[str, Any]) -> str:
    for row in table.get("rows") or []:
        for hint in row.get("locator_hints") or []:
            expression = str(hint.get("expression") or "")
            match = re.search(r"page\.locator\((['\"])(.*?)\1\)\.nth\(\d+\)", expression)
            if match:
                return match.group(2)
    return "tbody tr"


def _table_column_action_selector(table: Dict[str, Any], index: int, column: Dict[str, Any]) -> str:
    column_id = str(column.get("column_id") or "")
    rows = list(table.get("rows") or [])
    if index >= len(rows):
        return ""
    for cell in rows[index].get("cells") or []:
        if column_id and str(cell.get("column_id") or "") != column_id:
            continue
        actions = list(cell.get("actions") or cell.get("row_local_actions") or [])
        for action in actions:
            locator = action.get("locator") if isinstance(action, dict) else {}
            if isinstance(locator, dict) and locator.get("scope") == "row" and locator.get("value"):
                return str(locator.get("value"))
    if column_id:
        return f"td[data-colid={column_id!r}] a, td[data-colid={column_id!r}] button"
    return ""
```

- [ ] **Step 5: Run table ordinal tests**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_table_ordinal_lane_clicks_first_row_named_column_link RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_table_ordinal_lane_extracts_second_row_status RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py::test_table_ordinal_lane_falls_back_without_column_match -q
```

Expected: all pass.

- [ ] **Step 6: Run existing ordinal tests**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py -k "ordinal" -q
```

Expected: existing generic ordinal overlay tests still pass.

- [ ] **Step 7: Commit Task 4**

```powershell
git add RpaClaw/backend/rpa/recording_runtime_agent.py RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py
git commit -m "feat: add guarded table ordinal rpa lane"
```

---

### Task 5: Regression Verification

**Files:**
- No production edits unless verification reveals a bug.

- [ ] **Step 1: Run focused RPA snapshot/runtime tests**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_assistant_snapshot_runtime.py RpaClaw/backend/tests/test_rpa_snapshot_compression.py RpaClaw/backend/tests/test_rpa_recording_runtime_agent.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run compiler tests to protect trace-first replay**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_trace_skill_compiler.py -q
```

Expected: all compiler tests pass.

- [ ] **Step 3: Run route trace tests if available**

Run:

```powershell
$env:PYTHONPATH="RpaClaw"
pytest RpaClaw/backend/tests/test_rpa_route_trace.py -q
```

Expected: tests pass, or any pre-existing fixture/environment issue is documented without masking new failures.

- [ ] **Step 4: Inspect final diff**

Run:

```powershell
git diff --stat HEAD~4..HEAD
git status --short --branch
```

Expected: branch is clean after commits; changed files match this plan.

- [ ] **Step 5: Commit any verification-only fixes**

Only if a small bug fix was needed, commit it:

```powershell
git add <fixed-files>
git commit -m "fix: stabilize structured rpa snapshot tests"
```

If no fix was needed, do not create an empty commit.

---

## Self-Review

Spec coverage:

- `table_views` collection is covered by Task 1 and Task 2.
- `detail_views` collection is covered by Task 1 and Task 2.
- `char_budget=60000` is covered by Task 2.
- Prompt preference and diagnostics are covered by Task 3.
- Guarded table ordinal handling is covered by Task 4.
- Trace-first regression protection is covered by Task 5.

Placeholder scan:

- No unresolved `TBD` markers.
- No open-ended "add appropriate handling" steps.
- Each code-changing task includes target files, concrete snippets, commands, and expected results.

Type consistency:

- Browser snapshot keys are `table_views` and `detail_views`.
- Table row action fields support both `actions` and `row_local_actions` during rollout.
- Detail fields consistently use `data_prop`, `required`, `visible`, `hidden_reason`, and `value_kind`.
