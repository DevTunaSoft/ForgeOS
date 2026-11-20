"""Linux-runnable checks for the packaged self-test's staging and parsing."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from forgeos import self_test


class SelfTestStagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="forgeos-self-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = self.tmp / "tests"
        (self.source / "helpers").mkdir(parents=True)
        (self.source / "test_keep.py").write_text("x = 1\n", encoding="utf-8")
        (self.source / "test_documentation.py").write_text("x = 2\n", encoding="utf-8")
        (self.source / "helpers" / "fixture.txt").write_text("data", encoding="utf-8")
        (self.source / "__pycache__").mkdir()
        (self.source / "__pycache__" / "x.pyc").write_bytes(b"0")

    def test_stage_copies_runnable_modules_and_excludes_repository_readers(self):
        target = self.tmp / "workspace"
        staged = self_test.stage_test_modules(self.source, target)
        self.assertEqual(staged["modules_run"], ["test_keep.py"])
        self.assertIn("test_documentation.py", staged["modules_excluded"])
        self.assertTrue((target / "test_keep.py").is_file())
        self.assertTrue((target / "helpers" / "fixture.txt").is_file())
        self.assertFalse((target / "test_documentation.py").exists())
        self.assertFalse((target / "__pycache__").exists())

    def test_stage_refuses_an_existing_target(self):
        target = self.tmp / "workspace"
        target.mkdir()
        with self.assertRaises(FileExistsError):
            self_test.stage_test_modules(self.source, target)


class SelfTestParsingTests(unittest.TestCase):
    def test_parses_ok_and_counts(self):
        text = "...\n----\nRan 61 tests in 12.5s\n\nOK (skipped=3)\n"
        self.assertEqual(
            self_test.parse_unittest_output(text),
            {"tests_run": 61, "outcome": "OK", "outcome_details": "skipped=3"})

    def test_parses_failures(self):
        text = "Ran 10 tests in 1.0s\n\nFAILED (failures=2, errors=1)\n"
        parsed = self_test.parse_unittest_output(text)
        self.assertEqual(parsed["outcome"], "FAILED")
        self.assertEqual(parsed["outcome_details"], "failures=2, errors=1")

    def test_missing_summary_is_not_a_pass(self):
        parsed = self_test.parse_unittest_output("no summary here")
        self.assertIsNone(parsed["outcome"])
        self.assertIsNone(parsed["tests_run"])


class SelfTestFailureSummaryTests(unittest.TestCase):
    def test_groups_failures_by_their_final_traceback_line(self):
        text = (
            "======================================================================\n"
            "ERROR: test_a (test_mod.A.test_a)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            "  File \"x.py\", line 1, in test_a\n"
            "ModuleNotFoundError: No module named 'tkinter'\n"
            "\n"
            "======================================================================\n"
            "FAIL: test_b (test_mod.B.test_b)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            "AssertionError: False is not true\n"
            "\n"
            "----------------------------------------------------------------------\n"
            "Ran 2 tests in 0.1s\n"
            "\n"
            "FAILED (failures=1, errors=1)\n")
        summary = self_test.summarize_failures(text)
        self.assertEqual(summary["failing_tests"], 2)
        self.assertEqual(
            [entry["cause"] for entry in summary["causes"]],
            ["ModuleNotFoundError: No module named 'tkinter'",
             "AssertionError: False is not true"])
        self.assertEqual(
            summary["first_failing_tests"],
            ["test_a (test_mod.A.test_a)", "test_b (test_mod.B.test_b)"])

    def test_windows_paths_in_causes_are_generalised(self):
        text = (
            "======================================================================\n"
            "ERROR: test_c (test_mod.C.test_c)\n"
            "----------------------------------------------------------------------\n"
            "PermissionError: [WinError 5] Access is denied: 'C:\\Users\\x\\tmp'\n"
            "\n")
        summary = self_test.summarize_failures(text)
        self.assertIn("<path>", summary["causes"][0]["cause"])
        self.assertNotIn("C:", summary["causes"][0]["cause"])


class SandboxTraceTests(unittest.TestCase):
    def test_trace_result_names_each_test_when_it_starts(self):
        import io
        from forgeos import sandbox_runner

        class Probe(unittest.TestCase):
            def test_one(self):
                pass

        stream = io.StringIO()
        unittest.TextTestRunner(
            stream=stream, verbosity=2,
            resultclass=sandbox_runner._TraceResult,
        ).run(unittest.defaultTestLoader.loadTestsFromTestCase(Probe))
        self.assertIn("STARTED ", stream.getvalue())
        self.assertIn("test_one", stream.getvalue())


class SandboxModuleSelectionTests(unittest.TestCase):
    def test_empty_selection_runs_every_module(self):
        from forgeos import sandbox_runner
        self.assertEqual(sandbox_runner.selected_test_pattern(""), "test*.py")

    def test_named_module_is_selected(self):
        from forgeos import sandbox_runner
        self.assertEqual(
            sandbox_runner.selected_test_pattern("test_core.py"), "test_core.py")

    def test_invalid_module_names_are_refused(self):
        from forgeos import sandbox_runner
        for bad in ("../x.py", "test_*.py", "test_core.txt", "core.py", "test_a.py; rm"):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                sandbox_runner.selected_test_pattern(bad)


class SelfTestProbeTests(unittest.TestCase):
    def test_generated_probe_is_valid_python(self):
        compile(self_test._PROBE_SOURCE, self_test.PROBE_NAME, "exec")


@unittest.skipIf(os.environ.get("FORGEOS_EXECUTION_SNAPSHOT") == "1",
                 "runs a Python interpreter, which the sandboxed child cannot start")
class SelfTestProbeRunTests(unittest.TestCase):
    def test_generated_probe_runs_and_reports_each_check(self):
        import os
        import subprocess
        import sys
        import tempfile as temp_files

        source_root = Path(self_test.__file__).resolve().parents[1]
        with temp_files.TemporaryDirectory(prefix="forgeos-probe-run-") as folder:
            Path(folder, self_test.PROBE_NAME).write_text(
                self_test._PROBE_SOURCE, encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(source_root))
            done = subprocess.run(
                [sys.executable, "-m", "unittest",
                 self_test.PROBE_NAME[:-len(".py")]],
                cwd=folder, env=env, capture_output=True, text=True,
                errors="replace", timeout=120)
        output = done.stdout + done.stderr
        self.assertIn("PROBE ", output)
        self.assertIn("ACL root:", output)
        self.assertIn("mkdtemp.mkdir_sub=", output)
        self.assertIn("keygen=ok", output)
        self.assertNotIn("NameError", output)
        self.assertNotIn("SyntaxError", output)


@unittest.skipIf(os.environ.get("FORGEOS_EXECUTION_SNAPSHOT") == "1",
                 "excluded modules are not staged into the sandboxed snapshot")
class SelfTestExclusionTests(unittest.TestCase):
    def test_every_excluded_module_exists_in_the_repository(self):
        tests_dir = Path(__file__).resolve().parent
        for name in self_test.EXCLUDED_MODULES:
            with self.subTest(module=name):
                self.assertTrue((tests_dir / name).is_file(), name)


@unittest.skipIf(getattr(sys, "frozen", False),
                 "the refusal is checked from source mode")
class SelfTestRefusalTests(unittest.TestCase):
    def test_refuses_outside_the_packaged_app(self):
        with self.assertRaises(RuntimeError):
            self_test.bundled_tests_root()
