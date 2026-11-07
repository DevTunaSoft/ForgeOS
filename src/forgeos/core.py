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

    def list(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM tasks ORDER BY id DESC')]

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


class Ollama:
    def __init__(self, base='http://127.0.0.1:11434', model='qwen2.5-coder:3b'):
        if base != 'http://127.0.0.1:11434':
            raise ValueError('Starter only permits the local Ollama endpoint')
        self.base, self.model = base, model

    def generate_edits(self, prompt: str) -> str:
        """Request complete JSON edits rather than a planning essay."""
        payload = json.dumps({
            'model': self.model, 'prompt': prompt, 'stream': False, 'format': 'json',
            'options': {'num_ctx': 8192, 'num_predict': 4096},
        }).encode()
        req = Request(self.base + '/api/generate', data=payload,
                      headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=180) as response:
            body = json.load(response)
        result = body.get('response', '')
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError('Ollama returned no edits')
        return result

    def plan(self, description: str) -> str:
        prompt = (
            'You are a software planning assistant. Produce a concise, testable plan. '
            'Do not claim to have executed code. Include risks and acceptance tests.\n\nTask:\n'
            + description
        )
        payload = json.dumps({
            'model': self.model, 'prompt': prompt, 'stream': False,
            'options': {'num_ctx': 4096, 'num_predict': 900},
        }).encode()
        req = Request(self.base + '/api/generate', data=payload,
                      headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=180) as response:
            body = json.load(response)
        result = body.get('response', '')
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError('Ollama returned no plan')
        return result


def _execute_test_process(argv, root: Path, timeout: int):
    if os.name == 'nt':
        from .windows_containment import run_contained
        return run_contained(argv, root, timeout=timeout)
    result = subprocess.run(
        argv, cwd=root, capture_output=True, text=True,
        timeout=timeout, shell=False, errors='replace')
    return {
        'passed': result.returncode == 0,
        'returncode': result.returncode,
        'output': (result.stdout + '\n' + result.stderr)[-16000:],
        'containment': 'none',
    }


def run_test(project: str, command: str, timeout: int = 180):
    """Run an allowlisted test preset; Windows uses Job Object containment."""
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
    root = Path(project).expanduser().resolve(strict=True)
    if not root.is_dir() or not (root / '.git').exists():
        raise ValueError('Not a local Git repository')
    try:
        return _execute_test_process(allowed[command], root, timeout)
    except subprocess.TimeoutExpired:
        return {'passed': False, 'returncode': None,
                'output': f'Test timed out after {timeout}s',
                'containment': 'windows-restricted-token+job-object' if os.name == 'nt' else 'none'}
    except OSError as exc:
        return {'passed': False, 'returncode': None,
                'output': f'Test executable unavailable: {exc}',
                'containment': 'windows-job-object' if os.name == 'nt' else 'none'}
