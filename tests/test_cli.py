import unittest

from chatgptproxy.cli import build_parser


class CliTests(unittest.TestCase):
    def test_status_parse(self):
        args = build_parser().parse_args(["status", "demo", "--json"])
        self.assertEqual(args.profile, "demo")
        self.assertTrue(args.json)

    def test_profile_clone_parse(self):
        args = build_parser().parse_args(["profile", "clone", "a", "b"])
        self.assertEqual(args.source, "a")
        self.assertEqual(args.target, "b")

    def test_auth_refresh_parse(self):
        args = build_parser().parse_args(["auth", "refresh", "demo"])
        self.assertEqual(args.auth_cmd, "refresh")
        self.assertEqual(args.profile, "demo")

    def test_session_keepalive_parse(self):
        args = build_parser().parse_args(["session", "keepalive", "demo", "--once"])
        self.assertEqual(args.session_cmd, "keepalive")
        self.assertTrue(args.once)

    def test_configure_full_parse(self):
        args = build_parser().parse_args(["configure", "demo", "--mode", "full_workspace", "--workspace", "/tmp/project"])
        self.assertEqual(args.profile, "demo")
        self.assertEqual(args.mode, "full_workspace")
        self.assertEqual(args.workspace, "/tmp/project")

    def test_auth_login_parse(self):
        args = build_parser().parse_args(["auth", "login", "demo"])
        self.assertEqual(args.auth_cmd, "login")
        self.assertEqual(args.profile, "demo")

    def test_workspace_test_parse(self):
        args = build_parser().parse_args(["workspace", "test", "demo"])
        self.assertEqual(args.workspace_cmd, "test")
        self.assertEqual(args.profile, "demo")


if __name__ == "__main__":
    unittest.main()
