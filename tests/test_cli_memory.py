"""One-shot memory report contract: no SQLite DB, no network or daemon."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from forgeos.cli import main
from forgeos.model_routing import MemorySnapshot, ProcessMemorySnapshot


class NativeMemoryCliTests(unittest.TestCase):
    def test_report_is_one_shot_without_database_or_network(self):
        total = 8 * 1024 ** 3
        process = ProcessMemorySnapshot(90_000_000, 120_000_000, 100_000_000)
        output = io.StringIO()
        with patch("forgeos.model_routing.system_memory",
                   return_value=MemorySnapshot(total, 6 * 1024 ** 3)), \
             patch("forgeos.model_routing.process_memory",
                   return_value=process), \
             patch("forgeos.cli.Store",
                   side_effect=AssertionError("Memory command touched SQLite")), \
             patch("forgeos.model_routing.urlopen",
                   side_effect=AssertionError("Memory command contacted Ollama")), \
             redirect_stdout(output):
            self.assertEqual(main(["memory"]), 0)
        values = json.loads(output.getvalue())
        self.assertEqual(values["total_system_ram_bytes"], total)
        self.assertEqual(values["forgeos_process_rss_bytes"], process.rss_bytes)
        self.assertEqual(values["forgeos_process_peak_rss_bytes"],
                         process.peak_rss_bytes)
        self.assertEqual(values["forgeos_process_private_bytes"],
                         process.private_bytes)
        self.assertIn("Ollama", values["warning"])

    def test_unsupported_memory_probe_is_explicitly_unknown(self):
        output = io.StringIO()
        with patch("forgeos.model_routing.system_memory", return_value=None), \
             patch("forgeos.model_routing.process_memory", return_value=None), \
             patch("forgeos.cli.Store",
                   side_effect=AssertionError("Memory command touched SQLite")), \
             redirect_stdout(output):
            self.assertEqual(main(["memory"]), 0)
        values = json.loads(output.getvalue())
        self.assertIsNone(values["forgeos_process_rss_bytes"])
        self.assertIsNone(values["available_system_ram_bytes"])
        self.assertEqual(values["safe_local_model_file_budget_bytes"], 1024 ** 3)


if __name__ == "__main__":
    unittest.main()
