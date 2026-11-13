"""Disposable execution snapshots for running generated code safely.

The editable Git worktree remains the source of truth. Tests may mutate only a
fresh execution copy. On Windows the copy can be labeled Low integrity before
the child process starts.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import stat

MARKER = ".forgeos-execution-owner.json"
RUNTIME_DIR = ".forgeos-runtime"
VERSION = 1
MAX_MARKER_BYTES = 512 * 1024


@dataclass(frozen=True)
class TestRuntime:
    path: Path
    env: dict[str, str]


def _runtime_path(workspace: Path) -> Path:
    return workspace.parent / f".forgeos-exec-{workspace.name}"


def _delete_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _marker_path(runtime: Path) -> Path:
    return runtime.with_name(runtime.name + MARKER)


def _validate_marker(runtime: Path, workspace: Path) -> Path:
    marker = _marker_path(runtime)
    if not marker.is_file() or marker.is_symlink():
        raise ValueError(f"Refusing to remove unowned execution directory: {runtime}")
    try:
        with marker.open("rb") as stream:
            raw = stream.read(MAX_MARKER_BYTES + 1)
        if len(raw) > MAX_MARKER_BYTES:
            raise ValueError("Execution runtime marker is too large")
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError("Execution runtime marker is invalid") from exc
    if (not isinstance(data, dict) or data.get("version") != VERSION
            or data.get("workspace") != str(workspace)
            or data.get("runtime") != str(runtime)):
        raise ValueError("Execution runtime ownership marker mismatch")
    return marker


def remove_test_snapshot(workspace: str | Path) -> bool:
    source = Path(workspace).expanduser().resolve(strict=True)
    runtime = _runtime_path(source)
    marker = _marker_path(runtime)
    if not runtime.exists():
        if marker.exists():
            _validate_marker(runtime, source)
            marker.unlink()
        return False
    if runtime.is_symlink() or not runtime.is_dir():
        raise ValueError("Execution runtime path is not a real directory")
    marker = _validate_marker(runtime, source)
    shutil.rmtree(runtime, onerror=_delete_readonly)
    marker.unlink()
    return True


def _ignore_runtime_metadata(_directory, names):
    ignored = set()
    if ".forgeos" in names:
        ignored.add(".forgeos")
    if RUNTIME_DIR in names:
        ignored.add(RUNTIME_DIR)
    ignored.update(name for name in names if name.startswith(".env"))
    return ignored


def _build_environment(runtime: Path) -> dict[str, str]:
    writable = runtime / RUNTIME_DIR
    directories = {
        "tmp": writable / "tmp",
        "home": writable / "home",
        "cache": writable / "cache",
        "pycache": writable / "pycache",
        "pip": writable / "pip",
        "npm": writable / "npm",
        "cargo": writable / "cargo",
        "cargo-target": writable / "cargo-target",
        "gopath": writable / "gopath",
        "gocache": writable / "gocache",
        "dotnet": writable / "dotnet",
        "nuget": writable / "nuget",
        "gradle": writable / "gradle",
    }
    for path in directories.values():
        path.mkdir(parents=True, exist_ok=True)

    # Do not inherit the parent's arbitrary environment. Autonomous test code
    # must not receive API keys, access tokens, cloud credentials, or unrelated
    # application state simply because ForgeOS itself was launched with them.
    allowed_host = {
        "SystemRoot", "WINDIR", "ComSpec", "OS", "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER", "NUMBER_OF_PROCESSORS", "LANG", "LC_ALL",
    }
    env = {}
    for key in allowed_host:
        value = os.environ.get(key)
        if isinstance(value, str) and value and "\x00" not in value:
            env[key] = value
    env.update({
        "TEMP": str(directories["tmp"]),
        "TMP": str(directories["tmp"]),
        "TMPDIR": str(directories["tmp"]),
        "HOME": str(directories["home"]),
        "USERPROFILE": str(directories["home"]),
        "XDG_CACHE_HOME": str(directories["cache"]),
        "PYTHONPYCACHEPREFIX": str(directories["pycache"]),
        "PIP_CACHE_DIR": str(directories["pip"]),
        "npm_config_cache": str(directories["npm"]),
        "NPM_CONFIG_CACHE": str(directories["npm"]),
        "CARGO_HOME": str(directories["cargo"]),
        "CARGO_TARGET_DIR": str(directories["cargo-target"]),
        "GOPATH": str(directories["gopath"]),
        "GOCACHE": str(directories["gocache"]),
        "DOTNET_CLI_HOME": str(directories["dotnet"]),
        "NUGET_PACKAGES": str(directories["nuget"]),
        "GRADLE_USER_HOME": str(directories["gradle"]),
        "FORGEOS_EXECUTION_SNAPSHOT": "1",
        "PYTHONNOUSERSITE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    })
    return env


def create_test_snapshot(workspace: str | Path) -> TestRuntime:
    """Create a fresh, guarded copy of one ForgeOS worktree."""
    raw = Path(workspace).expanduser()
    if raw.is_symlink():
        raise ValueError("Workspace must not be a symlink")
    source = raw.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Workspace must be a real directory")
    runtime = _runtime_path(source)
    if runtime == source or runtime.is_relative_to(source):
        raise ValueError("Execution runtime must be outside the editable workspace")
    marker = _marker_path(runtime)
    if runtime.exists() or marker.exists():
        remove_test_snapshot(source)
    runtime.mkdir(parents=False)
    try:
        stream = marker.open("x", encoding="utf-8")
        try:
            with stream:
                json.dump({
                    "version": VERSION,
                    "workspace": str(source),
                    "runtime": str(runtime),
                }, stream, sort_keys=True)
        except BaseException:
            marker.unlink(missing_ok=True)
            raise
    except BaseException:
        runtime.rmdir()
        raise
    try:
        shutil.copytree(
            source, runtime, dirs_exist_ok=True, symlinks=True,
            ignore=_ignore_runtime_metadata,
        )
        env = _build_environment(runtime)
        return TestRuntime(path=runtime, env=env)
    except BaseException:
        try:
            remove_test_snapshot(source)
        except Exception:
            pass
        raise


def create_windows_low_integrity_snapshot(workspace: str | Path) -> TestRuntime:
    """Create and Low-label a disposable Windows execution snapshot."""
    if os.name != "nt":
        raise OSError("Windows Low-integrity snapshots require Windows")
    runtime = create_test_snapshot(workspace)
    from .windows_containment import prepare_low_integrity_workspace
    try:
        prepare_low_integrity_workspace(runtime.path)
        return runtime
    except BaseException:
        try:
            remove_test_snapshot(workspace)
        except Exception:
            pass
        raise
