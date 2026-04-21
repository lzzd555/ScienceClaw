import importlib
import sys
import unittest
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

    def test_answer_context_query_returns_context_values_without_page_lookup(self):
        self.ledger.page_context["buyer"] = "Wrong value"

        answer = self.service.answer_context_query("buyer")

        self.assertEqual(answer, "Ada Lovelace")

    def test_collect_declared_reads_extracts_legacy_context_placeholder(self):
        reads = self.service.collect_declared_reads("Please use context:buyer before submitting.")

        self.assertEqual(reads, ["buyer"])


if __name__ == "__main__":
    unittest.main()
