import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

RPA_ROUTE_MODULE = importlib.import_module("backend.route.rpa")


class _FakePage:
    def __init__(self, manager=None, switch_to=None):
        self.manager = manager
        self.switch_to = switch_to
        self.evaluate_calls = []
        self.wait_for_load_state_calls = []
        self.wait_for_function_calls = []
        self.wait_for_timeout_calls = []
        self.inner_text_value = "Example page"
        self.url = "https://example.com/list"
        self.snapshot_queue = [
            {
                "url": "https://example.com/list",
                "title": "List Page",
                "bodyText": "Old list content",
                "interactiveValues": [],
            },
            {
                "url": "https://example.com/detail",
                "title": "Detail Page",
                "bodyText": "Fresh detail content",
                "interactiveValues": ["input | keyword | search | keyword | Search | Example"],
            },
        ]

    async def inner_text(self, _selector):
        return self.inner_text_value

    async def evaluate(self, script):
        self.evaluate_calls.append(script)
        if script.strip().startswith("() =>") and self.snapshot_queue:
            return self.snapshot_queue.pop(0)
        return None

    def get_by_role(self, *args, **kwargs):
        return self

    async def click(self):
        if self.manager and self.switch_to is not None:
            self.manager.page = self.switch_to
        return None

    async def wait_for_load_state(self, state, timeout=None):
        self.wait_for_load_state_calls.append((state, timeout))
        return None

    async def wait_for_function(self, script, timeout=None):
        self.wait_for_function_calls.append((script, timeout))
        return None

    async def wait_for_timeout(self, timeout):
        self.wait_for_timeout_calls.append(timeout)
        return None

    async def sleep(self, _seconds):
        return None


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeModel:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, _messages):
        if isinstance(self._content, list):
            if not self._content:
                raise AssertionError("Fake model exhausted")
            return _FakeResponse(self._content.pop(0))
        return _FakeResponse(self._content)


class _FakeStep:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return dict(self._data)


class _FakeReActAgent:
    """Agent that natively produces ai_command steps for data extraction."""
    async def run(self, **_kwargs):
        yield {
            "event": "agent_step_done",
            "data": {
                "step": {
                    "action": "ai_command",
                    "description": "提取 PR 列表",
                    "prompt": "收集 PR 信息",
                    "result_key": "all_prs",
                    "data_format": "json",
                },
                "output": '[{"author":"alice","title":"Fix bug"}]',
            },
        }
        yield {
            "event": "agent_done",
            "data": {
                "total_steps": 1,
                "final_output": [{"author": "alice", "title": "Fix bug"}],
            },
        }


class _FakeInvalidFinalOutputAgent:
    async def run(self, **_kwargs):
        yield {
            "event": "agent_done",
            "data": {
                "total_steps": 0,
                "final_output": [{"name": "Fix bug"}],
            },
        }


class _FakeAIScriptAgent:
    """Agent that uses ai_script for backward compat testing."""
    async def run(self, **_kwargs):
        yield {
            "event": "agent_step_done",
            "data": {
                "step": {
                    "action": "ai_script",
                    "description": "批量收集 PR 信息",
                    "prompt": "收集 PR 信息",
                    "value": '\n'.join([
                        'items = await page.locator("article a.Link--primary").all_inner_texts()',
                        '_results["all_pr_titles"] = items',
                    ]),
                },
                "output": '[{"author":"alice","title":"Fix bug"}]',
            },
        }
        yield {
            "event": "agent_done",
            "data": {
                "total_steps": 1,
                "final_output": [{"author": "alice", "title": "Fix bug"}],
            },
        }


class _FakeOperationAgent:
    async def run(self, **_kwargs):
        yield {
            "event": "agent_step_done",
            "data": {
                "step": {
                    "action": "click",
                    "target": '{"method":"role","role":"link","name":"Trending Repo"}',
                    "frame_path": [],
                    "description": "点击本周最火项目",
                    "prompt": "打开本周最火项目详情页",
                    "source": "ai",
                },
                "output": "ok",
            },
        }
        yield {
            "event": "agent_done",
            "data": {
                "total_steps": 1,
                "final_output": None,
            },
        }


class _FakeOperationWithFinalOutputAgent:
    async def run(self, **_kwargs):
        yield {
            "event": "agent_step_done",
            "data": {
                "step": {
                    "action": "click",
                    "target": '{"method":"role","role":"link","name":"Trending Repo"}',
                    "frame_path": [],
                    "description": "点击本周最火项目",
                    "prompt": "打开本周最火项目详情页",
                    "source": "ai",
                },
                "output": "ok",
            },
        }
        yield {
            "event": "agent_done",
            "data": {
                "total_steps": 2,
                "final_output": [{"name": "owner/repo", "summary": "一个热门项目"}],
            },
        }


class _FakeManager:
    def __init__(self):
        self.events = []
        self.page = _FakePage(manager=self)
        self.session = SimpleNamespace(user_id="user-1", active_tab_id="tab-1", steps=[])

    async def get_session(self, _session_id):
        return self.session

    def get_page(self, _session_id):
        return self.page

    def pause_recording(self, _session_id):
        self.events.append("pause")

    def resume_recording(self, _session_id):
        self.events.append("resume")

    def suppress_navigation_events(self, _session_id, _tab_id, duration_ms=2000):
        self.events.append(("suppress_navigation", duration_ms))

    async def add_step(self, _session_id, step_data):
        self.events.append("add_step")
        return _FakeStep(step_data)


class SessionAICommandRouteTests(unittest.IsolatedAsyncioTestCase):
    def test_validate_auto_extract_output_is_independent_from_final_output_contract(self):
        extract_validation = RPA_ROUTE_MODULE._validate_auto_extract_output(
            "收集当前仓库所有 PR 的创建人和标题，严格输出数组",
            "Navigation Menu",
        )
        final_validation = RPA_ROUTE_MODULE._validate_auto_final_output_contract(
            "收集当前仓库所有 PR 的创建人和标题，严格输出数组",
            [{"author": "alice", "title": "Fix bug"}],
        )

        self.assertFalse(extract_validation["ok"])
        self.assertTrue(final_validation["ok"])

    def test_extract_request_auth_token_falls_back_to_session_cookie(self):
        request = SimpleNamespace(
            headers={},
            cookies={RPA_ROUTE_MODULE.settings.session_cookie: "cookie-session-token"},
        )

        token = RPA_ROUTE_MODULE._extract_request_auth_token(request)

        self.assertEqual(token, "cookie-session-token")

    def test_build_ai_command_url_uses_request_origin_for_local_mode(self):
        request = SimpleNamespace(base_url="http://127.0.0.1:12001/")

        url = RPA_ROUTE_MODULE._build_ai_command_url_for_request(request, is_local=True)

        self.assertEqual(url, "http://127.0.0.1:12001/api/v1/rpa/ai-command")

    def test_build_ai_command_url_rewrites_localhost_for_sandbox_mode(self):
        request = SimpleNamespace(base_url="http://localhost:5173/")

        url = RPA_ROUTE_MODULE._build_ai_command_url_for_request(request, is_local=False)

        self.assertEqual(url, "http://host.docker.internal:5173/api/v1/rpa/ai-command")

    async def test_auto_mode_persists_operation_and_data_before_resuming(self):
        """Auto mode records operation and data steps, then a summary step."""
        fake_manager = _FakeManager()
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="打开示例页面并读取标题",
            output_variable="page_title",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")

        class _OpAndDataAgent:
            async def run(self, **_kwargs):
                # Operation step
                yield {
                    "event": "agent_step_done",
                    "data": {
                        "step": {
                            "action": "navigate",
                            "url": "https://example.com",
                            "description": "打开示例页面",
                            "source": "ai",
                        },
                        "output": "ok",
                    },
                }
                # Data step
                yield {
                    "event": "agent_step_done",
                    "data": {
                        "step": {
                            "action": "ai_command",
                            "description": "读取页面标题",
                            "prompt": "读取当前页面标题",
                            "result_key": "page_title",
                            "data_format": "text",
                        },
                        "output": "Example page title",
                    },
                }
                yield {
                    "event": "agent_done",
                    "data": {
                        "total_steps": 2,
                        "final_output": "Example page title",
                    },
                }

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "RPAReActAgent",
            _OpAndDataAgent,
        ):
            result = await RPA_ROUTE_MODULE.session_ai_command(
                "session-1",
                request,
                current_user=current_user,
            )

        self.assertEqual(result["status"], "success")
        # pause, add_step (navigate), add_step (ai_command), add_step (summary), resume
        self.assertEqual(
            fake_manager.events,
            ["pause", "add_step", "add_step", "add_step", "resume"],
        )
        # Two steps: navigate + ai_command data, plus summary
        self.assertEqual(len(result["steps"]), 3)
        self.assertEqual(result["steps"][0]["action"], "navigate")
        self.assertEqual(result["steps"][1]["action"], "ai_command")
        self.assertEqual(result["steps"][1]["data_value"], "Example page title")
        self.assertEqual(result["steps"][1]["output_variable"], "page_title")
        self.assertEqual(result["steps"][2]["action"], "ai_command")
        self.assertEqual(result["steps"][2]["description"], "AI 最终总结")

    async def test_auto_mode_extracts_data_from_post_operation_context(self):
        """Agent extracts data after an operation, the data step records output."""
        fake_manager = _FakeManager()
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="打开详情页并读取最新标题",
            output_variable="detail_title",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")

        class _OpThenDataAgent:
            async def run(self, **_kwargs):
                # Operation step
                yield {
                    "event": "agent_step_done",
                    "data": {
                        "step": {
                            "action": "click",
                            "target": '{"method":"role","role":"link","name":"详情"}',
                            "frame_path": [],
                            "description": "打开详情页",
                            "source": "ai",
                        },
                        "output": "ok",
                    },
                }
                # Data extraction step
                yield {
                    "event": "agent_step_done",
                    "data": {
                        "step": {
                            "action": "ai_command",
                            "description": "读取最新详情标题",
                            "prompt": "读取当前详情页标题",
                            "result_key": "detail_title",
                            "data_format": "text",
                        },
                        "output": "Detail page title",
                    },
                }
                yield {
                    "event": "agent_done",
                    "data": {
                        "total_steps": 2,
                        "final_output": "Detail page title",
                    },
                }

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "RPAReActAgent",
            _OpThenDataAgent,
        ):
            result = await RPA_ROUTE_MODULE.session_ai_command(
                "session-1",
                request,
                current_user=current_user,
            )

        self.assertEqual(result["status"], "success")
        # click (operation), ai_command (data), ai_command (summary)
        self.assertEqual(len(result["steps"]), 3)
        self.assertEqual(result["steps"][0]["action"], "click")
        self.assertEqual(result["steps"][0]["replay_mode"], "ai")
        self.assertEqual(result["steps"][1]["action"], "ai_command")
        self.assertEqual(result["steps"][1]["data_value"], "Detail page title")
        self.assertEqual(result["steps"][1]["output_variable"], "detail_title")
        self.assertEqual(result["steps"][2]["description"], "AI 最终总结")

    async def test_auto_mode_returns_agent_final_output(self):
        fake_manager = _FakeManager()
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="收集当前仓库所有 PR 的创建人和标题，严格输出数组",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "RPAReActAgent",
            _FakeReActAgent,
        ):
            result = await RPA_ROUTE_MODULE.session_ai_command(
                "session-1",
                request,
                current_user=current_user,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["final_output"], [{"author": "alice", "title": "Fix bug"}])
        self.assertTrue(result["final_output_validation"]["ok"])
        # First step is the native ai_command from the agent
        self.assertEqual(result["steps"][0]["data_value"], '[{"author":"alice","title":"Fix bug"}]')
        self.assertEqual(result["steps"][0]["action"], "ai_command")
        self.assertEqual(result["steps"][0]["ai_mode"], "data")
        self.assertEqual(result["steps"][0]["ai_result_mode"], "data_only")
        self.assertEqual(
            result["steps"][0]["assistant_diagnostics"]["auto_persist_strategy"],
            "native_ai_command",
        )
        # Summary step is always present (second step)
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][1]["action"], "ai_command")
        self.assertEqual(result["steps"][1]["description"], "AI 最终总结")

    async def test_auto_mode_reports_final_output_contract_issues_without_failing_extract_validation(self):
        fake_manager = _FakeManager()
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="收集当前仓库所有 PR 的创建人和标题，严格输出数组",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "RPAReActAgent",
            _FakeInvalidFinalOutputAgent,
        ):
            result = await RPA_ROUTE_MODULE.session_ai_command(
                "session-1",
                request,
                current_user=current_user,
            )

        self.assertEqual(result["status"], "success")
        self.assertFalse(result["final_output_validation"]["ok"])
        self.assertIn("missing title", result["final_output_validation"]["errors"][0])

    async def test_auto_mode_preserves_native_ai_script_steps(self):
        """ai_script steps are kept as-is (no forced conversion)."""
        fake_manager = _FakeManager()
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="收集当前仓库所有 PR 的创建人和标题，严格输出数组",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "RPAReActAgent",
            _FakeAIScriptAgent,
        ):
            result = await RPA_ROUTE_MODULE.session_ai_command(
                "session-1",
                request,
                current_user=current_user,
            )

        self.assertEqual(result["status"], "success")
        # ai_script is preserved as-is, not converted to ai_command
        self.assertEqual(result["steps"][0]["action"], "ai_script")
        self.assertEqual(result["steps"][0]["source"], "ai")
        self.assertEqual(result["steps"][0]["data_value"], '[{"author":"alice","title":"Fix bug"}]')
        # Summary step is always present
        self.assertEqual(result["steps"][1]["action"], "ai_command")
        self.assertEqual(result["steps"][1]["description"], "AI 最终总结")

    async def test_auto_mode_defaults_ai_operation_steps_to_ai_replay(self):
        fake_manager = _FakeManager()
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="打开本周最火项目详情页",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "RPAReActAgent",
            _FakeOperationAgent,
        ):
            result = await RPA_ROUTE_MODULE.session_ai_command(
                "session-1",
                request,
                current_user=current_user,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["steps"][0]["action"], "click")
        self.assertEqual(result["steps"][0]["source"], "ai")
        self.assertEqual(result["steps"][0]["replay_mode"], "ai")
        # Summary step is always present (even for pure operation tasks)
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][1]["action"], "ai_command")
        self.assertEqual(result["steps"][1]["description"], "AI 最终总结")

    async def test_auto_mode_always_creates_summary_step_with_final_output(self):
        fake_manager = _FakeManager()
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="帮我查看这周 github 最火的项目，并帮我简单介绍下",
            output_variable="weekly_trending_summary",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "RPAReActAgent",
            _FakeOperationWithFinalOutputAgent,
        ):
            result = await RPA_ROUTE_MODULE.session_ai_command(
                "session-1",
                request,
                current_user=current_user,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["action"], "click")
        # Summary step with recorded final_output
        self.assertEqual(result["steps"][1]["action"], "ai_command")
        self.assertEqual(result["steps"][1]["ai_mode"], "data")
        self.assertEqual(result["steps"][1]["ai_result_mode"], "data_only")
        self.assertEqual(result["steps"][1]["output_variable"], "weekly_trending_summary")
        self.assertIn("用户原始请求", result["steps"][1]["data_prompt"])
        self.assertEqual(result["steps"][1]["data_context_mode"], "page")
        self.assertEqual(
            result["steps"][1]["assistant_diagnostics"]["recorded_final_output"],
            [{"name": "owner/repo", "summary": "一个热门项目"}],
        )

    async def test_auto_mode_summary_step_present_when_no_final_output(self):
        """Summary step is always created, even when agent returns no final_output."""
        fake_manager = _FakeManager()
        request = RPA_ROUTE_MODULE.SessionAICommandRequest(
            prompt="打开本周最火项目详情页",
        )
        current_user = SimpleNamespace(id="user-1", username="tester")

        with patch.object(RPA_ROUTE_MODULE, "rpa_manager", fake_manager), patch.object(
            RPA_ROUTE_MODULE,
            "_resolve_user_model_config",
            return_value={},
        ), patch.object(
            RPA_ROUTE_MODULE,
            "RPAReActAgent",
            _FakeOperationAgent,
        ):
            result = await RPA_ROUTE_MODULE.session_ai_command(
                "session-1",
                request,
                current_user=current_user,
            )

        self.assertEqual(result["status"], "success")
        # Operation step + summary step
        self.assertEqual(len(result["steps"]), 2)
        summary_step = result["steps"][1]
        self.assertEqual(summary_step["action"], "ai_command")
        self.assertEqual(summary_step["description"], "AI 最终总结")
        self.assertIsNone(summary_step["data_value"])
        self.assertIn("用户原始请求", summary_step["data_prompt"])


if __name__ == "__main__":
    unittest.main()
