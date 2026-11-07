"""Local repair outcomes: store failure signatures, never raw logs or code.

Only test-verified repair outcomes count as success. This module does not
execute remembered commands or inject previous output into a model's prompt.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sqlite3
import time

CATEGORIES = (
    "AssertionError", "ModuleNotFoundError", "ImportError", "FileNotFoundError",
    "PermissionError", "SyntaxError", "TypeError", "ValueError", "TimeoutError",
    "ConnectionError", "CalledProcessError", "OSError", "Traceback", "FAILED",
    "ERROR", "error", "failed", "panic", "timeout",
)
_CATEGORY = re.compile("|".join(re.escape(x) for x in CATEGORIES), re.IGNORECASE)
_HEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_NUM = re.compile(r"\b\d+\b")
_PATH = re.compile(r"(?:[A-Za-z]:\\|/)[^\s:'\"]+")
_WHITESPACE = re.compile(r"\s+")
_MODEL = re.compile(r"[A-Za-z0-9._:-]{1,100}\Z")
_PRESET = re.compile(r"[A-Za-z0-9-]{1,60}\Z")

DDL = """CREATE TABLE IF NOT EXISTS repair_history (
 signature TEXT NOT NULL, category TEXT NOT NULL, preset TEXT NOT NULL,
 model TEXT NOT NULL, successes INTEGER NOT NULL DEFAULT 0,
 failures INTEGER NOT NULL DEFAULT 0, last_verified REAL NOT NULL,
 PRIMARY KEY (signature, preset, model)
)"""


def fingerprint(output: str) -> tuple[str, str]:
    """Return a stable type and hash, not the source error message."""
    if not isinstance(output, str):
        raise TypeError("Diagnostic output must be text")
    matches = [line.strip()[:600] for line in output.splitlines()
               if _CATEGORY.search(line)]
    line = matches[-1] if matches else output[-600:]
    hit = _CATEGORY.search(line)
    category = hit.group(0) if hit else "unclassified"
    category = next((c for c in CATEGORIES if c.lower() == category.lower()), "unclassified")
    normalized = _WHITESPACE.sub(" ", _NUM.sub("#", _HEX.sub("@", _PATH.sub("<path>", line)))).lower()
    # The hash intentionally includes normalized evidence but never stores it.
    return category, hashlib.sha256((category.lower() + ":" + normalized).encode()).hexdigest()


class RepairMemory:
    """SQLite-backed test-verified repair statistics, no execution privileges."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else Path.home() / ".forgeos" / "repair_memory.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.path.parent.chmod(0o700)
        with self._connect() as db:
            db.execute(DDL)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.path), timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def record(self, failure_output: str, *, preset: str, model: str,
               verified_success: bool) -> dict:
        """Called only after completion of a real test/repair attempt."""
        if type(verified_success) is not bool:
            raise ValueError("Verification must be boolean")
        if not _PRESET.fullmatch(preset) or not _MODEL.fullmatch(model):
            raise ValueError("Invalid test preset or model identifier")
        category, signature = fingerprint(failure_output)
        successes, failures = (1, 0) if verified_success else (0, 1)
        with self._connect() as db:
            db.execute(
                """INSERT INTO repair_history
                   (signature,category,preset,model,successes,failures,last_verified)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(signature,preset,model) DO UPDATE SET
                   successes=successes+excluded.successes,
                   failures=failures+excluded.failures,
                   last_verified=excluded.last_verified""",
                (signature, category, preset, model, successes, failures, time.time()),
            )
        return {"signature": signature, "category": category,
                "verified_success": verified_success}

    def recommendations(self, *, preset: str, model: str) -> list[dict]:
        """Report only evidence-based statistics; do not provide executable actions."""
        if not _PRESET.fullmatch(preset) or not _MODEL.fullmatch(model):
            raise ValueError("Invalid test preset or model identifier")
        with self._connect() as db:
            rows = db.execute(
                """SELECT signature,category,successes,failures
                   FROM repair_history WHERE preset=? AND model=?
                   ORDER BY successes DESC, last_verified DESC LIMIT 100""",
                (preset, model),
            ).fetchall()
        results = []
        for key, category, successes, failures in rows:
            count = successes + failures
            rate = successes / count if count else 0.0
            results.append({
                "signature": key, "category": category,
                "successes": successes, "failures": failures,
                "empirical_success_rate": round(rate, 3),
                "eligible_for_review": successes >= 3 and count >= 4 and rate >= 0.8,
            })
        return results

    def stats(self) -> dict:
        with self._connect() as db:
            count, succeeded, failed = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(successes),0), COALESCE(SUM(failures),0) "
                "FROM repair_history"
            ).fetchone()
        return {"signatures": count, "verified_repairs": succeeded, "failed_repairs": failed}
