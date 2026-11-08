"""Actual process-output streaming, large logs, timeouts and evidence recovery."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from forgeos.diagnostics import EvidenceStore
from forgeos.output_capture import (
    BoundedSpool, HEAD_BYTES, MAX_RAW_BYTES, MAX_VISIBLE_BYTES,
    TAIL_BYTES, run_streamed_process, summarize_output_file,
)


class MemoryBoundedCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = EvidenceStore(self.root / "archives")

    def test_small_output_unmodified(self):
        with tempfile.TemporaryFile(mode="w+b") as stream:
            stream.write(b"okay\n")
            record = summarize_output_file(stream, store=self.store)
        self.assertEqual(record["output"], "okay\n")
        self.assertTrue(record["capture_complete"])
        self.assertFalse(record["output_preview_only"])

    def test_large_output_is_streamed_and_recall_is_exact(self):
        size = MAX_VISIBLE_BYTES + 320_000
        with tempfile.TemporaryFile(mode="w+b") as stream:
            stream.write(b"BEGIN\n")
            stream.write(b"x" * (size - 10))
            stream.write(b"END!")
            result = summarize_output_file(stream, store=self.store)
        self.assertEqual(result["capture_source_bytes"], size)
        self.assertTrue(result["capture_complete"])
        self.assertTrue(result["output_preview_only"])
        self.assertIn("original bytes omitted", result["output"])
        self.assertTrue(result["output"].startswith("BEGIN\n"))
        self.assertTrue(result["output"].endswith("END!"))
        original = self.store.get(result["evidence_reference"])
        self.assertEqual(len(original.encode()), size)
        self.assertTrue(original.endswith("END!"))

    def test_spool_does_not_write_past_cap(self):
        capture = BoundedSpool(cap=4096)
        try:
            capture.feed(b"one" + b"x" * 5000)
            capture.feed(b"TAIL-ERROR")
            record = capture.finish(store=self.store)
            self.assertLessEqual(capture.file.tell(), 4096)
            self.assertEqual(capture.total, 5013)
            self.assertFalse(record["capture_complete"])
            self.assertIn("full evidence unavailable", record["output"])
            self.assertIn("TAIL-ERROR", record["output"])
        finally:
            capture.close()

    def test_spawn_streams_without_truncating_normal_test(self):
        command = [sys.executable, "-c", "import sys; print('HELLO'); sys.stderr.write('ERROR\\n')"]
        result = run_streamed_process(command, self.root, timeout=10)
        self.assertEqual(result["returncode"], 0)
        self.assertTrue(result["capture_complete"])
        self.assertIn("HELLO", result["output"])
        self.assertIn("ERROR", result["output"])

    def test_spawn_preserves_failure_exit_code_and_tail(self):
        script = ("import sys; sys.stdout.write('INFO hello\\n' * 260000); "
                  "sys.stdout.flush(); sys.stderr.write('AssertionError final\\n'); "
                  "sys.exit(29)")
        result = run_streamed_process([sys.executable, "-c", script], self.root, timeout=30)
        self.assertEqual(result["returncode"], 29)
        self.assertFalse(result["passed"])
        self.assertIn("AssertionError final", result["output"])
        self.assertTrue(result["capture_complete"])
        self.assertTrue(result["output_preview_only"])
        self.assertIsNotNone(result["evidence_reference"])

    def test_streaming_large_output_has_bounded_python_allocations(self):
        import gc
        import tracemalloc
        gc.collect()
        tracemalloc.start()
        try:
            script = ("import sys; buf = b'INFO memory test\\n' * 4000; "
                      "[(sys.stdout.buffer.write(buf)) for _ in range(200)]")
            result = run_streamed_process(
                [sys.executable, "-c", script], self.root, timeout=30)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(result["capture_complete"])
        self.assertTrue(result["output_preview_only"])
        self.assertLess(peak, 32 * 1024 * 1024)

    def test_timeout_terminates_process(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_streamed_process(
                [sys.executable, "-c", "import time; time.sleep(8)"],
                self.root, timeout=1)

    def test_large_native_file_has_explicit_incomplete_marker(self):
        with tempfile.TemporaryFile(mode="w+b") as stream:
            chunk = b"INFO test output\n" * 4096
            amount = 0
            while amount <= MAX_RAW_BYTES:
                stream.write(chunk)
                amount += len(chunk)
            record = summarize_output_file(stream, store=self.store)
        self.assertFalse(record["capture_complete"])
        self.assertIn("full evidence unavailable", record["output"])
        self.assertIsNone(record["evidence_reference"])
        self.assertLess(len(record["output"]), 1_000_000)


if __name__ == "__main__":
    unittest.main()
