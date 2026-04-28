import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_app_client import EvalAppUserSession
from runner import (
    CaseAssertionError,
    assert_api_assertions,
    assert_expected_telemetry,
    build_browser_instruction,
    build_eval_auth_url,
    extract_final_url,
    render_console_summary,
    resolve_case_timeout_s,
    read_artifact_text,
    run_case,
)


class RunnerAssertionTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(__file__).resolve().parents[1] / ".runtime-test"
        self.tmp_dir.mkdir(exist_ok=True)

    def tearDown(self):
        for path in self.tmp_dir.glob("*"):
            if path.is_file():
                path.unlink()

    def test_final_url_prefers_last_accepted_trace_after_page(self):
        events = [
            {
                "event": "trace_added",
                "data": {
                    "accepted": True,
                    "after_page": {"url": "http://localhost:5175/contracts"},
                    "output": {"current_url": "http://localhost:5175/login"},
                },
            }
        ]

        self.assertEqual(extract_final_url(events, None), "http://localhost:5175/contracts")

    def test_numeric_expected_values_match_formatted_currency_text(self):
        assert_expected_telemetry(
            {"extracted_fields": {"amount": 680000}},
            {"output_text": "合同金额 ¥680,000.00 币种 CNY", "downloads": []},
        )

    def test_extracted_fields_do_not_pass_from_page_visible_text_only(self):
        with self.assertRaises(CaseAssertionError) as raised:
            assert_expected_telemetry(
                {"extracted_fields": {"amount": 680000}},
                {"visible_text": "合同金额 ¥680,000.00 币种 CNY", "downloads": []},
            )

        self.assertEqual(raised.exception.stage, "unsupported_output_telemetry")

    def test_contract_extract_case_starts_on_detail_without_reopening_it(self):
        case_path = Path(__file__).resolve().parent / "cases" / "contract_extract_001.yaml"
        import yaml

        case = yaml.safe_load(case_path.read_text(encoding="utf-8"))

        self.assertEqual(case["start_path"], "/contracts/CT-2026-RPA-001")
        self.assertIn("当前已经在合同详情页", case["instruction"])
        self.assertNotIn("打开合同", case["instruction"])

    def test_business_instruction_excludes_login_setup(self):
        text = build_browser_instruction(
            case={"instruction": "当前在工作台页面。请进入合同管理页面。"},
            login_url="http://localhost:5175/login",
            start_url="http://localhost:5175/dashboard",
            username="buyer",
            password="buyer123",
        )

        self.assertIn("只执行下面的业务任务", text)
        self.assertIn("当前在工作台页面。请进入合同管理页面。", text)
        self.assertNotIn("buyer123", text)
        self.assertNotIn("http://localhost:5175/login", text)

    def test_eval_auth_url_encodes_token(self):
        text = build_eval_auth_url("http://localhost:5175", "token with/slash+plus")

        self.assertEqual("http://localhost:5175/eval-auth.html?token=token%20with%2Fslash%2Bplus", text)

    def test_run_case_separates_login_setup_from_business_instruction(self):
        args = type(
            "Args",
            (),
            {
                "reset_token": "rpa-eval-reset",
                "eval_frontend_url": "http://localhost:5175",
                "case_timeout_s": 180,
            },
        )()
        case = {
            "id": "case_split",
            "name": "split",
            "tags": [],
            "user": {"username": "buyer"},
            "start_path": "/dashboard",
            "instruction": "当前在工作台页面。请进入合同管理页面。",
            "expected": {},
            "assertions": {},
        }
        eval_client = FakeRunnerEvalClient()
        rpa_client = FakeRunnerRpaClient()

        result = run_case(case, args, eval_client, rpa_client)

        self.assertTrue(result["passed"], result.get("failure_message"))
        self.assertEqual(
            [
                ("session-1", "http://localhost:5175/eval-auth.html?token=token"),
                ("session-1", "http://localhost:5175/dashboard"),
            ],
            rpa_client.navigations,
        )
        self.assertEqual(1, len(rpa_client.instructions))
        self.assertIn("只执行下面的业务任务", rpa_client.instructions[0])
        self.assertNotIn("buyer123", rpa_client.instructions[0])

    def test_case_timeout_uses_yaml_override(self):
        args = type("Args", (), {"case_timeout_s": 180})()

        self.assertEqual(resolve_case_timeout_s({"id": "simple", "timeout_s": 90}, args), 90)

    def test_case_timeout_falls_back_to_cli_default(self):
        args = type("Args", (), {"case_timeout_s": 180})()

        self.assertEqual(resolve_case_timeout_s({"id": "default"}, args), 180)

    def test_all_cases_define_reasonable_timeout(self):
        import yaml

        cases_dir = Path(__file__).resolve().parent / "cases"
        for path in cases_dir.glob("*.yaml"):
            case = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIn("timeout_s", case, path.name)
            self.assertGreater(case["timeout_s"], 0, path.name)
            self.assertLessEqual(case["timeout_s"], 240, path.name)

    def test_cases_do_not_use_visible_text_without_page_telemetry(self):
        import yaml

        offenders = []
        cases_dir = Path(__file__).resolve().parent / "cases"
        for path in cases_dir.glob("*.yaml"):
            case = yaml.safe_load(path.read_text(encoding="utf-8"))
            if (case.get("expected") or {}).get("visible_text"):
                offenders.append(path.name)

        self.assertEqual([], offenders)

    def test_download_contains_can_validate_local_xlsx_content(self):
        path = self.tmp_dir / "contracts_2026.xlsx"
        self._write_xlsx(path, [["合同编号", "供应商编号"], ["CT-2026-RPA-001", "SUP-2026-001"]])

        assert_expected_telemetry(
            {
                "download": {
                    "filename": "contracts_2026.xlsx",
                    "contains": ["CT-2026-RPA-001", "SUP-2026-001"],
                }
            },
            {"visible_text": "", "downloads": [str(path)]},
        )

    def test_download_contains_fails_without_readable_content(self):
        with self.assertRaises(CaseAssertionError) as raised:
            assert_expected_telemetry(
                {
                    "download": {
                        "filename": "contracts_2026.xlsx",
                        "contains": ["CT-2026-RPA-001"],
                    }
                },
                {"visible_text": "", "downloads": ["contracts_2026.xlsx"]},
            )

        self.assertEqual(raised.exception.stage, "unsupported_download_content_telemetry")

    def test_json_artifact_text_flattens_values_for_content_assertions(self):
        path = self.tmp_dir / "artifact.json"
        path.write_text(json.dumps({"number": "PR-2026-RPA-NEW-001", "amount": 34000}), encoding="utf-8")

        self.assertIn("PR-2026-RPA-NEW-001", read_artifact_text(path))

    def test_empty_result_accepts_agent_conclusion_text(self):
        assert_expected_telemetry(
            {"output_text": ["没有匹配结果"]},
            {"output_text": "searched_contract_number CT-2026-RPA-NOT-FOUND no_match True conclusion 没有匹配结果"},
        )

    def test_visible_text_does_not_pass_from_agent_output_only(self):
        with self.assertRaises(CaseAssertionError):
            assert_expected_telemetry(
                {"visible_text": ["没有匹配结果"]},
                {"output_text": "conclusion 没有匹配结果", "visible_text": ""},
            )

    def test_console_summary_contains_totals_and_case_rows(self):
        text = render_console_summary(
            [
                {"id": "case_a", "passed": True, "latency_ms": 1200},
                {"id": "case_b", "passed": False, "failure_stage": "assertion", "failure_message": "bad"},
            ]
        )

        self.assertIn("Total: 2", text)
        self.assertIn("Passed: 1", text)
        self.assertIn("case_a", text)
        self.assertIn("PASS", text)
        self.assertIn("case_b", text)
        self.assertIn("FAIL", text)

    def test_api_assertion_supports_absent_records(self):
        client = FakeEvalClient({"/api/purchase-orders": [{"number": "PO-2026-RPA-001"}]})

        assert_api_assertions(
            [
                {
                    "name": "new_order_not_present_before_run",
                    "path": "/api/purchase-orders",
                    "find": {"number": "PO-2026-RPA-NEW-001"},
                    "absent": True,
                }
            ],
            client,
            "token",
        )

    def test_api_assertion_supports_item_list_partial_match(self):
        client = FakeEvalClient(
            {
                "/api/purchase-requests": [
                    {
                        "number": "PR-2026-RPA-NEW-001",
                        "items": [
                            {
                                "name": "RPA采购审批机器人许可",
                                "quantity": 5,
                                "unit_price": 6800.0,
                                "cost_center": "PROC-RPA-2026",
                            }
                        ],
                    }
                ]
            }
        )

        assert_api_assertions(
            [
                {
                    "path": "/api/purchase-requests",
                    "find": {"number": "PR-2026-RPA-NEW-001"},
                    "expect": {
                        "items": [
                            {
                                "name": "RPA采购审批机器人许可",
                                "quantity": 5,
                                "unit_price": 6800.0,
                                "cost_center": "PROC-RPA-2026",
                            }
                        ]
                    },
                }
            ],
            client,
            "token",
        )

    @staticmethod
    def _write_xlsx(path: Path, rows: list[list[object]]) -> None:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        for row in rows:
            sheet.append(row)
        workbook.save(path)
        workbook.close()


class FakeEvalClient:
    def __init__(self, responses):
        self.responses = responses

    def get_json(self, path, token):
        return self.responses[path]


class FakeRunnerEvalClient:
    def reset(self, reset_token):
        self.reset_token = reset_token

    def login(self, username, password):
        self.login_args = (username, password)
        return EvalAppUserSession(username=username, token="token", user={"username": username})


class FakeRunnerRpaClient:
    def __init__(self):
        self.instructions = []
        self.navigations = []
        self.stopped = []

    def start_session(self, case_id):
        self.case_id = case_id
        return "session-1"

    def navigate(self, session_id, url):
        self.navigations.append((session_id, url))

    def chat_with_wall_timeout(self, session_id, instruction, *, timeout_s):
        self.instructions.append(instruction)
        return [{"event": "agent_done", "data": {"message": "ok"}}]

    def get_session(self, session_id):
        return {}

    def stop_session(self, session_id, *, ignore_errors=False):
        self.stopped.append((session_id, ignore_errors))


if __name__ == "__main__":
    unittest.main()
