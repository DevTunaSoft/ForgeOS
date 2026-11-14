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
        result = run_streamed_process(
            command, self.root, timeout=10, store=self.store)
        self.assertEqual(result["returncode"], 0)
        self.assertTrue(result["capture_complete"])
        self.assertIn("HELLO", result["output"])
        self.assertIn("ERROR", result["output"])

    def test_spawn_preserves_failure_exit_code_and_tail(self):
        script = ("import sys; sys.stdout.write('INFO hello\\n' * 260000); "
                  "sys.stdout.flush(); sys.stderr.write('AssertionError final\\n'); "
                  "sys.exit(29)")
        result = run_streamed_process(
            [sys.executable, "-c", script], self.root, timeout=30,
            store=self.store)
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
                [sys.executable, "-c", script], self.root, timeout=30,
                store=self.store)
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

    def test_owned_pytest_log_is_allowlisted_but_other_paths_rejected(self):
        from forgeos.output_capture import validate_secure_output_path
        directory = self.root / ".forgeos-runtime"
        directory.mkdir(exist_ok=True)
        owned = directory / "pytest-log.txt"
        self.assertEqual(validate_secure_output_path(self.root, owned), owned)
        with self.assertRaises(ValueError):
            validate_secure_output_path(self.root, directory / "external.log")

    def test_pytest_log_overflow_terminates_native_job(self):
        from forgeos.output_capture import wait_native_output_bounded
        directory = self.root / ".forgeos-runtime"
        directory.mkdir(exist_ok=True)
        log = directory / "pytest-log.txt"
        with log.open("wb") as stream:
            stream.truncate(MAX_RAW_BYTES + 1)
        class Kernel:
            killed = False
            def WaitForSingleObject(self, process, ms):
                return 0
            def TerminateJobObject(self, job, status):
                self.killed = True
                return 1
        kernel = Kernel()
        with tempfile.TemporaryFile(mode="w+b") as native:
            with self.assertRaisesRegex(OSError, "disk limit"):
                wait_native_output_bounded(
                    10, 11, native, 5, kernel32=kernel,
                    wait_timeout=258, wait_object_0=0,
                    watched_files=(log,))
        self.assertTrue(kernel.killed)

    def test_native_watcher_enforces_combined_output_budget(self):
        from forgeos.output_capture import wait_native_output_bounded
        first = self.root / "test-output.txt"
        second = self.root / "pytest-log.txt"
        with first.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024)
        with second.open("wb") as stream:
            stream.truncate(9 * 1024 * 1024)

        class Kernel:
            killed = False
            def WaitForSingleObject(self, process, ms):
                return 0
            def TerminateJobObject(self, job, status):
                self.killed = True
                return 1

        kernel = Kernel()
        with tempfile.TemporaryFile(mode="w+b") as native:
            native.truncate(9 * 1024 * 1024)
            with self.assertRaisesRegex(OSError, "Combined test outputs"):
                wait_native_output_bounded(
                    10, 20, native, 5, kernel32=kernel,
                    wait_timeout=258, wait_object_0=0,
                    watched_files=(first, second))
        self.assertTrue(kernel.killed)

    def test_native_watcher_accepts_exact_combined_output_budget(self):
        from forgeos.output_capture import wait_native_output_bounded
        first = self.root / "test-output.txt"
        second = self.root / "pytest-log.txt"
        with first.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024)
        with second.open("wb") as stream:
            stream.truncate(8 * 1024 * 1024)

        class Kernel:
            def WaitForSingleObject(self, process, ms):
                return 0
            def TerminateJobObject(self, job, status):
                raise AssertionError("Exact-cap test must not terminate")

        with tempfile.TemporaryFile(mode="w+b") as native:
            native.truncate(8 * 1024 * 1024)
            status = wait_native_output_bounded(
                10, 20, native, 5, kernel32=Kernel(),
                wait_timeout=258, wait_object_0=0,
                watched_files=(first, second))
        self.assertEqual(status, 0)

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
        kernel = FakeKernel()
        with tempfile.TemporaryFile(mode="w+b") as stdout:
            with file.open("wb") as sink:
                sink.truncate(MAX_RAW_BYTES + 1)
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

    def test_child_creates_secondary_output_during_wait(self):
        from forgeos.output_capture import wait_native_output_bounded
        path = self.root / "test-output.txt"
        class Kernel:
            waits = 0
            killed = False
            def WaitForSingleObject(self, process, ms):
                self.waits += 1
                if self.waits == 2:
                    with path.open("wb") as sink:
                        sink.truncate(MAX_RAW_BYTES + 1)
                return 0 if self.killed else 258
            def TerminateJobObject(self, job, code):
                self.killed = True
                return 1
        kernel = Kernel()
        with tempfile.TemporaryFile(mode="w+b") as stdout:
            with self.assertRaisesRegex(OSError, "Child-generated test output"):
                wait_native_output_bounded(
                    1, 2, stdout, 3, kernel32=kernel, wait_timeout=258,
                    wait_object_0=0, watched_files=(path,))
        self.assertTrue(kernel.killed)
        self.assertGreaterEqual(kernel.waits, 2)

    def test_child_generated_output_within_limit_completes(self):
        from forgeos.output_capture import wait_native_output_bounded
        file = self.root / "test-output.txt"
        file.write_bytes(b"ERROR limited\n")
        class Kernel:
            def WaitForSingleObject(self, process, ms): return 0
            def TerminateJobObject(self, job, code):
                raise AssertionError("Safe completed output was killed")
        with tempfile.TemporaryFile(mode="w+b") as stdout:
            status = wait_native_output_bounded(
                1, 2, stdout, 3, kernel32=Kernel(), wait_timeout=258,
                wait_object_0=0, watched_files=(file,))
        self.assertEqual(status, 0)

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
            with file.open("wb") as sink:
                sink.truncate(MAX_RAW_BYTES + 1)
            with self.assertRaisesRegex(OSError, "Child-generated test output"):
                wait_native_output_bounded(
                    1, 2, stdout, 2, kernel32=kernel, wait_timeout=258,
                    wait_object_0=0, watched_files=(file,))
        self.assertTrue(kernel.killed)

    @unittest.skipIf(os.name == "nt", "POSIX process-group containment")
    def test_timeout_kills_grandchild_without_orphaned_side_effects(self):
        # A helper inherits the test subprocess's session but outlives its
        # parent. Its delayed write must never occur after timeout.
        marker = self.root / "orphan-marker.txt"
        helper = (
            "import pathlib,time,sys; "
            "time.sleep(1.5); pathlib.Path(sys.argv[1]).write_text('orphan')"
        )
        parent = (
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]], "
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
            "stderr=subprocess.DEVNULL); "
            "print('helper started',flush=True); time.sleep(20)"
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            run_streamed_process(
                [sys.executable, "-c", parent, helper, str(marker)],
                self.root, timeout=0.5)
        import time
        time.sleep(1.7)
        self.assertFalse(marker.exists(), "Timed-out test left a live child")

    @unittest.skipIf(os.name == "nt", "POSIX process-group leak detection")
    def test_parent_success_does_not_hide_surviving_child(self):
        import time
        marker = self.root / "outlived-parent.txt"
        child = (
            "import pathlib,time,sys; time.sleep(1.2); "
            "pathlib.Path(sys.argv[1]).write_text('outlived')"
        )
        parent = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]], "
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
            "stderr=subprocess.DEVNULL); "
            "print('parent finished',flush=True)"
        )
        outcome = run_streamed_process(
            [sys.executable, "-c", parent, child, str(marker)],
            self.root, timeout=6)
        self.assertEqual(outcome["returncode"], 0)
        self.assertFalse(outcome["passed"])
        self.assertFalse(outcome["capture_complete"])
        self.assertEqual(outcome["capture_error"], "test-child-outlived-parent")
        self.assertIn("test helper survived", outcome["output"])
        time.sleep(1.4)
        self.assertFalse(marker.exists(), "Test helper was not terminated")

    def test_output_flood_terminates_early_with_bounded_capture(self):
        from unittest.mock import patch
        from forgeos.output_capture import BoundedSpool as OriginalSpool
        # Use a tiny real spool so this live-process regression does not
        # waste memory or disk generating tens of MiB during CI.
        script = (
            "import os,time\n"
            "for _ in range(100000):\n"
            "    os.write(1,b'x'*4096)\n"
            "time.sleep(20)\n"
        )
        with patch("forgeos.output_capture.BoundedSpool",
                   side_effect=lambda: OriginalSpool(cap=8192)):
            result = run_streamed_process(
                [sys.executable, "-c", script], self.root, timeout=10)
        self.assertFalse(result["passed"])
        self.assertFalse(result["capture_complete"])
        self.assertGreater(result["capture_source_bytes"], 8192)
        self.assertLessEqual(result["capture_source_bytes"], 8192 + 65536)
        self.assertIn("full evidence unavailable", result["output"])

    def test_output_at_spool_cap_can_still_pass(self):
        from unittest.mock import patch
        from forgeos.output_capture import BoundedSpool as OriginalSpool
        with patch("forgeos.output_capture.BoundedSpool",
                   side_effect=lambda: OriginalSpool(cap=8192)):
            result = run_streamed_process(
                [sys.executable, "-c",
                 "import os; os.write(1,b'x'*8192)"],
                self.root, timeout=10)
        self.assertTrue(result["passed"])
        self.assertTrue(result["capture_complete"])
        self.assertEqual(result["capture_source_bytes"], 8192)

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
