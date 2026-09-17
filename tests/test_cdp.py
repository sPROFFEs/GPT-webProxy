import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from chatgptproxy import cdp


def jwt_with_exp(exp: int) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip('=')
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip('=')
    return f"{header}.{payload}.x"


class CDPTests(unittest.TestCase):
    def test_auth_session_returns_metadata_without_token(self):
        exp = int((datetime.now(timezone.utc) + timedelta(minutes=30)).timestamp())
        target = {"type": "page", "url": "https://chatgpt.com/", "webSocketDebuggerUrl": "ws://example"}
        evaluated = {
            "ok": True,
            "status": 200,
            "body": {
                "accessToken": jwt_with_exp(exp),
                "expires": "2026-12-01T00:00:00Z",
                "user": {"email": "not-persisted@example.invalid"},
            },
        }
        with patch("chatgptproxy.cdp.targets", return_value=[target]), patch("chatgptproxy.cdp._evaluate", return_value=evaluated):
            result = cdp.auth_session(9232)
        self.assertTrue(result["authenticated"])
        self.assertNotIn("accessToken", result)
        self.assertNotIn("user", result)
        self.assertIsNotNone(result["token_expires_at"])
        self.assertTrue(result["user_present"])

    def test_select_chatgpt_target_prefers_chatgpt(self):
        items = [
            {"type": "page", "url": "https://example.com/", "webSocketDebuggerUrl": "ws://1"},
            {"type": "page", "url": "https://chatgpt.com/c/abc", "webSocketDebuggerUrl": "ws://2"},
        ]
        self.assertEqual(cdp.select_chatgpt_target(items)["webSocketDebuggerUrl"], "ws://2")

    def test_select_chatgpt_target_matches_auth_domains(self):
        items = [
            {"type": "page", "url": "https://example.com/", "webSocketDebuggerUrl": "ws://1"},
            {"type": "page", "url": "https://auth.openai.com/login", "webSocketDebuggerUrl": "ws://auth"},
        ]
        self.assertEqual(cdp.select_chatgpt_target(items)["webSocketDebuggerUrl"], "ws://auth")


if __name__ == "__main__":
    unittest.main()
