"""Linux-runnable checks for the packaged self-test's staging and parsing."""
import shutil
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


class SelfTestRefusalTests(unittest.TestCase):
    def test_refuses_outside_the_packaged_app(self):
        with self.assertRaises(RuntimeError):
            self_test.bundled_tests_root()
