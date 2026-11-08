"""One-shot managed-model baseline evidence for ForgeOS issue #55."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import tempfile
import uuid

from .engine import EditError, parse_edits
from .model_routing import GIB, system_memory

SCHEMA_VERSION = 1
QUANTIZATION = re.compile(
    r"(?:^|[-_.])(Q[2-8](?:_[A-Z0-9]+)+)(?:[-_.]|$)", re.I)





SCREENING_ROLES = (
    "planning", "implementation", "review_refactor", "debugging",
    "test_generation", "security_review", "documentation",
    "context_summarization",
)


@dataclass(frozen=True)
class ScreeningTask:
    id: str
    role: str
    kind: str
    prompt: str
    expected_items: tuple[str, ...] = ()
    path: str | None = None
    required_markers: tuple[str, ...] = ()
    forbidden_markers: tuple[str, ...] = ()


def _items(task_id: str, role: str, scenario: str,
           expected: tuple[str, ...]) -> ScreeningTask:
    return ScreeningTask(
        task_id, role, "items",
        scenario + "\nReturn only JSON exactly shaped as "
        '{"items":["id", "..."]}. Preserve the required order; no markdown.',
        expected_items=expected)


def _edit(task_id: str, role: str, scenario: str, path: str,
          required: tuple[str, ...], forbidden: tuple[str, ...] = ()) -> ScreeningTask:
    return ScreeningTask(
        task_id, role, "edit",
        scenario + "\nReturn only the ForgeOS edit manifest "
        '{"files":[{"path":"' + path + '","content":"..."}]}. '
        "Do not edit any other file and do not use markdown.",
        path=path, required_markers=required, forbidden_markers=forbidden)


SCREENING_TASKS = (
    _items("planning-01", "planning",
           "Order these constrained stages: checkpoint before edit; edit before test; test before publish.",
           ("checkpoint", "edit", "test", "publish")),
    _items("planning-02", "planning",
           "Order a database rollout: backup before migrate; migrate before verify; verify before cutover.",
           ("backup", "migrate", "verify", "cutover")),
    _items("planning-03", "planning",
           "Order a bug fix: reproduce before isolate; isolate before fix; fix before regression.",
           ("reproduce", "isolate", "fix", "regression")),
    _items("planning-04", "planning",
           "Order memory hardening: measure baseline before set-limit; set-limit before stress; stress before accept.",
           ("measure", "set-limit", "stress", "accept")),
    _items("planning-05", "planning",
           "Order a security release: threat-model before contain; contain before audit; audit before release.",
           ("threat-model", "contain", "audit", "release")),

    _edit("implementation-01", "implementation",
          "Implement add(a, b) in solution.py and return a + b.",
          "solution.py", ("def add", "return a + b")),
    _edit("implementation-02", "implementation",
          "Implement is_even(n) in solution.py using modulo and return a boolean.",
          "solution.py", ("def is_even", "% 2", "== 0")),
    _edit("implementation-03", "implementation",
          "Implement clamp(value, low, high) in solution.py using min and max.",
          "solution.py", ("def clamp", "min(", "max(")),
    _edit("implementation-04", "implementation",
          "Implement safe_divide(a, b) in solution.py; return None when b is zero, otherwise a / b.",
          "solution.py", ("def safe_divide", "return None", "a / b")),
    _edit("implementation-05", "implementation",
          "Implement first_or_none(items) in solution.py; return the first item when present, otherwise None.",
          "solution.py", ("def first_or_none", "return None")),

    _edit("review-01", "review_refactor",
          "Refactor refactor.py so read_text(path) uses a with-open context manager and UTF-8.",
          "refactor.py", ("def read_text", "with open", "encoding=")),
    _edit("review-02", "review_refactor",
          "Refactor refactor.py so normalize(name) strips whitespace once and lowercases the result.",
          "refactor.py", ("def normalize", ".strip()", ".lower()")),
    _edit("review-03", "review_refactor",
          "Refactor refactor.py so positive(values) returns a list comprehension containing only values greater than zero.",
          "refactor.py", ("def positive", "[", "for ", "> 0")),
    _edit("review-04", "review_refactor",
          "Refactor refactor.py so total(values) delegates to built-in sum rather than a manual accumulator.",
          "refactor.py", ("def total", "sum(")),
    _edit("review-05", "review_refactor",
          "Refactor refactor.py so has_items(values) returns bool(values) directly.",
          "refactor.py", ("def has_items", "bool(values)")),

    _edit("debug-01", "debugging",
          "bug.py currently has def subtract(a, b): return a + b. Fix subtract.",
          "bug.py", ("def subtract", "return a - b"), ("return a + b",)),
    _edit("debug-02", "debugging",
          "bug.py currently has def is_odd(n): return n % 2 == 0. Fix is_odd.",
          "bug.py", ("def is_odd", "% 2", "!= 0")),
    _edit("debug-03", "debugging",
          "bug.py currently has def last(items): return items[0]. Fix it to return the last element.",
          "bug.py", ("def last", "[-1]"), ("[0]",)),
    _edit("debug-04", "debugging",
          "bug.py currently has def greet(name): return 'Hello ' + 'name'. Fix it to use the argument.",
          "bug.py", ("def greet", "name"), ("'name'", '"name"')),
    _edit("debug-05", "debugging",
          "bug.py currently has def square(n): return n * 2. Fix square.",
          "bug.py", ("def square", "n * n"), ("n * 2",)),

    _edit("tests-01", "test_generation",
          "Create unittest tests in test_target.py for add(2, 3) == 5 and add(-1, 1) == 0.",
          "test_target.py", ("import unittest", "def test_", "assertEqual", "2, 3", "-1, 1")),
    _edit("tests-02", "test_generation",
          "Create unittest tests in test_target.py for is_even(4) true and is_even(5) false.",
          "test_target.py", ("import unittest", "def test_", "assertTrue", "assertFalse")),
    _edit("tests-03", "test_generation",
          "Create unittest tests in test_target.py for clamp(9, 0, 5) == 5 and clamp(-2, 0, 5) == 0.",
          "test_target.py", ("import unittest", "def test_", "assertEqual", "9, 0, 5", "-2, 0, 5")),
    _edit("tests-04", "test_generation",
          "Create unittest tests in test_target.py for safe_divide(8, 2) == 4 and safe_divide(8, 0) is None.",
          "test_target.py", ("import unittest", "def test_", "assertEqual", "assertIsNone")),
    _edit("tests-05", "test_generation",
          "Create unittest tests in test_target.py for first_or_none([7, 8]) == 7 and first_or_none([]) is None.",
          "test_target.py", ("import unittest", "def test_", "assertEqual", "assertIsNone")),

    _items("security-01", "security_review",
           "Code passes raw user text to subprocess.run(..., shell=True) and joins an unchecked user path under a root. Identify applicable IDs in this order: shell-injection, path-traversal.",
           ("shell-injection", "path-traversal")),
    _items("security-02", "security_review",
           "Code fetches an arbitrary user URL from a server process and writes an access token to logs. Identify IDs in this order: ssrf, secret-logging.",
           ("ssrf", "secret-logging")),
    _items("security-03", "security_review",
           "Code builds SQL with an f-string containing request data and calls pickle.loads on request bytes. Identify IDs in this order: sql-injection, unsafe-deserialization.",
           ("sql-injection", "unsafe-deserialization")),
    _items("security-04", "security_review",
           "Code chmods a sensitive file to 0777 and uses a predictable shared temp filename. Identify IDs in this order: overbroad-permissions, insecure-temp-file.",
           ("overbroad-permissions", "insecure-temp-file")),
    _items("security-05", "security_review",
           "A network client has no timeout and follows redirects to any host. Identify IDs in this order: missing-timeout, untrusted-redirect.",
           ("missing-timeout", "untrusted-redirect")),

    _items("docs-01", "documentation",
           "A new CLI flag needs user-facing docs. Required sections, in order: purpose, usage, default, failure.",
           ("purpose", "usage", "default", "failure")),
    _items("docs-02", "documentation",
           "A new sandbox boundary needs documentation. Required sections, in order: threat-model, guarantees, non-goals, recovery.",
           ("threat-model", "guarantees", "non-goals", "recovery")),
    _items("docs-03", "documentation",
           "A release artifact guide is missing. Required sections, in order: prerequisites, install, verify, rollback.",
           ("prerequisites", "install", "verify", "rollback")),
    _items("docs-04", "documentation",
           "A local model manager guide is missing. Required sections, in order: download, verify, run, remove.",
           ("download", "verify", "run", "remove")),
    _items("docs-05", "documentation",
           "A crash recovery guide is missing. Required sections, in order: symptoms, preserve, recover, escalate.",
           ("symptoms", "preserve", "recover", "escalate")),

    _items("summary-01", "context_summarization",
           "Facts: port is 8080; retries are 3; timeout is 20 seconds. Return normalized facts in order.",
           ("port=8080", "retries=3", "timeout=20s")),
    _items("summary-02", "context_summarization",
           "Facts: backend is llama.cpp; context is 2048; threads are 6. Return normalized facts in order.",
           ("backend=llama.cpp", "context=2048", "threads=6")),
    _items("summary-03", "context_summarization",
           "Facts: status is blocked; phase is test_pending; repair budget is 2. Return normalized facts in order.",
           ("status=blocked", "phase=test_pending", "repairs=2")),
    _items("summary-04", "context_summarization",
           "Facts: model SHA begins abc123; runtime tag is b12199; RAM tier is 16-gib. Return normalized facts in order.",
           ("model=abc123", "runtime=b12199", "ram=16-gib")),
    _items("summary-05", "context_summarization",
           "Facts: tests passed 18; tests failed 2; elapsed time is 45 seconds. Return normalized facts in order.",
           ("passed=18", "failed=2", "elapsed=45s")),
)


def screening_tasks(role: str | None = None) -> tuple[ScreeningTask, ...]:
    if role is not None and role not in SCREENING_ROLES:
        raise ValueError("Unknown screening role")
    return tuple(task for task in SCREENING_TASKS
                 if role is None or task.role == role)


def score_screening_response(task: ScreeningTask, response: str) -> dict:
    """Score format and objective screening separately; no LLM judge."""
    if not isinstance(task, ScreeningTask):
        raise TypeError("ScreeningTask required")
    if not isinstance(response, str):
        return {"task_id": task.id, "role": task.role, "json_contract": False,
                "objective_pass": False, "reason": "no_response"}
    if task.kind == "items":
        try:
            payload = json.loads(response)
        except (TypeError, ValueError):
            return {"task_id": task.id, "role": task.role, "json_contract": False,
                    "objective_pass": False, "reason": "invalid_json"}
        contract = (isinstance(payload, dict) and set(payload) == {"items"}
                    and isinstance(payload["items"], list)
                    and all(isinstance(x, str) for x in payload["items"]))
        objective = contract and tuple(payload["items"]) == task.expected_items
        return {"task_id": task.id, "role": task.role,
                "json_contract": contract, "objective_pass": objective,
                "reason": "passed" if objective else
                          "wrong_items" if contract else "invalid_schema"}
    if task.kind != "edit":
        raise ValueError("Unknown screening task kind")
    try:
        with tempfile.TemporaryDirectory() as temp:
            edits = parse_edits(Path(temp), response)
    except (EditError, OSError, ValueError):
        return {"task_id": task.id, "role": task.role, "json_contract": False,
                "objective_pass": False, "reason": "invalid_edit_manifest"}
    if len(edits) != 1:
        return {"task_id": task.id, "role": task.role, "json_contract": True,
                "objective_pass": False, "reason": "wrong_edit_count"}
    path, content = edits[0]
    objective = (
        path.name == task.path
        and all(marker in content for marker in task.required_markers)
        and all(marker not in content for marker in task.forbidden_markers)
    )
    return {"task_id": task.id, "role": task.role, "json_contract": True,
            "objective_pass": objective,
            "reason": "passed" if objective else "rubric_miss"}


def screening_summary(results: list[dict]) -> dict:
    """Apply the fixed 4/5 JSON and 3/5 objective screening gate per role."""
    if not isinstance(results, list):
        raise TypeError("Screening result list required")
    known = {task.id: task for task in SCREENING_TASKS}
    seen = set()
    by_role = {role: [] for role in SCREENING_ROLES}
    for item in results:
        if not isinstance(item, dict) or item.get("task_id") not in known:
            raise ValueError("Unknown screening result")
        task_id = item["task_id"]
        if task_id in seen:
            raise ValueError("Duplicate screening result")
        seen.add(task_id)
        role = known[task_id].role
        if item.get("role") != role:
            raise ValueError("Screening role mismatch")
        by_role[role].append(item)
    roles = {}
    for role, items in by_role.items():
        json_ok = sum(item.get("json_contract") is True for item in items)
        objective_ok = sum(item.get("objective_pass") is True for item in items)
        complete = len(items) == 5
        roles[role] = {
            "tasks": len(items),
            "json_successes": json_ok,
            "objective_successes": objective_ok,
            "json_rate": round(json_ok / len(items), 3) if items else 0.0,
            "objective_rate": round(objective_ok / len(items), 3) if items else 0.0,
            "screening_pass": complete and json_ok >= 4 and objective_ok >= 3,
        }
    return {
        "complete": len(seen) == len(SCREENING_TASKS),
        "roles": roles,
        "eligible_roles": [
            role for role in SCREENING_ROLES if roles[role]["screening_pass"]],
        "gate": {
            "json_minimum": "4/5 per role",
            "objective_minimum": "3/5 per role",
            "qualification": "screening only; final thresholds remain 95% JSON / 80% objective",
        },
    }


def ram_tier(total_bytes: int | None) -> str:
    """Classify physical RAM using the fixed #55 matrix."""
    if total_bytes is None:
        return "unknown"
    if type(total_bytes) is not int or total_bytes <= 0:
        raise ValueError("Invalid total RAM")
    if total_bytes < 6 * GIB:
        return "4-gib-gui-investigation"
    if total_bytes < 12 * GIB:
        return "8-gib"
    if total_bytes < 24 * GIB:
        return "16-gib"
    return "32-gib"


def _quantization(filename: str) -> str | None:
    match = QUANTIZATION.search(filename)
    return match.group(1).upper() if match else None


def capture_baseline(controller) -> dict:
    """Capture one secret-free identity/resource/structured-output snapshot."""
    identity = controller.benchmark_identity()
    model = identity["model"]
    snapshot = system_memory()
    self_test = controller.inference_self_test()
    return {
        "schema": SCHEMA_VERSION,
        "kind": "managed-model-baseline",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "qualification_status": "baseline_only_not_qualified",
        "model": {
            **model,
            "quantization_from_filename": _quantization(model["file"]),
            "quantization_verified": False,
            "license_id": None,
            "license_status": "not_recorded_in_download_manifest",
        },
        "runtime": identity["runtime"],
        "settings": identity["settings"],
        "hardware": {
            "ram_tier": ram_tier(snapshot.total_bytes if snapshot else None),
            "total_ram_bytes": snapshot.total_bytes if snapshot else None,
            "available_ram_bytes_at_capture":
                snapshot.available_bytes if snapshot else None,
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
        },
        "measurement": {
            "startup_elapsed_seconds": identity["startup_elapsed_seconds"],
            "self_test_elapsed_seconds": self_test["elapsed_seconds"],
            "self_test_response_bytes": self_test["response_bytes"],
            "self_test_status": self_test["status"],
            "managed_memory": self_test.get("memory"),
        },
        "limitations": [
            "Baseline evidence is not model qualification.",
            "License and quantization require pinned metadata verification.",
            "No GPU/shared-memory or tokens-per-second measurement is implied.",
        ],
    }


def write_baseline(controller, directory: str | Path | None = None) -> dict:
    """Persist a unique bounded JSON record without overwriting prior evidence."""
    record = capture_baseline(controller)
    root = (Path(directory).expanduser() if directory is not None
            else Path.home() / ".forgeos" / "benchmarks")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise OSError("Benchmark evidence directory is unsafe")
    if os.name != "nt":
        root.chmod(0o700)
    token = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / (
        f"baseline-{stamp}-{record['model']['sha256'][:12]}-{token[:8]}.json")
    temporary = root / ("." + target.name + "." + token + ".tmp")
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if len(payload.encode("utf-8")) > 64 * 1024:
        raise RuntimeError("Benchmark baseline record exceeds bounded size")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if target.exists():
            raise FileExistsError("Benchmark evidence target unexpectedly exists")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"path": str(target), "record": record}
