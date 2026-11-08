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

    def test_windows_crlf_is_normalized_for_view_but_not_for_archive(self):
        with tempfile.TemporaryFile(mode="w+b") as stream:
            stream.write(b"FIRST\r\nSECOND\r\n")
            result = summarize_output_file(stream, store=self.store)
        self.assertEqual(result["output"], "FIRST\nSECOND\n")
        self.assertEqual(result["capture_source_bytes"], len(b"FIRST\r\nSECOND\r\n"))

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

    def test_long_overflow_stream_keeps_only_bounded_tail_chunks(self):
        capture = BoundedSpool(cap=2048)
        try:
            for index in range(2500):
                capture.feed(b"INFO repeated filler\n" * 20)
            capture.feed(b"LAST_FAILURE")
            result = capture.finish(store=self.store)
            self.assertFalse(result["capture_complete"])
            self.assertIn("LAST_FAILURE", result["output"])
            self.assertLessEqual(capture._tail_size, TAIL_BYTES)
            self.assertLessEqual(capture._written, capture.cap)
        finally:
            capture.close()

    def test_native_disk_output_watchdog_kills_oversize_job(self):
        from forgeos.output_capture import wait_native_output_bounded
        class FakeWindowsKernel:
            def __init__(self): self.killed = False
            def WaitForSingleObject(self, process, delay):
                return 0 if self.killed else 258
            def TerminateJobObject(self, job, reason):
                self.killed = True
                return 1
        kernel = FakeWindowsKernel()
        with tempfile.TemporaryFile(mode="w+b") as native:
            native.truncate(MAX_RAW_BYTES + 1)
            with self.assertRaisesRegex(OSError, "disk limit"):
                wait_native_output_bounded(
                    10, 20, native, 15, kernel32=kernel,
                    wait_timeout=258, wait_object_0=0)
        self.assertTrue(kernel.killed)

    def test_native_output_watchdog_passes_success_without_termination(self):
        from forgeos.output_capture import wait_native_output_bounded
        class FinishedKernel:
            def WaitForSingleObject(self, process, delay): return 0
            def TerminateJobObject(self, job, reason):
                raise AssertionError("Successful process was terminated")
        with tempfile.TemporaryFile(mode="w+b") as native:
            self.assertEqual(wait_native_output_bounded(
                10, 20, native, 5, kernel32=FinishedKernel(),
                wait_timeout=258,
                wait_object_0=0), 0)

    def test_secondary_test_output_overflow_kills_job_and_preserves_host(self):
        from forgeos.output_capture import wait_native_output_bounded
        class FakeKernel:
            terminated = False
            def WaitForSingleObject(self, process, delay):
                return 0 if self.terminated else 258
            def TerminateJobObject(self, job, exit_code):
                self.terminated = True
                return 1
        file = self.root / "test-output.txt"
        file.write_bytes(b"small")
        class SyntheticStream:
            def fileno(self): return self._file.fileno()
            def __init__(self, file): self._file = file
        kernel = FakeKernel()
        with tempfile.TemporaryFile(mode="w+b") as stdout:
            file.write_bytes(b"0" * (MAX_RAW_BYTES + 1))
            with self.assertRaisesRegex(OSError, "Child-generated test output"):
                wait_native_output_bounded(
                    1, 2, stdout, 2, kernel32=kernel, wait_timeout=258,
                    wait_object_0=0, watched_files=(file,))
        self.assertTrue(kernel.terminated)
        self.assertEqual(file.stat().st_size, MAX_RAW_BYTES + 1)

    def test_watched_output_symlink_replacement_fails_closed(self):
        from forgeos.output_capture import wait_native_output_bounded
        outside = self.root / "outside-secret.txt"
        outside.write_text("do not read")
        folder = self.root / "owned"
        folder.mkdir()
        watched = folder / "test-output.txt"
        try:
            watched.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Cannot create symbolic links")
        class FakeKernel:
            killed = False
            def WaitForSingleObject(self, process, delay):
                return 0 if self.killed else 258
            def TerminateJobObject(self, job, code):
                self.killed = True
                return 1
        kernel = FakeKernel()
        with tempfile.TemporaryFile(mode="w+b") as stdout:
            with self.assertRaisesRegex(OSError, "unsafe path"):
                wait_native_output_bounded(
                    1, 2, stdout, 2, kernel32=kernel, wait_timeout=258,
                    wait_object_0=0, watched_files=(watched,))
        self.assertTrue(kernel.killed)
        self.assertEqual(outside.read_text(), "do not read")

    def test_secure_monitor_rejects_outside_file_and_directory_link(self):
        from forgeos.output_capture import validate_secure_output_path
        from forgeos.test_runtime import RUNTIME_DIR
        folder = self.root / RUNTIME_DIR
        folder.mkdir(exist_ok=True)
        expected = folder / "test-output.txt"
        self.assertEqual(validate_secure_output_path(self.root, expected), expected)
        with self.assertRaises(ValueError):
            validate_secure_output_path(self.root, self.root / "other.txt")
        outside = self.root / "outside"
        outside.mkdir()
        folder.rmdir()
        try:
            folder.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Cannot create directory symbolic links")
        with self.assertRaises(ValueError):
            validate_secure_output_path(self.root, expected)

    def test_watch_checks_final_size_even_if_process_already_exited(self):
        from forgeos.output_capture import wait_native_output_bounded
        class Completed:
            killed = False
            def WaitForSingleObject(self, process, delay): return 0
            def TerminateJobObject(self, job, code):
                self.killed = True
                return 1
        kernel = Completed()
        file = self.root / "test-output.txt"
        with tempfile.TemporaryFile(mode="w+b") as stdout:
            file.write_bytes(b"z" * (MAX_RAW_BYTES + 1))
            with self.assertRaisesRegex(OSError, "Child-generated test output"):
                wait_native_output_bounded(
                    1, 2, stdout, 2, kernel32=kernel, wait_timeout=258,
                    wait_object_0=0, watched_files=(file,))
        self.assertTrue(kernel.killed)

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
