"""Local repair outcomes: store failure signatures, never raw logs or code.

Only test-verified repair outcomes count as success. This module does not
execute remembered commands or inject previous output into a model's prompt.
"""
from __future__ import annotations

from contextlib import contextmanager
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

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            with db:
                yield db
        finally:
            db.close()

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


# Advisory display taxonomy, kept next to the existing stable repair fingerprints.
# Do not change fingerprint() hashing or stored category names: historical
# repair records rely on them. Both are analytical, never authoritative.
PATTERNS = (
    ("security_boundary", r"(?i)(?:appcontainer|sandbox violation|escape attempt|security boundary)"),
    ("resource_exhaustion", r"(?i)(?:out of memory|memoryerror|no space left|disk full|ENOSPC)"),
    ("permission", r"(?i)(?:permissionerror|permission denied|access is denied|EACCES|EPERM)"),
    ("timeout", r"(?i)(?:timed out|timeoutexpired|deadline exceeded|timeout after)"),
    ("dependency", r"(?i)(?:modulenotfounderror|no module named|cannot find module|missing dependency)"),
    ("syntax", r"(?i)(?:syntaxerror|indentationerror|unexpected token|invalid syntax)"),
    ("assertion", r"(?i)(?:assertionerror|assert .*failed|failed assertions?)"),
    ("network", r"(?i)(?:connection refused|could not resolve host|connectionerror|dns lookup failed)"),
    ("environment", r"(?i)(?:executable unavailable|command not found|not recognized as an internal)"),
    ("build", r"(?i)(?:build failed|compilation failed|compiler error|linker error)"),
)
COMPILED = [(label, re.compile(pattern)) for label, pattern in PATTERNS]


def classify_failure(output: str, *, passed: bool | None = None) -> dict:
    """Report up to three normalized types; raw untrusted output stays outside."""
    if passed is True:
        return {"category": "none", "signals": [], "method": "test-status"}
    if not isinstance(output, str):
        return {"category": "unknown", "signals": [], "method": "unclassified"}
    # Keep the first and last segments when logs exceed the analysis budget.
    sample = output if len(output) <= 180000 else output[:40000] + output[-140000:]
    labels = [category for category, pattern in COMPILED if pattern.search(sample)]
    return {
        "category": labels[0] if labels else "unknown",
        "signals": labels[:3],
        "method": "heuristic" if labels else "unclassified",
    }
