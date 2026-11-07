"""Secure runner orchestration tests that do not require Windows."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from forgeos.secure_runner import run_secure_windows_test
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
        with patch("forgeos.secure_runner.os.name", "nt"), \
             patch.object(sys, "frozen", False, create=True):
            with self.assertRaisesRegex(RuntimeError, "frozen ForgeOS"):
                run_secure_windows_test(self.runtime, "python-unittest")

    def test_unsupported_preset_fails_closed_without_appcontainer_call(self):
        with patch("forgeos.secure_runner.os.name", "nt"), \
             patch.object(sys, "frozen", True, create=True), \
             patch("forgeos.secure_runner.run_secure_appcontainer") as runner:
            with self.assertRaisesRegex(RuntimeError, "does not yet support"):
                run_secure_windows_test(self.runtime, "python-pytest")
            runner.assert_not_called()

    def test_frozen_unittest_uses_appcontainer_and_owned_output_path(self):
        output = self.root / RUNTIME_DIR / "test-output.txt"
        output.write_text("all good", encoding="utf-8")
        fake = {
            "passed": True, "returncode": 0, "output": "",
            "containment": "windows-zero-capability-appcontainer+job-object",
            "capability_count": 0, "network_capabilities_granted": False,
        }
        with patch("forgeos.secure_runner.os.name", "nt"), \
             patch.object(sys, "frozen", True, create=True), \
             patch("forgeos.secure_runner.Path.resolve",
                   return_value=Path(sys.executable)), \
             patch("forgeos.secure_runner.run_secure_appcontainer",
                   return_value=fake) as runner:
            result = run_secure_windows_test(
                self.runtime, "python-unittest", timeout=17)
        self.assertTrue(result["passed"])
        self.assertEqual(result["output"], "all good")
        args, kwargs = runner.call_args
        self.assertEqual(args[1], self.root)
        self.assertEqual(args[2]["FORGEOS_TEST_OUTPUT_FILE"], str(output))
        self.assertEqual(kwargs["timeout"], 17)
