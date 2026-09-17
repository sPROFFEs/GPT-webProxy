import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from chatgptproxy.profiles import default_profile, save_profile
from chatgptproxy.session import check, should_auth_check
from chatgptproxy.session_state import load_state


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CHATGPTPROXY_HOME"] = self.tmp.name
        self.profile = default_profile("demo")
        save_profile(self.profile)

    def tearDown(self):
        os.environ.pop("CHATGPTPROXY_HOME", None)
        self.tmp.cleanup()

    def test_check_tracks_ttl_and_ready_state(self):
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        snapshot = {
            "process_alive": True,
            "http_status": 200,
            "health": {
                "status": "healthy",
                "chrome_running": True,
                "driver_connected": True,
                "open_breakers": [],
                "last_successful_send_at": "2026-01-01T00:00:00Z",
            },
        }
        auth = {
            "authenticated": True,
            "http_status": 200,
            "token_expires_at": expiry,
            "reported_expires_at": expiry,
            "user_present": True,
            "error": None,
        }
        with patch("chatgptproxy.session.status_snapshot", return_value=snapshot), patch("chatgptproxy.session.cdp.auth_session", return_value=auth):
            state = check(self.profile, refresh=True)
        self.assertEqual(state["auth_state"], "READY")
        self.assertEqual(state["browser_state"], "READY")
        self.assertFalse(state["needs_login"])
        self.assertGreater(state["token_ttl_seconds"], 3000)
        self.assertIsNotNone(state["last_auth_refresh_at"])

    def test_auth_required_with_failed_probe_needs_login(self):
        snapshot = {
            "process_alive": True,
            "http_status": 200,
            "health": {
                "status": "degraded",
                "chrome_running": True,
                "driver_connected": True,
                "open_breakers": ["auth_required"],
            },
        }
        from chatgptproxy import cdp
        with patch("chatgptproxy.session.status_snapshot", return_value=snapshot), \
             patch("chatgptproxy.session.cdp.auth_session", side_effect=cdp.CDPError("not logged in")):
            state = check(self.profile)
        self.assertEqual(state["auth_state"], "NEEDS_LOGIN")
        self.assertTrue(state["needs_login"])

    def test_refresh_due_near_expiry(self):
        state = load_state("demo")
        state["last_auth_check_at"] = datetime.now(timezone.utc).isoformat()
        state["token_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=100)).isoformat()
        due, refresh = should_auth_check(self.profile, state)
        self.assertTrue(due)
        self.assertTrue(refresh)


if __name__ == "__main__":
    unittest.main()
