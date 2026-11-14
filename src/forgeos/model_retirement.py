"""Reversible GGUF retirement using the existing verified-download identity and lock."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from threading import Event
import uuid

from .gguf_download import (
    DownloadCancelled, MAX_MODEL_BYTES, _digest, _exclusive_download_lock, _gguf_header,
    _is_reparse_point, _reject_existing_link, _safe_name, _safe_storage_root,
    _write_json_atomic,
)
from .huggingface_catalog import REPO_ID
from .model_inventory import (
    MAX_SCAN_ENTRIES, VerificationCancelled, _manifest_model_file,
    _verify_download_unlocked,
)

RETIRE_DIR = ".forgeos-retired"
TOKEN = re.compile(r"[a-f0-9]{32}\Z")
REVISION = re.compile(r"[a-f0-9]{40}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
FIELDS = frozenset({"repo", "revision", "file", "bytes", "sha256"})


def _sync_directory(directory: Path) -> None:
    """Persist directory-entry mutations when POSIX directory fsync exists.

    Windows does not offer portable directory fsync through stdlib; actual
    power-loss recovery there remains a physical-machine acceptance gap.
    """
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _move(source: Path, target: Path) -> None:
    os.replace(source, target)
    _sync_directory(source.parent)
    if target.parent != source.parent:
        _sync_directory(target.parent)


def _store(root: Path, create: bool = False) -> Path:
    result = root / RETIRE_DIR
    _reject_existing_link(result, root)
    if create:
        result.mkdir(mode=0o700, exist_ok=True)
        _reject_existing_link(result, root)
    return result


def _entry(root: Path, identifier: str) -> Path:
    if not isinstance(identifier, str) or not TOKEN.fullmatch(identifier):
        raise ValueError("Invalid 32-character retirement identifier")
    entry = _store(root) / identifier
    _reject_existing_link(entry, root)
    return entry


def _record(root: Path, entry: Path) -> tuple[Path, dict]:
    journal = entry / "journal.json"
    _reject_existing_link(journal, root)
    if not entry.is_dir() or not journal.is_file() or journal.stat().st_size > 4096:
        raise ValueError("Retirement journal missing or oversized")
    # A journal shared through a hard link could be mutated outside the
    # managed storage tree after validation. Do not trust or delete it.
    if journal.stat().st_nlink != 1:
        raise ValueError("Hard-linked retirement journal is not trusted")
    try:
        data = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Unreadable retirement journal") from exc
    if not isinstance(data, dict) or set(data) != {"version", "path", "identity"} \
            or type(data["version"]) is not int or data["version"] != 1 \
            or not isinstance(data["identity"], dict):
        raise ValueError("Invalid retirement journal")
    info = data["identity"]
    if set(info) != FIELDS or not isinstance(info["repo"], str) \
            or not REPO_ID.fullmatch(info["repo"]) \
            or not isinstance(info["revision"], str) \
            or not REVISION.fullmatch(info["revision"]) \
            or not isinstance(info["sha256"], str) or not SHA.fullmatch(info["sha256"]) \
            or not isinstance(info["file"], str) \
            or type(info["bytes"]) is not int or not 16 <= info["bytes"] <= MAX_MODEL_BYTES:
        raise ValueError("Unsafe retired model identity")
    try:
        safe_parts = _safe_name(info["file"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Unsafe retired model filename") from exc
    model = root.joinpath(*info["repo"].split("/"), info["revision"],
                          *safe_parts)
    if data["path"] != model.relative_to(root).as_posix():
        raise ValueError("Retirement journal path disagrees with identity")
    _reject_existing_link(model, root)
    return model, info


def _single_link(path: Path) -> None:
    """Do not move a model whose bytes are shared via hard links."""
    if not path.is_file() or path.stat().st_nlink != 1:
        raise RuntimeError(
            "GGUF retirement requires an ordinary single-link file; "
            "hard-linked or missing files are not movable")


def _validate_bytes(path: Path, root: Path, info: dict, *,
                    cancel_event=None, progress_callback=None) -> None:
    _reject_existing_link(path, root)
    _single_link(path)
    if not path.is_file():
        raise RuntimeError("Retired GGUF bytes missing")
    before = path.stat()
    if before.st_size != info["bytes"]:
        raise RuntimeError("Retired GGUF size or SHA-256 mismatch")
    try:
        digest = _digest(path, cancel_event=cancel_event,
                         progress_callback=progress_callback)
    except DownloadCancelled:
        raise VerificationCancelled("GGUF verification cancelled") from None
    if digest != info["sha256"]:
        raise RuntimeError("Retired GGUF size or SHA-256 mismatch")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino):
        raise RuntimeError("GGUF changed during verification")
    if not _gguf_header(path):
        raise RuntimeError("Retired file lacks GGUF header")


def _validate_manifest(path: Path, root: Path, info: dict) -> None:
    _reject_existing_link(path, root)
    _single_link(path)
    if not path.is_file() or path.stat().st_size > 4096:
        raise RuntimeError("Retired model manifest missing or oversized")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("Invalid retired GGUF manifest") from exc
    if data != info:
        raise RuntimeError("Retired GGUF manifest identity mismatch")


def _check_options(cancel_event, progress_callback) -> None:
    if cancel_event is not None and not isinstance(cancel_event, Event):
        raise TypeError("cancel_event must be a threading.Event")
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError("progress_callback must be callable")


def _check_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise VerificationCancelled("GGUF verification cancelled")


def _verify_download(path: Path, root: Path, *, cancel_event=None,
                     progress_callback=None) -> dict:
    try:
        return _verify_download_unlocked(
            path, root, cancel_event=cancel_event,
            progress_callback=progress_callback)
    except DownloadCancelled:
        raise VerificationCancelled("GGUF verification cancelled") from None


def retire_download(path: str | Path, *,
                    directory: str | Path | None = None,
                    cancel_event=None, progress_callback=None) -> dict:
    """Fully reverify then move one model and manifest to restorable storage."""
    _check_options(cancel_event, progress_callback)
    root = _safe_storage_root(directory)
    model = Path(path).expanduser().absolute()
    _reject_existing_link(model, root)
    model = model.resolve(strict=False)
    manifest = model.with_name(model.name + ".forgeos.json")
    _reject_existing_link(manifest, root)
    expected, identity = _manifest_model_file(manifest, root)
    if model != expected or set(identity) != FIELDS:
        raise ValueError("Select a canonical downloaded GGUF with matching manifest")
    lock = model.with_name(model.name + ".lock")
    partial = model.with_name(model.name + ".part")
    download_journal = model.with_name(model.name + ".part.json")
    for path_item in (lock, partial, download_journal):
        _reject_existing_link(path_item, root)
    with _exclusive_download_lock(lock):
        if partial.exists() or download_journal.exists():
            raise RuntimeError("Unresolved GGUF download journal")
        _single_link(model)
        _single_link(manifest)
        _verify_download(model, root, cancel_event=cancel_event,
                         progress_callback=progress_callback)
        _check_cancelled(cancel_event)
        # Recheck after the multi-gigabyte SHA read before either move.
        _single_link(model)
        _single_link(manifest)
        token = uuid.uuid4().hex
        store = _store(root, create=True)
        entry = store / token
        entry.mkdir(mode=0o700, exist_ok=False)
        _sync_directory(store)
        record = {"version": 1, "path": model.relative_to(root).as_posix(),
                  "identity": identity}
        try:
            _write_json_atomic(entry / "journal.json", record)
            _sync_directory(entry)
            _move(model, entry / "weights.gguf")
            _move(manifest, entry / "manifest.json")
        except BaseException as exc:
            raise RuntimeError(
                "Retirement interrupted; restore using identifier " + token) from exc
    return {"status": "retired", "id": token, "path": str(model),
            "bytes": identity["bytes"], "sha256": identity["sha256"]}


def list_retired(directory: str | Path | None = None) -> list[dict]:
    """Metadata-only list, capped at 200 entries; never rehash large files."""
    root = _safe_storage_root(directory)
    folder = _store(root)
    if not folder.exists():
        return []
    output = []
    examined = 0
    with os.scandir(folder) as entries:
        for item in entries:
            examined += 1
            if examined > MAX_SCAN_ENTRIES:
                raise RuntimeError("Retired model inventory exceeds bounded scan limit")
            if not TOKEN.fullmatch(item.name):
                continue
            entry = Path(item.path)
            if _is_reparse_point(entry):
                if len(output) >= 200:
                    raise RuntimeError("Retired model inventory limit exceeded")
                output.append({"id": item.name,
                               "status": "needs_manual_inspection"})
                continue
            if not item.is_dir(follow_symlinks=False):
                continue
            if len(output) >= 200:
                raise RuntimeError("Retired model inventory limit exceeded")
            try:
                original, info = _record(root, entry)
                weight = entry / "weights.gguf"
                metadata = entry / "manifest.json"
                _reject_existing_link(weight, root)
                _reject_existing_link(metadata, root)
                status = ("retired" if weight.is_file() and metadata.is_file()
                          else "interrupted_retirement_or_restore")
                output.append({"id": item.name, "status": status,
                               "path": str(original), "repo": info["repo"],
                               "file": info["file"], "bytes": info["bytes"]})
            except (OSError, RuntimeError, ValueError):
                output.append({"id": item.name, "status": "needs_manual_inspection"})
    return sorted(output, key=lambda item: item["id"])


def purge_retired_download(identifier: str, *,
                           checkpoint_check,
                           directory: str | Path | None = None,
                           confirm: bool = False,
                           cancel_event=None, progress_callback=None) -> dict:
    """Permanently delete one explicitly confirmed retired model.

    The caller must supply the canonical checkpoint reference check. The
    model lock serializes purge against managed use, download, verification,
    retirement, and restore. The journal is removed last so an interrupted
    purge can be safely retried by the same identifier.
    """
    _check_options(cancel_event, progress_callback)
    if confirm is not True:
        raise ValueError("Explicit confirm=True required to permanently purge GGUF")
    if not callable(checkpoint_check):
        raise TypeError("checkpoint_check must be callable")
    root = _safe_storage_root(directory)
    entry = _entry(root, identifier)
    model, info = _record(root, entry)
    manifest = model.with_name(model.name + ".forgeos.json")
    lock = model.with_name(model.name + ".lock")
    partial = model.with_name(model.name + ".part")
    download_journal = model.with_name(model.name + ".part.json")
    saved_weight = entry / "weights.gguf"
    saved_manifest = entry / "manifest.json"
    for item in (model, manifest, lock, partial, download_journal,
                 saved_weight, saved_manifest):
        _reject_existing_link(item, root)
    with _exclusive_download_lock(lock):
        locked_model, locked_info = _record(root, entry)
        if locked_model != model or locked_info != info:
            raise RuntimeError("Retirement journal changed while waiting for lock")
        for item in (model, manifest, saved_weight, saved_manifest,
                     partial, download_journal):
            _reject_existing_link(item, root)
        if model.exists() or manifest.exists():
            raise RuntimeError("Purge refuses files already restored to the active path")
        if partial.exists() or download_journal.exists():
            raise RuntimeError("Cannot purge over an unfinished GGUF download")
        if checkpoint_check(info["sha256"]) is not False:
            raise RuntimeError("A blocked task checkpoint references this GGUF; purge refused")
        if saved_weight.exists():
            _validate_bytes(saved_weight, root, info,
                            cancel_event=cancel_event,
                            progress_callback=progress_callback)
        if saved_manifest.exists():
            _validate_manifest(saved_manifest, root, info)
        _check_cancelled(cancel_event)
        for item in (saved_weight, saved_manifest):
            _reject_existing_link(item, root)
        if saved_weight.exists():
            _single_link(saved_weight)
            saved_weight.unlink()
            _sync_directory(entry)
        if saved_manifest.exists():
            _single_link(saved_manifest)
            saved_manifest.unlink()
            _sync_directory(entry)
        journal = entry / "journal.json"
        _reject_existing_link(journal, root)
        if journal.exists():
            if journal.stat().st_nlink != 1:
                raise ValueError("Hard-linked retirement journal is not trusted")
            journal.unlink()
            _sync_directory(entry)
        entry.rmdir()
        _sync_directory(_store(root))
    return {"status": "purged", "id": identifier,
            "path": str(model), "bytes": info["bytes"],
            "sha256": info["sha256"]}


def restore_download(identifier: str, *,
                     directory: str | Path | None = None,
                     cancel_event=None, progress_callback=None) -> dict:
    """Restore by journal token; fail closed if any destination is occupied."""
    _check_options(cancel_event, progress_callback)
    root = _safe_storage_root(directory)
    entry = _entry(root, identifier)
    model, info = _record(root, entry)
    manifest = model.with_name(model.name + ".forgeos.json")
    lock = model.with_name(model.name + ".lock")
    partial = model.with_name(model.name + ".part")
    download_journal = model.with_name(model.name + ".part.json")
    saved_weight = entry / "weights.gguf"
    saved_manifest = entry / "manifest.json"
    for item in (manifest, lock, partial, download_journal,
                 saved_weight, saved_manifest):
        _reject_existing_link(item, root)
    model.parent.mkdir(parents=True, exist_ok=True)
    _reject_existing_link(model.parent, root)
    with _exclusive_download_lock(lock):
        # The lock protects our own downloader, but another process can
        # replace a journal or a destination while we waited for it.
        # Re-read the authoritative journal and path under the lock.
        locked_model, locked_info = _record(root, entry)
        if locked_model != model or locked_info != info:
            raise RuntimeError("Retirement journal changed while waiting for lock")
        for item in (model, manifest, saved_weight, saved_manifest,
                     partial, download_journal):
            _reject_existing_link(item, root)
        if partial.exists() or download_journal.exists():
            raise RuntimeError("Cannot restore over unfinished GGUF download")
        stored_weight, stored_manifest = saved_weight.exists(), saved_manifest.exists()
        original_weight, original_manifest = model.exists(), manifest.exists()
        if (stored_weight and original_weight or
                stored_manifest and original_manifest):
            raise RuntimeError("Restore refuses to overwrite existing files")
        if not (stored_weight or original_weight) or not (stored_manifest or original_manifest):
            raise RuntimeError("Retirement bytes or manifest missing")
        _validate_bytes(saved_weight if stored_weight else model, root, info,
                        cancel_event=cancel_event,
                        progress_callback=progress_callback)
        _validate_manifest(saved_manifest if stored_manifest else manifest, root, info)
        _check_cancelled(cancel_event)
        # A stale symlink or newly hard-linked destination must never be
        # accepted even if it appeared during a lengthy SHA-256 check.
        for item in (model, manifest, saved_weight, saved_manifest):
            _reject_existing_link(item, root)
        if (stored_weight and model.exists() or
                stored_manifest and manifest.exists()):
            raise RuntimeError("Restore refuses to overwrite existing files")
        if stored_weight:
            _single_link(saved_weight)
            _move(saved_weight, model)
        if stored_manifest:
            _single_link(saved_manifest)
            _move(saved_manifest, manifest)
        _verify_download(model, root, cancel_event=cancel_event,
                         progress_callback=progress_callback)
        (entry / "journal.json").unlink()
        _sync_directory(entry)
        entry.rmdir()
        _sync_directory(_store(root))
    return {"status": "restored", "id": identifier, "path": str(model),
            "bytes": info["bytes"], "sha256": info["sha256"]}
