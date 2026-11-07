"""Minimal test entry points used only inside ForgeOS secure execution."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import os
from pathlib import Path
import sys
import unittest


def _output_path(root: Path) -> Path:
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
    return target


def _output_stream(root: Path):
    return _output_path(root).open(
        "w", encoding="utf-8", errors="replace")


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


def _snapshot_root() -> Path:
    root = Path.cwd().resolve()
    if not root.is_dir():
        raise NotADirectoryError("invalid execution snapshot")
    return root


def _record_failure(root: Path | None, exc: BaseException) -> None:
    if root is None:
        return
    try:
        target = _output_path(root)
        target.write_text(
            "ForgeOS secure test runner failed: "
            + type(exc).__name__ + ": " + str(exc),
            encoding="utf-8", errors="replace")
    except Exception:
        pass


def run_unittest() -> int:
    """Discover and run unittest tests from the granted execution snapshot."""
    if os.environ.get("FORGEOS_EXECUTION_SNAPSHOT") != "1":
        return 90
    root = None
    original_path = None
    try:
        root = _snapshot_root()
        original_path = list(sys.path)
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
        _record_failure(root, exc)
        return 92
    finally:
        if original_path is not None:
            sys.path[:] = original_path


def run_pytest() -> int:
    """Run bundled pytest inside the granted execution snapshot."""
    if os.environ.get("FORGEOS_EXECUTION_SNAPSHOT") != "1":
        return 90
    root = None
    original_path = None
    try:
        root = _snapshot_root()
        original_path = list(sys.path)
        _scrub_bootstrap_environment(root)
        sys.path.insert(0, str(root))
        # Imported only in the packaged pytest path. PyInstaller still sees the
        # static import and bundles pytest plus its hook-discovered internals.
        import pytest
        with _output_stream(root) as stream, \
                redirect_stdout(stream), redirect_stderr(stream):
            code = pytest.main([
                "-q", "--color=no", "--disable-warnings", str(root),
            ])
            stream.flush()
        return int(code)
    except BaseException as exc:
        _record_failure(root, exc)
        return 92
    finally:
        if original_path is not None:
            sys.path[:] = original_path
