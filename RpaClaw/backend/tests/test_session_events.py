import importlib
import unittest
from unittest.mock import patch


SESSIONS_MODULE = importlib.import_module("backend.route.sessions")


class SessionEventTests(unittest.TestCase):
    def test_user_message_event_uses_a_fresh_event_id(self):
        with patch.object(SESSIONS_MODULE, "_new_event_id", return_value="fresh-event-id"):
            event = SESSIONS_MODULE._create_user_message_event(
                message="next user message",
                attachments=["file-1"],
                timestamp=123,
            )

        self.assertEqual(event["event"], "message")
        self.assertEqual(event["data"]["event_id"], "fresh-event-id")
        self.assertEqual(event["data"]["timestamp"], 123)
        self.assertEqual(event["data"]["content"], "next user message")
        self.assertEqual(event["data"]["role"], "user")
        self.assertEqual(event["data"]["attachments"], ["file-1"])


if __name__ == "__main__":
    unittest.main()
