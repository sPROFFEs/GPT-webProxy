import os
import tempfile
import unittest
from unittest.mock import patch

from chatgptproxy.profiles import default_profile, save_profile
from chatgptproxy.session_state import load_state
from chatgptproxy.supervisor import tick


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CHATGPTPROXY_HOME"] = self.tmp.name
        self.profile = default_profile("demo")
        save_profile(self.profile)

    def tearDown(self):
        os.environ.pop("CHATGPTPROXY_HOME", None)
        self.tmp.cleanup()

    def test_auth_required_does_not_restart_browser_when_login_missing(self):
        snapshot = {
            "process_alive": True,
            "http_status": 200,
            "health": {
                "status": "degraded",
                "open_breakers": ["auth_required"],
                "chrome_running": True,
                "driver_connected": True,
            },
        }
        state_after_check = load_state("demo")
        state_after_check.update({"auth_state": "NEEDS_LOGIN", "needs_login": True, "open_breakers": ["auth_required"]})
        with patch("chatgptproxy.supervisor.status_snapshot", return_value=snapshot), \
             patch("chatgptproxy.supervisor.should_auth_check", return_value=(True, True)), \
             patch("chatgptproxy.supervisor.check", return_value=state_after_check), \
             patch("chatgptproxy.supervisor.recover_managed_stack") as recover:
            tick(self.profile)
        recover.assert_not_called()

    def test_dead_managed_rest_is_recovered(self):
        snapshot = {"process_alive": False, "http_status": None, "health_error": "connection refused"}
        with patch("chatgptproxy.supervisor.status_snapshot", return_value=snapshot), \
             patch("chatgptproxy.supervisor.should_auth_check", return_value=(False, False)), \
             patch("chatgptproxy.supervisor.recover_managed_stack", return_value={"rest_pid": 123}) as recover:
            tick(self.profile)
        recover.assert_called_once()

    def test_valid_browser_session_reconciles_auth_required_breaker(self):
        snapshot = {
            "process_alive": True,
            "http_status": 200,
            "health": {
                "status": "degraded",
                "open_breakers": ["auth_required"],
                "chrome_running": True,
                "driver_connected": True,
            },
        }
        before = load_state("demo")
        before.update({"auth_state": "READY", "needs_login": False, "open_breakers": ["auth_required"]})
        after = dict(before)
        after["open_breakers"] = []
        with patch("chatgptproxy.supervisor.status_snapshot", return_value=snapshot), \
             patch("chatgptproxy.supervisor.should_auth_check", return_value=(True, True)), \
             patch("chatgptproxy.supervisor.check", side_effect=[before, after]), \
             patch("chatgptproxy.supervisor.run_ensure", return_value=0) as ensure, \
             patch("chatgptproxy.supervisor.recover_managed_stack") as recover:
            state = tick(self.profile)
        ensure.assert_called_once()
        recover.assert_not_called()
        self.assertFalse(state.get("needs_login"))


if __name__ == "__main__":
    unittest.main()
