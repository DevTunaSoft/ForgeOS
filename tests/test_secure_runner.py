"""Secure runner orchestration tests that do not require Windows."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from forgeos.secure_runner import (
    _bootstrap_environment, run_secure_windows_test,
)
from forgeos.test_runtime import TestRuntime, RUNTIME_DIR


class SecureRunnerContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / RUNTIME_DIR).mkdir()
        self.runtime = TestRuntime(
            path=self.root,
            env={"FORGEOS_EXECUTION_SNAPSHOT": "1", "TEMP": str(self.root)})

    def test_requires_frozen_forgeos_runtime(self):
        with patch("forgeos.secure_runner._is_windows", return_value=True), \
             patch.object(sys, "frozen", False, create=True):
            with self.assertRaisesRegex(RuntimeError, "frozen ForgeOS"):
                run_secure_windows_test(self.runtime, "python-unittest")

    def test_unsupported_preset_fails_closed_without_appcontainer_call(self):
        with patch("forgeos.secure_runner._is_windows", return_value=True), \
             patch.object(sys, "frozen", True, create=True), \
             patch("forgeos.secure_runner.run_secure_appcontainer") as runner:
            with self.assertRaisesRegex(RuntimeError, "does not yet support"):
                run_secure_windows_test(self.runtime, "npm-test")
            runner.assert_not_called()

    def test_symlinked_output_is_rejected_before_appcontainer_launch(self):
        source = self.root / "original.txt"
        source.write_text("unchanged")
        output = self.root / RUNTIME_DIR / "test-output.txt"
        try:
            output.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("File symlinks unavailable")
        with patch("forgeos.secure_runner._is_windows", return_value=True), \
             patch.object(sys, "frozen", True, create=True), \
             patch("forgeos.secure_runner.run_secure_appcontainer") as launch:
            with self.assertRaisesRegex(ValueError, "link or special"):
                run_secure_windows_test(self.runtime, "python-unittest")
        launch.assert_not_called()
        self.assertEqual(source.read_text(), "unchanged")

    def test_bootstrap_environment_adds_only_localappdata_marker(self):
        with patch("forgeos.secure_runner._host_local_appdata",
                   return_value=r"C:\Users\Example\AppData\Local"):
            env = _bootstrap_environment(self.runtime)
        self.assertEqual(
            env["LOCALAPPDATA"], r"C:\Users\Example\AppData\Local")
        self.assertEqual(env["FORGEOS_BOOTSTRAP_LOCALAPPDATA"], "1")
        self.assertEqual(env["FORGEOS_EXECUTION_SNAPSHOT"], "1")
        self.assertNotIn("APPDATA", env)

    def test_frozen_pytest_watches_both_owned_files_and_keeps_appcontainer(self):
        output = self.root / RUNTIME_DIR / "test-output.txt"
        logfile = self.root / RUNTIME_DIR / "pytest-log.txt"
        output.write_text("1 passed", encoding="utf-8")
        fake = {
            "passed": True, "returncode": 0, "output": "",
            "capture_complete": True,
            "containment": "windows-zero-capability-appcontainer+job-object",
            "capability_count": 0, "network_capabilities_granted": False,
        }
        with patch("forgeos.secure_runner._is_windows", return_value=True), \
             patch.object(sys, "frozen", True, create=True), \
             patch("forgeos.secure_runner._host_local_appdata",
                   return_value=r"C:\Users\Example\AppData\Local"), \
             patch("forgeos.secure_runner.run_secure_appcontainer",
                   return_value=fake) as runner:
            result = run_secure_windows_test(self.runtime, "python-pytest")
        self.assertTrue(result["passed"])
        args, options = runner.call_args
        self.assertEqual(args[0][1], "--sandbox-pytest")
        self.assertEqual(args[2]["FORGEOS_TEST_LOG_FILE"], str(logfile))
        self.assertEqual(options["watched_output"], output)
        self.assertEqual(options["watched_extra_outputs"], (logfile,))

    def test_missing_secure_output_can_never_approve_windows_test(self):
        # A zero exit code is not proof that the sandbox produced test
        # evidence. The missing-output case must block autonomous recovery.
        fake = {
            "passed": True, "returncode": 0, "output": "child exited",
            "capture_complete": True,
        }
        with patch("forgeos.secure_runner._is_windows", return_value=True), \
             patch.object(sys, "frozen", True, create=True), \
             patch("forgeos.secure_runner._host_local_appdata",
                   return_value=r"C:\Users\Example\AppData\Local"), \
             patch("forgeos.secure_runner.run_secure_appcontainer",
                   return_value=fake):
            result = run_secure_windows_test(self.runtime, "python-unittest")
        self.assertFalse(result["passed"])
        self.assertFalse(result["capture_complete"])
        self.assertEqual(
            result["capture_error"], "secure-test-output-unavailable")
        self.assertIsNone(result["evidence_reference"])
        self.assertIn("not produced", result["output"])
        self.assertEqual(result["returncode"], 0)

    def test_unreadable_secure_output_can_never_approve_windows_test(self):
        output = self.root / RUNTIME_DIR / "test-output.txt"
        output.write_text("tests passed", encoding="utf-8")
        fake = {"passed": True, "returncode": 0,
                "output": "", "capture_complete": True}
        with patch("forgeos.secure_runner._is_windows", return_value=True), \
             patch.object(sys, "frozen", True, create=True), \
             patch("forgeos.secure_runner._host_local_appdata",
                   return_value=r"C:\Users\Example\AppData\Local"), \
             patch("forgeos.secure_runner.run_secure_appcontainer",
                   return_value=fake), \
             patch.object(Path, "open", side_effect=PermissionError(
                 "simulated output read denial")):
            result = run_secure_windows_test(self.runtime, "python-unittest")
        self.assertFalse(result["passed"])
        self.assertFalse(result["capture_complete"])
        self.assertEqual(
            result["capture_error"], "secure-test-output-unavailable")
        self.assertIn("simulated output read denial", result["output"])
        self.assertIsNone(result["evidence_reference"])

    def test_secure_output_substitution_after_launch_fails_closed(self):
        outside = self.root / "private-original.txt"
        outside.write_text("outside data")
        output = self.root / RUNTIME_DIR / "test-output.txt"
        output.write_text("previous result")

        def swap_output(*args, **kwargs):
            output.unlink()
            output.symlink_to(outside)
            return {"passed": True, "returncode": 0, "output": "",
                    "capture_complete": True}

        try:
            with patch("forgeos.secure_runner._is_windows", return_value=True), \
                 patch.object(sys, "frozen", True, create=True), \
                 patch("forgeos.secure_runner._host_local_appdata",
                       return_value=r"C:\Users\Example\AppData\Local"), \
                 patch("forgeos.secure_runner.run_secure_appcontainer",
                       side_effect=swap_output):
                result = run_secure_windows_test(
                    self.runtime, "python-unittest")
        except (OSError, NotImplementedError) as exc:
            if not output.is_symlink():
                self.skipTest(f"File symlinks unavailable: {exc}")
            raise
        self.assertFalse(result["passed"])
        self.assertFalse(result["capture_complete"])
        self.assertEqual(
            result["capture_error"], "secure-test-output-unavailable")
        self.assertEqual(outside.read_text(), "outside data")

    def test_symlinked_pytest_log_refused_before_launch(self):
        outside = self.root / "outside.txt"
        outside.write_text("safe")
        logfile = self.root / RUNTIME_DIR / "pytest-log.txt"
        try:
            logfile.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with patch("forgeos.secure_runner._is_windows", return_value=True), \
             patch.object(sys, "frozen", True, create=True), \
             patch("forgeos.secure_runner.run_secure_appcontainer") as runner:
            with self.assertRaisesRegex(ValueError, "link or special"):
                run_secure_windows_test(self.runtime, "python-pytest")
        runner.assert_not_called()
        self.assertEqual(outside.read_text(), "safe")

    def test_frozen_unittest_uses_appcontainer_and_owned_output_path(self):
        output = self.root / RUNTIME_DIR / "test-output.txt"
        output.write_text("all good", encoding="utf-8")
        fake = {
            "passed": True, "returncode": 0, "output": "",
            "containment": "windows-zero-capability-appcontainer+job-object",
            "capability_count": 0, "network_capabilities_granted": False,
        }
        with patch("forgeos.secure_runner._is_windows", return_value=True), \
             patch.object(sys, "frozen", True, create=True), \
             patch("forgeos.secure_runner._host_local_appdata",
                   return_value=r"C:\Users\Example\AppData\Local"), \
             patch("forgeos.secure_runner.run_secure_appcontainer",
                   return_value=fake) as runner:
            result = run_secure_windows_test(
                self.runtime, "python-unittest", timeout=17)
        self.assertTrue(result["passed"])
        self.assertEqual(result["output"], "all good")
        args, kwargs = runner.call_args
        self.assertEqual(args[1], self.root)
        self.assertEqual(args[2]["FORGEOS_TEST_OUTPUT_FILE"], str(output))
        self.assertEqual(
            args[2]["LOCALAPPDATA"], r"C:\Users\Example\AppData\Local")
        self.assertEqual(
            args[2]["FORGEOS_BOOTSTRAP_LOCALAPPDATA"], "1")
        self.assertEqual(kwargs["timeout"], 17)
        self.assertEqual(kwargs["watched_output"], output)
