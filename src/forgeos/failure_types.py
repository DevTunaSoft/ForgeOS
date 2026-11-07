"""Advisory, deterministic failure taxonomy for local autonomous repair.

This classifier never executes output, does not make safety decisions, and
cannot change a test process's authoritative exit code or pass/fail status.
"""
from __future__ import annotations

import re

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
