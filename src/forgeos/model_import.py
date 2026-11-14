"""Explicit no-copy registry references to user-selected local GGUF files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from threading import Event

from .gguf_download import (
    CHUNK_BYTES, DownloadCancelled, MAX_MODEL_BYTES, _digest,
    _exclusive_download_lock, _is_reparse_point,
    _safe_storage_root,
)

IMPORT_FORMAT = "forgeos-local-import-v1"
MIN_MODEL_BYTES = 16
MAX_IMPORTED_MODELS = 200
MAX_IMPORT_INDEX_BYTES = 1024 * 1024
IMPORT_INDEX = ".forgeos-imports.json"
IMPORT_LOCK_PREFIX = ".forgeos-import-lock-"


class ImportCancelled(RuntimeError):
    """The user cancelled hashing before the import was published."""


class ImportUnregisterCancelled(RuntimeError):
    """The user cancelled verification before an import was unregistered."""


class ImportPurgeCancelled(RuntimeError):
    """The user cancelled verification before imported bytes were deleted."""


def _import_lifecycle_lock_path(target: Path, root: Path) -> Path:
    """Place imported-file locks in writable ForgeOS storage, not beside media."""
    lock_id = hashlib.sha256(os.fsencode(str(target))).hexdigest()
    lock = root / (IMPORT_LOCK_PREFIX + lock_id + ".lock")
    if _is_reparse_point(lock):
        raise ValueError("Refusing linked or redirected model lifecycle lock")
    if os.path.lexists(lock) and (not lock.is_file() or lock.stat().st_nlink != 1):
        raise ValueError("Imported model lifecycle lock is unsafe")
    return lock


def registered_import(path: str | Path, *,
                      directory: str | Path | None = None) -> dict | None:
    """Return a bounded registry identity for one path, without touching it."""
    target = Path(path).expanduser().absolute()
    if any(_is_reparse_point(item) for item in (target, *target.parents)):
        raise ValueError("Imported model path contains a link or reparse point")
    target = target.resolve(strict=False)
    root = (Path(directory) if directory is not None else
            Path.home() / ".forgeos" / "models").expanduser().absolute()
    if any(_is_reparse_point(item) for item in (root, *root.parents)):
        raise ValueError("Refusing linked or reparse-point model storage")
    if not root.is_dir():
        return None
    root = root.resolve(strict=True)
    index = root / IMPORT_INDEX
    if _is_reparse_point(index):
        raise ValueError("Local import registry is linked or redirected")
    if not index.exists():
        return None
    if not index.is_file() or index.stat().st_size > MAX_IMPORT_INDEX_BYTES:
        raise ValueError("Local import registry is missing or oversized")
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Local import registry is unreadable") from exc
    if (not isinstance(data, dict) or set(data) != {"version", "imports"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["imports"], list)
            or len(data["imports"]) > MAX_IMPORTED_MODELS):
        raise ValueError("Local import registry has an invalid shape")
    matches = []
    seen = set()
    for entry in data["imports"]:
        if (not isinstance(entry, dict) or set(entry) != {"path", "sha256"}
                or not isinstance(entry["path"], str)
                or len(entry["path"]) > 32768
                or not Path(entry["path"]).is_absolute()
                or not isinstance(entry["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])):
            raise ValueError("Local import registry contains an invalid entry")
        if entry["path"] in seen:
            raise ValueError("Local import registry has duplicate model paths")
        seen.add(entry["path"])
        if entry["path"] == str(target):
            matches.append(entry)
    if not matches:
        return None
    return {"format": IMPORT_FORMAT, "source_path": str(target),
            "file": target.name, "sha256": matches[0]["sha256"],
            "provenance": "unknown"}


def imported_model_lock_path(path: str | Path, *,
                             directory: str | Path | None = None) -> Path:
    """Return the shared store lock for registry references, else adjacent lock."""
    target = Path(path).expanduser().absolute()
    if any(_is_reparse_point(item) for item in (target, *target.parents)):
        raise ValueError("Imported model path contains a link or reparse point")
    target = target.resolve(strict=False)
    sidecar = target.with_name(target.name + ".forgeos.json")
    if _is_reparse_point(sidecar):
        raise ValueError("Linked or redirected GGUF identity metadata is not trusted")
    if sidecar.is_file():
        if sidecar.stat().st_size > 4096:
            return target.with_name(target.name + ".lock")
        try:
            legacy = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return target.with_name(target.name + ".lock")
        if not isinstance(legacy, dict) or legacy.get("format") != IMPORT_FORMAT:
            return target.with_name(target.name + ".lock")
    root = (Path(directory) if directory is not None else
            Path.home() / ".forgeos" / "models").expanduser().absolute()
    if any(_is_reparse_point(item) for item in (root, *root.parents)):
        raise ValueError("Refusing linked or reparse-point model storage")
    if not root.is_dir():
        return target.with_name(target.name + ".lock")
    root = root.resolve(strict=True)
    if registered_import(target, directory=root) is not None:
        return _import_lifecycle_lock_path(target, root)
    return target.with_name(target.name + ".lock")


def _check_import_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ImportCancelled("GGUF import cancelled; no identity was published")


def _register_import(path: Path, sha256: str, directory=None) -> None:
    root = _safe_storage_root(directory)
    index = root / IMPORT_INDEX
    lock_path = root / (IMPORT_INDEX + ".lock")
    if _is_reparse_point(index) or _is_reparse_point(lock_path):
        raise ValueError("Refusing linked or redirected local import inventory")
    with _exclusive_download_lock(lock_path):
        if _is_reparse_point(index) or _is_reparse_point(lock_path):
            raise ValueError("Local import inventory changed into a redirect")
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
        existing = next((item for item in normalized
                         if item["path"] == record["path"]), None)
        if existing is not None and existing["sha256"] != sha256:
            raise RuntimeError(
                "This path is already registered with a different identity; "
                "unregister it before recording changed bytes")
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
    """Hash a local GGUF into ForgeOS's registry without copying or sidecar writes.

    The source/revision/license are unknown unless a separate trusted source
    review establishes them. The user's source directory is read-only to this
    operation; registry and lifecycle lock data stay in ForgeOS model storage.
    Existing legacy sidecars are checked and preserved. A caller may
    cooperatively cancel between bounded reads or receive progress events.
    """
    if cancel_event is not None and not isinstance(cancel_event, Event):
        raise TypeError("cancel_event must be a threading.Event")
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError("progress_callback must be callable")
    _check_import_cancelled(cancel_event)
    target = Path(path).expanduser().absolute()
    if target.suffix.lower() != ".gguf":
        raise ValueError("Choose a .gguf file")
    if any(_is_reparse_point(item) for item in (target, *target.parents)):
        raise ValueError("Refusing a link or reparse point in the selected model path")
    target = target.resolve(strict=True)
    root = _safe_storage_root(directory)
    manifest = target.with_name(target.name + ".forgeos.json")
    lock_path = _import_lifecycle_lock_path(target, root)
    if _is_reparse_point(manifest):
        raise ValueError("Refusing linked or redirected model metadata")

    with _exclusive_download_lock(lock_path):
        existing = None
        if os.path.lexists(manifest):
            if (_is_reparse_point(manifest) or not manifest.is_file()
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
        _register_import(target, record["sha256"], root)
    return {"status": "imported", "path": str(target), **record}


def unregister_imported_gguf(path: str | Path, *,
                             directory: str | Path | None = None,
                             cancel_event=None, progress_callback=None) -> dict:
    """Remove an imported path from inventory while preserving its GGUF bytes."""
    if cancel_event is not None and not isinstance(cancel_event, Event):
        raise TypeError("cancel_event must be a threading.Event")
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError("progress_callback must be callable")
    target = Path(path).expanduser().absolute()
    if any(_is_reparse_point(item) for item in (target, *target.parents)):
        raise ValueError("Imported model path contains a link or reparse point")
    target = target.resolve(strict=True)
    root = _safe_storage_root(directory)
    lock_path = _import_lifecycle_lock_path(target, root)
    index = root / IMPORT_INDEX
    index_lock = root / (IMPORT_INDEX + ".lock")
    with _exclusive_download_lock(lock_path):
        sidecar = target.with_name(target.name + ".forgeos.json")
        if _is_reparse_point(sidecar):
            raise ValueError("Imported model sidecar is linked or redirected")
        before = target.stat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not MIN_MODEL_BYTES <= before.st_size <= MAX_MODEL_BYTES):
            raise RuntimeError("Imported GGUF file identity changed")
        with target.open("rb") as source:
            if source.read(4) != b"GGUF":
                raise ValueError("Imported model no longer has a GGUF header")

        if _is_reparse_point(index) or _is_reparse_point(index_lock):
            raise ValueError("Refusing linked or redirected local import inventory")
        with _exclusive_download_lock(index_lock):
            if _is_reparse_point(index) or _is_reparse_point(index_lock):
                raise ValueError("Local import inventory changed into a redirect")
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
            entries = data["imports"]
            matches = [item for item in entries if isinstance(item, dict)
                       and item.get("path") == str(target)]
            if len(matches) != 1:
                raise ValueError("Imported model is not registered uniquely in ForgeOS")
            expected_sha = matches[0].get("sha256")
            if (set(matches[0]) != {"path", "sha256"}
                    or not isinstance(expected_sha, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", expected_sha)):
                raise ValueError("Imported model registry identity is invalid")
            if sidecar.exists():
                if (not sidecar.is_file() or sidecar.stat().st_nlink != 1
                        or sidecar.stat().st_size > 4096):
                    raise ValueError("Imported model sidecar is unsafe")
                try:
                    legacy = json.loads(sidecar.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError) as exc:
                    raise ValueError("Imported model sidecar is unreadable") from exc
                if (not isinstance(legacy, dict)
                        or legacy.get("format") != IMPORT_FORMAT
                        or legacy.get("source_path") != str(target)
                        or legacy.get("sha256") != expected_sha
                        or legacy.get("bytes") != before.st_size):
                    raise ValueError("Imported model sidecar disagrees with registry")
        try:
            actual_digest = _digest(
                target, cancel_event=cancel_event,
                progress_callback=progress_callback)
        except DownloadCancelled:
            raise ImportUnregisterCancelled(
                "GGUF unregister cancelled; inventory was unchanged") from None
        after = target.stat()
        if actual_digest != expected_sha or (
                before.st_size, before.st_mtime_ns, before.st_ino, before.st_nlink) != (
                after.st_size, after.st_mtime_ns, after.st_ino, after.st_nlink):
            raise RuntimeError("Imported GGUF changed during verification")

        with _exclusive_download_lock(index_lock):
            if _is_reparse_point(index) or _is_reparse_point(index_lock):
                raise ValueError("Local import inventory changed into a redirect")
            try:
                latest = json.loads(index.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise ValueError("Local import inventory is unreadable") from exc
            if (not isinstance(latest, dict)
                    or set(latest) != {"version", "imports"}
                    or type(latest.get("version")) is not int
                    or latest["version"] != 1
                    or not isinstance(latest["imports"], list)
                    or len(latest["imports"]) > MAX_IMPORTED_MODELS):
                raise ValueError("Local import inventory has an invalid shape")
            latest_matches = [item for item in latest["imports"]
                              if isinstance(item, dict)
                              and item.get("path") == str(target)]
            if (len(latest_matches) != 1
                    or latest_matches[0].get("sha256") != expected_sha):
                raise RuntimeError("Imported model registry changed during unregister")
            retained = [item for item in latest["imports"]
                        if item["path"] != str(target)]
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
            "sha256": expected_sha, "bytes": before.st_size,
            "weights_preserved": True, "sidecar_preserved": sidecar.exists()}


def purge_imported_gguf(path: str | Path, *, checkpoint_check,
                        directory: str | Path | None = None,
                        confirm: bool = False, cancel_event=None,
                        progress_callback=None) -> dict:
    """Permanently delete one explicitly confirmed imported model.

    The registry SHA-256 is the identity source, so interrupted deletion can
    be retried after either the sidecar or model bytes are already gone. The
    registry entry is atomically removed last. No other imported paths change.
    """
    if cancel_event is not None and not isinstance(cancel_event, Event):
        raise TypeError("cancel_event must be a threading.Event")
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError("progress_callback must be callable")
    if confirm is not True:
        raise ValueError("Explicit confirm=True required to permanently purge imported GGUF")
    if not callable(checkpoint_check):
        raise TypeError("checkpoint_check must be callable")
    target = Path(path).expanduser().absolute()
    if any(_is_reparse_point(item) for item in (target, *target.parents)):
        raise ValueError("Imported model path contains a link or reparse point")
    target = target.resolve(strict=False)
    if target.suffix.lower() != ".gguf":
        raise ValueError("Select an imported .gguf file")
    root = _safe_storage_root(directory)
    index = root / IMPORT_INDEX
    index_lock = root / (IMPORT_INDEX + ".lock")
    lock_path = _import_lifecycle_lock_path(target, root)
    sidecar = target.with_name(target.name + ".forgeos.json")
    for item in (index, index_lock, lock_path, sidecar):
        if _is_reparse_point(item):
            raise ValueError("Refusing linked or redirected imported metadata or lock")

    with _exclusive_download_lock(lock_path):
        with _exclusive_download_lock(index_lock):
            for item in (index, index_lock, lock_path, sidecar, target):
                if _is_reparse_point(item):
                    raise ValueError("Imported model path changed into a redirect")
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
            entries = data["imports"]
            seen_paths = set()
            for item in entries:
                if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
                        or not isinstance(item["path"], str)
                        or len(item["path"]) > 32768
                        or not Path(item["path"]).is_absolute()
                        or not isinstance(item["sha256"], str)
                        or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])):
                    raise ValueError("Local import inventory contains an invalid entry")
                if item["path"] in seen_paths:
                    raise ValueError("Local import inventory has duplicate model paths")
                seen_paths.add(item["path"])
            matches = [item for item in entries if item["path"] == str(target)]
            if len(matches) > 1:
                raise ValueError("Local import inventory has duplicate model paths")
            if not matches:
                if not target.exists() and not sidecar.exists():
                    return {"status": "already_purged", "path": str(target)}
                raise ValueError("GGUF is not registered in ForgeOS imported inventory")
            expected_sha = matches[0]["sha256"]
            if checkpoint_check(expected_sha) is not False:
                raise RuntimeError(
                    "A blocked task checkpoint references this GGUF; purge refused")

            identity = None
            legacy_sidecar_present = sidecar.exists()
            deleted_bytes = 0
            if sidecar.exists():
                if (not sidecar.is_file() or sidecar.stat().st_nlink != 1
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
                        or identity.get("sha256") != expected_sha):
                    raise ValueError("Imported model sidecar disagrees with registry")

            if target.exists():
                before = target.stat()
                deleted_bytes = before.st_size
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or not MIN_MODEL_BYTES <= before.st_size <= MAX_MODEL_BYTES
                        or identity is not None and before.st_size != identity["bytes"]):
                    raise RuntimeError("Imported GGUF file identity changed")
                with target.open("rb") as source:
                    if source.read(4) != b"GGUF":
                        raise ValueError("Imported model no longer has a GGUF header")
                try:
                    actual_sha = _digest(
                        target, cancel_event=cancel_event,
                        progress_callback=progress_callback)
                except DownloadCancelled:
                    raise ImportPurgeCancelled(
                        "Imported GGUF purge cancelled; no files were deleted") from None
                after = target.stat()
                if (actual_sha != expected_sha or
                        (before.st_size, before.st_mtime_ns, before.st_ino,
                         before.st_nlink) !=
                        (after.st_size, after.st_mtime_ns, after.st_ino,
                         after.st_nlink)):
                    raise RuntimeError("Imported GGUF changed during purge verification")

            if cancel_event is not None and cancel_event.is_set():
                raise ImportPurgeCancelled(
                    "Imported GGUF purge cancelled; no files were deleted")
            if sidecar.exists():
                sidecar.unlink()
            if target.exists():
                target.unlink()
            retained = [item for item in entries if item["path"] != str(target)]
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
    return {"status": "purged", "path": str(target),
            "sha256": expected_sha,
            "bytes": deleted_bytes,
            "weights_deleted": True,
            "sidecar_deleted": legacy_sidecar_present}
