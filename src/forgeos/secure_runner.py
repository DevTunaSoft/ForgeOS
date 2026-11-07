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


def run_secure_windows_test(runtime: TestRuntime, preset: str,
                            timeout: int = 180) -> dict:
    if os.name != "nt":
        raise OSError("Secure Windows execution requires Windows")
    if not getattr(sys, "frozen", False):
        raise RuntimeError(
            "Production AppContainer runner requires the frozen ForgeOS runtime")
    if preset != "python-unittest":
        raise RuntimeError(
            f"Secure Windows runner does not yet support preset: {preset}")
    executable = Path(sys.executable).resolve(strict=True)
    output_file = runtime.path / RUNTIME_DIR / "test-output.txt"
    env = dict(runtime.env)
    env["FORGEOS_TEST_OUTPUT_FILE"] = str(output_file)
    argv = [str(executable), "--sandbox-unittest"]
    result = run_secure_appcontainer(
        argv, runtime.path, env,
        executable=executable, timeout=timeout)
    try:
        if output_file.is_file() and not output_file.is_symlink():
            result["output"] = output_file.read_text(
                encoding="utf-8", errors="replace")[-16000:]
    except OSError as exc:
        result["output"] = (
            result.get("output", "")[-12000:]
            + "\nCould not read secure test output: " + str(exc)
        )[-16000:]
    return result
