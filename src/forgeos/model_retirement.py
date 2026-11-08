"""Reversible GGUF retirement using the existing verified-download identity and lock."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import uuid

from .gguf_download import (
    MAX_MODEL_BYTES, _digest, _exclusive_download_lock, _gguf_header,
    _reject_existing_link, _safe_name, _safe_storage_root, _write_json_atomic,
)
from .huggingface_catalog import REPO_ID
from .model_inventory import _manifest_model_file, verify_download

RETIRE_DIR = ".forgeos-retired"
TOKEN = re.compile(r"[a-f0-9]{32}\Z")
REVISION = re.compile(r"[a-f0-9]{40}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
FIELDS = frozenset({"repo", "revision", "file", "bytes", "sha256"})


def _move(source: Path, target: Path) -> None:
    os.replace(source, target)


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
            or type(info["bytes"]) is not int or not 16 <= info["bytes"] <= MAX_MODEL_BYTES:
        raise ValueError("Unsafe retired model identity")
    model = root.joinpath(*info["repo"].split("/"), info["revision"],
                          *_safe_name(info["file"]))
    if data["path"] != model.relative_to(root).as_posix():
        raise ValueError("Retirement journal path disagrees with identity")
    _reject_existing_link(model, root)
    return model, info


def _validate_bytes(path: Path, root: Path, info: dict) -> None:
    _reject_existing_link(path, root)
    if not path.is_file():
        raise RuntimeError("Retired GGUF bytes missing")
    before = path.stat()
    if before.st_size != info["bytes"] or _digest(path) != info["sha256"]:
        raise RuntimeError("Retired GGUF size or SHA-256 mismatch")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino):
        raise RuntimeError("GGUF changed during verification")
    if not _gguf_header(path):
        raise RuntimeError("Retired file lacks GGUF header")


def _validate_manifest(path: Path, root: Path, info: dict) -> None:
    _reject_existing_link(path, root)
    if not path.is_file() or path.stat().st_size > 4096:
        raise RuntimeError("Retired model manifest missing or oversized")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("Invalid retired GGUF manifest") from exc
    if data != info:
        raise RuntimeError("Retired GGUF manifest identity mismatch")


def retire_download(path: str | Path, *,
                    directory: str | Path | None = None) -> dict:
    """Fully reverify then move one model and manifest to restorable storage."""
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
        verify_download(model, directory=root)
        token = uuid.uuid4().hex
        entry = _store(root, create=True) / token
        entry.mkdir(mode=0o700, exist_ok=False)
        record = {"version": 1, "path": model.relative_to(root).as_posix(),
                  "identity": identity}
        try:
            _write_json_atomic(entry / "journal.json", record)
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
    with os.scandir(folder) as entries:
        for item in entries:
            if not TOKEN.fullmatch(item.name) or item.is_symlink() \
                    or not item.is_dir(follow_symlinks=False):
                continue
            if len(output) >= 200:
                raise RuntimeError("Retired model inventory limit exceeded")
            entry = Path(item.path)
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


def restore_download(identifier: str, *,
                     directory: str | Path | None = None) -> dict:
    """Restore by journal token; fail closed if any destination is occupied."""
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
        if partial.exists() or download_journal.exists():
            raise RuntimeError("Cannot restore over unfinished GGUF download")
        stored_weight, stored_manifest = saved_weight.exists(), saved_manifest.exists()
        original_weight, original_manifest = model.exists(), manifest.exists()
        if stored_weight and original_weight or stored_manifest and original_manifest:
            raise RuntimeError("Restore refuses to overwrite existing files")
        if not (stored_weight or original_weight) or not (stored_manifest or original_manifest):
            raise RuntimeError("Retirement bytes or manifest missing")
        _validate_bytes(saved_weight if stored_weight else model, root, info)
        _validate_manifest(saved_manifest if stored_manifest else manifest, root, info)
        if stored_weight:
            _move(saved_weight, model)
        if stored_manifest:
            _move(saved_manifest, manifest)
        verify_download(model, directory=root)
        (entry / "journal.json").unlink()
        entry.rmdir()
    return {"status": "restored", "id": identifier, "path": str(model),
            "bytes": info["bytes"], "sha256": info["sha256"]}
