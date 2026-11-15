"""Bounded, content-free integrity manifests for Git worktrees."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import re

from .core import _publish_database_copy


WORKTREE_MANIFEST_FORMAT = "forgeos-worktree-manifest-v1"
MAX_WORKTREE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_WORKTREE_MANIFEST_LINE_BYTES = 2 * 1024
MAX_WORKTREE_FILES = 4096
MAX_WORKTREE_SCAN_ENTRIES = 8192
MAX_WORKTREE_DEPTH = 32
MAX_WORKTREE_FILE_BYTES = 64 * 1024 * 1024
MAX_WORKTREE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_WORKTREE_ISSUES = 32
HASH_CHUNK_BYTES = 64 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXCLUDED_NAMES = frozenset({
    ".git", ".forgeos", ".forgeos-runtime", ".venv", "venv",
    "node_modules", "__pycache__",
})


def _is_reparse(info) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _plain_info(path: Path, *, directory: bool | None = None):
    try:
        info = path.lstat()
    except OSError as exc:
        raise ValueError("Worktree entry is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
        raise ValueError("Worktree contains a symlink or reparse point")
    if directory is True and not stat.S_ISDIR(info.st_mode):
        raise ValueError("Worktree root must be a regular directory")
    if directory is False and not stat.S_ISREG(info.st_mode):
        raise ValueError("Worktree entry is not a regular file")
    if stat.S_ISREG(info.st_mode) and getattr(info, "st_nlink", 1) != 1:
        raise ValueError("Worktree contains a hard-linked file")
    return info


def _root(path: str | Path) -> Path:
    try:
        root = Path(path).expanduser().absolute()
    except TypeError as exc:
        raise ValueError("Worktree root is invalid") from exc
    _plain_info(root, directory=True)
    metadata = root / ".git"
    info = _plain_info(metadata)
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError("Worktree must contain regular Git metadata")
    return root


def _excluded(name: str) -> bool:
    return name in _EXCLUDED_NAMES or name.startswith(".env")


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 512
            or "\\" in value or "\x00" in value or ":" in value):
        raise ValueError("Worktree manifest path is invalid")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("Worktree manifest path is invalid")
    if any(_excluded(part) for part in pure.parts):
        raise ValueError("Worktree manifest path is excluded")
    return "/".join(pure.parts)


def _iter_files(root: Path):
    pending = [(root, "", 0)]
    scanned = 0
    while pending:
        directory, prefix, depth = pending.pop()
        try:
            with os.scandir(directory) as contents:
                entries = []
                for entry in contents:
                    scanned += 1
                    if scanned > MAX_WORKTREE_SCAN_ENTRIES:
                        raise RuntimeError("Worktree scan exceeds entry limit")
                    entries.append(entry)
        except OSError as exc:
            raise ValueError("Worktree directory could not be scanned") from exc
        entries.sort(key=lambda entry: (entry.name.casefold(), entry.name))
        for entry in entries:
            if _excluded(entry.name):
                continue
            relative = entry.name if not prefix else prefix + "/" + entry.name
            path = Path(entry.path)
            info = _plain_info(path)
            if stat.S_ISDIR(info.st_mode):
                if depth >= MAX_WORKTREE_DEPTH:
                    raise RuntimeError("Worktree nesting exceeds depth limit")
                pending.append((path, relative, depth + 1))
            elif stat.S_ISREG(info.st_mode):
                yield _relative(relative), path, info
            else:
                raise ValueError("Worktree contains a special file")


def _same_file(before, after) -> bool:
    return all(getattr(before, field, None) == getattr(after, field, None)
               for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns"))


def _stream_file(path: Path, before, consumer=None) -> tuple[int, str]:
    """Hash one regular file while optionally forwarding bounded blocks."""
    if before.st_size > MAX_WORKTREE_FILE_BYTES:
        raise ValueError("Worktree file exceeds size limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Worktree file could not be opened safely") from exc
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(descriptor)
        if (_is_reparse(opened) or not stat.S_ISREG(opened.st_mode)
                or getattr(opened, "st_nlink", 1) != 1
                or not _same_file(before, opened)):
            raise ValueError("Worktree file changed or became a link")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            while True:
                block = source.read(HASH_CHUNK_BYTES)
                if not block:
                    break
                size += len(block)
                if size > MAX_WORKTREE_FILE_BYTES:
                    raise ValueError("Worktree file exceeds size limit")
                if consumer is not None:
                    consumer(block)
                digest.update(block)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("Worktree file could not be hashed safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise ValueError("Worktree file changed during hashing") from exc
    if not _same_file(before, after):
        raise ValueError("Worktree file changed during hashing")
    return size, digest.hexdigest()


def _hash_file(path: Path, before) -> tuple[int, str]:
    return _stream_file(path, before)


def _destination(root: Path, destination: str | Path) -> Path:
    try:
        target = Path(destination).expanduser().absolute()
    except TypeError as exc:
        raise ValueError("Worktree manifest destination is invalid") from exc
    if target == root or root in target.parents:
        raise ValueError("Worktree manifest destination must be outside the worktree")
    try:
        _plain_info(target.parent, directory=True)
    except ValueError as exc:
        raise ValueError(
            "Worktree manifest destination directory is unavailable or unsafe") from exc
    if target.is_symlink() or target.exists():
        raise ValueError(
            "Worktree manifest must be a new file in an existing directory")
    return target


def _record(output, record, written: int) -> int:
    encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":"))
               + "\n").encode("utf-8")
    if len(encoded) > MAX_WORKTREE_MANIFEST_LINE_BYTES:
        raise RuntimeError("Worktree manifest record exceeds line limit")
    if written + len(encoded) > MAX_WORKTREE_MANIFEST_BYTES:
        raise RuntimeError("Worktree manifest exceeds size limit")
    output.write(encoded.decode("utf-8"))
    return written + len(encoded)


def export_worktree_manifest(root: str | Path, destination: str | Path) -> Path:
    """Stream regular-file digests from one Git worktree to a new JSONL file."""
    worktree = _root(root)
    target = _destination(worktree, destination)
    import tempfile as _tempfile
    fd, name = _tempfile.mkstemp(
        prefix=".forgeos-worktree-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    staging = Path(name)
    count = 0
    total = 0
    written = 0
    try:
        with staging.open("w", encoding="utf-8", newline="\n") as output:
            written = _record(output, {
                "record": "header", "format": WORKTREE_MANIFEST_FORMAT}, written)
            for relative, path, info in _iter_files(worktree):
                count += 1
                if count > MAX_WORKTREE_FILES:
                    raise RuntimeError("Worktree manifest exceeds file limit")
                if total + info.st_size > MAX_WORKTREE_TOTAL_BYTES:
                    raise RuntimeError("Worktree manifest exceeds byte limit")
                size, digest = _hash_file(path, info)
                if size != info.st_size:
                    raise ValueError("Worktree file changed during hashing")
                total += size
                written = _record(output, {
                    "record": "file", "path": relative,
                    "size": size, "sha256": digest}, written)
            written = _record(output, {
                "record": "summary", "file_count": count,
                "total_bytes": total, "complete": True}, written)
            output.flush()
            os.fsync(output.buffer.fileno())
        _publish_database_copy(staging, target)
        return target
    finally:
        staging.unlink(missing_ok=True)


def _issue(issues: list, message: str, line: int, path: str,
           max_issues: int) -> bool:
    if len(issues) < max_issues:
        issues.append({"line": line, "path": path, "problem": message})
        return False
    return True


def check_worktree_manifest(source: str | Path, root: str | Path,
                            *, max_issues: int = MAX_WORKTREE_ISSUES) -> dict:
    """Verify a manifest and its referenced worktree without opening SQLite."""
    if (type(max_issues) is not int or isinstance(max_issues, bool)
            or not 1 <= max_issues <= MAX_WORKTREE_ISSUES):
        raise ValueError("Worktree issue limit must be 1..32")
    manifest = Path(source).expanduser().absolute()
    try:
        manifest_info = _plain_info(manifest, directory=False)
    except ValueError as exc:
        raise ValueError(
            "Worktree manifest must be an existing regular file") from exc
    if manifest_info.st_size > MAX_WORKTREE_MANIFEST_BYTES:
        raise ValueError("Worktree manifest is oversized")
    worktree = _root(root)
    issues = []
    truncated = False
    expected = {}
    expected_keys = set()
    expected_count = None
    expected_total = None
    expected_bytes = 0
    header_valid = False
    summary_valid = False
    phase = "header"
    records = 0

    def issue(message, line=0, path=""):
        nonlocal truncated
        truncated = _issue(issues, message, line, path, max_issues) or truncated

    try:
        stream = manifest.open("rb")
    except OSError as exc:
        raise ValueError("Worktree manifest could not be opened") from exc
    try:
        while True:
            raw = stream.readline(MAX_WORKTREE_MANIFEST_LINE_BYTES + 1)
            if not raw:
                break
            records += 1
            if len(raw) > MAX_WORKTREE_MANIFEST_LINE_BYTES:
                issue("record exceeds the 2 KiB line limit", records)
                while raw and not raw.endswith(b"\n"):
                    raw = stream.readline(MAX_WORKTREE_MANIFEST_LINE_BYTES + 1)
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                issue("record is not valid UTF-8 JSON", records)
                continue
            if not isinstance(record, dict):
                issue("record must be a JSON object", records)
                continue
            kind = record.get("record")
            if phase == "header":
                if (set(record) != {"record", "format"}
                        or kind != "header"
                        or record.get("format") != WORKTREE_MANIFEST_FORMAT):
                    issue("invalid worktree manifest header", records)
                else:
                    header_valid = True
                phase = "files"
                continue
            if phase == "files":
                if kind == "file":
                    if set(record) != {"record", "path", "size", "sha256"}:
                        issue("file record fields are invalid", records)
                        continue
                    relative = record.get("path")
                    size = record.get("size")
                    digest = record.get("sha256")
                    try:
                        relative = _relative(relative)
                    except ValueError as exc:
                        issue(str(exc), records)
                        continue
                    if (type(size) is not int or size < 0
                            or size > MAX_WORKTREE_FILE_BYTES
                            or not isinstance(digest, str)
                            or not _SHA256.fullmatch(digest)):
                        issue("file record values are invalid", records, relative)
                        continue
                    key = relative.casefold()
                    if key in expected_keys:
                        issue("duplicate file record", records, relative)
                        continue
                    if len(expected) >= MAX_WORKTREE_FILES:
                        issue("manifest contains too many files", records, relative)
                        continue
                    expected[relative] = (size, digest)
                    expected_keys.add(key)
                    expected_bytes += size
                    continue
                if kind != "summary":
                    issue("summary must follow file records", records)
                    continue
                if set(record) != {
                        "record", "file_count", "total_bytes", "complete"}:
                    issue("summary record fields are invalid", records)
                else:
                    expected_count = record.get("file_count")
                    expected_total = record.get("total_bytes")
                    if (type(expected_count) is not int
                            or expected_count != len(expected)
                            or type(expected_total) is not int
                            or expected_total < 0
                            or expected_total > MAX_WORKTREE_TOTAL_BYTES
                            or expected_total != expected_bytes
                            or record.get("complete") is not True):
                        issue("summary does not match manifest records", records)
                    else:
                        summary_valid = True
                phase = "complete"
                continue
            issue("records are present after the completion summary", records)
    finally:
        stream.close()
    if not header_valid:
        issue("missing or invalid worktree manifest header")
    if not summary_valid:
        issue("missing or invalid worktree manifest summary", records)

    manifest_files = len(expected)
    checked = 0
    actual_bytes = 0
    if header_valid and summary_valid:
        try:
            for relative, path, info in _iter_files(worktree):
                if relative not in expected:
                    issue("worktree contains an unlisted file", 0, relative)
                    continue
                size, digest = expected.pop(relative)
                checked += 1
                actual_bytes += info.st_size
                try:
                    actual_size, actual_digest = _hash_file(path, info)
                except (OSError, ValueError) as exc:
                    issue(str(exc), 0, relative)
                    continue
                if actual_size != size or actual_digest != digest:
                    issue("worktree file does not match its manifest", 0, relative)
        except (OSError, RuntimeError, ValueError) as exc:
            issue(str(exc), 0)
        for relative in sorted(expected):
            issue("manifest file is missing from the worktree", 0, relative)
    return {
        "valid": bool(header_valid and summary_valid and not issues),
        "format": WORKTREE_MANIFEST_FORMAT if header_valid else "",
        "records": records,
        "manifest_files": manifest_files,
        "files_checked": checked,
        "bytes_checked": actual_bytes,
        "issues": issues,
        "truncated": truncated,
    }
