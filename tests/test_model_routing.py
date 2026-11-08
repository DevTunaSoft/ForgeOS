import io
import json
import unittest
from unittest.mock import patch
from forgeos.model_routing import (
    MemorySnapshot, ProcessMemorySnapshot, choose_installed_model,
    installed_models, local_memory_settings, managed_runtime_memory_ceiling,
    process_memory, safe_model_file_budget,
)

MODELS = [
    {"name":"qwen2.5-coder:3b","size":2_000_000_000},
    {"name":"qwen2.5-coder:7b","size":5_000_000_000},
    {"name":"qwen2.5-coder:14b","size":9_000_000_000},
    {"name":"image-model:1b","size":1_000_000_000},
]

class LocalModelRoutingTests(unittest.TestCase):
    def test_short_task_uses_smallest_code_model(self):
        self.assertEqual(choose_installed_model("Fix button",available=MODELS),
                         "qwen2.5-coder:3b")

    def test_complex_task_uses_bigger_local_model(self):
        self.assertEqual(choose_installed_model(
            "Fix concurrent recovery security bugs",available=MODELS),
            "qwen2.5-coder:14b")

    def test_middle_complexity(self):
        self.assertEqual(choose_installed_model(
            "Review crash defects",available=MODELS), "qwen2.5-coder:7b")

    def test_max_size_respected(self):
        self.assertEqual(choose_installed_model(
            "Fix concurrent recovery security bugs",available=MODELS,
            max_model_bytes=6_000_000_000), "qwen2.5-coder:7b")

    def test_reject_remote_endpoint_and_bad_catalog(self):
        with self.assertRaises(ValueError):
            choose_installed_model("task",available=MODELS,endpoint="https://api.example.com")
        with self.assertRaises(ValueError):
            installed_models(endpoint="http://localhost:11434")
        with self.assertRaises(RuntimeError):
            choose_installed_model("task",available=[{"name":"bad;rm","size":2}])
        with self.assertRaises(ValueError):
            choose_installed_model("  ",available=MODELS)
        with self.assertRaises(ValueError):
            choose_installed_model("task",available=MODELS,max_model_bytes=True)

    def test_model_discovery_local_only(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        data = json.dumps({"models":MODELS}).encode()
        with patch("forgeos.model_routing.urlopen",return_value=Response(data)) as mock, \
             patch("forgeos.model_routing.system_memory",
                   return_value=MemorySnapshot(32*1024**3, 24*1024**3)):
            result = choose_installed_model("Fix a button")
        self.assertEqual(result,"qwen2.5-coder:3b")
        self.assertEqual(mock.call_args.args[0],"http://127.0.0.1:11434/api/tags")

    def test_process_rss_probe_is_one_shot_and_nonnegative(self):
        reading = process_memory()
        # A few Unix variants may not expose /proc/self/status.
        if reading is None:
            self.skipTest("No supported native process working-set probe")
        self.assertIsInstance(reading, ProcessMemorySnapshot)
        self.assertGreater(reading.rss_bytes, 0)
        self.assertGreaterEqual(reading.peak_rss_bytes, reading.rss_bytes)
        if reading.private_bytes is not None:
            self.assertGreaterEqual(reading.private_bytes, 0)

    def test_low_ram_protects_against_oversized_installed_models(self):
        gib = 1024 ** 3
        scenarios = [
            (4, 3, 0),
            (8, 6, 2_000_000_000),
            (16, 12, 5_000_000_000),
            (32, 24, 9_000_000_000),
        ]
        for total, free, expected in scenarios:
            with self.subTest(total=total, free=free):
                budget = safe_model_file_budget(
                    MemorySnapshot(total * gib, free * gib))
                candidates = [m for m in MODELS
                              if m["size"] <= budget and "coder" in m["name"]]
                self.assertEqual(max((m["size"] for m in candidates), default=0), expected)

    def test_explicit_memory_budget_and_failed_preflight(self):
        self.assertEqual(choose_installed_model(
            "Fix concurrent recovery security bugs", available=MODELS,
            memory_limit_bytes=2_500_000_000), "qwen2.5-coder:3b")
        with self.assertRaisesRegex(RuntimeError, "safe memory budget"):
            choose_installed_model("important task", available=MODELS,
                                   memory_limit_bytes=500_000_000)
        with self.assertRaises(ValueError):
            choose_installed_model("task", available=MODELS,
                                   memory_limit_bytes=True)

    def test_managed_runtime_memory_ceiling_preserves_headroom(self):
        gib = 1024 ** 3
        self.assertIsNone(managed_runtime_memory_ceiling(None))
        self.assertEqual(
            managed_runtime_memory_ceiling(MemorySnapshot(32*gib, 24*gib)),
            20*gib)
        self.assertEqual(
            managed_runtime_memory_ceiling(MemorySnapshot(8*gib, 6*gib)),
            4*gib)
        self.assertEqual(
            managed_runtime_memory_ceiling(MemorySnapshot(4*gib, 1*gib)),
            0)
        with self.assertRaises(ValueError):
            managed_runtime_memory_ceiling(MemorySnapshot(0, 0))

    def test_automatic_low_memory_profiles(self):
        gib = 1024 ** 3
        with patch("forgeos.model_routing.system_memory",
                   return_value=MemorySnapshot(4*gib, 3*gib)):
            self.assertEqual(local_memory_settings()["num_ctx"], 1024)
            self.assertEqual(local_memory_settings()["keep_alive"], 0)
        with patch("forgeos.model_routing.system_memory",
                   return_value=MemorySnapshot(8*gib, 6*gib)):
            self.assertEqual(local_memory_settings()["num_ctx"], 2048)
        with patch("forgeos.model_routing.system_memory",
                   return_value=MemorySnapshot(32*gib, 24*gib)):
            self.assertEqual(local_memory_settings()["num_ctx"], 8192)
        self.assertEqual(safe_model_file_budget(None), 1024**3)

    def test_unbounded_catalog_is_rejected(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        with patch("forgeos.model_routing.urlopen",
                   return_value=Response(b"x" * (2*1024*1024+1))):
            with self.assertRaisesRegex(ValueError, "catalog exceeded"):
                installed_models()

    def test_unknown_models_can_fallback_to_installed_general_model(self):
        chosen=choose_installed_model(
            "test",available=[{"name":"gemma:2b","size":2_000_000_000}])
        self.assertEqual(chosen,"gemma:2b")

if __name__=="__main__":
    unittest.main()
