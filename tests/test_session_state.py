import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from chatgptproxy.session_state import load_state, save_state, ttl_seconds


class SessionStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CHATGPTPROXY_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("CHATGPTPROXY_HOME", None)
        self.tmp.cleanup()

    def test_state_round_trip(self):
        state = load_state("demo")
        state["auth_state"] = "READY"
        state["token_ttl_seconds"] = 123
        save_state("demo", state)
        loaded = load_state("demo")
        self.assertEqual(loaded["auth_state"], "READY")
        self.assertEqual(loaded["token_ttl_seconds"], 123)

    def test_ttl_seconds(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        expiry = now + timedelta(minutes=10)
        self.assertEqual(ttl_seconds(expiry.isoformat(), now=now), 600)


if __name__ == "__main__":
    unittest.main()
