"""Classic Windows AppContainer capability probing.

This module does not replace ForgeOS's current Windows containment path. It
probes whether the host can create and launch a zero-capability AppContainer
and verifies the resulting process token before reporting the feature usable.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

if os.name == "nt":
    from . import windows_containment as wc

    userenv = ctypes.WinDLL("userenv", use_last_error=True)

    wc.advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    wc.advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    wc.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    wc.kernel32.LocalFree.restype = ctypes.c_void_p

    PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
    TokenIsAppContainer = 29
    TokenCapabilities = 30
    ERROR_ALREADY_EXISTS = 183

    class SECURITY_CAPABILITIES(ctypes.Structure):
        _fields_ = [
            ("AppContainerSid", ctypes.c_void_p),
            ("Capabilities", ctypes.POINTER(wc.SID_AND_ATTRIBUTES)),
            ("CapabilityCount", wintypes.DWORD),
            ("Reserved", wintypes.DWORD),
        ]

    userenv.CreateAppContainerProfile.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ctypes.POINTER(wc.SID_AND_ATTRIBUTES), wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wintypes.LPCWSTR]
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long

    wc.advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    wc.advapi32.GetTokenInformation.restype = wintypes.BOOL
    wc.advapi32.FreeSid.argtypes = [ctypes.c_void_p]
    wc.advapi32.FreeSid.restype = ctypes.c_void_p


def _failed_hresult(value: int) -> bool:
    return bool(value & 0x80000000)


def _hresult_code(value: int) -> int:
    return value & 0xFFFFFFFF


def _token_dword(token, info_class: int) -> int:
    value = wintypes.DWORD()
    returned = wintypes.DWORD()
    if not wc.advapi32.GetTokenInformation(
            token, info_class, ctypes.byref(value), ctypes.sizeof(value),
            ctypes.byref(returned)):
        raise wc._win_error(f"GetTokenInformation({info_class})")
    return int(value.value)


def _sid_to_string(sid) -> str:
    value = wintypes.LPWSTR()
    if not wc.advapi32.ConvertSidToStringSidW(sid, ctypes.byref(value)):
        raise wc._win_error("ConvertSidToStringSidW")
    try:
        return value.value
    finally:
        wc.kernel32.LocalFree(ctypes.cast(value, ctypes.c_void_p))


def _icacls(path: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["icacls.exe", str(path), *arguments],
        capture_output=True, text=True, timeout=60, shell=False,
        errors="replace")
    if result.returncode != 0:
        detail = (result.stdout + "\n" + result.stderr)[-4000:]
        raise OSError(f"icacls failed for {path}: {detail}")


def _grant_probe_access(root: Path, allowed: Path, sid_string: str) -> None:
    # Root gets traverse/read attributes only, without inheritance. The writable
    # child directory gets an inheritable modify ACE and Low mandatory label.
    _icacls(root, "/grant", f"*{sid_string}:(RX)", "/Q")
    _icacls(
        allowed, "/grant", f"*{sid_string}:(OI)(CI)(M)", "/T", "/Q")
    wc.prepare_low_integrity_workspace(allowed)


def _token_capability_count(token) -> int:
    needed = wintypes.DWORD()
    wc.advapi32.GetTokenInformation(
        token, TokenCapabilities, None, 0, ctypes.byref(needed))
    if not needed.value:
        return 0
    buffer = ctypes.create_string_buffer(needed.value)
    if not wc.advapi32.GetTokenInformation(
            token, TokenCapabilities, buffer, needed.value,
            ctypes.byref(needed)):
        raise wc._win_error("GetTokenInformation(TokenCapabilities)")
    return int(ctypes.cast(
        buffer, ctypes.POINTER(wintypes.DWORD)).contents.value)


def _startup_with_security_capabilities(appcontainer_sid):
    size = ctypes.c_size_t()
    wc.kernel32.InitializeProcThreadAttributeList(
        None, 1, 0, ctypes.byref(size))
    if not size.value:
        raise wc._win_error("InitializeProcThreadAttributeList(size)")
    buffer = ctypes.create_string_buffer(size.value)
    attribute_list = ctypes.cast(buffer, ctypes.c_void_p)
    if not wc.kernel32.InitializeProcThreadAttributeList(
            attribute_list, 1, 0, ctypes.byref(size)):
        raise wc._win_error("InitializeProcThreadAttributeList")
    capabilities = SECURITY_CAPABILITIES()
    capabilities.AppContainerSid = appcontainer_sid
    capabilities.Capabilities = None
    capabilities.CapabilityCount = 0
    capabilities.Reserved = 0
    try:
        if not wc.kernel32.UpdateProcThreadAttribute(
                attribute_list, 0,
                PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(capabilities), ctypes.sizeof(capabilities),
                None, None):
            raise wc._win_error(
                "UpdateProcThreadAttribute(SECURITY_CAPABILITIES)")
    except BaseException:
        wc.kernel32.DeleteProcThreadAttributeList(attribute_list)
        raise
    startup = wc.STARTUPINFOEXW()
    startup.StartupInfo.cb = ctypes.sizeof(wc.STARTUPINFOEXW)
    startup.lpAttributeList = attribute_list
    return startup, buffer, capabilities



def _launch_appcontainer_process(appcontainer_sid, argv, *,
                                 cwd: Path | None = None,
                                 timeout: int = 15) -> dict:
    startup, attribute_buffer, security_capabilities = (
        _startup_with_security_capabilities(appcontainer_sid))
    info = wc.PROCESS_INFORMATION()
    command_line = ctypes.create_unicode_buffer(
        subprocess.list2cmdline(list(argv)))
    flags = (wc.CREATE_SUSPENDED | wc.CREATE_NO_WINDOW
             | wc.EXTENDED_STARTUPINFO_PRESENT)
    try:
        created = wc.kernel32.CreateProcessW(
            None, command_line, None, None, False, flags, None,
            str(cwd) if cwd is not None else None,
            ctypes.cast(ctypes.byref(startup),
                        ctypes.POINTER(wc.STARTUPINFOW)),
            ctypes.byref(info))
    finally:
        if startup.lpAttributeList:
            wc.kernel32.DeleteProcThreadAttributeList(
                startup.lpAttributeList)
            startup.lpAttributeList = None
    if not created:
        raise wc._win_error("CreateProcessW(AppContainer)")

    process, thread = info.hProcess, info.hThread
    token = wintypes.HANDLE()
    try:
        if not wc.advapi32.OpenProcessToken(
                process, wc.TOKEN_QUERY, ctypes.byref(token)):
            raise wc._win_error("OpenProcessToken(AppContainer child)")
        is_appcontainer = _token_dword(token, TokenIsAppContainer)
        capability_count = _token_capability_count(token)
        if is_appcontainer != 1:
            raise OSError("Child token is not an AppContainer")
        if capability_count != 0:
            raise OSError(
                f"Zero-capability child unexpectedly has {capability_count} capabilities")
        if wc.kernel32.ResumeThread(thread) == 0xFFFFFFFF:
            raise wc._win_error("ResumeThread(AppContainer)")
        wc.kernel32.CloseHandle(thread)
        thread = None
        status = wc.kernel32.WaitForSingleObject(process, timeout * 1000)
        if status == wc.WAIT_TIMEOUT:
            wc.kernel32.TerminateProcess(process, 124)
            wc.kernel32.WaitForSingleObject(process, 5000)
            raise subprocess.TimeoutExpired(list(argv), timeout)
        if status != wc.WAIT_OBJECT_0:
            raise wc._win_error("WaitForSingleObject(AppContainer)")
        exit_code = wintypes.DWORD()
        if not wc.kernel32.GetExitCodeProcess(
                process, ctypes.byref(exit_code)):
            raise wc._win_error("GetExitCodeProcess(AppContainer)")
        return {
            "returncode": int(exit_code.value),
            "token_is_appcontainer": True,
            "capability_count": 0,
        }
    finally:
        if token:
            wc.kernel32.CloseHandle(token)
        if thread:
            wc.kernel32.TerminateProcess(process, 125)
            wc.kernel32.CloseHandle(thread)
        wc.kernel32.CloseHandle(process)


def _create_probe_profile():
    profile_name = "ForgeOS.Probe." + uuid.uuid4().hex
    sid = ctypes.c_void_p()
    hr = int(userenv.CreateAppContainerProfile(
        profile_name, "ForgeOS Probe", "Temporary ForgeOS capability probe",
        None, 0, ctypes.byref(sid))) & 0xFFFFFFFF
    if _failed_hresult(hr):
        raise OSError(f"CreateAppContainerProfile failed: 0x{hr:08X}")
    return profile_name, sid


def probe_appcontainer(timeout: int = 15) -> dict:
    """Probe classic AppContainer launch and verify the resulting child token.

    No network or other capability SID is granted. This function never falls
    back to the ordinary restricted-token runner.
    """
    if os.name != "nt":
        return {
            "available": False,
            "reason": "AppContainer probing is Windows-only",
            "verified": False,
        }
    if (not isinstance(timeout, int) or isinstance(timeout, bool)
            or not 1 <= timeout <= 60):
        raise ValueError("timeout must be 1..60 seconds")

    profile_name = "ForgeOS.Probe." + uuid.uuid4().hex
    sid = ctypes.c_void_p()
    profile_created = False
    process = None
    thread = None
    token = wintypes.HANDLE()
    startup = None
    try:
        hr = int(userenv.CreateAppContainerProfile(
            profile_name, "ForgeOS Probe", "Temporary ForgeOS capability probe",
            None, 0, ctypes.byref(sid))) & 0xFFFFFFFF
        if _failed_hresult(hr):
            return {
                "available": False,
                "verified": False,
                "reason": f"CreateAppContainerProfile failed: 0x{hr:08X}",
            }
        profile_created = True

        startup, attribute_buffer, security_capabilities = (
            _startup_with_security_capabilities(sid))
        info = wc.PROCESS_INFORMATION()
        command = os.environ.get(
            "ComSpec",
            str(Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32" / "cmd.exe"))
        command_line = ctypes.create_unicode_buffer(
            subprocess.list2cmdline([command, "/d", "/c", "exit", "0"]))
        flags = (wc.CREATE_SUSPENDED | wc.CREATE_NO_WINDOW
                 | wc.EXTENDED_STARTUPINFO_PRESENT)
        try:
            created = wc.kernel32.CreateProcessW(
                None, command_line, None, None, False, flags, None, None,
                ctypes.cast(ctypes.byref(startup),
                            ctypes.POINTER(wc.STARTUPINFOW)),
                ctypes.byref(info))
        finally:
            if startup.lpAttributeList:
                wc.kernel32.DeleteProcThreadAttributeList(
                    startup.lpAttributeList)
                startup.lpAttributeList = None
        if not created:
            return {
                "available": False,
                "verified": False,
                "reason": str(wc._win_error("CreateProcessW(AppContainer)")),
            }

        process, thread = info.hProcess, info.hThread
        if not wc.advapi32.OpenProcessToken(
                process, wc.TOKEN_QUERY, ctypes.byref(token)):
            raise wc._win_error("OpenProcessToken(AppContainer child)")
        is_appcontainer = _token_dword(token, TokenIsAppContainer)
        capability_count = _token_capability_count(token)
        if is_appcontainer != 1:
            raise OSError("Child token is not an AppContainer")
        if capability_count != 0:
            raise OSError(
                f"Zero-capability probe unexpectedly has {capability_count} capabilities")

        if wc.kernel32.ResumeThread(thread) == 0xFFFFFFFF:
            raise wc._win_error("ResumeThread(AppContainer)")
        wc.kernel32.CloseHandle(thread)
        thread = None
        status = wc.kernel32.WaitForSingleObject(process, timeout * 1000)
        if status == wc.WAIT_TIMEOUT:
            wc.kernel32.TerminateProcess(process, 124)
            wc.kernel32.WaitForSingleObject(process, 5000)
            return {
                "available": False,
                "verified": False,
                "reason": "AppContainer probe process timed out",
            }
        if status != wc.WAIT_OBJECT_0:
            raise wc._win_error("WaitForSingleObject(AppContainer)")
        exit_code = wintypes.DWORD()
        if not wc.kernel32.GetExitCodeProcess(
                process, ctypes.byref(exit_code)):
            raise wc._win_error("GetExitCodeProcess(AppContainer)")
        if exit_code.value != 0:
            return {
                "available": False,
                "verified": False,
                "reason": f"AppContainer probe exited {exit_code.value}",
            }
        return {
            "available": True,
            "verified": True,
            "token_is_appcontainer": True,
            "capability_count": 0,
            "network_capabilities_granted": False,
            "profile": profile_name,
        }
    except OSError as exc:
        return {
            "available": False,
            "verified": False,
            "reason": str(exc),
        }
    finally:
        if token:
            wc.kernel32.CloseHandle(token)
        if thread:
            wc.kernel32.TerminateProcess(process, 125)
            wc.kernel32.CloseHandle(thread)
        if process:
            wc.kernel32.CloseHandle(process)
        if sid:
            wc.advapi32.FreeSid(sid)
        if profile_created:
            userenv.DeleteAppContainerProfile(profile_name)


def probe_appcontainer_filesystem(timeout: int = 15) -> dict:
    """Prove explicit AppContainer access while ungranted sibling access fails."""
    if os.name != "nt":
        return {
            "available": False,
            "verified": False,
            "reason": "AppContainer probing is Windows-only",
        }
    profile_name = None
    sid = ctypes.c_void_p()
    try:
        profile_name, sid = _create_probe_profile()
        sid_string = _sid_to_string(sid)
        command = os.environ.get(
            "ComSpec",
            str(Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32" / "cmd.exe"))
        with tempfile.TemporaryDirectory(prefix="forgeos-appcontainer-") as tmp:
            root = Path(tmp)
            allowed = root / "allowed"
            allowed.mkdir()
            outside = root / "outside-secret.txt"
            outside.write_text("must-not-be-readable", encoding="utf-8")
            _grant_probe_access(root, allowed, sid_string)

            created = allowed / "created-by-appcontainer.txt"
            write_command = (
                f'echo appcontainer-write>"{created}"')
            write_result = _launch_appcontainer_process(
                sid, [command, "/d", "/c", write_command],
                cwd=allowed, timeout=timeout)
            if write_result["returncode"] != 0 or not created.is_file():
                raise OSError(
                    f"AppContainer could not write explicitly granted workspace "
                    f"(exit {write_result['returncode']})")

            read_result = _launch_appcontainer_process(
                sid, [command, "/d", "/c",
                      f'type "{outside}" >nul'],
                cwd=allowed, timeout=timeout)
            if read_result["returncode"] == 0:
                raise OSError(
                    "AppContainer unexpectedly read an ungranted sibling file")
            return {
                "available": True,
                "verified": True,
                "workspace_write": True,
                "ungranted_sibling_read_blocked": True,
                "token_is_appcontainer": True,
                "capability_count": 0,
                "network_capabilities_granted": False,
            }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "verified": False,
            "reason": str(exc),
        }
    finally:
        if sid:
            wc.advapi32.FreeSid(sid)
        if profile_name:
            userenv.DeleteAppContainerProfile(profile_name)
