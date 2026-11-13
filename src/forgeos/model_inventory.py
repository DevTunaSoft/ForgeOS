"""Bounded, offline inventory and explicit re-verification of local GGUFs.

Downloaded weights use their revisioned manifests; imported weights are path
references in one bounded registry plus adjacent identity sidecars. No model
copy, background watcher, implicit hashing or on-demand network IO.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

from .gguf_download import (
    MAX_MODEL_BYTES, _digest, _reject_existing_link,
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
    if index.is_symlink():
        raise RuntimeError("Local GGUF import index is a symbolic link")
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
    if (sidecar.is_symlink() or not sidecar.is_file()
            or sidecar.stat().st_nlink != 1
            or sidecar.stat().st_size > MAX_MANIFEST_BYTES):
        raise ValueError("Imported GGUF identity sidecar is missing or unsafe")
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
                if any(item.is_symlink() for item in (path, *path.parents)):
                    status = "unsafe_path"
                else:
                    info = _import_metadata(path, checksum)
                    stat_result = path.stat()
                    amount = stat_result.st_size
                    status = ("size_mismatch" if amount != info["bytes"]
                              else "imported_not_reverified")
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
    if (manifest.is_symlink() or not manifest.is_file()
            or manifest.stat().st_size > MAX_MANIFEST_BYTES):
        raise ValueError("Missing or oversized GGUF manifest")
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
                if item.is_symlink():
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
                if model.is_symlink():
                    continue
                try:
                    file_size = model.stat().st_size if model.is_file() else None
                except OSError:
                    file_size = None
                status = ("missing" if file_size is None
                          else "size_mismatch" if file_size != info["bytes"]
                          else "present_not_reverified")
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
                if item.is_symlink():
                    continue
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
                return "matching imported registry entry needs manual inspection"
    return "not in local model inventory"


def verify_download(path: str | Path, *,
                    directory: str | Path | None = None) -> dict:
    """Explicit streaming SHA check; does not load the model or use RAM quota."""
    root = _safe_storage_root(directory)
    selected = Path(path).expanduser().absolute()
    imported = next((record for record in _import_records(root)
                     if record["path"] == selected), None)
    if imported is not None:
        if any(item.is_symlink() for item in (selected, *selected.parents)):
            raise ValueError("Imported GGUF path contains a symbolic link")
        selected = selected.resolve(strict=True)
        if selected != imported["path"] or not selected.is_file():
            raise ValueError("Select the registered local imported GGUF")
        info = _import_metadata(selected, imported["sha256"])
        before = selected.stat()
        if before.st_nlink != 1 or before.st_size != info["bytes"]:
            raise RuntimeError("Imported GGUF size or link count changed")
        if _digest(selected) != info["sha256"]:
            raise RuntimeError("Imported GGUF SHA-256 mismatch")
        after = selected.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ino):
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
    if _digest(selected) != info["sha256"]:
        raise RuntimeError("GGUF SHA-256 mismatch; file was modified")
    after = selected.stat()
    if (before.st_size != after.st_size or
            before.st_mtime_ns != after.st_mtime_ns or
            before.st_ino != after.st_ino):
        raise RuntimeError("GGUF changed while verifying")
    with selected.open("rb") as inp:
        if inp.read(4) != b"GGUF":
            raise RuntimeError("Verified bytes are not a GGUF file")
    return {"status": "verified", "path": str(selected),
            "sha256": info["sha256"], "bytes": info["bytes"],
            "repo": info["repo"], "revision": info["revision"]}
