"""Read-only diagnostic comparison view model for the existing ForgeOS GUI."""
from __future__ import annotations

import json
from typing import Any

MAX_RESULT_JSON = 2_000_000
MAX_PREVIEW = 240_000


def _preview(text: str, limit: int = MAX_PREVIEW) -> str:
    if not isinstance(text, str):
        return ""
    if len(text) <= limit:
        return text
    before = limit * 3 // 4
    after = limit - before
    return (text[:before] + f"\n[... {len(text) - limit} original characters"
            " omitted from GUI preview; retrieve full archive via CLI ...]\n"
            + text[-after:])


def get_comparison(record: dict, *, evidence_store: Any | None = None,
                   preview_limit: int = MAX_PREVIEW) -> dict:
    """Build compact/raw comparison; caller decides whether to read raw evidence."""
    if type(preview_limit) is not int or preview_limit < 2000:
        raise ValueError("Preview limit must be >= 2000")
    if not isinstance(record, dict):
        raise ValueError("Expected task record")
    payload = record.get("result")
    if isinstance(payload, str):
        if len(payload) > MAX_RESULT_JSON:
            raise ValueError("Task result exceeds GUI parsing safety limit")
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = None
    if not isinstance(payload, dict):
        raise ValueError("Selected task has no structured diagnostic evidence")
    result = payload.get("test", payload)
    if not isinstance(result, dict) or not isinstance(result.get("output"), str):
        raise ValueError("No recorded test output is available for this task")
    optimized = result.get("optimization")
    optimized = optimized if isinstance(optimized, dict) else {}
    reference = optimized.get("reference", "")
    valid_reference = (isinstance(reference, str) and len(reference) == 64
                       and all(char in "0123456789abcdef" for char in reference))
    compact = result["output"]
    full = "Original evidence has not been requested."
    if evidence_store is not None:
        if not valid_reference:
            full = "No recoverable original diagnostic was archived for this task."
        else:
            full = (evidence_store.preview(reference, max_chars=preview_limit)
                    if hasattr(evidence_store, "preview")
                    else evidence_store.get(reference))
    return {
        "task_id": record.get("id"),
        "status": record.get("status", "unknown"),
        "model": payload.get("model", "unspecified"),
        "compact": _preview(compact, preview_limit),
        "raw": _preview(full, preview_limit),
        "raw_available": valid_reference,
        "reference": reference if valid_reference else "",
        "failure_type": result.get("failure_type") if isinstance(result.get("failure_type"), dict) else {},
        "metrics": optimized,
    }
