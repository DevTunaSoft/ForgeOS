"""Persistent repair signatures; privacy, confidence, and concurrency tests."""
from pathlib import Path
import random
import tempfile
import threading
import unittest
from forgeos.error_learning import RepairMemory, fingerprint

class RepairMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.sqlite3"
        self.memory = RepairMemory(self.path)

    def test_error_signature_normalizes_line_numbers(self):
        a = fingerprint("AssertionError: value 123 at /tmp/source.py:45")
        b = fingerprint("AssertionError: value 456 at /tmp/source.py:90")
        self.assertEqual(a, b)
        self.assertEqual(a[0], "AssertionError")
        self.assertEqual(len(a[1]), 64)

    def test_memory_persists_only_hashes_categories_and_counters(self):
        secret = "ModuleNotFoundError: secret-password-goes-here"
        self.memory.record(secret, preset="python-unittest", model="qwen2.5-coder:3b",
                           verified_success=True)
        self.assertEqual(RepairMemory(self.path).stats()["verified_repairs"], 1)
        self.assertNotIn(secret.encode(), self.path.read_bytes())
        self.assertEqual(len(self.memory.recommendations(
            preset="python-unittest", model="qwen2.5-coder:3b")), 1)

    def test_suggest_only_with_repeated_evidence(self):
        raw = "AssertionError: wrong value"
        for _ in range(3):
            self.memory.record(raw, preset="python-pytest", model="qwen2.5-coder:3b",
                               verified_success=True)
        suggestions = self.memory.recommendations(
            preset="python-pytest", model="qwen2.5-coder:3b")
        self.assertFalse(suggestions[0]["eligible_for_review"])
        self.memory.record(raw, preset="python-pytest", model="qwen2.5-coder:3b",
                           verified_success=True)
        self.assertTrue(self.memory.recommendations(
            preset="python-pytest", model="qwen2.5-coder:3b")[0]["eligible_for_review"])
        self.memory.record(raw, preset="python-pytest", model="qwen2.5-coder:3b",
                           verified_success=False)
        self.assertTrue(self.memory.recommendations(
            preset="python-pytest", model="qwen2.5-coder:3b")[0]["eligible_for_review"])

    def test_invalid_identifiers_and_non_boolean_rejected(self):
        for model in ("../thing", "a" * 400, "name\nsecret"):
            with self.assertRaises(ValueError):
                self.memory.record("ERROR", preset="python-unittest", model=model,
                                   verified_success=True)
        with self.assertRaises(ValueError):
            self.memory.record("ERROR", preset="python-unittest", model="local",
                               verified_success=1)

    def test_parallel_records_transactional(self):
        errors = []
        def worker():
            try:
                local = RepairMemory(self.path)
                for _ in range(5):
                    local.record("ERROR: lost test", preset="npm-test", model="local",
                                 verified_success=False)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(RepairMemory(self.path).stats()["failed_repairs"], 30)

    def test_seeded_random_failure_signatures(self):
        randomizer = random.Random(55)
        for index in range(60):
            text = "ERROR:" + "".join(randomizer.choice("abc123Ω\n") for _ in range(index + 2))
            self.memory.record(text, preset="go-test", model="local",
                               verified_success=index % 2 == 0)
        status = self.memory.stats()
        self.assertEqual(status["verified_repairs"], 30)
        self.assertEqual(status["failed_repairs"], 30)

if __name__ == "__main__":
    unittest.main()
