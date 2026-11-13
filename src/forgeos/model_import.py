"""Explicit no-copy registration of a user-selected local GGUF."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from threading import Event
import uuid

from .gguf_download import (
    CHUNK_BYTES, MAX_MODEL_BYTES, _exclusive_download_lock,
    _safe_storage_root,
)

IMPORT_FORMAT = "forgeos-local-import-v1"
MIN_MODEL_BYTES = 16
MAX_IMPORTED_MODELS = 200
MAX_IMPORT_INDEX_BYTES = 1024 * 1024
IMPORT_INDEX = ".forgeos-imports.json"


class ImportCancelled(RuntimeError):
    """The user cancelled hashing before the import was published."""


def _check_import_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ImportCancelled("GGUF import cancelled; no identity was published")


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
                       directory: str | Path | None = None,
                       cancel_event: Event | None = None,
                       progress_callback=None) -> dict:
    """Hash a local GGUF and create an adjacent identity sidecar, without copying.

    The source/revision/license are unknown unless a separate trusted source
    review establishes them. The adjacent sidecar is published exclusively.
    A caller may cooperatively cancel between bounded reads or receive
    progress events on this worker thread.
    """
    if cancel_event is not None and not isinstance(cancel_event, Event):
        raise TypeError("cancel_event must be a threading.Event")
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError("progress_callback must be callable")
    _check_import_cancelled(cancel_event)
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
        existing = None
        if os.path.lexists(manifest):
            if (manifest.is_symlink() or not manifest.is_file()
                    or manifest.stat().st_nlink != 1
                    or manifest.stat().st_size > 4096):
                raise ValueError("Existing model sidecar is not a safe local import")
            try:
                existing = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise FileExistsError(
                    "An existing model sidecar is unreadable; preserve it") from exc
            if (not isinstance(existing, dict)
                    or existing.get("format") != IMPORT_FORMAT
                    or existing.get("source_path") != str(target)
                    or existing.get("file") != target.name
                    or existing.get("provenance") != "unknown"):
                raise FileExistsError(
                    "A non-import model sidecar already exists; preserve it")
        before = target.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("Imported GGUF must be an ordinary single-link file")
        if not MIN_MODEL_BYTES <= before.st_size <= MAX_MODEL_BYTES:
            raise ValueError("Imported GGUF size is outside the supported 16-byte to 16-GiB range")

        digest = hashlib.sha256()
        hashed = 0
        with target.open("rb") as source:
            if source.read(4) != b"GGUF":
                raise ValueError("Selected file does not have a GGUF header")
            source.seek(0)
            while True:
                _check_import_cancelled(cancel_event)
                block = source.read(CHUNK_BYTES)
                _check_import_cancelled(cancel_event)
                if not block:
                    break
                digest.update(block)
                hashed += len(block)
                if progress_callback is not None:
                    progress_callback("verify", hashed, before.st_size)
        _check_import_cancelled(cancel_event)
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
        if existing is not None:
            if existing != record:
                raise RuntimeError(
                    "Imported GGUF changed since its sidecar was recorded")
        else:
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
            # Keep the model lock through rollback so no runtime can start in
            # the interval between identity publication and inventory commit.
            if published and manifest.is_file() and not manifest.is_symlink():
                try:
                    current = json.loads(manifest.read_text(encoding="utf-8"))
                    if current == record:
                        manifest.unlink()
                except (OSError, UnicodeError, ValueError):
                    pass
            raise
    return {"status": "imported", "path": str(target), **record}


def unregister_imported_gguf(path: str | Path, *,
                             directory: str | Path | None = None) -> dict:
    """Remove an imported path from active inventory; preserve weights/sidecar."""
    target = Path(path).expanduser().absolute()
    if any(item.is_symlink() for item in (target, *target.parents)):
        raise ValueError("Imported model path contains a symbolic link")
    target = target.resolve(strict=True)
    lock_path = target.with_name(target.name + ".lock")
    if lock_path.is_symlink():
        raise ValueError("Refusing symbolic-link model lifecycle lock")
    root = _safe_storage_root(directory)
    index = root / IMPORT_INDEX
    index_lock = root / (IMPORT_INDEX + ".lock")
    with _exclusive_download_lock(lock_path):
        sidecar = target.with_name(target.name + ".forgeos.json")
        if (sidecar.is_symlink() or not sidecar.is_file()
                or sidecar.stat().st_nlink != 1
                or sidecar.stat().st_size > 4096):
            raise ValueError("Imported model sidecar is missing or unsafe")
        try:
            identity = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError("Imported model sidecar is unreadable") from exc
        if (not isinstance(identity, dict)
                or set(identity) != {"format", "source_path", "file", "bytes",
                                     "sha256", "provenance"}
                or identity.get("format") != IMPORT_FORMAT
                or identity.get("source_path") != str(target)
                or identity.get("file") != target.name
                or identity.get("provenance") != "unknown"
                or type(identity.get("bytes")) is not int
                or not MIN_MODEL_BYTES <= identity["bytes"] <= MAX_MODEL_BYTES
                or not isinstance(identity.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", identity["sha256"])):
            raise ValueError("Imported model sidecar identity is invalid")
        before = target.stat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size != identity["bytes"]):
            raise RuntimeError("Imported GGUF file identity changed")
        digest = hashlib.sha256()
        with target.open("rb") as source:
            if source.read(4) != b"GGUF":
                raise ValueError("Imported model no longer has a GGUF header")
            source.seek(0)
            for block in iter(lambda: source.read(CHUNK_BYTES), b""):
                digest.update(block)
        after = target.stat()
        if digest.hexdigest() != identity["sha256"] or (
                before.st_size, before.st_mtime_ns, before.st_ino, before.st_nlink) != (
                after.st_size, after.st_mtime_ns, after.st_ino, after.st_nlink):
            raise RuntimeError("Imported GGUF changed during verification")

        if index.is_symlink() or index_lock.is_symlink():
            raise ValueError("Refusing symbolic-link local import inventory")
        with _exclusive_download_lock(index_lock):
            if index.is_symlink() or index_lock.is_symlink():
                raise ValueError("Local import inventory changed into a symbolic link")
            if not index.is_file() or index.stat().st_size > MAX_IMPORT_INDEX_BYTES:
                raise ValueError("Local import inventory is missing or oversized")
            try:
                data = json.loads(index.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise ValueError("Local import inventory is unreadable") from exc
            if (not isinstance(data, dict) or set(data) != {"version", "imports"}
                    or type(data["version"]) is not int or data["version"] != 1
                    or not isinstance(data["imports"], list)
                    or len(data["imports"]) > MAX_IMPORTED_MODELS):
                raise ValueError("Local import inventory has an invalid shape")
            retained = [item for item in data["imports"]
                        if not isinstance(item, dict)
                        or item.get("path") != str(target)]
            if len(retained) == len(data["imports"]):
                raise ValueError("Imported model is not registered in ForgeOS")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".forgeos-import-index-", suffix=".tmp", dir=root)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump({"version": 1, "imports": retained}, output,
                              sort_keys=True)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary_name, index)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
    return {"status": "unregistered", "path": str(target),
            "sha256": identity["sha256"], "bytes": identity["bytes"],
            "weights_preserved": True, "sidecar_preserved": True}
