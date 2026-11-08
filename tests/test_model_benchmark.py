import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.model_benchmark import capture_baseline, ram_tier, write_baseline
from forgeos.model_routing import GIB, MemorySnapshot


class FakeController:
    def benchmark_identity(self):
        return {
            "model": {
                "repo": "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF",
                "revision": "a" * 40,
                "file": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
                "bytes": 1_100_000_000,
                "sha256": "b" * 64,
            },
            "runtime": {"sha256": "c" * 64, "release_tag": "b12199"},
            "settings": {
                "context_tokens": 2048,
                "threads": 6,
                "memory_ceiling_bytes": 8 * GIB,
            },
            "startup_elapsed_seconds": 4.25,
        }

    def inference_self_test(self):
        return {
            "status": "passed",
            "elapsed_seconds": 0.75,
            "response_bytes": 12,
            "memory": {
                "job_memory_limit_bytes": 8 * GIB,
                "peak_job_memory_used_bytes": 2 * GIB,
                "peak_process_memory_used_bytes": 2 * GIB,
                "scope": "windows_job_committed_memory",
            },
        }


class ModelBenchmarkTests(unittest.TestCase):
    def test_fixed_ram_tiers(self):
        self.assertEqual(ram_tier(4 * GIB), "4-gib-gui-investigation")
        self.assertEqual(ram_tier(8 * GIB), "8-gib")
        self.assertEqual(ram_tier(16 * GIB), "16-gib")
        self.assertEqual(ram_tier(32 * GIB), "32-gib")
        with self.assertRaises(ValueError):
            ram_tier(0)

    def test_capture_is_identity_bound_secret_free_and_not_qualification(self):
        with patch(
                "forgeos.model_benchmark.system_memory",
                return_value=MemorySnapshot(32 * GIB, 20 * GIB)):
            record = capture_baseline(FakeController())
        self.assertEqual(record["hardware"]["ram_tier"], "32-gib")
        self.assertEqual(record["model"]["quantization_from_filename"], "Q4_K_M")
        self.assertFalse(record["model"]["quantization_verified"])
        self.assertIsNone(record["model"]["license_id"])
        self.assertEqual(
            record["measurement"]["managed_memory"]
            ["peak_job_memory_used_bytes"], 2 * GIB)
        self.assertEqual(
            record["qualification_status"], "baseline_only_not_qualified")
        serialized = json.dumps(record)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("endpoint", serialized)

    def test_write_is_bounded_unique_and_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as temp, patch(
                "forgeos.model_benchmark.system_memory",
                return_value=MemorySnapshot(16 * GIB, 10 * GIB)):
            first = write_baseline(FakeController(), temp)
            second = write_baseline(FakeController(), temp)
            self.assertNotEqual(first["path"], second["path"])
            for result in (first, second):
                path = Path(result["path"])
                self.assertTrue(path.is_file())
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema"], 1)
                self.assertLess(path.stat().st_size, 64 * 1024)
            self.assertFalse(list(Path(temp).glob("*.sqlite*")))


if __name__ == "__main__":
    unittest.main()
