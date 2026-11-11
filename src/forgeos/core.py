"""Persistent task store and cautious, bounded development workflow."""
from __future__ import annotations
from io import BytesIO
import json
import os
from pathlib import Path
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


class LeaseLostError(RuntimeError):
    """An expired, reclaimed, or concurrently changed task cannot be published."""


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("Input contains invalid Unicode text") from exc


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


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(SCHEMA)
        self.db.execute(LEASE_SCHEMA)
        self.db.execute(ACTIVE_TASK_LOOKUP_INDEX)
        self.db.commit()

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
        return int(cur.lastrowid)

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
            self.db.execute(
                'UPDATE tasks SET status=?,updated=?,last_error=?,result=?,attempts=attempts+? WHERE id=?',
                (status, time.time(), error, result, int(increment), task_id))

    def transition(self, task_id: int, expected, status: str, *,
                   increment: bool = False, result: str | None = None,
                   error: str = '', lease_owner: str | None = None) -> bool:
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
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError('timeout must be a positive integer')
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
