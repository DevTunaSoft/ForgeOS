"""Conservative orchestration of interrupted resumable checkpoints.

Only explicit host-test authorization can execute repository code. Recovery
replays the execution contract persisted with each checkpoint.
"""
from __future__ import annotations

import json
from pathlib import Path

from .core import MAX_CHECKPOINT_BYTES, Ollama
from .engine import execute_task

PRESETS = {"python-unittest", "python-pytest", "npm-test", "cargo-test",
           "go-test", "dotnet-test"}


def _checkpoint_contract(checkpoint):
    contract = checkpoint.get("contract")
    if not isinstance(contract, dict) or set(contract) != {"preset", "max_repairs", "model"}:
        raise ValueError("Missing or invalid checkpoint execution contract")
    preset = contract["preset"]
    repairs = contract["max_repairs"]
    model = contract["model"]
    if preset not in PRESETS:
        raise ValueError("Invalid checkpoint test preset")
    if (not isinstance(repairs, int) or isinstance(repairs, bool)
            or not 0 <= repairs <= 5):
        raise ValueError("Invalid checkpoint repair budget")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ValueError("Invalid checkpoint model identifier")
    return {"preset": preset, "max_repairs": repairs, "model": model}


def resume_safe(store, workspace_parent: Path, *, preset=None, max_repairs=None,
                allow_host_tests=False, execute=execute_task,
                model_factory=Ollama):
    if not allow_host_tests:
        raise ValueError("Recovery cannot execute tests without explicit host-test permission")
    if preset is not None and preset not in PRESETS:
        raise ValueError("Invalid test preset override")
    if max_repairs is not None and (
            not isinstance(max_repairs, int) or isinstance(max_repairs, bool)
            or not 0 <= max_repairs <= 5):
        raise ValueError("Invalid repair budget override")
    parent = Path(workspace_parent).expanduser().resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("Workspace parent must be a directory")
    interrupted = store.recover()
    report = {"interrupted": interrupted, "resumed": [], "skipped": [], "failed": []}
    for task in store.iter_blocked_checkpoints():
        if task["status"] != "blocked":
            continue
        if task["checkpoint_bytes"] > MAX_CHECKPOINT_BYTES:
            report["skipped"].append({
                "id": task["id"],
                "reason": f"Checkpoint exceeds {MAX_CHECKPOINT_BYTES}-byte safety limit",
            })
            continue
        try:
            checkpoint = json.loads(task["result"])
        except (ValueError, TypeError):
            report["skipped"].append({"id": task["id"], "reason": "No structured checkpoint"})
            continue
        if (not isinstance(checkpoint, dict)
                or checkpoint.get("phase") not in {"test_pending", "repair_pending"}):
            report["skipped"].append({"id": task["id"], "reason": "No resumable checkpoint"})
            continue
        if (not isinstance(checkpoint.get("workspace"), str) or
                Path(checkpoint["workspace"]).resolve() !=
                (parent / f"task-{task['id']}").resolve()):
            report["skipped"].append({"id": task["id"], "reason": "Workspace location mismatch"})
            continue
        try:
            contract = _checkpoint_contract(checkpoint)
        except ValueError as exc:
            report["skipped"].append({"id": task["id"], "reason": str(exc)})
            continue
        if preset is not None and preset != contract["preset"]:
            report["skipped"].append({"id": task["id"], "reason": "Test preset override mismatch"})
            continue
        if max_repairs is not None and max_repairs != contract["max_repairs"]:
            report["skipped"].append({"id": task["id"], "reason": "Repair budget override mismatch"})
            continue
        try:
            model = model_factory(model=contract["model"])
            result = execute(
                store, task["id"], parent, preset=contract["preset"],
                model=model, max_repairs=contract["max_repairs"],
                allow_host_tests=True, resume=True,
            )
            if result["passed"]:
                report["resumed"].append(task["id"])
            else:
                report["failed"].append({"id": task["id"], "reason": "Tests failed"})
        except Exception as exc:
            report["failed"].append({"id": task["id"], "reason": str(exc)})
    return report
