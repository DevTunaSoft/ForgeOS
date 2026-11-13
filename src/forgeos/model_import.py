"""Explicit no-copy registration of a user-selected local GGUF."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from .gguf_download import (
    CHUNK_BYTES, MAX_MODEL_BYTES, _exclusive_download_lock,
)

IMPORT_FORMAT = "forgeos-local-import-v1"
MIN_MODEL_BYTES = 16


def import_local_gguf(path: str | Path) -> dict:
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
        finally:
            temporary.unlink(missing_ok=True)
    return {"status": "imported", "path": str(target), **record}
