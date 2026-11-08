"""Opt-in synthetic RAM benchmark wired into cross-platform regression CI."""
from pathlib import Path
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MemoryBenchmarkTests(unittest.TestCase):
    def test_synthetic_three_mib_output_stays_memory_bounded(self):
        command = [sys.executable, str(ROOT / "tools" / "memory_benchmark.py"),
                   "--mib", "3", "--check"]
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True,
            text=True, timeout=90, check=False)
        self.assertEqual(result.returncode, 0,
                         result.stdout[-3000:] + result.stderr[-3000:])
        values = json.loads(result.stdout)
        self.assertTrue(values["complete"])
        self.assertTrue(values["archive_recoverable"])
        self.assertEqual(values["test_exit_code"], 0)
        self.assertEqual(values["source_bytes"], 3 * 1024 * 1024)
        self.assertLess(values["python_tracemalloc_peak_bytes"], 32 * 1024 * 1024)
        self.assertLess(values["in_memory_preview_chars"], 900000)

    def test_three_cycles_report_process_residual_memory_without_network(self):
        command = [sys.executable, str(ROOT / "tools" / "memory_benchmark.py"),
                   "--mib", "3", "--cycles", "3", "--check"]
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            timeout=90, check=False)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        values = json.loads(result.stdout)
        self.assertEqual(values["cycles_run"], 3)
        self.assertEqual(len(values["cycle_readings"]), 3)
        self.assertTrue(values["archive_recoverable"])
        self.assertLess(values["python_tracemalloc_peak_bytes"], 32 * 1024 * 1024)
        if values["process_rss_bytes_after"] is not None:
            self.assertGreater(values["process_rss_bytes_after"], 0)

    def test_small_complete_logs_do_not_require_a_separate_archive(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "memory_benchmark.py"),
             "--mib", "1", "--check"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        values = json.loads(result.stdout)
        self.assertTrue(values["complete"])
        self.assertFalse(values["archive_recoverable"])
        self.assertEqual(values["source_bytes"], 1024 * 1024)

    def test_invalid_cycle_count_fails_fast(self):
        for cycle in ("0", "11"):
            command = [sys.executable,
                       str(ROOT / "tools" / "memory_benchmark.py"),
                       "--cycles", cycle]
            run = subprocess.run(command, cwd=ROOT,
                                 capture_output=True, text=True, timeout=15)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("--cycles must be 1..10", run.stderr)

    def test_invalid_benchmark_size_fails_without_running(self):
        command = [sys.executable, str(ROOT / "tools" / "memory_benchmark.py"),
                   "--mib", "0"]
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True,
            text=True, timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--mib must be 1..30", result.stderr)


if __name__ == "__main__":
    unittest.main()
