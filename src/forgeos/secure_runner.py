"""Production Windows autonomous test execution.

Frozen ForgeOS uses its own executable as the Python runtime inside a
zero-capability AppContainer. There is no fallback to the weaker host runner.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from .appcontainer import run_secure_appcontainer
from .test_runtime import TestRuntime


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
    argv = [str(executable), "--sandbox-unittest"]
    return run_secure_appcontainer(
        argv, runtime.path, runtime.env,
        executable=executable, timeout=timeout)
