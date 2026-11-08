"""Fail-closed model qualification and role routing for ForgeOS issue #55."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

FINAL_JSON_RATE = 0.95
FINAL_OBJECTIVE_RATE = 0.80
QUALIFICATION_SCHEMA = 1
MAX_QUALIFICATION_RECORD_BYTES = 64 * 1024
MAX_QUALIFICATION_RECORDS = 128
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}")
SUPPORTED_QUALIFIED_BACKENDS = frozenset({"llama.cpp"})


@dataclass(frozen=True)
class QualifiedCandidate:
    model_sha256: str
    runtime_sha256: str
    role: str
    model_bytes: int
    peak_memory_bytes: int
    median_latency_seconds: float


def _sha(value, field):
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"Invalid {field}")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}") from exc
    return value.lower()


def qualify_role_evidence(record: dict) -> dict:
    """Validate final role evidence. Screening records can never qualify."""
    if not isinstance(record, dict):
        raise TypeError("Qualification record required")
    if record.get("schema") != QUALIFICATION_SCHEMA:
        raise ValueError("Unsupported qualification schema")
    if record.get("kind") != "role-qualification":
        raise ValueError("Final role qualification evidence required")
    model_sha = _sha(record.get("model_sha256"), "model SHA-256")
    runtime_sha = _sha(record.get("runtime_sha256"), "runtime SHA-256")
    role = record.get("role")
    if not isinstance(role, str) or not role:
        raise ValueError("Qualification role required")
    backend = record.get("backend")
    model_name = record.get("model_name")
    if backend not in SUPPORTED_QUALIFIED_BACKENDS:
        raise ValueError("Backend lacks ForgeOS runtime identity attestation")
    if not isinstance(model_name, str) or not MODEL_NAME.fullmatch(model_name):
        raise ValueError("Invalid qualified model identifier")
    total = record.get("tasks")
    json_ok = record.get("json_successes")
    objective_ok = record.get("objective_successes")
    if (type(total) is not int or total < 20 or
            type(json_ok) is not int or not 0 <= json_ok <= total or
            type(objective_ok) is not int or not 0 <= objective_ok <= total):
        raise ValueError("Invalid qualification counts")
    runtime_failures = record.get("runtime_failures")
    safety_violations = record.get("safety_violations")
    if (type(runtime_failures) is not int or
            not 0 <= runtime_failures <= total):
        raise ValueError("Invalid runtime failure count")
    if type(safety_violations) is not int or safety_violations < 0:
        raise ValueError("Invalid safety violation count")
    model_bytes = record.get("model_bytes")
    peak = record.get("peak_memory_bytes")
    latency = record.get("median_latency_seconds")
    if type(model_bytes) is not int or model_bytes <= 0:
        raise ValueError("Invalid model size")
    if type(peak) is not int or peak <= 0:
        raise ValueError("Invalid peak memory")
    if type(latency) not in (int, float) or isinstance(latency, bool) or latency < 0:
        raise ValueError("Invalid latency")
    json_rate = json_ok / total
    objective_rate = objective_ok / total
    runtime_failure_rate = runtime_failures / total
    passed = (
        json_rate >= FINAL_JSON_RATE
        and objective_rate >= FINAL_OBJECTIVE_RATE
        and runtime_failure_rate <= 0.05
        and safety_violations == 0
    )
    normalized = {
        "schema": QUALIFICATION_SCHEMA,
        "kind": "role-qualification",
        "model_sha256": model_sha,
        "runtime_sha256": runtime_sha,
        "role": role,
        "backend": backend,
        "model_name": model_name,
        "tasks": total,
        "json_successes": json_ok,
        "objective_successes": objective_ok,
        "json_rate": round(json_rate, 6),
        "objective_rate": round(objective_rate, 6),
        "runtime_failures": runtime_failures,
        "runtime_failure_rate": round(runtime_failure_rate, 6),
        "safety_violations": safety_violations,
        "model_bytes": model_bytes,
        "peak_memory_bytes": peak,
        "median_latency_seconds": float(latency),
        "qualified": passed,
    }
    normalized["evidence_sha256"] = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return normalized


def load_qualification_records(directory: str | Path | None = None) -> list[dict]:
    """Load only bounded local final-qualification JSON evidence."""
    root = (Path(directory).expanduser() if directory is not None
            else Path.home() / ".forgeos" / "benchmarks")
    if root.is_symlink():
        raise OSError("Qualification evidence directory is unsafe")
    if not root.exists():
        return []
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise OSError("Qualification evidence directory is unsafe")
    records = []
    for path in sorted(root.glob("qualification-*.json")):
        if len(records) >= MAX_QUALIFICATION_RECORDS:
            break
        try:
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            if stat.st_nlink != 1 or stat.st_size > MAX_QUALIFICATION_RECORD_BYTES:
                continue
            raw = path.read_bytes()
            if len(raw) > MAX_QUALIFICATION_RECORD_BYTES:
                continue
            value = json.loads(raw.decode("utf-8"))
            if isinstance(value, dict):
                records.append(value)
        except (OSError, UnicodeError, ValueError):
            continue
    return records


def route_qualified_role(role: str, records: list[dict], *,
                         memory_budget_bytes: int,
                         backend: str | None = None,
                         model_sha256: str | None = None,
                         runtime_sha256: str | None = None,
                         evidence_sha256: str | None = None) -> dict:
    """Choose the smallest measured-qualified model that fits memory."""
    if not isinstance(role, str) or not role:
        raise ValueError("Role required")
    if type(memory_budget_bytes) is not int or memory_budget_bytes <= 0:
        raise ValueError("Positive memory budget required")
    if not isinstance(records, list):
        raise TypeError("Qualification records required")
    required_evidence = (
        _sha(evidence_sha256, "required evidence SHA-256")
        if evidence_sha256 is not None else None)
    candidates = []
    rejected = []
    for raw in records:
        try:
            item = qualify_role_evidence(raw)
        except (TypeError, ValueError) as exc:
            rejected.append(str(exc))
            continue
        if item["role"] != role or not item["qualified"]:
            continue
        if backend is not None and item["backend"] != backend:
            continue
        if model_sha256 is not None and item["model_sha256"] != _sha(
                model_sha256, "required model SHA-256"):
            continue
        if runtime_sha256 is not None and item["runtime_sha256"] != _sha(
                runtime_sha256, "required runtime SHA-256"):
            continue
        if required_evidence is not None and item["evidence_sha256"] != required_evidence:
            continue
        if item["peak_memory_bytes"] > memory_budget_bytes:
            continue
        candidates.append(item)
    if not candidates:
        raise RuntimeError("No measured-qualified model fits this role and memory budget")
    candidates.sort(key=lambda item: (
        item["model_bytes"], item["peak_memory_bytes"],
        item["median_latency_seconds"], item["model_sha256"]))
    chosen = candidates[0]
    return {
        "role": role,
        "backend": chosen["backend"],
        "model_name": chosen["model_name"],
        "model_sha256": chosen["model_sha256"],
        "runtime_sha256": chosen["runtime_sha256"],
        "evidence_sha256": chosen["evidence_sha256"],
        "model_bytes": chosen["model_bytes"],
        "peak_memory_bytes": chosen["peak_memory_bytes"],
        "median_latency_seconds": chosen["median_latency_seconds"],
        "selection_policy": "smallest-measured-qualified-model",
        "rejected_invalid_records": len(rejected),
    }


def resolve_qualified_managed_model(model, *, role: str,
                                    directory: str | Path | None = None,
                                    evidence_sha256: str | None = None) -> dict:
    """Resolve qualified-auto against an attested managed llama.cpp runtime.

    This is the single qualification entry point used by planning and the
    autonomous edit/test/repair engine. It never falls back to an unqualified
    or merely installed model.
    """
    if getattr(model, "model", None) != "qualified-auto":
        raise ValueError("Qualified model resolver requires qualified-auto")
    if not isinstance(role, str) or not role.strip() or len(role) > 80:
        raise ValueError("Qualified model role is required")
    backend = getattr(model, "backend", None)
    model_sha = getattr(model, "managed_model_sha256", None)
    runtime_sha = getattr(model, "managed_runtime_sha256", None)
    if backend != "llama.cpp":
        raise ValueError(
            "qualified-auto currently requires ForgeOS-managed llama.cpp")
    model_sha = _sha(model_sha, "managed model SHA-256")
    runtime_sha = _sha(runtime_sha, "managed runtime SHA-256")
    from .model_routing import (
        managed_runtime_memory_ceiling, system_memory,
    )
    memory_budget = managed_runtime_memory_ceiling(system_memory())
    if not isinstance(memory_budget, int) or memory_budget <= 0:
        raise RuntimeError(
            "No safe memory budget is available for qualified routing")
    selection = route_qualified_role(
        role, load_qualification_records(directory),
        memory_budget_bytes=memory_budget, backend="llama.cpp",
        model_sha256=model_sha, runtime_sha256=runtime_sha,
        evidence_sha256=evidence_sha256)
    if selection["model_name"] != "forgeos-managed":
        raise RuntimeError(
            "Qualified evidence does not identify the active "
            "ForgeOS-managed model")
    model.model = selection["model_name"]
    return selection
