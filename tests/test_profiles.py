import os
import tempfile
import unittest

from chatgptproxy.profiles import clone_profile, default_profile, load_profile, save_profile, upstream_config


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CHATGPTPROXY_HOME"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("CHATGPTPROXY_HOME", None)
        self.tmp.cleanup()

    def test_save_load_and_config(self):
        p = default_profile("dev")
        p["port"] = 4444
        save_profile(p)
        loaded = load_profile("dev")
        self.assertEqual(loaded["port"], 4444)
        cfg = upstream_config(loaded)
        self.assertEqual(cfg["api_keys"], [loaded["api_key"]])
        self.assertTrue(cfg["user_data_dir"].endswith("browser/dev"))

    def test_clone_rotates_key(self):
        p = default_profile("a")
        save_profile(p)
        b = clone_profile("a", "b")
        self.assertNotEqual(p["api_key"], b["api_key"])
        self.assertEqual(load_profile("b")["name"], "b")


if __name__ == "__main__":
    unittest.main()
