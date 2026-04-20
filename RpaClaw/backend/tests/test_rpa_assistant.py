import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ASSISTANT_MODULE = importlib.import_module("backend.rpa.assistant")
ASSISTANT_RUNTIME_MODULE = importlib.import_module("backend.rpa.assistant_runtime")
MANAGER_MODULE = importlib.import_module("backend.rpa.manager")


class _FakeModel:
    def __init__(self, response):
        self._response = response

    async def ainvoke(self, _messages):
        return self._response


class _FakeStreamingModel:
    def __init__(self, chunks):
        self._chunks = chunks

    async def astream(self, _messages):
        for chunk in self._chunks:
            yield chunk


class _FakePage:
    url = "https://example.com"

    async def title(self):
        return "Example"


class _FakeSnapshotFrame:
    def __init__(self, name, url, frame_path, elements=None, child_frames=None):
        self.name = name
        self.url = url
        self._frame_path = frame_path
        self._elements = elements or []
        self.child_frames = child_frames or []

    async def evaluate(self, _script):
        return json.dumps(self._elements)


class _FakeSnapshotPage:
    url = "https://example.com"

    def __init__(self, main_frame):
        self.main_frame = main_frame

    async def title(self):
        return "Example"


class _FakeLocator:
    def __init__(self, text=""):
        self.click_calls = 0
        self.text = text

    async def click(self):
        self.click_calls += 1

    async def inner_text(self):
        return self.text


class _FakeFrameScope:
    def __init__(self):
        self.locator_calls = []
        self.locator_obj = _FakeLocator("Resolved text")

    def locator(self, selector):
        self.locator_calls.append(selector)
        return self.locator_obj

    def frame_locator(self, selector):
        self.locator_calls.append(f"frame:{selector}")
        return self

    def get_by_role(self, role, **kwargs):
        self.locator_calls.append(f"role:{role}:{kwargs.get('name', '')}")
        return self.locator_obj

    def get_by_text(self, value):
        self.locator_calls.append(f"text:{value}")
        return self.locator_obj


class _FakeActionPage(_FakePage):
    def __init__(self):
        self.scope = _FakeFrameScope()
        self.goto_calls = []
        self.load_state_calls = []

    def frame_locator(self, selector):
        self.scope.locator_calls.append(f"frame:{selector}")
        return self.scope

    def locator(self, selector):
        self.scope.locator_calls.append(selector)
        return self.scope.locator_obj

    def get_by_role(self, role, **kwargs):
        self.scope.locator_calls.append(f"role:{role}:{kwargs.get('name', '')}")
        return self.scope.locator_obj

    def get_by_text(self, value):
        self.scope.locator_calls.append(f"text:{value}")
        return self.scope.locator_obj

    async def goto(self, url):
        self.goto_calls.append(url)

    async def wait_for_load_state(self, state):
        self.load_state_calls.append(state)


class RPAReActAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_llm_preserves_whitespace_between_stream_chunks(self):
        response_text = 'await page.goto("https://github.com/trending?since=weekly")\n'
        stream_chunks = [
            SimpleNamespace(content="await", additional_kwargs={}),
            SimpleNamespace(content=" page", additional_kwargs={}),
            SimpleNamespace(content='.goto("https://github.com/trending?since=weekly")\n', additional_kwargs={}),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_extracts_text_from_stream_content_blocks(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        stream_chunks = [
            SimpleNamespace(
                content=[
                    {"type": "thinking", "thinking": "inspect the page"},
                    {"type": "text", "text": response_text},
                ],
                additional_kwargs={},
            ),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_falls_back_to_stream_reasoning_content(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        stream_chunks = [
            SimpleNamespace(
                content="",
                additional_kwargs={"reasoning_content": response_text},
            ),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_extracts_text_from_content_blocks(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        fake_response = SimpleNamespace(
            content=[
                {"type": "thinking", "thinking": "inspect the page"},
                {"type": "text", "text": response_text},
            ],
            additional_kwargs={},
        )

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeModel(fake_response),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_run_falls_back_to_reasoning_content_when_text_is_empty(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        fake_response = SimpleNamespace(
            content="",
            additional_kwargs={"reasoning_content": response_text},
        )
        agent = ASSISTANT_MODULE.RPAReActAgent()

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeModel(fake_response),
        ), patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value={"url": "https://example.com", "title": "Example", "frames": []}),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=_FakePage(),
                goal="finish the task",
                existing_steps=[],
            ):
                events.append(event)

        self.assertEqual(
            [event["event"] for event in events],
            ["agent_thought", "agent_done"],
        )

    async def test_react_agent_build_observation_lists_frames_and_collections(self):
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_hint": "main document",
                    "frame_path": [],
                    "elements": [{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
                    "collections": [],
                },
                {
                    "frame_hint": "iframe title=results",
                    "frame_path": ["iframe[title='results']"],
                    "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Result A"}],
                    "collections": [{"kind": "search_results", "item_count": 2}],
                },
            ],
        }

        content = ASSISTANT_MODULE.RPAReActAgent._build_observation(snapshot, 0)

        self.assertIn("Frame: main document", content)
        self.assertIn("Frame: iframe title=results", content)
        self.assertIn("Collection: search_results (2 items)", content)

    async def test_react_agent_build_observation_lists_snapshot_v2_containers(self):
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [
                {
                    "container_id": "table-1",
                    "frame_path": [],
                    "container_kind": "table",
                    "name": "合同列表",
                    "summary": "合同下载列表",
                    "child_actionable_ids": ["a-1", "a-2"],
                    "child_content_ids": ["c-1", "c-2"],
                }
            ],
        }

        content = ASSISTANT_MODULE.RPAReActAgent._build_observation(snapshot, 0)

        self.assertIn("Container: table 合同列表", content)
        self.assertIn("actionable=2", content)
        self.assertIn("content=2", content)

    async def test_react_agent_executes_structured_collection_action_with_frame_context(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_path": ["iframe[title='results']"],
                    "frame_hint": "iframe title=results",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": ["iframe[title='results']"],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Result A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Result B"},
                            ],
                        }
                    ],
                }
            ],
        }
        responses = [
            json.dumps(
                {
                    "thought": "click the first item",
                    "action": "execute",
                    "operation": "click",
                    "description": "点击列表中的第一个项目",
                    "target_hint": {"role": "link", "name": "item"},
                    "collection_hint": {"kind": "search_results"},
                    "ordinal": "first",
                    "risk": "none",
                    "risk_reason": "",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "done",
                    "description": "done",
                    "risk": "none",
                    "risk_reason": "",
                },
                ensure_ascii=False,
            ),
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=page,
                goal="点击列表中的第一个项目",
                existing_steps=[],
            ):
                events.append(event)

        step_done = next(event for event in events if event["event"] == "agent_step_done")
        self.assertEqual(page.scope.locator_calls[0], "frame:iframe[title='results']")
        self.assertEqual(
            json.loads(step_done["data"]["step"]["target"]),
            {
                "method": "collection_item",
                "collection": {"method": "css", "value": "main article.card"},
                "ordinal": "first",
                "item": {"method": "css", "value": "h2 a"},
            },
        )

class RPAAssistantFrameAwareSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_page_snapshot_v2_includes_actionable_content_and_containers(self):
        main = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
        )
        page = _FakeSnapshotPage(main)

        with patch.object(
            ASSISTANT_RUNTIME_MODULE,
            "_extract_frame_snapshot_v2",
            new=AsyncMock(
                return_value={
                    "actionable_nodes": [
                        {
                            "node_id": "act-1",
                            "frame_path": [],
                            "container_id": "table-1",
                            "role": "link",
                            "name": "ContractList20260411124156",
                            "action_kinds": ["click"],
                            "locator": {"method": "role", "role": "link", "name": "ContractList20260411124156"},
                            "locator_candidates": [
                                {
                                    "kind": "role",
                                    "selected": True,
                                    "locator": {
                                        "method": "role",
                                        "role": "link",
                                        "name": "ContractList20260411124156",
                                    },
                                }
                            ],
                            "validation": {"status": "ok"},
                            "bbox": {"x": 10, "y": 20, "width": 120, "height": 24},
                            "center_point": {"x": 70, "y": 32},
                            "is_visible": True,
                            "is_enabled": True,
                            "hit_test_ok": True,
                            "element_snapshot": {"tag": "a", "text": "ContractList20260411124156"},
                        }
                    ],
                    "content_nodes": [
                        {
                            "node_id": "content-1",
                            "frame_path": [],
                            "container_id": "table-1",
                            "semantic_kind": "cell",
                            "text": "已归档",
                            "bbox": {"x": 300, "y": 20, "width": 80, "height": 24},
                            "locator": {"method": "text", "value": "已归档"},
                            "element_snapshot": {"tag": "td", "text": "已归档"},
                        }
                    ],
                    "containers": [
                        {
                            "container_id": "table-1",
                            "frame_path": [],
                            "container_kind": "table",
                            "name": "合同列表",
                            "bbox": {"x": 0, "y": 0, "width": 800, "height": 600},
                            "summary": "合同下载列表",
                            "child_actionable_ids": ["act-1"],
                            "child_content_ids": ["content-1"],
                        }
                    ],
                }
            ),
        ):
            snapshot = await ASSISTANT_MODULE.build_page_snapshot(
                page,
                frame_path_builder=lambda frame: frame._frame_path,
            )

        self.assertIn("actionable_nodes", snapshot)
        self.assertIn("content_nodes", snapshot)
        self.assertIn("containers", snapshot)
        self.assertEqual(snapshot["actionable_nodes"][0]["locator"]["method"], "role")
        self.assertEqual(snapshot["content_nodes"][0]["semantic_kind"], "cell")
        self.assertEqual(snapshot["containers"][0]["container_kind"], "table")

    async def test_build_page_snapshot_includes_iframe_elements_and_collections(self):
        iframe = _FakeSnapshotFrame(
            name="editor",
            url="https://example.com/editor",
            frame_path=["iframe[title='editor']"],
            elements=[
                {"index": 1, "tag": "a", "role": "link", "name": "Quarterly Report"},
                {"index": 2, "tag": "a", "role": "link", "name": "Annual Report"},
            ],
        )
        main = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
            child_frames=[iframe],
        )
        page = _FakeSnapshotPage(main)

        snapshot = await ASSISTANT_MODULE.build_page_snapshot(
            page,
            frame_path_builder=lambda frame: frame._frame_path,
        )

        self.assertEqual(snapshot["title"], "Example")
        self.assertEqual(len(snapshot["frames"]), 2)
        self.assertEqual(snapshot["frames"][1]["frame_path"], ["iframe[title='editor']"])
        self.assertEqual(snapshot["frames"][1]["elements"][0]["name"], "Quarterly Report")
        self.assertEqual(snapshot["frames"][1]["collections"][0]["item_count"], 2)

    async def test_build_page_snapshot_skips_detached_child_frame(self):
        detached = _FakeSnapshotFrame(
            name="detached",
            url="https://example.com/detached",
            frame_path=["iframe[title='detached']"],
            elements=[{"index": 1, "tag": "a", "role": "link", "name": "Detached Link"}],
        )
        main = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
            child_frames=[detached],
        )
        page = _FakeSnapshotPage(main)

        async def flaky_frame_path_builder(frame):
            if frame is detached:
                raise RuntimeError("Frame.frame_element: Frame has been detached.")
            return frame._frame_path

        snapshot = await ASSISTANT_MODULE.build_page_snapshot(
            page,
            frame_path_builder=flaky_frame_path_builder,
        )

        self.assertEqual(len(snapshot["frames"]), 1)
        self.assertEqual(snapshot["frames"][0]["frame_path"], [])

    async def test_detect_collections_builds_structured_template_from_repeated_context(self):
        collections = ASSISTANT_RUNTIME_MODULE._detect_collections(
            [
                {"index": 1, "tag": "a", "role": "link", "name": "Skip to content", "href": "#start-of-content"},
                {
                    "index": 2,
                    "tag": "a",
                    "role": "link",
                    "name": "Item A",
                    "collection_container_selector": "main article.card",
                    "collection_item_selector": "h2 a",
                },
                {
                    "index": 3,
                    "tag": "a",
                    "role": "link",
                    "name": "Item B",
                    "collection_container_selector": "main article.card",
                    "collection_item_selector": "h2 a",
                },
            ],
            [],
        )

        self.assertGreaterEqual(len(collections), 1)
        self.assertEqual(collections[0]["kind"], "repeated_items")
        self.assertEqual(collections[0]["container_hint"]["locator"], {"method": "css", "value": "main article.card"})
        self.assertEqual(collections[0]["item_hint"]["locator"], {"method": "css", "value": "h2 a"})
        self.assertEqual(collections[0]["items"][0]["name"], "Item A")
        self.assertEqual(collections[0]["items"][1]["name"], "Item B")

    async def test_pick_first_item_uses_collection_scope_not_global_page_order(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "elements": [{"name": "Sidebar Link", "role": "link"}],
                    "collections": [],
                },
                {
                    "frame_path": ["iframe[title='results']"],
                    "elements": [],
                    "collections": [
                        {
                            "kind": "search_results",
                            "frame_path": ["iframe[title='results']"],
                            "container_hint": {"role": "list"},
                            "item_hint": {"role": "link"},
                            "items": [
                                {"name": "Result A", "role": "link"},
                                {"name": "Result B", "role": "link"},
                            ],
                        }
                    ],
                },
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_collection_target(
            snapshot,
            {"action": "click", "ordinal": "first"},
        )

        self.assertEqual(resolved["frame_path"], ["iframe[title='results']"])
        self.assertEqual(resolved["resolved_target"]["name"], "Result A")

    async def test_sort_nodes_by_visual_position_orders_top_to_bottom_then_left_to_right(self):
        nodes = [
            {"node_id": "download-2", "name": "文件二", "bbox": {"x": 40, "y": 60, "width": 80, "height": 20}},
            {"node_id": "download-1", "name": "文件一", "bbox": {"x": 20, "y": 20, "width": 80, "height": 20}},
            {"node_id": "download-3", "name": "文件三", "bbox": {"x": 100, "y": 20, "width": 80, "height": 20}},
        ]

        ordered = ASSISTANT_RUNTIME_MODULE._sort_nodes_by_visual_position(nodes)

        self.assertEqual([node["name"] for node in ordered], ["文件一", "文件三", "文件二"])


class RPAAssistantStructuredExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_structured_intent_uses_bbox_order_for_first_match_in_single_pass(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "download-1",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260411124156",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "ContractList20260411124156"},
                    "locator_candidates": [{"kind": "text", "selected": True, "locator": {"method": "text", "value": "ContractList20260411124156"}}],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "is_visible": True,
                    "is_enabled": True,
                    "bbox": {"x": 20, "y": 20, "width": 80, "height": 20},
                },
                {
                    "node_id": "download-2",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260411124157",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "ContractList20260411124157"},
                    "locator_candidates": [{"kind": "text", "selected": True, "locator": {"method": "text", "value": "ContractList20260411124157"}}],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "is_visible": True,
                    "is_enabled": True,
                    "bbox": {"x": 20, "y": 60, "width": 80, "height": 20},
                },
            ],
            "content_nodes": [],
            "containers": [
                {
                    "container_id": "table-1",
                    "frame_path": [],
                    "container_kind": "table",
                    "name": "合同列表",
                    "bbox": {"x": 0, "y": 0, "width": 800, "height": 600},
                    "summary": "合同下载列表",
                    "child_actionable_ids": ["download-1", "download-2"],
                    "child_content_ids": [],
                }
            ],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击第一个文件下载",
                "prompt": "点击第一个文件下载",
                "target_hint": {"role": "link", "name": "contractlist"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["value"], "ContractList20260411124156")
        self.assertEqual(resolved["resolved"]["ordinal"], "first")
        self.assertNotIn("assistant_diagnostics", resolved["resolved"])

    async def test_resolve_structured_intent_prefers_snapshot_locator_bundle_for_actionable_node(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "download-1",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260411124156",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "ContractList20260411124156"},
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": False,
                            "locator": {"method": "role", "role": "link", "name": "ContractList20260411124156"},
                        },
                        {
                            "kind": "text",
                            "selected": True,
                            "locator": {"method": "text", "value": "ContractList20260411124156"},
                        },
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                }
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击第一个文件下载",
                "target_hint": {"role": "link", "name": "contractlist"},
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "text")
        self.assertTrue(resolved["resolved"]["locator_candidates"][1]["selected"])

    async def test_resolve_structured_intent_extract_text_prefers_content_nodes(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "button-1",
                    "frame_path": [],
                    "container_id": "card-1",
                    "role": "button",
                    "name": "复制标题",
                    "action_kinds": ["click"],
                    "locator": {"method": "role", "role": "button", "name": "复制标题"},
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "button", "name": "复制标题"},
                        }
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                }
            ],
            "content_nodes": [
                {
                    "node_id": "title-1",
                    "frame_path": [],
                    "container_id": "card-1",
                    "semantic_kind": "heading",
                    "role": "heading",
                    "text": "Quarterly Report",
                    "bbox": {"x": 20, "y": 20, "width": 200, "height": 24},
                    "locator": {"method": "text", "value": "Quarterly Report"},
                    "element_snapshot": {"tag": "h2", "text": "Quarterly Report"},
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
                "target_hint": {"name": "report title"},
                "result_key": "report_title",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "text")
        self.assertEqual(resolved["resolved"]["content_node"]["semantic_kind"], "heading")

    async def test_execute_structured_extract_text_parses_field_value_from_content_node(self):
        page = _FakeActionPage()
        intent = {
            "action": "extract_text",
            "description": "获取购买人字段值",
            "prompt": "帮我获取购买人",
            "result_key": "buyer_name",
            "target_hint": {"role": "field", "name": "购买人"},
            "resolved": {
                "frame_path": [],
                "locator": {"method": "text", "value": "购买人李雨晨"},
                "locator_candidates": [],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "text",
                "content_node": {
                    "node_id": "content-1",
                    "frame_path": [],
                    "container_id": "field-1",
                    "semantic_kind": "field",
                    "role": "",
                    "text": "购买人李雨晨",
                    "bbox": {"x": 20, "y": 20, "width": 200, "height": 24},
                    "locator": {"method": "text", "value": "购买人李雨晨"},
                    "element_snapshot": {"tag": "div", "text": "购买人李雨晨"},
                },
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "李雨晨")
        self.assertEqual(page.scope.locator_calls, [])

    async def test_execute_structured_click_does_not_mark_local_expansion_in_single_pass_mode(self):
        page = _FakeActionPage()
        intent = {
            "action": "click",
            "description": "点击第一个文件下载",
            "prompt": "点击第一个文件下载",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "text", "value": "ContractList20260411124156"},
                "locator_candidates": [
                    {
                        "kind": "text",
                        "selected": True,
                        "locator": {"method": "text", "value": "ContractList20260411124156"},
                    }
                ],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": "first",
                "selected_locator_kind": "text",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.scope.locator_calls[0], "text:ContractList20260411124156")
        self.assertNotIn("used_local_expansion", result["step"]["assistant_diagnostics"])

    async def test_execute_structured_click_uses_frame_locator_chain(self):
        page = _FakeActionPage()
        intent = {
            "action": "click",
            "description": "点击发送按钮",
            "prompt": "点击发送按钮",
            "resolved": {
                "frame_path": ["iframe[title='editor']"],
                "locator": {"method": "role", "role": "button", "name": "Send"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Send"},
                    }
                ],
                "selected_locator_kind": "role",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.scope.locator_calls[0], "frame:iframe[title='editor']")
        self.assertEqual(result["step"]["frame_path"], ["iframe[title='editor']"])
        self.assertEqual(result["step"]["source"], "ai")
        self.assertEqual(
            result["step"]["target"],
            '{"method": "role", "role": "button", "name": "Send"}',
        )

    async def test_execute_structured_click_persists_adaptive_collection_target_for_first_collection_item(self):
        page = _FakeActionPage()
        intent = {
            "action": "click",
            "description": "点击第一个卡片项目",
            "prompt": "点击列表中的第一个项目",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "role", "role": "link", "name": "Item A"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "link", "name": "Item A"},
                    }
                ],
                "collection_hint": {
                    "kind": "repeated_items",
                    "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                },
                "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                "ordinal": "first",
                "selected_locator_kind": "role",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(
            json.loads(result["step"]["target"]),
            {
                "method": "collection_item",
                "collection": {"method": "css", "value": "main article.card"},
                "ordinal": "first",
                "item": {"method": "css", "value": "h2 a"},
            },
        )
        self.assertEqual(result["step"]["collection_hint"]["kind"], "repeated_items")
        self.assertEqual(result["step"]["item_hint"]["locator"], {"method": "css", "value": "h2 a"})
        self.assertEqual(result["step"]["ordinal"], "first")

    async def test_execute_structured_navigate_uses_page_goto(self):
        page = _FakeActionPage()
        intent = {
            "action": "navigate",
            "description": "打开 GitHub Trending 页面",
            "prompt": "打开 GitHub Trending 页面",
            "value": "https://github.com/trending",
            "resolved": {
                "frame_path": [],
                "locator": None,
                "locator_candidates": [],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "navigate",
                "url": "https://github.com/trending",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.goto_calls, ["https://github.com/trending"])
        self.assertEqual(page.load_state_calls, ["domcontentloaded"])
        self.assertEqual(result["step"]["action"], "navigate")
        self.assertEqual(result["step"]["url"], "https://github.com/trending")

    async def test_execute_structured_extract_text_persists_result_key(self):
        page = _FakeActionPage()
        intent = {
            "action": "extract_text",
            "description": "提取最近一条 issue 的标题",
            "prompt": "提取最近一条 issue 的标题",
            "result_key": "latest_issue_title",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "role", "role": "link", "name": "Issue Title"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "link", "name": "Issue Title"},
                    }
                ],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "role",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "Resolved text")
        self.assertEqual(result["step"]["action"], "extract_text")
        self.assertEqual(result["step"]["result_key"], "latest_issue_title")

    async def test_resolve_structured_intent_prefers_collection_item_inside_iframe(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Sidebar"}],
                    "collections": [],
                },
                {
                    "frame_path": ["iframe[title='results']"],
                    "frame_hint": "iframe title=results",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "search_results",
                            "frame_path": ["iframe[title='results']"],
                            "container_hint": {"role": "list"},
                            "item_hint": {"role": "link"},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Result A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Result B"},
                            ],
                        }
                    ],
                },
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击第一个结果",
                "collection_hint": {"kind": "search_results"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["frame_path"], ["iframe[title='results']"])
        self.assertEqual(resolved["resolved"]["locator"]["method"], "role")
        self.assertEqual(resolved["resolved"]["locator"]["name"], "Result A")

    async def test_resolve_structured_intent_prefers_structured_collection_over_flat_links(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [
                        {"index": 1, "tag": "a", "role": "link", "name": "Skip to content", "href": "#start-of-content"},
                        {"index": 2, "tag": "a", "role": "link", "name": "Homepage", "href": "/"},
                        {"index": 3, "tag": "a", "role": "link", "name": "Item A"},
                        {"index": 4, "tag": "a", "role": "link", "name": "Item B"},
                    ],
                    "collections": [
                        {
                            "kind": "search_results",
                            "frame_path": [],
                            "container_hint": {"role": "list"},
                            "item_hint": {"role": "link"},
                            "item_count": 4,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Skip to content", "href": "#start-of-content"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Homepage", "href": "/"},
                                {"index": 3, "tag": "a", "role": "link", "name": "Item A"},
                                {"index": 4, "tag": "a", "role": "link", "name": "Item B"},
                            ],
                        },
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 3, "tag": "a", "role": "link", "name": "Item A"},
                                {"index": 4, "tag": "a", "role": "link", "name": "Item B"},
                            ],
                        },
                    ],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击列表中的第一个项目",
                "prompt": "点击列表中的第一个项目",
                "target_hint": {"role": "link", "name": "item"},
                "collection_hint": {"kind": "search_results"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["name"], "Item A")
        self.assertEqual(resolved["resolved"]["collection_hint"]["kind"], "repeated_items")

    async def test_resolve_structured_intent_normalizes_first_ordinal_from_prompt(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Item A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Item B"},
                            ],
                        },
                    ],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击列表中的第一个项目",
                "prompt": "点击列表中的第一个项目",
                "target_hint": {"role": "link", "name": "item"},
                "collection_hint": {"kind": "search_results"},
                "ordinal": "25",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["name"], "Item A")
        self.assertEqual(resolved["resolved"]["ordinal"], "first")

    async def test_resolve_structured_intent_falls_back_to_direct_target_when_collection_hint_has_no_match(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [
                        {"index": 1, "tag": "input", "role": "textbox", "name": "Search", "placeholder": "Search"}
                    ],
                    "collections": [],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "fill",
                "description": "在搜索框中输入关键词",
                "prompt": "在搜索框中输入关键词",
                "target_hint": {"role": "textbox", "name": "search"},
                "collection_hint": {"kind": "cards"},
                "ordinal": "1",
                "value": "github",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "role")
        self.assertEqual(resolved["resolved"]["locator"]["name"], "Search")
        self.assertEqual(resolved["resolved"]["collection_hint"], {})

    async def test_resolve_structured_intent_prefers_primary_collection_items_over_repeated_controls(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "div.actions a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Star project A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Star project B"},
                            ],
                        },
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 3, "tag": "a", "role": "link", "name": "Project A"},
                                {"index": 4, "tag": "a", "role": "link", "name": "Project B"},
                            ],
                        },
                    ],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击列表中的第一个项目链接",
                "prompt": "点击列表中的第一个项目",
                "target_hint": {"role": "link", "name": "project title link"},
                "collection_hint": {"kind": "search_results"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["name"], "Project A")
        self.assertEqual(
            resolved["resolved"]["item_hint"]["locator"],
            {"method": "css", "value": "h2 a"},
        )


class RPAAssistantContextPromotionTests(unittest.IsolatedAsyncioTestCase):
    """Tests for the context_reads / context_writes promotion contract.

    These tests verify that the assistant's event stream surfaces context
    information so that callers (e.g. the RPA session manager) can persist
    extracted values and read previously stored ones.

    They are expected to FAIL until the assistant implementation is updated
    to include ``context_writes`` and ``context_reads`` lists in the
    ``result`` event payload.
    """

    async def test_assistant_promotes_explicit_extraction_to_context(self):
        """When the user asks to extract something for later use, the result
        event must include ``context_writes`` containing the extracted key."""
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()

        # Simulated LLM response: a structured extract_text intent with a
        # result_key that signals cross-page data transfer.
        llm_response = json.dumps({
            "thought": "User wants to extract the person name for later use",
            "action": "extract_text",
            "description": "提取当前人物姓名",
            "prompt": "提取当前人物姓名，后面要填写到另一个页面",
            "result_key": "person_name",
            "target_hint": {"role": "heading", "name": "person name"},
            "risk": "none",
            "risk_reason": "",
        })

        snapshot = {
            "url": "https://example.com/profile",
            "title": "Profile Page",
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [
                        {"index": 1, "tag": "h1", "role": "heading", "name": "张三"},
                    ],
                    "collections": [],
                },
            ],
            "actionable_nodes": [
                {
                    "node_id": "h-1",
                    "frame_path": [],
                    "role": "heading",
                    "name": "张三",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "张三"},
                    "locator_candidates": [
                        {"kind": "text", "selected": True, "locator": {"method": "text", "value": "张三"}},
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "is_visible": True,
                    "is_enabled": True,
                    "bbox": {"x": 20, "y": 20, "width": 80, "height": 24},
                },
            ],
            "content_nodes": [
                {
                    "node_id": "c-1",
                    "frame_path": [],
                    "semantic_kind": "heading",
                    "text": "张三",
                    "bbox": {"x": 20, "y": 20, "width": 80, "height": 24},
                    "locator": {"method": "text", "value": "张三"},
                },
            ],
            "containers": [],
        }

        async def fake_stream(_messages, _model_config=None):
            yield llm_response

        assistant._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            events = []
            async for event in assistant.chat(
                session_id="ctx-test-1",
                page=page,
                message="提取当前人物姓名，后面要填写到另一个页面",
                steps=[],
            ):
                events.append(event)

        result_events = [e for e in events if e["event"] == "result"]
        self.assertTrue(len(result_events) > 0, "Expected at least one result event")

        result_data = result_events[-1]["data"]
        self.assertIn("context_writes", result_data,
                       "result event must include context_writes list")
        self.assertEqual(
            result_data["context_writes"],
            [],
            "extract_text must never promote context writes",
        )

    async def test_assistant_promotes_runtime_required_cross_page_value(self):
        """When the user describes cross-page data transfer (e.g. copying a
        number from a detail page to a registration page), the result event
        must include ``context_writes`` containing the relevant key."""
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()

        llm_response = json.dumps({
            "thought": "User needs to extract the contract number for filling into another page",
            "action": "extract_text",
            "description": "提取合同编号",
            "prompt": "打开详情后把编号填写到登记页",
            "result_key": "contract_number",
            "target_hint": {"role": "cell", "name": "合同编号"},
            "risk": "none",
            "risk_reason": "",
        })

        snapshot = {
            "url": "https://example.com/contract/detail",
            "title": "Contract Detail",
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [
                        {"index": 1, "tag": "td", "role": "cell", "name": "合同编号"},
                        {"index": 2, "tag": "td", "role": "cell", "name": "HT-2026-001"},
                    ],
                    "collections": [],
                },
            ],
            "actionable_nodes": [],
            "content_nodes": [
                {
                    "node_id": "c-1",
                    "frame_path": [],
                    "semantic_kind": "cell",
                    "text": "HT-2026-001",
                    "bbox": {"x": 100, "y": 40, "width": 120, "height": 24},
                    "locator": {"method": "text", "value": "HT-2026-001"},
                },
            ],
            "containers": [],
        }

        async def fake_stream(_messages, _model_config=None):
            yield llm_response

        assistant._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            events = []
            async for event in assistant.chat(
                session_id="ctx-test-2",
                page=page,
                message="打开详情后把编号填写到登记页",
                steps=[],
            ):
                events.append(event)

        result_events = [e for e in events if e["event"] == "result"]
        self.assertTrue(len(result_events) > 0, "Expected at least one result event")

        result_data = result_events[-1]["data"]
        self.assertIn("context_writes", result_data,
                       "result event must include context_writes list for cross-page transfer")
        self.assertEqual(
            result_data["context_writes"],
            [],
            "extract_text must never promote cross-page context writes",
        )

    async def test_assistant_ignores_nonessential_observations(self):
        """When the user gives a simple continuation command (e.g. '继续下一步'),
        no extraneous context keys should appear in context_writes."""
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()

        # Simulated LLM response: a simple click, no extraction intent.
        llm_response = json.dumps({
            "thought": "User wants to proceed to next step",
            "action": "click",
            "description": "点击下一步按钮",
            "prompt": "继续下一步",
            "target_hint": {"role": "button", "name": "Next"},
            "risk": "none",
            "risk_reason": "",
        })

        snapshot = {
            "url": "https://example.com/wizard",
            "title": "Wizard",
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [
                        {"index": 1, "tag": "button", "role": "button", "name": "Next"},
                        {"index": 2, "tag": "div", "role": "complementary", "name": "Sidebar"},
                    ],
                    "collections": [],
                },
            ],
            "actionable_nodes": [
                {
                    "node_id": "btn-1",
                    "frame_path": [],
                    "role": "button",
                    "name": "Next",
                    "action_kinds": ["click"],
                    "locator": {"method": "role", "role": "button", "name": "Next"},
                    "locator_candidates": [
                        {"kind": "role", "selected": True, "locator": {"method": "role", "role": "button", "name": "Next"}},
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "is_visible": True,
                    "is_enabled": True,
                    "bbox": {"x": 300, "y": 500, "width": 100, "height": 40},
                },
            ],
            "content_nodes": [],
            "containers": [],
        }

        async def fake_stream(_messages, _model_config=None):
            yield llm_response

        assistant._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            events = []
            async for event in assistant.chat(
                session_id="ctx-test-3",
                page=page,
                message="继续下一步",
                steps=[],
            ):
                events.append(event)

        result_events = [e for e in events if e["event"] == "result"]
        self.assertTrue(len(result_events) > 0, "Expected at least one result event")

        result_data = result_events[-1]["data"]
        self.assertIn("context_writes", result_data,
                       "result event must include context_writes list (even if empty)")
        # No extraneous keys like "sidebar_hint" should be present.
        context_writes = result_data["context_writes"]
        self.assertIsInstance(context_writes, list)
        self.assertNotIn("sidebar_hint", context_writes,
                          "context_writes should not contain nonessential keys like 'sidebar_hint'")
        # For a simple click with no extraction, the list should be empty.
        self.assertEqual(context_writes, [],
                          "context_writes should be empty for a simple continuation command")


class RPAStepContractTests(unittest.TestCase):
    def test_rpa_step_exposes_new_contract_fields(self):
        step = MANAGER_MODULE.RPAStep(id="step-1", action="ai_script")

        self.assertEqual(step.output_schema, {})
        self.assertEqual(step.output_payload, {})
        self.assertEqual(step.context_bindings, [])
        self.assertEqual(step.attempt_summary, {})
        self.assertEqual(step.extraction_source, {})


class RPAAssistantReviewFixTests(unittest.TestCase):
    def test_compute_context_writes_never_promotes_extract_text(self):
        writes = ASSISTANT_MODULE.RPAAssistant._compute_context_writes(
            "读取 buyer",
            {
                "action": "extract_text",
                "result_key": "buyer",
                "description": "读取 buyer",
                "prompt": "读取 buyer",
            },
            None,
        )

        self.assertEqual(writes, [])

    def test_ai_script_missing_output_payload_binding_raises_contract_error(self):
        with self.assertRaises(ValueError) as ctx:
            ASSISTANT_MODULE.RPAAssistant._compute_context_writes(
                "提取 buyer",
                {
                    "action": "ai_script",
                    "output_payload": {"buyer": "李雨晨"},
                    "context_bindings": ["buyer", "department"],
                    "description": "提取 buyer",
                    "prompt": "提取 buyer",
                },
                None,
            )

        self.assertIn("contract_error", str(ctx.exception))
        self.assertIn("department", str(ctx.exception))

    def test_ai_script_without_context_bindings_does_not_write_context(self):
        writes = ASSISTANT_MODULE.RPAAssistant._compute_context_writes(
            "提取 buyer",
            {
                "action": "ai_script",
                "output_payload": {"buyer": "李雨晨"},
                "description": "提取 buyer",
                "prompt": "提取 buyer",
            },
            None,
        )

        self.assertEqual(writes, [])

    def test_ai_script_promotes_to_ledger_without_cross_page_heuristics(self):
        ledger = SimpleNamespace(should_promote_value=lambda **_: False)
        session = SimpleNamespace(context_ledger=ledger)
        manager = SimpleNamespace(
            sessions={"session-1": session},
            record_context_value=unittest.mock.Mock(),
        )

        ASSISTANT_MODULE.RPAAssistant._promote_to_ledger(
            rpa_manager=manager,
            session_id="session-1",
            context_writes=["buyer"],
            step_data={
                "action": "ai_script",
                "prompt": "提取 buyer",
                "description": "提取 buyer",
                "output_payload": {"buyer": "李雨晨"},
            },
            output=None,
        )

        manager.record_context_value.assert_called_once()
        call_kwargs = manager.record_context_value.call_args.kwargs
        self.assertEqual(call_kwargs["key"], "buyer")
        self.assertEqual(call_kwargs["value"], "李雨晨")

    def test_context_write_entries_use_structured_payload_values(self):
        entries = ASSISTANT_MODULE.RPAAssistant._build_context_write_entries(
            ["buyer", "department"],
            {"buyer": "李雨晨", "department": "研发效能组"},
            "RAW_OUTPUT_SHOULD_NOT_LEAK",
        )

        self.assertEqual(
            entries,
            [
                {"key": "buyer", "value": "李雨晨"},
                {"key": "department", "value": "研发效能组"},
            ],
        )

    def test_resolve_context_in_intent_preserves_falsy_values(self):
        ledger = SimpleNamespace(
            observed_values={"empty_text": "", "zero_value": 0, "false_value": False},
            derived_values={"missing": "fallback"},
        )
        assistant = ASSISTANT_MODULE.RPAAssistant()
        intent = {
            "action": "fill",
            "description": "fill from context",
            "value": "${empty_text}-${zero_value}-${false_value}",
        }

        reads = assistant._resolve_context_in_intent(intent, ledger)

        self.assertEqual(reads, ["empty_text", "zero_value", "false_value"])
        self.assertEqual(intent["value"], "-0-False")

    def test_empty_step_payload_dicts_do_not_block_json_recovery(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        step_data = {
            "output_payload": {},
            "output_schema": {},
        }
        raw_output = json.dumps({"buyer": "李雨晨"}, ensure_ascii=False)

        output_payload = assistant._normalize_output_payload(step_data, raw_output)
        output_schema = assistant._normalize_output_schema(step_data, output_payload)

        self.assertEqual(output_payload, {"buyer": "李雨晨"})
        self.assertEqual(output_schema, {"buyer": "string"})


class RPAAssistantAttemptEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_executes_ai_script_prefers_runtime_payload_over_envelope_payload(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()
        session_id = "ai-script-real-path"
        MANAGER_MODULE.rpa_manager.sessions[session_id] = SimpleNamespace(
            context_ledger=MANAGER_MODULE.TaskContextLedger(),
            task_context_id=None,
        )
        self.addCleanup(lambda: MANAGER_MODULE.rpa_manager.sessions.pop(session_id, None))

        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [],
                }
            ],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }

        code = "async def run(page):\n    return " + repr(json.dumps({"buyer": "runtime"}, ensure_ascii=False))
        ai_script_response = json.dumps(
            {
                "action": "ai_script",
                "description": "提取 buyer",
                "prompt": "提取 buyer",
                "code": code,
                "context_bindings": ["buyer"],
                "output_schema": {"buyer": "string"},
                "output_payload": {"buyer": "envelope"},
            },
            ensure_ascii=False,
        )

        async def fake_stream(_messages, _model_config=None):
            yield ai_script_response

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            assistant,
            "_stream_llm",
            fake_stream,
        ):
            events = []
            async for event in assistant.chat(
                session_id=session_id,
                page=page,
                message="提取 buyer",
                steps=[],
            ):
                events.append(event)

        result_event = next(event for event in events if event["event"] == "result")
        result_data = result_event["data"]

        self.assertEqual(result_data["context_writes"], ["buyer"])
        self.assertEqual(result_data["step"]["action"], "ai_script")
        self.assertEqual(result_data["step"]["context_bindings"], ["buyer"])
        self.assertEqual(result_data["step"]["output_payload"], {"buyer": "runtime"})
        self.assertEqual(result_data["step"]["output_schema"], {"buyer": "string"})

        ledger_entry = MANAGER_MODULE.rpa_manager.sessions[session_id].context_ledger.observed_values["buyer"]
        self.assertEqual(ledger_entry.value, "runtime")
        self.assertEqual(ledger_entry.source_kind, "assistant_extraction")

    async def test_execute_single_response_keeps_empty_runtime_payload_over_envelope_payload(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()

        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [],
                }
            ],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }

        code = "async def run(page):\n    return " + repr(json.dumps({}, ensure_ascii=False))
        ai_script_response = json.dumps(
            {
                "action": "ai_script",
                "description": "提取 buyer",
                "prompt": "提取 buyer",
                "code": code,
                "context_bindings": ["buyer"],
                "output_schema": {"buyer": "string"},
                "output_payload": {"buyer": "envelope"},
            },
            ensure_ascii=False,
        )

        async def fake_stream(_messages, _model_config=None):
            yield ai_script_response

        with patch.object(
            assistant,
            "_stream_llm",
            fake_stream,
        ):
            result, code, resolution, context_reads = await assistant._execute_single_response(
                current_page=page,
                snapshot=snapshot,
                full_response=ai_script_response,
                context_ledger=MANAGER_MODULE.TaskContextLedger(),
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["step"]["output_payload"], {})
        self.assertEqual(result["step"]["context_bindings"], ["buyer"])
        self.assertEqual(context_reads, [])
        self.assertIsNone(resolution)

        with self.assertRaises(ValueError) as ctx:
            assistant._compute_context_writes(
                "提取 buyer",
                result["step"],
                None,
            )

        self.assertIn("contract_error", str(ctx.exception))
        self.assertIn("buyer", str(ctx.exception))

    async def test_execute_single_response_rejects_plain_text_runtime_payload_for_structured_context(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()

        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [],
                }
            ],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }

        code = "async def run(page):\n    return 'plain text result'"
        ai_script_response = json.dumps(
            {
                "action": "ai_script",
                "description": "提取 buyer",
                "prompt": "提取 buyer",
                "code": code,
                "context_bindings": ["buyer"],
                "output_schema": {"buyer": "string"},
                "output_payload": {"buyer": "envelope"},
            },
            ensure_ascii=False,
        )

        async def fake_execute_on_page(_page, _code):
            return {"success": True, "error": None, "output": "plain text result"}

        with patch.object(
            assistant,
            "_execute_on_page",
            fake_execute_on_page,
        ):
            result, _code, resolution, context_reads = await assistant._execute_single_response(
                current_page=page,
                snapshot=snapshot,
                full_response=ai_script_response,
                context_ledger=MANAGER_MODULE.TaskContextLedger(),
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["step"]["output_payload"], {})
        self.assertEqual(result["step"]["context_bindings"], ["buyer"])
        self.assertEqual(context_reads, [])
        self.assertIsNone(resolution)

        with self.assertRaises(ValueError) as ctx:
            assistant._compute_context_writes(
                "提取 buyer",
                result["step"],
                None,
            )

        self.assertIn("contract_error", str(ctx.exception))
        self.assertIn("buyer", str(ctx.exception))

    async def test_chat_emits_attempt_events_and_recovered_after_retry_status(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()

        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [],
                }
            ],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }

        success_step = {
            "action": "ai_script",
            "source": "ai",
            "description": "提取 buyer",
            "prompt": "提取 buyer",
            "output_schema": {"buyer": "string"},
            "output_payload": {"buyer": "李雨晨"},
            "context_bindings": ["buyer"],
            "attempt_summary": {},
        }

        execution_results = [
            (
                {"success": False, "error": "boom", "output": ""},
                None,
                {
                    "action": "ai_script",
                    "description": "提取 buyer",
                    "output_schema": {"buyer": "string"},
                    "context_bindings": ["buyer"],
                },
                [],
            ),
            (
                {"success": True, "error": None, "output": json.dumps({"buyer": "李雨晨"}, ensure_ascii=False), "step": success_step},
                None,
                {
                    "action": "ai_script",
                    "description": "提取 buyer",
                    "output_schema": {"buyer": "string"},
                    "output_payload": {"buyer": "李雨晨"},
                    "context_bindings": ["buyer"],
                },
                [],
            ),
        ]

        async def fake_execute_single_response(*_args, **_kwargs):
            return execution_results.pop(0)

        async def fake_stream(_messages, _model_config=None):
            yield json.dumps(
                {
                    "action": "ai_script",
                    "description": "提取 buyer",
                    "prompt": "提取 buyer",
                    "output_schema": {"buyer": "string"},
                    "context_bindings": ["buyer"],
                    "thought": "retry",
                },
                ensure_ascii=False,
            )

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            assistant,
            "_execute_single_response",
            side_effect=fake_execute_single_response,
        ), patch.object(
            assistant,
            "_stream_llm",
            fake_stream,
        ):
            events = []
            async for event in assistant.chat(
                session_id="attempt-test-1",
                page=page,
                message="提取 buyer",
                steps=[],
            ):
                events.append(event)

        event_types = [event["event"] for event in events if event["event"] in {
            "attempt_started",
            "attempt_output",
            "attempt_failed",
            "attempt_succeeded",
            "result",
        }]
        self.assertEqual(
            event_types,
            [
                "attempt_started",
                "attempt_output",
                "attempt_failed",
                "attempt_started",
                "attempt_output",
                "attempt_succeeded",
                "result",
            ],
        )

        attempt_output_events = [event for event in events if event["event"] == "attempt_output"]
        self.assertEqual(len(attempt_output_events), 2)
        self.assertEqual(attempt_output_events[0]["data"]["attempt"], 1)
        self.assertEqual(attempt_output_events[0]["data"]["action"], "ai_script")
        self.assertEqual(attempt_output_events[0]["data"]["summary"], "提取 buyer")
        self.assertEqual(attempt_output_events[0]["data"]["expected_output_keys"], ["buyer"])
        self.assertEqual(attempt_output_events[1]["data"]["attempt"], 2)
        self.assertEqual(attempt_output_events[1]["data"]["expected_output_keys"], ["buyer"])

        attempt_succeeded_events = [event for event in events if event["event"] == "attempt_succeeded"]
        self.assertEqual(len(attempt_succeeded_events), 1)
        self.assertEqual(
            attempt_succeeded_events[0]["data"]["context_writes"],
            [{"key": "buyer", "value": "李雨晨"}],
        )

        result_event = next(event for event in events if event["event"] == "result")
        result_data = result_event["data"]

        self.assertEqual(result_data["status"], "recovered_after_retry")
        self.assertTrue(result_data["success"])
        self.assertEqual(result_data["step"]["output_schema"], {"buyer": "string"})
        self.assertEqual(result_data["step"]["output_payload"], {"buyer": "李雨晨"})
        self.assertEqual(result_data["step"]["context_bindings"], ["buyer"])
        self.assertEqual(result_data["step"]["attempt_summary"]["attempt_count"], 2)
        self.assertEqual(result_data["step"]["attempt_summary"]["final_status"], "recovered_after_retry")
        self.assertEqual(result_data["step"]["extraction_source"], {"kind": "ai_script", "action": "ai_script"})

    async def test_chat_emits_attempt_output_before_execution_error(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()

        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [],
                }
            ],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }

        async def fake_execute_single_response(*_args, **_kwargs):
            raise RuntimeError("boom")

        async def fake_stream(_messages, _model_config=None):
            yield json.dumps(
                {
                    "action": "ai_script",
                    "description": "提取 buyer",
                    "prompt": "提取 buyer",
                    "output_schema": {"buyer": "string"},
                    "context_bindings": ["buyer"],
                    "thought": "plan ready",
                },
                ensure_ascii=False,
            )

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            assistant,
            "_execute_single_response",
            side_effect=fake_execute_single_response,
        ), patch.object(
            assistant,
            "_stream_llm",
            fake_stream,
        ):
            events = []
            async for event in assistant.chat(
                session_id="attempt-test-2",
                page=page,
                message="提取 buyer",
                steps=[],
            ):
                events.append(event)

        event_types = [event["event"] for event in events if event["event"] in {
            "attempt_started",
            "attempt_output",
            "attempt_failed",
            "result",
        }]
        self.assertEqual(
            event_types[:3],
            ["attempt_started", "attempt_output", "attempt_failed"],
        )
        attempt_output_event = next(event for event in events if event["event"] == "attempt_output")
        self.assertEqual(attempt_output_event["data"]["summary"], "提取 buyer")
        self.assertEqual(attempt_output_event["data"]["expected_output_keys"], ["buyer"])

        result_event = next(event for event in events if event["event"] == "result")
        self.assertEqual(result_event["data"]["status"], "failed")

    async def test_chat_propagates_partial_success_status(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()

        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [],
                }
            ],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [],
        }

        partial_step = {
            "action": "ai_script",
            "source": "ai",
            "description": "提取 buyer",
            "prompt": "提取 buyer",
            "output_schema": {"buyer": "string", "department": "string"},
            "output_payload": {"buyer": "李雨晨"},
            "context_bindings": ["buyer"],
            "attempt_summary": {},
        }

        async def fake_execute_single_response(*_args, **_kwargs):
            return (
                {
                    "success": True,
                    "status": "partial_success",
                    "error": None,
                    "output": json.dumps({"buyer": "李雨晨"}, ensure_ascii=False),
                    "step": partial_step,
                },
                None,
                {
                    "action": "ai_script",
                    "description": "提取 buyer",
                    "output_schema": {"buyer": "string", "department": "string"},
                    "output_payload": {"buyer": "李雨晨"},
                    "context_bindings": ["buyer"],
                },
                [],
            )

        async def fake_stream(_messages, _model_config=None):
            yield "{\"thought\":\"done\"}"

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            assistant,
            "_execute_single_response",
            side_effect=fake_execute_single_response,
        ), patch.object(
            assistant,
            "_stream_llm",
            fake_stream,
        ):
            events = []
            async for event in assistant.chat(
                session_id="partial-test-1",
                page=page,
                message="提取 buyer",
                steps=[],
            ):
                events.append(event)

        result_event = next(event for event in events if event["event"] == "result")
        result_data = result_event["data"]

        self.assertEqual(result_data["status"], "partial_success")
        self.assertTrue(result_data["success"])
        self.assertEqual(result_data["context_writes"], ["buyer"])
        self.assertEqual(result_data["step"]["attempt_summary"]["final_status"], "partial_success")
        self.assertEqual(result_data["step"]["attempt_summary"]["attempt_count"], 1)
        self.assertEqual(result_data["step"]["output_payload"], {"buyer": "李雨晨"})
        self.assertEqual(result_data["step"]["output_schema"], {"buyer": "string", "department": "string"})
        self.assertEqual(result_data["step"]["extraction_source"], {"kind": "ai_script", "action": "ai_script"})


class RPAAssistantPromptFormattingTests(unittest.TestCase):
    def test_build_messages_lists_frames_and_collections(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        snapshot = {
            "frames": [
                {
                    "frame_hint": "main document",
                    "frame_path": [],
                    "elements": [{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
                    "collections": [],
                },
                {
                    "frame_hint": "iframe title=results",
                    "frame_path": ["iframe[title='results']"],
                    "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Result A"}],
                    "collections": [{"kind": "search_results", "item_count": 2}],
                },
            ]
        }

        messages = assistant._build_messages("点击第一个结果", [], snapshot, [])
        content = messages[-1]["content"]

        self.assertIn("Frame: main document", content)
        self.assertIn("Frame: iframe title=results", content)
        self.assertIn("Collection: search_results (2 items)", content)

    def test_system_prompt_separates_extract_text_from_context_producing_ai_script(self):
        prompt = ASSISTANT_MODULE.SYSTEM_PROMPT

        self.assertIn("extract_text is for temporary page reading only", prompt)
        self.assertIn("must not write context", prompt)
        self.assertIn("Any context-producing extraction", prompt)
        self.assertIn("must use ai_script / code path", prompt)
        self.assertIn("context_bindings", prompt)
        self.assertIn("output_payload", prompt)
        self.assertIn("Do not use result_key or result_keys for context writes.", prompt)
        self.assertNotIn("For extract_text actions, include result_key", prompt)
        self.assertNotIn("For extract_text actions, include result_key as a short ASCII snake_case key", prompt)

    def test_react_system_prompt_separates_extract_text_from_context_producing_ai_script(self):
        prompt = ASSISTANT_MODULE.REACT_SYSTEM_PROMPT

        self.assertIn("Any context-producing extraction", prompt)
        self.assertIn("must use ai_script / code path", prompt)
        self.assertIn("context_bindings", prompt)
        self.assertIn("output_payload", prompt)
        self.assertIn("Do not use result_key or result_keys for context writes.", prompt)
        self.assertNotIn("For extraction tasks, use operation=extract_text", prompt)


if __name__ == "__main__":
    unittest.main()
