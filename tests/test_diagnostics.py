"""Evidence fidelity, failure diagnostics, and adversarial recovery tests."""
import gzip
import hashlib
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import Mock
from forgeos.diagnostics import EvidenceStore, optimize_test_result

class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = EvidenceStore(Path(self.temp.name) / "evidence")

    def test_roundtrip_and_deduplicate(self):
        raw = "FAIL: test_example\nAssertionError: 2 != 42\nΩ漢字\n"
        ref = self.store.put(raw)
        self.assertEqual(len(ref), 64)
        self.assertEqual(self.store.get(ref), raw)
        self.assertEqual(self.store.put(raw), ref)
        self.assertEqual(len(list(self.store.root.glob("*.gz"))), 1)

    def test_invalid_reference_rejected(self):
        for ref in ["../secret", "A" * 64, "x" * 64, "a" * 63, "", "f" * 64 + "/"]:
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                self.store.get(ref)

    def test_corrupt_evidence_detected_and_repaired(self):
        raw = "original"
        ref = self.store.put(raw)
        self.store._path(ref).write_bytes(gzip.compress(b"tampered"))
        with self.assertRaises(ValueError):
            self.store.get(ref)
        self.assertEqual(self.store.put(raw), ref)
        self.assertEqual(self.store.get(ref), raw)

    def test_archiving_failure_returns_uncompressed(self):
        raw = "noise\n" * 400
        failed_store = Mock()
        failed_store.put.side_effect = OSError("disk full")
        result = optimize_test_result(
            {"passed": False, "returncode": 1, "output": raw}, "python-unittest",
            store=failed_store, budget=1200)
        self.assertEqual(result["output"], raw)
        self.assertFalse(result["optimization"]["archived"])
        self.assertFalse(result["passed"])

    def test_failures_and_counts_survive_with_recall(self):
        raw = ("INFO normal\n" * 600 + 'Traceback (most recent call last):\n'
               + ' File "app.py", line 73\nAssertionError: expected 42\n'
               + "INFO other\n" * 600 + "FAILED (failures=1)\n")
        result = optimize_test_result(
            {"passed": False, "returncode": 1, "output": raw}, "python-unittest",
            store=self.store, budget=2500)
        self.assertIn("AssertionError", result["output"])
        self.assertIn("FAILED (failures=1)", result["output"])
        self.assertLess(len(result["output"]), len(raw))
        self.assertEqual(result["returncode"], 1)
        self.assertEqual(self.store.get(result["optimization"]["reference"]), raw)

    def test_success_summary_and_metadata(self):
        raw = "PASS case\n" * 2000 + "2000 passed in 10s\n"
        result = optimize_test_result(
            {"passed": True, "returncode": 0, "output": raw, "containment": "none"},
            "python-pytest", store=self.store, budget=2000)
        self.assertTrue(result["passed"])
        self.assertIn("2000 passed", result["output"])
        self.assertEqual(result["containment"], "none")
        self.assertGreater(result["optimization"]["saved_bytes"], 0)

    def test_small_output_roundtrip(self):
        raw = "\x1b[31mError\x1b[0m: failed\n"
        result = optimize_test_result(
            {"passed": False, "returncode": 1, "output": raw}, "npm-test", store=self.store)
        self.assertEqual(self.store.get(result["optimization"]["reference"]), raw)
        self.assertEqual(result["optimization"]["reference"],
                         hashlib.sha256(raw.encode()).hexdigest())

    def test_json_and_text_parsing_non_authoritative(self):
        from forgeos.diagnostics import parse_test_summary
        self.assertEqual(parse_test_summary('{"summary":{"total":5,"passed":4,"failed":1}}',
                                            "python-pytest"),
                         {"tier": "json", "counts": {"passed": 4, "failed": 1, "total": 5}})
        self.assertEqual(parse_test_summary("Ran 9 tests in 0.13s\n\nFAILED (failures=2, errors=1)",
                                            "python-unittest"),
                         {"tier": "text", "counts": {"total": 9, "failed": 2, "errors": 1}})
        self.assertEqual(parse_test_summary("===== 3 passed, 1 failed in 1.2s =====",
                                            "python-pytest"),
                         {"tier": "text", "counts": {"passed": 3, "failed": 1}})
        self.assertEqual(parse_test_summary("random output", "unknown"),
                         {"tier": "raw", "counts": {}})
        self.assertEqual(parse_test_summary('{"summary":{"passed":true}}', "python-pytest"),
                         {"tier": "raw", "counts": {}})

    def test_parser_never_rewrites_authoritative_exit_code(self):
        raw = "Ran 8 tests in 0.5s\nOK\n"
        result = optimize_test_result(
            {"passed": False, "returncode": 99, "output": raw},
            "python-unittest", store=self.store)
        self.assertEqual(result["parsed_test"]["tier"], "text")
        self.assertEqual(result["returncode"], 99)
        self.assertFalse(result["passed"])

    def test_noncritical_dedup_keeps_error_and_evidence(self):
        raw = "Downloading dependencies\n" * 40 + "ERROR: fails\n" * 6
        result = optimize_test_result(
            {"passed": False, "returncode": 17, "output": raw},
            "npm-test", store=self.store, budget=1600)
        self.assertEqual(result["optimization"]["deduplicated_lines"], 39)
        self.assertIn("39 repeated noncritical", result["output"])
        self.assertEqual(result["output"].count("ERROR: fails"), 6)
        self.assertEqual(result["returncode"], 17)
        self.assertEqual(self.store.get(result["optimization"]["reference"]), raw)

    def test_small_outputs_do_not_expand_context(self):
        raw = "\x1b[31mERROR\x1b[0m"
        result = optimize_test_result(
            {"passed": False, "returncode": 1, "output": raw},
            "npm-test", store=self.store)
        self.assertLessEqual(len(result["output"].encode()), len(raw.encode()))
        self.assertEqual(self.store.get(result["optimization"]["reference"]), raw)

    def test_bad_budget(self):
        with self.assertRaises(ValueError):
            optimize_test_result({"output": "x"}, "test", store=self.store, budget=0)

    def test_deterministic_unicode_fuzz_and_integrity(self):
        rng = random.Random(20261107)
        chars = "abXYZ123\n\r\tΩ漢字\0\x1b"
        for i in range(50):
            raw = "".join(rng.choice(chars) for _ in range(rng.randint(20, 4000)))
            record = optimize_test_result(
                {"passed": i % 2 == 0, "returncode": i % 2, "output": raw},
                "python-unittest", store=self.store, budget=1024)
            self.assertEqual(self.store.get(record["optimization"]["reference"]), raw)
            self.assertEqual(record["returncode"], i % 2)

    def test_oversize_archive_fails_closed_to_raw(self):
        with self.assertRaises(ValueError):
            self.store.put("x" * (32 * 1024 * 1024 + 1))

    def test_symlink_denied(self):
        outside = Path(self.temp.name) / "outside"
        outside.write_text("safe")
        target = self.store._path(hashlib.sha256(b"hello").hexdigest())
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            self.store.put("hello")
        self.assertEqual(outside.read_text(), "safe")

if __name__ == "__main__":
    unittest.main()
