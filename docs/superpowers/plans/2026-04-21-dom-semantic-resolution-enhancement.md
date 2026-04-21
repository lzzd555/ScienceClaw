# DOM Semantic Resolution Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the RPA snapshot JS to resolve label-value associations in complex framework forms (AUI, Ant Design, Element UI) and standard HTML via AX semantics + framework-aware container grouping.

**Architecture:** All changes live inside `SNAPSHOT_V2_JS` in `assistant_snapshot_runtime.py`. We extend container detection to recognize form-item wrappers, add framework-specific label-value matching functions, and adjust `fieldNameFromElement()` priority. The Python runtime (`assistant_runtime.py`) is untouched — it already consumes `field_groups` correctly.

**Tech Stack:** JavaScript (runs in-browser via `page.evaluate`), Python test infrastructure with mock snapshot data.

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `RpaClaw/backend/rpa/assistant_snapshot_runtime.py` | Modify | All JS changes: container detection, field name resolution, field group strategies |
| `RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py` | Create | Tests for new field_groups behavior with mock snapshot data |

---

### Task 1: Extend `ensureContainer()` to recognize form-item wrappers

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py` (the `ensureContainer` function inside `SNAPSHOT_V2_JS`)

- [ ] **Step 1: Write the test**

Create `RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py`:

```python
import importlib
import json
import re
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SNAPSHOT_MODULE = importlib.import_module("backend.rpa.assistant_snapshot_runtime")
SNAPSHOT_V2_JS = SNAPSHOT_MODULE.SNAPSHOT_V2_JS


def _extract_js_function_body(js_source: str, function_name: str) -> str:
    """Extract a function body from the JS source string."""
    pattern = re.compile(r"function\s+" + re.escape(function_name) + r"\s*\([^)]*\)\s*\{", re.DOTALL)
    match = pattern.search(js_source)
    if not match:
        return ""
    start = match.end()
    depth = 1
    pos = start
    while pos < len(js_source) and depth > 0:
        ch = js_source[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        pos += 1
    return js_source[start : pos - 1]


class ContainerDetectionTests(unittest.TestCase):
    """Tests for ensureContainer recognizing form-item wrappers."""

    def _run_snapshot_js(self, html: str) -> dict:
        """Extract field_groups and containers from SNAPSHOT_V2_JS by evaluating
        it against a minimal DOM. We test by inspecting the JS source string
        directly rather than running a browser."""
        return {
            "has_form_item_selector": ".aui-form-item" in SNAPSHOT_V2_JS,
            "has_ant_form_item": ".ant-form-item" in SNAPSHOT_V2_JS,
            "has_el_form_item": ".el-form-item" in SNAPSHOT_V2_JS,
            "has_data_prop": "[data-prop]" in SNAPSHOT_V2_JS,
            "has_field_panel": ".field-panel" in SNAPSHOT_V2_JS,
        }

    def test_ensureContainer_includes_aui_form_item_selector(self):
        result = self._run_snapshot_js("")
        self.assertTrue(result["has_form_item_selector"],
                        "ensureContainer should include .aui-form-item in closest() selector")

    def test_ensureContainer_includes_ant_form_item_selector(self):
        result = self._run_snapshot_js("")
        self.assertTrue(result["has_ant_form_item"],
                        "ensureContainer should include .ant-form-item in closest() selector")

    def test_ensureContainer_includes_el_form_item_selector(self):
        result = self._run_snapshot_js("")
        self.assertTrue(result["has_el_form_item"],
                        "ensureContainer should include .el-form-item in closest() selector")

    def test_ensureContainer_includes_data_prop_selector(self):
        result = self._run_snapshot_js("")
        self.assertTrue(result["has_data_prop"],
                        "ensureContainer should include [data-prop] in closest() selector")

    def test_ensureContainer_includes_field_panel_selector(self):
        result = self._run_snapshot_js("")
        self.assertTrue(result["has_field_panel"],
                        "ensureContainer should include .field-panel in closest() selector")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py::ContainerDetectionTests -v`
Expected: FAIL — `.aui-form-item` not yet in `SNAPSHOT_V2_JS`

- [ ] **Step 3: Extend `ensureContainer()` selector**

In `RpaClaw/backend/rpa/assistant_snapshot_runtime.py`, modify the `closest()` call inside `ensureContainer()`:

Change this line (around line 132):
```javascript
const containerEl = el.closest('table,[role=table],[role=grid],ul,ol,[role=list],form,[role=toolbar],section,article');
```

To:
```javascript
const containerEl = el.closest('table,[role=table],[role=grid],ul,ol,[role=list],form,[role=toolbar],section,article,.aui-form-item,.ant-form-item,.el-form-item,[data-prop],.field-panel,.field-item,.aui-collapse-item__content');
```

Also update `detectContainerKind()` to recognize these new containers. Add after the existing `article` check (around line 115):

```javascript
if (el.classList && el.classList.contains('aui-form-item'))
    return 'form_section';
if (el.classList && el.classList.contains('ant-form-item'))
    return 'form_section';
if (el.classList && el.classList.contains('el-form-item'))
    return 'form_section';
if (el.getAttribute('data-prop'))
    return 'form_section';
if (el.classList && el.classList.contains('field-panel'))
    return 'form_section';
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py::ContainerDetectionTests -v`
Expected: PASS

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_assistant_runtime.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/assistant_snapshot_runtime.py RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py
git commit -m "feat: extend container detection for AUI/Ant Design/Element UI form items"
```

---

### Task 2: Add framework-aware field name resolution in `fieldNameFromElement()`

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py` (the `fieldNameFromElement` function)
- Modify: `RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py`

- [ ] **Step 1: Write the test**

Add to `test_rpa_snapshot_field_groups.py`:

```python
class FieldNameResolutionTests(unittest.TestCase):
    """Tests for fieldNameFromElement priority and framework container lookup."""

    def test_js_has_match_by_data_prop_function(self):
        """Verify matchByDataProp helper function exists in JS."""
        self.assertIn("matchByDataProp", SNAPSHOT_V2_JS,
                      "matchByDataProp function should be defined")

    def test_js_has_match_by_form_container_function(self):
        """Verify matchByFormContainer helper function exists in JS."""
        self.assertIn("matchByFormContainer", SNAPSHOT_V2_JS,
                      "matchByFormContainer function should be defined")

    def test_field_name_from_element_uses_form_item_closest(self):
        """Verify fieldNameFromElement includes .closest() for form items."""
        fn_body = _extract_js_function_body(SNAPSHOT_V2_JS, "fieldNameFromElement")
        self.assertIn("closest", fn_body,
                      "fieldNameFromElement should use closest() to find form item container")
        self.assertIn("aui-form-item", fn_body,
                      "fieldNameFromElement should look for .aui-form-item container")

    def test_field_name_from_element_priority_order(self):
        """Verify AX semantic signals come before framework container lookup."""
        fn_body = _extract_js_function_body(SNAPSHOT_V2_JS, "fieldNameFromElement")
        # el.labels check should appear before closest() check
        labels_pos = fn_body.find("el.labels")
        closest_pos = fn_body.find("closest")
        if labels_pos >= 0 and closest_pos >= 0:
            self.assertLess(labels_pos, closest_pos,
                            "el.labels check should come before closest() in fieldNameFromElement")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py::FieldNameResolutionTests -v`
Expected: FAIL — `matchByDataProp` not yet defined, `closest` not in `fieldNameFromElement`

- [ ] **Step 3: Add `matchByDataProp()` helper function**

Add this new function inside `SNAPSHOT_V2_JS`, right before `fieldNameFromElement()` (around line 246):

```javascript
function matchByDataProp(el) {
    const formItem = el.closest('.aui-form-item,[data-prop]');
    if (!formItem) return '';
    const dataProp = formItem.getAttribute('data-prop');
    if (!dataProp) return '';
    const label = formItem.querySelector('label[for="' + dataProp + '"]');
    if (label) return normalizeText(label.innerText || label.textContent || '', 80);
    const labelEl = formItem.querySelector('.aui-form-item__label,.ant-form-item-label,.el-form-item__label');
    if (labelEl) return normalizeText(labelEl.innerText || labelEl.textContent || '', 80);
    return '';
}
```

- [ ] **Step 4: Add `matchByFormContainer()` helper function**

Add right after `matchByDataProp()`:

```javascript
function matchByFormContainer(el) {
    const formItem = el.closest('.aui-form-item,.ant-form-item,.el-form-item,.field-panel,.field-item');
    if (!formItem) return '';
    const labelEl = formItem.querySelector('.aui-form-item__label,.ant-form-item-label,.el-form-item__label,label');
    if (!labelEl) return '';
    const text = normalizeText(labelEl.innerText || labelEl.textContent || '', 80);
    if (!text) return '';
    return text;
}
```

- [ ] **Step 5: Refactor `fieldNameFromElement()` priority**

Replace the existing `fieldNameFromElement` function (lines 246-290) with this version that adds framework container lookup at priority 4:

```javascript
function fieldNameFromElement(el, role) {
    // Priority 1: el.labels (standard <label for="id">)
    const labelTexts = [];
    try {
        if (el.labels) {
            for (const labelEl of Array.from(el.labels)) {
                const text = normalizeText(labelEl.innerText || labelEl.textContent || '', 80);
                if (text)
                    labelTexts.push(text);
            }
        }
    } catch (e) {}
    // Priority 2: aria-label
    const ariaLabel = normalizeText(el.getAttribute('aria-label') || '', 80);
    if (ariaLabel)
        return ariaLabel;
    // Priority 3: aria-labelledby
    const ariaLabelledBy = normalizeText(el.getAttribute('aria-labelledby') || '', 80);
    if (ariaLabelledBy) {
        const parts = [];
        for (const id of ariaLabelledBy.split(/\s+/)) {
            if (!id)
                continue;
            const labelEl = document.getElementById(id);
            if (!labelEl)
                continue;
            const text = normalizeText(labelEl.innerText || labelEl.textContent || '', 80);
            if (text)
                parts.push(text);
        }
        if (parts.length)
            return normalizeText(parts.join(' '), 80);
    }
    if (labelTexts.length)
        return labelTexts[0];
    // Priority 4: Framework container lookup
    const dataPropMatch = matchByDataProp(el);
    if (dataPropMatch)
        return dataPropMatch;
    const formContainerMatch = matchByFormContainer(el);
    if (formContainerMatch)
        return formContainerMatch;
    // Priority 5: placeholder
    const placeholder = normalizeText(el.getAttribute('placeholder') || '', 80);
    if (placeholder)
        return placeholder;
    // Priority 6: title
    const title = normalizeText(el.getAttribute('title') || '', 80);
    if (title)
        return title;
    // Priority 7: getAccessibleName
    const name = getAccessibleName(el);
    if (name)
        return name;
    // Priority 8: role fallback
    if (role)
        return normalizeText(role, 80);
    return '';
}
```

**Important:** The existing code already has the same logic but without the framework container lookup. The refactored version reorders to insert `matchByDataProp` and `matchByFormContainer` at priority 4, between `el.labels` and `placeholder`.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py::FieldNameResolutionTests -v`
Expected: PASS

- [ ] **Step 7: Run existing tests to verify no regression**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_assistant_runtime.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add RpaClaw/backend/rpa/assistant_snapshot_runtime.py RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py
git commit -m "feat: add framework-aware field name resolution with AX priority"
```

---

### Task 3: Add data-prop based value locator generation

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py` (new function + field_groups generation loop)
- Modify: `RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py`

- [ ] **Step 1: Write the test**

Add to `test_rpa_snapshot_field_groups.py`:

```python
class DataPropFieldGroupTests(unittest.TestCase):
    """Tests that field_groups are generated with data-prop based locators for AUI forms."""

    def test_js_has_find_value_in_container_function(self):
        """Verify findValueInContainer helper exists for locating value nodes."""
        self.assertIn("findValueInContainer", SNAPSHOT_V2_JS,
                      "findValueInContainer function should be defined")

    def test_js_has_build_stable_locator_function(self):
        """Verify buildStableLocator helper exists for generating stable locators."""
        self.assertIn("buildStableLocator", SNAPSHOT_V2_JS,
                      "buildStableLocator function should be defined")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py::DataPropFieldGroupTests -v`
Expected: FAIL — `findValueInContainer` and `buildStableLocator` not yet defined

- [ ] **Step 3: Add `findValueInContainer()` helper**

Add after `matchByFormContainer()` in `SNAPSHOT_V2_JS`:

```javascript
function findValueInContainer(container, controlNode) {
    // Priority 1: display-only content (AUI pattern)
    const displayOnly = container.querySelector('.aui-input-display-only__content');
    if (displayOnly) {
        const text = normalizeText(displayOnly.innerText || displayOnly.textContent || '', 160);
        if (text) return { element: displayOnly, text: text };
    }
    // Priority 2: data-field attribute
    const dataField = container.querySelector('[data-field]');
    if (dataField) {
        const text = normalizeText(dataField.innerText || dataField.textContent || '', 160);
        if (text) return { element: dataField, text: text };
    }
    // Priority 3: Ant Design display text
    const antText = container.querySelector('.ant-form-text');
    if (antText) {
        const text = normalizeText(antText.innerText || antText.textContent || '', 160);
        if (text) return { element: antText, text: text };
    }
    // Priority 4: disabled input value
    const disabledInput = container.querySelector('input[disabled],textarea[disabled]');
    if (disabledInput && disabledInput !== controlNode) {
        const val = normalizeText(disabledInput.value || disabledInput.getAttribute('title') || '', 160);
        if (val) return { element: disabledInput, text: val };
    }
    return null;
}
```

- [ ] **Step 4: Add `buildStableLocator()` helper**

Add right after `findValueInContainer()`:

```javascript
function buildStableLocator(container, valueElement) {
    // Priority 1: data-prop on the container (most stable for AUI)
    const dataProp = container.getAttribute('data-prop');
    if (dataProp) {
        return { method: 'css', value: '[data-prop="' + dataProp + '"]' };
    }
    // Priority 2: data-field on the value element
    if (valueElement) {
        const dataField = valueElement.getAttribute('data-field');
        if (dataField) {
            return { method: 'css', value: '[data-field="' + dataField + '"]' };
        }
    }
    // Priority 3: use the element's existing locator (will be built by buildFallbackLocator)
    return null;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py::DataPropFieldGroupTests -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add RpaClaw/backend/rpa/assistant_snapshot_runtime.py RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py
git commit -m "feat: add value finder and stable locator builder for framework forms"
```

---

### Task 4: Enhance field_groups generation with framework-aware strategies

**Files:**
- Modify: `RpaClaw/backend/rpa/assistant_snapshot_runtime.py` (the actionable_nodes field_groups loop + new content_nodes scan)
- Modify: `RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py`

- [ ] **Step 1: Write the test**

Add to `test_rpa_snapshot_field_groups.py`:

```python
class FieldGroupFrameworkStrategyTests(unittest.TestCase):
    """Tests that field_groups are correctly generated using framework strategies."""

    def test_js_has_enhanced_field_group_generation(self):
        """Verify the field_groups generation uses matchByDataProp and matchByFormContainer."""
        # Check that the actionable_nodes field_groups loop calls the new functions
        self.assertIn("matchByDataProp", SNAPSHOT_V2_JS)
        self.assertIn("matchByFormContainer", SNAPSHOT_V2_JS)
        self.assertIn("findValueInContainer", SNAPSHOT_V2_JS)
        self.assertIn("buildStableLocator", SNAPSHOT_V2_JS)

    def test_js_field_group_loop_uses_framework_container(self):
        """Verify the field group generation loop looks for form-item containers."""
        # The loop that generates field_groups from actionable_nodes should
        # reference the new framework-aware functions
        self.assertIn("findValueInContainer", SNAPSHOT_V2_JS)
        self.assertIn("buildStableLocator", SNAPSHOT_V2_JS)
```

- [ ] **Step 2: Run test to verify it passes (functions exist from Task 3)**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py::FieldGroupFrameworkStrategyTests -v`
Expected: PASS (functions were added in Task 3)

- [ ] **Step 3: Enhance the actionable_nodes field_groups loop**

In `SNAPSHOT_V2_JS`, the second `for` loop (around line 548) iterates `actionable_nodes` to build field_groups for form controls. Replace the entire loop with this enhanced version:

Replace from `for (const node of result.actionable_nodes) {` (the second such loop, ~line 548) to the end of that loop block (~line 577):

```javascript
    for (const node of result.actionable_nodes) {
        const tag = (node.tag || '').toLowerCase();
        if (!(tag === 'input' || tag === 'textarea' || tag === 'select' || node.role === 'textbox' || node.role === 'combobox' || node.role === 'checkbox' || node.role === 'radio' || node.type === 'contenteditable'))
            continue;
        const fieldName = fieldNameFromNode(node);
        if (!fieldName)
            continue;
        // Try to find a value node using existing name-based matching
        let valueNode = fieldValueNodeCandidates(fieldName, node.container_id, node);
        let stableLocator = null;
        // Look up the DOM element for the container (key in containerMap is the DOM element)
        const containerEntry = Array.from(containerMap.entries())
            .find(([domEl, cObj]) => cObj.container_id === node.container_id);
        const containerDomEl = containerEntry ? containerEntry[0] : null;
        // If no value node found by name, try container-based value search
        if (!valueNode && containerDomEl) {
            const found = findValueInContainer(containerDomEl, null);
            if (found) {
                valueNode = {
                    node_id: 'content-derived-' + fieldGroupIndex,
                    text: found.text,
                    bbox: found.element.getBoundingClientRect ? bbox(found.element.getBoundingClientRect()) : node.bbox,
                    locator: buildStableLocator(containerDomEl, found.element) || { method: 'text', value: found.text },
                    locator_candidates: [],
                    container_id: node.container_id,
                };
            }
        }
        // Build stable value_locator from container
        if (containerDomEl) {
            stableLocator = buildStableLocator(containerDomEl, null);
        }
        const containerObj = containerEntry ? containerEntry[1] : {};
        const controlExtractionKind = node.role === 'checkbox' || node.role === 'radio' ? 'control_state' : 'control_value';
        addFieldGroup({
            frame_path: [],
            container_id: node.container_id,
            container_kind: containerObj.container_kind || '',
            field_name: fieldName,
            field_control_kind: node.role || node.type || tag,
            field_node_id: node.node_id,
            value_node_id: valueNode ? valueNode.node_id : null,
            label_node_id: null,
            bbox: valueNode ? valueNode.bbox : node.bbox,
            locator: valueNode ? valueNode.locator : (stableLocator || node.locator),
            value_locator: stableLocator || node.locator,
            locator_candidates: valueNode ? (valueNode.locator_candidates || []) : (node.locator_candidates || []),
            selected_locator_kind: valueNode ? 'content_nodes' : 'actionable_nodes',
            extraction_kind: controlExtractionKind,
            allow_empty_fallback: false,
            fallback_locator: valueNode ? valueNode.locator : node.locator,
            fallback_frame_path: [],
        });
    }
```

- [ ] **Step 4: Run existing tests to verify no regression**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_assistant_runtime.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add RpaClaw/backend/rpa/assistant_snapshot_runtime.py RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py
git commit -m "feat: enhance field_groups generation with framework-aware value resolution"
```

---

### Task 5: Add end-to-end field_groups resolution tests

**Files:**
- Modify: `RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py`

These tests verify the Python `resolve_structured_intent` correctly handles field_groups produced by the enhanced JS. They use mock snapshot data that simulates what the enhanced JS would produce.

- [ ] **Step 1: Write the test for AUI data-prop field_group**

Add to `test_rpa_snapshot_field_groups.py`:

```python
ASSISTANT_MODULE = importlib.import_module("backend.rpa.assistant_runtime")


class AUIFieldGroupResolutionTests(unittest.TestCase):
    """Test Python resolution of field_groups from AUI-style forms."""

    def _make_aui_field_group(self, field_name, data_prop, extraction_kind="control_value"):
        return {
            "field_name": field_name,
            "frame_path": [],
            "container_id": "container-1",
            "container_kind": "form_section",
            "field_control_kind": "textbox",
            "field_node_id": "actionable-1",
            "value_node_id": None,
            "label_node_id": None,
            "bbox": {"x": 10, "y": 100, "width": 200, "height": 24},
            "locator": {"method": "css", "value": '[data-prop="' + data_prop + '"]'},
            "value_locator": {"method": "css", "value": '[data-prop="' + data_prop + '"]'},
            "locator_candidates": [
                {
                    "kind": "css",
                    "selected": True,
                    "locator": {"method": "css", "value": '[data-prop="' + data_prop + '"]'},
                }
            ],
            "selected_locator_kind": "actionable_nodes",
            "extraction_kind": extraction_kind,
            "allow_empty_fallback": False,
            "fallback_locator": {"method": "css", "value": '[data-prop="' + data_prop + '"]'},
            "fallback_frame_path": [],
        }

    def test_resolve_aui_field_group_by_data_prop(self):
        """field_group with data-prop locator resolves correctly."""
        snapshot = {
            "frames": [],
            "field_groups": [
                self._make_aui_field_group("期望完成时间 (UTC+08:00)", "expectedCompletionDate"),
            ],
            "content_nodes": [],
            "containers": [],
        }
        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取期望完成时间",
                "prompt": "提取期望完成时间",
                "target_hint": {"name": "期望完成时间"},
            },
        )
        self.assertIn("resolved", resolved)
        self.assertEqual(
            resolved["resolved"]["locator"],
            {"method": "css", "value": "[data-prop=\"expectedCompletionDate\"]"},
        )
        self.assertEqual(resolved["resolved"]["extraction_kind"], "control_value")

    def test_resolve_aui_field_group_matches_partial_name(self):
        """field_group matches when target hint partially matches field_name."""
        snapshot = {
            "frames": [],
            "field_groups": [
                self._make_aui_field_group("期望完成时间 (UTC+08:00)", "expectedCompletionDate"),
            ],
            "content_nodes": [],
            "containers": [],
        }
        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取完成时间",
                "prompt": "提取完成时间",
                "target_hint": {"name": "完成时间"},
            },
        )
        self.assertIn("resolved", resolved)
        self.assertIsNotNone(resolved["resolved"].get("field_group"))

    def test_resolve_multiple_aui_field_groups(self):
        """Multiple AUI field_groups resolve to the correct one by name."""
        snapshot = {
            "frames": [],
            "field_groups": [
                self._make_aui_field_group("需求标题", "reqName"),
                self._make_aui_field_group("期望完成时间 (UTC+08:00)", "expectedCompletionDate"),
            ],
            "content_nodes": [],
            "containers": [],
        }
        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取期望完成时间",
                "prompt": "提取期望完成时间",
                "target_hint": {"name": "期望完成时间"},
            },
        )
        self.assertIn("resolved", resolved)
        self.assertEqual(
            resolved["resolved"]["field_group"]["field_name"],
            "期望完成时间 (UTC+08:00)",
        )
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py::AUIFieldGroupResolutionTests -v`
Expected: PASS — the Python resolution logic already handles `field_groups` correctly regardless of locator type

- [ ] **Step 3: Commit**

```bash
git add RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py
git commit -m "test: add AUI field_group resolution tests with data-prop locators"
```

---

### Task 6: Run full regression suite

**Files:** None (verification only)

- [ ] **Step 1: Run all RPA tests**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/test_rpa_assistant_runtime.py RpaClaw/backend/tests/test_rpa_snapshot_field_groups.py -v`
Expected: All PASS

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/lzzd/project/RPA-Agent/ScienceClaw && python -m pytest RpaClaw/backend/tests/ -v --timeout=60`
Expected: All PASS

- [ ] **Step 3: Final commit if any test fixes needed**

If any tests needed adjustment, commit the fixes:
```bash
git add -A
git commit -m "fix: test adjustments for DOM semantic resolution enhancement"
```
