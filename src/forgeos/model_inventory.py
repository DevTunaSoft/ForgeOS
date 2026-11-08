"""Bounded, offline view and explicit re-verification of GGUF downloads.

Uses the SAME revisioned files and manifests as gguf_download.py. No second
model database, background watcher, implicit hashing or on-demand network IO.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from .gguf_download import (
    MAX_MODEL_BYTES, _digest, _reject_existing_link,
    _safe_name, _safe_storage_root,
)
from .huggingface_catalog import REPO_ID

MAX_SCAN_ENTRIES = 4096
MAX_LISTED_MODELS = 200
MAX_MANIFEST_BYTES = 4096
REVISION = re.compile(r"[a-f0-9]{40}\Z")
CHECKSUM = re.compile(r"[a-f0-9]{64}\Z")


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
    results.sort(key=lambda x: (x["repo"].lower(), x["file"].lower()))
    return results


def verify_download(path: str | Path, *,
                    directory: str | Path | None = None) -> dict:
    """Explicit streaming SHA check; does not load the model or use RAM quota."""
    root = _safe_storage_root(directory)
    selected = Path(path).expanduser().absolute()
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
