import os
import sys
import tempfile
import unittest
from pathlib import Path

from chatgptproxy.profiles import default_profile
from chatgptproxy.workspace import (
    WorkspaceError,
    WorkspaceExecutor,
    apply_mode,
    parse_tool_request,
)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "README.md").write_text("hello workspace\nsecond line\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def profile(self, mode="full_workspace"):
        p = default_profile("test")
        apply_mode(p, mode, str(self.root))
        return p

    def test_apply_modes(self):
        p = self.profile("full_workspace")
        self.assertTrue(p["workspace_write_enabled"])
        self.assertTrue(p["exec_enabled"])
        apply_mode(p, "read_only", str(self.root))
        self.assertFalse(p["workspace_write_enabled"])
        self.assertFalse(p["exec_enabled"])

    def test_read_and_path_escape(self):
        ex = WorkspaceExecutor(self.profile("read_only"))
        result = ex.read("README.md")
        self.assertIn("hello workspace", result["content"])
        with self.assertRaises(WorkspaceError):
            ex.read("../outside.txt")

    def test_read_only_rejects_write(self):
        ex = WorkspaceExecutor(self.profile("read_only"))
        with self.assertRaises(WorkspaceError):
            ex.write("new.txt", "no")

    def test_full_write_and_exec(self):
        ex = WorkspaceExecutor(self.profile("full_workspace"))
        ex.write("sub/new.txt", "created")
        self.assertEqual((self.root / "sub/new.txt").read_text(), "created")
        result = ex.exec([sys.executable, "-c", "print('ok')"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("ok", result["output"])

    def test_guarded_exec_allows_sh_and_bash(self):
        ex = WorkspaceExecutor(self.profile("full_workspace"))
        result = ex.exec(["sh", "-c", "echo test_ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("test_ok", result["output"])

    def test_guarded_exec_rejects_unknown_binary(self):
        ex = WorkspaceExecutor(self.profile("full_workspace"))
        with self.assertRaises(WorkspaceError):
            ex.exec(["definitely-not-allowed", "x"])

    def test_parse_tool_request(self):
        content = '```chatgpt-workspace\n{"actions":[{"op":"read","path":"README.md"}]}\n```'
        actions, error = parse_tool_request(content)
        self.assertIsNone(error)
        self.assertEqual(actions[0]["op"], "read")

    def test_malformed_tool_request(self):
        actions, error = parse_tool_request("```chatgpt-workspace\n{broken}\n```")
        self.assertIsNone(actions)
        self.assertIn("invalid JSON", error)


if __name__ == "__main__":
    unittest.main()
