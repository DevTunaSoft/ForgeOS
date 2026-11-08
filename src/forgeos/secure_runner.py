"""Production Windows autonomous test execution.

Frozen ForgeOS uses its own executable as the Python runtime inside a
zero-capability AppContainer. There is no fallback to the weaker host runner.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from .appcontainer import run_secure_appcontainer
from .test_runtime import RUNTIME_DIR, TestRuntime


def _is_windows() -> bool:
    return os.name == "nt"


def _host_local_appdata() -> str:
    """Return the Windows profile root needed to bootstrap AppContainer launch."""
    value = os.environ.get("LOCALAPPDATA", "")
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeError("Windows LOCALAPPDATA is unavailable for AppContainer bootstrap")
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise RuntimeError("Windows LOCALAPPDATA is invalid for AppContainer bootstrap")
    return str(path.resolve())


def _bootstrap_environment(runtime: TestRuntime) -> dict[str, str]:
    """Add only the host value Windows needs before the sandbox child starts.

    AppContainer process creation resolves its package profile through the
    caller's LOCALAPPDATA. The child sandbox runner replaces this value with a
    snapshot-owned path before importing or discovering generated project code.
    """
    env = dict(runtime.env)
    env["LOCALAPPDATA"] = _host_local_appdata()
    env["FORGEOS_BOOTSTRAP_LOCALAPPDATA"] = "1"
    return env


def run_secure_windows_test(runtime: TestRuntime, preset: str,
                            timeout: int = 180) -> dict:
    if not _is_windows():
        raise OSError("Secure Windows execution requires Windows")
    if not getattr(sys, "frozen", False):
        raise RuntimeError(
            "Production AppContainer runner requires the frozen ForgeOS runtime")
    if preset != "python-unittest":
        raise RuntimeError(
            f"Secure Windows runner does not yet support preset: {preset}")
    executable = Path(sys.executable).resolve(strict=True)
    output_file = runtime.path / RUNTIME_DIR / "test-output.txt"
    env = _bootstrap_environment(runtime)
    env["FORGEOS_TEST_OUTPUT_FILE"] = str(output_file)
    argv = [str(executable), "--sandbox-unittest"]
    result = run_secure_appcontainer(
        argv, runtime.path, env,
        executable=executable, timeout=timeout)
    try:
        if output_file.is_file() and not output_file.is_symlink():
            from .output_capture import summarize_output_file
            with output_file.open("rb") as output_stream:
                result.update(summarize_output_file(
                    output_stream, encoding="utf-8"))
    except OSError as exc:
        result["output"] = (
            result.get("output", "")[-12000:]
            + "\nCould not read secure test output: " + str(exc)
        )[-16000:]
    return result
