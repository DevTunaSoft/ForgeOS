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
    child_modes = {
        "python-unittest": "--sandbox-unittest",
        "python-pytest": "--sandbox-pytest",
    }
    child_mode = child_modes.get(preset)
    if child_mode is None:
        raise RuntimeError(
            f"Secure Windows runner does not yet support preset: {preset}")
    executable = Path(sys.executable).resolve(strict=True)
    output_file = runtime.path / RUNTIME_DIR / "test-output.txt"
    # Reject unsafe owned paths before reading bootstrap host environment or
    # creating any Windows child/AppContainer grants.
    from .output_capture import validate_secure_output_path
    monitored = validate_secure_output_path(runtime.path, output_file)
    extra_outputs = ()
    if preset == "python-pytest":
        # Pytest's logging plugin opens os.devnull by default, which a
        # zero-capability AppContainer may not access. Supply a fixed local
        # file and watch its growth alongside the original test output.
        log_file = runtime.path / RUNTIME_DIR / "pytest-log.txt"
        extra_outputs = (validate_secure_output_path(runtime.path, log_file),)
    env = _bootstrap_environment(runtime)
    env["FORGEOS_TEST_OUTPUT_FILE"] = str(output_file)
    if extra_outputs:
        env["FORGEOS_TEST_LOG_FILE"] = str(extra_outputs[0])
    argv = [str(executable), child_mode]
    kwargs = {
        "executable": executable,
        "timeout": timeout,
        "watched_output": monitored,
    }
    if extra_outputs:
        kwargs["watched_extra_outputs"] = extra_outputs
    result = run_secure_appcontainer(
        argv, runtime.path, env, **kwargs)
    # The AppContainer's native stdout capture is independent of the child
    # test-output file. A complete child file cannot erase earlier evidence
    # of a failed/oversized native capture.
    native_incomplete = result.get("capture_complete") is False
    native_preview = (
        result.get("output", "")[-2000:] if native_incomplete
        and isinstance(result.get("output"), str) else "")
    try:
        # Revalidate after the child exits: a successful exit without its
        # ForgeOS-owned output file is not sufficient test evidence.
        validate_secure_output_path(runtime.path, output_file)
        if not output_file.is_file():
            raise OSError("Secure test output file was not produced")
        from .output_capture import summarize_output_file
        with output_file.open("rb") as output_stream:
            result.update(summarize_output_file(
                output_stream, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Fail closed instead of returning the AppContainer process exit
        # status as a test pass. An incomplete result stops autonomous
        # repair/recovery and retains a short diagnostic for inspection.
        previous = result.get("output", "")
        result.update(
            passed=False,
            capture_complete=False,
            capture_error="secure-test-output-unavailable",
            evidence_reference=None,
            output=(
                (previous[-12000:] if isinstance(previous, str) else "")
                + "\nSecure test output unavailable: " + str(exc)[-2000:]
            )[-16000:],
        )
    if native_incomplete:
        result["passed"] = False
        result["capture_complete"] = False
        if not result.get("capture_error"):
            result["capture_error"] = "secure-native-output-incomplete"
        result["output"] = (
            native_preview
            + "\n[ForgeOS warning: native runner output evidence incomplete]\n"
            + (result.get("output", "")[-12000:]
               if isinstance(result.get("output"), str) else "")
        )[-16000:]
    return result
