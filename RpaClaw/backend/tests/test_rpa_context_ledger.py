import importlib
import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

CONTEXT_LEDGER_MODULE = importlib.import_module("backend.rpa.context_ledger")


class TaskContextLedgerShouldPromoteValueTests(unittest.TestCase):
    """Tests for TaskContextLedger.should_promote_value rules.

    These tests define the context-ledger promotion rules:
    - Explicit user-requested extractions are always promoted.
    - Non-essential observations are never promoted.
    - Cross-page runtime dependencies are promoted.
    """

    def setUp(self):
        self.ledger = CONTEXT_LEDGER_MODULE.TaskContextLedger()

    def test_explicit_user_requested_extraction_is_promoted(self):
        """When user_explicit=True, should_promote_value returns True."""
        result = self.ledger.should_promote_value(
            key="order_id",
            source="dom_extraction",
            user_explicit=True,
            runtime_required=False,
            consumed_later=False,
        )
        self.assertTrue(result)

    def test_nonessential_observation_is_not_promoted(self):
        """When source=observation, user_explicit=False, runtime_required=False,
        consumed_later=False, should_promote_value returns False."""
        result = self.ledger.should_promote_value(
            key="background_color",
            source="observation",
            user_explicit=False,
            runtime_required=False,
            consumed_later=False,
        )
        self.assertFalse(result)

    def test_cross_page_runtime_dependency_is_promoted(self):
        """When runtime_required=True and consumed_later=True,
        should_promote_value returns True."""
        result = self.ledger.should_promote_value(
            key="csrf_token",
            source="dom_extraction",
            user_explicit=False,
            runtime_required=True,
            consumed_later=True,
        )
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
