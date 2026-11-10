"""Opt-in Windows acceptance of ForgeOS's existing Browsentic MCP connector.

This is a short-lived *development test*, not a production browser agent.
No content, URL, or browser title is written into the JSON evidence.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from typing import NamedTuple

from .browser_observation import allowed_origin

MAX_CAPTURE_BYTES = 4096
MAX_DURATION_SECONDS = 90


def _is_windows() -> bool:
    return os.name == "nt"


class TreeMemory(NamedTuple):
    working_set_bytes: int
    process_count: int
    unreadable_processes: int


def _windows_process_tree(root_pid: int) -> TreeMemory | None:
    """One-shot Windows Toolhelp+PSAPI working set; no WMI or psutil dependency.

    This captures the acceptance CLI process and its *current descendants*.
    An already running Browser/Bridge is NOT a descendant and is excluded.
    """
    if not _is_windows():
        return None

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    handle = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return None
    parents: dict[int, int] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if kernel.Process32FirstW(handle, ctypes.byref(entry)):
            while True:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                    break
    finally:
        kernel.CloseHandle(handle)
    if root_pid not in parents:
        return None

    # Traverse only known descendants; never count arbitrary sibling processes.
    selected = {root_pid}
    for _ in range(12):
        children = {pid for pid, parent in parents.items() if parent in selected}
        if children.issubset(selected):
            break
        selected.update(children)

    total = unreadable = 0
    for pid in selected:
        process = kernel.OpenProcess(0x1000 | 0x0010, False, pid)
        if not process:
            unreadable += 1
            continue
        try:
            memory = PROCESS_MEMORY_COUNTERS()
            memory.cb = ctypes.sizeof(memory)
            if psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
                total += int(memory.WorkingSetSize)
            else:
                unreadable += 1
        finally:
            kernel.CloseHandle(process)
    return TreeMemory(total, len(selected), unreadable)


def _stop_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop a timed-out client and its descendants; do not retry browser actions."""
    if process.poll() is not None:
        return
    if _is_windows():
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.kill()


def _make_report(status: str, code: str, *, elapsed_ms: int = 0,
                 output_chars: int = 0, peak: int | None = None,
                 samples: int = 0, unreadable: int = 0,
                 maximum_processes: int = 0,
                 external_peaks: dict[str, int | None] | None = None) -> dict[str, object]:
    return {
        "schema": "forgeos-browser-acceptance-v1",
        "status": status,
        "reason_code": code,
        "elapsed_ms": elapsed_ms,
        "observation_chars": output_chars,
        "peak_client_tree_working_set_bytes": peak,
        "memory_samples": samples,
        "memory_status": ("PARTIAL" if unreadable else "CAPTURED") if samples else "UNVERIFIED",
        "max_client_tree_processes": maximum_processes,
        "unreadable_processes_peak": unreadable,
        "external_pid_tree_peak_working_set_bytes": external_peaks or {},
        "memory_scope": "Client process tree plus optional explicitly selected external process trees; never summed; excludes GPU",
        "contains_page_content": False,
        "contains_url": False,
    }


def run_acceptance(*, origin: str, confirmed: bool, timeout_seconds: int = 25,
                   scroll: str | None = None, approve_scroll: bool = False,
                   changes_only: bool = False,
                   external_pids: tuple[int, ...] = ()) -> dict[str, object]:
    """Run one bounded acceptance command, returning privacy-safe evidence."""
    from .browsentic_readonly import _check_scroll

    if type(timeout_seconds) is not int or not 5 <= timeout_seconds <= MAX_DURATION_SECONDS:
        raise ValueError("Timeout must be between 5 and 90 seconds")
    _check_scroll(scroll, approve_scroll, changes_only=changes_only)
    if (type(external_pids) is not tuple or len(external_pids) > 2
            or any(type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF for pid in external_pids)
            or len(set(external_pids)) != len(external_pids)):
        raise ValueError("Specify up to two distinct process IDs")
    if type(confirmed) is not bool or not confirmed:
        return _make_report("BLOCKED", "PUBLIC_TAB_CONFIRMATION_REQUIRED")
    if not isinstance(origin, str) or allowed_origin(origin) != origin:
        raise ValueError("Specify one canonical public HTTPS origin")
    if not _is_windows():
        return _make_report("BLOCKED", "WINDOWS_REQUIRED")
    if shutil.which("browsentic") is None:
        return _make_report("BLOCKED", "BROWSENTIC_NOT_INSTALLED")

    command = [
        sys.executable, "-m", "forgeos.browsentic_readonly",
        "--allow-current-tab", "--allow-origin", origin,
    ]
    if scroll is not None:
        command.extend(("--scroll-direction", scroll, "--approve-scroll"))
    if changes_only:
        command.append("--changes-only")

    start = time.monotonic()
    peak: int | None = None
    external_peaks: dict[str, int | None] = {str(pid): None for pid in external_pids}
    samples = unreadable = maximum_processes = 0
    process = None
    stop = threading.Event()

    try:
        # This subprocess is the *only* new runtime dependency of the test.
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, shell=False,
        )

        def sample_memory() -> None:
            nonlocal peak, samples, unreadable, maximum_processes
            while not stop.is_set():
                if process.poll() is not None:
                    return
                try:
                    reading = _windows_process_tree(process.pid)
                except (OSError, AttributeError, ValueError):
                    reading = None
                if reading is not None:
                    peak = max(peak or 0, reading.working_set_bytes)
                    samples += 1
                    unreadable = max(unreadable, reading.unreadable_processes)
                    maximum_processes = max(maximum_processes, reading.process_count)
                for pid in external_pids:
                    try:
                        external = _windows_process_tree(pid)
                    except (OSError, AttributeError, ValueError):
                        external = None
                    if external is not None:
                        key = str(pid)
                        external_peaks[key] = max(external_peaks[key] or 0,
                                                   external.working_set_bytes)
                stop.wait(0.15)

        sampler = threading.Thread(target=sample_memory, daemon=True)
        sampler.start()
        try:
            stdout, _stderr = process.communicate(timeout=timeout_seconds)
            if len(stdout) > MAX_CAPTURE_BYTES:
                status, code, output_chars = "FAIL", "OUTPUT_OVERSIZE", 0
            elif process.returncode != 0:
                status, code, output_chars = (
                    ("BLOCKED", "CONNECTOR_REJECTED", 0)
                    if process.returncode == 2 else ("FAIL", "CONNECTOR_FAILED", 0)
                )
            else:
                try:
                    observation = stdout.decode("utf-8")
                except UnicodeError:
                    observation = ""
                expected = ("Browsentic visible changes (" if changes_only
                            else "Browsentic page diagram (")
                if observation.startswith(expected) and len(observation) <= 2401:
                    status, code, output_chars = "PASS", "OBSERVATION_VERIFIED", len(observation.rstrip("\n"))
                else:
                    status, code, output_chars = "FAIL", "INVALID_OBSERVATION", 0
        except subprocess.TimeoutExpired:
            _stop_tree(process)
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            status, code, output_chars = "FAIL", "TIMEOUT", 0
        finally:
            stop.set()
            sampler.join(timeout=3)
    except OSError:
        status, code, output_chars = "FAIL", "PROCESS_START_FAILED", 0
    elapsed = round((time.monotonic() - start) * 1000)
    return _make_report(status, code, elapsed_ms=elapsed,
                        output_chars=output_chars, peak=peak,
                        samples=samples, unreadable=unreadable,
                        maximum_processes=maximum_processes,
                        external_peaks=external_peaks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Opt-in Windows Browsentic acceptance (no page content saved)"
    )
    parser.add_argument("--allow-origin", required=True,
                        help="Exact public HTTPS origin currently selected in the browser")
    parser.add_argument("--confirm-public-test-tab", action="store_true",
                        help="Confirm the selected browser tab has no private or account data")
    parser.add_argument("--timeout-seconds", type=int, default=25)
    parser.add_argument("--scroll-direction", choices=("up", "down", "top", "bottom"))
    parser.add_argument("--approve-scroll", action="store_true")
    parser.add_argument("--changes-only", action="store_true")
    parser.add_argument("--external-pid", action="append", type=int, default=[],
                        help="Optional Bridge/browser root PID to measure (maximum two, no content read)")
    args = parser.parse_args(argv)
    try:
        report = run_acceptance(
            origin=args.allow_origin, confirmed=args.confirm_public_test_tab,
            timeout_seconds=args.timeout_seconds, scroll=args.scroll_direction,
            approve_scroll=args.approve_scroll, changes_only=args.changes_only,
            external_pids=tuple(args.external_pid),
        )
    except ValueError:
        report = _make_report("BLOCKED", "INVALID_TEST_CONFIGURATION")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
