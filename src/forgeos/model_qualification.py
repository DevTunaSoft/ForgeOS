"""Fail-closed model qualification and role routing for ForgeOS issue #55."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

FINAL_JSON_RATE = 0.95
FINAL_OBJECTIVE_RATE = 0.80
QUALIFICATION_SCHEMA = 1


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
    total = record.get("tasks")
    json_ok = record.get("json_successes")
    objective_ok = record.get("objective_successes")
    if (type(total) is not int or total < 20 or
            type(json_ok) is not int or not 0 <= json_ok <= total or
            type(objective_ok) is not int or not 0 <= objective_ok <= total):
        raise ValueError("Invalid qualification counts")
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
    passed = json_rate >= FINAL_JSON_RATE and objective_rate >= FINAL_OBJECTIVE_RATE
    normalized = {
        "schema": QUALIFICATION_SCHEMA,
        "kind": "role-qualification",
        "model_sha256": model_sha,
        "runtime_sha256": runtime_sha,
        "role": role,
        "tasks": total,
        "json_successes": json_ok,
        "objective_successes": objective_ok,
        "json_rate": round(json_rate, 6),
        "objective_rate": round(objective_rate, 6),
        "model_bytes": model_bytes,
        "peak_memory_bytes": peak,
        "median_latency_seconds": float(latency),
        "qualified": passed,
    }
    normalized["evidence_sha256"] = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return normalized


def route_qualified_role(role: str, records: list[dict], *,
                         memory_budget_bytes: int) -> dict:
    """Choose the smallest measured-qualified model that fits memory."""
    if not isinstance(role, str) or not role:
        raise ValueError("Role required")
    if type(memory_budget_bytes) is not int or memory_budget_bytes <= 0:
        raise ValueError("Positive memory budget required")
    if not isinstance(records, list):
        raise TypeError("Qualification records required")
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
        "model_sha256": chosen["model_sha256"],
        "runtime_sha256": chosen["runtime_sha256"],
        "evidence_sha256": chosen["evidence_sha256"],
        "model_bytes": chosen["model_bytes"],
        "peak_memory_bytes": chosen["peak_memory_bytes"],
        "median_latency_seconds": chosen["median_latency_seconds"],
        "selection_policy": "smallest-measured-qualified-model",
        "rejected_invalid_records": len(rejected),
    }
