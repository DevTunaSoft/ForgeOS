"""Persistent task store and cautious, bounded development workflow."""
from __future__ import annotations
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

STATUSES = {'queued','planning','planned','editing','testing','passed','failed','blocked'}
DEFAULT_LEASE_SECONDS = 600


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(SCHEMA)
        self.db.execute(LEASE_SCHEMA)
        self.db.commit()

    def add(self, project: str, description: str) -> int:
        project_path = Path(project).expanduser().resolve(strict=True)
        if not project_path.is_dir() or not (project_path / '.git').exists():
            raise ValueError('Project must be an existing local Git repository')
        if not description.strip():
            raise ValueError('Task description is required')
        now = time.time()
        with self.db:
            cur = self.db.execute(
                'INSERT INTO tasks(project,description,created,updated) VALUES(?,?,?,?)',
                (str(project_path), description.strip(), now, now))
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

    def update(self, task_id: int, status: str, *, error: str = '',
               result: str = '', increment: bool = False):
        if status not in STATUSES:
            raise ValueError('Invalid status')
        with self.db:
            self.db.execute(
                'UPDATE tasks SET status=?,updated=?,last_error=?,result=?,attempts=attempts+? WHERE id=?',
                (status, time.time(), error, result, int(increment), task_id))

    def transition(self, task_id: int, expected, status: str, *,
                   increment: bool = False) -> bool:
        """Atomically change status while preserving checkpoint/result data."""
        expected = tuple(expected)
        if status not in STATUSES or not expected or any(item not in STATUSES for item in expected):
            raise ValueError('Invalid status transition')
        placeholders = ','.join('?' for _ in expected)
        with self.db:
            cur = self.db.execute(
                f"UPDATE tasks SET status=?,updated=?,last_error='',attempts=attempts+? "
                f"WHERE id=? AND status IN ({placeholders})",
                (status, time.time(), int(increment), task_id, *expected))
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
        now = time.time()
        with self.db:
            cur = self.db.execute(
                'UPDATE task_leases SET heartbeat=? WHERE task_id=? AND owner=?',
                (now, task_id, owner))
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
                "UPDATE tasks SET status='queued',updated=? WHERE status='planning'",
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
        with urlopen(req, timeout=180) as response:
            bounded = response.read(MAX_OLLAMA_RESPONSE_BYTES + 1)
        if len(bounded) > MAX_OLLAMA_RESPONSE_BYTES:
            raise RuntimeError(
                'Local AI response exceeded safe memory limit; try smaller edits')
        body = json.loads(bounded)
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
        return {'passed': False, 'returncode': None,
                'output': f'Test timed out after {timeout}s',
                'containment': _containment_name(low_integrity)}
    except OSError as exc:
        return {'passed': False, 'returncode': None,
                'output': f'Test executable unavailable: {exc}',
                'containment': _containment_name(low_integrity)}
