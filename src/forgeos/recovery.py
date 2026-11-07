"""Conservative orchestration of interrupted test checkpoints.

Only explicit host-test authorization can execute repository code. Invalid or
unrecoverable tasks stay blocked and are reported individually.
"""
from __future__ import annotations

import json
from pathlib import Path

from .engine import execute_task


def resume_safe(store, workspace_parent: Path, *, preset="python-unittest",
                max_repairs=2, allow_host_tests=False, execute=execute_task):
    if not allow_host_tests:
        raise ValueError("Recovery cannot execute tests without explicit host-test permission")
    if not isinstance(max_repairs, int) or isinstance(max_repairs, bool) or not 0 <= max_repairs <= 5:
        raise ValueError("Invalid repair budget")
    parent = Path(workspace_parent).expanduser().resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("Workspace parent must be a directory")
    interrupted = store.recover()
    report = {"interrupted": interrupted, "resumed": [], "skipped": [], "failed": []}
    for task in store.list():
        if task["status"] != "blocked":
            continue
        try:
            checkpoint = json.loads(task["result"])
        except (ValueError, TypeError):
            report["skipped"].append({"id": task["id"], "reason": "No structured checkpoint"})
            continue
        if not isinstance(checkpoint, dict) or checkpoint.get("phase") not in {"test_pending", "repair_pending"}:
            report["skipped"].append({"id": task["id"], "reason": "No resumable checkpoint"})
            continue
        if (not isinstance(checkpoint.get("workspace"), str) or
                Path(checkpoint["workspace"]).resolve() != (parent / f"task-{task['id']}").resolve()):
            report["skipped"].append({"id": task["id"], "reason": "Workspace location mismatch"})
            continue
        try:
            result = execute(store, task["id"], parent, preset=preset,
                             max_repairs=max_repairs, allow_host_tests=True, resume=True)
            if result["passed"]:
                report["resumed"].append(task["id"])
            else:
                report["failed"].append({"id": task["id"], "reason": "Tests failed"})
        except Exception as exc:
            report["failed"].append({"id": task["id"], "reason": str(exc)})
    return report
