import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from chatgptproxy.gateway import _extract_content, _tool_loop
from chatgptproxy.profiles import default_profile
from chatgptproxy.workspace import apply_mode


class FakeUpstreamHandler(BaseHTTPRequestHandler):
    calls = 0
    saw_tool_result = False

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        type(self).calls += 1
        messages = body.get("messages", [])
        if type(self).calls == 1:
            content = '```chatgpt-workspace\n{"actions":[{"op":"read","path":"README.md"}]}\n```'
        else:
            type(self).saw_tool_result = any("workspace tool results" in str(m.get("content", "")) for m in messages if isinstance(m, dict))
            content = "Verified workspace and completed the task."
        response = {
            "id": "fake",
            "object": "chat.completion",
            "created": 1,
            "model": "auto",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        }
        raw = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class GatewayToolLoopTests(unittest.TestCase):
    def test_read_only_tool_loop(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "README.md").write_text("real workspace state", encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstreamHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                FakeUpstreamHandler.calls = 0
                FakeUpstreamHandler.saw_tool_result = False
                profile = default_profile("test")
                profile["upstream_port"] = server.server_address[1]
                apply_mode(profile, "read_only", root)
                request = {"model": "auto", "messages": [{"role": "user", "content": "Inspect README"}], "stream": True}
                response, steps = _tool_loop(profile, request)
                self.assertEqual(_extract_content(response), "Verified workspace and completed the task.")
                self.assertEqual(steps, 1)
                self.assertTrue(FakeUpstreamHandler.saw_tool_result)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
