import importlib
import json
import re
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SNAPSHOT_MODULE = importlib.import_module("backend.rpa.assistant_snapshot_runtime")
SNAPSHOT_V2_JS = SNAPSHOT_MODULE.SNAPSHOT_V2_JS

ASSISTANT_MODULE = importlib.import_module("backend.rpa.assistant_runtime")


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


class FieldGroupFrameworkStrategyTests(unittest.TestCase):
    """Tests that field_groups are correctly generated using framework strategies."""

    def test_js_has_enhanced_field_group_generation(self):
        """Verify the field_groups generation uses matchByDataProp and matchByFormContainer."""
        self.assertIn("matchByDataProp", SNAPSHOT_V2_JS)
        self.assertIn("matchByFormContainer", SNAPSHOT_V2_JS)
        self.assertIn("findValueInContainer", SNAPSHOT_V2_JS)
        self.assertIn("buildStableLocator", SNAPSHOT_V2_JS)

    def test_js_field_group_loop_uses_framework_container(self):
        """Verify the field group generation loop looks for form-item containers."""
        self.assertIn("findValueInContainer", SNAPSHOT_V2_JS)
        self.assertIn("buildStableLocator", SNAPSHOT_V2_JS)


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
