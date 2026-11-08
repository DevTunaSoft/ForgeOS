"""Opt-in, single-process, CPU-first llama.cpp server lifecycle.

One managed llama-server belongs to one ForgeOS GUI session. Executables are
provided explicitly and pinned to a SHA-256 supplied by the operator; no code
downloads, remote inference, Docker, shell or second desktop application.

A checksum is integrity evidence, NOT a software publisher signature. Verify
the upstream binary and adjacent DLL provenance before electing to run it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .local_models import LocalChatModel, _open
from .model_routing import local_memory_settings, safe_model_file_budget, system_memory

CHUNK_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 4096
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
ALIAS = "forgeos-managed"
MAX_TIMEOUT = 180
DEFAULT_TIMEOUT = 90


def _hex_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_file(path: str | Path, *, suffix: str) -> Path:
    target = Path(path).expanduser().absolute()
    if not target.is_file() or target.suffix.lower() != suffix:
        raise ValueError("Select an existing " + suffix + " file")
    # Refuse symlink chains to avoid unintentional path substitution between
    # selection, inspection and model loading. Junction/reparse hardening and
    # concurrent mutation remain separate Windows acceptance requirements.
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError("Managed local inference does not follow symbolic links")
    if os.name != "nt" and suffix != ".gguf" and not os.access(target, os.X_OK):
        raise ValueError("llama-server must be executable")
    return target


def _verified_model(path: str | Path) -> tuple[Path, dict]:
    target = _existing_file(path, suffix=".gguf")
    manifest = target.with_name(target.name + ".forgeos.json")
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Only SHA-verified ForgeOS GGUF downloads can be loaded")
    if manifest.stat().st_nlink != 1:
        raise ValueError("Hard-linked GGUF verification manifests are not trusted")
    if target.stat().st_nlink != 1:
        raise ValueError("Hard-linked GGUF model files are not trusted")
    if manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("GGUF verification manifest is too large")
    try:
        info = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("Invalid GGUF verification manifest") from exc
    if not isinstance(info, dict):
        raise ValueError("Invalid GGUF verification manifest format")
    expected = info.get("sha256")
    size = info.get("bytes")
    if (not isinstance(expected, str) or not SHA256.fullmatch(expected)
            or type(size) is not int or size < 16
            or size > 16 * 1024**3 or target.stat().st_size != size):
        raise ValueError("GGUF identity or file size disagrees with its manifest")
    # The metadata alone is not trusted. Rehash immediately before inference.
    before = target.stat()
    if _hex_digest(target) != expected:
        raise ValueError("GGUF SHA-256 mismatch; refusing modified model")
    after = target.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError("GGUF changed during verification")
    if after.st_nlink != 1:
        raise ValueError("Hard-linked GGUF model files are not trusted")
    with target.open("rb") as source:
        if source.read(4) != b"GGUF":
            raise ValueError("Verified file does not have a GGUF header")
    memory_limit = safe_model_file_budget(system_memory())
    if size > memory_limit:
        raise RuntimeError(
            "This GGUF exceeds the conservative free RAM budget; "
            "stop competing apps, or choose a smaller model")
    return target, dict(info)


def _verified_runtime(binary: str | Path, expected_sha256: str) -> Path:
    if not isinstance(expected_sha256, str) or not SHA256.fullmatch(expected_sha256):
        raise ValueError("Enter the trusted, lowercase 64-character llama-server SHA-256")
    suffix = ".exe" if os.name == "nt" else ""
    path = _existing_file(binary, suffix=suffix)
    before = path.stat()
    if before.st_nlink != 1:
        raise ValueError("Hard-linked llama-server binaries are not trusted")
    if before.st_size > 512 * 1024**2:
        raise ValueError("llama-server binary exceeds the expected size cap")
    if _hex_digest(path) != expected_sha256:
        raise ValueError("llama-server SHA-256 did not match the trusted checksum")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError("llama-server changed during verification")
    if after.st_nlink != 1:
        raise ValueError("Hard-linked llama-server binaries are not trusted")
    return path


# Minimal operating-system variables only. In particular, do not inherit
# GGML_*, CUDA_*, OMP_*, PYTHON*, DLL injection, provider credentials, proxy
# settings or arbitrary model-server feature toggles from the user's shell.
# A negative prefix filter is insufficient because inference engines and
# native libraries may introduce new environment switches in later versions.
_CHILD_ENV_KEYS = frozenset((
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
    "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
))


def _child_environment() -> dict[str, str]:
    """Provide the smallest predictable OS environment to the model server.

    The runtime executable is invoked by absolute path with its own cwd.
    No application/provider secrets or inherited llama/GGML/GPU overrides
    should be forwarded to the untrusted local inference subprocess.
    """
    return {key: value for key, value in os.environ.items()
            if key.upper() in _CHILD_ENV_KEYS}


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listening:
        listening.bind(("127.0.0.1", 0))
        return int(listening.getsockname()[1])


class _WindowsNativeProcess:
    """Minimal Popen-compatible wrapper for a suspended native Windows child."""

    def __init__(self, handle, pid: int):
        self._handle = handle
        self.pid = int(pid)
        self.returncode = None

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        from ctypes import wintypes
        from . import windows_containment as win
        status = win.kernel32.WaitForSingleObject(self._handle, 0)
        if status == win.WAIT_TIMEOUT:
            return None
        if status != win.WAIT_OBJECT_0:
            raise OSError("Could not poll managed llama-server")
        code = wintypes.DWORD()
        if not win.kernel32.GetExitCodeProcess(self._handle, __import__("ctypes").byref(code)):
            raise OSError("Could not read managed llama-server exit code")
        self.returncode = int(code.value)
        return self.returncode

    def wait(self, timeout=None):
        from ctypes import wintypes
        from . import windows_containment as win
        milliseconds = win.INFINITE if timeout is None else max(0, int(timeout * 1000))
        status = win.kernel32.WaitForSingleObject(self._handle, milliseconds)
        if status == win.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(["llama-server"], timeout)
        if status != win.WAIT_OBJECT_0:
            raise OSError("Could not wait for managed llama-server")
        code = wintypes.DWORD()
        if not win.kernel32.GetExitCodeProcess(self._handle, __import__("ctypes").byref(code)):
            raise OSError("Could not read managed llama-server exit code")
        self.returncode = int(code.value)
        return self.returncode

    def terminate(self):
        from . import windows_containment as win
        if not win.kernel32.TerminateProcess(self._handle, 1):
            raise OSError("Could not terminate managed llama-server")

    kill = terminate

    def close(self):
        if self._handle:
            from . import windows_containment as win
            win.kernel32.CloseHandle(self._handle)
            self._handle = None


def _launch_windows_suspended_job(argv: list[str], cwd: Path,
                                  env: dict[str, str]):
    """Create llama-server suspended, attach Job Object, then resume it.

    No child instruction can execute before Job Object assignment, eliminating
    the Popen-to-Job escape window present in the earlier implementation.
    """
    import ctypes
    from . import windows_containment as win

    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
    environment = win._environment_block(env)
    environment_pointer = (ctypes.cast(environment, ctypes.c_void_p)
                           if environment is not None else None)
    startup = win.STARTUPINFOW()
    startup.cb = ctypes.sizeof(win.STARTUPINFOW)
    info = win.PROCESS_INFORMATION()
    flags = win.CREATE_SUSPENDED | win.CREATE_NO_WINDOW
    if environment is not None:
        flags |= win.CREATE_UNICODE_ENVIRONMENT

    job = win.kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("Could not create a Windows model process Job Object")
    process_handle = None
    thread_handle = None
    try:
        limits = win.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            win.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | win.JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
        limits.BasicLimitInformation.ActiveProcessLimit = 8
        if not win.kernel32.SetInformationJobObject(
                job, win.JobObjectExtendedLimitInformation,
                ctypes.byref(limits), ctypes.sizeof(limits)):
            raise OSError("Could not configure Windows model process Job Object")
        if not win.kernel32.CreateProcessW(
                None, command_line, None, None, False, flags,
                environment_pointer, str(cwd), ctypes.byref(startup),
                ctypes.byref(info)):
            raise OSError("Could not create suspended managed llama-server")
        process_handle, thread_handle = info.hProcess, info.hThread
        if not win.kernel32.AssignProcessToJobObject(job, process_handle):
            raise OSError("Could not attach suspended llama-server to Windows Job Object")
        if win.kernel32.ResumeThread(thread_handle) == 0xFFFFFFFF:
            raise OSError("Could not resume managed llama-server")
        win.kernel32.CloseHandle(thread_handle)
        thread_handle = None
        return _WindowsNativeProcess(process_handle, info.dwProcessId), job
    except BaseException:
        if process_handle:
            win.kernel32.TerminateProcess(process_handle, 125)
            win.kernel32.WaitForSingleObject(process_handle, 5000)
        if thread_handle:
            win.kernel32.CloseHandle(thread_handle)
        if process_handle:
            win.kernel32.CloseHandle(process_handle)
        win.kernel32.CloseHandle(job)
        raise


def _release_windows_job(job) -> None:
    """Close the kill-on-close job even if its members already exited."""
    from . import windows_containment as win
    win.kernel32.TerminateJobObject(job, 1)
    if not win.kernel32.CloseHandle(job):
        raise OSError("Could not release the Windows model process Job Object")


def _terminate_posix_group(pid: int, signal_number: int) -> None:
    """Kill the owned process session (not just the llama-server parent)."""
    if type(pid) is not int or pid <= 1:
        raise ValueError("Missing managed process-group identity")
    try:
        os.killpg(pid, signal_number)
    except ProcessLookupError:
        pass


class ManagedLlamaServer:
    """Bounded, explicit lifecycle for one independently verified GGUF model.

    start() is blocking: GUI invokes it in a worker thread and only communicates
    with Tk from the main event queue. No startup polling runs when idle.
    """

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self._windows_job = None
        self._secrets: tempfile.TemporaryDirectory | None = None
        self.api_key: str | None = None
        self.endpoint: str | None = None
        self.model_name: str | None = None
        self.model_sha256: str | None = None
        self.runtime_sha256: str | None = None

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start_installed_cpu(self, *, model_path: str | Path,
                            directory: str | Path | None = None,
                            timeout: int = DEFAULT_TIMEOUT,
                            threads: int | None = None,
                            context: int | None = None) -> dict:
        """Start the existing pinned CPU runtime after complete bundle verification.

        Reverify the original official release ZIP and every adjacent DLL,
        not merely the executable hash. This never performs network access:
        installation is a separate explicit user action. The small gap
        between verifying DLLs and native loading remains a Windows risk.
        """
        from .runtime_installer import verify_installed_cpu_runtime
        verified = verify_installed_cpu_runtime(directory)
        return self.start(executable=verified["path"],
                          executable_sha256=verified["sha256"],
                          model_path=model_path, timeout=timeout,
                          threads=threads, context=context)

    def start(self, *, executable: str | Path, executable_sha256: str,
              model_path: str | Path, timeout: int = DEFAULT_TIMEOUT,
              threads: int | None = None, context: int | None = None) -> dict:
        if self.process is not None or self._secrets is not None:
            raise RuntimeError("A managed llama-server session already exists")
        if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT:
            raise ValueError("Startup timeout must be 1–180 seconds")
        model_file, manifest = _verified_model(model_path)
        binary = _verified_runtime(executable, executable_sha256)
        settings = local_memory_settings()
        if context is None:
            context = min(4096, settings["num_ctx"])
        if type(context) is not int or not 512 <= context <= 8192:
            raise ValueError("Model context must be between 512 and 8192 tokens")
        # Explicit overrides must not bypass the canonical local RAM
        # profile. A larger context can exhaust KV cache on small hosts.
        if context > settings["num_ctx"]:
            raise ValueError(
                "Model context exceeds the current RAM profile; choose "
                "a smaller context or free system memory")
        if threads is None:
            threads = min(8, max(1, (os.cpu_count() or 2) // 2))
        if type(threads) is not int or not 1 <= threads <= 32:
            raise ValueError("CPU thread count must be between 1 and 32")
        # Recheck available physical memory after verifying large model
        # and binary bytes, immediately before starting the native server.
        # A competing app may have consumed RAM during the slow SHA-256 pass.
        if model_file.stat().st_size > safe_model_file_budget(system_memory()):
            raise RuntimeError(
                "Free RAM fell below the conservative model budget "
                "before native inference startup; close other apps "
                "or choose a smaller verified GGUF")

        try:
            self._secrets = tempfile.TemporaryDirectory(prefix="forgeos-llama-")
            key = secrets.token_urlsafe(32)
            key_file = Path(self._secrets.name) / "api-keys"
            # Private permissions on POSIX; Windows ACLs still depend on the
            # temporary-directory security policy of the user's session.
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as output:
                output.write(key + "\n")
            port = _unused_loopback_port()
            argv = [str(binary), "--model", str(model_file),
                    "--host", "127.0.0.1", "--port", str(port),
                    "--alias", ALIAS, "--api-key-file", str(key_file),
                    "--ctx-size", str(context), "--threads", str(threads),
                    "--n-gpu-layers", "0", "--parallel", "1",
                    "--no-webui"]
            if os.name == "nt":
                self.process, self._windows_job = _launch_windows_suspended_job(
                    argv, binary.parent, _child_environment())
            else:
                self.process = subprocess.Popen(
                    argv, shell=False, cwd=str(binary.parent),
                    env=_child_environment(), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
            self.api_key = key
            self.endpoint = f"http://127.0.0.1:{port}/v1"
            self.model_name = ALIAS
            self.model_sha256 = manifest["sha256"]
            self.runtime_sha256 = executable_sha256
            # Public health is not an authenticated identity proof: require
            # an authenticated model-catalog request before returning READY.
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                if self.process.poll() is not None:
                    raise RuntimeError("Managed llama-server exited before becoming ready")
                req = Request(
                    self.endpoint.removesuffix("/v1") + "/health",
                    headers={"Accept": "application/json"})
                try:
                    with _open(req, timeout=2) as response:
                        data = response.read(256)
                    if len(data) > 256 or json.loads(data).get("status") != "ok":
                        raise RuntimeError("Unexpected llama-server health response")
                    models = LocalChatModel(
                        backend="llama.cpp", model=ALIAS,
                        endpoint=self.endpoint, api_key=key).installed()
                    if not any(item["name"] == ALIAS for item in models):
                        raise RuntimeError("Managed server reported the wrong model")
                    return {"endpoint": self.endpoint, "model": ALIAS,
                            "sha256": self.model_sha256, "runtime_sha256":
                                self.runtime_sha256, "pid": self.process.pid}
                except (HTTPError, URLError, TimeoutError, OSError,
                        ConnectionError, ValueError):
                    pass
                time.sleep(0.2)
            raise RuntimeError("Managed llama-server startup timed out")
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        process = self.process
        self.process = None
        job = self._windows_job
        self._windows_job = None
        try:
            job_error = None
            if job is not None:
                # A Windows Job Object release can fail. Do not skip the
                # process cleanup when the native API reports an error.
                try:
                    _release_windows_job(job)
                except OSError as exc:
                    job_error = exc
            if process is not None:
                if process.poll() is None and (job is None or job_error is not None):
                    if os.name != "nt":
                        _terminate_posix_group(process.pid, signal.SIGTERM)
                    process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    if os.name != "nt" and job is None:
                        _terminate_posix_group(process.pid, signal.SIGKILL)
                    process.kill()
                    process.wait(timeout=4)
            if hasattr(process, "close"):
                process.close()
            if job_error is not None:
                raise job_error
        finally:
            temporary = self._secrets
            self._secrets = None
            if temporary is not None:
                temporary.cleanup()
            self.api_key = None
            self.endpoint = None
            self.model_name = None
            self.model_sha256 = None
            self.runtime_sha256 = None
