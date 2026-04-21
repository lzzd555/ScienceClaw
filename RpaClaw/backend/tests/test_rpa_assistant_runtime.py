import importlib
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ASSISTANT_MODULE = importlib.import_module("backend.rpa.assistant_runtime")


class _FakeLocator:
    def __init__(self, text="", error=None, value="", checked=False, selected=False):
        self.text = text
        self.error = error
        self.value = value
        self.checked = checked
        self.selected = selected

    @property
    def first(self):
        return self

    async def click(self):
        return None

    async def fill(self, _value):
        return None

    async def press(self, _value):
        return None

    async def inner_text(self):
        if self.error:
            raise self.error
        return self.text

    async def input_value(self):
        return self.value

    async def is_checked(self):
        return self.checked

    async def is_selected(self):
        return self.selected

    async def text_content(self):
        return self.text


class _FakeScope:
    def __init__(self, locators, calls=None, frame_path=None):
        self.locators = locators
        self.calls = calls if calls is not None else []
        self.frame_path = list(frame_path or [])

    def frame_locator(self, selector):
        self.calls.append(("frame", tuple(self.frame_path), selector))
        return _FakeScope(self.locators, self.calls, self.frame_path + [selector])

    def locator(self, selector):
        self.calls.append(("locator", tuple(self.frame_path), selector))
        return self.locators.get((tuple(self.frame_path), "locator", selector), _FakeLocator())

    def get_by_role(self, role, **kwargs):
        name = kwargs.get("name", "")
        self.calls.append(("role", tuple(self.frame_path), role, name))
        return self.locators.get((tuple(self.frame_path), "role", role, name), _FakeLocator())

    def get_by_text(self, value):
        self.calls.append(("text", tuple(self.frame_path), value))
        return self.locators.get((tuple(self.frame_path), "text", value), _FakeLocator())

    def get_by_placeholder(self, value):
        self.calls.append(("placeholder", tuple(self.frame_path), value))
        return self.locators.get((tuple(self.frame_path), "placeholder", value), _FakeLocator())


class _FakePage:
    def __init__(self, locators):
        self.scope = _FakeScope(locators)

    def frame_locator(self, selector):
        return self.scope.frame_locator(selector)

    def locator(self, selector):
        return self.scope.locator(selector)

    def get_by_role(self, role, **kwargs):
        return self.scope.get_by_role(role, **kwargs)

    def get_by_text(self, value):
        return self.scope.get_by_text(value)

    def get_by_placeholder(self, value):
        return self.scope.get_by_placeholder(value)


class _FrameAwarePage:
    def __init__(self, locators):
        self.scope = _FakeScope(locators)

    def frame_locator(self, selector):
        return self.scope.frame_locator(selector)

    def locator(self, selector):
        return self.scope.locator(selector)

    def get_by_role(self, role, **kwargs):
        return self.scope.get_by_role(role, **kwargs)

    def get_by_text(self, value):
        return self.scope.get_by_text(value)

    def get_by_placeholder(self, value):
        return self.scope.get_by_placeholder(value)


class RPAAssistantRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_dedupe_field_groups_keeps_same_named_fields_in_distinct_frames_and_sections(self):
        field_groups = [
            {
                "field_name": "Report Title",
                "frame_path": ["iframe[title='left']"],
                "container_id": "",
                "field_node_id": "field-1",
                "value_node_id": "value-1",
                "bbox": {"x": 10, "y": 20, "width": 100, "height": 20},
                "locator": {"method": "text", "value": "Left Title"},
            },
            {
                "field_name": "Report Title",
                "frame_path": ["iframe[title='left']"],
                "container_id": "",
                "field_node_id": "field-2",
                "value_node_id": "value-2",
                "bbox": {"x": 10, "y": 60, "width": 100, "height": 20},
                "locator": {"method": "text", "value": "Left Title B"},
            },
            {
                "field_name": "Report Title",
                "frame_path": ["iframe[title='right']"],
                "container_id": "",
                "field_node_id": "field-3",
                "value_node_id": "value-3",
                "bbox": {"x": 10, "y": 20, "width": 100, "height": 20},
                "locator": {"method": "text", "value": "Right Title"},
            },
        ]

        deduped = ASSISTANT_MODULE._dedupe_field_groups(field_groups)

        self.assertEqual(len(deduped), 3)
        self.assertEqual(
            sorted(group["field_node_id"] for group in deduped),
            ["field-1", "field-2", "field-3"],
        )

    def test_dedupe_field_groups_keeps_repeated_same_named_fields_in_same_container_distinct(self):
        field_groups = [
            {
                "field_name": "Item Name",
                "frame_path": [],
                "container_id": "table-1",
                "bbox": {"x": 20, "y": 24, "width": 160, "height": 20},
                "locator": {"method": "text", "value": "Item Name A"},
            },
            {
                "field_name": "Item Name",
                "frame_path": [],
                "container_id": "table-1",
                "bbox": {"x": 20, "y": 64, "width": 160, "height": 20},
                "locator": {"method": "text", "value": "Item Name B"},
            },
        ]

        deduped = ASSISTANT_MODULE._dedupe_field_groups(field_groups)

        self.assertEqual(len(deduped), 2)
        self.assertEqual(
            [group["locator"]["value"] for group in deduped],
            ["Item Name A", "Item Name B"],
        )

    def test_extract_text_prefers_actionable_backed_field_group_over_content_duplicate(self):
        snapshot = {
            "frames": [],
            "field_groups": [
                {
                    "field_name": "Report Title",
                    "container_id": "form-1",
                    "frame_path": [],
                    "locator": {"method": "text", "value": "Quarterly Report"},
                    "value_locator": {"method": "text", "value": "Quarterly Report"},
                    "fallback_locator": {"method": "text", "value": "Quarterly Report"},
                    "field_node_id": None,
                    "value_node_id": "content-1",
                    "bbox": {"x": 10, "y": 12, "width": 180, "height": 20},
                    "extraction_kind": "text",
                    "allow_empty_fallback": True,
                },
                {
                    "field_name": "Report Title",
                    "container_id": "form-1",
                    "frame_path": [],
                    "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                    "value_locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                    "fallback_locator": {"method": "text", "value": "Quarterly Report"},
                    "field_node_id": "actionable-1",
                    "value_node_id": "actionable-1",
                    "bbox": {"x": 10, "y": 10, "width": 180, "height": 24},
                    "field_control_kind": "textbox",
                    "extraction_kind": "control_value",
                    "allow_empty_fallback": False,
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                        }
                    ],
                },
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "target_hint": {"name": "Report Title"},
            },
        )

        self.assertEqual(resolved["resolved"]["field_group"]["field_node_id"], "actionable-1")
        self.assertEqual(resolved["resolved"]["extraction_kind"], "control_value")

    async def test_extract_text_uses_control_value_for_field_group(self):
        snapshot = {
            "frames": [],
            "field_groups": [
                {
                    "field_name": "Report Title",
                    "container_id": "form-1",
                    "frame_path": [],
                    "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                    "value_locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                    "fallback_locator": {"method": "text", "value": "Quarterly Report"},
                    "field_node_id": "actionable-1",
                    "value_node_id": "actionable-1",
                    "bbox": {"x": 10, "y": 10, "width": 180, "height": 24},
                    "field_control_kind": "textbox",
                    "extraction_kind": "control_value",
                    "allow_empty_fallback": False,
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                        }
                    ],
                }
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "target_hint": {"name": "Report Title"},
            },
        )

        page = _FrameAwarePage(
            {
                ((), "role", "textbox", "Report Title"): _FakeLocator(text="", value="Quarterly Report"),
            }
        )

        result = await ASSISTANT_MODULE.execute_structured_intent(
            page,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "resolved": resolved["resolved"],
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "Quarterly Report")
        self.assertNotIn("fallback_used", result["step"]["assistant_diagnostics"])

    async def test_extract_text_uses_control_state_for_checkbox_field_group(self):
        snapshot = {
            "frames": [],
            "field_groups": [
                {
                    "field_name": "Subscribe",
                    "container_id": "form-1",
                    "frame_path": [],
                    "locator": {"method": "role", "role": "checkbox", "name": "Subscribe"},
                    "value_locator": {"method": "role", "role": "checkbox", "name": "Subscribe"},
                    "fallback_locator": {"method": "text", "value": "Subscribe"},
                    "field_node_id": "actionable-2",
                    "value_node_id": "actionable-2",
                    "bbox": {"x": 10, "y": 10, "width": 180, "height": 24},
                    "field_control_kind": "checkbox",
                    "extraction_kind": "control_state",
                    "allow_empty_fallback": False,
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "checkbox", "name": "Subscribe"},
                        }
                    ],
                }
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取订阅状态",
                "prompt": "提取订阅状态",
                "target_hint": {"name": "Subscribe"},
            },
        )

        page = _FrameAwarePage(
            {
                ((), "role", "checkbox", "Subscribe"): _FakeLocator(text="", checked=True),
            }
        )

        result = await ASSISTANT_MODULE.execute_structured_intent(
            page,
            {
                "action": "extract_text",
                "description": "提取订阅状态",
                "prompt": "提取订阅状态",
                "resolved": resolved["resolved"],
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "checked")
        self.assertNotIn("fallback_used", result["step"]["assistant_diagnostics"])

    def test_extract_text_prefers_field_group_value_locator(self):
        snapshot = {
            "frames": [],
            "field_groups": [
                {
                    "field_name": "Report Title",
                    "frame_path": [],
                    "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                    "fallback_locator": {"method": "text", "value": "Quarterly Report"},
                    "fallback_frame_path": ["iframe[title='details']"],
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                        }
                    ],
                }
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "target_hint": {"name": "Report Title"},
            },
        )

        self.assertEqual(resolved["resolved"]["field_group"]["field_name"], "Report Title")
        self.assertEqual(resolved["resolved"]["locator"], {"method": "role", "role": "textbox", "name": "Report Title"})
        self.assertEqual(resolved["resolved"]["fallback_locator"], {"method": "text", "value": "Quarterly Report"})
        self.assertEqual(resolved["resolved"]["fallback_frame_path"], ["iframe[title='details']"])

    async def test_extract_text_falls_back_to_content_nodes_when_field_group_locator_is_invalid(self):
        snapshot = {
            "frames": [],
            "field_groups": [
                {
                    "field_name": "Report Title",
                    "frame_path": [],
                    "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                    "fallback_locator": {"method": "text", "value": "Quarterly Report"},
                    "fallback_frame_path": ["iframe[title='details']"],
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                        }
                    ],
                }
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "target_hint": {"name": "Report Title"},
            },
        )

        page = _FrameAwarePage(
            {
                ((), "role", "textbox", "Report Title"): _FakeLocator(error=RuntimeError("primary locator failed")),
                (("iframe[title='details']",), "text", "Quarterly Report"): _FakeLocator("Quarterly Report"),
            }
        )

        result = await ASSISTANT_MODULE.execute_structured_intent(
            page,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "resolved": resolved["resolved"],
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "Quarterly Report")
        self.assertEqual(result["step"]["assistant_diagnostics"]["fallback_used"], "content_nodes")
        self.assertIn(("frame", (), "iframe[title='details']"), page.scope.calls)
        self.assertIn(("role", (), "textbox", "Report Title"), page.scope.calls)
        self.assertIn(("text", ("iframe[title='details']",), "Quarterly Report"), page.scope.calls)

    async def test_extract_text_uses_fallback_frame_path_when_it_differs_from_primary(self):
        snapshot = {
            "frames": [],
            "field_groups": [
                {
                    "field_name": "Report Title",
                    "frame_path": ["iframe[title='primary']"],
                    "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                    "fallback_locator": {"method": "text", "value": "Quarterly Report"},
                    "fallback_frame_path": ["iframe[title='fallback']"],
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                        }
                    ],
                }
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "target_hint": {"name": "Report Title"},
            },
        )

        page = _FrameAwarePage(
            {
                (("iframe[title='primary']",), "role", "textbox", "Report Title"): _FakeLocator(error=RuntimeError("primary locator failed")),
                (("iframe[title='fallback']",), "text", "Quarterly Report"): _FakeLocator("Quarterly Report"),
            }
        )

        result = await ASSISTANT_MODULE.execute_structured_intent(
            page,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "resolved": resolved["resolved"],
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "Quarterly Report")
        self.assertEqual(result["step"]["assistant_diagnostics"]["fallback_used"], "content_nodes")
        self.assertIn(("frame", (), "iframe[title='primary']"), page.scope.calls)
        self.assertIn(("frame", (), "iframe[title='fallback']"), page.scope.calls)
        self.assertIn(("role", ("iframe[title='primary']",), "textbox", "Report Title"), page.scope.calls)
        self.assertIn(("text", ("iframe[title='fallback']",), "Quarterly Report"), page.scope.calls)

    async def test_extract_text_keeps_field_group_frame_for_field_group_fallback_when_content_node_differs(self):
        snapshot = {
            "frames": [],
            "field_groups": [
                {
                    "field_name": "Report Title",
                    "frame_path": ["iframe[title='field-group']"],
                    "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                    "fallback_locator": {"method": "text", "value": "Quarterly Report"},
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "textbox", "name": "Report Title"},
                        }
                    ],
                }
            ],
            "content_nodes": [
                {
                    "node_id": "content-1",
                    "frame_path": ["iframe[title='content-node']"],
                    "semantic_kind": "heading",
                    "field_name": "Report Title",
                    "text": "Quarterly Report",
                    "locator": {"method": "text", "value": "Quarterly Report"},
                    "bbox": {"x": 10, "y": 10, "width": 200, "height": 24},
                }
            ],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "target_hint": {"name": "Report Title"},
            },
        )

        self.assertEqual(resolved["resolved"]["fallback_locator"], {"method": "text", "value": "Quarterly Report"})
        self.assertEqual(resolved["resolved"]["fallback_frame_path"], ["iframe[title='field-group']"])

        page = _FrameAwarePage(
            {
                (("iframe[title='field-group']",), "role", "textbox", "Report Title"): _FakeLocator(error=RuntimeError("primary locator failed")),
                (("iframe[title='field-group']",), "text", "Quarterly Report"): _FakeLocator("Quarterly Report"),
            }
        )

        result = await ASSISTANT_MODULE.execute_structured_intent(
            page,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "resolved": resolved["resolved"],
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "Quarterly Report")
        self.assertEqual(result["step"]["assistant_diagnostics"]["fallback_used"], "content_nodes")
        self.assertIn(("frame", (), "iframe[title='field-group']"), page.scope.calls)
        self.assertNotIn(("frame", (), "iframe[title='content-node']"), page.scope.calls)
