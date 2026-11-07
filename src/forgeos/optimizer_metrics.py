"""Local-only aggregate optimization metrics; no raw output or project paths."""
from __future__ import annotations
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import time

SCHEMA = """CREATE TABLE IF NOT EXISTS optimization_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp REAL NOT NULL, preset TEXT NOT NULL, parser_tier TEXT NOT NULL,
  passed INTEGER NOT NULL, archived INTEGER NOT NULL,
  source_bytes INTEGER NOT NULL, context_bytes INTEGER NOT NULL,
  saved_bytes INTEGER NOT NULL
)"""
VALID_TIERS = {"json", "text", "degraded", "raw"}


class OptimizationMetrics:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else Path.home() / ".forgeos" / "optimization.sqlite3"
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            self.path.parent.chmod(0o700)
        with self._connect() as db:
            db.execute(SCHEMA)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA busy_timeout=10000")
            with connection:
                yield connection
        finally:
            connection.close()

    def record(self, outcome: dict) -> None:
        optimization = outcome.get("optimization")
        if not isinstance(optimization, dict):
            raise ValueError("Missing optimization metadata")
        preset = optimization.get("preset", "unknown")
        if not isinstance(preset, str) or not 0 < len(preset) < 61 or not all(
                ch.isalnum() or ch == "-" for ch in preset):
            raise ValueError("Invalid preset")
        source = optimization.get("source_bytes")
        context = optimization.get("context_bytes", source)
        archived = optimization.get("archived")
        if (type(source) is not int or type(context) is not int or
                source < 0 or context < 0 or type(archived) is not bool):
            raise ValueError("Invalid diagnostic byte counts")
        tier = outcome.get("parsed_test", {}).get("tier", "raw")
        if tier not in VALID_TIERS:
            tier = "raw"
        with self._connect() as db:
            db.execute(
                """INSERT INTO optimization_metrics
                (timestamp,preset,parser_tier,passed,archived,source_bytes,context_bytes,saved_bytes)
                VALUES (?,?,?,?,?,?,?,?)""",
                (time.time(), preset, tier, int(outcome.get("passed") is True),
                 int(archived), source, context, max(0, source-context)),
            )

    def stats(self) -> dict:
        with self._connect() as db:
            row = db.execute(
                """SELECT COUNT(*),COALESCE(SUM(source_bytes),0),
                COALESCE(SUM(context_bytes),0),COALESCE(SUM(saved_bytes),0),
                COALESCE(SUM(archived),0),
                COALESCE(SUM(CASE WHEN parser_tier='raw' THEN 1 ELSE 0 END),0)
                FROM optimization_metrics"""
            ).fetchone()
        count, source, context, saved, archived, raw = map(int, row)
        return {
            "runs": count, "archived_runs": archived,
            "raw_bytes": source, "context_bytes": context,
            "bytes_saved": saved,
            "reduction_percent": round((100.0 * saved/source) if source else 0.0, 2),
            "approx_tokens_saved_bytes_div_4": saved // 4,
            "unparsed_runs": raw,
            "note": "Estimated command-output savings, not exact LLM tokens or dollar costs",
        }
