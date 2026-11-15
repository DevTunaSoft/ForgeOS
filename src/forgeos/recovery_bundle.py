"""Bounded integrity indexes for separately published recovery artifacts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .core import (
    BACKUP_MANIFEST_FORMAT,
    JOURNAL_HANDOFF_MANIFEST_FORMAT,
    MAX_BACKUP_MANIFEST_BYTES,
    MAX_JOURNAL_HANDOFF_LINE_BYTES,
    MAX_JOURNAL_HANDOFF_MANIFEST_BYTES,
    Store,
    _cleanup_private_staging,
    _database_sidecars,
    _hold_private_staging,
    _publication_directory_info,
    _verify_backup_manifest,
    _publish_database_copy,
)
from .diagnostics import MAX_ARCHIVE_BYTES, EvidenceStore
from .worktree_archive import (
    MAX_WORKTREE_ARCHIVE_BYTES,
    WORKTREE_ARCHIVE_FORMAT,
    check_worktree_archive,
)
from .worktree_manifest import (
    HASH_CHUNK_BYTES,
    MAX_WORKTREE_ISSUES,
    _plain_info,
    _same_file,
)


RECOVERY_BUNDLE_INDEX_FORMAT = "forgeos-recovery-bundle-index-v1"
MAX_RECOVERY_BUNDLE_INDEX_BYTES = 2 * 1024 * 1024
MAX_RECOVERY_BUNDLE_INDEX_LINE_BYTES = 4 * 1024
MAX_RECOVERY_BUNDLE_EVIDENCE_FILES = 4096
MAX_RECOVERY_BUNDLE_HANDOFF_BYTES = 64 * 1024 * 1024
MAX_RECOVERY_BUNDLE_EVIDENCE_FILE_BYTES = 64 * 1024 * 1024
MAX_RECOVERY_BUNDLE_EVIDENCE_BYTES = 256 * 1024 * 1024
MAX_RECOVERY_BUNDLE_DATABASE_BYTES = 512 * 1024 * 1024
MAX_RECOVERY_BUNDLE_ISSUES = MAX_WORKTREE_ISSUES
RECOVERY_BUNDLE_DATABASE_FORMAT = "forgeos-sqlite-backup-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FILENAME = re.compile(r"^[^\\/:*?\"<>|\x00]{1,255}$")


def _artifact_digest(path: Path, *, max_bytes: int, consumer=None) -> tuple[int, str]:
    info = _plain_info(path, directory=False)
    if info.st_size > max_bytes:
        raise ValueError("Recovery artifact exceeds its size limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("Recovery artifact could not be opened safely") from exc
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(descriptor)
        if (not _same_file(info, opened)
                or not os.path.isfile(path)
                or getattr(opened, "st_nlink", 1) != 1):
            raise ValueError("Recovery artifact changed or became a link")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            while True:
                block = source.read(HASH_CHUNK_BYTES)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise ValueError("Recovery artifact exceeds its size limit")
                if consumer is not None:
                    consumer(block)
                digest.update(block)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("Recovery artifact could not be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise ValueError("Recovery artifact changed during hashing") from exc
    if not _same_file(info, after):
        raise ValueError("Recovery artifact changed during hashing")
    return size, digest.hexdigest()


def _regular_file(path: str | Path, *, max_bytes: int) -> Path:
    try:
        value = Path(path).expanduser().absolute()
    except TypeError as exc:
        raise ValueError("Recovery artifact path is invalid") from exc
    _artifact_digest(value, max_bytes=max_bytes)
    return value


def _database_backup(path: str | Path) -> Path:
    """Validate a standalone SQLite backup before adding it to a bundle."""
    value = _regular_file(path, max_bytes=MAX_RECOVERY_BUNDLE_DATABASE_BYTES)
    if any(sidecar.exists() or sidecar.is_symlink()
           for sidecar in _database_sidecars(value)):
        raise ValueError(
            "Recovery database backup must not have SQLite sidecars")
    try:
        Store.inspect_existing_database(value)
    except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
        raise ValueError(
            "Recovery database backup is not a valid ForgeOS database") from exc
    return value


def _filename(path: Path) -> str:
    name = path.name
    if not _FILENAME.fullmatch(name) or name in {".", ".."}:
        raise ValueError("Recovery artifact filename is invalid")
    return name


def _record_filename(value) -> str:
    if not isinstance(value, str) or not _FILENAME.fullmatch(value):
        raise ValueError("Recovery artifact filename is invalid")
    return value


def _new_destination(destination: str | Path,
                     inputs: tuple[Path, ...]) -> Path:
    try:
        target = Path(destination).expanduser().absolute()
    except TypeError as exc:
        raise ValueError("Recovery bundle index destination is invalid") from exc
    if (not target.parent.is_dir() or target.parent.is_symlink()
            or target.is_symlink() or target.exists()
            or target in inputs):
        raise ValueError(
            "Recovery bundle index must be a new file in an existing directory")
    return target


def _write_record(output, record: dict, written: int) -> int:
    encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_RECOVERY_BUNDLE_INDEX_LINE_BYTES:
        raise RuntimeError("Recovery bundle index record exceeds line limit")
    if written + len(encoded) > MAX_RECOVERY_BUNDLE_INDEX_BYTES:
        raise RuntimeError("Recovery bundle index exceeds size limit")
    output.write(encoded.decode("utf-8"))
    return written + len(encoded)


def _handoff_evidence_refs(handoff: Path) -> tuple[int, list[str]]:
    if handoff.stat().st_size > MAX_RECOVERY_BUNDLE_HANDOFF_BYTES:
        raise ValueError("Journal handoff exceeds bundle index size limit")
    task_id = None
    references = set()
    try:
        stream = handoff.open("rb")
    except OSError as exc:
        raise ValueError("Journal handoff could not be opened") from exc
    line_number = 0
    try:
        while True:
            raw = stream.readline(MAX_JOURNAL_HANDOFF_LINE_BYTES + 1)
            if not raw:
                break
            line_number += 1
            if len(raw) > MAX_JOURNAL_HANDOFF_LINE_BYTES:
                raise ValueError("Journal handoff contains an oversized record")
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Journal handoff contains invalid JSON") from exc
            if not isinstance(record, dict):
                raise ValueError("Journal handoff record is not an object")
            if record.get("record") == "header":
                task_id = record.get("task_id")
            elif record.get("record") == "event":
                reference = record.get("evidence_ref")
                if reference:
                    if not isinstance(reference, str) or not _SHA256.fullmatch(reference):
                        raise ValueError("Journal handoff contains an invalid evidence reference")
                    references.add(reference)
                    if len(references) > MAX_RECOVERY_BUNDLE_EVIDENCE_FILES:
                        raise ValueError("Recovery bundle index contains too many evidence files")
    finally:
        stream.close()
    if type(task_id) is not int or task_id < 1:
        raise ValueError("Journal handoff task identity is unavailable")
    return task_id, sorted(references)


def _check_handoff(handoff: Path, manifest: Path | None,
                   evidence_directory: Path | None) -> tuple[dict, list[str]]:
    report = Store.check_handoff(
        handoff, manifest=manifest, evidence_directory=evidence_directory)
    if not report["valid"]:
        raise ValueError("Journal handoff is invalid for a recovery bundle index")
    task_id, references = _handoff_evidence_refs(handoff)
    if report.get("task_id") != task_id:
        raise ValueError("Journal handoff task identity changed during indexing")
    if references and evidence_directory is None:
        raise ValueError(
            "Evidence directory is required for referenced recovery evidence")
    return report, references


def export_recovery_bundle_index(
        handoff: str | Path, worktree_archive: str | Path,
        destination: str | Path, *,
        manifest: str | Path | None = None,
        evidence_directory: str | Path | None = None,
        database: str | Path | None = None,
        database_manifest: str | Path | None = None) -> Path:
    """Bind recovery artifacts and an optional verified database backup."""
    handoff_path = _regular_file(
        handoff, max_bytes=MAX_RECOVERY_BUNDLE_HANDOFF_BYTES)
    archive_path = _regular_file(
        worktree_archive, max_bytes=MAX_WORKTREE_ARCHIVE_BYTES)
    manifest_path = None
    if manifest is not None:
        manifest_path = _regular_file(
            manifest, max_bytes=MAX_JOURNAL_HANDOFF_MANIFEST_BYTES)
    database_path = None
    database_manifest_path = None
    if database_manifest is not None and database is None:
        raise ValueError("Database manifest requires a database backup")
    if database is not None:
        database_path = _database_backup(database)
        if database_manifest is not None:
            database_manifest_path = _regular_file(
                database_manifest, max_bytes=MAX_BACKUP_MANIFEST_BYTES)
            try:
                _verify_backup_manifest(database_path, database_manifest_path)
            except (OSError, RuntimeError, ValueError) as exc:
                raise ValueError(
                    "Recovery database backup does not match its manifest") from exc
    evidence_root = None
    if evidence_directory is not None:
        evidence_root = Path(evidence_directory).expanduser().absolute()
        if evidence_root.is_symlink() or not evidence_root.is_dir():
            raise ValueError("Evidence directory is unavailable or unsafe")

    inputs = tuple(path for path in (
        handoff_path, archive_path, manifest_path, database_path,
        database_manifest_path) if path is not None)
    target = _new_destination(destination, inputs)
    handoff_report, references = _check_handoff(
        handoff_path, manifest_path, evidence_root)
    archive_report = check_worktree_archive(archive_path)
    if not archive_report["valid"]:
        raise ValueError("Worktree archive is invalid for a recovery bundle index")
    if references and evidence_root is None:
        raise ValueError(
            "Evidence directory is required for referenced recovery evidence")

    handoff_size, handoff_digest = _artifact_digest(
        handoff_path, max_bytes=MAX_RECOVERY_BUNDLE_HANDOFF_BYTES)
    archive_size, archive_digest = _artifact_digest(
        archive_path, max_bytes=MAX_WORKTREE_ARCHIVE_BYTES)
    manifest_size = manifest_digest = None
    if manifest_path is not None:
        manifest_size, manifest_digest = _artifact_digest(
            manifest_path, max_bytes=MAX_JOURNAL_HANDOFF_MANIFEST_BYTES)
    database_size = database_digest = None
    if database_path is not None:
        database_size, database_digest = _artifact_digest(
            database_path, max_bytes=MAX_RECOVERY_BUNDLE_DATABASE_BYTES)
    database_manifest_size = database_manifest_digest = None
    if database_manifest_path is not None:
        database_manifest_size, database_manifest_digest = _artifact_digest(
            database_manifest_path, max_bytes=MAX_BACKUP_MANIFEST_BYTES)

    evidence_records = []
    if evidence_root is not None:
        for reference in references:
            location = evidence_root / (reference + ".txt.gz")
            uncompressed_bytes = EvidenceStore.verify_at(evidence_root, reference)
            compressed_size, compressed_digest = _artifact_digest(
                location, max_bytes=MAX_RECOVERY_BUNDLE_EVIDENCE_FILE_BYTES)
            evidence_records.append({
                "record": "evidence",
                "filename": location.name,
                "reference": reference,
                "bytes": uncompressed_bytes,
                "compressed_size": compressed_size,
                "compressed_sha256": compressed_digest,
            })

    parent_info = _publication_directory_info(target.parent)
    fd, name = tempfile.mkstemp(
        prefix=".forgeos-recovery-index-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    staging = Path(name)
    staging_identity = None
    written = 0
    try:
        staging_identity = _hold_private_staging(
            staging, "Recovery bundle index staging")
        with staging.open("w", encoding="utf-8", newline="\n") as output:
            written = _write_record(output, {
                "record": "header", "format": RECOVERY_BUNDLE_INDEX_FORMAT},
                written)
            written = _write_record(output, {
                "record": "handoff", "filename": _filename(handoff_path),
                "size": handoff_size, "sha256": handoff_digest}, written)
            if manifest_path is not None:
                written = _write_record(output, {
                    "record": "handoff-manifest",
                    "filename": _filename(manifest_path),
                    "format": JOURNAL_HANDOFF_MANIFEST_FORMAT,
                    "size": manifest_size, "sha256": manifest_digest,
                }, written)
            written = _write_record(output, {
                "record": "worktree-archive",
                "filename": _filename(archive_path),
                "format": archive_report["format"] or WORKTREE_ARCHIVE_FORMAT,
                "size": archive_size, "sha256": archive_digest,
            }, written)
            if database_path is not None:
                written = _write_record(output, {
                    "record": "database",
                    "filename": _filename(database_path),
                    "format": RECOVERY_BUNDLE_DATABASE_FORMAT,
                    "size": database_size, "sha256": database_digest,
                }, written)
            if database_manifest_path is not None:
                written = _write_record(output, {
                    "record": "database-manifest",
                    "filename": _filename(database_manifest_path),
                    "format": BACKUP_MANIFEST_FORMAT,
                    "size": database_manifest_size,
                    "sha256": database_manifest_digest,
                }, written)
            for record in evidence_records:
                written = _write_record(output, record, written)
            written = _write_record(output, {
                "record": "summary", "task_id": handoff_report["task_id"],
                "evidence_count": len(evidence_records), "complete": True,
            }, written)
            output.flush()
            os.fsync(output.buffer.fileno())
        _publish_database_copy(staging, target)
        return target
    finally:
        _cleanup_private_staging(
            staging, staging_identity, parent_info,
            "Recovery bundle index staging")


def _index_issue(issues: list, message: str, line: int, path: str,
                 max_issues: int) -> bool:
    if len(issues) < max_issues:
        issues.append({"line": line, "path": path, "problem": message})
        return False
    return True


def _validate_digest_record(record: dict, fields: set[str], *, line: int,
                            issues: list, max_issues: int,
                            max_size: int, label: str) -> bool:
    if set(record) != fields:
        return False
    filename = record.get("filename")
    size = record.get("size")
    digest = record.get("sha256")
    try:
        _record_filename(filename)
    except (TypeError, ValueError):
        _index_issue(issues, f"{label} filename is invalid", line, "", max_issues)
        return False
    if (type(size) is not int or size < 0 or size > max_size
            or not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
        _index_issue(issues, f"{label} digest fields are invalid", line,
                     filename, max_issues)
        return False
    return True


def check_recovery_bundle_index(
        source: str | Path, handoff: str | Path,
        worktree_archive: str | Path, *,
        manifest: str | Path | None = None,
        evidence_directory: str | Path | None = None,
        database: str | Path | None = None,
        database_manifest: str | Path | None = None,
        max_issues: int = MAX_RECOVERY_BUNDLE_ISSUES) -> dict:
    """Verify a recovery bundle index and every separately stored artifact."""
    if (type(max_issues) is not int or isinstance(max_issues, bool)
            or not 1 <= max_issues <= MAX_RECOVERY_BUNDLE_ISSUES):
        raise ValueError("Recovery bundle issue limit must be 1..32")
    index = _regular_file(source, max_bytes=MAX_RECOVERY_BUNDLE_INDEX_BYTES)
    handoff_path = Path(handoff).expanduser().absolute()
    archive_path = Path(worktree_archive).expanduser().absolute()
    manifest_path = (Path(manifest).expanduser().absolute()
                     if manifest is not None else None)
    evidence_root = (Path(evidence_directory).expanduser().absolute()
                     if evidence_directory is not None else None)
    database_path = (Path(database).expanduser().absolute()
                     if database is not None else None)
    database_manifest_path = (Path(database_manifest).expanduser().absolute()
                              if database_manifest is not None else None)
    issues = []
    truncated = False
    records = 0
    phase = "header"
    header_valid = False
    summary_valid = False
    complete = False
    task_id = None
    handoff_record = None
    manifest_record = None
    archive_record = None
    database_record = None
    database_manifest_record = None
    evidence_records = {}

    def issue(message: str, line: int = 0, path: str = ""):
        nonlocal truncated
        truncated = _index_issue(issues, message, line, path, max_issues) or truncated

    try:
        stream = index.open("rb")
    except OSError as exc:
        raise ValueError("Recovery bundle index could not be opened") from exc
    try:
        while True:
            raw = stream.readline(MAX_RECOVERY_BUNDLE_INDEX_LINE_BYTES + 1)
            if not raw:
                break
            records += 1
            if len(raw) > MAX_RECOVERY_BUNDLE_INDEX_LINE_BYTES:
                issue("record exceeds the 4 KiB line limit", records)
                while raw and not raw.endswith(b"\n"):
                    raw = stream.readline(MAX_RECOVERY_BUNDLE_INDEX_LINE_BYTES + 1)
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
                        or record.get("format") != RECOVERY_BUNDLE_INDEX_FORMAT):
                    issue("invalid recovery bundle index header", records)
                else:
                    header_valid = True
                phase = "artifacts"
                continue
            if phase == "artifacts":
                if kind == "handoff":
                    if handoff_record is not None:
                        issue("duplicate handoff record", records)
                    elif _validate_digest_record(
                            record, {"record", "filename", "size", "sha256"},
                            line=records, issues=issues, max_issues=max_issues,
                            max_size=MAX_RECOVERY_BUNDLE_HANDOFF_BYTES,
                            label="Handoff"):
                        handoff_record = record
                    continue
                if kind == "handoff-manifest":
                    if manifest_record is not None:
                        issue("duplicate handoff manifest record", records)
                    elif (set(record) == {
                            "record", "filename", "format", "size", "sha256"}
                            and record.get("format") == JOURNAL_HANDOFF_MANIFEST_FORMAT
                            and _validate_digest_record(
                                record, {"record", "filename", "format", "size", "sha256"},
                                line=records, issues=issues, max_issues=max_issues,
                                max_size=MAX_JOURNAL_HANDOFF_MANIFEST_BYTES,
                                label="Handoff manifest")):
                        manifest_record = record
                    else:
                        issue("handoff manifest record is invalid", records)
                    continue
                if kind == "worktree-archive":
                    if archive_record is not None:
                        issue("duplicate worktree archive record", records)
                    elif (set(record) == {
                            "record", "filename", "format", "size", "sha256"}
                            and record.get("format") == WORKTREE_ARCHIVE_FORMAT
                            and _validate_digest_record(
                                record, {"record", "filename", "format", "size", "sha256"},
                                line=records, issues=issues, max_issues=max_issues,
                                max_size=MAX_WORKTREE_ARCHIVE_BYTES,
                                label="Worktree archive")):
                        archive_record = record
                    else:
                        issue("worktree archive record is invalid", records)
                    continue
                if kind == "database":
                    if database_record is not None:
                        issue("duplicate database record", records)
                    elif (record.get("format") == RECOVERY_BUNDLE_DATABASE_FORMAT
                          and _validate_digest_record(
                              record,
                              {"record", "filename", "format", "size", "sha256"},
                              line=records, issues=issues,
                              max_issues=max_issues,
                              max_size=MAX_RECOVERY_BUNDLE_DATABASE_BYTES,
                              label="Database backup")):
                        database_record = record
                    else:
                        issue("database record is invalid", records)
                    continue
                if kind == "database-manifest":
                    if database_manifest_record is not None:
                        issue("duplicate database manifest record", records)
                    elif (record.get("format") == BACKUP_MANIFEST_FORMAT
                          and _validate_digest_record(
                              record,
                              {"record", "filename", "format", "size", "sha256"},
                              line=records, issues=issues,
                              max_issues=max_issues,
                              max_size=MAX_BACKUP_MANIFEST_BYTES,
                              label="Database backup manifest")):
                        database_manifest_record = record
                    else:
                        issue("database manifest record is invalid", records)
                    continue
                if kind == "evidence":
                    fields = {
                        "record", "filename", "reference", "bytes",
                        "compressed_size", "compressed_sha256"}
                    if set(record) != fields:
                        issue("evidence record fields are invalid", records)
                        continue
                    reference = record.get("reference")
                    filename = record.get("filename")
                    if (not isinstance(reference, str)
                            or not _SHA256.fullmatch(reference)
                            or filename != reference + ".txt.gz"):
                        issue("evidence record identity is invalid", records)
                        continue
                    try:
                        _record_filename(filename)
                    except (TypeError, ValueError):
                        issue("evidence filename is invalid", records, filename)
                        continue
                    if (type(record.get("bytes")) is not int
                            or record["bytes"] < 0
                            or record["bytes"] > MAX_ARCHIVE_BYTES
                            or type(record.get("compressed_size")) is not int
                            or record["compressed_size"] < 0
                            or record["compressed_size"] > MAX_RECOVERY_BUNDLE_EVIDENCE_FILE_BYTES
                            or not isinstance(record.get("compressed_sha256"), str)
                            or not _SHA256.fullmatch(record["compressed_sha256"])):
                        issue("evidence record size or digest is invalid", records,
                              filename)
                        continue
                    if reference in evidence_records:
                        issue("duplicate evidence record", records, filename)
                        continue
                    if len(evidence_records) >= MAX_RECOVERY_BUNDLE_EVIDENCE_FILES:
                        issue("recovery bundle index contains too many evidence files",
                              records, filename)
                        continue
                    evidence_records[reference] = record
                    continue
                if kind != "summary":
                    issue("summary must follow artifact records", records)
                    continue
                fields = {"record", "task_id", "evidence_count", "complete"}
                if set(record) != fields:
                    issue("summary record fields are invalid", records)
                else:
                    task_id = record.get("task_id")
                    if (type(task_id) is not int or task_id < 1
                            or type(record.get("evidence_count")) is not int
                            or record["evidence_count"] != len(evidence_records)
                            or record.get("complete") is not True):
                        issue("summary does not match index records", records)
                    else:
                        summary_valid = True
                        complete = True
                phase = "complete"
                continue
            issue("records are present after the completion summary", records)
    finally:
        stream.close()

    if not header_valid:
        issue("missing or invalid recovery bundle index header")
    if handoff_record is None:
        issue("missing or invalid handoff record")
    if archive_record is None:
        issue("missing or invalid worktree archive record")
    if database_manifest_record is not None and database_record is None:
        issue("database manifest requires a database backup record")
    if not summary_valid:
        issue("missing or invalid recovery bundle index summary", records)

    artifacts_checked = 0
    if header_valid and summary_valid and not issues:
        try:
            if handoff_record["filename"] != _filename(handoff_path):
                issue("handoff path does not match its indexed filename", 0,
                      handoff_record["filename"])
            size, digest = _artifact_digest(
                handoff_path, max_bytes=MAX_RECOVERY_BUNDLE_HANDOFF_BYTES)
            if size != handoff_record["size"] or digest != handoff_record["sha256"]:
                issue("handoff does not match its indexed digest")
            else:
                artifacts_checked += 1

            if manifest_record is None:
                if manifest_path is not None:
                    issue("unexpected handoff manifest path")
            else:
                if manifest_path is None:
                    issue("indexed handoff manifest path is required")
                else:
                    size, digest = _artifact_digest(
                        manifest_path, max_bytes=MAX_JOURNAL_HANDOFF_MANIFEST_BYTES)
                    if (manifest_record["filename"] != _filename(manifest_path)
                            or size != manifest_record["size"]
                            or digest != manifest_record["sha256"]):
                        issue("handoff manifest does not match its index")
                    else:
                        artifacts_checked += 1

            if archive_record["filename"] != _filename(archive_path):
                issue("worktree archive path does not match its indexed filename")
            size, digest = _artifact_digest(
                archive_path, max_bytes=MAX_WORKTREE_ARCHIVE_BYTES)
            if size != archive_record["size"] or digest != archive_record["sha256"]:
                issue("worktree archive does not match its indexed digest")
            else:
                archive_report = check_worktree_archive(archive_path)
                if not archive_report["valid"]:
                    issue("worktree archive fails its integrity check")
                else:
                    artifacts_checked += 1

            if database_record is None:
                if database_path is not None:
                    issue("unexpected database backup path")
                if database_manifest_path is not None:
                    issue("unexpected database manifest path")
            else:
                if database_path is None:
                    issue("indexed database backup path is required")
                else:
                    try:
                        database_path = _database_backup(database_path)
                        size, digest = _artifact_digest(
                            database_path,
                            max_bytes=MAX_RECOVERY_BUNDLE_DATABASE_BYTES)
                        if (database_record["filename"] != _filename(database_path)
                                or size != database_record["size"]
                                or digest != database_record["sha256"]):
                            issue("database backup does not match its index")
                        else:
                            artifacts_checked += 1
                    except (OSError, RuntimeError, ValueError) as exc:
                        issue(str(exc), 0, database_record.get("filename", ""))

                if database_manifest_record is None:
                    if database_manifest_path is not None:
                        issue("unexpected database manifest path")
                elif database_manifest_path is None:
                    issue("indexed database manifest path is required")
                else:
                    try:
                        database_manifest_path = _regular_file(
                            database_manifest_path,
                            max_bytes=MAX_BACKUP_MANIFEST_BYTES)
                        size, digest = _artifact_digest(
                            database_manifest_path,
                            max_bytes=MAX_BACKUP_MANIFEST_BYTES)
                        if (database_manifest_record["filename"]
                                != _filename(database_manifest_path)
                                or size != database_manifest_record["size"]
                                or digest != database_manifest_record["sha256"]):
                            issue("database backup manifest does not match its index")
                        elif database_path is not None:
                            _verify_backup_manifest(
                                database_path, database_manifest_path)
                            artifacts_checked += 1
                    except (OSError, RuntimeError, ValueError) as exc:
                        issue(str(exc), 0,
                              database_manifest_record.get("filename", ""))

            if evidence_records and evidence_root is None:
                issue("evidence directory is required by the index")
            if evidence_root is not None and (
                    evidence_root.is_symlink() or not evidence_root.is_dir()):
                issue("evidence directory is unavailable or unsafe")
            if evidence_root is not None:
                for reference, record in sorted(evidence_records.items()):
                    location = evidence_root / (reference + ".txt.gz")
                    if location.name != record["filename"]:
                        issue("evidence path does not match its indexed filename", 0,
                              record["filename"])
                        continue
                    uncompressed = EvidenceStore.verify_at(evidence_root, reference)
                    size, digest = _artifact_digest(
                        location, max_bytes=MAX_RECOVERY_BUNDLE_EVIDENCE_FILE_BYTES)
                    if (uncompressed != record["bytes"]
                            or size != record["compressed_size"]
                            or digest != record["compressed_sha256"]):
                        issue("evidence does not match its indexed digest", 0,
                              record["filename"])
                    else:
                        artifacts_checked += 1

            handoff_report = Store.check_handoff(
                handoff_path,
                manifest=manifest_path,
                evidence_directory=evidence_root)
            if not handoff_report["valid"]:
                issue("handoff fails its integrity check")
            handoff_task_id, handoff_references = _handoff_evidence_refs(
                handoff_path)
            if handoff_report.get("task_id") != task_id or handoff_task_id != task_id:
                issue("handoff task ID does not match the index summary")
            if set(handoff_references) != set(evidence_records):
                issue("indexed evidence references do not match the handoff")
            if handoff_references and evidence_root is None:
                issue("evidence directory is required by the handoff")
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            issue(str(exc))

    return {
        "valid": bool(header_valid and summary_valid and complete and not issues),
        "complete": complete,
        "format": RECOVERY_BUNDLE_INDEX_FORMAT if header_valid else "",
        "task_id": task_id,
        "records": records,
        "evidence_files": len(evidence_records),
        "database_present": database_record is not None,
        "database_manifest_present": database_manifest_record is not None,
        "artifacts_checked": artifacts_checked,
        "issues": issues,
        "truncated": truncated,
    }
