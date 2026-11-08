"""Classic Windows AppContainer capability probing.

This module does not replace ForgeOS's current Windows containment path. It
probes whether the host can create and launch a zero-capability AppContainer
and verifies the resulting process token before reporting the feature usable.
"""
from __future__ import annotations

import ctypes
import locale
from contextlib import contextmanager
from ctypes import wintypes
import os
import socket
import sys
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


def _grant_path_read_execute(path: Path, sid_string: str) -> None:
    if path.is_dir():
        _icacls(
            path, "/grant", f"*{sid_string}:(OI)(CI)(RX)", "/T", "/Q")
    else:
        _icacls(path, "/grant", f"*{sid_string}:(RX)", "/Q")


def _revoke_path_grant(path: Path, sid_string: str, *, recursive: bool = True) -> None:
    arguments = ["/remove:g", f"*{sid_string}"]
    if recursive and path.is_dir():
        arguments.append("/T")
    arguments.append("/Q")
    _icacls(path, *arguments)


def _python_runtime_acl_targets() -> list[Path]:
    executable = Path(sys.executable).resolve()
    base = Path(sys.base_prefix).resolve()
    version_tag = f"python{sys.version_info.major}{sys.version_info.minor}"
    candidates = [
        executable,
        base / "python3.dll",
        base / f"{version_tag}.dll",
        base / "DLLs",
        base / "Lib",
        base / f"{version_tag}.zip",
        base / "vcruntime140.dll",
        base / "vcruntime140_1.dll",
    ]
    result = []
    seen = set()
    for path in candidates:
        if path.exists():
            key = str(path).casefold()
            if key not in seen:
                seen.add(key)
                result.append(path)
    return result


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


def _delete_probe_profile(profile_name: str) -> None:
    """Delete a temporary profile and require the documented S_OK result.

    Windows does not expose a supported independent "profile exists" query;
    SID derivation is deterministic and therefore cannot verify deletion.
    """
    hr = int(userenv.DeleteAppContainerProfile(profile_name)) & 0xFFFFFFFF
    if _failed_hresult(hr):
        raise OSError(
            f"DeleteAppContainerProfile failed: 0x{hr:08X}")


def _create_probe_profile():
    profile_name = "ForgeOS.Probe." + uuid.uuid4().hex
    sid = ctypes.c_void_p()
    hr = int(userenv.CreateAppContainerProfile(
        profile_name, "ForgeOS Probe", "Temporary ForgeOS capability probe",
        None, 0, ctypes.byref(sid))) & 0xFFFFFFFF
    if _failed_hresult(hr):
        raise OSError(f"CreateAppContainerProfile failed: 0x{hr:08X}")
    return profile_name, sid


@contextmanager
def _temporary_probe_profile():
    profile_name, sid = _create_probe_profile()
    try:
        yield profile_name, sid
    finally:
        if sid:
            wc.advapi32.FreeSid(sid)
        _delete_probe_profile(profile_name)


def probe_appcontainer(timeout: int = 15) -> dict:
    """Probe classic AppContainer launch and verify the resulting child token.

    No network or other capability SID is granted. This function never falls
    back to the ordinary restricted-token runner. A successful result is only
    returned after the temporary profile has been deleted and verified absent.
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
    try:
        command = os.environ.get(
            "ComSpec",
            str(Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32" / "cmd.exe"))
        profile_name = None
        with _temporary_probe_profile() as (profile_name, sid):
            launched = _launch_appcontainer_process(
                sid, [command, "/d", "/c", "exit", "0"],
                timeout=timeout)
            if launched["returncode"] != 0:
                raise OSError(
                    f"AppContainer probe exited {launched['returncode']}")
        return {
            "available": True,
            "verified": True,
            "token_is_appcontainer": True,
            "capability_count": 0,
            "network_capabilities_granted": False,
            "profile": profile_name,
            "profile_deleted": True,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "verified": False,
            "reason": str(exc),
        }


def probe_appcontainer_filesystem(timeout: int = 15) -> dict:
    """Prove explicit AppContainer access while ungranted sibling access fails."""
    if os.name != "nt":
        return {
            "available": False,
            "verified": False,
            "reason": "AppContainer probing is Windows-only",
        }
    if (not isinstance(timeout, int) or isinstance(timeout, bool)
            or not 1 <= timeout <= 60):
        raise ValueError("timeout must be 1..60 seconds")
    try:
        with _temporary_probe_profile() as (profile_name, sid):
            sid_string = _sid_to_string(sid)
            command = os.environ.get(
                "ComSpec",
                str(Path(os.environ.get("SystemRoot", r"C:\Windows"))
                    / "System32" / "cmd.exe"))
            with tempfile.TemporaryDirectory(
                    prefix="forgeos-appcontainer-") as tmp:
                root = Path(tmp)
                allowed = root / "allowed"
                allowed.mkdir()
                outside = root / "outside-secret.txt"
                outside.write_text(
                    "must-not-be-readable", encoding="utf-8")
                _grant_probe_access(root, allowed, sid_string)

                created = allowed / "created-by-appcontainer.txt"
                write_result = _launch_appcontainer_process(
                    sid, [command, "/d", "/c",
                          "echo appcontainer-write>created-by-appcontainer.txt"],
                    cwd=allowed, timeout=timeout)
                if (write_result["returncode"] != 0
                        or not created.is_file()):
                    raise OSError(
                        "AppContainer could not write explicitly granted "
                        f"workspace (exit {write_result['returncode']})")

                read_result = _launch_appcontainer_process(
                    sid, [command, "/d", "/c",
                          'type "..\\outside-secret.txt" >nul'],
                    cwd=allowed, timeout=timeout)
                if read_result["returncode"] == 0:
                    raise OSError(
                        "AppContainer unexpectedly read an ungranted "
                        "sibling file")
        return {
            "available": True,
            "verified": True,
            "workspace_write": True,
            "ungranted_sibling_read_blocked": True,
            "token_is_appcontainer": True,
            "capability_count": 0,
            "network_capabilities_granted": False,
            "profile_deleted": True,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "verified": False,
            "reason": str(exc),
        }


def probe_appcontainer_network(timeout: int = 15) -> dict:
    """Prove a zero-capability AppContainer cannot reach a loopback listener."""
    if os.name != "nt":
        return {
            "available": False,
            "verified": False,
            "reason": "AppContainer probing is Windows-only",
        }
    if (not isinstance(timeout, int) or isinstance(timeout, bool)
            or not 3 <= timeout <= 60):
        raise ValueError("timeout must be 3..60 seconds")
    curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    if not curl.is_file():
        return {
            "available": False,
            "verified": False,
            "reason": "Windows curl.exe is unavailable",
        }
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.5)
    port = listener.getsockname()[1]
    try:
        with _temporary_probe_profile() as (_profile_name, sid):
            client_check = _launch_appcontainer_process(
                sid, [str(curl), "--version"], timeout=timeout)
            if client_check["returncode"] != 0:
                raise OSError(
                    "curl.exe could not execute inside zero-capability "
                    f"AppContainer (exit {client_check['returncode']})")

            request = _launch_appcontainer_process(
                sid, [str(curl), "--silent", "--show-error",
                      "--max-time", "2", "--output", "NUL",
                      f"http://127.0.0.1:{port}/"],
                timeout=timeout)
            connected = False
            try:
                connection, _address = listener.accept()
            except socket.timeout:
                pass
            else:
                connected = True
                connection.close()
            if connected:
                raise OSError(
                    "Zero-capability AppContainer reached localhost listener")
            if request["returncode"] == 0:
                raise OSError(
                    "curl unexpectedly reported network success from "
                    "zero-capability AppContainer")
        return {
            "available": True,
            "verified": True,
            "client_executable_verified": True,
            "loopback_connection_blocked": True,
            "token_is_appcontainer": True,
            "capability_count": 0,
            "network_capabilities_granted": False,
            "profile_deleted": True,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "verified": False,
            "reason": str(exc),
        }
    finally:
        listener.close()


def probe_appcontainer_python_runtime(timeout: int = 20) -> dict:
    """Prove Python works only after narrow read/execute runtime grants.

    The probe records whether the host Python tree was already visible to the
    AppContainer, grants this one temporary AppContainer SID read/execute on the
    minimum runtime roots needed by CPython, then removes those ACEs and checks
    that visibility returns to its pre-probe state.
    """
    if os.name != "nt":
        return {
            "available": False,
            "verified": False,
            "reason": "AppContainer Python probing is Windows-only",
        }
    if (not isinstance(timeout, int) or isinstance(timeout, bool)
            or not 3 <= timeout <= 60):
        raise ValueError("timeout must be 3..60 seconds")
    executable = Path(sys.executable).resolve()
    if not executable.is_file():
        return {
            "available": False,
            "verified": False,
            "reason": f"Python executable missing: {executable}",
        }
    try:
        with _temporary_probe_profile() as (_profile_name, sid):
            sid_string = _sid_to_string(sid)
            with tempfile.TemporaryDirectory(
                    prefix="forgeos-appcontainer-python-") as tmp:
                root = Path(tmp)
                allowed = root / "allowed"
                allowed.mkdir()
                _grant_probe_access(root, allowed, sid_string)

                command = [str(executable), "-I", "-S", "-c", "pass"]
                before = _launch_appcontainer_process(
                    sid, command, cwd=allowed, timeout=timeout)
                pregrant_visible = before["returncode"] == 0

                targets = _python_runtime_acl_targets()
                if not targets:
                    raise OSError("No Python runtime ACL targets were found")
                granted = []
                try:
                    for target in targets:
                        _grant_path_read_execute(target, sid_string)
                        granted.append(target)

                    startup = _launch_appcontainer_process(
                        sid, command, cwd=allowed, timeout=timeout)
                    if startup["returncode"] != 0:
                        raise OSError(
                            "Python could not initialize after runtime RX grants "
                            f"(exit {startup['returncode']})")

                    created = allowed / "python-created.txt"
                    writer = _launch_appcontainer_process(
                        sid, [str(executable), "-I", "-S", "-c",
                              "open('python-created.txt','w').write('ok')"],
                        cwd=allowed, timeout=timeout)
                    if writer["returncode"] != 0 or not created.is_file():
                        raise OSError(
                            "Python could not write explicitly granted "
                            f"AppContainer workspace (exit {writer['returncode']})")
                    if created.read_text(encoding="utf-8") != "ok":
                        raise OSError(
                            "Python AppContainer write verification failed")
                finally:
                    cleanup_errors = []
                    for target in reversed(granted):
                        try:
                            _revoke_path_grant(target, sid_string)
                        except OSError as exc:
                            cleanup_errors.append(str(exc))
                    if cleanup_errors:
                        raise OSError(
                            "Failed to revoke temporary Python runtime ACEs: "
                            + "; ".join(cleanup_errors))

                after = _launch_appcontainer_process(
                    sid, command, cwd=allowed, timeout=timeout)
                postgrant_visible = after["returncode"] == 0
                if postgrant_visible != pregrant_visible:
                    raise OSError(
                        "Python runtime access did not return to its pre-probe "
                        "state after ACL revocation")
        return {
            "available": True,
            "verified": True,
            "python_executable": str(executable),
            "python_startup": True,
            "workspace_write": True,
            "host_runtime_preexposed": pregrant_visible,
            "runtime_acl_targets": [str(path) for path in targets],
            "temporary_runtime_grants_revoked": True,
            "token_is_appcontainer": True,
            "capability_count": 0,
            "network_capabilities_granted": False,
            "profile_deleted": True,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "verified": False,
            "python_executable": str(executable),
            "reason": str(exc),
        }


def _startup_with_appcontainer_and_handles(appcontainer_sid, in_handle, out_handle):
    """Build STARTUPINFOEX with zero-capability AppContainer + handle allowlist."""
    size = ctypes.c_size_t()
    wc.kernel32.InitializeProcThreadAttributeList(
        None, 2, 0, ctypes.byref(size))
    if not size.value:
        raise wc._win_error("InitializeProcThreadAttributeList(size)")
    buffer = ctypes.create_string_buffer(size.value)
    attribute_list = ctypes.cast(buffer, ctypes.c_void_p)
    if not wc.kernel32.InitializeProcThreadAttributeList(
            attribute_list, 2, 0, ctypes.byref(size)):
        raise wc._win_error("InitializeProcThreadAttributeList")

    capabilities = SECURITY_CAPABILITIES()
    capabilities.AppContainerSid = appcontainer_sid
    capabilities.Capabilities = None
    capabilities.CapabilityCount = 0
    capabilities.Reserved = 0
    handles = (wintypes.HANDLE * 2)(
        wintypes.HANDLE(in_handle), wintypes.HANDLE(out_handle))
    try:
        if not wc.kernel32.UpdateProcThreadAttribute(
                attribute_list, 0,
                PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(capabilities), ctypes.sizeof(capabilities),
                None, None):
            raise wc._win_error(
                "UpdateProcThreadAttribute(SECURITY_CAPABILITIES)")
        if not wc.kernel32.UpdateProcThreadAttribute(
                attribute_list, 0, wc.PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.cast(handles, ctypes.c_void_p), ctypes.sizeof(handles),
                None, None):
            raise wc._win_error("UpdateProcThreadAttribute(HANDLE_LIST)")
    except BaseException:
        wc.kernel32.DeleteProcThreadAttributeList(attribute_list)
        raise

    startup = wc.STARTUPINFOEXW()
    startup.StartupInfo.cb = ctypes.sizeof(wc.STARTUPINFOEXW)
    startup.StartupInfo.dwFlags = wc.STARTF_USESTDHANDLES
    startup.StartupInfo.hStdInput = wintypes.HANDLE(in_handle)
    startup.StartupInfo.hStdOutput = wintypes.HANDLE(out_handle)
    startup.StartupInfo.hStdError = wintypes.HANDLE(out_handle)
    startup.lpAttributeList = attribute_list
    return startup, buffer, capabilities, handles


def _run_appcontainer_process(appcontainer_sid, argv, *, cwd: Path,
                              env: dict[str, str], timeout: int = 180,
                              active_process_limit: int = 64,
                              process_memory_bytes: int = 1024 * 1024 * 1024,
                              job_memory_bytes: int = 2 * 1024 * 1024 * 1024,
                              watched_outputs: tuple[Path, ...] = ()) -> dict:
    """Run a process tree in a zero-capability AppContainer and Job Object."""
    if os.name != "nt":
        raise OSError("Secure AppContainer execution is Windows-only")
    if (not isinstance(timeout, int) or isinstance(timeout, bool)
            or not 1 <= timeout <= 3600):
        raise ValueError("timeout must be 1..3600 seconds")
    if (not isinstance(active_process_limit, int)
            or isinstance(active_process_limit, bool)
            or not 1 <= active_process_limit <= 256):
        raise ValueError("active_process_limit must be 1..256")
    if process_memory_bytes < 64 * 1024 * 1024:
        raise ValueError("process_memory_bytes is too small")
    if job_memory_bytes < process_memory_bytes:
        raise ValueError("job_memory_bytes must be >= process_memory_bytes")
    root = Path(cwd).resolve(strict=True)
    if not root.is_dir() or Path(cwd).is_symlink():
        raise ValueError("AppContainer cwd must be a real directory")
    if not isinstance(argv, (list, tuple)) or not argv or any(
            not isinstance(item, str) or not item for item in argv):
        raise ValueError("argv must contain nonempty strings")
    environment = wc._environment_block(env)
    environment_pointer = ctypes.cast(environment, ctypes.c_void_p)
    command_line = ctypes.create_unicode_buffer(
        subprocess.list2cmdline(list(argv)))

    job = wc.kernel32.CreateJobObjectW(None, None)
    if not job:
        raise wc._win_error("CreateJobObjectW(AppContainer)")
    process = None
    thread = None
    assigned = False
    output = tempfile.TemporaryFile(mode="w+b")
    devnull = open(os.devnull, "rb")
    out_handle = wc.msvcrt.get_osfhandle(output.fileno())
    in_handle = wc.msvcrt.get_osfhandle(devnull.fileno())
    os.set_handle_inheritable(out_handle, True)
    os.set_handle_inheritable(in_handle, True)
    token = wintypes.HANDLE()

    try:
        limits = wc.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            wc.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | wc.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | wc.JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | wc.JOB_OBJECT_LIMIT_JOB_MEMORY
        )
        limits.BasicLimitInformation.ActiveProcessLimit = active_process_limit
        limits.ProcessMemoryLimit = process_memory_bytes
        limits.JobMemoryLimit = job_memory_bytes
        if not wc.kernel32.SetInformationJobObject(
                job, wc.JobObjectExtendedLimitInformation,
                ctypes.byref(limits), ctypes.sizeof(limits)):
            raise wc._win_error("SetInformationJobObject(AppContainer)")
        ui_restrictions = wc._apply_ui_restrictions(job, True)

        startup, attribute_buffer, security_caps, allowed_handles = (
            _startup_with_appcontainer_and_handles(
                appcontainer_sid, in_handle, out_handle))
        info = wc.PROCESS_INFORMATION()
        flags = (wc.CREATE_SUSPENDED | wc.CREATE_NO_WINDOW
                 | wc.EXTENDED_STARTUPINFO_PRESENT
                 | wc.CREATE_UNICODE_ENVIRONMENT)
        try:
            created = wc.kernel32.CreateProcessW(
                None, command_line, None, None, True, flags,
                environment_pointer, str(root),
                ctypes.cast(ctypes.byref(startup),
                            ctypes.POINTER(wc.STARTUPINFOW)),
                ctypes.byref(info))
        finally:
            if startup.lpAttributeList:
                wc.kernel32.DeleteProcThreadAttributeList(
                    startup.lpAttributeList)
                startup.lpAttributeList = None
        if not created:
            raise wc._win_error("CreateProcessW(Secure AppContainer)")
        process, thread = info.hProcess, info.hThread
        os.set_handle_inheritable(out_handle, False)
        os.set_handle_inheritable(in_handle, False)

        if not wc.advapi32.OpenProcessToken(
                process, wc.TOKEN_QUERY, ctypes.byref(token)):
            raise wc._win_error("OpenProcessToken(Secure AppContainer)")
        if _token_dword(token, TokenIsAppContainer) != 1:
            raise OSError("Secure child token is not an AppContainer")
        capability_count = _token_capability_count(token)
        if capability_count != 0:
            raise OSError(
                f"Secure child unexpectedly has {capability_count} capabilities")

        if not wc.kernel32.AssignProcessToJobObject(job, process):
            raise wc._win_error("AssignProcessToJobObject(Secure AppContainer)")
        assigned = True
        if wc.kernel32.ResumeThread(thread) == 0xFFFFFFFF:
            raise wc._win_error("ResumeThread(Secure AppContainer)")
        wc.kernel32.CloseHandle(thread)
        thread = None

        from .output_capture import wait_native_output_bounded
        status = wait_native_output_bounded(
            process, job, output, timeout, kernel32=wc.kernel32,
            wait_timeout=wc.WAIT_TIMEOUT,
            wait_object_0=wc.WAIT_OBJECT_0, watched_files=watched_outputs)
        if status == wc.WAIT_TIMEOUT:
            if not wc.kernel32.TerminateJobObject(job, 124):
                raise wc._win_error("TerminateJobObject(Secure AppContainer)")
            wc.kernel32.WaitForSingleObject(process, 5000)
            raise subprocess.TimeoutExpired(list(argv), timeout)
        if status == wc.WAIT_FAILED:
            raise wc._win_error("WaitForSingleObject(Secure AppContainer)")
        if status != wc.WAIT_OBJECT_0:
            raise OSError(
                f"Unexpected secure AppContainer wait status: {status}")

        code = wintypes.DWORD()
        if not wc.kernel32.GetExitCodeProcess(
                process, ctypes.byref(code)):
            raise wc._win_error("GetExitCodeProcess(Secure AppContainer)")
        output.flush()
        output.seek(0)
        encoding = locale.getpreferredencoding(False) or "utf-8"
        from .output_capture import summarize_output_file
        capture = summarize_output_file(output, encoding=encoding)
        return {
            "passed": code.value == 0,
            "returncode": int(code.value),
            **capture,
            "containment": "windows-zero-capability-appcontainer+job-object",
            "token_is_appcontainer": True,
            "capability_count": 0,
            "network_capabilities_granted": False,
            "handle_allowlist": True,
            "ui_restrictions": ui_restrictions,
        }
    finally:
        try:
            os.set_handle_inheritable(out_handle, False)
            os.set_handle_inheritable(in_handle, False)
        except OSError:
            pass
        if process and not assigned:
            wc.kernel32.TerminateProcess(process, 125)
            wc.kernel32.WaitForSingleObject(process, 5000)
        if token:
            wc.kernel32.CloseHandle(token)
        if thread:
            wc.kernel32.CloseHandle(thread)
        if process:
            wc.kernel32.CloseHandle(process)
        if job:
            wc.kernel32.CloseHandle(job)
        devnull.close()
        output.close()


def run_secure_appcontainer(argv, cwd: str | Path, env: dict[str, str], *,
                            executable: str | Path,
                            timeout: int = 180,
                            watched_output: Path | None = None) -> dict:
    """Run autonomous code with zero capabilities and no weaker fallback.

    Only the disposable execution snapshot and the ForgeOS-owned executable are
    explicitly granted to the temporary AppContainer SID. Grants are revoked
    before the temporary profile is deleted.
    """
    if os.name != "nt":
        raise OSError("Secure AppContainer execution is Windows-only")
    root = Path(cwd).expanduser()
    if root.is_symlink():
        raise ValueError("Secure execution snapshot must not be a symlink")
    root = root.resolve(strict=True)
    runner = Path(executable).expanduser()
    if runner.is_symlink():
        raise ValueError("Secure runner executable must not be a symlink")
    runner = runner.resolve(strict=True)
    if not root.is_dir() or not runner.is_file():
        raise ValueError("Secure execution paths are invalid")
    if not isinstance(env, dict):
        raise ValueError("Secure execution environment must be a dictionary")
    watched = ()
    if watched_output is not None:
        from .output_capture import validate_secure_output_path
        watched = (validate_secure_output_path(root, watched_output),)

    result = None
    with _temporary_probe_profile() as (_profile_name, sid):
        sid_string = _sid_to_string(sid)
        grants: list[tuple[Path, bool]] = []
        seen = set()

        def grant(path: Path, permission: str, *, recursive: bool = False):
            key = (str(path).casefold(), permission, recursive)
            if key in seen:
                return
            seen.add(key)
            if recursive:
                _icacls(
                    path, "/grant",
                    f"*{sid_string}:(OI)(CI)({permission})",
                    "/T", "/Q")
            else:
                _icacls(
                    path, "/grant", f"*{sid_string}:({permission})", "/Q")
            grants.append((path, recursive))

        try:
            grant(root.parent, "RX")
            grant(root, "M", recursive=True)
            if runner.parent != root.parent:
                grant(runner.parent, "RX")
            grant(runner, "RX")
            result = _run_appcontainer_process(
                sid, argv, cwd=root, env=env, timeout=timeout,
                watched_outputs=watched)
        finally:
            cleanup_errors = []
            for path, recursive in reversed(grants):
                try:
                    _revoke_path_grant(
                        path, sid_string, recursive=recursive)
                except OSError as exc:
                    cleanup_errors.append(str(exc))
            if cleanup_errors:
                raise OSError(
                    "Failed to revoke secure AppContainer grants: "
                    + "; ".join(cleanup_errors))
    if result is None:
        raise OSError("Secure AppContainer did not produce a result")
    result["profile_deleted"] = True
    return result
