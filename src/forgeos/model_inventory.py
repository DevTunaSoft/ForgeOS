"""Bounded, offline inventory and explicit re-verification of local GGUFs.

Downloaded weights use their revisioned manifests; imported weights are path
references in one bounded ForgeOS registry. Legacy adjacent import sidecars are
validated when present. No model copy, background watcher, implicit hashing or
on-demand network IO.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from threading import Event

from .gguf_download import (
    DownloadCancelled, MAX_MODEL_BYTES, _digest, _exclusive_download_lock,
    _is_reparse_point, _reject_existing_link,
    _safe_name, _safe_storage_root,
)
from .huggingface_catalog import REPO_ID
from .model_import import (
    IMPORT_FORMAT, IMPORT_INDEX, MAX_IMPORT_INDEX_BYTES,
    MAX_IMPORTED_MODELS, MIN_MODEL_BYTES,
)

MAX_SCAN_ENTRIES = 4096
MAX_LISTED_MODELS = 200
MAX_MANIFEST_BYTES = 4096
REVISION = re.compile(r"[a-f0-9]{40}\Z")
CHECKSUM = re.compile(r"[a-f0-9]{64}\Z")


def _import_records(root: Path) -> list[dict]:
    index = root / IMPORT_INDEX
    if _is_reparse_point(index):
        raise RuntimeError("Local GGUF import index is linked or redirected")
    if not index.exists():
        return []
    if not index.is_file() or index.stat().st_size > MAX_IMPORT_INDEX_BYTES:
        raise RuntimeError("Local GGUF import index is not a bounded regular file")
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("Local GGUF import index is unreadable") from exc
    if (not isinstance(data, dict) or set(data) != {"version", "imports"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["imports"], list)
            or len(data["imports"]) > MAX_IMPORTED_MODELS):
        raise RuntimeError("Local GGUF import index has an invalid shape")
    records = []
    seen = set()
    for entry in data["imports"]:
        if (not isinstance(entry, dict) or set(entry) != {"path", "sha256"}
                or not isinstance(entry["path"], str)
                or len(entry["path"]) > 4096
                or not isinstance(entry["sha256"], str)
                or not CHECKSUM.fullmatch(entry["sha256"])):
            raise RuntimeError("Local GGUF import index contains an invalid entry")
        path = Path(entry["path"])
        if not path.is_absolute() or str(path) in seen:
            raise RuntimeError("Local GGUF import index contains a duplicate or relative path")
        seen.add(str(path))
        records.append({"path": path, "sha256": entry["sha256"]})
    return records


def _import_metadata(path: Path, checksum: str) -> dict:
    sidecar = path.with_name(path.name + ".forgeos.json")
    if _is_reparse_point(sidecar):
        raise ValueError("Imported GGUF identity sidecar is unsafe")
    if not sidecar.exists():
        info = {"format": IMPORT_FORMAT, "source_path": str(path),
                "file": path.name, "bytes": path.stat().st_size,
                "sha256": checksum, "provenance": "unknown"}
        if not MIN_MODEL_BYTES <= info["bytes"] <= MAX_MODEL_BYTES:
            raise ValueError("Imported GGUF size is outside the supported range")
        return info
    if (not sidecar.is_file() or sidecar.stat().st_nlink != 1
            or sidecar.stat().st_size > MAX_MANIFEST_BYTES):
        raise ValueError("Imported GGUF identity sidecar is unsafe")
    try:
        info = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Imported GGUF identity sidecar is unreadable") from exc
    if (not isinstance(info, dict)
            or set(info) != {"format", "source_path", "file", "bytes",
                             "sha256", "provenance"}
            or info.get("format") != IMPORT_FORMAT
            or info.get("source_path") != str(path)
            or info.get("file") != path.name
            or info.get("provenance") != "unknown"
            or type(info.get("bytes")) is not int
            or not MIN_MODEL_BYTES <= info["bytes"] <= MAX_MODEL_BYTES
            or info.get("sha256") != checksum):
        raise ValueError("Imported GGUF sidecar disagrees with its inventory entry")
    return info


def _list_imports(root: Path) -> list[dict]:
    output = []
    for record in _import_records(root):
        path, checksum = record["path"], record["sha256"]
        status = "missing"
        amount = None
        try:
            if path.exists():
                if any(_is_reparse_point(item) for item in (path, *path.parents)):
                    status = "unsafe_path"
                else:
                    info = _import_metadata(path, checksum)
                    stat_result = path.stat(follow_symlinks=False)
                    amount = stat_result.st_size
                    status = (
                        "unsafe_path" if (not stat.S_ISREG(stat_result.st_mode)
                                          or stat_result.st_nlink != 1) else
                        "size_mismatch" if amount != info["bytes"] else
                        "imported_not_reverified")
            else:
                # Preserve a visible inventory entry when the original weight
                # was moved or disconnected; it cannot be selected for use.
                sidecar = path.with_name(path.name + ".forgeos.json")
                if sidecar.exists():
                    _import_metadata(path, checksum)
        except (OSError, ValueError, RuntimeError):
            status = "needs_manual_inspection"
        output.append({
            "path": str(path), "repo": "Local import (provenance unknown)",
            "revision": None, "file": path.name, "bytes": amount,
            "sha256": checksum, "status": status, "kind": "imported",
        })
    return output


def _manifest_model_file(manifest: Path, root: Path) -> tuple[Path, dict]:
    if (_is_reparse_point(manifest) or not manifest.is_file()
            or manifest.stat().st_nlink != 1
            or manifest.stat().st_size > MAX_MANIFEST_BYTES):
        raise ValueError("Missing, linked or oversized GGUF manifest")
    try:
        info = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Unreadable GGUF manifest") from exc
    if not isinstance(info, dict):
        raise ValueError("Invalid GGUF metadata")
    repo, revision, name = (
        info.get("repo"), info.get("revision"), info.get("file"))
    amount, checksum = info.get("bytes"), info.get("sha256")
    if (not isinstance(repo, str) or not REPO_ID.fullmatch(repo)
            or not isinstance(revision, str) or not REVISION.fullmatch(revision)
            or not isinstance(checksum, str) or not CHECKSUM.fullmatch(checksum)
            or type(amount) is not int or not 16 <= amount <= MAX_MODEL_BYTES):
        raise ValueError("GGUF manifest has unsafe identity fields")
    name_parts = _safe_name(name)
    expected = root.joinpath(*repo.split("/"), revision, *name_parts)
    if manifest != expected.with_name(expected.name + ".forgeos.json"):
        raise ValueError("GGUF manifest does not match revisioned storage path")
    _reject_existing_link(expected, root)
    return expected, info


def list_downloads(directory: str | Path | None = None) -> list[dict]:
    """List up to 200 local files with metadata status; NO gigabyte rehash.

    A 'present_not_reverified' entry must not be claimed SHA-verified until
    verify_download() has streamed its file contents successfully.
    """
    root = _safe_storage_root(directory)
    pending = [(root, 0)]
    examined = 0
    results = []
    while pending:
        folder, depth = pending.pop()
        if depth > 12:
            raise RuntimeError("GGUF storage nesting exceeds inventory limit")
        with os.scandir(folder) as contents:
            for item in contents:
                examined += 1
                if examined > MAX_SCAN_ENTRIES:
                    raise RuntimeError("GGUF inventory exceeds bounded scan limit")
                if _is_reparse_point(Path(item.path)):
                    continue
                if folder == root and item.name == ".forgeos-retired":
                    # Retired weights have their own bounded inventory.
                    continue
                if item.is_dir(follow_symlinks=False):
                    pending.append((Path(item.path), depth + 1))
                    continue
                if not item.is_file(follow_symlinks=False):
                    continue
                if not item.name.lower().endswith(".gguf.forgeos.json"):
                    continue
                manifest = Path(item.path)
                try:
                    model, info = _manifest_model_file(manifest, root)
                except (OSError, ValueError):
                    # Untrusted/malformed metadata cannot become a selectable
                    # model, even if a matching .gguf file happens to exist.
                    continue
                if _is_reparse_point(model):
                    continue
                try:
                    file_info = model.stat(follow_symlinks=False)
                except OSError:
                    file_info = None
                status = (
                    "missing" if file_info is None else
                    "unsafe_path" if (not stat.S_ISREG(file_info.st_mode)
                                      or file_info.st_nlink != 1) else
                    "size_mismatch" if file_info.st_size != info["bytes"] else
                    "present_not_reverified")
                results.append({
                    "path": str(model), "repo": info["repo"],
                    "revision": info["revision"], "file": info["file"],
                    "bytes": info["bytes"], "sha256": info["sha256"],
                    "status": status,
                })
                if len(results) > MAX_LISTED_MODELS:
                    raise RuntimeError("GGUF inventory exceeds model count limit")
    results.extend(_list_imports(root))
    if len(results) > MAX_LISTED_MODELS:
        raise RuntimeError("GGUF inventory exceeds model count limit")
    results.sort(key=lambda x: (x["repo"].lower(), x["file"].lower()))
    return results


def storage_usage(directory: str | Path | None = None) -> dict:
    """Count managed GGUF weight bytes without hashing; bounded and offline.

    Active, retired, partial, and quarantined GGUFs count toward the quota.
    Imported paths outside ForgeOS storage are references and do not count.
    Hard links are counted once by file identity. Unsafe/unreadable oversized
    trees fail closed so a quota check never treats unknown usage as zero.
    """
    root = _safe_storage_root(directory)
    pending = [(root, 0)]
    examined = 0
    total = 0
    files = 0
    seen = set()
    while pending:
        folder, depth = pending.pop()
        if depth > 12:
            raise RuntimeError("GGUF storage nesting exceeds usage scan limit")
        with os.scandir(folder) as contents:
            for item in contents:
                examined += 1
                if examined > MAX_SCAN_ENTRIES:
                    raise RuntimeError("GGUF storage exceeds bounded usage scan limit")
                if _is_reparse_point(Path(item.path)):
                    raise RuntimeError(
                        "GGUF storage contains a linked or reparse-point entry")
                if item.is_dir(follow_symlinks=False):
                    pending.append((Path(item.path), depth + 1))
                    continue
                name = item.name.lower()
                is_weight = (name.endswith(".gguf") or name.endswith(".gguf.part")
                             or ".gguf.part.corrupt-" in name)
                if not is_weight or not item.is_file(follow_symlinks=False):
                    continue
                info = item.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_size < 0:
                    raise RuntimeError("GGUF storage contains an unsafe weight entry")
                identity = ((info.st_dev, info.st_ino) if info.st_ino else
                            os.path.normcase(os.path.abspath(item.path)))
                if identity in seen:
                    continue
                seen.add(identity)
                total += info.st_size
                files += 1
    return {"bytes": total, "files": files}


def catalog_file_status(repo: str, revision: str, file_info: dict,
                        records: list[dict]) -> str:
    """Describe matching local metadata without hashing large model files."""
    name, checksum = file_info.get("path"), file_info.get("sha256")
    exact = [entry for entry in records
             if entry.get("repo") == repo
             and entry.get("revision") == revision
             and entry.get("file") == name]
    if exact:
        if checksum and all(entry.get("sha256") != checksum for entry in exact):
            return "local revision exists with a different SHA-256"
        labels = {
            "present_not_reverified": "present; SHA-256 not reverified in this view",
            "missing": "recorded locally; file is missing",
            "size_mismatch": "local file size differs from its manifest",
            "unsafe_path": "local model file is linked or not a regular file",
        }
        return labels.get(exact[0].get("status"),
                          "local entry needs manual inspection")
    if checksum:
        for entry in records:
            if entry.get("sha256") == checksum and entry.get("kind") == "imported":
                state = entry.get("status")
                if state == "imported_not_reverified":
                    return ("import registry claims same SHA-256; bytes not reverified, "
                            "source unknown")
                if state == "missing":
                    return "import registry claims same SHA-256, but local file is missing"
                if state == "size_mismatch":
                    return "import registry claims same SHA-256, but local size differs"
                if state == "unsafe_path":
                    return "matching imported model is linked or not a regular file"
                return "matching imported registry entry needs manual inspection"
    return "not in local model inventory"


class VerificationCancelled(RuntimeError):
    """The caller cancelled a streaming model SHA-256 verification."""


def _verify_download_unlocked(path: str | Path, root: Path, *,
                              cancel_event=None, progress_callback=None) -> dict:
    """Verify model bytes while the caller holds that model's lifecycle lock."""
    selected = Path(path).expanduser().absolute()
    imported = next((record for record in _import_records(root)
                     if record["path"] == selected), None)
    if imported is not None:
        if any(_is_reparse_point(item) for item in (selected, *selected.parents)):
            raise ValueError("Imported GGUF path contains a link or reparse point")
        selected = selected.resolve(strict=True)
        if selected != imported["path"] or not selected.is_file():
            raise ValueError("Select the registered local imported GGUF")
        info = _import_metadata(selected, imported["sha256"])
        before = selected.stat()
        if before.st_nlink != 1 or before.st_size != info["bytes"]:
            raise RuntimeError("Imported GGUF size or link count changed")
        if _digest(selected, cancel_event=cancel_event,
                   progress_callback=progress_callback) != info["sha256"]:
            raise RuntimeError("Imported GGUF SHA-256 mismatch")
        after = selected.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino,
                before.st_nlink) != (
                after.st_size, after.st_mtime_ns, after.st_ino,
                after.st_nlink) or after.st_nlink != 1:
            raise RuntimeError("Imported GGUF changed while verifying")
        with selected.open("rb") as inp:
            if inp.read(4) != b"GGUF":
                raise RuntimeError("Imported bytes are not a GGUF file")
        return {"status": "verified", "path": str(selected),
                "sha256": info["sha256"], "bytes": info["bytes"],
                "repo": None, "revision": None, "provenance": "unknown"}
    _reject_existing_link(selected, root)
    # Windows can report a TemporaryDirectory path with an alias or
    # different canonical spelling than the already-resolved storage root.
    # Normalize only AFTER the no-link/outside-root validation.
    selected = selected.resolve(strict=False)
    manifest = selected.with_name(selected.name + ".forgeos.json")
    _reject_existing_link(manifest, root)
    expected, info = _manifest_model_file(manifest, root)
    if expected != selected or not selected.is_file():
        raise ValueError("Select a downloaded GGUF with a matching manifest")
    if selected.stat().st_size != info["bytes"]:
        raise RuntimeError("GGUF size changed since download")
    before = selected.stat()
    if before.st_nlink != 1:
        raise RuntimeError("Hard-linked GGUF model files are not trusted")
    if _digest(selected, cancel_event=cancel_event,
               progress_callback=progress_callback) != info["sha256"]:
        raise RuntimeError("GGUF SHA-256 mismatch; file was modified")
    after = selected.stat()
    if (before.st_size != after.st_size or
            before.st_mtime_ns != after.st_mtime_ns or
            before.st_ino != after.st_ino or
            before.st_nlink != after.st_nlink or after.st_nlink != 1):
        raise RuntimeError("GGUF changed while verifying")
    with selected.open("rb") as inp:
        if inp.read(4) != b"GGUF":
            raise RuntimeError("Verified bytes are not a GGUF file")
    return {"status": "verified", "path": str(selected),
            "sha256": info["sha256"], "bytes": info["bytes"],
            "repo": info["repo"], "revision": info["revision"]}


def verify_download(path: str | Path, *,
                    directory: str | Path | None = None,
                    cancel_event=None, progress_callback=None) -> dict:
    """Explicit streaming SHA check serialized with managed model lifecycle.

    The optional callback receives ``(stage, completed_bytes, total_bytes)``;
    cancellation is cooperative between bounded 1 MiB reads.
    """
    if cancel_event is not None and not isinstance(cancel_event, Event):
        raise TypeError("cancel_event must be a threading.Event")
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError("progress_callback must be callable")
    root = _safe_storage_root(directory)
    selected = Path(path).expanduser().absolute()
    imported = next((record for record in _import_records(root)
                     if record["path"] == selected), None)
    if imported is not None:
        if any(_is_reparse_point(item) for item in (selected, *selected.parents)):
            raise ValueError("Imported GGUF path contains a link or reparse point")
        from .model_import import _import_lifecycle_lock_path
        lock = _import_lifecycle_lock_path(selected.resolve(strict=False), root)
        if any(_is_reparse_point(item) for item in (lock, *lock.parents)):
            raise ValueError("Imported GGUF lifecycle lock is linked or redirected")
        if os.path.lexists(lock) and (
                not lock.is_file() or lock.stat().st_nlink != 1):
            raise ValueError("Imported GGUF lifecycle lock is unsafe")
    else:
        lock = selected.with_name(selected.name + ".lock")
        _reject_existing_link(selected, root)
        selected = selected.resolve(strict=False)
        lock = selected.with_name(selected.name + ".lock")
        _reject_existing_link(lock, root)
        if os.path.lexists(lock) and (
                not lock.is_file() or lock.stat().st_nlink != 1):
            raise ValueError("GGUF lifecycle lock is unsafe")
    with _exclusive_download_lock(lock):
        try:
            return _verify_download_unlocked(
                selected, root, cancel_event=cancel_event,
                progress_callback=progress_callback)
        except DownloadCancelled:
            raise VerificationCancelled("GGUF verification cancelled") from None
