"""Recoverable, journaled, best-effort multi-file edit transactions.

Crash recovery requires an explicit call to recover_edits(workspace). A
filesystem transaction is not truly atomic across multiple files.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


class TransactionError(RuntimeError):
    pass


# Bounds apply to the existing file being backed up, not the output preview.
# A large existing file must never be read fully into ForgeOS memory.
MAX_BACKUP_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024
COPY_CHUNK_BYTES = 64 * 1024
_WINDOWS_REPARSE_POINT = 0x0400


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sync_dir(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _durable_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if fd is not None:
            os.close(fd)


def _lstat(path: Path, description: str):
    """Read one internal journal path without following links."""
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        raise TransactionError(f"{description} is missing: {path}") from exc
    except OSError as exc:
        raise TransactionError(f"Cannot inspect {description}: {path}") from exc


def _is_reparse_point(info) -> bool:
    """Detect POSIX links and Windows reparse points from one lstat result."""
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)


def _is_link_or_reparse(path: Path, info) -> bool:
    """Include Windows link presentation when lstat mode bits are generic."""
    if _is_reparse_point(info):
        return True
    try:
        return path.is_symlink()
    except OSError:
        return True


def _same_file(before, after, *, require_unlinked: bool = False) -> bool:
    same = all(getattr(before, field, None) == getattr(after, field, None)
               for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns"))
    return same and (not require_unlinked
                     or getattr(after, "st_nlink", 1) == 1)


def _same_identity(before, after) -> bool:
    """Compare the stable identity of one filesystem object."""
    return all(getattr(before, field, None) == getattr(after, field, None)
               for field in ("st_dev", "st_ino"))


def _regular_file_info(path: Path, description: str, *,
                       allow_missing: bool = False,
                       require_unlinked: bool = False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise TransactionError(f"{description} is missing: {path}")
    except OSError as exc:
        raise TransactionError(f"Cannot inspect {description}: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or _is_link_or_reparse(path, info):
        raise TransactionError(f"{description} must be a regular file: {path}")
    if require_unlinked and getattr(info, "st_nlink", 1) != 1:
        raise TransactionError(
            f"{description} must be an unlinked regular file: {path}")
    return info


def _directory_info(path: Path, description: str, *, allow_missing: bool = False):
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        if allow_missing:
            return None
        raise TransactionError(f"{description} is missing: {path}") from exc
    except OSError as exc:
        raise TransactionError(f"Cannot inspect {description}: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or _is_link_or_reparse(path, info):
        raise TransactionError(f"{description} must be a real directory: {path}")
    return info


def _publication_directories(root: Path, target: Path):
    """Create and snapshot the real directory chain used for publication."""
    try:
        relative_parent = target.parent.relative_to(root)
    except ValueError as exc:
        raise TransactionError(
            f"Edit target is outside the workspace: {target}") from exc
    current = root
    directories = [(current, _directory_info(current, "Workspace root"))]
    for part in relative_parent.parts:
        current = current / part
        info = _directory_info(
            current, "Edit target directory", allow_missing=True)
        if info is None:
            try:
                current.mkdir(mode=0o777)
            except FileExistsError:
                pass
            info = _directory_info(current, "Edit target directory")
            _sync_dir(current.parent)
        directories.append((current, info))
    return directories


def _verify_publication_directories(directories) -> None:
    for path, before in directories:
        after = _directory_info(path, "Edit target directory")
        if not _same_identity(before, after):
            raise TransactionError(
                f"Edit target directory changed during publication: {path}")


def _publish_staged(root: Path, target: Path, staged: Path, *,
                    expected_before, expected_after: str) -> None:
    """Publish one staged file only across a validated directory boundary."""
    directories = _publication_directories(root, target)
    _verify_publication_directories(directories)
    current_target = _regular_file_info(
        target, "Edit target", allow_missing=True)
    if expected_before is None:
        if current_target is not None:
            raise TransactionError(
                f"Edit target appeared during publication: {target}")
    elif (current_target is None
          or not _same_file(expected_before, current_target)):
        raise TransactionError(
            f"Edit target changed during publication: {target}")
    staged_info = _regular_file_info(
        staged, "Staged replacement", require_unlinked=True)
    if (_hash_file(
            staged, expected=staged_info,
            require_unlinked=True,
            description="Staged replacement") != expected_after):
        raise TransactionError(f"Staged replacement changed: {staged}")
    _verify_publication_directories(directories)
    os.replace(staged, target)
    _verify_publication_directories(directories)
    published_info = _regular_file_info(
        target, "Published edit target", require_unlinked=True)
    if (_hash_file(
            target, expected=published_info,
            require_unlinked=True,
            description="Published edit target") != expected_after):
        raise TransactionError(f"Published edit target changed: {target}")
    _verify_publication_directories(directories)
    _sync_dir(target.parent)


def _open_bound_file(path: Path, before, description: str, *,
                     require_unlinked: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TransactionError(
            f"{description} could not be opened safely: {path}") from exc
    try:
        current = path.lstat()
        if (_is_link_or_reparse(path, current)
                or not _same_file(before, current,
                                  require_unlinked=require_unlinked)):
            raise TransactionError(
                f"{description} changed or became a link: {path}")
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode)
                or _is_reparse_point(opened)
                or not _same_file(before, opened,
                                  require_unlinked=require_unlinked)):
            raise TransactionError(
                f"{description} changed or became a link: {path}")
    except TransactionError:
        os.close(descriptor)
        raise
    except OSError as exc:
        os.close(descriptor)
        raise TransactionError(
            f"Cannot inspect opened {description}: {path}") from exc
    return descriptor


class _PrivateStagingIdentity:
    """Keep a transaction rollback temporary identity stable through cleanup."""

    def __init__(self, info, descriptor=None):
        self.info = info
        self.descriptor = descriptor

    def matches(self, current) -> bool:
        if self.descriptor is not None:
            try:
                info = os.fstat(self.descriptor)
            except OSError:
                return False
        else:
            info = self.info
        return _same_identity(info, current)

    def close(self) -> None:
        if self.descriptor is None:
            return
        descriptor = self.descriptor
        self.descriptor = None
        try:
            os.close(descriptor)
        except OSError:
            pass


def _hold_private_staging(path: Path, description: str):
    """Capture a rollback temporary and pin its POSIX file object."""
    info = _regular_file_info(path, description, require_unlinked=True)
    if os.name == "nt":
        # Windows cannot keep this handle open because os.replace may reject
        # the rename while the handle is live; its file identity is stable.
        return _PrivateStagingIdentity(info)
    descriptor = _open_bound_file(
        path, info, description, require_unlinked=True)
    return _PrivateStagingIdentity(info, descriptor)


def _cleanup_private_staging(path: Path, staging_identity,
                             parent_info, description: str) -> None:
    """Remove only an unchanged rollback temporary and real parent."""
    if staging_identity is None:
        return
    try:
        current = _regular_file_info(path, description)
        current_parent = _directory_info(path.parent, "Edit target directory")
        if (not staging_identity.matches(current)
                or not _same_identity(parent_info, current_parent)):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    except TransactionError:
        return
    finally:
        staging_identity.close()


def _hash_file(path: Path, *, expected=None,
               require_unlinked: bool = False,
               description: str = "Transaction file") -> str:
    """Hash bounded file contents through an identity-checked descriptor."""
    before = (expected if expected is not None else
              _regular_file_info(path, description,
                                 require_unlinked=require_unlinked))
    if before.st_size > MAX_BACKUP_BYTES:
        raise TransactionError(f"File exceeds transaction backup limit: {path}")
    descriptor = _open_bound_file(
        path, before, description, require_unlinked=require_unlinked)
    value = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            while True:
                chunk = stream.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BACKUP_BYTES:
                    raise TransactionError(
                        f"File exceeds transaction backup limit: {path}")
                value.update(chunk)
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError(
            f"{description} could not be hashed safely: {path}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise TransactionError(
            f"{description} changed during hashing: {path}") from exc
    if (_is_link_or_reparse(path, after) or not stat.S_ISREG(after.st_mode)
            or not _same_file(before, after,
                              require_unlinked=require_unlinked)):
        raise TransactionError(f"{description} changed during hashing: {path}")
    return value.hexdigest()


def _copy_bounded(source: Path, destination: Path, *, mode: int = 0o600,
                  expected=None, require_unlinked: bool = False,
                  description: str = "Transaction source") -> str:
    """Create a durable, streamed identity-checked copy."""
    before = (expected if expected is not None else
              _regular_file_info(source, description,
                                 require_unlinked=require_unlinked))
    if before.st_size > MAX_BACKUP_BYTES:
        raise TransactionError(
            f"File exceeds transaction backup limit: {source}")
    source_fd = _open_bound_file(
        source, before, description, require_unlinked=require_unlinked)
    destination_fd = None
    try:
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_BINARY", 0))
        destination_fd = os.open(destination, flags, mode)
        value = hashlib.sha256()
        size = 0
        with os.fdopen(source_fd, "rb") as read:
            source_fd = None
            with os.fdopen(destination_fd, "wb") as write:
                destination_fd = None
                while True:
                    chunk = read.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_BACKUP_BYTES:
                        raise TransactionError(
                            f"File exceeds transaction backup limit: {source}")
                    write.write(chunk)
                    value.update(chunk)
                write.flush()
                os.fsync(write.fileno())
    except TransactionError:
        raise
    except OSError as exc:
        raise TransactionError(
            f"{description} could not be copied safely: {source}") from exc
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if destination_fd is not None:
            os.close(destination_fd)
    try:
        after = source.lstat()
    except OSError as exc:
        raise TransactionError(
            f"{description} changed during copying: {source}") from exc
    if (_is_link_or_reparse(source, after) or not stat.S_ISREG(after.st_mode)
            or not _same_file(before, after,
                              require_unlinked=require_unlinked)):
        raise TransactionError(f"{description} changed during copying: {source}")
    return value.hexdigest()


def _read_bounded(path: Path, *, max_bytes: int, description: str,
                  expected=None, require_unlinked: bool = False) -> bytes:
    """Read a small file through the same identity-checked descriptor path."""
    before = (expected if expected is not None else
              _regular_file_info(path, description,
                                 require_unlinked=require_unlinked))
    if before.st_size > max_bytes:
        raise TransactionError(f"{description} exceeds safety limit")
    descriptor = _open_bound_file(
        path, before, description, require_unlinked=require_unlinked)
    try:
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            content = stream.read(max_bytes + 1)
    except OSError as exc:
        raise TransactionError(
            f"{description} could not be read safely: {path}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise TransactionError(
            f"{description} changed during reading: {path}") from exc
    if (len(content) > max_bytes or _is_link_or_reparse(path, after)
            or not stat.S_ISREG(after.st_mode)
            or not _same_file(before, after,
                              require_unlinked=require_unlinked)):
        if len(content) > max_bytes:
            raise TransactionError(f"{description} exceeds safety limit")
        raise TransactionError(f"{description} changed during reading: {path}")
    return content


def _restrict_permissions(path: Path, mode: int, description: str) -> None:
    """Make internal POSIX metadata private without changing Windows ACLs."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except (NotImplementedError, TypeError) as exc:
        raise TransactionError(
            f"Cannot secure {description} permissions: {path}") from exc
    except OSError as exc:
        raise TransactionError(
            f"Cannot secure {description} permissions: {path}") from exc


def _require_journal_directory(path: Path, description: str):
    info = _lstat(path, description)
    if not stat.S_ISDIR(info.st_mode) or _is_link_or_reparse(path, info):
        raise TransactionError(
            f"{description} must be a real directory: {path}")
    _restrict_permissions(path, 0o700, description)
    return info


def _require_journal_file(path: Path, description: str):
    info = _lstat(path, description)
    if (not stat.S_ISREG(info.st_mode) or _is_link_or_reparse(path, info)
            or getattr(info, "st_nlink", 1) != 1):
        raise TransactionError(
            f"{description} must be an unlinked regular file: {path}")
    _restrict_permissions(path, 0o600, description)
    return info


def _journal_parent(root: Path, *, create: bool) -> Path | None:
    """Return a real internal directory, never a symlink or reparse point."""
    parent = root / ".forgeos"
    try:
        parent.lstat()
    except FileNotFoundError:
        if not create:
            return None
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            # A concurrent creator wins only after the resulting path passes
            # the same identity check below.
            pass
        _require_journal_directory(parent, "Transaction metadata directory")
        _sync_dir(root)
        return parent
    except OSError as exc:
        raise TransactionError(
            f"Cannot inspect transaction metadata directory: {parent}") from exc
    _require_journal_directory(parent, "Transaction metadata directory")
    return parent


def _journal_cleanup_names(entry_count: int) -> set[str]:
    """Return the bounded set of names a transaction can create."""
    return ({"manifest.json", "committed.json"}
            | {f"{i}.bak" for i in range(entry_count)}
            | {f"{i}.new" for i in range(entry_count)})


def _journal_snapshot(txn: Path, *, expected_names: set[str]):
    """Validate the bounded flat journal layout without changing it."""
    parent_info = _require_journal_directory(
        txn.parent, "Transaction metadata directory")
    txn_info = _require_journal_directory(txn, "Pending edit journal")
    children = []
    with os.scandir(txn) as entries:
        for entry in entries:
            child = txn / entry.name
            if entry.name not in expected_names:
                raise TransactionError(
                    f"Unexpected transaction journal file: {child}")
            info = _lstat(child, "Transaction journal file")
            if (not stat.S_ISREG(info.st_mode)
                    or _is_link_or_reparse(child, info)):
                raise TransactionError(
                    f"Transaction journal file must be an unlinked regular file: {child}")
            children.append((child, info))

    current_txn = _require_journal_directory(txn, "Pending edit journal")
    if not _same_identity(txn_info, current_txn):
        raise TransactionError(
            f"Pending edit journal changed during cleanup: {txn}")
    current_parent = _require_journal_directory(
        txn.parent, "Transaction metadata directory")
    if not _same_identity(parent_info, current_parent):
        raise TransactionError(
            f"Transaction metadata directory changed during cleanup: {txn.parent}")
    return parent_info, txn_info, children


def _remove_journal(txn: Path, *, expected_names: set[str]) -> None:
    """Remove only known regular journal files and persist their removal.

    Journal layout is flat and bounded. Refuse unexpected, linked or directory
    children instead of recursively traversing them, preserving the journal
    when its contents are not the files ForgeOS created for this transaction.
    """
    parent_info, txn_info, children = _journal_snapshot(
        txn, expected_names=expected_names)
    for child, before in children:
        current_txn = _require_journal_directory(txn, "Pending edit journal")
        if not _same_identity(txn_info, current_txn):
            raise TransactionError(
                f"Pending edit journal changed during cleanup: {txn}")
        current_parent = _require_journal_directory(
            txn.parent, "Transaction metadata directory")
        if not _same_identity(parent_info, current_parent):
            raise TransactionError(
                f"Transaction metadata directory changed during cleanup: {txn.parent}")
        current = _lstat(child, "Transaction journal file")
        if (not stat.S_ISREG(current.st_mode)
                or _is_link_or_reparse(child, current)
                or not _same_file(before, current, require_unlinked=True)):
            raise TransactionError(
                f"Transaction journal file changed during cleanup: {child}")
        child.unlink()

    current_parent = _require_journal_directory(
        txn.parent, "Transaction metadata directory")
    if not _same_identity(parent_info, current_parent):
        raise TransactionError(
            f"Transaction metadata directory changed during cleanup: {txn.parent}")
    txn.rmdir()
    _sync_dir(txn.parent)


def _safe_target(root: Path, name: str) -> Path:
    # Import lazily to reuse the engine's strict path rules.
    from .engine import _validate_path
    return _validate_path(root, name)


def recover_edits(root: Path) -> bool:
    """Roll back interrupted edits without loading large files into RAM.

    Refuses to overwrite a target changed by a third party after the crash.
    Invalid/oversized journals and backups remain intact for inspection.
    """
    root = Path(root).resolve(strict=True)
    parent = _journal_parent(root, create=False)
    if parent is None:
        return False
    txn = parent / "pending-edit"
    try:
        txn.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TransactionError(f"Cannot inspect pending edit journal: {txn}") from exc
    _require_journal_directory(txn, "Pending edit journal")
    manifest_path = txn / "manifest.json"
    try:
        manifest_info = _require_journal_file(
            manifest_path, "Transaction manifest")
    except TransactionError as exc:
        if "is missing" in str(exc):
            raise TransactionError(f"Transaction manifest missing: {txn}") from exc
        raise
    raw = _read_bounded(
        manifest_path,
        max_bytes=MAX_MANIFEST_BYTES,
        description="Transaction manifest",
        expected=manifest_info,
        require_unlinked=True)
    try:
        data = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise TransactionError("Invalid transaction manifest JSON") from exc
    if (not isinstance(data, dict) or data.get("version") != 1
            or data.get("state") not in ("pending", "committed")):
        raise TransactionError("Invalid edit journal")
    entries = data.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
        raise TransactionError("Invalid journal entries")
    cleanup_names = _journal_cleanup_names(len(entries))
    _journal_snapshot(txn, expected_names=cleanup_names)

    # Inspect every entry before making any edit, including cleanup after
    # commit. A malformed or duplicate path must not delete the journal.
    seen = set()
    planned = []
    for i, item in enumerate(entries):
        if not isinstance(item, dict) or set(item) != {"path", "before", "after"}:
            raise TransactionError("Malformed journal entry")
        target = _safe_target(root, item["path"])
        name = str(target).casefold()
        if name in seen:
            raise TransactionError("Duplicate edit journal target")
        seen.add(name)
        before, after = item["before"], item["after"]
        def valid_hash(value):
            return (isinstance(value, str) and len(value) == 64
                    and all(c in "0123456789abcdef" for c in value))
        if before is not None and not valid_hash(before):
            raise TransactionError("Invalid backup hash")
        if not valid_hash(after):
            raise TransactionError("Invalid staged hash")
        if data["state"] == "committed":
            target_info = _regular_file_info(
                target, "Committed edit target", allow_missing=True)
            current_hash = (_hash_file(
                target,
                expected=target_info,
                description="Committed edit target")
                            if target_info is not None else None)
            if current_hash != after:
                raise TransactionError(
                    f"Committed edit target changed or is missing: {target}")
            continue
        backup = txn / f"{i}.bak"
        backup_info = None
        if before is not None:
            try:
                backup_info = _require_journal_file(
                    backup, "Transaction backup")
            except TransactionError as exc:
                raise TransactionError("Backup missing or damaged") from exc
            if (_hash_file(
                    backup,
                    expected=backup_info,
                    require_unlinked=True,
                    description="Transaction backup") != before):
                raise TransactionError("Backup missing or damaged")
        target_info = _regular_file_info(
            target, "Edit target", allow_missing=True)
        current_hash = (_hash_file(
            target, expected=target_info, description="Edit target")
                        if target_info is not None else None)
        if current_hash is not None and current_hash not in {before, after}:
            raise TransactionError(f"Conflicting external edit: {target}")
        if current_hash is None and before is not None:
            raise TransactionError(f"Original file unexpectedly removed: {target}")
        planned.append((target, target_info,
                        backup if before is not None else None,
                        current_hash, before))
    if data["state"] == "committed":
        _remove_journal(txn, expected_names=cleanup_names)
        return True

    for target, target_info, backup, current_hash, before in planned:
        if backup is None:
            current_info = _regular_file_info(
                target, "Edit target", allow_missing=True)
            if target_info is None and current_info is not None:
                raise TransactionError(f"Edit target changed during recovery: {target}")
            if (target_info is not None and
                    (current_info is None
                     or not _same_file(target_info, current_info))):
                raise TransactionError(f"Edit target changed during recovery: {target}")
            if current_info is not None:
                target.unlink()
        elif current_hash != before:
            current_info = _regular_file_info(
                target, "Edit target", allow_missing=True)
            if (current_info is None
                    or not _same_file(target_info, current_info)):
                raise TransactionError(f"Edit target changed during recovery: {target}")
            parent_info = _directory_info(
                target.parent, "Edit target directory")
            temp = target.parent / (".forgeos-restore-" + next(tempfile._get_candidate_names()))
            temp_identity = None
            try:
                _copy_bounded(backup, temp, require_unlinked=True)
                temp_identity = _hold_private_staging(
                    temp, "Transaction rollback staging")
                os.replace(temp, target)
                _sync_dir(target.parent)
            finally:
                _cleanup_private_staging(
                    temp, temp_identity, parent_info,
                    "Transaction rollback staging")
    _remove_journal(txn, expected_names=cleanup_names)
    return True


def apply_transaction(root: Path, edits: list[tuple[Path, str]]) -> list[str]:
    """Stage bounded edits, journal streamed backups, then atomically replace.

    No edits occur until every stage file and durable manifest exist. When
    staging fails, the original workspace remains unchanged.
    """
    from .engine import MAX_FILE_BYTES, MAX_FILES

    requested_root = Path(root).absolute()
    root = requested_root.resolve(strict=True)
    parent = _journal_parent(root, create=False)
    if parent is not None:
        txn = parent / "pending-edit"
        try:
            txn.lstat()
        except FileNotFoundError:
            pass
        else:
            _require_journal_directory(txn, "Pending edit journal")
            raise TransactionError(f"Pending transaction requires recovery: {txn}")
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_FILES:
        raise TransactionError("Invalid transaction edit count")
    staged = []
    seen = set()
    # Preflight every target and bounded replacement before creating a journal.
    for target, content in edits:
        target = Path(target).absolute()
        name = target.relative_to(requested_root).as_posix()
        target = _safe_target(root, name)
        key = str(target).casefold()
        if key in seen:
            raise TransactionError("Duplicate transaction target")
        seen.add(key)
        if not isinstance(content, str):
            raise TransactionError("Transaction content must be text")
        after = content.encode("utf-8")
        if len(after) > MAX_FILE_BYTES:
            raise TransactionError("Replacement exceeds edit size limit")
        target_info = _regular_file_info(
            target, "Edit target", allow_missing=True)
        if target_info is not None and target_info.st_size > MAX_BACKUP_BYTES:
            raise TransactionError(f"File exceeds transaction backup limit: {target}")
        staged.append((name, after))

    parent = _journal_parent(root, create=True)
    txn = parent / "pending-edit"
    try:
        txn.lstat()
    except FileNotFoundError:
        pass
    else:
        _require_journal_directory(txn, "Pending edit journal")
        raise TransactionError(f"Pending transaction requires recovery: {txn}")
    txn.mkdir(mode=0o700)
    _require_journal_directory(txn, "Pending edit journal")
    entries = []
    cleanup_names = _journal_cleanup_names(len(staged))
    prepared = False
    try:
        for i, (name, after) in enumerate(staged):
            target = _safe_target(root, name)
            target_info = _regular_file_info(
                target, "Edit target", allow_missing=True)
            before = (_copy_bounded(
                target, txn / f"{i}.bak", expected=target_info,
                description="Edit target")
                      if target_info is not None else None)
            # The private journal directory protects staged source text; the
            # published replacement keeps the historical umask-derived mode.
            _durable_write(txn / f"{i}.new", after, mode=0o666)
            entries.append({"path": name, "before": before, "after": _digest(after)})
        manifest = {"version": 1, "state": "pending", "entries": entries}
        serialized = json.dumps(manifest).encode("utf-8")
        if len(serialized) > MAX_MANIFEST_BYTES:
            raise TransactionError("Transaction manifest exceeds safety limit")
        _durable_write(txn / "manifest.json", serialized)
        _sync_dir(txn)
        _sync_dir(txn.parent)
        prepared = True
        for i, (name, _) in enumerate(staged):
            # Detect external changes since staging, and reject symlink swaps.
            target = _safe_target(root, name)
            target_info = _regular_file_info(
                target, "Edit target", allow_missing=True)
            current = (_hash_file(
                target, expected=target_info, description="Edit target")
                      if target_info is not None else None)
            if current != entries[i]["before"]:
                raise TransactionError(f"Edit target changed during staging: {target}")
            _publish_staged(
                root, target, txn / f"{i}.new",
                expected_before=target_info,
                expected_after=entries[i]["after"])
        committed = txn / "committed.json"
        _durable_write(committed, json.dumps({**manifest, "state": "committed"}).encode())
        os.replace(committed, txn / "manifest.json")
        _sync_dir(txn)
        _remove_journal(txn, expected_names=cleanup_names)
        return [item["path"] for item in entries]
    except Exception:
        if prepared:
            recover_edits(root)
        else:
            _remove_journal(txn, expected_names=cleanup_names)
        raise
