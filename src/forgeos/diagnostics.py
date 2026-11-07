"""Recoverable summaries for local AI. Compression cannot alter exit codes.

Full output is archived first. If archiving fails, no output is shortened.
"""
from __future__ import annotations
import gzip
import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Any

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
DEFAULT_BUDGET = 12000
_REF = re.compile(r"[0-9a-f]{64}\Z")
_ERROR = re.compile(
    r"(?i)(?:\b(?:error|fail(?:ed|ure|ing)?|exception|traceback|fatal|"
    r"assertionerror|panic|syntaxerror|timeout|timed out)\b|^E\s+|^F\s+)"
)
_SUMMARY = re.compile(
    r"(?i)(?:\b\d+\s+(?:passed|failed|errors?|tests?|skipped)\b|"
    r"^Ran \d+ tests?|^FAILED\s*\(|^OK$)"
)
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class EvidenceStore:
    """Private SHA-256-addressed gzip files with atomic writes and verification."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else Path.home() / ".forgeos" / "diagnostics"
        if self.root.is_symlink():
            raise ValueError("Diagnostic directory must not be symlinked")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.root.is_dir() or self.root.is_symlink():
            raise ValueError("Diagnostic directory must be a real directory")
        if os.name != "nt":
            self.root.chmod(0o700)

    def _path(self, reference: str) -> Path:
        if not isinstance(reference, str) or not _REF.fullmatch(reference):
            raise ValueError("Invalid diagnostic reference")
        return self.root / (reference + ".txt.gz")

    def put(self, text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("Output must be text")
        raw = text.encode("utf-8")
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise ValueError("Output exceeds evidence archive limit")
        reference = hashlib.sha256(raw).hexdigest()
        target = self._path(reference)
        if target.is_symlink():
            raise ValueError("Refusing to overwrite symlinked evidence")
        if target.exists():
            try:
                if self.get(reference) == text:
                    return reference
            except (OSError, ValueError, UnicodeError):
                pass
        fd, name = tempfile.mkstemp(prefix=".forgeos-", suffix=".tmp", dir=self.root)
        temporary = Path(name)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as destination:
                with gzip.GzipFile(fileobj=destination, mode="wb", mtime=0) as stream:
                    stream.write(raw)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, target)
            return reference
        finally:
            if temporary.exists():
                temporary.unlink()

    def get(self, reference: str) -> str:
        path = self._path(reference)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Diagnostic evidence unavailable or unsafe")
        with gzip.open(path, "rb") as source:
            raw = source.read(MAX_ARCHIVE_BYTES + 1)
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise ValueError("Diagnostic archive exceeds size limit")
        if hashlib.sha256(raw).hexdigest() != reference:
            raise ValueError("Diagnostic integrity check failed")
        return raw.decode("utf-8")


def _focus(text: str, failed: bool, budget: int) -> str:
    normal = _ANSI.sub("", text).replace("\r\n", "\n")
    if len(normal) <= budget:
        return normal
    lines = normal.splitlines()
    if not lines:
        return normal[:budget]
    critical, summaries = set(), set()
    for i, line in enumerate(lines):
        if _ERROR.search(line):
            critical.update(range(max(0, i - 2), min(len(lines), i + (14 if failed else 4))))
        elif _SUMMARY.search(line):
            summaries.add(i)
    priority = (sorted(critical) + sorted(summaries)
                + list(range(max(0, len(lines) - 45), len(lines)))
                + list(range(min(22, len(lines)))))
    selected: set[int] = set()
    spent = 0
    allowance = max(128, budget - 850)
    for i in priority:
        if i in selected:
            continue
        cost = len(lines[i]) + 1
        if spent + cost <= allowance:
            selected.add(i)
            spent += cost
    if not selected:
        return normal[:allowance] + "\n[More lines omitted]"
    parts: list[str] = []
    previous = -1
    for i in sorted(selected):
        if i > previous + 1:
            parts.append(f"[... {i - previous - 1} lines omitted ...]")
        parts.append(lines[i])
        previous = i
    if previous + 1 < len(lines):
        parts.append(f"[... {len(lines) - previous - 1} lines omitted ...]")
    summary = "\n".join(parts)
    if len(summary) > budget - 180:
        summary = summary[:budget - 220] + "\n[Summary clipped; retrieve full evidence]"
    return summary


def optimize_test_result(result: dict[str, Any], preset: str, *,
                         store: EvidenceStore | None = None,
                         budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """Archive exact output, then create a compact context without modifying status."""
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1024:
        raise ValueError("Budget must be >= 1024")
    output = result.get("output")
    if not isinstance(output, str):
        return dict(result)
    outcome = dict(result)
    raw_bytes = len(output.encode("utf-8"))
    try:
        reference = (store if store is not None else EvidenceStore()).put(output)
    except (OSError, ValueError, UnicodeError) as exc:
        outcome["optimization"] = {
            "archived": False, "optimized": False, "source_bytes": raw_bytes,
            "reason": f"Archiving failed: {type(exc).__name__}"}
        return outcome
    concise = _focus(output, result.get("passed") is False, budget)
    if concise != output:
        concise += f"\n[Full original diagnostic: forgeos diagnostics show {reference}]"
    context_bytes = len(concise.encode("utf-8"))
    outcome["output"] = concise
    outcome["optimization"] = {
        "archived": True, "reference": reference, "source_bytes": raw_bytes,
        "context_bytes": context_bytes, "saved_bytes": max(0, raw_bytes - context_bytes),
        "optimized": concise != output, "preset": preset,
    }
    return outcome
