import json
from pathlib import Path
import tempfile
import unittest

from forgeos.model_compatibility import (
    INDEX_NAME, list_runtime_self_tests, record_runtime_self_test,
)


class FakeController:
    def __init__(self, model_hash="a" * 64, runtime_hash="b" * 64):
        self.model_sha256 = model_hash
        self.runtime_sha256 = runtime_hash
        self.active = True
        self.outcome = {
            "status": "passed", "elapsed_seconds": 0.25,
            "response_bytes": 12, "memory": None,
        }

    def running(self):
        return self.active

    def inference_self_test(self):
        return self.outcome


class ModelCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "models"
        self.controller = FakeController()

    def tearDown(self):
        self.temp.cleanup()

    def test_records_only_a_pass_for_exact_model_runtime_pair(self):
        outcome = record_runtime_self_test(
            self.controller, directory=self.root)
        self.assertEqual(outcome["model_sha256"], "a" * 64)
        self.assertEqual(outcome["runtime_sha256"], "b" * 64)
        records = list_runtime_self_tests(directory=self.root)
        self.assertEqual(len(records["a" * 64]), 1)
        self.assertEqual(records["a" * 64][0]["runtime_sha256"], "b" * 64)
        self.assertRegex(records["a" * 64][0]["tested_at"],
                         r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertNotIn("/", (self.root / INDEX_NAME).read_text("utf-8"))

    def test_retest_replaces_same_pair_and_new_runtime_is_distinct(self):
        record_runtime_self_test(self.controller, directory=self.root)
        record_runtime_self_test(self.controller, directory=self.root)
        self.controller.runtime_sha256 = "c" * 64
        record_runtime_self_test(self.controller, directory=self.root)
        records = list_runtime_self_tests(directory=self.root)["a" * 64]
        self.assertEqual([item["runtime_sha256"] for item in records],
                         ["b" * 64, "c" * 64])

    def test_failed_or_invalid_self_test_does_not_write_record(self):
        self.controller.outcome = {
            "status": "failed", "response_bytes": 12,
        }
        with self.assertRaisesRegex(RuntimeError, "did not pass"):
            record_runtime_self_test(self.controller, directory=self.root)
        self.assertFalse((self.root / INDEX_NAME).exists())
        self.controller.outcome = {"status": "passed", "response_bytes": 0}
        with self.assertRaisesRegex(RuntimeError, "did not pass"):
            record_runtime_self_test(self.controller, directory=self.root)
        self.assertFalse((self.root / INDEX_NAME).exists())

    def test_changed_identity_or_stopped_runtime_does_not_record(self):
        class ChangingController(FakeController):
            def inference_self_test(inner):
                inner.runtime_sha256 = "c" * 64
                return inner.outcome

        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            record_runtime_self_test(ChangingController(), directory=self.root)
        self.assertFalse((self.root / INDEX_NAME).exists())
        self.controller.active = False
        with self.assertRaisesRegex(RuntimeError, "must be running"):
            record_runtime_self_test(self.controller, directory=self.root)
        self.assertFalse((self.root / INDEX_NAME).exists())

    def test_invalid_existing_index_fails_closed_and_is_preserved(self):
        self.root.mkdir(parents=True)
        index = self.root / INDEX_NAME
        index.write_text(json.dumps({"version": 1, "pairs": [{
            "model_sha256": "invalid", "runtime_sha256": "b" * 64,
            "tested_at": "2026-01-01T00:00:00Z",
        }]}), encoding="utf-8")
        original = index.read_bytes()
        with self.assertRaisesRegex(ValueError, "invalid pair"):
            list_runtime_self_tests(directory=self.root)
        with self.assertRaisesRegex(ValueError, "invalid pair"):
            record_runtime_self_test(self.controller, directory=self.root)
        self.assertEqual(index.read_bytes(), original)

    def test_oversized_self_test_response_cannot_be_recorded(self):
        self.controller.outcome = {"status": "passed", "response_bytes": 4097}
        with self.assertRaisesRegex(RuntimeError, "did not pass"):
            record_runtime_self_test(self.controller, directory=self.root)
        self.assertFalse((self.root / INDEX_NAME).exists())


if __name__ == "__main__":
    unittest.main()
