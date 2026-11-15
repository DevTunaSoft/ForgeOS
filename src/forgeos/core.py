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
BACKUP_MANIFEST_FORMAT = "forgeos-sqlite-backup-v1"
MAX_BACKUP_MANIFEST_BYTES = 1024


class LeaseLostError(RuntimeError):
    """An expired, reclaimed, or concurrently changed task cannot be published."""


def _publish_database_copy(staging: Path, target: Path) -> None:
    """Publish a staged SQLite copy without replacing a concurrent destination.

    Windows rename fails when the destination already exists, giving the same
    no-overwrite publication contract without relying on hard-link support.
    POSIX keeps the hard-link publication used by the existing same-volume
    atomic path.
    """
    if os.name == "nt":
        os.rename(staging, target)
    else:
        os.link(staging, target)


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("Input contains invalid Unicode text") from exc


def _database_file_digest(path: Path) -> tuple[int, str]:
    """Stream a database digest without loading the file into memory."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
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
        if source.is_symlink() or not source.is_file():
            raise ValueError("Task database must be an existing regular file")
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            _verify_task_database_connection(db)

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
        if src.is_symlink() or not src.is_file():
            raise ValueError("Restore source must be a regular SQLite database file")
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
            with staged.open("rb+") as stream:
                os.fsync(stream.fileno())
            # No-overwrite publication also protects concurrent users.
            _publish_database_copy(staged, target)
            return target
        finally:
            staged.unlink(missing_ok=True)

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
