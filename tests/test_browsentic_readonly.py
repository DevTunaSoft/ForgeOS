"""Offline Browsentic MCP contract tests (never contacts a real browser)."""
import io
import json
import subprocess
import sys
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


def status_reply(request_id, url="https://example.com/docs", *, connected=True,
                 browser="Chrome"):
    return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [
        {"type": "text", "text": json.dumps({
            "connected": connected, "activeTab": {"url": url, "title": "PRIVATE TITLE"},
            "browser": browser,
        })}]}}


def ok_replies(text="button Save"):
    return [
        {"jsonrpc": "2.0", "id": 1,
         "result": {"protocolVersion": "2025-03-26", "capabilities": {},
                    "serverInfo": {"name": "browsentic", "version": "0.9"}}},
        status_reply(2),
        {"jsonrpc": "2.0", "id": 3, "result": {"contents": [
            {"uri": "browsentic://page/diagram", "mimeType": "text/plain", "text": text}]}},
        status_reply(4),
    ]


class BrowsenticReadOnlyTests(unittest.TestCase):
    def test_real_stdio_transport_round_trip(self):
        """Exercise the real subprocess pipes on Linux and Windows; no browser."""
        server = r'''
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    number = message["id"]
    if number == 1:
        result = {"protocolVersion": "2025-03-26"}
    elif number in (2, 4):
        state = {"connected": True, "browser": "Chrome",
                 "activeTab": {"url": "https://example.com/docs",
                               "title": "PRIVATE TITLE"}}
        result = {"content": [{"type": "text", "text": json.dumps(state)}]}
    elif number == 3:
        result = {"contents": [{"uri": "browsentic://page/diagram",
                                "mimeType": "text/plain", "text": "heading Welcome"}]}
    else:
        break
    print(json.dumps({"jsonrpc": "2.0", "id": number, "result": result}), flush=True)
'''
        with subprocess.Popen(
            [sys.executable, "-u", "-c", server],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        ) as child:
            self.assertEqual(
                _read_diagram(child, 5, "https://example.com"),
                "heading Welcome",
            )
            child.terminate()
            child.wait(timeout=3)

    def test_real_stdio_timeout_fails_closed(self):
        """A stalled MCP bridge must consume a bounded deadline, not hang."""
        with subprocess.Popen(
            [sys.executable, "-u", "-c", "import time; time.sleep(20)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        ) as child:
            with self.assertRaises(TimeoutError):
                _read_diagram(child, 0.15, "https://example.com")
            child.terminate()
            child.wait(timeout=3)

    def test_mcp_issues_only_readonly_messages(self):
        process = FakeProcess(ok_replies())
        self.assertEqual(_read_diagram(process, 2, "https://example.com"), "button Save")
        sent = [json.loads(x) for x in process.stdin.getvalue().splitlines()]
        self.assertEqual([x["method"] for x in sent],
                         ["initialize", "notifications/initialized", "tools/call",
                          "resources/read", "tools/call"])
        self.assertEqual(sent[3]["params"], {"uri": "browsentic://page/diagram"})
        self.assertEqual(sent[2]["params"], {"name": "browsentic_status", "arguments": {}})
        self.assertEqual(sent[4]["params"], {"name": "browsentic_status", "arguments": {}})

    def test_malformed_handshake_fails_closed(self):
        process = FakeProcess([{"jsonrpc": "2.0", "id": 1, "result": {}}])
        with self.assertRaises(ValueError):
            _read_diagram(process, 2, "https://example.com")

    def test_unexpected_resource_is_rejected(self):
        packets = ok_replies()
        packets[2]["result"]["contents"][0]["uri"] = "browsentic://page/current"
        with self.assertRaises(ValueError):
            _read_diagram(FakeProcess(packets), 2, "https://example.com")

    def test_error_packets_never_expose_server_contents(self):
        packets = ok_replies()
        packets[2] = {"jsonrpc": "2.0", "id": 3,
                      "error": {"code": -32603, "message": "private URL and token"}}
        with self.assertRaisesRegex(ValueError, "MCP request failed") as cm:
            _read_diagram(FakeProcess(packets), 2, "https://example.com")
        self.assertNotIn("private URL", str(cm.exception))

    def test_excessive_json_line_fails_closed(self):
        process = FakeProcess(ok_replies("A" * 70_000))
        with self.assertRaises(ValueError):
            _read_diagram(process, 2, "https://example.com")

    def test_offline_process_fails_closed(self):
        with self.assertRaises(ValueError):
            _read_diagram(FakeProcess([]), 2, "https://example.com")

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
        result = observe_paired_browser(permitted_origin="https://example.com")
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
            observe_paired_browser(permitted_origin="https://example.com")
        popen.assert_not_called()

    @patch("forgeos.browsentic_readonly.observe_paired_browser")
    def test_command_requires_explicit_consent(self, observer):
        with self.assertRaises(SystemExit) as result:
            main([])
        self.assertEqual(result.exception.code, 2)
        observer.assert_not_called()


    def test_mismatched_origin_is_blocked_before_reading_diagram(self):
        packets = ok_replies("PRIVATE CONTENT")
        packets[1] = status_reply(2, "https://other.example/private")
        process = FakeProcess(packets)
        with self.assertRaisesRegex(ValueError, "outside the approved origin"):
            _read_diagram(process, 2, "https://example.com")
        sent = [json.loads(line)["method"] for line in process.stdin.getvalue().splitlines()]
        self.assertNotIn("resources/read", sent)

    def test_tab_switch_during_observation_fails_closed(self):
        packets = ok_replies("PRIVATE CONTENT")
        packets[3] = status_reply(4, "https://example.com/different")
        with self.assertRaisesRegex(ValueError, "tab changed"):
            _read_diagram(FakeProcess(packets), 2, "https://example.com")

    def test_browser_switch_during_observation_fails_closed(self):
        packets = ok_replies()
        packets[3] = status_reply(4, browser="Edge")
        with self.assertRaisesRegex(ValueError, "tab changed"):
            _read_diagram(FakeProcess(packets), 2, "https://example.com")

    def test_disconnected_status_fails_closed(self):
        packets = ok_replies()
        packets[1] = status_reply(2, connected=False)
        with self.assertRaisesRegex(ValueError, "not connected"):
            _read_diagram(FakeProcess(packets), 2, "https://example.com")

    def test_malformed_status_fails_closed(self):
        packets = ok_replies()
        packets[1] = {"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": "SECRET USER URL"}]}}
        with self.assertRaisesRegex(ValueError, "Invalid browser status") as cm:
            _read_diagram(FakeProcess(packets), 2, "https://example.com")
        self.assertNotIn("SECRET", str(cm.exception))

    def test_rejects_noncanonical_origin_without_opening_process(self):
        with patch("forgeos.browsentic_readonly.shutil.which") as which:
            for origin in ("http://example.com", "https://example.com/path",
                           "https://localhost", "https://example.com?secret=x",
                           "https://user:pw@example.com"):
                with self.subTest(origin=origin), self.assertRaises(ValueError):
                    observe_paired_browser(permitted_origin=origin)
            which.assert_not_called()

    def test_status_url_is_not_in_printed_diagram(self):
        packets = ok_replies("Safe heading")
        url = "https://example.com/account?token=PRIVATE_SECRET"
        packets[1] = status_reply(2, url)
        packets[3] = status_reply(4, url)
        result = _read_diagram(FakeProcess(packets), 2, "https://example.com")
        self.assertEqual(result, "Safe heading")
        self.assertNotIn("PRIVATE_SECRET", result)

    @patch("forgeos.browsentic_readonly.observe_paired_browser")
    def test_cli_requires_both_tab_consent_and_origin(self, observer):
        with self.assertRaises(SystemExit) as error:
            main(["--allow-origin", "https://example.com"])
        self.assertEqual(error.exception.code, 2)
        observer.assert_not_called()

    def test_scroll_requires_explicit_approval_before_any_process(self):
        from forgeos.browsentic_readonly import _check_scroll
        for direction, approval in (("down", False), ("right", True),
                                    ("Down", True), ([], True), ("up", 1),
                                    (None, True), ("", True)):
            with self.subTest(direction=direction, approval=approval):
                with self.assertRaises(ValueError):
                    _check_scroll(direction, approval)
        with patch("forgeos.browsentic_readonly.shutil.which") as which:
            with self.assertRaises(ValueError):
                observe_paired_browser(permitted_origin="https://example.com",
                                       scroll_direction="down", approve_scroll=False)
            which.assert_not_called()

    def test_approved_scroll_is_exactly_one_safe_tool_call(self):
        from forgeos.browsentic_readonly import _read_diagram
        replies = [
            ok_replies()[0], status_reply(2),
            {"jsonrpc": "2.0", "id": 3, "result": {
                "content": [{"type": "text", "text": "{}"}]}},
            status_reply(4),
            {"jsonrpc": "2.0", "id": 5, "result": {"contents": [{
                "uri": "browsentic://page/diagram", "text": "heading Updated"}]}},
            status_reply(6),
        ]
        proc = FakeProcess(replies)
        output = _read_diagram(proc, 2, "https://example.com",
                               scroll_direction="down", approve_scroll=True)
        self.assertEqual(output, "heading Updated")
        sent = [json.loads(line) for line in proc.stdin.getvalue().splitlines()]
        self.assertEqual([packet["method"] for packet in sent], [
            "initialize", "notifications/initialized", "tools/call",
            "tools/call", "tools/call", "resources/read", "tools/call",
        ])
        self.assertEqual(sent[3]["params"], {
            "name": "page_scrollTo",
            "arguments": {"direction": "down", "behavior": "instant"},
        })
        self.assertEqual(
            sum(packet.get("params", {}).get("name") == "page_scrollTo" for packet in sent),
            1,
        )

    def test_scroll_result_error_does_not_read_or_retry(self):
        replies = [
            ok_replies()[0], status_reply(2),
            {"jsonrpc": "2.0", "id": 3, "result": {
                "isError": True, "content": [{"type": "text", "text": "SECRET"}]}},
        ]
        proc = FakeProcess(replies)
        with self.assertRaisesRegex(ValueError, "scroll was refused") as error:
            _read_diagram(proc, 2, "https://example.com",
                          scroll_direction="up", approve_scroll=True)
        self.assertNotIn("SECRET", str(error.exception))
        sent = [json.loads(x) for x in proc.stdin.getvalue().splitlines()]
        self.assertEqual(
            sum(p.get("params", {}).get("name") == "page_scrollTo" for p in sent), 1)
        self.assertNotIn("resources/read", [p["method"] for p in sent])

    def test_scroll_switch_after_action_never_reads_diagram(self):
        replies = [
            ok_replies()[0], status_reply(2),
            {"jsonrpc": "2.0", "id": 3, "result": {"content": []}},
            status_reply(4, "https://malicious.example/switch"),
        ]
        proc = FakeProcess(replies)
        with self.assertRaises(ValueError):
            _read_diagram(proc, 2, "https://example.com",
                          scroll_direction="bottom", approve_scroll=True)
        sent = [json.loads(x)["method"] for x in proc.stdin.getvalue().splitlines()]
        self.assertNotIn("resources/read", sent)

    def test_malformed_scroll_result_fails_closed(self):
        replies = [
            ok_replies()[0], status_reply(2),
            {"jsonrpc": "2.0", "id": 3, "result": {"content": "UNTRUSTED"}},
        ]
        proc = FakeProcess(replies)
        with self.assertRaisesRegex(ValueError, "Invalid browser scroll result"):
            _read_diagram(proc, 2, "https://example.com",
                          scroll_direction="top", approve_scroll=True)

    @patch("forgeos.browsentic_readonly.observe_paired_browser")
    def test_cli_passes_single_approved_scroll(self, observer):
        from forgeos.browser_observation import Observation
        observer.return_value = Observation("Safe text", 1, False)
        with patch("builtins.print") as printer:
            self.assertEqual(main([
                "--allow-origin", "https://example.com", "--allow-current-tab",
                "--scroll-direction", "down", "--approve-scroll",
            ]), 0)
        observer.assert_called_once_with(
            permitted_origin="https://example.com",
            scroll_direction="down", approve_scroll=True,
        )
        printer.assert_called_once_with("Safe text")

    @patch("forgeos.browsentic_readonly.shutil.which")
    def test_cli_rejects_scroll_without_approval(self, which):
        with self.assertRaises(SystemExit) as error:
            main(["--allow-current-tab", "--allow-origin", "https://example.com",
                  "--scroll-direction", "down"])
        self.assertEqual(error.exception.code, 2)
        which.assert_not_called()

    def test_reject_invalid_deadlines(self):
        for v in (True, 0, 31, "3"):
            with self.subTest(v=v), self.assertRaises(ValueError):
                observe_paired_browser(permitted_origin="https://example.com", timeout_seconds=v)


if __name__ == "__main__":
    unittest.main()
