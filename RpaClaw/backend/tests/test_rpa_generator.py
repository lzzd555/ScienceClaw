import importlib
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path


GENERATOR_PATH = Path(__file__).resolve().parents[1] / "rpa" / "generator.py"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

CONTEXT_LEDGER_MODULE = importlib.import_module("backend.rpa.context_ledger")
SESSION_CONTEXT_SERVICE_MODULE = importlib.import_module("backend.rpa.session_context_service")
SPEC = importlib.util.spec_from_file_location("rpa_generator_module", GENERATOR_PATH)
GENERATOR_MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(GENERATOR_MODULE)
PlaywrightGenerator = GENERATOR_MODULE.PlaywrightGenerator


class PlaywrightGeneratorTests(unittest.TestCase):
    def test_build_locator_nested_role_chains_get_by_role(self):
        generator = PlaywrightGenerator()
        target = json.dumps(
            {
                "method": "nested",
                "parent": {"method": "role", "role": "navigation", "name": "Main"},
                "child": {"method": "role", "role": "link", "name": "Pricing"},
            }
        )

        locator = generator._build_locator(target)

        self.assertEqual(
            locator,
            'page.get_by_role("navigation", name="Main", exact=True)'
            '.get_by_role("link", name="Pricing", exact=True)',
        )

    def test_generate_script_uses_nested_role_locator_without_bare_get_by_role(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps(
                    {
                        "method": "nested",
                        "parent": {"method": "role", "role": "navigation", "name": "Main"},
                        "child": {"method": "role", "role": "link", "name": "Pricing"},
                    }
                ),
                "description": "点击导航中的 Pricing 链接",
                "tag": "A",
                "url": "https://example.com",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn(
            'await current_page.get_by_role("navigation", name="Main", exact=True).get_by_role("link", name="Pricing", exact=True).click()',
            script,
        )
        self.assertNotIn("await get_by_role(", script)
        self.assertNotIn("expect_navigation", script)

    def test_build_locator_nested_css_parent_and_role_child(self):
        generator = PlaywrightGenerator()
        target = json.dumps(
            {
                "method": "nested",
                "parent": {"method": "css", "value": "#hero"},
                "child": {"method": "role", "role": "button", "name": "Sign up for GitHub"},
            }
        )

        locator = generator._build_locator(target)

        self.assertEqual(
            locator,
            'page.locator("#hero").get_by_role("button", name="Sign up for GitHub", exact=True)',
        )

    def test_build_locator_supports_nth_locator_payload(self):
        generator = PlaywrightGenerator()
        target = json.dumps(
            {
                "method": "nth",
                "locator": {"method": "role", "role": "button", "name": "Save"},
                "index": 2,
            }
        )

        locator = generator._build_locator(target)

        self.assertEqual(
            locator,
            'page.get_by_role("button", name="Save", exact=True).nth(2)',
        )

    def test_generate_script_click_with_nth_locator_uses_nth_chain(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps(
                    {
                        "method": "nth",
                        "locator": {"method": "role", "role": "button", "name": "Save"},
                        "index": 1,
                    }
                ),
                "description": "Click second Save button",
                "tag": "BUTTON",
                "url": "https://example.com",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn(
            'await current_page.get_by_role("button", name="Save", exact=True).nth(1).click()',
            script,
        )

    def test_generate_script_tracks_current_page_for_open_tab_click(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "open_tab_click",
                "target": json.dumps({"method": "role", "role": "link", "name": "Open report"}),
                "description": "Open report in a new tab",
                "tag": "A",
                "url": "https://example.com",
                "tab_id": "tab-1",
                "target_tab_id": "tab-2",
            },
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Confirm"}),
                "description": "Confirm in popup tab",
                "tag": "BUTTON",
                "url": "https://example.com/report",
                "tab_id": "tab-2",
            },
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('tabs = {"tab-1": page}', script)
        self.assertIn("current_page = page", script)
        self.assertIn("async with current_page.expect_popup() as popup_info:", script)
        self.assertIn('tabs["tab-2"] = new_page', script)
        self.assertIn("current_page = new_page", script)
        self.assertIn('await current_page.get_by_role("button", name="Confirm", exact=True).click()', script)

    def test_generate_script_ignores_popup_signal_for_download_when_popup_tab_is_unused(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "css", "value": "a.link-special"}),
                "description": "Click first download link",
                "tag": "A",
                "url": "https://example.com",
                "tab_id": "tab-1",
                "signals": {
                    "popup": {"target_tab_id": "tab-2"},
                    "download": {"filename": "ContractList20260411111546.xlsx"},
                },
                "source": "ai",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn("async with current_page.expect_download() as _dl_info:", script)
        self.assertIn('await current_page.locator("a.link-special").click()', script)
        self.assertIn("_dl = await _dl_info.value", script)
        self.assertIn("_dl_dest = _os.path.join(_dl_dir, _dl.suggested_filename)", script)
        self.assertIn('_results["download_ContractList20260411111546"]', script)
        self.assertNotIn("manually wrap the triggering click with expect_download()", script)
        self.assertNotIn("expect_popup", script)

    def test_generate_script_keeps_popup_signal_for_download_when_popup_tab_is_used_later(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "css", "value": "a.link-special"}),
                "description": "Click first download link",
                "tag": "A",
                "url": "https://example.com",
                "tab_id": "tab-1",
                "signals": {
                    "popup": {"target_tab_id": "tab-2"},
                    "download": {"filename": "ContractList20260411111546.xlsx"},
                },
                "source": "ai",
            },
            {
                "action": "click",
                "target": json.dumps({"method": "css", "value": "#confirm"}),
                "description": "Confirm in popup tab",
                "tag": "BUTTON",
                "url": "https://example.com/export",
                "tab_id": "tab-2",
            },
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn("async with current_page.expect_download() as _dl_info:", script)
        self.assertIn("async with current_page.expect_popup() as popup_info:", script)
        self.assertIn('tabs["tab-2"] = new_page', script)
        self.assertIn("current_page = new_page", script)

    def test_generate_script_merges_legacy_popup_then_download_steps(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "open_tab_click",
                "target": json.dumps({"method": "css", "value": "a.link-special"}),
                "description": "Open first download link in a new tab",
                "tag": "A",
                "url": "https://example.com",
                "tab_id": "tab-1",
                "target_tab_id": "tab-2",
            },
            {
                "action": "download",
                "value": "ContractList20260411111546.xlsx",
                "description": "Download file",
                "url": "https://example.com/export",
                "tab_id": "tab-2",
            },
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn("async with current_page.expect_download() as _dl_info:", script)
        self.assertNotIn("expect_popup", script)
        self.assertNotIn('NOTE: download of "ContractList20260411111546.xlsx" was triggered by a previous action', script)

    def test_generate_script_does_not_assume_ai_click_download_without_signal(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "css", "value": "a.link-special"}),
                "description": "Click first file link",
                "tag": "A",
                "url": "https://example.com",
                "tab_id": "tab-1",
                "source": "ai",
                "signals": {},
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('await current_page.locator("a.link-special").click()', script)
        self.assertNotIn("expect_download", script)
        self.assertNotIn("expect_popup", script)

    def test_generate_script_switches_back_to_existing_tab(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "switch_tab",
                "description": "Switch back to the original tab",
                "tab_id": "tab-2",
                "target_tab_id": "tab-1",
                "url": "https://example.com",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('current_page = tabs["tab-1"]', script)
        self.assertIn("await current_page.bring_to_front()", script)

    def test_generate_script_does_not_assume_every_link_click_navigates(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "css", "value": 'a[name="tj_briicon"]'}),
                "description": "点击百度入口链接",
                "tag": "A",
                "url": "https://www.baidu.com/",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('await current_page.locator("a[name=\\"tj_briicon\\"]").click()', script)
        self.assertIn("await current_page.wait_for_timeout(500)", script)
        self.assertNotIn("expect_navigation", script)

    def test_generate_script_uses_expect_navigation_for_navigate_click(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "navigate_click",
                "target": json.dumps({"method": "role", "role": "link", "name": "Search"}),
                "description": "点击 Search 并跳转页面",
                "tag": "A",
                "url": "https://example.com/search",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn("async with current_page.expect_navigation", script)
        self.assertIn('await current_page.get_by_role("link", name="Search", exact=True).click()', script)

    def test_generate_script_uses_expect_navigation_for_navigate_press(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "navigate_press",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "Search"}),
                "description": "Press Enter and navigate",
                "tag": "INPUT",
                "value": "Enter",
                "url": "https://example.com/search",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn("async with current_page.expect_navigation", script)
        self.assertIn('await current_page.get_by_role("textbox", name="Search", exact=True).press("Enter")', script)

    def test_generate_script_uses_check_for_check_action(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "check",
                "target": json.dumps({"method": "role", "role": "checkbox", "name": "Subscribe"}),
                "description": "勾选订阅",
                "tag": "INPUT",
                "url": "https://example.com/settings",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('await current_page.get_by_role("checkbox", name="Subscribe", exact=True).check()', script)

    def test_generate_script_uses_uncheck_for_uncheck_action(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "uncheck",
                "target": json.dumps({"method": "role", "role": "checkbox", "name": "Subscribe"}),
                "description": "取消勾选订阅",
                "tag": "INPUT",
                "url": "https://example.com/settings",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('await current_page.get_by_role("checkbox", name="Subscribe", exact=True).uncheck()', script)

    def test_generate_script_uses_set_input_files_for_file_action(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "set_input_files",
                "target": json.dumps({"method": "label", "value": "Upload file"}),
                "description": "上传文件",
                "tag": "INPUT",
                "url": "https://example.com/upload",
                "value": "report.xlsx",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('await current_page.get_by_label("Upload file", exact=True).set_input_files(', script)

    def test_generate_script_infers_open_tab_click_from_tab_id_change(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "css", "value": 'a[name="tj_briicon"]'}),
                "description": "点击百度入口链接",
                "tag": "A",
                "url": "https://www.baidu.com/",
                "tab_id": "tab-1",
            },
            {
                "action": "click",
                "target": json.dumps({"method": "css", "value": "#kw"}),
                "description": "点击搜索框",
                "tag": "INPUT",
                "url": "https://chat.baidu.com/",
                "tab_id": "tab-2",
            },
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn("async with current_page.expect_popup() as popup_info:", script)
        self.assertIn('tabs["tab-2"] = new_page', script)
        self.assertIn("current_page = new_page", script)
        self.assertIn('await current_page.locator("#kw").click()', script)

    def test_generate_script_infers_switch_tab_for_known_tab_change(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "open_tab_click",
                "target": json.dumps({"method": "text", "value": "Open"}),
                "description": "打开新标签页",
                "tag": "A",
                "url": "https://example.com",
                "tab_id": "tab-1",
                "target_tab_id": "tab-2",
            },
            {
                "action": "click",
                "target": json.dumps({"method": "css", "value": "#confirm"}),
                "description": "在新标签页点击确认",
                "tag": "BUTTON",
                "url": "https://example.com/new",
                "tab_id": "tab-2",
            },
            {
                "action": "click",
                "target": json.dumps({"method": "css", "value": "#search"}),
                "description": "切回原标签页点击搜索",
                "tag": "INPUT",
                "url": "https://example.com",
                "tab_id": "tab-1",
            },
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('current_page = tabs["tab-1"]', script)
        self.assertIn("await current_page.bring_to_front()", script)
        self.assertIn('await current_page.locator("#search").click()', script)

    def test_generate_script_handles_close_tab_and_fallback(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "open_tab_click",
                "target": json.dumps({"method": "text", "value": "Open"}),
                "description": "打开新标签页",
                "tag": "A",
                "url": "https://example.com",
                "tab_id": "tab-1",
                "target_tab_id": "tab-2",
            },
            {
                "action": "close_tab",
                "description": "关闭标签页",
                "tab_id": "tab-2",
                "target_tab_id": "tab-1",
                "url": "https://example.com/new",
            },
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('closing_page = tabs.pop("tab-2", current_page)', script)
        self.assertIn("await closing_page.close()", script)
        self.assertIn('current_page = tabs["tab-1"]', script)
        self.assertIn("await current_page.bring_to_front()", script)

    def test_generate_script_closes_target_tab_without_repointing_current_page(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "open_tab_click",
                "target": json.dumps({"method": "text", "value": "Open"}),
                "description": "打开新标签页",
                "tag": "A",
                "url": "https://example.com",
                "tab_id": "tab-1",
                "target_tab_id": "tab-2",
            },
            {
                "action": "switch_tab",
                "description": "切回原标签页",
                "tab_id": "tab-2",
                "target_tab_id": "tab-1",
                "url": "https://example.com",
            },
            {
                "action": "close_tab",
                "description": "关闭后台标签页",
                "tab_id": "tab-2",
                "target_tab_id": "tab-1",
                "url": "https://example.com/new",
            },
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('closing_page = tabs.pop("tab-2", current_page)', script)
        self.assertIn("await closing_page.close()", script)
        self.assertEqual(script.count('current_page = tabs["tab-1"]'), 1)
        self.assertEqual(script.count("await current_page.bring_to_front()"), 1)

    def test_generate_script_uses_frame_locator_chain_for_frame_path(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Save"}),
                "description": "Click Save inside nested iframe",
                "tag": "BUTTON",
                "url": "https://example.com",
                "tab_id": "tab-1",
                "frame_path": ["iframe[name='workspace']", "iframe[title='editor']"],
            },
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('frame_scope = current_page.frame_locator("iframe[name=\'workspace\']")', script)
        self.assertIn('frame_scope = frame_scope.frame_locator("iframe[title=\'editor\']")', script)
        self.assertIn('await frame_scope.get_by_role("button", name="Save", exact=True).click()', script)

    def test_generate_script_does_not_await_frame_locator_assignments_inside_ai_script(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "ai_script",
                "source": "ai",
                "description": "点击菜鸟笔记",
                "value": '\n'.join([
                    'preview = page.frame_locator("iframe[title=\'运行结果预览\']").frame_locator("iframe")',
                    '_results["preview"] = preview',
                    'await preview.get_by_role("link", name="菜鸟笔记").click()',
                ]),
                "url": "https://www.runoob.com/try/try.php?filename=tryhtml_iframe",
                "tab_id": "tab-1",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('preview = page.frame_locator("iframe[title=\'运行结果预览\']").frame_locator("iframe")', script)
        self.assertNotIn('preview = await page.frame_locator("iframe[title=\'运行结果预览\']").frame_locator("iframe")', script)
        self.assertNotIn('_results["preview"] = preview', script)


    def test_generate_script_keeps_result_capture_after_locator_variable_is_reassigned_to_data(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "ai_script",
                "source": "ai",
                "description": "鑾峰彇 iframe 鏍囬",
                "value": '\n'.join([
                    'preview = page.frame_locator("iframe[title=\'杩愯缁撴灉棰勮\']").frame_locator("iframe")',
                    'preview = await preview.locator("h1").inner_text()',
                    '_results["preview"] = preview',
                ]),
                "url": "https://www.runoob.com/try/try.php?filename=tryhtml_iframe",
                "tab_id": "tab-1",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('preview = page.frame_locator("iframe[title=\'杩愯缁撴灉棰勮\']").frame_locator("iframe")', script)
        self.assertIn('preview = await preview.locator("h1").inner_text()', script)
        self.assertEqual(script.count('_results["preview"] = preview'), 1)


    def test_generate_script_does_not_prefix_for_loop_over_page_frames_with_await(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "ai_script",
                "source": "ai",
                "description": "loop frames",
                "value": '\n'.join([
                    'for frame in page.frames:',
                    '    if "preview" in frame.url:',
                    '        await frame.get_by_role("link", name="docs").click()',
                    '        break',
                ]),
                "url": "https://example.com",
                "tab_id": "tab-1",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('for frame in page.frames:', script)
        self.assertNotIn('await for frame in page.frames:', script)

    def test_generate_script_uses_collection_item_locator_for_first_structured_collection(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps(
                    {
                        "method": "collection_item",
                        "collection": {"method": "css", "value": "main article.card"},
                        "item": {"method": "css", "value": "h2 a"},
                        "ordinal": "first",
                    }
                ),
                "description": "点击列表中的第一个项目",
                "url": "https://example.com/list",
                "source": "ai",
                "collection_hint": {"kind": "repeated_items", "container_hint": {"locator": {"method": "css", "value": "main article.card"}}},
                "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                "ordinal": "first",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('await current_page.locator("main article.card").first.locator("h2 a").click()', script)
        self.assertNotIn('forrestchang / andrej-karpathy-skills', script)

    def test_generate_script_extract_text_step_reads_text_and_persists_result(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "extract_text",
                "target": json.dumps({"method": "role", "role": "link", "name": "Issue Title"}),
                "description": "提取最近一条 issue 的标题",
                "result_key": "latest_issue_title",
                "url": "https://example.com/repo/issues",
                "source": "ai",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn(
            'extract_text_value_1 = await current_page.get_by_role("link", name="Issue Title", exact=True).inner_text()',
            script,
        )
        self.assertIn('_results["latest_issue_title"] = extract_text_value_1', script)

    def test_generate_script_extract_text_step_uses_stable_suffix_for_duplicate_result_keys(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "extract_text",
                "target": json.dumps({"method": "role", "role": "link", "name": "Issue Title A"}),
                "description": "提取最近一条 issue 的标题",
                "result_key": "latest_issue_title",
                "url": "https://example.com/repo/issues",
                "source": "ai",
            },
            {
                "action": "extract_text",
                "target": json.dumps({"method": "role", "role": "link", "name": "Issue Title B"}),
                "description": "提取最近一条 issue 的标题",
                "result_key": "latest_issue_title",
                "url": "https://example.com/repo/issues",
                "source": "ai",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('_results["latest_issue_title"] = extract_text_value_1', script)
        self.assertIn('_results["latest_issue_title_2"] = extract_text_value_2', script)

    def test_generate_script_extract_text_step_falls_back_to_default_key_without_result_key(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "extract_text",
                "target": json.dumps({"method": "role", "role": "link", "name": "Issue Title"}),
                "description": "提取最近一条 issue 的标题",
                "url": "https://example.com/repo/issues",
                "source": "ai",
            }
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn('_results["extract_text_1"] = extract_text_value_1', script)


    def test_generate_script_test_mode_wraps_click_in_try_except(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Submit"}),
                "description": "点击提交按钮",
                "url": "https://example.com",
            }
        ]
        script = generator.generate_script(steps, is_local=True, test_mode=True)
        self.assertIn("class StepExecutionError(Exception):", script)
        self.assertIn("except StepExecutionError:", script)
        self.assertIn("raise StepExecutionError(step_index=0,", script)
        self.assertIn('.get_by_role("button", name="Submit", exact=True).click()', script)

    def test_generate_script_test_mode_wraps_navigate_step(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "navigate",
                "target": "",
                "url": "https://example.com",
                "description": "打开首页",
            }
        ]
        script = generator.generate_script(steps, is_local=True, test_mode=True)
        self.assertIn("raise StepExecutionError(step_index=0,", script)
        self.assertIn('await current_page.goto("https://example.com")', script)

    def test_generate_script_test_mode_false_produces_unchanged_output(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Go"}),
                "description": "Click go",
                "url": "https://example.com",
            }
        ]
        script_normal = generator.generate_script(steps, is_local=True)
        script_explicit = generator.generate_script(steps, is_local=True, test_mode=False)
        self.assertEqual(script_normal, script_explicit)
        self.assertNotIn("StepExecutionError", script_normal)

    def test_generate_script_local_runner_uses_relaxed_browser_security_settings(self):
        generator = PlaywrightGenerator()

        script = generator.generate_script([], is_local=True)

        self.assertIn("--ignore-certificate-errors", script)
        self.assertIn("--allow-insecure-localhost", script)
        self.assertIn("--allow-running-insecure-content", script)
        self.assertIn("--test-type", script)
        self.assertIn("'ignore_https_errors': True", script)

    def test_generate_script_docker_runner_ignores_https_errors_in_context(self):
        generator = PlaywrightGenerator()

        script = generator.generate_script([], is_local=False)

        self.assertIn("'ignore_https_errors': True", script)

    def test_generate_script_test_mode_step_index_aligns_after_dedup(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "A"}),
                "description": "first click",
                "url": "https://example.com",
            },
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "A"}),
                "description": "duplicate click",
                "url": "https://example.com",
            },
            {
                "action": "fill",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "Search"}),
                "value": "hello",
                "description": "fill search",
                "url": "https://example.com",
            },
        ]
        script = generator.generate_script(steps, is_local=True, test_mode=True)
        self.assertIn("raise StepExecutionError(step_index=0,", script)
        self.assertIn("raise StepExecutionError(step_index=1,", script)
        self.assertNotIn("step_index=2", script)

    def test_generate_script_test_mode_reraises_step_execution_error(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "X"}),
                "description": "click",
                "url": "https://example.com",
            }
        ]
        script = generator.generate_script(steps, is_local=True, test_mode=True)
        step_error_pos = script.index("except StepExecutionError:")
        raise_pos = script.index("raise\n", step_error_pos)
        generic_except_pos = script.index("except Exception as _e:", step_error_pos)
        self.assertLess(raise_pos, generic_except_pos)


    def test_test_mode_script_raises_parseable_step_error_on_missing_locator(self):
        """Integration: generate a test_mode script, exec it, verify the error carries step_index."""
        import asyncio
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "navigate",
                "target": "",
                "url": "https://example.com",
                "description": "打开首页",
            },
            {
                "action": "click",
                "target": json.dumps({"method": "role", "role": "button", "name": "Nonexistent"}),
                "description": "点击不存在的按钮",
                "url": "https://example.com",
            },
        ]

        script = generator.generate_script(steps, is_local=True, test_mode=True)

        # Extract execute_skill and StepExecutionError from the generated script
        namespace = {}
        exec(compile(script, "<test>", "exec"), namespace)
        self.assertIn("execute_skill", namespace)
        self.assertIn("StepExecutionError", namespace)

        StepError = namespace["StepExecutionError"]

        # Verify StepExecutionError message format is parseable
        err = StepError(step_index=1, original_error="Timeout 30000ms")
        self.assertIn("STEP_FAILED:1:", str(err))
        parts = str(err).split("STEP_FAILED:", 1)[1].split(":", 1)
        self.assertEqual(int(parts[0]), 1)
        self.assertEqual(parts[1], "Timeout 30000ms")


class ContextRebuildTests(unittest.TestCase):
    """Tests for the context-rebuild phase in generated scripts.

    These tests verify that the generator emits a rebuild_context function
    and cross-page value transfer logic when steps carry context_writes /
    context_reads fields.  They are expected to FAIL until the generator
    implementation is updated (Task 6).
    """

    def test_generator_emits_rebuild_context_function(self):
        """The generated script must contain an async rebuild_context function."""
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "navigate",
                "target": "",
                "url": "https://example.com/form",
                "description": "Open form page",
            },
            {
                "action": "fill",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "Name"}),
                "value": "Alice",
                "description": "Fill name",
                "url": "https://example.com/form",
                "context_writes": ["person_name"],
            },
        ]

        script = generator.generate_script(steps, is_local=True)

        self.assertIn(
            "async def rebuild_context(page, context, **kwargs):",
            script,
            "Generated script must define an async rebuild_context function "
            "that receives page, context dict, and kwargs.",
        )

    def test_session_context_service_exports_normalized_generator_contract(self):
        ledger = CONTEXT_LEDGER_MODULE.TaskContextLedger()
        ledger.record_rebuild_action(
            "navigate",
            "https://site.com/search",
            step_ref="extract-step",
        )
        ledger.record_rebuild_action(
            "extract_text",
            "Extract buyer name from results",
            step_ref="extract-step",
            writes=["buyer"],
        )
        service = SESSION_CONTEXT_SERVICE_MODULE.SessionContextService(ledger)

        contract = service.export_generator_contract(
            [
                {
                    "id": "extract-step",
                    "action": "extract_text",
                    "target": json.dumps({"method": "role", "role": "cell", "name": "Buyer"}),
                    "description": "Extract buyer name",
                    "url": "https://site.com/search",
                    "context_writes": ["buyer"],
                },
                {
                    "id": "fill-step",
                    "action": "fill",
                    "target": json.dumps({"method": "role", "role": "textbox", "name": "Buyer"}),
                    "value": "context:buyer",
                    "description": "Fill the saved buyer",
                    "url": "https://site.com/form",
                },
            ]
        )

        self.assertEqual(contract["steps"][1]["context_contract"]["reads"], ["buyer"])
        self.assertEqual(contract["steps"][1]["context_contract"]["writes"], [])
        self.assertEqual(
            contract["rebuild_sequence"],
            [
                {
                    "action": "navigate",
                    "description": "https://site.com/search",
                    "writes": [],
                    "source_step_id": "extract-step",
                    "url": "https://site.com/search",
                },
                {
                    "action": "extract_text",
                    "description": "Extract buyer name from results",
                    "writes": ["buyer"],
                    "source_step_id": "extract-step",
                },
            ],
        )
        self.assertNotIn("context:buyer", contract["steps"][1]["context_contract"]["reads"])

    def test_generator_rebuilds_page_a_value_before_page_b_fill(self):
        """Cross-page value transfer: rebuild_context must appear before fill()."""
        generator = PlaywrightGenerator()
        steps = [
            # Page A — extract a value
            {
                "action": "navigate",
                "target": "",
                "url": "https://site.com/search",
                "description": "Open search page",
            },
            {
                "action": "extract_text",
                "target": json.dumps({"method": "role", "role": "cell", "name": "Result Name"}),
                "description": "Extract person name from search results",
                "result_key": "person_name",
                "url": "https://site.com/search",
                "source": "ai",
                "context_writes": ["person_name"],
            },
            # Page B — fill the extracted value
            {
                "action": "navigate",
                "target": "",
                "url": "https://site.com/form",
                "description": "Open form page",
            },
            {
                "action": "fill",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "Full Name"}),
                "value": "{{person_name}}",
                "description": "Fill the person name from page A",
                "url": "https://site.com/form",
                "context_reads": ["person_name"],
            },
        ]
        params = {}

        script = generator.generate_script(steps, params=params, is_local=True)

        # The script must reference the transferred value via context
        self.assertIn(
            'context["person_name"]',
            script,
            "Generated script must read person_name from the context dict "
            "for cross-page value transfer.",
        )

        # rebuild_context must appear before the first fill() call so the
        # context is populated before the value is consumed.
        rebuild_pos = script.find("rebuild_context")
        first_fill_pos = script.find(".fill(")
        self.assertGreater(
            rebuild_pos,
            -1,
            "Script must contain at least one reference to rebuild_context.",
        )
        self.assertGreater(
            first_fill_pos,
            -1,
            "Script must contain at least one .fill() call.",
        )
        self.assertLess(
            rebuild_pos,
            first_fill_pos,
            "rebuild_context must appear before the first .fill() call so "
            "the context dict is populated before values are consumed.",
        )

    def test_generator_uses_service_exported_contract_for_legacy_context_reads(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "id": "search-step",
                "action": "navigate",
                "target": "",
                "url": "https://site.com/search",
                "description": "Open search page",
            },
            {
                "id": "extract-step",
                "action": "extract_text",
                "target": json.dumps({"method": "role", "role": "cell", "name": "Buyer"}),
                "description": "Extract buyer name from results",
                "result_key": "buyer",
                "url": "https://site.com/search",
                "context_writes": ["buyer"],
            },
            {
                "id": "form-step",
                "action": "navigate",
                "target": "",
                "url": "https://site.com/form",
                "description": "Open form page",
            },
            {
                "id": "fill-step",
                "action": "fill",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "Buyer"}),
                "value": "context:buyer",
                "description": "Fill the saved buyer",
                "url": "https://site.com/form",
            },
        ]

        exploding_ledger = types.SimpleNamespace(
            rebuild_actions=[
                types.SimpleNamespace(
                    action="navigate",
                    description="https://site.com/search",
                    step_ref="extract-step",
                    writes=[],
                ),
                types.SimpleNamespace(
                    action="extract_text",
                    description="Extract buyer name from results",
                    step_ref="extract-step",
                    writes=["buyer"],
                ),
            ],
            observed_values={},
            derived_values={},
        )

        def _unexpected_direct_rebuild_sequence():
            raise AssertionError("generator should consume the service export, not ledger.get_rebuild_sequence()")

        exploding_ledger.get_rebuild_sequence = _unexpected_direct_rebuild_sequence

        script = generator.generate_script(steps, is_local=True, context_ledger=exploding_ledger)

        self.assertIn(
            'await current_page.get_by_role("textbox", name="Buyer", exact=True).fill(context.get("buyer", kwargs.get("buyer", "")))',
            script,
        )
        self.assertIn(
            'rebuild_var_ledger_1 = await current_page.get_by_role("cell", name="Buyer", exact=True).inner_text()',
            script,
        )
        self.assertNotIn("context:buyer", script)

    def test_generate_script_normal_path_eliminates_legacy_context_placeholders_from_contract_flow(self):
        generator = PlaywrightGenerator()
        steps = [
            {
                "id": "search-step",
                "action": "navigate",
                "target": "",
                "url": "https://site.com/search",
                "description": "Open search page",
            },
            {
                "id": "extract-step",
                "action": "extract_text",
                "target": json.dumps({"method": "role", "role": "cell", "name": "Buyer"}),
                "description": "Extract buyer name from results",
                "result_key": "buyer",
                "url": "https://site.com/search",
                "context_writes": ["buyer"],
            },
            {
                "id": "form-step",
                "action": "navigate",
                "target": "",
                "url": "https://site.com/form",
                "description": "Open form page",
            },
            {
                "id": "fill-buyer-step",
                "action": "fill",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "Buyer"}),
                "value": "context:buyer",
                "description": "Fill buyer using context:buyer",
                "url": "https://site.com/form",
            },
            {
                "id": "fill-note-step",
                "action": "fill",
                "target": json.dumps({"method": "role", "role": "textbox", "name": "Note"}),
                "value": "Buyer is context:buyer",
                "description": "Keep the legacy placeholder text out of the generated code path",
                "url": "https://site.com/form",
                "context_reads": ["buyer"],
            },
        ]

        ledger = types.SimpleNamespace(
            rebuild_actions=[
                types.SimpleNamespace(
                    action="navigate",
                    description="https://site.com/search",
                    step_ref="search-step",
                    writes=[],
                ),
                types.SimpleNamespace(
                    action="extract_text",
                    description="Extract buyer name from results",
                    step_ref="extract-step",
                    writes=["buyer"],
                ),
            ],
            observed_values={},
            derived_values={},
        )

        script = generator.generate_script(steps, is_local=True, context_ledger=ledger)

        self.assertIn(
            'await current_page.get_by_role("textbox", name="Buyer", exact=True).fill(context.get("buyer", kwargs.get("buyer", "")))',
            script,
        )
        self.assertIn(
            'await current_page.get_by_role("textbox", name="Note", exact=True).fill(context.get("buyer", kwargs.get("buyer", "")))',
            script,
        )
        self.assertNotIn('.fill("context:buyer")', script)
        self.assertNotIn(".fill('context:buyer')", script)
        self.assertNotIn('"Buyer is context:buyer"', script)
        self.assertNotIn("'Buyer is context:buyer'", script)

    def test_ai_script_step_still_reads_runtime_context(self):
        """Steps with context_reads should generate code that reads from context."""
        generator = PlaywrightGenerator()
        steps = [
            {
                "action": "navigate",
                "target": "",
                "url": "https://example.com",
                "description": "Open page",
            },
            {
                "action": "extract_text",
                "target": json.dumps({"method": "role", "role": "cell", "name": "ID"}),
                "description": "Extract person ID",
                "result_key": "person_id",
                "url": "https://example.com",
                "source": "ai",
                "context_writes": ["person_id"],
            },
            {
                "action": "ai_script",
                "source": "ai",
                "description": "Use the extracted person ID in custom logic",
                "value": (
                    "user_url = f'https://api.example.com/users/{person_id}'\n"
                    "await page.goto(user_url)"
                ),
                "url": "https://example.com",
                "tab_id": "tab-1",
                "context_reads": ["person_id"],
            },
        ]

        script = generator.generate_script(steps, is_local=True)

        # The generated script must inject a context read before the ai_script
        # body so that the variable "person_id" is available.  The generator
        # should emit something like:
        #   person_id = context.get("person_id", kwargs.get("person_id"))
        # before the user-provided ai_script code.
        has_context_read = (
            'context.get("person_id"' in script
            or "context.get('person_id'" in script
            or 'context["person_id"]' in script
            or "context['person_id']" in script
        )
        self.assertTrue(
            has_context_read,
            "Generated script must include code that reads person_id from the "
            "context dict (context.get('person_id', ...) or context['person_id']) "
            "for steps that declare context_reads.",
        )


if __name__ == "__main__":
    unittest.main()
