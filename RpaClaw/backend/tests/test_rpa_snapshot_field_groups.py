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

    def test_ensureContainer_includes_aui_form_item_selector(self):
        self.assertIn(".aui-form-item", SNAPSHOT_V2_JS,
                      "ensureContainer should include .aui-form-item in closest() selector")

    def test_ensureContainer_includes_ant_form_item_selector(self):
        self.assertIn(".ant-form-item", SNAPSHOT_V2_JS,
                      "ensureContainer should include .ant-form-item in closest() selector")

    def test_ensureContainer_includes_el_form_item_selector(self):
        self.assertIn(".el-form-item", SNAPSHOT_V2_JS,
                      "ensureContainer should include .el-form-item in closest() selector")

    def test_ensureContainer_includes_data_prop_selector(self):
        self.assertIn("[data-prop]", SNAPSHOT_V2_JS,
                      "ensureContainer should include [data-prop] in closest() selector")

    def test_ensureContainer_includes_field_panel_selector(self):
        self.assertIn(".field-panel", SNAPSHOT_V2_JS,
                      "ensureContainer should include .field-panel in closest() selector")

    def test_ensureContainer_includes_field_item_selector(self):
        self.assertIn(".field-item", SNAPSHOT_V2_JS,
                      "ensureContainer should include .field-item in closest() selector")

    def test_ensureContainer_includes_aui_collapse_content_selector(self):
        self.assertIn(".aui-collapse-item__content", SNAPSHOT_V2_JS,
                      "ensureContainer should include .aui-collapse-item__content in closest() selector")


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
        """Verify fieldNameFromElement delegates to helpers that use closest() for form items."""
        fn_body = _extract_js_function_body(SNAPSHOT_V2_JS, "fieldNameFromElement")
        self.assertIn("matchByDataProp", fn_body,
                      "fieldNameFromElement should call matchByDataProp for framework container lookup")
        self.assertIn("matchByFormContainer", fn_body,
                      "fieldNameFromElement should call matchByFormContainer for framework container lookup")
        # Verify the helpers themselves contain closest() and aui-form-item
        data_prop_body = _extract_js_function_body(SNAPSHOT_V2_JS, "matchByDataProp")
        self.assertIn("closest", data_prop_body,
                      "matchByDataProp should use closest() to find form item container")
        self.assertIn("aui-form-item", data_prop_body,
                      "matchByDataProp should look for .aui-form-item container")

    def test_field_name_from_element_priority_order(self):
        """Verify AX semantic signals come before framework container lookup."""
        fn_body = _extract_js_function_body(SNAPSHOT_V2_JS, "fieldNameFromElement")
        # el.labels check should appear before closest() check
        labels_pos = fn_body.find("el.labels")
        closest_pos = fn_body.find("closest")
        if labels_pos >= 0 and closest_pos >= 0:
            self.assertLess(labels_pos, closest_pos,
                            "el.labels check should come before closest() in fieldNameFromElement")
