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
        from io import BytesIO
        return self.put_stream(BytesIO(raw))

    def put_stream(self, source) -> str:
        """Atomically archive a byte stream without buffering it all in RAM."""
        fd, name = tempfile.mkstemp(prefix=".forgeos-", suffix=".tmp", dir=self.root)
        temporary = Path(name)
        digest = hashlib.sha256()
        count = 0
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as destination:
                with gzip.GzipFile(fileobj=destination, mode="wb", mtime=0) as stream:
                    while True:
                        part = source.read(64 * 1024)
                        if not part:
                            break
                        if not isinstance(part, bytes):
                            raise TypeError("Archive stream must return bytes")
                        count += len(part)
                        if count > MAX_ARCHIVE_BYTES:
                            raise ValueError("Output exceeds evidence archive limit")
                        digest.update(part)
                        stream.write(part)
                destination.flush()
                os.fsync(destination.fileno())
            reference = digest.hexdigest()
            target = self._path(reference)
            if target.is_symlink():
                raise ValueError("Refusing to overwrite symlinked evidence")
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



def parse_test_summary(text: str, preset: str) -> dict[str, Any]:
    """Three-tier non-authoritative test analytics: JSON, text, or raw fallback.

    The parser never changes return codes or test pass/fail conclusions.
    Unsupported, malformed, or ambiguous formats degrade without raising.
    """
    import json
    if not isinstance(text, str):
        return {"tier": "raw", "counts": {}}
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        # Common reporter shapes. Only accept explicit nonnegative integer counts.
        data = parsed.get("summary", parsed)
        if isinstance(data, dict):
            names = {
                "passed": ("passed", "numPassedTests"),
                "failed": ("failed", "numFailedTests"),
                "skipped": ("skipped", "numPendingTests"),
                "total": ("total", "numTotalTests"),
            }
            counts = {}
            for label, alternatives in names.items():
                for key in alternatives:
                    value = data.get(key)
                    if type(value) is int and value >= 0:
                        counts[label] = value
                        break
            if counts:
                return {"tier": "json", "counts": counts}
    counts: dict[str, int] = {}
    if preset == "python-unittest":
        total = re.search(r"(?m)^Ran\s+(\d+)\s+tests?\s+in\s+", text)
        if total:
            counts["total"] = int(total.group(1))
        failed = re.search(r"(?m)^FAILED\s*\(([^)]*)\)", text)
        if failed:
            for key, number in re.findall(r"\b(failures|errors|skipped)=(\d+)\b", failed.group(1)):
                counts[{"failures": "failed", "errors": "errors", "skipped": "skipped"}[key]] = int(number)
        if counts.get("total") is not None and (re.search(r"(?m)^OK(?:\s*\(|$)", text) or failed):
            return {"tier": "text", "counts": counts}
    elif preset in {"python-pytest", "npm-test", "cargo-test", "go-test", "dotnet-test"}:
        # Non-Python runners differ; a generic line is only a partial hint.
        summary_lines = [line for line in text.splitlines()[-30:]
                         if re.search(r"\b\d+\s+(passed|failed|skipped|errors?)\b", line, re.I)]
        if summary_lines:
            for number, label in re.findall(
                r"\b(\d+)\s+(passed|failed|skipped|errors?)\b",
                summary_lines[-1], re.I
            ):
                counts[label.lower()] = int(number)
            if counts:
                return {"tier": "text", "counts": counts}
    return {"tier": "degraded" if counts else "raw", "counts": counts}

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
    outcome["parsed_test"] = parse_test_summary(output, preset)
    from .error_learning import classify_failure
    outcome["failure_type"] = classify_failure(
        output, passed=result.get("passed"))
    measured = result.get("capture_source_bytes")
    raw_bytes = (measured if type(measured) is int and measured >= 0
                 else len(output.encode("utf-8")))
    full_capture = result.get("capture_complete") is not False
    stored_reference = result.get("evidence_reference")
    try:
        if stored_reference is not None:
            if not isinstance(stored_reference, str) or not _REF.fullmatch(stored_reference):
                raise ValueError("Invalid prearchived evidence reference")
            reference = stored_reference
        else:
            reference = (store if store is not None else EvidenceStore()).put(output)
    except (OSError, ValueError, UnicodeError, TypeError) as exc:
        outcome["optimization"] = {
            "archived": False, "optimized": False, "source_bytes": raw_bytes,
            "reason": f"Archiving failed: {type(exc).__name__}"}
        return outcome
    try:
        from .output_rules import TrustedOutputRules, deduplicate_output
        rules, filter_status = TrustedOutputRules().enabled_rules()
        prepared, removed = deduplicate_output(output, preset, rules)
    except Exception:
        prepared, removed, filter_status = output, 0, "unavailable"
    concise = _focus(prepared, result.get("passed") is False, budget)
    if concise != output:
        kind = ("Full original diagnostic" if full_capture
                else "Partial captured diagnostic (original output exceeded limit)")
        concise += f"\n[{kind}: forgeos diagnostics show {reference}]"
    if not full_capture:
        concise += "\n[ForgeOS: diagnostic capture incomplete; manual review required]"
    # Do not inflate tiny results just to include a recall hint.
    if full_capture and len(concise.encode("utf-8")) >= raw_bytes:
        concise, removed = output, 0
    context_bytes = len(concise.encode("utf-8"))
    outcome["output"] = concise
    outcome["optimization"] = {
        "archived": True, "reference": reference, "source_bytes": raw_bytes,
        "context_bytes": context_bytes, "saved_bytes": max(0, raw_bytes - context_bytes),
        "optimized": concise != output, "preset": preset,
        "deduplicated_lines": removed, "filter_status": filter_status,
        "capture_complete": full_capture,
        "source_prearchived": stored_reference is not None,
    }
    return outcome
