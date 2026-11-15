"""Bounded, source-only ZIP archives for Git worktree transfer."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import tempfile
import zipfile

from .core import _publish_database_copy
from .worktree_manifest import (
    HASH_CHUNK_BYTES,
    MAX_WORKTREE_FILE_BYTES,
    MAX_WORKTREE_FILES,
    MAX_WORKTREE_ISSUES,
    MAX_WORKTREE_MANIFEST_BYTES,
    MAX_WORKTREE_MANIFEST_LINE_BYTES,
    MAX_WORKTREE_TOTAL_BYTES,
    WORKTREE_MANIFEST_FORMAT,
    _destination,
    _iter_files,
    _plain_info,
    _record,
    _relative,
    _root,
    _stream_file,
)


WORKTREE_ARCHIVE_FORMAT = "forgeos-worktree-archive-v1"
WORKTREE_ARCHIVE_MANIFEST = "forgeos-worktree-manifest.jsonl"
MAX_WORKTREE_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_WORKTREE_ARCHIVE_ENTRIES = MAX_WORKTREE_FILES + 1
MAX_WORKTREE_ARCHIVE_CENTRAL_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARCHIVE_MANIFEST_KEY = WORKTREE_ARCHIVE_MANIFEST.casefold()


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 0
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0
    return info


def _read_zip_bounds(source: Path) -> tuple[int, int]:
    """Read bounded classic ZIP central-directory metadata before ZipFile."""
    size = source.stat().st_size
    if size < 22:
        raise ValueError("Worktree archive is too small to be a ZIP file")
    tail_size = min(size, 22 + 0xFFFF)
    try:
        with source.open("rb") as stream:
            stream.seek(size - tail_size)
            tail = stream.read(tail_size)
    except OSError as exc:
        raise ValueError("Worktree archive could not be read") from exc
    marker = tail.rfind(b"PK\x05\x06")
    if marker < 0 or marker + 22 > len(tail):
        raise ValueError("Worktree archive has no bounded ZIP end record")
    (_signature, disk, central_disk, entries_disk, entries, central_size,
     central_offset, comment_size) = struct.unpack_from(
         "<4s4H2LH", tail, marker)
    if (disk != 0 or central_disk != 0 or entries_disk != entries
            or comment_size != len(tail) - marker - 22):
        raise ValueError("Worktree archive uses unsupported ZIP layout")
    if (entries == 0xFFFF or central_size == 0xFFFFFFFF
            or central_offset == 0xFFFFFFFF):
        raise ValueError("ZIP64 worktree archives are not supported")
    if entries > MAX_WORKTREE_ARCHIVE_ENTRIES:
        raise ValueError("Worktree archive contains too many entries")
    if central_size > MAX_WORKTREE_ARCHIVE_CENTRAL_BYTES:
        raise ValueError("Worktree archive central directory is oversized")
    end_record_offset = size - tail_size + marker
    if (central_offset > end_record_offset
            or central_size > end_record_offset - central_offset):
        raise ValueError("Worktree archive central directory is out of bounds")
    return entries, central_size


def _archive_issue(issues: list, message: str, line: int, path: str,
                   max_issues: int) -> bool:
    if len(issues) < max_issues:
        issues.append({"line": line, "path": path, "problem": message})
        return False
    return True


def _parse_manifest(payload: bytes, *, max_issues: int) -> dict:
    """Parse the archive's bounded JSONL worktree manifest."""
    issues = []
    truncated = False
    expected = {}
    expected_keys = set()
    expected_bytes = 0
    header_valid = False
    summary_valid = False
    phase = "header"
    records = 0

    def issue(message: str, line: int = 0, path: str = ""):
        nonlocal truncated
        truncated = _archive_issue(
            issues, message, line, path, max_issues) or truncated

    position = 0
    while position < len(payload):
        newline = payload.find(b"\n", position)
        if newline < 0:
            raw = payload[position:]
            position = len(payload)
        else:
            raw = payload[position:newline + 1]
            position = newline + 1
        records += 1
        if len(raw) > MAX_WORKTREE_MANIFEST_LINE_BYTES:
            issue("record exceeds the 2 KiB line limit", records)
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
            if set(record) != {"record", "file_count", "total_bytes", "complete"}:
                issue("summary record fields are invalid", records)
            else:
                file_count = record.get("file_count")
                total_bytes = record.get("total_bytes")
                if (type(file_count) is not int
                        or file_count != len(expected)
                        or type(total_bytes) is not int
                        or total_bytes < 0
                        or total_bytes > MAX_WORKTREE_TOTAL_BYTES
                        or total_bytes != expected_bytes
                        or record.get("complete") is not True):
                    issue("summary does not match manifest records", records)
                else:
                    summary_valid = True
            phase = "complete"
            continue
        issue("records are present after the completion summary", records)

    if not header_valid:
        issue("missing or invalid worktree manifest header")
    if not summary_valid:
        issue("missing or invalid worktree manifest summary", records)
    return {
        "valid": bool(header_valid and summary_valid and not issues),
        "records": records,
        "manifest_files": len(expected),
        "manifest_bytes": expected_bytes,
        "expected": expected,
        "issues": issues,
        "truncated": truncated,
    }


def _is_unsafe_zip_entry(info: zipfile.ZipInfo) -> bool:
    if info.flag_bits & 0x1:
        return True
    if info.external_attr & 0x400:
        return True
    if info.create_system == 3:
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG}:
            return True
    return False


def export_worktree_archive(root: str | Path, destination: str | Path) -> Path:
    """Stream a regular-file-only worktree into a new bounded ZIP archive."""
    worktree = _root(root)
    target = _destination(worktree, destination)
    fd, name = tempfile.mkstemp(
        prefix=".forgeos-worktree-archive-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    staging = Path(name)
    count = 0
    total = 0
    written = 0
    manifest = io.StringIO()
    try:
        with zipfile.ZipFile(
                staging, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=6, allowZip64=False) as archive:
            written = _record(manifest, {
                "record": "header", "format": WORKTREE_MANIFEST_FORMAT},
                written)
            for relative, path, info in _iter_files(worktree):
                if relative.casefold() == _ARCHIVE_MANIFEST_KEY:
                    raise ValueError("Worktree contains a reserved archive name")
                count += 1
                if count > MAX_WORKTREE_FILES:
                    raise RuntimeError("Worktree archive exceeds file limit")
                if info.st_size > MAX_WORKTREE_FILE_BYTES:
                    raise ValueError("Worktree file exceeds size limit")
                if total + info.st_size > MAX_WORKTREE_TOTAL_BYTES:
                    raise RuntimeError("Worktree archive exceeds byte limit")
                entry = _zip_info(relative)
                with archive.open(entry, "w") as output:
                    size, digest = _stream_file(path, info, output.write)
                if size != info.st_size:
                    raise ValueError("Worktree file changed during archiving")
                total += size
                if total > MAX_WORKTREE_TOTAL_BYTES:
                    raise RuntimeError("Worktree archive exceeds byte limit")
                written = _record(manifest, {
                    "record": "file", "path": relative,
                    "size": size, "sha256": digest}, written)
                if staging.stat().st_size > MAX_WORKTREE_ARCHIVE_BYTES:
                    raise RuntimeError("Worktree archive exceeds size limit")
            written = _record(manifest, {
                "record": "summary", "file_count": count,
                "total_bytes": total, "complete": True}, written)
            payload = manifest.getvalue().encode("utf-8")
            if len(payload) > MAX_WORKTREE_MANIFEST_BYTES:
                raise RuntimeError("Worktree archive manifest exceeds size limit")
            archive.writestr(_zip_info(WORKTREE_ARCHIVE_MANIFEST), payload)
        if staging.stat().st_size > MAX_WORKTREE_ARCHIVE_BYTES:
            raise RuntimeError("Worktree archive exceeds size limit")
        _publish_database_copy(staging, target)
        return target
    finally:
        staging.unlink(missing_ok=True)


def check_worktree_archive(source: str | Path,
                           *, max_issues: int = MAX_WORKTREE_ISSUES) -> dict:
    """Verify a source-only worktree archive without extracting or opening SQLite."""
    if (type(max_issues) is not int or isinstance(max_issues, bool)
            or not 1 <= max_issues <= MAX_WORKTREE_ISSUES):
        raise ValueError("Worktree issue limit must be 1..32")
    archive_path = Path(source).expanduser().absolute()
    info = _plain_info(archive_path, directory=False)
    if info.st_size > MAX_WORKTREE_ARCHIVE_BYTES:
        raise ValueError("Worktree archive is oversized")

    issues = []
    truncated = False
    archive_entries = {}
    archive_keys = set()
    manifest_info = None
    manifest_bytes = 0
    actual_bytes = 0
    files_checked = 0
    central_entries = 0
    central_size = 0

    def issue(message: str, line: int = 0, path: str = ""):
        nonlocal truncated
        truncated = _archive_issue(
            issues, message, line, path, max_issues) or truncated

    try:
        central_entries, central_size = _read_zip_bounds(archive_path)
    except ValueError as exc:
        issue(str(exc))
        return {
            "valid": False,
            "format": "",
            "archive_entries": 0,
            "manifest_files": 0,
            "files_checked": 0,
            "bytes_checked": 0,
            "issues": issues,
            "truncated": truncated,
        }

    parsed = None
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) != central_entries:
                issue("ZIP central-directory entry count is inconsistent")
            if len(infos) > MAX_WORKTREE_ARCHIVE_ENTRIES:
                issue("Worktree archive contains too many entries")
            for entry in infos:
                name = entry.filename
                if (name.casefold() == _ARCHIVE_MANIFEST_KEY
                        and name != WORKTREE_ARCHIVE_MANIFEST):
                    issue("archive path conflicts with its reserved manifest", 0,
                          name)
                    continue
                if name == WORKTREE_ARCHIVE_MANIFEST:
                    if manifest_info is not None:
                        issue("archive contains duplicate manifests", 0, name)
                    elif entry.is_dir() or _is_unsafe_zip_entry(entry):
                        issue("archive manifest entry is unsafe", 0, name)
                    elif entry.file_size > MAX_WORKTREE_MANIFEST_BYTES:
                        issue("archive manifest is oversized", 0, name)
                    else:
                        manifest_info = entry
                    continue
                try:
                    relative = _relative(name)
                except ValueError as exc:
                    issue(str(exc), 0, name)
                    continue
                if name != relative:
                    issue("archive path is not canonical", 0, name)
                    continue
                if entry.is_dir() or _is_unsafe_zip_entry(entry):
                    issue("archive contains an unsafe entry", 0, relative)
                    continue
                if entry.file_size > MAX_WORKTREE_FILE_BYTES:
                    issue("archive file exceeds size limit", 0, relative)
                    continue
                if entry.compress_size > MAX_WORKTREE_ARCHIVE_BYTES:
                    issue("archive compressed entry exceeds size limit", 0, relative)
                    continue
                key = relative.casefold()
                if key in archive_keys:
                    issue("archive contains duplicate file paths", 0, relative)
                    continue
                if len(archive_entries) >= MAX_WORKTREE_FILES:
                    issue("archive contains too many files", 0, relative)
                    continue
                archive_entries[relative] = entry
                archive_keys.add(key)
                actual_bytes += entry.file_size
                if actual_bytes > MAX_WORKTREE_TOTAL_BYTES:
                    issue("archive exceeds total file size limit", 0, relative)
            if manifest_info is None:
                issue("archive is missing its worktree manifest")
            else:
                try:
                    with archive.open(manifest_info, "r") as embedded:
                        payload = embedded.read(MAX_WORKTREE_MANIFEST_BYTES + 1)
                    manifest_bytes = len(payload)
                    if manifest_bytes > MAX_WORKTREE_MANIFEST_BYTES:
                        issue("archive manifest is oversized", 0,
                              WORKTREE_ARCHIVE_MANIFEST)
                    else:
                        parsed = _parse_manifest(payload, max_issues=max_issues)
                        for problem in parsed["issues"]:
                            issue(problem["problem"], problem["line"],
                                  problem["path"])
                        if any(path.casefold() == _ARCHIVE_MANIFEST_KEY
                               for path in parsed["expected"]):
                            issue("manifest lists its reserved archive name", 0,
                                  WORKTREE_ARCHIVE_MANIFEST)
                except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
                    issue(f"archive manifest could not be read: {exc}", 0,
                          WORKTREE_ARCHIVE_MANIFEST)
            if parsed is not None and parsed["valid"] and manifest_info is not None:
                expected = parsed["expected"]
                for relative, entry in archive_entries.items():
                    if relative not in expected:
                        issue("archive contains an unlisted file", 0, relative)
                        continue
                    expected_size, expected_digest = expected[relative]
                    digest = hashlib.sha256()
                    size = 0
                    try:
                        with archive.open(entry, "r") as embedded:
                            while True:
                                block = embedded.read(HASH_CHUNK_BYTES)
                                if not block:
                                    break
                                size += len(block)
                                if size > MAX_WORKTREE_FILE_BYTES:
                                    raise ValueError("archive file exceeds size limit")
                                digest.update(block)
                    except (OSError, RuntimeError, EOFError, ValueError,
                            zipfile.BadZipFile) as exc:
                        issue(f"archive file could not be verified: {exc}", 0,
                              relative)
                        continue
                    files_checked += 1
                    if size != expected_size or digest.hexdigest() != expected_digest:
                        issue("archive file does not match its manifest", 0,
                              relative)
                    expected.pop(relative)
                for relative in sorted(expected):
                    issue("manifest file is missing from the archive", 0, relative)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile,
            zipfile.LargeZipFile) as exc:
        issue(f"worktree archive could not be checked: {exc}")

    parsed_valid = bool(parsed is not None and parsed["valid"])
    valid = bool(
        parsed_valid and manifest_info is not None and not issues
        and len(archive_entries) == parsed["manifest_files"]
        and actual_bytes == parsed["manifest_bytes"])
    return {
        "valid": valid,
        "format": WORKTREE_ARCHIVE_FORMAT if valid else "",
        "archive_entries": len(archive_entries),
        "central_entries": central_entries,
        "central_bytes": central_size,
        "manifest_bytes": manifest_bytes,
        "manifest_files": parsed["manifest_files"] if parsed else 0,
        "files_checked": files_checked,
        "bytes_checked": parsed["manifest_bytes"] if valid else 0,
        "issues": issues,
        "truncated": truncated,
    }
