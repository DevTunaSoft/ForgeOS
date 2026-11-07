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
import tempfile


class TransactionError(RuntimeError):
    pass


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


def _journal(root: Path) -> Path:
    return root / ".forgeos" / "pending-edit"


def _safe_target(root: Path, name: str) -> Path:
    # Import lazily to reuse the engine's strict path rules.
    from .engine import _validate_path
    return _validate_path(root, name)


def recover_edits(root: Path) -> bool:
    """Roll back an interrupted edit, or finish cleanup after commit.

    Refuses to overwrite a target changed by a third party after the crash.
    """
    root = Path(root).resolve(strict=True)
    txn = _journal(root)
    if not txn.exists():
        return False
    manifest_path = txn / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise TransactionError(f"Transaction manifest missing: {txn}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("state") not in ("pending", "committed"):
        raise TransactionError("Invalid edit journal")
    entries = data.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
        raise TransactionError("Invalid journal entries")
    if data["state"] == "committed":
        shutil.rmtree(txn)
        return True
    # Verify *all* paths and contents before rolling back any file.
    planned = []
    for i, item in enumerate(entries):
        if not isinstance(item, dict) or set(item) != {"path", "before", "after"}:
            raise TransactionError("Malformed journal entry")
        target = _safe_target(root, item["path"])
        before = item["before"]
        after = item["after"]
        if before is not None and (not isinstance(before, str) or len(before) != 64):
            raise TransactionError("Invalid backup hash")
        if not isinstance(after, str) or len(after) != 64:
            raise TransactionError("Invalid staged hash")
        backup = txn / f"{i}.bak"
        if before is not None:
            if backup.is_symlink() or not backup.is_file() or _digest(backup.read_bytes()) != before:
                raise TransactionError("Backup missing or damaged")
        current = target.read_bytes() if target.exists() else None
        if current is not None and _digest(current) not in {before, after}:
            raise TransactionError(f"Conflicting external edit: {target}")
        if current is None and before is not None:
            raise TransactionError(f"Original file unexpectedly removed: {target}")
        planned.append((target, backup if before is not None else None, current))
    for target, backup, current in planned:
        if backup is None:
            if target.exists():
                target.unlink()
        elif current is None or _digest(current) != _digest(backup.read_bytes()):
            temp = target.parent / (".forgeos-restore-" + next(tempfile._get_candidate_names()))
            try:
                _durable_write(temp, backup.read_bytes())
                os.replace(temp, target)
                _sync_dir(target.parent)
            finally:
                temp.unlink(missing_ok=True)
    shutil.rmtree(txn)
    return True


def apply_transaction(root: Path, edits: list[tuple[Path, str]]) -> list[str]:
    """Stage all files, journal backups, then replace; roll back on failure."""
    # Keep the caller's path spelling: Windows may resolve 8.3 temp paths
    # to a different alias, breaking Path.relative_to on valid targets.
    root = Path(root).absolute()
    txn = _journal(root)
    if txn.exists():
        raise TransactionError(f"Pending transaction requires recovery: {txn}")
    txn.parent.mkdir(parents=True, exist_ok=True)
    txn.mkdir()
    entries = []
    prepared = False
    try:
        for i, (target, content) in enumerate(edits):
            name = target.relative_to(root).as_posix()
            target = _safe_target(root, name)
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise TransactionError(f"Unsafe edit target: {target}")
            before = target.read_bytes() if target.exists() else None
            after = content.encode("utf-8")
            if before is not None:
                _durable_write(txn / f"{i}.bak", before)
            _durable_write(txn / f"{i}.new", after)
            entries.append({"path": name, "before": _digest(before) if before is not None else None,
                            "after": _digest(after)})
        manifest = {"version": 1, "state": "pending", "entries": entries}
        _durable_write(txn / "manifest.json", json.dumps(manifest).encode())
        _sync_dir(txn)
        _sync_dir(txn.parent)
        prepared = True
        for i, (target, _) in enumerate(edits):
            # Detect external changes since staging, and reject symlink swaps.
            target = _safe_target(root, entries[i]["path"])
            current = target.read_bytes() if target.exists() else None
            if (None if current is None else _digest(current)) != entries[i]["before"]:
                raise TransactionError(f"Edit target changed during staging: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(txn / f"{i}.new", target)
            _sync_dir(target.parent)
        committed = txn / "committed.json"
        _durable_write(committed, json.dumps({**manifest, "state": "committed"}).encode())
        os.replace(committed, txn / "manifest.json")
        _sync_dir(txn)
        shutil.rmtree(txn)
        return [item["path"] for item in entries]
    except Exception:
        if prepared:
            recover_edits(root)
        else:
            shutil.rmtree(txn)
        raise
