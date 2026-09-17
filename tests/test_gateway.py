import unittest
from unittest.mock import MagicMock

from chatgptproxy.gateway import (
    GatewayHandler,
    _extract_content,
    _replace_content,
    _sse_chunks,
)


class GatewayTests(unittest.TestCase):
    def test_replace_content(self):
        payload = {"id": "x", "model": "auto", "choices": [{"message": {"role": "assistant", "content": "old"}}]}
        updated = _replace_content(payload, "new")
        self.assertEqual(_extract_content(updated), "new")
        self.assertEqual(_extract_content(payload), "old")

    def test_sse_has_done_and_fluid_chunks(self):
        payload = {"id": "x", "model": "auto", "created": 1}
        chunks = _sse_chunks(payload, "hello world from chatgptproxy gateway")
        self.assertEqual(chunks[-1], b"data: [DONE]\n\n")
        self.assertIn(b"hello", b"".join(chunks))
        self.assertTrue(len(chunks) >= 3)

    def test_authorized_timing_safe(self):
        handler = MagicMock(spec=GatewayHandler)
        handler.profile = {"api_key": "secret_key_123"}
        handler.headers = {"Authorization": "Bearer secret_key_123"}
        self.assertTrue(GatewayHandler._authorized(handler))
        handler.headers = {"Authorization": "Bearer wrong_key"}
        self.assertFalse(GatewayHandler._authorized(handler))

    def test_validate_host_and_origin(self):
        handler = MagicMock(spec=GatewayHandler)
        handler.profile = {"host": "127.0.0.1", "port": 1235}

        # Valid loopback hosts
        for valid_host in ("127.0.0.1:1235", "localhost:1235", "[::1]:1235"):
            handler.headers = {"Host": valid_host}
            self.assertTrue(GatewayHandler._validate_host_and_origin(handler))

        # Invalid host
        handler.headers = {"Host": "evil.invalid"}
        self.assertFalse(GatewayHandler._validate_host_and_origin(handler))

        # Rejected origin
        handler.headers = {"Host": "127.0.0.1:1235", "Origin": "https://evil.invalid"}
        self.assertFalse(GatewayHandler._validate_host_and_origin(handler))


if __name__ == "__main__":
    unittest.main()
