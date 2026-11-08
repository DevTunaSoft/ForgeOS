"""Sandbox child runner safety contract tests."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

from forgeos.sandbox_runner import run_unittest, run_pytest


def _secure_env(root: Path, output: Path) -> dict[str, str]:
    return {
        "FORGEOS_EXECUTION_SNAPSHOT": "1",
        "FORGEOS_TEST_OUTPUT_FILE": str(output),
        "FORGEOS_BOOTSTRAP_LOCALAPPDATA": "1",
        "LOCALAPPDATA": str(root.parent / "host-local-appdata"),
    }


class SandboxRunnerTests(unittest.TestCase):
    def test_refuses_without_snapshot_marker(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(run_unittest(), 90)

    def test_writes_results_only_inside_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = root / "test_ok.py"
            test_file.write_text(
                "import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_ok(self): self.assertTrue(True)\n",
                encoding="utf-8")
            output = root / ".forgeos-runtime" / "out.txt"
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, _secure_env(root, output), clear=True):
                    self.assertEqual(run_unittest(), 0)
                    self.assertNotIn(
                        "FORGEOS_BOOTSTRAP_LOCALAPPDATA", os.environ)
                    self.assertTrue(
                        Path(os.environ["LOCALAPPDATA"]).resolve().is_relative_to(root.resolve()))
                    self.assertTrue(
                        Path(os.environ["APPDATA"]).resolve().is_relative_to(root.resolve()))
            finally:
                os.chdir(previous)
            self.assertIn("OK", output.read_text(encoding="utf-8"))

    def test_pytest_uses_owned_logfile_without_windows_nul(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / ".forgeos-runtime" / "test-output.txt"
            logs = root / ".forgeos-runtime" / "pytest-log.txt"
            output.parent.mkdir()
            calls = []
            fake = SimpleNamespace(main=lambda args: calls.append(args) or 0)
            previous = Path.cwd()
            try:
                os.chdir(root)
                env = _secure_env(root, output)
                env["FORGEOS_TEST_LOG_FILE"] = str(logs)
                with patch.dict(os.environ, env, clear=True), \
                     patch.dict(sys.modules, {"pytest": fake}):
                    self.assertEqual(run_pytest(), 0)
                    self.assertNotIn("FORGEOS_BOOTSTRAP_LOCALAPPDATA", os.environ)
                    self.assertTrue(
                        Path(os.environ["APPDATA"]).resolve().is_relative_to(root))
            finally:
                os.chdir(previous)
            self.assertEqual(len(calls), 1)
            self.assertIn("--log-file=" + str(logs), calls[0])
            self.assertIn("--log-file-mode=w", calls[0])
            self.assertNotIn("-p no:logging", calls[0])
            self.assertIn("--capture=no", calls[0])

    def test_pytest_refuses_external_log_path_before_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / ".forgeos-runtime" / "test-output.txt"
            output.parent.mkdir()
            previous = Path.cwd()
            try:
                os.chdir(root)
                env = _secure_env(root, output)
                env["FORGEOS_TEST_LOG_FILE"] = str(root.parent / "outside.txt")
                with patch.dict(os.environ, env, clear=True), \
                     patch.dict(sys.modules, {"pytest": SimpleNamespace(main=lambda args: 0)}):
                    self.assertEqual(run_pytest(), 92)
            finally:
                os.chdir(previous)
            self.assertIn("log path is invalid",
                          output.read_text(encoding="utf-8"))

    def test_rejects_output_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / (root.name + "-outside.txt")
            self.addCleanup(lambda: outside.unlink(missing_ok=True))
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(
                        os.environ, _secure_env(root, outside), clear=True):
                    self.assertEqual(run_unittest(), 92)
            finally:
                os.chdir(previous)
            self.assertFalse(outside.exists())

    def test_refuses_missing_bootstrap_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / ".forgeos-runtime" / "out.txt"
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {
                    "FORGEOS_EXECUTION_SNAPSHOT": "1",
                    "FORGEOS_TEST_OUTPUT_FILE": str(output),
                }, clear=True):
                    self.assertEqual(run_unittest(), 92)
            finally:
                os.chdir(previous)
            self.assertIn(
                "bootstrap marker",
                output.read_text(encoding="utf-8").lower())
