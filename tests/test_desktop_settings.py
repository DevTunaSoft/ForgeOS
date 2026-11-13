import json
import tempfile
import unittest
from pathlib import Path

from forgeos.desktop_settings import load_settings, save_settings


class DesktopSettingsTests(unittest.TestCase):
    def test_round_trip_and_excludes_unknown_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(path, {"model": "qwen2.5-coder:3b",
                                 "backend": "ollama", "repair_count": 2,
                                 "allow_host_tests": True, "api_key": "secret",
                                 "startup_resume_workspace": directory})
            result = load_settings(path)
            self.assertEqual(result["model"], "qwen2.5-coder:3b")
            self.assertNotIn("allow_host_tests", result)
            self.assertEqual(result["startup_resume_workspace"], directory)
            self.assertNotIn("api_key", path.read_text())
            self.assertEqual(json.loads(path.read_text())["backend"], "ollama")

    def test_invalid_and_oversized_settings_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_settings(path), {})
            path.write_text("x" * 16385, encoding="utf-8")
            self.assertEqual(load_settings(path), {})

    def test_embedded_nul_is_never_saved_or_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for key in ("project", "model", "endpoint", "workspaces", "task_role",
                        "startup_resume_workspace"):
                with self.subTest(setting=key):
                    save_settings(path, {key: "before\x00after"})
                    self.assertNotIn(key, load_settings(path))
                    # Also reject settings written by older versions.
                    path.write_text(json.dumps({key: "before\x00after"}), encoding="utf-8")
                    self.assertNotIn(key, load_settings(path))
            # Printable backslash-x text is not a NUL control character.
            save_settings(path, {"project": r"literal\x00text"})
            self.assertEqual(load_settings(path)["project"], r"literal\x00text")

    def test_invalid_values_not_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings(path, {"backend": "remote", "repair_count": 999,
                                 "model": "ok"})
            self.assertEqual(load_settings(path), {"model": "ok"})


if __name__ == "__main__":
    unittest.main()
