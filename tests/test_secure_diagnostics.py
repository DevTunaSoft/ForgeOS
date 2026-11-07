"""Regression for preserving large frozen AppContainer unittest output."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from forgeos.secure_runner import run_secure_windows_test
from forgeos.test_runtime import TestRuntime, RUNTIME_DIR


class SecureDiagnosticTests(unittest.TestCase):
    def test_secure_test_file_is_not_silently_truncated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / RUNTIME_DIR
            folder.mkdir()
            original = "begin\n" + "diagnostic payload\n" * 2000 + "FAIL: end\n"
            (folder / "test-output.txt").write_text(original, encoding="utf-8")
            runtime = TestRuntime(path=root, env={})
            with patch("forgeos.secure_runner._is_windows", return_value=True), \
                 patch("forgeos.secure_runner.sys.frozen", True, create=True), \
                 patch("forgeos.secure_runner._bootstrap_environment", return_value={}), \
                 patch("forgeos.secure_runner.run_secure_appcontainer",
                       return_value={"passed": False, "returncode": 9, "output": "fallback"}) as run:
                result = run_secure_windows_test(runtime, "python-unittest")
            self.assertEqual(result["output"], original)
            self.assertEqual(result["returncode"], 9)
            self.assertFalse(result["passed"])
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
