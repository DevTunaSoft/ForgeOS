"""Minimal test entry point used only inside ForgeOS secure execution."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


def _output_stream(root: Path):
    raw = os.environ.get("FORGEOS_TEST_OUTPUT_FILE", "")
    if not raw:
        raise RuntimeError("secure test output path is missing")
    target = Path(raw)
    if not target.is_absolute():
        raise RuntimeError("secure test output path must be absolute")
    target = target.resolve()
    if not target.is_relative_to(root):
        raise RuntimeError("secure test output path escapes execution snapshot")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.open("w", encoding="utf-8", errors="replace")


def run_unittest() -> int:
    """Discover and run unittest tests from the granted execution snapshot."""
    if os.environ.get("FORGEOS_EXECUTION_SNAPSHOT") != "1":
        return 90
    root = Path.cwd().resolve()
    if not root.is_dir():
        return 91
    sys.path.insert(0, str(root))
    try:
        with _output_stream(root) as stream:
            suite = unittest.defaultTestLoader.discover(str(root))
            result = unittest.TextTestRunner(
                stream=stream, verbosity=2).run(suite)
            stream.flush()
            return 0 if result.wasSuccessful() else 1
    except BaseException as exc:
        try:
            raw = os.environ.get("FORGEOS_TEST_OUTPUT_FILE", "")
            target = Path(raw).resolve() if raw else None
            if target is not None and target.is_relative_to(root):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    "ForgeOS secure test runner failed: "
                    + type(exc).__name__ + ": " + str(exc),
                    encoding="utf-8", errors="replace")
        except Exception:
            pass
        return 92
