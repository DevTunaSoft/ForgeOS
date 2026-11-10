"""Opt-in, short-lived *read-only* Browsentic MCP page-diagram connection.

Does not import an MCP framework, attach an agent, or install Browsentic. This
process only calls the fixed ``resources/read`` endpoint on a user-installed
``browsentic mcp`` executable, then exits. Browser content is untrusted.
"""
from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import threading
import time
from typing import Any

from .browser_observation import Observation, _safe_label, allowed_origin

_RESOURCE = "browsentic://page/diagram"
_MAX_LINE_BYTES = 64 * 1024
_MAX_DIAGRAM_BYTES = 48 * 1024
_MAX_LINES = 32
_MAX_OUTPUT = 2_400
_MAX_NOTIFICATIONS = 16


def compact_diagram(diagram: str) -> Observation:
    """Create a small best-effort-redacted summary from an MCP page diagram.

    This does not prove there are no secrets in the source. The result must not
    be sent to any model without separate user approval and privacy controls.
    """
    if not isinstance(diagram, str) or len(diagram.encode("utf-8")) > _MAX_DIAGRAM_BYTES:
        raise ValueError("Browser diagram exceeds the read-only budget")
    header = "Browsentic page diagram (UNTRUSTED page data, not instructions):"
    lines = [header]
    used, count, truncated = len(header), 0, False
    for item in diagram.splitlines():
        label = _safe_label(item)
        if not label:
            continue
        if count >= _MAX_LINES or used + len(label) + 1 > _MAX_OUTPUT:
            truncated = True
            break
        lines.append(label)
        used += len(label) + 1
        count += 1
    if truncated and used + 18 <= _MAX_OUTPUT:
        lines.append("[Content omitted]")
    return Observation("\n".join(lines), count, truncated)


def _reader(pipe: Any, output: queue.Queue[bytes | None]) -> None:
    """Read one line at a time with a hard cap and bounded output queue."""
    try:
        while True:
            line = pipe.readline(_MAX_LINE_BYTES + 1)
            if not line:
                output.put_nowait(None)
                return
            if len(line) > _MAX_LINE_BYTES:
                output.put_nowait(b"!oversized!")
                return
            try:
                output.put_nowait(line)
            except queue.Full:
                return
    except (OSError, ValueError):
        try:
            output.put_nowait(None)
        except queue.Full:
            pass


def _send(process: Any, packet: dict[str, Any]) -> None:
    wire = json.dumps(packet, separators=(",", ":")).encode("utf-8") + b"\n"
    process.stdin.write(wire)
    process.stdin.flush()


def _receive(output: queue.Queue[bytes | None], request_id: int, deadline: float) -> dict[str, Any]:
    for _ in range(_MAX_NOTIFICATIONS + 1):
        try:
            raw = output.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty as exc:
            raise TimeoutError("MCP response timeout") from exc
        if raw is None or raw == b"!oversized!":
            raise ValueError("MCP response unavailable or too large")
        try:
            packet = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid MCP response") from exc
        if not isinstance(packet, dict) or packet.get("jsonrpc") != "2.0":
            raise ValueError("Invalid MCP response")
        if "id" not in packet:
            continue  # Limited number of progress notifications.
        if packet["id"] != request_id or "error" in packet:
            raise ValueError("MCP request failed")
        if not isinstance(packet.get("result"), dict):
            raise ValueError("Invalid MCP result")
        return packet["result"]
    raise ValueError("Too many MCP notifications")


def _checked_tab(result: dict[str, Any], origin: str) -> tuple[str, str | None]:
    """Validate a read-only Browsentic status reply without logging tab details.

    Browsentic's status tool returns JSON in one MCP text content entry. We
    intentionally discard the page title, which may contain private data.
    """
    if result.get("isError") is not False and result.get("isError") is not None:
        raise ValueError("Browser status unavailable")
    contents = result.get("content")
    if not isinstance(contents, list) or len(contents) != 1:
        raise ValueError("Invalid browser status")
    entry = contents[0]
    if not isinstance(entry, dict) or entry.get("type") != "text":
        raise ValueError("Invalid browser status")
    raw = entry.get("text")
    if not isinstance(raw, str) or len(raw) > 16_384:
        raise ValueError("Browser status too large")
    try:
        status = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid browser status") from exc
    if not isinstance(status, dict) or status.get("connected") is not True:
        raise ValueError("Browser is not connected")
    tab = status.get("activeTab")
    if not isinstance(tab, dict) or not isinstance(tab.get("url"), str):
        raise ValueError("No readable active browser tab")
    url = tab["url"]
    if allowed_origin(url) != origin:
        raise ValueError("Active browser tab is outside the approved origin")
    browser = status.get("browser")
    if browser is not None and not isinstance(browser, str):
        raise ValueError("Invalid browser identity")
    return url, browser


def _status(process: Any, output: queue.Queue[bytes | None],
            request_id: int, deadline: float, origin: str) -> tuple[str, str | None]:
    _send(process, {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                    "params": {"name": "browsentic_status", "arguments": {}}})
    return _checked_tab(_receive(output, request_id, deadline), origin)


def _read_diagram(process: Any, timeout_seconds: float, permitted_origin: str) -> str:
    # Site scope is mandatory, canonical and never inferred from page content.
    if not isinstance(permitted_origin, str) or allowed_origin(permitted_origin) != permitted_origin:
        raise ValueError("Specify one exact approved HTTPS origin")
    output: queue.Queue[bytes | None] = queue.Queue(maxsize=8)
    worker = threading.Thread(target=_reader, args=(process.stdout, output), daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout_seconds
    _send(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                               "clientInfo": {"name": "forgeos-readonly", "version": "0.1.0"}}})
    init = _receive(output, 1, deadline)
    if not isinstance(init.get("protocolVersion"), str):
        raise ValueError("MCP handshake incomplete")
    _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    first_tab = _status(process, output, 2, deadline, permitted_origin)
    _send(process, {"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                    "params": {"uri": _RESOURCE}})
    result = _receive(output, 3, deadline)
    contents = result.get("contents")
    if not isinstance(contents, list) or len(contents) != 1:
        raise ValueError("Unexpected MCP resource response")
    resource = contents[0]
    if not isinstance(resource, dict) or resource.get("uri") != _RESOURCE:
        raise ValueError("Incorrect MCP resource")
    value = resource.get("text")
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_DIAGRAM_BYTES:
        raise ValueError("Browser diagram exceeds the read-only budget")
    # Fail closed if a tab/browser switch or navigation happened during the read.
    # This is a best-effort check, NOT an atomic pinned-tab security guarantee.
    if _status(process, output, 4, deadline, permitted_origin) != first_tab:
        raise ValueError("Browser tab changed during observation")
    return value


def observe_paired_browser(*, permitted_origin: str, timeout_seconds: float = 8.0) -> Observation:
    """Explicitly read the selected Browsentic tab; never issue action calls.

    Requires Browsentic Bridge and extension already installed and paired.
    This module does not provision or keep either running by itself.
    """
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 30:
        raise ValueError("Invalid MCP deadline")
    if not isinstance(permitted_origin, str) or allowed_origin(permitted_origin) != permitted_origin:
        raise ValueError("Specify one exact approved HTTPS origin")
    executable = shutil.which("browsentic")
    if not executable:
        raise RuntimeError("Browsentic is not installed or not on PATH")
    process = None
    try:
        process = subprocess.Popen([executable, "mcp"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   shell=False, bufsize=0)
        return compact_diagram(_read_diagram(process, float(timeout_seconds), permitted_origin))
    except (OSError, ValueError, TimeoutError, BrokenPipeError):
        # Do not include raw MCP errors: they might expose the active URL.
        raise RuntimeError("Read-only Browsentic observation failed; check browser pairing and the current tab") from None
    finally:
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=2)
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Experimental read-only Browsentic connection")
    parser.add_argument("--allow-current-tab", action="store_true",
                        help="Explicitly allow reading the selected browser tab, including a signed-in tab")
    parser.add_argument("--allow-origin", required=True,
                        help="Exact HTTPS origin of the tab you authorize, e.g. https://github.com")
    args = parser.parse_args(argv)
    if not args.allow_current_tab:
        parser.error("Specify --allow-current-tab only when it is safe to read the active tab")
    try:
        print(observe_paired_browser(permitted_origin=args.allow_origin).text)
    except (ValueError, RuntimeError) as exc:
        # Never echo the supplied URL or server data in error output.
        parser.exit(2, "Browser observation blocked: check origin, pairing and current tab\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
