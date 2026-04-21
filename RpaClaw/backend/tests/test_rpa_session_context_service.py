import importlib
import sys
import unittest
import subprocess
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

CONTEXT_LEDGER_MODULE = importlib.import_module("backend.rpa.context_ledger")
SESSION_CONTEXT_SERVICE_MODULE = importlib.import_module("backend.rpa.session_context_service")


class SessionContextServiceTests(unittest.TestCase):
    def setUp(self):
        self.ledger = CONTEXT_LEDGER_MODULE.TaskContextLedger()
        self.ledger.record_value("observed", "buyer", "Ada Lovelace")
        self.ledger.record_value("derived", "purchase_order", "PO-2048")
        self.service = SESSION_CONTEXT_SERVICE_MODULE.SessionContextService(self.ledger)

    def test_build_current_context_merges_observed_and_derived_values(self):
        context = self.service.build_current_context()

        self.assertEqual(
            context,
            {
                "buyer": "Ada Lovelace",
                "purchase_order": "PO-2048",
            },
        )

    def test_answer_context_query_returns_structured_payload_for_all_context(self):
        answer = self.service.answer_context_query("现在上下文中的所有内容有哪些")

        self.assertEqual(answer["mode"], "all")
        self.assertEqual(
            answer["values"],
            {
                "buyer": "Ada Lovelace",
                "purchase_order": "PO-2048",
            },
        )
        self.assertIn("buyer: Ada Lovelace", answer["text"])
        self.assertIn("purchase_order: PO-2048", answer["text"])

    def test_answer_context_query_returns_structured_payload_for_legacy_placeholder(self):
        self.ledger.page_context["buyer"] = "Wrong value"

        answer = self.service.answer_context_query("context:buyer")

        self.assertEqual(answer["mode"], "key")
        self.assertEqual(answer["values"], {"buyer": "Ada Lovelace"})
        self.assertEqual(answer["text"], "buyer: Ada Lovelace")

    def test_answer_context_query_returns_all_matching_values_for_multi_key_query(self):
        answer = self.service.answer_context_query("context:buyer and context:purchase_order")

        self.assertEqual(answer["mode"], "keys")
        self.assertEqual(
            answer["values"],
            {
                "buyer": "Ada Lovelace",
                "purchase_order": "PO-2048",
            },
        )
        self.assertIn("buyer: Ada Lovelace", answer["text"])
        self.assertIn("purchase_order: PO-2048", answer["text"])

    def test_maybe_answer_context_query_matches_context_key_question_without_legacy_placeholder(self):
        answer = self.service.maybe_answer_context_query("当前记录的 purchase_order 是什么？")

        self.assertIsNotNone(answer)
        self.assertEqual(answer["mode"], "key")
        self.assertEqual(answer["values"], {"purchase_order": "PO-2048"})
        self.assertEqual(answer["text"], "purchase_order: PO-2048")

    def test_collect_declared_reads_accepts_explicit_reads_and_legacy_placeholder(self):
        reads = self.service.collect_declared_reads(
            ["buyer", "supplier"],
            legacy_text="Please use context:buyer before submitting.",
        )

        self.assertEqual(reads, ["buyer", "supplier"])

    def test_collect_declared_reads_normalizes_legacy_placeholder_entries(self):
        reads = self.service.collect_declared_reads(
            ["context:buyer", "supplier", "context:buyer"],
            legacy_text="Please also use context:purchase_order before submitting.",
        )

        self.assertEqual(reads, ["buyer", "supplier", "purchase_order"])

    def test_capture_runtime_contract_records_explicit_writes_from_context_diff(self):
        contract = self.service.capture_runtime_contract(
            before_context={"buyer": "Ada Lovelace", "status": "draft"},
            after_context={"buyer": "Ada Lovelace", "status": "submitted", "purchase_order": "PO-2048"},
            declared_reads=["context:buyer"],
        )

        self.assertEqual(contract.reads, ["buyer"])
        self.assertEqual(contract.writes, ["status", "purchase_order"])
        self.assertEqual(
            contract.updates,
            {
                "status": "submitted",
                "purchase_order": "PO-2048",
            },
        )


class StepContextContractTests(unittest.TestCase):
    def test_step_context_contract_exposes_forward_fields(self):
        contract = SESSION_CONTEXT_SERVICE_MODULE.StepContextContract(
            reads=["buyer"],
            writes=["purchase_order"],
            updates={"buyer": "Ada Lovelace"},
        )

        self.assertEqual(contract.reads, ["buyer"])
        self.assertEqual(contract.writes, ["purchase_order"])
        self.assertEqual(contract.updates, {"buyer": "Ada Lovelace"})


class RpaPackageImportTests(unittest.TestCase):
    def test_importing_session_context_service_does_not_load_runtime_stack(self):
        script = (
            "import importlib, sys; "
            f"sys.path.insert(0, {repr(str(BACKEND_ROOT))}); "
            "importlib.import_module('backend.rpa.session_context_service'); "
            "print('backend.rpa.manager' in sys.modules, 'backend.rpa.cdp_connector' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.stdout.strip(), "False False")


if __name__ == "__main__":
    unittest.main()
