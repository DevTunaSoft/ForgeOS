"""Minimal test entry point used only inside ForgeOS secure execution."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import faulthandler
import os
from pathlib import Path
import re
import stat
import sys
import unittest

_MODULE_NAME = re.compile(r"test_[A-Za-z0-9_]{1,80}\.py")
# In trace mode, a test still running after this many seconds has its stack
# written to the output, before the host's module timeout ends the run.
_HANG_DUMP_SECONDS = 120

# On Windows, CPython creates a folder made with mode 0o700 under a protected
# owner-only DACL. The sandboxed child has no entry in that DACL, so it cannot
# use any folder made that way: tempfile.mkdtemp, and private folders the code
# makes with mode 0o700, all failed inside the sandbox.
_WINDOWS_PRIVATE_MODE = 0o700


def _inheriting_mkdir(real_mkdir):
    """Wrap os.mkdir so a folder made with mode 0o700 takes the snapshot's rules.

    The default mode gives the folder the access its parent grants, which is
    the access the sandboxed child already has throughout the snapshot.
    """

    def mkdir(path, mode=0o777, *, dir_fd=None):
        if mode == _WINDOWS_PRIVATE_MODE:
            mode = 0o777
        return real_mkdir(path, mode, dir_fd=dir_fd)

    return mkdir


def _install_inheriting_mkdir() -> None:
    """Install the wrapper in the sandboxed child, on Windows only."""
    if os.name != "nt":
        return
    os.mkdir = _inheriting_mkdir(os.mkdir)


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WINDOWS_ACCESS_DENIED = 5


def _links_checked_path_resolver(original, base=None):
    """Wrap ntpath._getfinalpathname so a sandboxed child can still resolve paths.

    Inside the AppContainer, GetFinalPathNameByHandle fails with access denied
    when it builds the drive-letter name of a path that opens and stats fine,
    so Path.resolve(strict=True) failed everywhere. This wrapper answers only
    that failure: it returns the absolute path as written, provided no part of
    the path inside the snapshot is a link. Other failures, paths outside the
    snapshot, and paths through a link still raise. It grants no new access.
    """
    snapshot = os.path.abspath(base if base is not None else os.getcwd())

    def _getfinalpathname(path):
        try:
            return original(path)
        except OSError as exc:
            if getattr(exc, "winerror", None) != _WINDOWS_ACCESS_DENIED:
                raise
            absolute = os.path.abspath(path)
            try:
                relative = os.path.relpath(absolute, snapshot)
            except ValueError:
                raise exc
            if relative == os.pardir or relative.startswith(os.pardir + os.sep):
                raise exc
            parts = [] if relative == os.curdir else relative.split(os.sep)
            current = snapshot
            for part in [None] + parts:
                if part is not None:
                    current = os.path.join(current, part)
                try:
                    info = os.lstat(current)
                except OSError:
                    raise exc
                if (stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0)
                        & _FILE_ATTRIBUTE_REPARSE_POINT):
                    raise exc
            return absolute

    return _getfinalpathname


def _install_windows_path_resolver() -> None:
    """Install the path resolver in the sandboxed child, on Windows only."""
    if os.name != "nt":
        return
    import ntpath
    ntpath._getfinalpathname = _links_checked_path_resolver(ntpath._getfinalpathname)


def selected_test_pattern(module: str) -> str:
    """Return the discovery pattern: every test module, or one named module."""
    if not module:
        return "test*.py"
    if not _MODULE_NAME.fullmatch(module):
        raise RuntimeError("self-test module name is invalid")
    return module


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
    # TEMP is a folder under the child's AppData. The child creates it with the
    # default mode, so it takes the snapshot's access rules like the rest of it.
    temp = local / "Temp"
    temp.mkdir(parents=True, exist_ok=True)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(temp)


class _TraceResult(unittest.TextTestResult):
    """Name each test as it starts, so a test that never finishes is visible."""

    def startTest(self, test):
        super().startTest(test)
        self.stream.write("STARTED " + test.id() + "\n")
        self.stream.flush()


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
        _install_inheriting_mkdir()
        _install_windows_path_resolver()
        sys.path.insert(0, str(root))
        with _output_stream(root) as stream:
            loader = unittest.TestLoader()
            suite = loader.discover(
                str(root),
                pattern=selected_test_pattern(
                    os.environ.get("FORGEOS_SELF_TEST_MODULE", "")))
            trace = os.environ.get("FORGEOS_SELF_TEST_TRACE") == "1"
            if trace:
                faulthandler.dump_traceback_later(_HANG_DUMP_SECONDS, file=stream)
            try:
                result = unittest.TextTestRunner(
                    stream=stream, verbosity=2,
                    resultclass=_TraceResult if trace else unittest.TextTestResult,
                ).run(suite)
            finally:
                if trace:
                    faulthandler.cancel_dump_traceback_later()
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
        _install_inheriting_mkdir()
        _install_windows_path_resolver()
        from .test_runtime import RUNTIME_DIR
        logfile = root / RUNTIME_DIR / "pytest-log.txt"
        raw_log = os.environ.get("FORGEOS_TEST_LOG_FILE", "")
        raw_output = os.environ.get("FORGEOS_TEST_OUTPUT_FILE", "")
        supplied = Path(raw_log) if raw_log else None
        primary = Path(raw_output) if raw_output else None
        # The TRUSTED parent validates both paths, reparse points, and the
        # snapshot root *before* launching the child. Within a zero-capability
        # AppContainer, re-resolving .forgeos-runtime can raise WinError 5
        # even when opening a known, granted child file is permitted.
        # Compare the two snapshot-owned paths lexically without a second
        # directory open/resolve, and let the parent watchdog catch link
        # substitutions, oversize outputs and unsafe directory changes.
        if (supplied is None or primary is None
                or not supplied.is_absolute() or not primary.is_absolute()
                or supplied.name != "pytest-log.txt"
                or primary.name != "test-output.txt"
                or supplied.parent.name != RUNTIME_DIR
                or primary.parent.name != RUNTIME_DIR
                or supplied != primary.with_name("pytest-log.txt")):
            raise RuntimeError("Secure pytest log path is invalid")
        logfile = supplied
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
