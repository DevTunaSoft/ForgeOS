"""Windows-native process-tree containment for test execution.

This module limits and cleans up a Windows process tree with Job Objects. It is
not a security sandbox: it does not remove user privileges, isolate the
filesystem, or block network access.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import locale
import os
from pathlib import Path
import subprocess
import tempfile

if os.name == "nt":
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    CREATE_SUSPENDED = 0x00000004
    CREATE_NO_WINDOW = 0x08000000
    STARTF_USESTDHANDLES = 0x00000100
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF
    INFINITE = 0xFFFFFFFF

    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9

    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_DUPLICATE = 0x0002
    TOKEN_QUERY = 0x0008
    DISABLE_MAX_PRIVILEGE = 0x00000001
    LUA_TOKEN = 0x00000004

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.CreateRestrictedToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.POINTER(wintypes.HANDLE)]
    advapi32.CreateRestrictedToken.restype = wintypes.BOOL
    advapi32.IsTokenRestricted.argtypes = [wintypes.HANDLE]
    advapi32.IsTokenRestricted.restype = wintypes.BOOL
    advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
        ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    advapi32.CreateProcessAsUserW.restype = wintypes.BOOL


def _win_error(operation: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{operation} failed: {ctypes.FormatError(code).strip()}")



def _create_restricted_primary_token():
    """Return a privilege-reduced version of the current process token."""
    if os.name != "nt":
        raise OSError("Restricted tokens are only available on Windows")
    current = wintypes.HANDLE()
    restricted = wintypes.HANDLE()
    desired = TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY
    if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), desired, ctypes.byref(current)):
        raise _win_error("OpenProcessToken")
    try:
        flags = DISABLE_MAX_PRIVILEGE
        if not advapi32.CreateRestrictedToken(
                current, flags, 0, None, 0, None, 0, None,
                ctypes.byref(restricted)):
            raise _win_error("CreateRestrictedToken")
        return restricted
    finally:
        kernel32.CloseHandle(current)


def run_contained(argv, cwd: str | Path, *, timeout: int,
                  active_process_limit: int = 64,
                  process_memory_bytes: int = 1024 * 1024 * 1024,
                  job_memory_bytes: int = 2 * 1024 * 1024 * 1024,
                  restrict_privileges: bool = True):
    """Run one command in a Windows Job Object and capture combined output."""
    if os.name != "nt":
        raise OSError("Windows Job Object containment is only available on Windows")
    if (not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0):
        raise ValueError("timeout must be a positive integer")
    if (not isinstance(active_process_limit, int) or isinstance(active_process_limit, bool)
            or not 1 <= active_process_limit <= 256):
        raise ValueError("active_process_limit must be 1..256")
    if process_memory_bytes < 64 * 1024 * 1024:
        raise ValueError("process_memory_bytes is too small")
    if job_memory_bytes < process_memory_bytes:
        raise ValueError("job_memory_bytes must be >= process_memory_bytes")
    root = Path(cwd).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("cwd must be a directory")
    if not isinstance(argv, (list, tuple)) or not argv or any(
            not isinstance(item, str) or not item for item in argv):
        raise ValueError("argv must contain nonempty strings")

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _win_error("CreateJobObjectW")

    process = None
    thread = None
    assigned = False
    restricted_token = None
    output = tempfile.TemporaryFile(mode="w+b")
    devnull = open(os.devnull, "rb")
    out_handle = msvcrt.get_osfhandle(output.fileno())
    in_handle = msvcrt.get_osfhandle(devnull.fileno())
    os.set_handle_inheritable(out_handle, True)
    os.set_handle_inheritable(in_handle, True)

    try:
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_JOB_MEMORY
        )
        limits.BasicLimitInformation.ActiveProcessLimit = active_process_limit
        limits.ProcessMemoryLimit = process_memory_bytes
        limits.JobMemoryLimit = job_memory_bytes
        if not kernel32.SetInformationJobObject(
                job, JobObjectExtendedLimitInformation,
                ctypes.byref(limits), ctypes.sizeof(limits)):
            raise _win_error("SetInformationJobObject")

        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        startup.dwFlags = STARTF_USESTDHANDLES
        startup.hStdInput = wintypes.HANDLE(in_handle)
        startup.hStdOutput = wintypes.HANDLE(out_handle)
        startup.hStdError = wintypes.HANDLE(out_handle)
        info = PROCESS_INFORMATION()
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(argv)))
        flags = CREATE_SUSPENDED | CREATE_NO_WINDOW
        if not isinstance(restrict_privileges, bool):
            raise ValueError("restrict_privileges must be boolean")
        if restrict_privileges:
            restricted_token = _create_restricted_primary_token()
            created = advapi32.CreateProcessAsUserW(
                restricted_token, None, command_line, None, None, True,
                flags, None, str(root), ctypes.byref(startup), ctypes.byref(info))
            operation = "CreateProcessAsUserW"
        else:
            created = kernel32.CreateProcessW(
                None, command_line, None, None, True, flags, None,
                str(root), ctypes.byref(startup), ctypes.byref(info))
            operation = "CreateProcessW"
        if not created:
            raise _win_error(operation)
        process, thread = info.hProcess, info.hThread
        os.set_handle_inheritable(out_handle, False)
        os.set_handle_inheritable(in_handle, False)

        if not kernel32.AssignProcessToJobObject(job, process):
            raise _win_error("AssignProcessToJobObject")
        assigned = True
        previous_suspend_count = kernel32.ResumeThread(thread)
        if previous_suspend_count == 0xFFFFFFFF:
            raise _win_error("ResumeThread")
        kernel32.CloseHandle(thread)
        thread = None

        wait_ms = min(timeout * 1000, 0xFFFFFFFE)
        status = kernel32.WaitForSingleObject(process, wait_ms)
        if status == WAIT_TIMEOUT:
            if not kernel32.TerminateJobObject(job, 124):
                raise _win_error("TerminateJobObject")
            kernel32.WaitForSingleObject(process, 5000)
            raise subprocess.TimeoutExpired(list(argv), timeout)
        if status == WAIT_FAILED:
            raise _win_error("WaitForSingleObject")
        if status != WAIT_OBJECT_0:
            raise OSError(f"Unexpected Windows wait status: {status}")

        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process, ctypes.byref(code)):
            raise _win_error("GetExitCodeProcess")
        output.flush()
        output.seek(0)
        encoding = locale.getpreferredencoding(False) or "utf-8"
        text = output.read().decode(encoding, errors="replace")
        return {
            "passed": code.value == 0,
            "returncode": int(code.value),
            "output": text[-16000:],
            "containment": ("windows-restricted-token+job-object"
                            if restrict_privileges else "windows-job-object"),
        }
    finally:
        try:
            os.set_handle_inheritable(out_handle, False)
            os.set_handle_inheritable(in_handle, False)
        except OSError:
            pass
        if process and not assigned:
            kernel32.TerminateProcess(process, 125)
            kernel32.WaitForSingleObject(process, 5000)
        if thread:
            kernel32.CloseHandle(thread)
        if process:
            kernel32.CloseHandle(process)
        if restricted_token:
            kernel32.CloseHandle(restricted_token)
        if job:
            kernel32.CloseHandle(job)
        devnull.close()
        output.close()
