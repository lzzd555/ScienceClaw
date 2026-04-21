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
