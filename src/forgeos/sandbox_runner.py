"""Minimal test entry point used only inside ForgeOS secure execution."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
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


def _scrub_bootstrap_environment(root: Path) -> None:
    """Remove host-only launch values before any generated project code runs."""
    marker = os.environ.pop("FORGEOS_BOOTSTRAP_LOCALAPPDATA", None)
    if marker != "1":
        raise RuntimeError("secure AppContainer bootstrap marker is missing")
    appdata_root = root / ".forgeos-runtime" / "home" / "AppData"
    local = appdata_root / "Local"
    roaming = appdata_root / "Roaming"
    local.mkdir(parents=True, exist_ok=True)
    roaming.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(local)
    os.environ["APPDATA"] = str(roaming)


def run_unittest() -> int:
    """Discover and run unittest tests from the granted execution snapshot."""
    if os.environ.get("FORGEOS_EXECUTION_SNAPSHOT") != "1":
        return 90
    root = Path.cwd().resolve()
    if not root.is_dir():
        return 91
    original_path = list(sys.path)
    try:
        _scrub_bootstrap_environment(root)
        sys.path.insert(0, str(root))
        with _output_stream(root) as stream:
            loader = unittest.TestLoader()
            suite = loader.discover(str(root))
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
    finally:
        sys.path[:] = original_path


def run_pytest() -> int:
    """Run bundled pytest inside zero-capability execution snapshot.

    Keep its built-in logging plugin, including caplog, but force the log
    sink away from Windows NUL to a fixed snapshot-owned file watched by
    the parent. No arbitrary pytest config can redirect this CLI override.
    """
    if os.environ.get("FORGEOS_EXECUTION_SNAPSHOT") != "1":
        return 90
    root = Path.cwd().resolve()
    if not root.is_dir():
        return 91
    original_path = list(sys.path)
    try:
        _scrub_bootstrap_environment(root)
        from .test_runtime import RUNTIME_DIR
        logfile = root / RUNTIME_DIR / "pytest-log.txt"
        if (os.environ.get("FORGEOS_TEST_LOG_FILE") != str(logfile)
                or logfile.is_symlink() or not logfile.parent.is_dir()):
            raise RuntimeError("Secure pytest log path is invalid")
        sys.path.insert(0, str(root))
        # Import only in hidden frozen child, not on GUI startup.
        import pytest
        with _output_stream(root) as stream, \
                redirect_stdout(stream), redirect_stderr(stream):
            code = pytest.main([
                "-q", "--capture=no", "--color=no", "--disable-warnings",
                "-p", "no:cacheprovider", "--log-file=" + str(logfile),
                "--log-file-mode=w", str(root),
            ])
            stream.flush()
        return int(code)
    except BaseException as exc:
        try:
            raw = os.environ.get("FORGEOS_TEST_OUTPUT_FILE", "")
            target = Path(raw).resolve() if raw else None
            if target is not None and target.is_relative_to(root):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    "ForgeOS secure pytest failed: "
                    + type(exc).__name__ + ": " + str(exc),
                    encoding="utf-8", errors="replace")
        except Exception:
            pass
        return 92
    finally:
        sys.path[:] = original_path
