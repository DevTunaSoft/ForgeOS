"""Offline Browsentic MCP contract tests (never contacts a real browser)."""
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from forgeos.browsentic_readonly import (
    _read_diagram, compact_diagram, observe_paired_browser, main,
)


class FakeProcess:
    def __init__(self, replies):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"".join(json.dumps(x).encode() + b"\n" for x in replies))
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def ok_replies(text="button Save"):
    return [
        {"jsonrpc": "2.0", "id": 1,
         "result": {"protocolVersion": "2025-03-26", "capabilities": {},
                    "serverInfo": {"name": "browsentic", "version": "0.9"}}},
        {"jsonrpc": "2.0", "id": 2, "result": {"contents": [
            {"uri": "browsentic://page/diagram", "mimeType": "text/plain", "text": text}]}}
    ]


class BrowsenticReadOnlyTests(unittest.TestCase):
    def test_mcp_issues_only_readonly_messages(self):
        process = FakeProcess(ok_replies())
        self.assertEqual(_read_diagram(process, 2), "button Save")
        sent = [json.loads(x) for x in process.stdin.getvalue().splitlines()]
        self.assertEqual([x["method"] for x in sent],
                         ["initialize", "notifications/initialized", "resources/read"])
        self.assertEqual(sent[2]["params"], {"uri": "browsentic://page/diagram"})

    def test_malformed_handshake_fails_closed(self):
        process = FakeProcess([{"jsonrpc": "2.0", "id": 1, "result": {}}])
        with self.assertRaises(ValueError):
            _read_diagram(process, 2)

    def test_unexpected_resource_is_rejected(self):
        packets = ok_replies()
        packets[1]["result"]["contents"][0]["uri"] = "browsentic://page/current"
        with self.assertRaises(ValueError):
            _read_diagram(FakeProcess(packets), 2)

    def test_error_packets_never_expose_server_contents(self):
        packets = ok_replies()
        packets[1] = {"jsonrpc": "2.0", "id": 2,
                      "error": {"code": -32603, "message": "private URL and token"}}
        with self.assertRaisesRegex(ValueError, "MCP request failed") as cm:
            _read_diagram(FakeProcess(packets), 2)
        self.assertNotIn("private URL", str(cm.exception))

    def test_excessive_json_line_fails_closed(self):
        process = FakeProcess(ok_replies("A" * 70_000))
        with self.assertRaises(ValueError):
            _read_diagram(process, 2)

    def test_offline_process_fails_closed(self):
        with self.assertRaises(ValueError):
            _read_diagram(FakeProcess([]), 2)

    def test_bounded_text_and_best_effort_redaction(self):
        result = compact_diagram("password=secret123\ncontact jane@example.com\n" + "heading\n" * 80)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.text), 2400)
        self.assertLessEqual(result.controls, 32)
        self.assertNotIn("secret123", result.text)
        self.assertNotIn("jane@example.com", result.text)
        self.assertIn("UNTRUSTED", result.text)

    def test_reject_resource_larger_than_budget(self):
        with self.assertRaises(ValueError):
            compact_diagram("x" * 50000)

    @patch("forgeos.browsentic_readonly.subprocess.Popen")
    @patch("forgeos.browsentic_readonly.shutil.which", return_value="/bin/browsentic")
    def test_lazy_launch_and_cleanup(self, _which, popen):
        process = FakeProcess(ok_replies("button Next"))
        popen.return_value = process
        result = observe_paired_browser()
        self.assertIn("Next", result.text)
        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(popen.call_args.args[0][-1], "mcp")

    @patch("forgeos.browsentic_readonly.subprocess.Popen")
    @patch("forgeos.browsentic_readonly.shutil.which", return_value=None)
    def test_missing_install_never_launches(self, _which, popen):
        with self.assertRaises(RuntimeError):
            observe_paired_browser()
        popen.assert_not_called()

    @patch("forgeos.browsentic_readonly.observe_paired_browser")
    def test_command_requires_explicit_consent(self, observer):
        with self.assertRaises(SystemExit) as result:
            main([])
        self.assertEqual(result.exception.code, 2)
        observer.assert_not_called()

    def test_reject_invalid_deadlines(self):
        for v in (True, 0, 31, "3"):
            with self.subTest(v=v), self.assertRaises(ValueError):
                observe_paired_browser(timeout_seconds=v)


if __name__ == "__main__":
    unittest.main()
