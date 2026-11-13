"""Explicit no-copy registration of a user-selected local GGUF."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

from .gguf_download import (
    CHUNK_BYTES, MAX_MODEL_BYTES, _exclusive_download_lock,
    _safe_storage_root,
)

IMPORT_FORMAT = "forgeos-local-import-v1"
MIN_MODEL_BYTES = 16
MAX_IMPORTED_MODELS = 200
MAX_IMPORT_INDEX_BYTES = 65536
IMPORT_INDEX = ".forgeos-imports.json"


def _register_import(path: Path, sha256: str, directory=None) -> None:
    root = _safe_storage_root(directory)
    index = root / IMPORT_INDEX
    lock_path = root / (IMPORT_INDEX + ".lock")
    if index.is_symlink() or lock_path.is_symlink():
        raise ValueError("Refusing symbolic-link local import inventory")
    with _exclusive_download_lock(lock_path):
        if index.is_symlink() or lock_path.is_symlink():
            raise ValueError("Local import inventory changed into a symbolic link")
        entries = []
        if os.path.lexists(index):
            if not index.is_file() or index.stat().st_size > MAX_IMPORT_INDEX_BYTES:
                raise ValueError("Local import inventory is not a bounded regular file")
            try:
                data = json.loads(index.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise ValueError("Local import inventory is unreadable") from exc
            if (not isinstance(data, dict) or set(data) != {"version", "imports"}
                    or type(data["version"]) is not int or data["version"] != 1
                    or not isinstance(data["imports"], list)
                    or len(data["imports"]) > MAX_IMPORTED_MODELS):
                raise ValueError("Local import inventory has an invalid shape")
            entries = data["imports"]
        normalized = []
        seen_paths = set()
        for item in entries:
            if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
                    or not isinstance(item["path"], str)
                    or len(item["path"]) > 4096
                    or not isinstance(item["sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])):
                raise ValueError("Local import inventory contains an invalid entry")
            if not Path(item["path"]).is_absolute() or item["path"] in seen_paths:
                raise ValueError("Local import inventory has a relative or duplicate path")
            seen_paths.add(item["path"])
            normalized.append(item)
        record = {"path": str(path), "sha256": sha256}
        normalized = [item for item in normalized if item["path"] != record["path"]]
        if len(normalized) >= MAX_IMPORTED_MODELS:
            raise ValueError("Local import inventory limit reached")
        normalized.append(record)
        payload = {"version": 1, "imports": normalized}
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".forgeos-import-index-", suffix=".tmp", dir=root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, index)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)


def import_local_gguf(path: str | Path, *,
                       directory: str | Path | None = None) -> dict:
    """Hash a local GGUF and create an adjacent identity sidecar, without copying.

    The source/revision/license are unknown unless a separate trusted source
    review establishes them. The adjacent sidecar is published exclusively.
    """
    target = Path(path).expanduser().absolute()
    if target.suffix.lower() != ".gguf":
        raise ValueError("Choose a .gguf file")
    if any(item.is_symlink() for item in (target, *target.parents)):
        raise ValueError("Refusing a symbolic link in the selected model path")
    target = target.resolve(strict=True)
    manifest = target.with_name(target.name + ".forgeos.json")
    lock_path = target.with_name(target.name + ".lock")
    if manifest.is_symlink() or lock_path.is_symlink():
        raise ValueError("Refusing symbolic-link model metadata or lock files")

    published = False
    with _exclusive_download_lock(lock_path):
        if os.path.lexists(manifest):
            raise FileExistsError(
                "A model manifest already exists; preserve it and use its existing model workflow")
        before = target.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("Imported GGUF must be an ordinary single-link file")
        if not MIN_MODEL_BYTES <= before.st_size <= MAX_MODEL_BYTES:
            raise ValueError("Imported GGUF size is outside the supported 16-byte to 16-GiB range")

        digest = hashlib.sha256()
        with target.open("rb") as source:
            if source.read(4) != b"GGUF":
                raise ValueError("Selected file does not have a GGUF header")
            source.seek(0)
            while True:
                block = source.read(CHUNK_BYTES)
                if not block:
                    break
                digest.update(block)
        after = target.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_nlink) != (
                after.st_size, after.st_mtime_ns, after.st_ino, after.st_nlink):
            raise RuntimeError("GGUF changed while its identity was being recorded")

        record = {
            "format": IMPORT_FORMAT,
            "source_path": str(target),
            "file": target.name,
            "bytes": before.st_size,
            "sha256": digest.hexdigest(),
            "provenance": "unknown",
        }
        temporary = manifest.with_name(
            manifest.name + ".tmp-" + uuid.uuid4().hex)
        try:
            with temporary.open("x", encoding="utf-8") as output:
                json.dump(record, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            if os.name == "nt":
                # Windows rename is exclusive; never replace a sidecar another
                # process may have published while hashing the model.
                os.rename(temporary, manifest)
            else:
                os.link(temporary, manifest)
                temporary.unlink()
            published = True
        finally:
            temporary.unlink(missing_ok=True)
    try:
        _register_import(target, record["sha256"], directory)
    except BaseException:
        # The sidecar was created by this call; roll it back if canonical
        # inventory publication failed. Never remove a pre-existing sidecar.
        if published and manifest.is_file() and not manifest.is_symlink():
            try:
                current = json.loads(manifest.read_text(encoding="utf-8"))
                if current == record:
                    manifest.unlink()
            except (OSError, UnicodeError, ValueError):
                pass
        raise
    return {"status": "imported", "path": str(target), **record}
