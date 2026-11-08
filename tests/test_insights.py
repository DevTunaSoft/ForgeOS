import json
import tempfile
from pathlib import Path
import unittest
from forgeos.insights import get_comparison
from forgeos.diagnostics import EvidenceStore, optimize_test_result


class InsightModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.evidence = EvidenceStore(Path(self.tmp.name) / "evidence")

    def test_nested_autonomous_and_test_only_results(self):
        raw = "INFO line\n" * 1200 + "AssertionError: fails\n"
        test = optimize_test_result(
            {"passed": False, "returncode": 1, "output": raw},
            "python-unittest", store=self.evidence, budget=1400)
        auto = {"id": 19, "status": "failed",
                "result": json.dumps({"model": "qwen2.5-coder:3b", "test": test})}
        before = get_comparison(auto)
        self.assertEqual(before["model"], "qwen2.5-coder:3b")
        self.assertTrue(before["raw_available"])
        self.assertIn("not been requested", before["raw"])
        after = get_comparison(auto, evidence_store=self.evidence)
        self.assertEqual(after["raw"], raw)
        just_test = {"id": 20, "status": "failed", "result": json.dumps(test)}
        self.assertEqual(get_comparison(just_test)["model"], "unspecified")

    def test_gui_uses_bounded_archive_preview_not_unbounded_get(self):
        raw = "INFO large\n" * 300000 + "ERROR final\n"
        reference = self.evidence.put(raw)
        record = {"id": 7, "result": {
            "output": "AI summary", "optimization": {"reference": reference}}}
        class PreviewOnly:
            def __init__(self, store):
                self.store = store
            def preview(self, ref, *, max_chars):
                return self.store.preview(ref, max_chars=max_chars)
            def get(self, reference):
                raise AssertionError("Unbounded evidence decode invoked")
        item = get_comparison(record, evidence_store=PreviewOnly(self.evidence),
                              preview_limit=12000)
        self.assertLessEqual(len(item["raw"]), 12000)
        self.assertIn("ERROR final", item["raw"])

    def test_malicious_path_never_used_as_filename(self):
        fake = {"id": 12, "status": "failed",
                "result": json.dumps({"output": "failed",
                                      "optimization": {"reference": "../../secret"}})}
        class MustNotCall:
            def get(self, reference):
                raise AssertionError("Unvalidated diagnostic path was read")
        result = get_comparison(fake, evidence_store=MustNotCall())
        self.assertFalse(result["raw_available"])
        self.assertIn("No recoverable", result["raw"])

    def test_unexpected_classification_shape_does_not_crash_gui_model(self):
        value = {"id": 22, "result": {"output": "failed",
                 "failure_type": "untrusted bad metadata"}}
        self.assertEqual(get_comparison(value)["failure_type"], {})

    def test_too_large_result_rejected(self):
        with self.assertRaises(ValueError):
            get_comparison({"result": "x" * (2_000_001)})

    def test_preview_and_invalid_records(self):
        short = {"id": 5, "result": {"output": "Z" * 6000}}
        item = get_comparison(short, preview_limit=2500)
        self.assertIn("omitted from GUI preview", item["compact"])
        self.assertLess(len(item["compact"]), 3000)
        for value in ({}, {"result": "not valid json"}, {"result": []}):
            with self.assertRaises(ValueError):
                get_comparison(value)
        with self.assertRaises(ValueError):
            get_comparison(short, preview_limit=20)

if __name__ == "__main__":
    unittest.main()
