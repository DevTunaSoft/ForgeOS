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
MAX_MARKER_BYTES = 4096
_WINDOWS_REPARSE_POINT = 0x0400


@dataclass(frozen=True)
class TestRuntime:
    path: Path
    env: dict[str, str]


def _runtime_path(workspace: Path) -> Path:
    return workspace.parent / f".forgeos-exec-{workspace.name}"


def _delete_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _is_reparse_point(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)


def _is_link_or_reparse(path: Path, info) -> bool:
    if _is_reparse_point(info):
        return True
    try:
        return path.is_symlink()
    except OSError:
        return True


def _same_identity(before, after) -> bool:
    return all(getattr(before, field, None) == getattr(after, field, None)
               for field in ("st_dev", "st_ino"))


def _same_file(before, after) -> bool:
    return (_same_identity(before, after)
            and getattr(before, "st_size", None)
            == getattr(after, "st_size", None)
            and getattr(before, "st_mtime_ns", None)
            == getattr(after, "st_mtime_ns", None))


def _directory_info(path: Path, description: str, *, allow_missing=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ValueError(f"{description} is missing: {path}")
    except OSError as exc:
        raise ValueError(f"Cannot inspect {description}: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or _is_link_or_reparse(path, info):
        raise ValueError(f"{description} must be a real directory: {path}")
    return info


def _regular_file_info(path: Path, description: str, *, allow_missing=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ValueError(f"{description} is missing: {path}")
    except OSError as exc:
        raise ValueError(f"Cannot inspect {description}: {path}") from exc
    if (not stat.S_ISREG(info.st_mode) or _is_link_or_reparse(path, info)
            or getattr(info, "st_nlink", 1) != 1):
        raise ValueError(
            f"{description} must be an unlinked regular file: {path}")
    return info


class _SnapshotIdentity:
    """Keep a disposable snapshot identity stable through cleanup."""

    def __init__(self, info, *, directory: bool, descriptor=None):
        self.info = info
        self.directory = directory
        self.descriptor = descriptor

    def matches(self, current) -> bool:
        if self.descriptor is not None:
            try:
                info = os.fstat(self.descriptor)
            except OSError:
                return False
        else:
            info = self.info
        return (_same_identity(info, current) if self.directory
                else _same_file(info, current))

    def close(self) -> None:
        if self.descriptor is None:
            return
        descriptor = self.descriptor
        self.descriptor = None
        try:
            os.close(descriptor)
        except OSError:
            pass


def _write_snapshot_marker(marker: Path, payload: dict) -> None:
    """Create one small ownership marker without overwriting a new path."""
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"))
               + "\n").encode("utf-8")
    if len(encoded) > MAX_MARKER_BYTES:
        raise ValueError("Execution ownership marker is oversized")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(marker, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
        current = marker.lstat()
        opened = os.fstat(descriptor)
        if not _same_file(opened, current):
            raise ValueError("Execution ownership marker changed during creation")
    except FileExistsError as exc:
        raise ValueError(
            "Execution ownership marker appeared during snapshot creation") from exc
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(
            "Execution ownership marker could not be created") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _hold_snapshot_identity(path: Path, description: str, *, directory: bool,
                            expected=None):
    info = (_directory_info(path, description)
            if directory else _regular_file_info(path, description))
    if (expected is not None
            and (not _same_identity(expected, info) if directory
                 else not _same_file(expected, info))):
        raise ValueError(f"{description} changed during cleanup: {path}")
    if os.name == "nt":
        # Windows directory/file handles can block the recursive removal or
        # marker unlink, so retain the identity comparison without a handle.
        return _SnapshotIdentity(info, directory=directory)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(
            f"{description} could not be opened safely: {path}") from exc
    try:
        current = path.lstat()
        opened = os.fstat(descriptor)
        matches = (_same_identity(info, current)
                   and _same_identity(info, opened)
                   if directory else
                   _same_file(info, current) and _same_file(info, opened))
        if (not matches or _is_reparse_point(current)
                or _is_reparse_point(opened)):
            raise ValueError(f"{description} changed during cleanup: {path}")
    except ValueError:
        os.close(descriptor)
        raise
    except OSError as exc:
        os.close(descriptor)
        raise ValueError(f"Cannot inspect opened {description}: {path}") from exc
    return _SnapshotIdentity(info, directory=directory, descriptor=descriptor)


def _check_snapshot_identity(path: Path, identity: _SnapshotIdentity,
                             parent_info, description: str, *, directory: bool):
    current = (_directory_info(path, description)
               if directory else _regular_file_info(path, description))
    current_parent = _directory_info(path.parent, "Execution runtime parent")
    if (not identity.matches(current)
            or not _same_identity(parent_info, current_parent)):
        raise ValueError(f"{description} changed during cleanup: {path}")


def _marker_path(runtime: Path) -> Path:
    return runtime.with_name(runtime.name + MARKER)


def _read_marker_bytes(marker: Path, identity: _SnapshotIdentity) -> bytes:
    descriptor = None
    try:
        if identity.descriptor is not None:
            descriptor = os.dup(identity.descriptor)
            stream = os.fdopen(descriptor, "rb")
            descriptor = None
        else:
            stream = marker.open("rb")
        with stream:
            raw = stream.read(MAX_MARKER_BYTES + 1)
    except OSError as exc:
        raise ValueError("Execution ownership marker could not be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(raw) > MAX_MARKER_BYTES:
        raise ValueError("Execution ownership marker is oversized")
    return raw


def _validate_marker(runtime: Path, workspace: Path, marker_identity=None) -> Path:
    marker = _marker_path(runtime)
    owned_here = marker_identity is None
    if owned_here:
        marker_identity = _hold_snapshot_identity(
            marker, "Execution ownership marker", directory=False)
    try:
        current = _regular_file_info(marker, "Execution ownership marker")
        if not marker_identity.matches(current):
            raise ValueError("Execution ownership marker changed during cleanup")
        try:
            raw = _read_marker_bytes(marker, marker_identity)
        except ValueError:
            raise
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ValueError("Execution runtime marker is invalid") from exc
        current = _regular_file_info(marker, "Execution ownership marker")
        if not marker_identity.matches(current):
            raise ValueError("Execution ownership marker changed during read")
        if (not isinstance(data, dict) or data.get("version") != VERSION
                or data.get("workspace") != str(workspace)
                or data.get("runtime") != str(runtime)):
            raise ValueError("Execution runtime ownership marker mismatch")
        return marker
    finally:
        if owned_here:
            marker_identity.close()


def _remove_snapshot_file(path: Path, identity: _SnapshotIdentity,
                          parent_info, description: str) -> None:
    _check_snapshot_identity(
        path, identity, parent_info, description, directory=False)
    try:
        path.unlink()
    except OSError as exc:
        raise ValueError(f"Cannot remove {description}: {path}") from exc
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError(f"Cannot verify removed {description}: {path}") from exc
    else:
        raise ValueError(f"{description} changed during cleanup: {path}")
    current_parent = _directory_info(path.parent, "Execution runtime parent")
    if not _same_identity(parent_info, current_parent):
        raise ValueError(f"Execution runtime parent changed during cleanup: {path.parent}")


def _remove_snapshot_directory(runtime: Path, identity: _SnapshotIdentity,
                               parent_info) -> None:
    _check_snapshot_identity(
        runtime, identity, parent_info, "Execution runtime directory",
        directory=True)
    from .windows_containment import remove_reparse_points
    remove_reparse_points(runtime)
    shutil.rmtree(runtime, onerror=_delete_readonly)
    try:
        runtime.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError(
            f"Cannot verify removed execution runtime: {runtime}") from exc
    else:
        raise ValueError(
            f"Execution runtime directory changed during cleanup: {runtime}")
    current_parent = _directory_info(runtime.parent, "Execution runtime parent")
    if not _same_identity(parent_info, current_parent):
        raise ValueError(
            f"Execution runtime parent changed during cleanup: {runtime.parent}")


def remove_test_snapshot(workspace: str | Path) -> bool:
    source = Path(workspace).expanduser().resolve(strict=True)
    runtime = _runtime_path(source)
    marker = _marker_path(runtime)
    parent_info = _directory_info(runtime.parent, "Execution runtime parent")
    runtime_info = _directory_info(
        runtime, "Execution runtime directory", allow_missing=True)
    marker_info = _regular_file_info(
        marker, "Execution ownership marker", allow_missing=True)
    if runtime_info is None:
        if marker_info is not None:
            marker_identity = _hold_snapshot_identity(
                marker, "Execution ownership marker", directory=False,
                expected=marker_info)
            try:
                _validate_marker(runtime, source, marker_identity)
                _remove_snapshot_file(
                    marker, marker_identity, parent_info,
                    "Execution ownership marker")
            finally:
                marker_identity.close()
        return False
    if marker_info is None:
        raise ValueError(
            f"Refusing to remove unowned execution directory: {runtime}")
    runtime_identity = _hold_snapshot_identity(
        runtime, "Execution runtime directory", directory=True,
        expected=runtime_info)
    marker_identity = _hold_snapshot_identity(
        marker, "Execution ownership marker", directory=False,
        expected=marker_info)
    try:
        _validate_marker(runtime, source, marker_identity)
        _check_snapshot_identity(
            runtime, runtime_identity, parent_info,
            "Execution runtime directory", directory=True)
        _check_snapshot_identity(
            marker, marker_identity, parent_info,
            "Execution ownership marker", directory=False)
        _remove_snapshot_directory(runtime, runtime_identity, parent_info)
        _remove_snapshot_file(
            marker, marker_identity, parent_info,
            "Execution ownership marker")
    finally:
        marker_identity.close()
        runtime_identity.close()
    return True


def _ignore_runtime_metadata(_directory, names):
    ignored = set()
    if ".forgeos" in names:
        ignored.add(".forgeos")
    if RUNTIME_DIR in names:
        ignored.add(RUNTIME_DIR)
    # Casefold: .ENV.local must not be copied into a snapshot on case-insensitive volumes.
    ignored.update(name for name in names if name.casefold().startswith(".env"))
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
    _write_snapshot_marker(marker, {
        "version": VERSION,
        "workspace": str(source),
        "runtime": str(runtime),
    })
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


def _refuse_links(root: Path) -> None:
    """Refuse a snapshot holding links: a recursive icacls run walks into link targets."""
    from .windows_containment import is_reparse_point

    def fail(exc):
        raise exc

    for dirpath, dirnames, filenames in os.walk(root, onerror=fail):
        for name in dirnames + filenames:
            if is_reparse_point(Path(dirpath, name)):
                raise ValueError("Windows test snapshot must not contain links")


def create_windows_low_integrity_snapshot(workspace: str | Path) -> TestRuntime:
    """Create and Low-label a disposable Windows execution snapshot."""
    if os.name != "nt":
        raise OSError("Windows Low-integrity snapshots require Windows")
    runtime = create_test_snapshot(workspace)
    from .windows_containment import prepare_low_integrity_workspace
    try:
        _refuse_links(runtime.path)
        prepare_low_integrity_workspace(runtime.path)
        return runtime
    except BaseException:
        try:
            remove_test_snapshot(workspace)
        except Exception:
            pass
        raise
