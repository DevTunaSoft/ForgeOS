import random
from pathlib import Path
import tempfile
import threading
import unittest
from forgeos.optimizer_metrics import OptimizationMetrics

class OptimizationMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = OptimizationMetrics(Path(self.tmp.name) / "metrics.sqlite3")

    def test_empty(self):
        self.assertEqual(self.store.stats()["runs"], 0)

    def test_aggregate_and_token_estimate(self):
        self.store.record({"passed": True, "parsed_test": {"tier": "json"},
                           "optimization": {"preset": "python-pytest",
                                            "source_bytes": 1000, "context_bytes": 200,
                                            "archived": True}})
        self.store.record({"passed": False, "parsed_test": {"tier": "raw"},
                           "optimization": {"preset": "npm-test",
                                            "source_bytes": 300, "context_bytes": 300,
                                            "archived": False}})
        total = OptimizationMetrics(self.store.path).stats()
        self.assertEqual(total["runs"], 2)
        self.assertEqual(total["bytes_saved"], 800)
        self.assertEqual(total["approx_tokens_saved_bytes_div_4"], 200)
        self.assertEqual(total["archived_runs"], 1)
        self.assertEqual(total["unparsed_runs"], 1)

    def test_invalid_metrics_rejected(self):
        samples = [
            {"preset": "npm-test", "source_bytes": -1, "context_bytes": 2, "archived": True},
            {"preset": "x;echo", "source_bytes": 5, "context_bytes": 2, "archived": True},
            {"preset": "npm-test", "source_bytes": True, "context_bytes": 2, "archived": True},
            {"preset": "npm-test", "source_bytes": 10, "context_bytes": 3, "archived": "true"}
        ]
        for data in samples:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.store.record({"optimization": data})
        self.assertEqual(self.store.stats()["runs"], 0)

    def test_repeated_concurrent_metrics(self):
        errors = []
        def worker():
            try:
                store = OptimizationMetrics(self.store.path)
                for i in range(10):
                    store.record({"passed": True,
                                  "optimization": {"preset": "go-test",
                                      "source_bytes": 200, "context_bytes": 100,
                                      "archived": True}})
            except Exception as error:
                errors.append(error)
        workers = [threading.Thread(target=worker) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(15)
            self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        self.assertEqual(self.store.stats()["runs"], 40)
        self.assertEqual(self.store.stats()["bytes_saved"], 4000)

if __name__ == "__main__":
    unittest.main()
