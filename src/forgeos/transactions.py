"""Recoverable, journaled, best-effort multi-file edit transactions.

Crash recovery requires an explicit call to recover_edits(workspace). A
filesystem transaction is not truly atomic across multiple files.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
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


def _durable_write(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _hash_file(path: Path) -> str:
    """Hash bounded file contents without materializing them in memory."""
    value = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BACKUP_BYTES:
                raise TransactionError(f"File exceeds transaction backup limit: {path}")
            value.update(chunk)
    return value.hexdigest()


def _copy_bounded(source: Path, destination: Path) -> str:
    """Create a durable, streamed copy, rejecting oversized source data."""
    value = hashlib.sha256()
    size = 0
    with source.open("rb") as read, destination.open("xb") as write:
        while True:
            chunk = read.read(COPY_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BACKUP_BYTES:
                raise TransactionError(f"File exceeds transaction backup limit: {source}")
            write.write(chunk)
            value.update(chunk)
        write.flush()
        os.fsync(write.fileno())
    return value.hexdigest()


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


def _require_journal_directory(path: Path, description: str) -> None:
    info = _lstat(path, description)
    if not stat.S_ISDIR(info.st_mode) or _is_reparse_point(info):
        raise TransactionError(
            f"{description} must be a real directory: {path}")


def _require_journal_file(path: Path, description: str) -> None:
    info = _lstat(path, description)
    if (not stat.S_ISREG(info.st_mode) or _is_reparse_point(info)
            or getattr(info, "st_nlink", 1) != 1):
        raise TransactionError(
            f"{description} must be an unlinked regular file: {path}")


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


def _remove_journal(txn: Path) -> None:
    """Remove only a validated journal directory and persist its removal."""
    _require_journal_directory(txn.parent, "Transaction metadata directory")
    _require_journal_directory(txn, "Pending edit journal")
    shutil.rmtree(txn)
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
        _require_journal_file(manifest_path, "Transaction manifest")
    except TransactionError as exc:
        if "is missing" in str(exc):
            raise TransactionError(f"Transaction manifest missing: {txn}") from exc
        raise
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise TransactionError("Transaction manifest exceeds safety limit")
    with manifest_path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise TransactionError("Transaction manifest exceeds safety limit")
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
            try:
                target_info = target.lstat()
            except FileNotFoundError:
                target_info = None
            except OSError as exc:
                raise TransactionError(
                    f"Cannot inspect committed edit target: {target}") from exc
            if (target_info is not None and
                    (not stat.S_ISREG(target_info.st_mode)
                     or _is_reparse_point(target_info))):
                raise TransactionError(
                    f"Committed edit target is not a regular file: {target}")
            current_hash = (_hash_file(target)
                            if target_info is not None else None)
            if current_hash != after:
                raise TransactionError(
                    f"Committed edit target changed or is missing: {target}")
            continue
        backup = txn / f"{i}.bak"
        if before is not None:
            try:
                _require_journal_file(backup, "Transaction backup")
            except TransactionError as exc:
                raise TransactionError("Backup missing or damaged") from exc
            if _hash_file(backup) != before:
                raise TransactionError("Backup missing or damaged")
        current_hash = _hash_file(target) if target.exists() else None
        if current_hash is not None and current_hash not in {before, after}:
            raise TransactionError(f"Conflicting external edit: {target}")
        if current_hash is None and before is not None:
            raise TransactionError(f"Original file unexpectedly removed: {target}")
        planned.append((target, backup if before is not None else None, current_hash, before))
    if data["state"] == "committed":
        _remove_journal(txn)
        return True

    for target, backup, current_hash, before in planned:
        if backup is None:
            if target.exists():
                target.unlink()
        elif current_hash != before:
            temp = target.parent / (".forgeos-restore-" + next(tempfile._get_candidate_names()))
            try:
                _copy_bounded(backup, temp)
                os.replace(temp, target)
                _sync_dir(target.parent)
            finally:
                temp.unlink(missing_ok=True)
    _remove_journal(txn)
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
        if target.exists() and target.stat().st_size > MAX_BACKUP_BYTES:
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
    txn.mkdir()
    _require_journal_directory(txn, "Pending edit journal")
    entries = []
    prepared = False
    try:
        for i, (name, after) in enumerate(staged):
            target = _safe_target(root, name)
            before = (_copy_bounded(target, txn / f"{i}.bak")
                      if target.exists() else None)
            _durable_write(txn / f"{i}.new", after)
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
            current = _hash_file(target) if target.exists() else None
            if current != entries[i]["before"]:
                raise TransactionError(f"Edit target changed during staging: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(txn / f"{i}.new", target)
            _sync_dir(target.parent)
        committed = txn / "committed.json"
        _durable_write(committed, json.dumps({**manifest, "state": "committed"}).encode())
        os.replace(committed, txn / "manifest.json")
        _sync_dir(txn)
        _remove_journal(txn)
        return [item["path"] for item in entries]
    except Exception:
        if prepared:
            recover_edits(root)
        else:
            _remove_journal(txn)
        raise
