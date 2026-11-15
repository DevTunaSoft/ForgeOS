"""Persistent task store and cautious, bounded development workflow."""
from __future__ import annotations
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time
from urllib.request import Request, urlopen

SCHEMA = """CREATE TABLE IF NOT EXISTS tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, description TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL, updated REAL NOT NULL, last_error TEXT NOT NULL DEFAULT '',
 result TEXT NOT NULL DEFAULT ''
);"""

LEASE_SCHEMA = """CREATE TABLE IF NOT EXISTS task_leases (
 task_id INTEGER PRIMARY KEY, owner TEXT NOT NULL,
 acquired REAL NOT NULL, heartbeat REAL NOT NULL
);"""

EVENT_SCHEMA = """CREATE TABLE IF NOT EXISTS forgeos_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id INTEGER,
 event_type TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT '',
 actor TEXT NOT NULL DEFAULT '',
 created REAL NOT NULL,
 details TEXT NOT NULL DEFAULT '',
 action TEXT NOT NULL DEFAULT '',
 attempt INTEGER,
 artifact_ref TEXT NOT NULL DEFAULT '',
 evidence_ref TEXT NOT NULL DEFAULT '',
 FOREIGN KEY(task_id) REFERENCES tasks(id)
);"""
EVENT_INDEX = """CREATE INDEX IF NOT EXISTS idx_forgeos_events_task_time
 ON forgeos_events(task_id, id);"""
EVENT_NO_UPDATE = """CREATE TRIGGER IF NOT EXISTS forgeos_events_no_update
 BEFORE UPDATE ON forgeos_events BEGIN
 SELECT RAISE(ABORT, 'ForgeOS event journal is append-only');
 END;"""

ACTIVATION_MARKER_FORMAT = "forgeos-db-activation-v1"
ACTIVATION_MARKER_LIMIT = 4096
ACTIVATION_PHASES = frozenset(
    ("prepared", "old_moved", "publishing", "published"))
ACTIVATION_PATH_FIELDS = ("source", "destination", "rollback", "staged")
ACTIVATION_BINDING_FIELDS = ("staged", "rollback")
DATABASE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
EVENT_NO_DELETE = """CREATE TRIGGER IF NOT EXISTS forgeos_events_no_delete
 BEFORE DELETE ON forgeos_events BEGIN
 SELECT RAISE(ABORT, 'ForgeOS event journal is append-only');
 END;"""

# Index only active tasks: past task history remains outside this small lookup.
# Non-unique so older databases with duplicate active tasks can migrate safely.
ACTIVE_TASK_LOOKUP_INDEX = """CREATE INDEX IF NOT EXISTS idx_forgeos_active_task_lookup
 ON tasks(project, description, id)
 WHERE status IN ('queued','planning','planned','editing','testing');"""

STATUSES = {'queued','planning','planned','editing','testing','passed','failed','blocked'}
DEFAULT_LEASE_SECONDS = 600
MAX_CHECKPOINT_BYTES = 64 * 1024
MAX_TASK_DESCRIPTION_BYTES = 16 * 1024
MAX_LOCAL_PROMPT_BYTES = 64 * 1024
CHECKPOINT_PAGE_SIZE = 16
MAX_EVENT_TYPE_BYTES = 64
MAX_EVENT_ACTOR_BYTES = 128
MAX_EVENT_DETAILS_BYTES = 2048
EVENT_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
MAX_EVENT_ACTION_BYTES = 64
MAX_EVENT_ARTIFACT_BYTES = 128
MAX_EVENT_EVIDENCE_BYTES = 64
EVENT_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]+$")
MAX_JOURNAL_ISSUES = 32
JOURNAL_HANDOFF_FORMAT = "forgeos-journal-handoff-v1"
MAX_JOURNAL_HANDOFF_LINE_BYTES = 16 * 1024
JOURNAL_HANDOFF_MANIFEST_FORMAT = "forgeos-journal-handoff-manifest-v1"
MAX_JOURNAL_HANDOFF_MANIFEST_BYTES = 1024
BACKUP_MANIFEST_FORMAT = "forgeos-sqlite-backup-v1"
MAX_BACKUP_MANIFEST_BYTES = 1024
_WINDOWS_REPARSE_POINT = 0x0400


class LeaseLostError(RuntimeError):
    """An expired, reclaimed, or concurrently changed task cannot be published."""


def _sync_publication_directory(directory: Path) -> None:
    """Flush a POSIX publication directory without changing Windows behavior."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _is_link_or_reparse(path: Path, info) -> bool:
    if (stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)):
        return True
    try:
        return path.is_symlink()
    except OSError:
        return True


def _publication_directory_info(path: Path):
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"Publication directory is missing: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot inspect publication directory: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or _is_link_or_reparse(path, info):
        raise ValueError(f"Publication directory must be real: {path}")
    return info


def _publication_file_info(path: Path, description: str, *,
                           require_unlinked: bool = False):
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"{description} is missing: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot inspect {description}: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or _is_link_or_reparse(path, info):
        raise ValueError(f"{description} must be a regular file: {path}")
    if require_unlinked and getattr(info, "st_nlink", 1) != 1:
        raise ValueError(f"{description} must be unlinked: {path}")
    return info


def _publication_target_info(target: Path):
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(
            f"Cannot inspect publication destination: {target}") from exc
    if _is_link_or_reparse(target, info) or not stat.S_ISREG(info.st_mode):
        raise ValueError(
            f"Publication destination must be a new regular file: {target}")
    raise ValueError(f"Publication destination already exists: {target}")


def _same_publication_file(before, after) -> bool:
    return all(getattr(before, field, None) == getattr(after, field, None)
               for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns"))


def _same_publication_identity(before, after) -> bool:
    return all(getattr(before, field, None) == getattr(after, field, None)
               for field in ("st_dev", "st_ino"))


def _database_source_info(path: Path, description: str):
    return _publication_file_info(path, description)


def _assert_database_source_unchanged(before, path: Path,
                                      description: str) -> None:
    try:
        after = _database_source_info(path, description)
    except ValueError as exc:
        raise ValueError(f"{description} changed during use: {path}") from exc
    if not _same_publication_file(before, after):
        raise ValueError(f"{description} changed during use: {path}")


def _publish_database_copy(staging: Path, target: Path) -> None:
    """Publish a staged SQLite copy without replacing a concurrent destination.

    Windows rename fails when the destination already exists, giving the same
    no-overwrite publication contract without relying on hard-link support.
    POSIX keeps the hard-link publication used by the existing same-volume
    atomic path.
    """
    staging_info = _publication_file_info(
        staging, "Publication staging", require_unlinked=True)
    parent_info = _publication_directory_info(target.parent)
    _publication_target_info(target)
    current_staging = _publication_file_info(
        staging, "Publication staging", require_unlinked=True)
    current_parent = _publication_directory_info(target.parent)
    if not _same_publication_file(staging_info, current_staging):
        raise ValueError(f"Publication staging changed: {staging}")
    if not _same_publication_identity(parent_info, current_parent):
        raise ValueError(f"Publication directory changed: {target.parent}")
    _publication_target_info(target)
    current_staging = _publication_file_info(
        staging, "Publication staging", require_unlinked=True)
    current_parent = _publication_directory_info(target.parent)
    if (not _same_publication_file(staging_info, current_staging)
            or not _same_publication_identity(parent_info, current_parent)):
        raise ValueError(f"Publication inputs changed: {target}")
    if os.name == "nt":
        os.rename(staging, target)
    else:
        os.link(staging, target)
    published_info = _publication_file_info(
        target, "Published artifact")
    if not _same_publication_file(staging_info, published_info):
        raise ValueError(f"Published artifact changed: {target}")
    current_parent = _publication_directory_info(target.parent)
    if not _same_publication_identity(parent_info, current_parent):
        raise ValueError(f"Publication directory changed: {target.parent}")
    if os.name != "nt":
        _sync_publication_directory(target.parent)


def _move_database_copy(source: Path, target: Path) -> None:
    """Move one same-directory database path without replacing its target."""
    if os.name == "nt":
        # Windows rename fails when target exists, preserving the rollback
        # destination under the same no-overwrite contract as publication.
        os.rename(source, target)
    else:
        # POSIX rename would replace an unexpected target. A hard link followed
        # by unlink is deliberately fail-closed if a concurrent target appears.
        os.link(source, target)
        os.unlink(source)


def _activation_marker_path(destination: Path) -> Path:
    try:
        return destination.with_name(
            destination.name + ".forgeos-activation.json")
    except ValueError as exc:
        raise ValueError("Activation destination must name a database file") from exc


def _database_sidecars(database: Path) -> tuple[Path, ...]:
    return tuple(Path(str(database) + suffix)
                 for suffix in DATABASE_SIDECAR_SUFFIXES)


def _read_activation_marker(marker: Path) -> dict:
    """Read one bounded activation marker without trusting its contents."""
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("Activation marker must be a regular file")
    try:
        with marker.open("rb") as stream:
            encoded = stream.read(ACTIVATION_MARKER_LIMIT + 1)
    except OSError as exc:
        raise ValueError("Activation marker could not be read") from exc
    if len(encoded) > ACTIVATION_MARKER_LIMIT:
        raise ValueError("Activation marker is oversized")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Activation marker is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Activation marker must contain an object")
    return payload


def _validate_activation_marker(marker: Path, payload: dict) -> dict[str, Path]:
    """Validate marker identity and return only its bounded path projection."""
    if (payload.get("format") != ACTIVATION_MARKER_FORMAT
            or payload.get("phase") not in ACTIVATION_PHASES):
        raise ValueError("Activation marker format or phase is invalid")
    paths = {}
    try:
        for field in ACTIVATION_PATH_FIELDS:
            value = payload.get(field)
            if type(value) is not str or not value:
                raise ValueError("Activation marker path is invalid")
            path = Path(value)
            if not path.is_absolute():
                raise ValueError("Activation marker paths must be absolute")
            paths[field] = path
        for field in ACTIVATION_BINDING_FIELDS:
            size = payload.get(f"{field}_size")
            digest = payload.get(f"{field}_sha256")
            if size is None and digest is None:
                continue
            if (type(size) is not int or size < 0
                    or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError("Activation marker database binding is invalid")
        target = paths["destination"]
        rollback = paths["rollback"]
        staged = paths["staged"]
        if marker != _activation_marker_path(target):
            raise ValueError("Activation marker does not match its destination")
        if (target == rollback or target == paths["source"]
                or target == staged or rollback == paths["source"]
                or rollback == staged or staged == paths["source"]):
            raise ValueError("Activation marker paths must be distinct")
        if (target.parent != rollback.parent or staged.parent != target.parent
                or not target.parent.is_dir()
                or target.parent.is_symlink()):
            raise ValueError("Activation marker directory is unsafe")
        if any(path.is_symlink() for path in (target, rollback, staged, marker)):
            raise ValueError("Activation marker paths cannot be symlinks")
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Activation marker"):
            raise
        raise ValueError("Activation marker paths are invalid") from exc
    return paths


def _write_activation_marker(marker: Path, payload: dict,
                             *, replace: bool = False) -> None:
    """Durably write a small activation phase marker owned by this process."""
    import tempfile
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"))
               + "\n").encode("utf-8")
    if len(encoded) > ACTIVATION_MARKER_LIMIT:
        raise ValueError("Database activation marker is oversized")
    fd, name = tempfile.mkstemp(
        prefix=".forgeos-activation-", suffix=".tmp", dir=marker.parent)
    os.close(fd)
    staging = Path(name)
    try:
        staging.write_bytes(encoded)
        with staging.open("rb+") as stream:
            os.fsync(stream.fileno())
        if marker.is_symlink():
            raise ValueError("Activation marker cannot be a symlink")
        if replace:
            os.replace(staging, marker)
            _sync_publication_directory(marker.parent)
        else:
            _publish_database_copy(staging, marker)
    finally:
        staging.unlink(missing_ok=True)


def _remove_activation_marker(marker: Path, *, missing_ok: bool = False) -> None:
    """Remove an activation marker and flush its POSIX directory entry."""
    marker.unlink(missing_ok=missing_ok)
    _sync_publication_directory(marker.parent)


def _set_activation_binding(payload: dict, field: str, path: Path) -> None:
    size, digest = _database_file_digest(path)
    payload[f"{field}_size"] = size
    payload[f"{field}_sha256"] = digest


def _verify_activation_binding(payload: dict, field: str, path: Path) -> None:
    expected_size = payload.get(f"{field}_size")
    expected_digest = payload.get(f"{field}_sha256")
    if expected_size is None and expected_digest is None:
        return
    try:
        size, digest = _database_file_digest(path)
    except OSError as exc:
        raise ValueError(
            f"Activation {field} database binding could not be read") from exc
    if size != expected_size or digest != expected_digest:
        raise ValueError(f"Activation {field} database changed")


def _discard_activation_staged(staged: Path) -> None:
    """Validate and remove one private staged activation database."""
    if staged.is_symlink() or not staged.is_file():
        raise ValueError("Staged activation file must be a regular file")
    if any(sidecar.exists() or sidecar.is_symlink()
           for sidecar in _database_sidecars(staged)):
        raise RuntimeError(
            "Clear SQLite sidecars beside the staged activation file")
    Store.inspect_existing_database(staged)
    staged.unlink()
    _sync_publication_directory(staged.parent)


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("Input contains invalid Unicode text") from exc


def _database_file_digest(path: Path) -> tuple[int, str]:
    """Stream a database digest without loading the file into memory."""
    before = _database_source_info(path, "Database artifact")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise ValueError(f"Database artifact could not be read: {path}") from exc
    _assert_database_source_unchanged(before, path, "Database artifact")
    return size, digest.hexdigest()


def _read_backup_manifest(path: str | Path) -> dict[str, object]:
    manifest = Path(path).expanduser().absolute()
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Backup manifest must be an existing regular file")
    if manifest.stat().st_size > MAX_BACKUP_MANIFEST_BYTES:
        raise ValueError("Backup manifest is oversized")
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Backup manifest is invalid") from exc
    if not isinstance(value, dict) or value.get("format") != BACKUP_MANIFEST_FORMAT:
        raise ValueError("Backup manifest format is unsupported")
    size = value.get("size")
    digest = value.get("sha256")
    if (type(size) is not int or size < 0 or
            not isinstance(digest, str) or
            not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("Backup manifest fields are invalid")
    return value


def _verify_backup_manifest(database: Path, manifest: str | Path) -> None:
    expected = _read_backup_manifest(manifest)
    size, digest = _database_file_digest(database)
    if size != expected["size"] or digest != expected["sha256"]:
        raise ValueError("Backup does not match its manifest")


def _publish_backup_manifest(database: Path, manifest: Path) -> None:
    if (manifest == database or not manifest.parent.is_dir() or
            manifest.parent.is_symlink() or manifest.is_symlink() or
            manifest.exists()):
        raise ValueError("Backup manifest must be a new file in an existing directory")
    size, digest = _database_file_digest(database)
    payload = json.dumps({
        "format": BACKUP_MANIFEST_FORMAT,
        "filename": database.name,
        "size": size,
        "sha256": digest,
    }, sort_keys=True, separators=(",", ":")) + "\n"
    if len(payload.encode("utf-8")) > MAX_BACKUP_MANIFEST_BYTES:
        raise RuntimeError("Backup manifest unexpectedly exceeds its bound")
    import tempfile
    fd, name = tempfile.mkstemp(
        prefix=".forgeos-manifest-", suffix=".tmp", dir=manifest.parent)
    os.close(fd)
    staging = Path(name)
    try:
        staging.write_text(payload, encoding="utf-8", newline="\n")
        with staging.open("rb+") as stream:
            os.fsync(stream.fileno())
        _publish_database_copy(staging, manifest)
    finally:
        staging.unlink(missing_ok=True)


def _read_handoff_manifest(path: str | Path) -> dict[str, object]:
    manifest = Path(path).expanduser().absolute()
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Journal handoff manifest must be an existing regular file")
    if manifest.stat().st_size > MAX_JOURNAL_HANDOFF_MANIFEST_BYTES:
        raise ValueError("Journal handoff manifest is oversized")
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Journal handoff manifest is invalid") from exc
    if (not isinstance(value, dict)
            or value.get("format") != JOURNAL_HANDOFF_MANIFEST_FORMAT):
        raise ValueError("Journal handoff manifest format is unsupported")
    size = value.get("size")
    digest = value.get("sha256")
    if (type(size) is not int or size < 0
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("Journal handoff manifest fields are invalid")
    return value


def _verify_handoff_manifest(handoff: Path, manifest: str | Path) -> None:
    expected = _read_handoff_manifest(manifest)
    size, digest = _database_file_digest(handoff)
    if size != expected["size"] or digest != expected["sha256"]:
        raise ValueError("Journal handoff does not match its manifest")


def _validate_handoff_manifest_destination(handoff: Path, manifest: Path) -> None:
    if (manifest == handoff or not manifest.parent.is_dir()
            or manifest.parent.is_symlink() or manifest.is_symlink()
            or manifest.exists()):
        raise ValueError(
            "Journal handoff manifest must be a new file in an existing directory")


def _publish_handoff_manifest(handoff: Path, manifest: Path) -> None:
    _validate_handoff_manifest_destination(handoff, manifest)
    size, digest = _database_file_digest(handoff)
    payload = json.dumps({
        "format": JOURNAL_HANDOFF_MANIFEST_FORMAT,
        "filename": handoff.name,
        "size": size,
        "sha256": digest,
    }, sort_keys=True, separators=(",", ":")) + "\n"
    if len(payload.encode("utf-8")) > MAX_JOURNAL_HANDOFF_MANIFEST_BYTES:
        raise RuntimeError("Journal handoff manifest unexpectedly exceeds its bound")
    import tempfile
    fd, name = tempfile.mkstemp(
        prefix=".forgeos-handoff-manifest-", suffix=".tmp", dir=manifest.parent)
    os.close(fd)
    staging = Path(name)
    try:
        staging.write_text(payload, encoding="utf-8", newline="\n")
        with staging.open("rb+") as stream:
            os.fsync(stream.fileno())
        _publish_database_copy(staging, manifest)
    finally:
        staging.unlink(missing_ok=True)


def validate_task_description(description: str) -> str:
    """Reject oversized prompts before copying them into SQLite or local AI context."""
    if not isinstance(description, str) or len(description) > MAX_TASK_DESCRIPTION_BYTES:
        raise ValueError("Task description must be text of at most 16 KiB")
    cleaned = description.strip()
    if not cleaned:
        raise ValueError("Task description is required")
    if _utf8_size(cleaned) > MAX_TASK_DESCRIPTION_BYTES:
        raise ValueError("Task description must be at most 16 KiB UTF-8")
    return cleaned


def validate_local_prompt(prompt: str) -> str:
    """Cap model requests before model routing or JSON serialization."""
    if not isinstance(prompt, str) or len(prompt) > MAX_LOCAL_PROMPT_BYTES:
        raise ValueError("Local model prompt exceeds 64 KiB")
    if not prompt.strip():
        raise ValueError("Local model prompt is required")
    if _utf8_size(prompt) > MAX_LOCAL_PROMPT_BYTES:
        raise ValueError("Local model prompt exceeds 64 KiB UTF-8")
    return prompt


def _verify_task_database_connection(db: sqlite3.Connection) -> None:
    """Check SQLite structure and essential ForgeOS schema without row loads."""
    result = db.execute("PRAGMA integrity_check(1)").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError("SQLite database integrity check failed")
    required = {
        "tasks": {"id", "project", "description", "status", "attempts",
                  "created", "updated", "last_error", "result"},
        "task_leases": {"task_id", "owner", "acquired", "heartbeat"},
    }
    for table, columns in required.items():
        actual = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if not columns.issubset(actual):
            raise ValueError("Not a compatible ForgeOS task database")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(SCHEMA)
        self.db.execute(LEASE_SCHEMA)
        self.db.execute(EVENT_SCHEMA)
        self._migrate_event_metadata()
        self.db.execute(EVENT_INDEX)
        self.db.execute(EVENT_NO_UPDATE)
        self.db.execute(EVENT_NO_DELETE)
        self.db.execute(ACTIVE_TASK_LOOKUP_INDEX)
        self.db.commit()

    def _migrate_event_metadata(self) -> None:
        """Add bounded journal metadata columns to pre-journal databases."""
        columns = {row[1] for row in self.db.execute(
            "PRAGMA table_info(forgeos_events)")}
        migrations = {
            "action": "TEXT NOT NULL DEFAULT ''",
            "attempt": "INTEGER",
            "artifact_ref": "TEXT NOT NULL DEFAULT ''",
            "evidence_ref": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in migrations.items():
            if name not in columns:
                self.db.execute(
                    f"ALTER TABLE forgeos_events ADD COLUMN {name} {definition}")

    @staticmethod
    def _validate_event_text(value: str, limit: int, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"Event {label} must be nonempty text")
        if len(value.encode("utf-8")) > limit:
            raise ValueError(f"Event {label} exceeds {limit} bytes")
        return value

    def _record_event(self, event_type: str, *, task_id: int | None = None,
                      status: str = '', actor: str = '', details: str = '',
                      action: str = '', attempt: int | None = None,
                      artifact_ref: str = '', evidence_ref: str = '') -> None:
        """Append a bounded lifecycle event inside the caller's transaction."""
        event_type = self._validate_event_text(
            event_type, MAX_EVENT_TYPE_BYTES, 'type')
        if not EVENT_TYPE_PATTERN.fullmatch(event_type):
            raise ValueError('Event type contains unsupported characters')
        if task_id is not None and (
                type(task_id) is not int or task_id < 1):
            raise ValueError('Event task ID must be a positive integer')
        if status and status not in STATUSES:
            raise ValueError('Event status is invalid')
        if actor:
            self._validate_event_text(actor, MAX_EVENT_ACTOR_BYTES, 'actor')
        if not isinstance(details, str):
            raise ValueError('Event details must be text')
        if len(details.encode('utf-8')) > MAX_EVENT_DETAILS_BYTES:
            raise ValueError('Event details exceed 2048 bytes')
        if action:
            self._validate_event_text(action, MAX_EVENT_ACTION_BYTES, 'action')
            if not EVENT_TYPE_PATTERN.fullmatch(action):
                raise ValueError('Event action contains unsupported characters')
        if attempt is not None and (
                type(attempt) is not int or not 1 <= attempt <= 1000000):
            raise ValueError('Event attempt must be 1..1000000')
        for value, limit, label in (
                (artifact_ref, MAX_EVENT_ARTIFACT_BYTES, 'artifact reference'),
                (evidence_ref, MAX_EVENT_EVIDENCE_BYTES, 'evidence reference')):
            if value:
                self._validate_event_text(value, limit, label)
                pattern = (re.compile(r"^[0-9a-f]{64}$")
                           if label == 'evidence reference'
                           else EVENT_REFERENCE_PATTERN)
                if not pattern.fullmatch(value):
                    raise ValueError(f'Event {label} contains unsupported characters')
        self.db.execute(
            'INSERT INTO forgeos_events(task_id,event_type,status,actor,created,details,'
            'action,attempt,artifact_ref,evidence_ref) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (task_id, event_type, status, actor, time.time(), details, action,
             attempt, artifact_ref, evidence_ref))

    def iter_events(self, *, task_id: int | None = None, limit: int = 100):
        """Yield a bounded journal page without materializing all history."""
        if task_id is not None and (type(task_id) is not int or task_id < 1):
            raise ValueError('Task ID must be a positive integer')
        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError('Event page limit must be 1..500')
        if task_id is None:
            cursor = self.db.execute(
                'SELECT id,task_id,event_type,status,actor,created,details,action,attempt,'
                'artifact_ref,evidence_ref '
                'FROM forgeos_events ORDER BY id DESC LIMIT ?', (limit,))
        else:
            cursor = self.db.execute(
                'SELECT id,task_id,event_type,status,actor,created,details,action,attempt,'
                'artifact_ref,evidence_ref '
                'FROM forgeos_events WHERE task_id=? ORDER BY id DESC LIMIT ?',
                (task_id, limit))
        try:
            for row in cursor:
                yield dict(row)
        finally:
            cursor.close()

    def check_journal(self, *, max_issues: int = MAX_JOURNAL_ISSUES) -> dict:
        """Stream-check journal rows and return bounded corruption diagnostics."""
        if (type(max_issues) is not int or isinstance(max_issues, bool)
                or not 1 <= max_issues <= MAX_JOURNAL_ISSUES):
            raise ValueError('Journal issue limit must be 1..32')
        checked = 0
        issues = []
        truncated = False
        previous_id = 0
        cursor = self.db.execute(
            'SELECT e.id,e.task_id,e.event_type,e.status,e.actor,e.created,e.details,'
            'e.action,e.attempt,e.artifact_ref,e.evidence_ref,t.id AS linked_task_id '
            'FROM forgeos_events e LEFT JOIN tasks t ON t.id=e.task_id '
            'ORDER BY e.id ASC')
        try:
            for row in cursor:
                checked += 1
                row_id = row['id']
                row_issues = []
                if type(row_id) is not int or row_id <= previous_id:
                    row_issues.append('event id is not strictly increasing')
                previous_id = row_id if type(row_id) is int else previous_id
                task_id = row['task_id']
                if task_id is not None and row['linked_task_id'] is None:
                    row_issues.append('event references a missing task')
                event_type = row['event_type']
                if (not isinstance(event_type, str)
                        or not EVENT_TYPE_PATTERN.fullmatch(event_type)
                        or len(event_type.encode('utf-8')) > MAX_EVENT_TYPE_BYTES):
                    row_issues.append('event type is invalid')
                if row['status'] and row['status'] not in STATUSES:
                    row_issues.append('event status is invalid')
                if not isinstance(row['actor'], str) or (
                        len(row['actor'].encode('utf-8')) > MAX_EVENT_ACTOR_BYTES):
                    row_issues.append('event actor is oversized')
                if not isinstance(row['details'], str) or (
                        len(row['details'].encode('utf-8')) > MAX_EVENT_DETAILS_BYTES):
                    row_issues.append('event details are oversized')
                action = row['action']
                if action and (not isinstance(action, str)
                               or not EVENT_TYPE_PATTERN.fullmatch(action)
                               or len(action.encode('utf-8')) > MAX_EVENT_ACTION_BYTES):
                    row_issues.append('event action is invalid')
                attempt = row['attempt']
                if attempt is not None and (
                        type(attempt) is not int or not 1 <= attempt <= 1000000):
                    row_issues.append('event attempt is invalid')
                artifact = row['artifact_ref']
                if artifact and (not isinstance(artifact, str)
                                 or len(artifact.encode('utf-8')) > MAX_EVENT_ARTIFACT_BYTES
                                 or not EVENT_REFERENCE_PATTERN.fullmatch(artifact)):
                    row_issues.append('event artifact reference is invalid')
                evidence = row['evidence_ref']
                if evidence and (not isinstance(evidence, str)
                                 or not re.fullmatch(r'[0-9a-f]{64}', evidence)):
                    row_issues.append('event evidence reference is invalid')
                created = row['created']
                if (not isinstance(created, (int, float))
                        or not math.isfinite(created)):
                    row_issues.append('event timestamp is invalid')
                if row_issues and len(issues) < max_issues:
                    issues.append({'id': row_id, 'problems': row_issues})
                elif row_issues:
                    truncated = True
        finally:
            cursor.close()
        return {'checked': checked, 'issues': issues, 'truncated': truncated}

    def export_events(self, destination: str | Path,
                      *, task_id: int | None = None) -> Path:
        """Stream bounded journal rows to a new, durable JSONL artifact."""
        if task_id is not None and (type(task_id) is not int or task_id < 1):
            raise ValueError('Task ID must be a positive integer')
        target = Path(destination).expanduser().absolute()
        if (not target.parent.is_dir() or target.parent.is_symlink()
                or target.is_symlink() or target.exists()):
            raise ValueError('Journal export must be a new file in an existing directory')
        import tempfile
        fd, name = tempfile.mkstemp(
            prefix='.forgeos-journal-', suffix='.tmp', dir=target.parent)
        os.close(fd)
        staging = Path(name)
        try:
            with staging.open('w', encoding='utf-8', newline='\n') as output:
                if task_id is None:
                    cursor = self.db.execute(
                        'SELECT id,task_id,event_type,status,actor,created,details,'
                        'action,attempt,artifact_ref,evidence_ref '
                        'FROM forgeos_events ORDER BY id ASC')
                else:
                    cursor = self.db.execute(
                        'SELECT id,task_id,event_type,status,actor,created,details,'
                        'action,attempt,artifact_ref,evidence_ref '
                        'FROM forgeos_events WHERE task_id=? ORDER BY id ASC',
                        (task_id,))
                try:
                    for row in cursor:
                        output.write(json.dumps(
                            dict(row), ensure_ascii=False,
                            separators=(',', ':')))
                        output.write('\n')
                finally:
                    cursor.close()
                output.flush()
                os.fsync(output.buffer.fileno())
            _publish_database_copy(staging, target)
            return target
        finally:
            staging.unlink(missing_ok=True)

    def export_handoff(self, destination: str | Path, *, task_id: int,
                       manifest: str | Path | None = None) -> Path:
        """Export one task's bounded state and journal as a replay plan.

        The artifact is deliberately a plan, not an action executor.  It
        contains no task description, project path, result/checkpoint blob,
        lease data, raw error or test output.  Event artifact/evidence fields
        remain validated identifiers so a later recovery tool can resolve
        them under an explicit policy without copying sensitive output here.
        """
        if type(task_id) is not int or task_id < 1:
            raise ValueError('Task ID must be a positive integer')
        target = Path(destination).expanduser().absolute()
        if (not target.parent.is_dir() or target.parent.is_symlink()
                or target.is_symlink() or target.exists()):
            raise ValueError('Journal handoff must be a new file in an existing directory')
        manifest_target = None
        if manifest is not None:
            manifest_target = Path(manifest).expanduser().absolute()
            _validate_handoff_manifest_destination(target, manifest_target)
        task = self.db.execute(
            'SELECT id,status,attempts,created,updated FROM tasks WHERE id=?',
            (task_id,)).fetchone()
        if task is None:
            raise ValueError('Task not found')
        task_record = {
            'record': 'task', 'task_id': task_id,
            'status': task['status'], 'attempts': task['attempts'],
            'created': task['created'], 'updated': task['updated'],
        }
        self._validate_handoff_task(task_record, task_id)
        import tempfile
        fd, name = tempfile.mkstemp(
            prefix='.forgeos-handoff-', suffix='.tmp', dir=target.parent)
        os.close(fd)
        staging = Path(name)
        event_count = 0
        last_event_id = None

        def write_record(output, record):
            output.write(json.dumps(
                record, ensure_ascii=False, separators=(',', ':')))
            output.write('\n')

        try:
            with staging.open('w', encoding='utf-8', newline='\n') as output:
                write_record(output, {
                    'record': 'header', 'format': JOURNAL_HANDOFF_FORMAT,
                    'task_id': task_id,
                })
                write_record(output, task_record)
                cursor = self.db.execute(
                    'SELECT id,task_id,'
                    'CASE WHEN length(CAST(event_type AS BLOB))<=? '
                    'THEN event_type ELSE NULL END AS event_type,'
                    'CASE WHEN length(CAST(status AS BLOB))<=? '
                    'THEN status ELSE NULL END AS status,'
                    'CASE WHEN length(CAST(actor AS BLOB))<=? '
                    'THEN actor ELSE NULL END AS actor,'
                    'created,'
                    'CASE WHEN length(CAST(details AS BLOB))<=? '
                    'THEN details ELSE NULL END AS details,'
                    'CASE WHEN length(CAST(action AS BLOB))<=? '
                    'THEN action ELSE NULL END AS action,'
                    'attempt,'
                    'CASE WHEN length(CAST(artifact_ref AS BLOB))<=? '
                    'THEN artifact_ref ELSE NULL END AS artifact_ref,'
                    'CASE WHEN length(CAST(evidence_ref AS BLOB))<=? '
                    'THEN evidence_ref ELSE NULL END AS evidence_ref '
                    'FROM forgeos_events WHERE task_id=? ORDER BY id ASC',
                    (MAX_EVENT_TYPE_BYTES, MAX_EVENT_TYPE_BYTES,
                     MAX_EVENT_ACTOR_BYTES, MAX_EVENT_DETAILS_BYTES,
                     MAX_EVENT_ACTION_BYTES, MAX_EVENT_ARTIFACT_BYTES,
                     MAX_EVENT_EVIDENCE_BYTES, task_id))
                try:
                    for row in cursor:
                        event = dict(row)
                        self._validate_handoff_event(event, task_id)
                        write_record(output, {'record': 'event', **event})
                        event_count += 1
                        last_event_id = event['id']
                finally:
                    cursor.close()
                write_record(output, {
                    'record': 'summary', 'task_id': task_id,
                    'event_count': event_count, 'last_event_id': last_event_id,
                    'complete': True,
                })
                output.flush()
                os.fsync(output.buffer.fileno())
            _publish_database_copy(staging, target)
            if manifest_target is not None:
                _publish_handoff_manifest(target, manifest_target)
            return target
        finally:
            staging.unlink(missing_ok=True)

    @staticmethod
    def _is_finite_handoff_number(value) -> bool:
        if type(value) not in (int, float):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    @staticmethod
    def _validate_handoff_task(record: dict, task_id: int) -> None:
        """Validate the scalar task projection used by a handoff artifact."""
        if (set(record) != {
                'record', 'task_id', 'status', 'attempts', 'created', 'updated'}
                or record.get('record') != 'task'
                or record.get('task_id') != task_id
                or not isinstance(record.get('status'), str)
                or record['status'] not in STATUSES
                or type(record.get('attempts')) is not int
                or record['attempts'] < 0
                or not Store._is_finite_handoff_number(record.get('created'))
                or not Store._is_finite_handoff_number(record.get('updated'))):
            raise ValueError('Journal handoff contains an invalid task projection')

    @staticmethod
    def _handoff_issue(issues: list, message: str, line: int,
                       max_issues: int) -> bool:
        """Append one bounded handoff diagnostic and report truncation."""
        if len(issues) < max_issues:
            issues.append({'line': line, 'problem': message})
            return False
        return True

    @classmethod
    def check_handoff(cls, source: str | Path, *, manifest: str | Path | None = None,
                      evidence_directory: str | Path | None = None,
                      max_issues: int = MAX_JOURNAL_ISSUES) -> dict:
        """Stream-check one exported handoff without opening the task database."""
        if (type(max_issues) is not int or isinstance(max_issues, bool)
                or not 1 <= max_issues <= MAX_JOURNAL_ISSUES):
            raise ValueError('Handoff issue limit must be 1..32')
        path = Path(source).expanduser().absolute()
        if path.is_symlink() or not path.is_file():
            raise ValueError('Journal handoff must be an existing regular file')
        issues = []
        truncated = False
        checked = 0
        header_task_id = None
        header_valid = False
        task_valid = False
        summary_valid = False
        phase = 'header'
        event_count = 0
        last_event_id = None
        expected_event_keys = {
            'record', 'id', 'task_id', 'event_type', 'status', 'actor',
            'created', 'details', 'action', 'attempt', 'artifact_ref',
            'evidence_ref'}

        def issue(message, line):
            nonlocal truncated
            truncated = cls._handoff_issue(
                issues, message, line, max_issues) or truncated

        evidence_root = None
        evidence_valid = None
        evidence_store = None
        if evidence_directory is not None:
            evidence_valid = True
            try:
                evidence_root = Path(evidence_directory).expanduser().absolute()
            except TypeError as exc:
                raise ValueError('Evidence directory is invalid') from exc
            if evidence_root.is_symlink() or not evidence_root.is_dir():
                evidence_valid = False
                issue('Evidence directory is unavailable or unsafe', 0)
            else:
                from .diagnostics import EvidenceStore
                evidence_store = EvidenceStore

        try:
            stream = path.open('rb')
        except OSError as exc:
            raise ValueError('Journal handoff could not be opened') from exc
        try:
            while True:
                raw = stream.readline(MAX_JOURNAL_HANDOFF_LINE_BYTES + 1)
                if not raw:
                    break
                checked += 1
                if len(raw) > MAX_JOURNAL_HANDOFF_LINE_BYTES:
                    issue('record exceeds the 16 KiB line limit', checked)
                    while raw and not raw.endswith(b'\n'):
                        raw = stream.readline(MAX_JOURNAL_HANDOFF_LINE_BYTES + 1)
                    continue
                try:
                    record = json.loads(raw.decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                    issue('record is not valid UTF-8 JSON', checked)
                    continue
                if not isinstance(record, dict):
                    issue('record must be a JSON object', checked)
                    continue
                kind = record.get('record')
                if phase == 'header':
                    if (set(record) != {'record', 'format', 'task_id'}
                            or kind != 'header'
                            or record.get('format') != JOURNAL_HANDOFF_FORMAT
                            or type(record.get('task_id')) is not int
                            or record['task_id'] < 1):
                        issue('invalid handoff header', checked)
                    else:
                        header_task_id = record['task_id']
                        header_valid = True
                    phase = 'task'
                    continue
                if phase == 'task':
                    if kind != 'task':
                        issue('task projection must follow the header', checked)
                    else:
                        try:
                            cls._validate_handoff_task(
                                record, header_task_id)
                        except (TypeError, ValueError):
                            issue('invalid task projection', checked)
                        else:
                            task_valid = True
                    phase = 'events'
                    continue
                if phase == 'events':
                    if kind == 'event':
                        if set(record) != expected_event_keys:
                            issue('event record fields are invalid', checked)
                            continue
                        event = {key: record[key] for key in expected_event_keys
                                 if key != 'record'}
                        try:
                            cls._validate_handoff_event(event, header_task_id)
                        except (TypeError, ValueError) as exc:
                            issue(str(exc), checked)
                            continue
                        if evidence_store is not None and event['evidence_ref']:
                            try:
                                evidence_store.verify_at(
                                    evidence_root, event['evidence_ref'])
                            except (OSError, ValueError) as exc:
                                evidence_valid = False
                                issue(str(exc), checked)
                        if (last_event_id is not None
                                and event['id'] <= last_event_id):
                            issue('event IDs are not strictly increasing', checked)
                        event_count += 1
                        last_event_id = event['id']
                        continue
                    if kind != 'summary':
                        issue('summary must follow event records', checked)
                        continue
                    if set(record) != {
                            'record', 'task_id', 'event_count',
                            'last_event_id', 'complete'}:
                        issue('summary record fields are invalid', checked)
                    elif (record.get('task_id') != header_task_id
                          or type(record.get('event_count')) is not int
                          or record['event_count'] != event_count
                          or record.get('last_event_id') != last_event_id
                          or record.get('complete') is not True):
                        issue('summary does not match streamed events', checked)
                    else:
                        summary_valid = True
                    phase = 'complete'
                    continue
                issue('records are present after the completion summary', checked)
        finally:
            stream.close()
        if not header_valid:
            issue('missing or invalid handoff header', 0)
        if not task_valid:
            issue('missing or invalid task projection', 0)
        if not summary_valid:
            issue('missing or invalid completion summary', checked)
        manifest_valid = None
        if manifest is not None:
            manifest_valid = False
            try:
                _verify_handoff_manifest(path, manifest)
            except (OSError, ValueError) as exc:
                issue(str(exc), 0)
            else:
                manifest_valid = True
        return {
            'valid': not issues,
            'format': JOURNAL_HANDOFF_FORMAT if header_valid else '',
            'task_id': header_task_id,
            'records': checked,
            'events': event_count,
            'manifest_checked': manifest is not None,
            'manifest_valid': manifest_valid,
            'evidence_checked': evidence_directory is not None,
            'evidence_valid': evidence_valid,
            'issues': issues,
            'truncated': truncated,
        }

    @classmethod
    def _validate_handoff_event(cls, event: dict, task_id: int) -> None:
        """Reject malformed journal data before it reaches a replay plan."""
        if (type(event.get('id')) is not int or event['id'] < 1
                or event.get('task_id') != task_id):
            raise ValueError('Journal handoff contains an invalid event identity')
        event_type = event.get('event_type')
        if (not isinstance(event_type, str)
                or not EVENT_TYPE_PATTERN.fullmatch(event_type)
                or len(event_type.encode('utf-8')) > MAX_EVENT_TYPE_BYTES):
            raise ValueError('Journal handoff contains an invalid event type')
        status = event.get('status')
        if status != '' and (not isinstance(status, str) or status not in STATUSES):
            raise ValueError('Journal handoff contains an invalid event status')
        actor = event.get('actor')
        if (not isinstance(actor, str)
                or len(actor.encode('utf-8')) > MAX_EVENT_ACTOR_BYTES):
            raise ValueError('Journal handoff contains an oversized event actor')
        details = event.get('details')
        if (not isinstance(details, str)
                or len(details.encode('utf-8')) > MAX_EVENT_DETAILS_BYTES):
            raise ValueError('Journal handoff contains oversized event details')
        action = event.get('action')
        if (action != '' and (not isinstance(action, str)
                        or not EVENT_TYPE_PATTERN.fullmatch(action)
                        or len(action.encode('utf-8')) > MAX_EVENT_ACTION_BYTES)):
            raise ValueError('Journal handoff contains an invalid event action')
        attempt = event.get('attempt')
        if (attempt is not None
                and (type(attempt) is not int or not 1 <= attempt <= 1000000)):
            raise ValueError('Journal handoff contains an invalid event attempt')
        artifact = event.get('artifact_ref')
        if (artifact != '' and (not isinstance(artifact, str)
                          or len(artifact.encode('utf-8')) > MAX_EVENT_ARTIFACT_BYTES
                          or not EVENT_REFERENCE_PATTERN.fullmatch(artifact))):
            raise ValueError('Journal handoff contains an invalid artifact reference')
        evidence = event.get('evidence_ref')
        if (evidence != '' and (not isinstance(evidence, str)
                          or not re.fullmatch(r'[0-9a-f]{64}', evidence))):
            raise ValueError('Journal handoff contains an invalid evidence reference')
        created = event.get('created')
        if not cls._is_finite_handoff_number(created):
            raise ValueError('Journal handoff contains an invalid event timestamp')

    def check_integrity(self) -> None:
        """Explicitly check SQLite integrity and ForgeOS task schema."""
        try:
            _verify_task_database_connection(self.db)
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("SQLite integrity check could not complete") from exc

    @staticmethod
    def inspect_existing_database(path: str | Path) -> None:
        """Validate an existing database read-only; never create/migrate it."""
        from contextlib import closing

        source = Path(path).expanduser().absolute()
        try:
            source_info = _database_source_info(source, "Task database")
        except ValueError as exc:
            raise ValueError(
                "Task database must be an existing regular file") from exc
        # SQLite may create a -shm file merely by opening a sidecar-free
        # WAL-mode database read-only. Immutable mode prevents that mutation
        # when no journal sidecar exists; a present WAL/rollback journal must
        # remain visible through the normal read-only connection instead.
        has_sidecar = any(sidecar.exists() or sidecar.is_symlink()
                          for sidecar in _database_sidecars(source))
        query = "?mode=ro" if has_sidecar else "?mode=ro&immutable=1"
        with closing(sqlite3.connect(source.as_uri() + query, uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            _verify_task_database_connection(db)
        _assert_database_source_unchanged(
            source_info, source, "Task database")

    def backup_to(self, destination: str | Path,
                  manifest: str | Path | None = None) -> Path:
        """Publish an integrity-checked, non-overwriting SQLite online backup."""
        import tempfile
        from contextlib import closing

        target = Path(destination).expanduser().absolute()
        if (target == self.path or not target.parent.is_dir()
                or target.parent.is_symlink() or target.is_symlink()
                or target.exists()):
            raise ValueError("Backup destination must be a new file in an existing directory")
        manifest_target = None
        if manifest is not None:
            manifest_target = Path(manifest).expanduser().absolute()
            if (manifest_target == target or not manifest_target.parent.is_dir()
                    or manifest_target.parent.is_symlink()
                    or manifest_target.is_symlink() or manifest_target.exists()):
                raise ValueError(
                    "Backup manifest must be a new file in an existing directory")
        if self.db.in_transaction:
            raise RuntimeError("Finish the active task transaction before backup")
        self.check_integrity()
        fd, name = tempfile.mkstemp(
            prefix=".forgeos-backup-", suffix=".tmp", dir=target.parent)
        os.close(fd)
        staging = Path(name)
        try:
            with closing(sqlite3.connect(staging)) as snapshot:
                self.db.backup(snapshot, pages=64, sleep=0.05)
                checked = snapshot.execute("PRAGMA integrity_check(1)").fetchone()
                if checked is None or checked[0] != "ok":
                    raise RuntimeError("Backup integrity verification failed")
            with staging.open("rb+") as stream:
                os.fsync(stream.fileno())
            # Exclusive atomic publication; never replace another backup.
            _publish_database_copy(staging, target)
            if manifest_target is not None:
                _publish_backup_manifest(target, manifest_target)
            return target
        finally:
            staging.unlink(missing_ok=True)

    @staticmethod
    def restore_backup_to(source: str | Path, destination: str | Path,
                          manifest: str | Path | None = None) -> Path:
        """Verify a ForgeOS SQLite database and copy it to a new file only.

        The old database, backup and active ForgeOS database are never
        overwritten. This is not an automatic activation or data merge.
        """
        import tempfile
        from contextlib import closing

        src = Path(source).expanduser().absolute()
        target = Path(destination).expanduser().absolute()
        try:
            source_info = _database_source_info(src, "Restore source")
        except ValueError as exc:
            raise ValueError(
                "Restore source must be a regular SQLite database file") from exc
        if manifest is not None:
            _verify_backup_manifest(src, manifest)
        if (src == target or not target.parent.is_dir()
                or target.parent.is_symlink() or target.is_symlink()
                or target.exists()):
            raise ValueError(
                "Restore destination must be a new file in an existing directory")
        fd, name = tempfile.mkstemp(
            prefix=".forgeos-restore-", suffix=".tmp", dir=target.parent)
        os.close(fd)
        staged = Path(name)
        try:
            # mode=ro protects the input from SQLite writes. The online
            # backup API reads a consistent snapshot, including live WAL.
            with closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)) as original:
                original.execute("PRAGMA query_only=ON")
                _verify_task_database_connection(original)
                with closing(sqlite3.connect(staged)) as restored:
                    original.backup(restored, pages=64, sleep=0.05)
                    # Protect against concurrent source-schema changes while
                    # copying, and reject a corrupt or incompatible result.
                    _verify_task_database_connection(restored)
            _assert_database_source_unchanged(
                source_info, src, "Restore source")
            with staged.open("rb+") as stream:
                os.fsync(stream.fileno())
            # No-overwrite publication also protects concurrent users.
            _publish_database_copy(staged, target)
            return target
        finally:
            staged.unlink(missing_ok=True)

    @staticmethod
    def activate_backup_to(source: str | Path, destination: str | Path,
                           rollback: str | Path, *,
                           manifest: str | Path | None = None,
                           confirm: bool = False) -> Path:
        """Activate a verified backup offline while retaining the old database.

        The caller must stop ForgeOS first. Existing SQLite sidecars are
        refused, Windows open-handle failures are preserved, and a phase
        marker plus rollback copy remain if recovery cannot finish cleanly.
        """
        if confirm is not True:
            raise ValueError("Explicit activation confirmation is required")
        src = Path(source).expanduser().absolute()
        target = Path(destination).expanduser().absolute()
        old = Path(rollback).expanduser().absolute()
        marker = _activation_marker_path(target)
        if src.is_symlink() or not src.is_file():
            raise ValueError("Restore source must be a regular SQLite database file")
        if (target.parent != old.parent or not target.parent.is_dir()
                or target.parent.is_symlink() or src == target
                or src == old or target == old):
            raise ValueError("Activation paths must be distinct in one safe directory")
        if (target.is_symlink() or old.is_symlink() or marker.is_symlink()
                or old.exists() or marker.exists()):
            raise ValueError("Activation rollback and marker paths must be new files")
        if target.exists() and not target.is_file():
            raise ValueError("Activation destination must be a regular database file")
        for sidecar in _database_sidecars(target):
            if sidecar.exists() or sidecar.is_symlink():
                raise RuntimeError(
                    "Stop ForgeOS and clear SQLite sidecars before activation")
        if target.exists():
            Store.inspect_existing_database(target)

        import tempfile
        fd, name = tempfile.mkstemp(
            prefix=".forgeos-activate-", suffix=".sqlite3", dir=target.parent)
        os.close(fd)
        staged = Path(name)
        staged.unlink()
        old_moved = False
        published = False
        try:
            Store.restore_backup_to(src, staged, manifest=manifest)
            marker_payload = {
                "format": ACTIVATION_MARKER_FORMAT,
                "source": str(src),
                "destination": str(target),
                "rollback": str(old),
                "staged": str(staged),
                "phase": "prepared",
            }
            _set_activation_binding(marker_payload, "staged", staged)
            _write_activation_marker(marker, marker_payload)
            if target.exists():
                _move_database_copy(target, old)
                old_moved = True
                _set_activation_binding(marker_payload, "rollback", old)
                marker_payload["phase"] = "old_moved"
                _write_activation_marker(marker, marker_payload, replace=True)
            marker_payload["phase"] = "publishing"
            _write_activation_marker(marker, marker_payload, replace=True)
            _move_database_copy(staged, target)
            published = True
            marker_payload["phase"] = "published"
            _write_activation_marker(marker, marker_payload, replace=True)
            _remove_activation_marker(marker)
            return target
        except Exception:
            restored = False
            if old_moved and not published and not target.exists() and old.exists():
                try:
                    _move_database_copy(old, target)
                    restored = True
                except OSError:
                    # Keep the marker and rollback copy for manual recovery.
                    pass
            if restored or (not old_moved and not published and target.exists()):
                # No replacement was left in place, so our marker is no longer
                # needed and should not block a later operator retry.
                _remove_activation_marker(marker, missing_ok=True)
            raise
        finally:
            staged.unlink(missing_ok=True)

    @staticmethod
    def activation_status(destination: str | Path) -> dict:
        """Return bounded, read-only status for an activation marker."""
        target = Path(destination).expanduser().absolute()
        marker = _activation_marker_path(target)
        if marker.is_symlink():
            raise ValueError("Activation marker cannot be a symlink")
        if not marker.exists():
            return {"status": "clear", "marker": str(marker)}
        payload = _read_activation_marker(marker)
        paths = _validate_activation_marker(marker, payload)
        return {
            "status": "pending",
            "marker": str(marker),
            "phase": payload["phase"],
            "source": str(paths["source"]),
            "destination": str(paths["destination"]),
            "rollback": str(paths["rollback"]),
            "staged": str(paths["staged"]),
            "destination_exists": paths["destination"].exists(),
            "rollback_exists": paths["rollback"].exists(),
            "staged_exists": paths["staged"].exists(),
        }

    @staticmethod
    def rollback_activation(destination: str | Path, *, confirm: bool = False,
                            discard_staged: bool = False) -> Path:
        """Restore an interrupted activation's retained database, never overwrite.

        A staged replacement left by a process crash is preserved by default.
        An operator may explicitly discard it after ForgeOS validates the
        staged SQLite file and confirms the rollback operation.
        """
        if confirm is not True:
            raise ValueError("Explicit activation rollback confirmation is required")
        if type(discard_staged) is not bool:
            raise ValueError("Staged activation discard must be boolean")
        target = Path(destination).expanduser().absolute()
        marker = _activation_marker_path(target)
        if marker.is_symlink() or not marker.is_file():
            raise ValueError("No activation marker is available for rollback")
        payload = _read_activation_marker(marker)
        paths = _validate_activation_marker(marker, payload)
        if payload["phase"] not in ("old_moved", "publishing"):
            raise ValueError(
                "Activation marker is not awaiting rollback from old_moved "
                "or publishing phase")
        target = paths["destination"]
        old = paths["rollback"]
        staged = paths["staged"]
        if target.exists() or target.is_symlink():
            raise ValueError("Activation rollback refuses an occupied destination")
        for database in (target, old):
            if any(sidecar.exists() or sidecar.is_symlink()
                   for sidecar in _database_sidecars(database)):
                raise RuntimeError(
                    "Clear SQLite sidecars before activation rollback")
        if old.is_symlink() or not old.is_file():
            raise ValueError("Retained rollback database is unavailable")
        _verify_activation_binding(payload, "rollback", old)
        Store.inspect_existing_database(old)
        staged_present = staged.exists() or staged.is_symlink()
        if staged_present and not discard_staged:
            raise RuntimeError(
                "Preserve the staged activation file for manual inspection, "
                "or explicitly pass discard_staged=True")
        if any(sidecar.exists() or sidecar.is_symlink()
               for sidecar in _database_sidecars(staged)):
            raise RuntimeError(
                "Clear SQLite sidecars beside the staged activation file")
        if staged_present:
            _verify_activation_binding(payload, "staged", staged)
            _discard_activation_staged(staged)
        _move_database_copy(old, target)
        if marker.is_symlink():
            raise RuntimeError("Activation marker changed during rollback")
        _remove_activation_marker(marker)
        return target

    @staticmethod
    def finalize_activation(destination: str | Path, *, confirm: bool = False) -> Path:
        """Clear a durable publishing marker after validating both databases."""
        if confirm is not True:
            raise ValueError("Explicit activation finalization confirmation is required")
        target = Path(destination).expanduser().absolute()
        marker = _activation_marker_path(target)
        if marker.is_symlink() or not marker.is_file():
            raise ValueError("No activation marker is available for finalization")
        payload = _read_activation_marker(marker)
        paths = _validate_activation_marker(marker, payload)
        if payload["phase"] not in ("publishing", "published"):
            raise ValueError(
                "Activation marker is not ready for publishing finalization")
        target = paths["destination"]
        old = paths["rollback"]
        staged = paths["staged"]
        for database in (target, old):
            if database.is_symlink() or not database.is_file():
                raise ValueError("Published activation database is unavailable")
            if any(sidecar.exists() or sidecar.is_symlink()
                   for sidecar in _database_sidecars(database)):
                raise RuntimeError(
                    "Clear SQLite sidecars before activation finalization")
        if staged.exists() or staged.is_symlink():
            raise RuntimeError(
                "Published activation still has a staged file")
        if any(sidecar.exists() or sidecar.is_symlink()
               for sidecar in _database_sidecars(staged)):
            raise RuntimeError(
                "Clear SQLite sidecars beside the staged activation file")
        Store.inspect_existing_database(target)
        _verify_activation_binding(payload, "staged", target)
        _verify_activation_binding(payload, "rollback", old)
        Store.inspect_existing_database(old)
        _remove_activation_marker(marker)
        return target

    @staticmethod
    def abandon_activation(destination: str | Path, *, confirm: bool = False,
                           discard_staged: bool = False) -> Path:
        """Cancel a prepared activation without moving the active database."""
        if confirm is not True:
            raise ValueError("Explicit activation abandonment confirmation is required")
        if type(discard_staged) is not bool:
            raise ValueError("Staged activation discard must be boolean")
        target = Path(destination).expanduser().absolute()
        marker = _activation_marker_path(target)
        if marker.is_symlink() or not marker.is_file():
            raise ValueError("No activation marker is available for abandonment")
        payload = _read_activation_marker(marker)
        paths = _validate_activation_marker(marker, payload)
        if payload["phase"] != "prepared":
            raise ValueError(
                "Activation marker is not awaiting prepared abandonment")
        target = paths["destination"]
        old = paths["rollback"]
        staged = paths["staged"]
        if old.exists() or old.is_symlink():
            raise ValueError(
                "Prepared activation has an unexpected rollback database")
        if target.is_symlink() or not target.is_file():
            raise ValueError("Prepared activation destination is unavailable")
        for database in (target, staged):
            if any(sidecar.exists() or sidecar.is_symlink()
                   for sidecar in _database_sidecars(database)):
                raise RuntimeError(
                    "Clear SQLite sidecars before prepared abandonment")
        Store.inspect_existing_database(target)
        staged_present = staged.exists() or staged.is_symlink()
        if staged_present and not discard_staged:
            raise RuntimeError(
                "Preserve the staged activation file for manual inspection, "
                "or explicitly pass discard_staged=True")
        if staged_present:
            _verify_activation_binding(payload, "staged", staged)
            _discard_activation_staged(staged)
        _remove_activation_marker(marker)
        return target

    def add(self, project: str, description: str) -> int:
        project_path = Path(project).expanduser().resolve(strict=True)
        if not project_path.is_dir() or not (project_path / '.git').exists():
            raise ValueError('Project must be an existing local Git repository')
        description = validate_task_description(description)
        project_key = str(project_path)
        now = time.time()
        with self.db:
            # Serialize the check and insert across separate ForgeOS windows.
            # A read followed by a deferred write could race another process.
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute(
                "SELECT id FROM tasks WHERE project=? AND description=? "
                "AND status IN ('queued','planning','planned','editing','testing') "
                "ORDER BY id DESC LIMIT 1",
                (project_key, description),
            ).fetchone()
            if existing is not None:
                raise ValueError(
                    f"Active task #{existing['id']} already has this description; "
                    "select the existing task instead")
            cur = self.db.execute(
                'INSERT INTO tasks(project,description,created,updated) VALUES(?,?,?,?)',
                (project_key, description, now, now))
            task_id = int(cur.lastrowid)
            self._record_event('task.created', task_id=task_id, status='queued')
        return task_id

    def list(self, *, brief: bool = False, limit: int | None = None,
             offset: int = 0):
        """Read a bounded task page, without materializing diagnostic blobs.

        The historical CLI may request the complete list with no limit.
        The desktop GUI MUST pass an explicit limit to avoid Tk/SQLite
        allocations proportional to the number of historical tasks.
        """
        if type(brief) is not bool:
            raise ValueError("brief must be boolean")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 500):
            raise ValueError("Page limit must be 1..500")
        if type(offset) is not int or offset < 0 or (offset and limit is None):
            raise ValueError("Page offset requires a bounded page limit")
        fields = ("id,status,substr(description,1,140) AS description"
                  if brief else "*")
        sql = f"SELECT {fields} FROM tasks ORDER BY id DESC"
        args = ()
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args = (limit, offset)
        return [dict(row) for row in self.db.execute(sql, args)]

    def iter_tasks(self):
        """Yield original full task rows one at a time for CLI export.

        Preserve the legacy full JSON representation without accumulating
        all historical result/diagnostic blobs in Python memory. Consumers
        must exhaust or close the generator before closing the task store.
        """
        cursor = self.db.execute("SELECT * FROM tasks ORDER BY id DESC")
        try:
            for row in cursor:
                yield dict(row)
        finally:
            cursor.close()

    def iter_blocked_checkpoints(self):
        """Yield only blocked checkpoints with bounded SQLite page allocations.

        SQL omits oversized result text before it reaches Python. Sixteen
        rows are fetched per keyset page (at most 1 MiB of checkpoint text);
        completed-task diagnostics are never read by the recovery scan.
        Pages are fully fetched before yielding, so the caller can update
        task states without keeping an open SQLite read cursor.
        """
        before_id = (1 << 63) - 1
        while True:
            rows = self.db.execute(
                "SELECT id,status, "
                "CASE WHEN length(CAST(result AS BLOB)) <= ? "
                "THEN result ELSE NULL END AS result, "
                "length(CAST(result AS BLOB)) AS checkpoint_bytes "
                "FROM tasks WHERE status='blocked' AND id<=? "
                "ORDER BY id DESC LIMIT ?",
                (MAX_CHECKPOINT_BYTES, before_id, CHECKPOINT_PAGE_SIZE),
            ).fetchall()
            if not rows:
                return
            for row in rows:
                yield dict(row)
            before_id = int(rows[-1]["id"]) - 1

    def counts(self) -> dict[str, int]:
        """SQL aggregate, not a full scan into Python task objects."""
        result = {"total": 0, "passed": 0, "failed": 0, "blocked": 0}
        for row in self.db.execute(
                "SELECT status,COUNT(*) AS n FROM tasks GROUP BY status"):
            count = int(row["n"])
            result["total"] += count
            if row["status"] in result:
                result[row["status"]] += count
        return result

    def get(self, task_id: int):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise ValueError(f'No task #{task_id}')
        return dict(row)

    def get_status(self, task_id: int) -> str:
        """Read a single status value for the GUI activity monitor."""
        row = self.db.execute(
            'SELECT status FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise ValueError(f'No task #{task_id}')
        return row['status']

    def get_task_inputs(self, task_id: int, *, resume: bool = False) -> dict:
        """Read only execution inputs; never fetch old diagnostic blobs.

        A resume needs the original checkpoint, but SQL rejects oversized
        values before SQLite transfers them into Python. Regular execution
        does not load prior results, errors or other historical task columns.
        """
        if type(resume) is not bool:
            raise ValueError("resume must be boolean")
        fields = (
            "id,project,status,"
            "CASE WHEN length(CAST(description AS BLOB)) <= ? "
            "THEN description ELSE NULL END AS description"
        )
        if resume:
            sql = (
                f"SELECT {fields},"
                "CASE WHEN length(CAST(result AS BLOB)) <= ? "
                "THEN result ELSE NULL END AS result,"
                "length(CAST(result AS BLOB)) AS checkpoint_bytes "
                "FROM tasks WHERE id=?"
            )
            args = (MAX_TASK_DESCRIPTION_BYTES, MAX_CHECKPOINT_BYTES, task_id)
        else:
            sql = f"SELECT {fields},'' AS result FROM tasks WHERE id=?"
            args = (MAX_TASK_DESCRIPTION_BYTES, task_id)
        row = self.db.execute(sql, args).fetchone()
        if row is None:
            raise ValueError(f"No task #{task_id}")
        if row["description"] is None:
            raise ValueError("Task description exceeds 16 KiB UTF-8")
        if resume and (row["checkpoint_bytes"] is None
                       or row["checkpoint_bytes"] > MAX_CHECKPOINT_BYTES):
            raise ValueError("Resume checkpoint exceeds 64 KiB safety limit")
        return dict(row)

    def update(self, task_id: int, status: str, *, error: str = '',
               result: str = '', increment: bool = False):
        if status not in STATUSES:
            raise ValueError('Invalid status')
        with self.db:
            cur = self.db.execute(
                'UPDATE tasks SET status=?,updated=?,last_error=?,result=?,attempts=attempts+? WHERE id=?',
                (status, time.time(), error, result, int(increment), task_id))
            if cur.rowcount == 1:
                self._record_event('task.updated', task_id=task_id, status=status)

    def transition(self, task_id: int, expected, status: str, *,
                   increment: bool = False, result: str | None = None,
                   error: str = '', lease_owner: str | None = None,
                   event_action: str = '', event_attempt: int | None = None,
                   event_artifact: str = '', event_evidence: str = '') -> bool:
        """Atomic state/result update, optionally guarded by task lease owner.

        The default preserves prior behavior and checkpoint content. When a
        lease owner is provided, stale workers cannot publish results after
        another process has reclaimed their task lease.
        """
        expected = tuple(expected)
        if (status not in STATUSES or not expected
                or any(item not in STATUSES for item in expected)):
            raise ValueError('Invalid status transition')
        if not isinstance(error, str) or (result is not None and not isinstance(result, str)):
            raise ValueError('Invalid transition result or error')
        if (lease_owner is not None and
                (not isinstance(lease_owner, str) or not lease_owner or len(lease_owner) > 128)):
            raise ValueError('Invalid lease owner')
        placeholders = ','.join('?' for _ in expected)
        now = time.time()
        sql = "UPDATE tasks SET status=?,updated=?,last_error=?,attempts=attempts+?"
        params = [status, now, error, int(increment)]
        if result is not None:
            sql += ",result=?"
            params.append(result)
        sql += f" WHERE id=? AND status IN ({placeholders})"
        params.extend((task_id, *expected))
        if lease_owner is not None:
            # Identity alone is insufficient: an expired lease must never
            # authorize a result write, even before recovery reclaims it.
            sql += (" AND EXISTS (SELECT 1 FROM task_leases l"
                    " WHERE l.task_id=tasks.id AND l.owner=?"
                    " AND l.heartbeat>=? AND l.heartbeat<=?)")
            params.extend((lease_owner, now - DEFAULT_LEASE_SECONDS, now + 60))
        with self.db:
            cur = self.db.execute(sql, params)
            if cur.rowcount == 1:
                self._record_event('task.transitioned', task_id=task_id,
                                   status=status, actor=lease_owner or '',
                                   action=event_action, attempt=event_attempt,
                                   artifact_ref=event_artifact,
                                   evidence_ref=event_evidence)
        return cur.rowcount == 1

    def acquire_lease(self, task_id: int, owner: str,
                      stale_after: int = DEFAULT_LEASE_SECONDS) -> bool:
        if not isinstance(owner, str) or not owner or len(owner) > 128:
            raise ValueError('Invalid lease owner')
        if (not isinstance(stale_after, int) or isinstance(stale_after, bool)
                or not 30 <= stale_after <= 86400):
            raise ValueError('Invalid lease duration')
        if self.db.execute('SELECT 1 FROM tasks WHERE id=?', (task_id,)).fetchone() is None:
            raise ValueError(f'No task #{task_id}')
        now = time.time()
        with self.db:
            self.db.execute(
                'DELETE FROM task_leases WHERE heartbeat<? OR heartbeat>?',
                (now - stale_after, now + 60))
            try:
                self.db.execute(
                    'INSERT INTO task_leases(task_id,owner,acquired,heartbeat) VALUES(?,?,?,?)',
                    (task_id, owner, now, now))
            except sqlite3.IntegrityError:
                return False
            self._record_event('lease.acquired', task_id=task_id, actor=owner)
        return True

    def heartbeat_lease(self, task_id: int, owner: str) -> bool:
        """Refresh only an owned *live* lease; never resurrect expiry."""
        now = time.time()
        with self.db:
            cur = self.db.execute(
                'UPDATE task_leases SET heartbeat=? WHERE task_id=? AND owner=? '
                'AND heartbeat>=? AND heartbeat<=?',
                (now, task_id, owner, now - DEFAULT_LEASE_SECONDS, now + 60))
        return cur.rowcount == 1

    def release_lease(self, task_id: int, owner: str) -> bool:
        with self.db:
            cur = self.db.execute(
                'DELETE FROM task_leases WHERE task_id=? AND owner=?',
                (task_id, owner))
            if cur.rowcount == 1:
                self._record_event('lease.released', task_id=task_id, actor=owner)
        return cur.rowcount == 1

    def recover(self, stale_after: int = DEFAULT_LEASE_SECONDS) -> int:
        """Recover interrupted tasks without disturbing live leased execution."""
        if (not isinstance(stale_after, int) or isinstance(stale_after, bool)
                or not 30 <= stale_after <= 86400):
            raise ValueError('Invalid lease duration')
        now = time.time()
        with self.db:
            self.db.execute(
                'DELETE FROM task_leases WHERE heartbeat<? OR heartbeat>?',
                (now - stale_after, now + 60))
            cur = self.db.execute(
                "UPDATE tasks SET status='blocked',updated=?,last_error=? "
                "WHERE status IN ('editing','testing') "
                "AND NOT EXISTS (SELECT 1 FROM task_leases l WHERE l.task_id=tasks.id)",
                (now, 'Interrupted execution: inspect workspace and recover edit journal before retrying'))
            changed = cur.rowcount
            cur = self.db.execute(
                "UPDATE tasks SET status='queued',updated=? WHERE status='planning' "
                "AND NOT EXISTS (SELECT 1 FROM task_leases l WHERE l.task_id=tasks.id)",
                (now,))
            changed += cur.rowcount
            if changed:
                self._record_event('recovery.reconciled', details=f'count={changed}')
        return changed

    def close(self):
        self.db.close()


MAX_OLLAMA_RESPONSE_BYTES = 8 * 1024 * 1024


class Ollama:
    def __init__(self, base='http://127.0.0.1:11434', model='qwen2.5-coder:3b'):
        if base != 'http://127.0.0.1:11434':
            raise ValueError('Starter only permits the local Ollama endpoint')
        self.base, self.model = base, model

    def _complete(self, prompt: str, *, edits: bool = False) -> str:
        """Bound network input, context length and RAM retention per request."""
        validate_local_prompt(prompt)
        from .model_routing import choose_installed_model, local_memory_settings
        if self.model == "auto":
            self.model = choose_installed_model(prompt)
        settings = local_memory_settings()
        options = {
            'num_ctx': settings['num_ctx'] if edits else
            min(4096, settings['num_ctx']),
            'num_predict': settings['num_predict'] if edits else
            min(900, settings['num_predict']),
        }
        arguments = {
            'model': self.model, 'prompt': prompt, 'stream': False,
            'options': options, 'keep_alive': settings['keep_alive'],
        }
        if edits:
            arguments['format'] = 'json'
        payload = json.dumps(arguments).encode()
        req = Request(self.base + '/api/generate', data=payload,
                      headers={'Content-Type': 'application/json'})
        # A socket timeout alone is not an end-to-end deadline: a server can
        # drip bytes forever. Bound the whole response by monotonic time and
        # read small chunks, without retaining unbounded response data.
        deadline = time.monotonic() + 180
        # BytesIO avoids a growing list of chunk objects and supports a
        # zero-copy getvalue() on CPython when the buffer is no longer mutated.
        bounded = BytesIO()
        total = 0
        with urlopen(req, timeout=30) as response:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Local AI response exceeded 180s total deadline")
                # read1 returns currently available data rather than waiting
                # for an entire requested block (important for slow streams).
                chunk = response.read1(
                    min(8192, MAX_OLLAMA_RESPONSE_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_OLLAMA_RESPONSE_BYTES:
                    raise RuntimeError(
                        'Local AI response exceeded safe memory limit; try smaller edits')
                bounded.write(chunk)
        if time.monotonic() >= deadline:
            raise TimeoutError("Local AI response exceeded 180s total deadline")
        body = json.loads(bounded.getvalue())
        if not isinstance(body, dict):
            raise RuntimeError('Ollama response was not structured JSON')
        result = body.get('response', '')
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError('Ollama returned no usable response')
        return result

    def generate_edits(self, prompt: str) -> str:
        """Request bounded JSON edits without loading an unbounded response."""
        return self._complete(prompt, edits=True)

    def plan(self, description: str) -> str:
        description = validate_task_description(description)
        prompt = (
            'You are a software planning assistant. Produce a concise, testable plan. '
            'Do not claim to have executed code. Include risks and acceptance tests.\n\nTask:\n'
            + description
        )
        return self._complete(prompt)


def _containment_name(low_integrity: bool) -> str:
    if os.name != 'nt':
        return 'none'
    return ('windows-restricted-token+job-object+low-integrity'
            if low_integrity else 'windows-restricted-token+job-object')


def _execute_test_process(argv, root: Path, timeout: int, *,
                          low_integrity: bool = False,
                          env: dict[str, str] | None = None):
    if os.name == 'nt':
        from .windows_containment import run_contained
        return run_contained(
            argv, root, timeout=timeout, low_integrity=low_integrity, env=env)
    if low_integrity:
        raise ValueError('Low-integrity execution is only available on Windows')
    from .output_capture import run_streamed_process
    return run_streamed_process(argv, root, timeout, env=env)


def run_test(project: str, command: str, timeout: int = 180, *,
             low_integrity: bool = False,
             env: dict[str, str] | None = None):
    """Run an allowlisted test preset under the requested containment level."""
    allowed = {
        'python-unittest': ['python', '-m', 'unittest', 'discover'],
        'python-pytest': ['python', '-m', 'pytest'],
        'npm-test': ['npm', 'test'],
        'cargo-test': ['cargo', 'test'],
        'go-test': ['go', 'test', './...'],
        'dotnet-test': ['dotnet', 'test'],
    }
    if command not in allowed:
        raise ValueError('Unknown test preset')
    # Match the Windows contained runtime's 1-hour ceiling so a bad
    # config cannot keep even a trusted host test alive indefinitely.
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError('timeout must be 1..3600 seconds')
    if not isinstance(low_integrity, bool):
        raise ValueError('low_integrity must be boolean')
    if env is not None and not isinstance(env, dict):
        raise ValueError('env must be a dictionary or None')
    root = Path(project).expanduser().resolve(strict=True)
    if not root.is_dir() or not (root / '.git').exists():
        raise ValueError('Not a local Git repository')
    try:
        from .diagnostics import optimize_test_result
        execution = _execute_test_process(
            allowed[command], root, timeout,
            low_integrity=low_integrity, env=env)
        outcome = optimize_test_result(execution, command)
        try:
            from .optimizer_metrics import OptimizationMetrics
            OptimizationMetrics().record(outcome)
        except Exception:
            # Metrics must never change the outcome of a test.
            outcome["metrics_recorded"] = False
        return outcome
    except subprocess.TimeoutExpired:
        # No complete test evidence is available. Never send this to an
        # autonomous repair loop as an ordinary, reproducible test failure.
        return {'passed': False, 'returncode': None,
                'output': f'Test timed out after {timeout}s',
                'capture_complete': False, 'capture_error': 'test-timeout',
                'containment': _containment_name(low_integrity)}
    except OSError as exc:
        # Includes unavailable executables and native containment failures.
        # Both are setup/runtime blockers, not verified application defects.
        return {'passed': False, 'returncode': None,
                'output': f'Test runtime unavailable: {str(exc)[-4000:]}',
                'capture_complete': False,
                'capture_error': 'test-runtime-unavailable',
                'containment': _containment_name(low_integrity)}
