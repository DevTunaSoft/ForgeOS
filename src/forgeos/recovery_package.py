"""Bounded ZIP packages for validated recovery artifacts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import tempfile
import zipfile

from .core import MAX_JOURNAL_HANDOFF_MANIFEST_BYTES, _publish_database_copy
from .recovery_bundle import (
    MAX_RECOVERY_BUNDLE_EVIDENCE_BYTES,
    MAX_RECOVERY_BUNDLE_EVIDENCE_FILE_BYTES,
    MAX_RECOVERY_BUNDLE_HANDOFF_BYTES,
    MAX_RECOVERY_BUNDLE_INDEX_BYTES,
    _artifact_digest,
    _filename,
    _new_destination,
    _record_filename,
    check_recovery_bundle_index,
    export_recovery_bundle_index,
)
from .worktree_archive import (
    HASH_CHUNK_BYTES,
    MAX_WORKTREE_ARCHIVE_BYTES,
    _is_unsafe_zip_entry,
)
from .worktree_manifest import _plain_info


RECOVERY_BUNDLE_FORMAT = "forgeos-recovery-bundle-v1"
RECOVERY_BUNDLE_HEADER_NAME = "recovery-bundle-header.json"
RECOVERY_BUNDLE_INDEX_NAME = "recovery-bundle-index.jsonl"
RECOVERY_BUNDLE_HANDOFF_PREFIX = "handoff/"
RECOVERY_BUNDLE_SOURCE_PREFIX = "source/"
RECOVERY_BUNDLE_MANIFEST_PREFIX = "manifest/"
RECOVERY_BUNDLE_EVIDENCE_PREFIX = "evidence/"
MAX_RECOVERY_BUNDLE_BYTES = 1024 * 1024 * 1024
MAX_RECOVERY_BUNDLE_ENTRIES = 4101
MAX_RECOVERY_BUNDLE_CENTRAL_BYTES = 8 * 1024 * 1024
MAX_RECOVERY_BUNDLE_HEADER_BYTES = 1024


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 0
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0
    return info


def _bundle_header() -> bytes:
    return (json.dumps({
        "format": RECOVERY_BUNDLE_FORMAT,
        "index": RECOVERY_BUNDLE_INDEX_NAME,
        "complete": True,
    }, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _index_rows(path: Path) -> list[dict]:
    if path.stat().st_size > MAX_RECOVERY_BUNDLE_INDEX_BYTES:
        raise ValueError("Recovery bundle index is oversized")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError("Recovery bundle index could not be read") from exc
    rows = []
    for raw in payload.splitlines():
        try:
            row = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Recovery bundle index is invalid") from exc
        if not isinstance(row, dict):
            raise ValueError("Recovery bundle index contains a non-object record")
        rows.append(row)
    return rows


def _row(rows: list[dict], kind: str) -> dict | None:
    matches = [row for row in rows if row.get("record") == kind]
    if len(matches) != 1:
        raise ValueError(f"Recovery bundle index must contain one {kind} record")
    return matches[0]


def _copy_to_zip(archive: zipfile.ZipFile, source: Path, name: str,
                 expected_size: int, expected_digest: str,
                 max_bytes: int) -> int:
    with archive.open(_zip_info(name), "w") as output:
        size, digest = _artifact_digest(
            source, max_bytes=max_bytes, consumer=output.write)
    if size != expected_size or digest != expected_digest:
        raise ValueError(f"Recovery artifact changed while packaging: {source.name}")
    return size


def export_recovery_bundle(
        handoff: str | Path, worktree_archive: str | Path,
        destination: str | Path, *, manifest: str | Path | None = None,
        evidence_directory: str | Path | None = None) -> Path:
    """Package validated recovery artifacts into one bounded ZIP."""
    handoff_path = Path(handoff).expanduser().absolute()
    archive_path = Path(worktree_archive).expanduser().absolute()
    manifest_path = (Path(manifest).expanduser().absolute()
                     if manifest is not None else None)
    inputs = tuple(path for path in (
        handoff_path, archive_path, manifest_path) if path is not None)
    target = _new_destination(destination, inputs)
    evidence_root = (Path(evidence_directory).expanduser().absolute()
                     if evidence_directory is not None else None)

    fd, name = tempfile.mkstemp(
        prefix=".forgeos-bundle-index-", suffix=".jsonl", dir=target.parent)
    os.close(fd)
    index_path = Path(name)
    index_path.unlink(missing_ok=True)
    staging_fd, staging_name = tempfile.mkstemp(
        prefix=".forgeos-recovery-bundle-", suffix=".tmp", dir=target.parent)
    os.close(staging_fd)
    staging = Path(staging_name)
    try:
        export_recovery_bundle_index(
            handoff_path, archive_path, index_path,
            manifest=manifest_path, evidence_directory=evidence_root)
        index_report = check_recovery_bundle_index(
            index_path, handoff_path, archive_path,
            manifest=manifest_path, evidence_directory=evidence_root)
        if not index_report["valid"]:
            raise ValueError("Recovery bundle index is invalid")
        rows = _index_rows(index_path)
        handoff_record = _row(rows, "handoff")
        archive_record = _row(rows, "worktree-archive")
        manifest_records = [
            row for row in rows if row.get("record") == "handoff-manifest"]
        if len(manifest_records) > 1:
            raise ValueError("Recovery bundle index contains duplicate manifests")
        evidence_records = [
            row for row in rows if row.get("record") == "evidence"]
        evidence_bytes = sum(row["compressed_size"] for row in evidence_records)
        if evidence_bytes > MAX_RECOVERY_BUNDLE_EVIDENCE_BYTES:
            raise ValueError("Recovery evidence exceeds bundle size limit")
        content_bytes = (
            handoff_record["size"] + archive_record["size"]
            + sum(row["compressed_size"] for row in evidence_records)
            + (manifest_records[0]["size"] if manifest_records else 0))
        if content_bytes > MAX_RECOVERY_BUNDLE_BYTES:
            raise ValueError("Recovery bundle contents exceed size limit")
        header = _bundle_header()
        if len(header) > MAX_RECOVERY_BUNDLE_HEADER_BYTES:
            raise RuntimeError("Recovery bundle header exceeds size limit")

        with zipfile.ZipFile(
                staging, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=6, allowZip64=False) as bundle:
            bundle.writestr(_zip_info(RECOVERY_BUNDLE_HEADER_NAME), header)
            with bundle.open(_zip_info(RECOVERY_BUNDLE_INDEX_NAME), "w") as output:
                _artifact_digest(
                    index_path, max_bytes=MAX_RECOVERY_BUNDLE_INDEX_BYTES,
                    consumer=output.write)
            _copy_to_zip(
                bundle, handoff_path,
                RECOVERY_BUNDLE_HANDOFF_PREFIX + handoff_record["filename"],
                handoff_record["size"], handoff_record["sha256"],
                MAX_RECOVERY_BUNDLE_HANDOFF_BYTES)
            if manifest_records:
                record = manifest_records[0]
                if manifest_path is None:
                    raise ValueError("Indexed handoff manifest is unavailable")
                _copy_to_zip(
                    bundle, manifest_path,
                    RECOVERY_BUNDLE_MANIFEST_PREFIX + record["filename"],
                    record["size"], record["sha256"],
                    MAX_JOURNAL_HANDOFF_MANIFEST_BYTES)
            _copy_to_zip(
                bundle, archive_path,
                RECOVERY_BUNDLE_SOURCE_PREFIX + archive_record["filename"],
                archive_record["size"], archive_record["sha256"],
                MAX_WORKTREE_ARCHIVE_BYTES)
            for record in evidence_records:
                if evidence_root is None:
                    raise ValueError("Indexed evidence directory is unavailable")
                location = evidence_root / record["filename"]
                _copy_to_zip(
                    bundle, location,
                    RECOVERY_BUNDLE_EVIDENCE_PREFIX + record["filename"],
                    record["compressed_size"], record["compressed_sha256"],
                    MAX_RECOVERY_BUNDLE_EVIDENCE_FILE_BYTES)
            if staging.stat().st_size > MAX_RECOVERY_BUNDLE_BYTES:
                raise RuntimeError("Recovery bundle exceeds size limit")
        if staging.stat().st_size > MAX_RECOVERY_BUNDLE_BYTES:
            raise RuntimeError("Recovery bundle exceeds size limit")
        report = check_recovery_bundle(staging)
        if not report["valid"]:
            raise RuntimeError("Recovery bundle self-check failed")
        _publish_database_copy(staging, target)
        return target
    finally:
        index_path.unlink(missing_ok=True)
        if staging is not None:
            staging.unlink(missing_ok=True)


def _zip_bounds(source: Path) -> tuple[int, int]:
    size = source.stat().st_size
    if size < 22:
        raise ValueError("Recovery bundle is too small to be a ZIP file")
    tail_size = min(size, 22 + 0xFFFF)
    with source.open("rb") as stream:
        stream.seek(size - tail_size)
        tail = stream.read(tail_size)
    marker = tail.rfind(b"PK\x05\x06")
    if marker < 0 or marker + 22 > len(tail):
        raise ValueError("Recovery bundle has no bounded ZIP end record")
    (_signature, disk, central_disk, entries_disk, entries, central_size,
     central_offset, comment_size) = struct.unpack_from(
         "<4s4H2LH", tail, marker)
    if (disk != 0 or central_disk != 0 or entries_disk != entries
            or comment_size != len(tail) - marker - 22):
        raise ValueError("Recovery bundle uses unsupported ZIP layout")
    if (entries == 0xFFFF or central_size == 0xFFFFFFFF
            or central_offset == 0xFFFFFFFF):
        raise ValueError("ZIP64 recovery bundles are not supported")
    if entries > MAX_RECOVERY_BUNDLE_ENTRIES:
        raise ValueError("Recovery bundle contains too many entries")
    if central_size > MAX_RECOVERY_BUNDLE_CENTRAL_BYTES:
        raise ValueError("Recovery bundle central directory is oversized")
    end_record_offset = size - tail_size + marker
    if (central_offset > end_record_offset
            or central_size > end_record_offset - central_offset):
        raise ValueError("Recovery bundle central directory is out of bounds")
    return entries, central_size


def _bundle_entry_kind(name: str) -> tuple[str, str | None]:
    if name in {RECOVERY_BUNDLE_HEADER_NAME, RECOVERY_BUNDLE_INDEX_NAME}:
        return name, None
    for prefix, kind in (
            (RECOVERY_BUNDLE_HANDOFF_PREFIX, "handoff"),
            (RECOVERY_BUNDLE_SOURCE_PREFIX, "source"),
            (RECOVERY_BUNDLE_MANIFEST_PREFIX, "manifest"),
            (RECOVERY_BUNDLE_EVIDENCE_PREFIX, "evidence")):
        if name.startswith(prefix) and name.count("/") == 1:
            filename = name[len(prefix):]
            try:
                _record_filename(filename)
            except ValueError:
                return "invalid", None
            return kind, filename
    return "invalid", None


def _materialize_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo,
                       destination: Path, max_bytes: int) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with archive.open(info, "r") as source, destination.open("xb") as output:
        while True:
            block = source.read(HASH_CHUNK_BYTES)
            if not block:
                break
            count += len(block)
            if count > max_bytes:
                raise ValueError("Recovery bundle entry exceeds size limit")
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    if count != info.file_size:
        raise ValueError("Recovery bundle entry size is inconsistent")
    return count


def check_recovery_bundle(source: str | Path,
                          *, max_issues: int = 32) -> dict:
    """Verify a recovery ZIP through private bounded temporary files."""
    if (type(max_issues) is not int or isinstance(max_issues, bool)
            or not 1 <= max_issues <= 32):
        raise ValueError("Recovery bundle issue limit must be 1..32")
    bundle = Path(source).expanduser().absolute()
    info = _plain_info(bundle, directory=False)
    if info.st_size > MAX_RECOVERY_BUNDLE_BYTES:
        raise ValueError("Recovery bundle is oversized")
    issues = []
    truncated = False

    def issue(message: str, path: str = ""):
        nonlocal truncated
        if len(issues) < max_issues:
            issues.append({"path": path, "problem": message})
        else:
            truncated = True

    try:
        entry_count, _central_size = _zip_bounds(bundle)
    except (OSError, ValueError) as exc:
        issue(str(exc))
        return {"valid": False, "complete": False,
                "format": "", "entries": 0, "issues": issues,
                "truncated": truncated}

    try:
        with zipfile.ZipFile(bundle, "r") as archive:
            infos = archive.infolist()
            if len(infos) != entry_count:
                issue("ZIP entry count is inconsistent")
            entries = {}
            evidence_total = 0
            raw_total = 0
            for info in infos:
                name = info.filename
                kind, filename = _bundle_entry_kind(name)
                if kind == "invalid":
                    issue("bundle entry name is invalid", name)
                    continue
                if name in entries:
                    issue("bundle contains a duplicate entry", name)
                    continue
                if info.is_dir() or _is_unsafe_zip_entry(info):
                    issue("bundle contains an unsafe entry", name)
                    continue
                if kind == RECOVERY_BUNDLE_HEADER_NAME:
                    max_bytes = MAX_RECOVERY_BUNDLE_HEADER_BYTES
                elif kind == RECOVERY_BUNDLE_INDEX_NAME:
                    max_bytes = MAX_RECOVERY_BUNDLE_INDEX_BYTES
                elif kind == "handoff":
                    max_bytes = MAX_RECOVERY_BUNDLE_HANDOFF_BYTES
                elif kind == "source":
                    max_bytes = MAX_WORKTREE_ARCHIVE_BYTES
                elif kind == "manifest":
                    max_bytes = MAX_JOURNAL_HANDOFF_MANIFEST_BYTES
                else:
                    max_bytes = MAX_RECOVERY_BUNDLE_EVIDENCE_FILE_BYTES
                    evidence_total += info.file_size
                    if evidence_total > MAX_RECOVERY_BUNDLE_EVIDENCE_BYTES:
                        issue("bundle evidence exceeds size limit", name)
                if info.file_size > max_bytes:
                    issue("bundle entry exceeds size limit", name)
                    continue
                raw_total += info.file_size
                if raw_total > MAX_RECOVERY_BUNDLE_BYTES:
                    issue("bundle contents exceed size limit", name)
                    continue
                entries[name] = info
            if len(entries) > MAX_RECOVERY_BUNDLE_ENTRIES:
                issue("bundle contains too many entries")
            required = {
                RECOVERY_BUNDLE_HEADER_NAME, RECOVERY_BUNDLE_INDEX_NAME}
            if not required.issubset(entries):
                issue("bundle is missing its header or index")
            handoffs = [name for name in entries if name.startswith(
                RECOVERY_BUNDLE_HANDOFF_PREFIX)]
            sources = [name for name in entries if name.startswith(
                RECOVERY_BUNDLE_SOURCE_PREFIX)]
            manifests = [name for name in entries if name.startswith(
                RECOVERY_BUNDLE_MANIFEST_PREFIX)]
            if len(handoffs) != 1:
                issue("bundle must contain exactly one handoff")
            if len(sources) != 1:
                issue("bundle must contain exactly one source archive")
            if len(manifests) > 1:
                issue("bundle contains duplicate handoff manifests")

            if RECOVERY_BUNDLE_HEADER_NAME in entries:
                header = archive.read(entries[RECOVERY_BUNDLE_HEADER_NAME])
                try:
                    header_value = json.loads(header.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    issue("bundle header is invalid", RECOVERY_BUNDLE_HEADER_NAME)
                else:
                    if header_value != {
                            "complete": True,
                            "format": RECOVERY_BUNDLE_FORMAT,
                            "index": RECOVERY_BUNDLE_INDEX_NAME}:
                        issue("bundle header fields are invalid",
                              RECOVERY_BUNDLE_HEADER_NAME)

            if issues:
                return {"valid": False, "complete": False,
                        "format": "", "entries": len(entries),
                        "issues": issues, "truncated": truncated}

            with tempfile.TemporaryDirectory(prefix=".forgeos-bundle-check-") as directory:
                root = Path(directory)
                index_path = root / RECOVERY_BUNDLE_INDEX_NAME
                handoff_path = root / handoffs[0].split("/", 1)[1]
                source_path = root / sources[0].split("/", 1)[1]
                manifest_path = None
                evidence_root = root / "evidence"
                _materialize_entry(
                    archive, entries[RECOVERY_BUNDLE_INDEX_NAME], index_path,
                    MAX_RECOVERY_BUNDLE_INDEX_BYTES)
                _materialize_entry(
                    archive, entries[handoffs[0]], handoff_path,
                    MAX_RECOVERY_BUNDLE_HANDOFF_BYTES)
                _materialize_entry(
                    archive, entries[sources[0]], source_path,
                    MAX_WORKTREE_ARCHIVE_BYTES)
                if manifests:
                    manifest_path = root / manifests[0].split("/", 1)[1]
                    _materialize_entry(
                        archive, entries[manifests[0]], manifest_path,
                        MAX_JOURNAL_HANDOFF_MANIFEST_BYTES)
                for name in entries:
                    if name.startswith(RECOVERY_BUNDLE_EVIDENCE_PREFIX):
                        target = evidence_root / name.split("/", 1)[1]
                        _materialize_entry(
                            archive, entries[name], target,
                            MAX_RECOVERY_BUNDLE_EVIDENCE_FILE_BYTES)
                index_report = check_recovery_bundle_index(
                    index_path, handoff_path, source_path,
                    manifest=manifest_path, evidence_directory=(
                        evidence_root if any(name.startswith(
                            RECOVERY_BUNDLE_EVIDENCE_PREFIX) for name in entries)
                        else None), max_issues=max_issues)
                if not index_report["valid"]:
                    return {
                        **index_report, "format": "", "entries": len(entries),
                    }
                rows = _index_rows(index_path)
                indexed_evidence = {
                    row["filename"] for row in rows
                    if row.get("record") == "evidence"
                }
                bundled_evidence = {
                    name.split("/", 1)[1] for name in entries
                    if name.startswith(RECOVERY_BUNDLE_EVIDENCE_PREFIX)
                }
                if indexed_evidence != bundled_evidence:
                    issue("bundle evidence entries do not match its index")
                    return {
                        **index_report,
                        "valid": False,
                        "complete": False,
                        "format": "",
                        "entries": len(entries),
                        "issues": issues,
                        "truncated": truncated,
                    }
                return {
                    **index_report,
                    "valid": True,
                    "complete": True,
                    "format": RECOVERY_BUNDLE_FORMAT,
                    "entries": len(entries),
                }
    except (OSError, RuntimeError, ValueError, EOFError,
            zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        issue(str(exc))
    return {"valid": False, "complete": False, "format": "",
            "entries": 0, "issues": issues, "truncated": truncated}
