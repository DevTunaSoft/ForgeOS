"""Offline acceptance-harness tests; one Windows-native memory probe test."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from forgeos import browser_acceptance as acceptance


class FakeClient:
    pid = 12345

    def __init__(self, *, output: bytes = b"Browsentic page diagram (UNTRUSTED):\nheading Ready\n",
                 returncode: int = 0, delay: float = 0):
        self.output = output
        self.final_code = returncode
        self.returncode = None
        self.delay = delay
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        if self.delay:
            time.sleep(self.delay)
        self.returncode = self.final_code
        return self.output, b"PRIVATE SERVER DIAGNOSTICS"

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = self.final_code
        return self.returncode


class TimeoutClient(FakeClient):
    def communicate(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired(["fake"], timeout)
        return b"", b""


class WindowsBrowserAcceptanceTests(unittest.TestCase):
    def _invoke(self, fake=None, sample=None, **options):
        fake = fake or FakeClient(delay=0.15)
        args = dict(origin="https://example.com", confirmed=True)
        args.update(options)
        def mocked_capture(client, timeout_seconds):
            # Existing integration-level fakes intentionally bypass the
            # native pipe reader; dedicated real-subprocess tests cover it.
            output, _ = client.communicate(timeout=timeout_seconds)
            return output[:acceptance.MAX_CAPTURE_BYTES], (
                len(output) > acceptance.MAX_CAPTURE_BYTES
            )

        with patch.object(acceptance, "_is_windows", return_value=True), \
             patch.object(acceptance.shutil, "which", return_value="C:/tools/browsentic.cmd"), \
             patch.object(acceptance.subprocess, "Popen", return_value=fake) as popen, \
             patch.object(acceptance, "_capture_limited", side_effect=mocked_capture), \
             patch.object(acceptance, "_windows_process_tree",
                          return_value=acceptance.TreeMemory(33_000_000, 2, 0),
                          side_effect=sample):
            result = acceptance.run_acceptance(**args)
        return result, popen

    def test_readonly_acceptance_passes_and_captures_memory(self):
        report, popen = self._invoke()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["reason_code"], "OBSERVATION_VERIFIED")
        self.assertEqual(report["peak_client_tree_working_set_bytes"], 33_000_000)
        self.assertGreaterEqual(report["memory_samples"], 1)
        self.assertEqual(report["memory_status"], "CAPTURED")
        self.assertGreaterEqual(report["max_client_tree_processes"], 2)
        self.assertTrue(popen.call_args.kwargs["shell"] is False)
        command = popen.call_args.args[0]
        self.assertEqual(command[:3], [sys.executable, "-m", "forgeos.browsentic_readonly"])
        self.assertIn("--allow-current-tab", command)
        self.assertNotIn("--approve-scroll", command)
        self.assertNotIn("--changes-only", command)
        self.assertNotIn("https://example.com", json.dumps(report))
        self.assertNotIn("PRIVATE SERVER DIAGNOSTICS", json.dumps(report))

    def test_approved_change_only_acceptance_passes_one_action_flag(self):
        output = b"Browsentic visible changes (UNTRUSTED):\n+ button Next\n"
        report, popen = self._invoke(FakeClient(output=output, delay=0.12),
                                    scroll="down", approve_scroll=True, changes_only=True)
        self.assertEqual(report["status"], "PASS")
        args = popen.call_args.args[0]
        self.assertEqual(args.count("--scroll-direction"), 1)
        self.assertEqual(args.count("--approve-scroll"), 1)
        self.assertEqual(args.count("--changes-only"), 1)

    def test_real_pipe_capture_stays_bounded_on_megabyte_output(self):
        # Real child, not a mocked communicate() call. No browser/network.
        command = [
            sys.executable, "-u", "-c",
            "import sys; sys.stdout.buffer.write(b'X' * 1048576); "
            "sys.stdout.buffer.flush()",
        ]
        with subprocess.Popen(command, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
            captured, overflow = acceptance._capture_limited(child, 4)
            self.assertTrue(overflow)
            self.assertLessEqual(len(captured), acceptance.MAX_CAPTURE_BYTES)
            self.assertIsNotNone(child.poll())

    def test_real_pipe_capture_allows_short_successful_output(self):
        script = "import sys; sys.stdout.buffer.write(b'Browsentic page diagram (test)\\n')"
        with subprocess.Popen([sys.executable, "-c", script],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as child:
            output, overflow = acceptance._capture_limited(child, 4)
            self.assertFalse(overflow)
            self.assertTrue(output.startswith(b"Browsentic page diagram"))
            self.assertEqual(child.returncode, 0)

    def test_real_pipe_capture_times_out_without_child_leak(self):
        with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as child:
            with self.assertRaises(subprocess.TimeoutExpired):
                acceptance._capture_limited(child, 0.2)
            self.assertIsNotNone(child.poll())

    def test_capture_reader_never_calls_communicate(self):
        script = "import sys; sys.stdout.buffer.write(b'OK')"
        with subprocess.Popen([sys.executable, "-c", script],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as child, \
             patch.object(child, "communicate", side_effect=AssertionError(
                 "communicate must not be used")):
            output, overflow = acceptance._capture_limited(child, 4)
            self.assertEqual(output, b"OK")
            self.assertFalse(overflow)

    def test_default_does_not_start_without_explicit_consent(self):
        with patch.object(acceptance.subprocess, "Popen") as popen:
            result = acceptance.run_acceptance(origin="https://example.com", confirmed=False)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason_code"], "PUBLIC_TAB_CONFIRMATION_REQUIRED")
        popen.assert_not_called()

    def test_non_windows_refuses_without_launch(self):
        with patch.object(acceptance, "_is_windows", return_value=False), \
             patch.object(acceptance.subprocess, "Popen") as popen:
            result = acceptance.run_acceptance(origin="https://example.com", confirmed=True)
        self.assertEqual(result["reason_code"], "WINDOWS_REQUIRED")
        popen.assert_not_called()

    def test_missing_browsentic_refuses_without_launch(self):
        with patch.object(acceptance, "_is_windows", return_value=True), \
             patch.object(acceptance.shutil, "which", return_value=None), \
             patch.object(acceptance.subprocess, "Popen") as popen:
            result = acceptance.run_acceptance(origin="https://example.com", confirmed=True)
        self.assertEqual(result["reason_code"], "BROWSENTIC_NOT_INSTALLED")
        popen.assert_not_called()

    def test_invalid_origin_or_unapproved_scroll_fails_before_launch(self):
        for kwargs in (
            dict(origin="https://example.com/private", confirmed=True),
            dict(origin="http://example.com", confirmed=True),
            dict(origin="https://example.com", confirmed=True, scroll="down"),
            dict(origin="https://example.com", confirmed=True, changes_only=True),
            dict(origin="https://example.com", confirmed=True, timeout_seconds=True),
            dict(origin="https://example.com", confirmed=True, timeout_seconds=150),
        ):
            with self.subTest(kwargs=kwargs), patch.object(
                acceptance.subprocess, "Popen"
            ) as popen, self.assertRaises(ValueError):
                acceptance.run_acceptance(**kwargs)
            popen.assert_not_called()

    def test_connector_denied_is_blocked(self):
        report, _ = self._invoke(FakeClient(returncode=2))
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["reason_code"], "CONNECTOR_REJECTED")
        self.assertEqual(report["observation_chars"], 0)

    def test_invalid_or_oversized_stdout_fails_closed(self):
        for output, code in (
            (b"PRIVATE WEBPAGE TEXT", "INVALID_OBSERVATION"),
            (b"Browsentic page diagram (\xff\xfe)", "INVALID_OBSERVATION"),
            (b"X" * (acceptance.MAX_CAPTURE_BYTES + 1), "OUTPUT_OVERSIZE"),
        ):
            with self.subTest(code=code):
                report, _ = self._invoke(FakeClient(output=output))
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["reason_code"], code)
                self.assertEqual(report["observation_chars"], 0)

    def test_timeout_kills_process_once_without_action_retry(self):
        fake = TimeoutClient()
        with patch.object(acceptance, "_stop_tree", side_effect=lambda p: p.kill()) as stopper:
            report, popen = self._invoke(fake, timeout_seconds=5)
        self.assertEqual(report["reason_code"], "TIMEOUT")
        self.assertEqual(report["status"], "FAIL")
        stopper.assert_called_once_with(fake)
        self.assertEqual(fake.kill_calls, 1)
        popen.assert_called_once()

    def test_explicit_external_process_trees_are_sampled_separately(self):
        def sample(pid):
            values = {12345: 33_000_000, 321: 80_000_000, 654: 150_000_000}
            return acceptance.TreeMemory(values[pid], 2, 0)

        report, popen = self._invoke(sample=sample, external_pids=(321, 654))
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["peak_client_tree_working_set_bytes"], 33_000_000)
        self.assertEqual(report["external_pid_tree_peak_working_set_bytes"],
                         {"321": 80_000_000, "654": 150_000_000})
        self.assertNotIn("--external-pid", popen.call_args.args[0])
        self.assertNotIn("321", str(popen.call_args.args[0]))

    def test_bad_external_pids_are_rejected_without_process_launch(self):
        for external in ((0,), (-1,), (123, 123), (1, 2, 3),
                         ("1",), (True,), ([],), [1], (2**45,)):
            with self.subTest(external=external), patch.object(
                acceptance.subprocess, "Popen"
            ) as popen, self.assertRaises(ValueError):
                acceptance.run_acceptance(
                    origin="https://example.com", confirmed=True,
                    external_pids=external)
            popen.assert_not_called()

    @patch.object(acceptance, "run_acceptance")
    def test_cli_accepts_explicit_memory_roots(self, runner):
        runner.return_value = acceptance._make_report("PASS", "OBSERVATION_VERIFIED")
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(acceptance.main([
                "--allow-origin", "https://example.com",
                "--confirm-public-test-tab",
                "--external-pid", "321", "--external-pid", "654",
            ]), 0)
        self.assertEqual(runner.call_args.kwargs["external_pids"], (321, 654))

    def test_no_sample_is_not_reported_as_measured_memory(self):
        fake = FakeClient()
        result, _ = self._invoke(fake)
        # A very fast client may finish before the first sampler iteration.
        self.assertIn(result["memory_status"], ("CAPTURED", "UNVERIFIED"))
        self.assertEqual(result["memory_samples"] == 0,
                         result["memory_status"] == "UNVERIFIED")

    def test_cli_prints_only_json_with_no_tab_content(self):
        fake = acceptance._make_report("BLOCKED", "PUBLIC_TAB_CONFIRMATION_REQUIRED")
        with patch.object(acceptance, "run_acceptance", return_value=fake), \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            exit_code = acceptance.main(["--allow-origin", "https://example.com"])
        self.assertEqual(exit_code, 2)
        result = json.loads(out.getvalue())
        self.assertFalse(result["contains_page_content"])
        self.assertNotIn("https://example.com", out.getvalue())

    def test_sampler_rejects_invalid_host_without_network(self):
        with patch.object(acceptance.subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):
                acceptance.run_acceptance(origin="https://127.0.0.1", confirmed=True)
            popen.assert_not_called()

    def test_windows_api_bindings_are_cached_without_polling(self):
        # Initialization is lazy and reusable; no process snapshots are cached.
        api = acceptance._windows_api()
        self.assertIs(acceptance._windows_api(), api)
        if os.name == "nt":
            self.assertIsNotNone(api)
            self.assertEqual(len(api), 4)
        else:
            self.assertIsNone(api)

    def test_non_windows_memory_probe_returns_unavailable(self):
        if os.name != "nt":
            self.assertIsNone(acceptance._windows_process_tree(os.getpid()))

    @unittest.skipUnless(os.name == "nt", "Actual Win32 Toolhelp snapshot is Windows-only")
    def test_native_windows_process_memory_probe(self):
        with subprocess.Popen([
            sys.executable, "-c", "import time;time.sleep(3)"
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL) as child:
            for _ in range(5):
                sample = acceptance._windows_process_tree(child.pid)
                if sample is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(sample)
            self.assertGreaterEqual(sample.process_count, 1)
            self.assertGreater(sample.working_set_bytes, 0)
            self.assertLessEqual(sample.unreadable_processes, sample.process_count)
            before_hits = acceptance._windows_api.cache_info().hits
            next_sample = acceptance._windows_process_tree(child.pid)
            self.assertIsNotNone(next_sample)
            self.assertGreaterEqual(acceptance._windows_api.cache_info().hits,
                                    before_hits + 1)
            child.terminate()
            child.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
