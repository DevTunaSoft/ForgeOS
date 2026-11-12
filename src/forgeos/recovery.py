"""Conservative orchestration of interrupted resumable checkpoints.

Only explicit host-test authorization can execute repository code. Recovery
replays the execution contract persisted with each checkpoint.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .core import MAX_CHECKPOINT_BYTES, Ollama
from .local_models import DEFAULT_ENDPOINTS, create_model, validate_endpoint
from .engine import execute_task

PRESETS = {"python-unittest", "python-pytest", "npm-test", "cargo-test",
           "go-test", "dotnet-test"}
MAX_RECOVERY_REASON_CHARS = 400


def _checkpoint_contract(checkpoint):
    contract = checkpoint.get("contract")
    legacy = {"preset", "max_repairs", "model"}
    extended = legacy | {"backend", "endpoint"}
    managed_extended = extended | {"managed_model_sha256", "managed_runtime_sha256"}
    qualified_managed = managed_extended | {
        "qualification_role", "qualification_evidence_sha256",
        "qualification_directory"}
    if (not isinstance(contract, dict)
            or set(contract) not in (legacy, extended, managed_extended,
                                     qualified_managed)):
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
    result = {"preset": preset, "max_repairs": repairs, "model": model}
    if set(contract) in (extended, managed_extended, qualified_managed):
        backend = contract["backend"]
        endpoint = contract["endpoint"]
        if not isinstance(backend, str) or backend not in DEFAULT_ENDPOINTS:
            raise ValueError("Invalid checkpoint local inference backend")
        if not isinstance(endpoint, str) or validate_endpoint(endpoint) != endpoint:
            raise ValueError("Invalid checkpoint local inference endpoint")
        if set(contract) in (managed_extended, qualified_managed):
            hashes = (contract["managed_model_sha256"],
                      contract["managed_runtime_sha256"])
            if (backend != "llama.cpp" or model != "forgeos-managed"
                    or any(not isinstance(v, str) or
                           not re.fullmatch(r"[a-f0-9]{64}", v)
                           for v in hashes)):
                raise ValueError("Invalid managed GGUF checkpoint identity")
            result.update(
                managed_model_sha256=hashes[0],
                managed_runtime_sha256=hashes[1])
            if set(contract) == qualified_managed:
                role = contract["qualification_role"]
                evidence = contract["qualification_evidence_sha256"]
                directory = contract["qualification_directory"]
                if (not isinstance(role, str) or not role or len(role) > 80
                        or not isinstance(evidence, str)
                        or not re.fullmatch(r"[a-f0-9]{64}", evidence)
                        or not isinstance(directory, str) or len(directory) > 4096):
                    raise ValueError("Invalid qualified-model checkpoint evidence")
                if directory:
                    try:
                        root = Path(directory)
                        resolved = root.resolve(strict=True)
                    except OSError as exc:
                        raise ValueError(
                            "Qualification evidence directory is unavailable") from exc
                    if (not root.is_absolute() or root.is_symlink()
                            or not resolved.is_dir() or str(resolved) != directory):
                        raise ValueError("Invalid qualification evidence directory")
                result.update(
                    qualification_role=role,
                    qualification_evidence_sha256=evidence,
                    qualification_directory=directory)
        elif backend == "llama.cpp" and model == "forgeos-managed":
            raise ValueError(
                "Managed local GGUF checkpoint needs verified runtime and "
                "model identity; automatic recovery is not yet supported")
        # Reuse the exact originally persisted server identity. Never
        # substitute a port, backend, cloud endpoint, or different model.
        result.update(backend=backend, endpoint=endpoint)
    return result


def resume_safe(store, workspace_parent: Path, *, preset=None, max_repairs=None,
                allow_host_tests=False, execute=execute_task,
                model_factory=Ollama, managed_model_factory=None,
                on_event=None, collect_results=True):
    """Guarded resumption with optional constant-memory event reporting.

    The default report shape is unchanged. Streaming callers may disable
    outcome-list collection and receive one event per processed checkpoint.
    """
    if not allow_host_tests:
        raise ValueError("Recovery cannot execute tests without explicit host-test permission")
    if type(collect_results) is not bool:
        raise ValueError("collect_results must be boolean")
    if on_event is not None and not callable(on_event):
        raise ValueError("on_event must be callable")
    if managed_model_factory is not None and not callable(managed_model_factory):
        raise ValueError("managed_model_factory must be callable")
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
    counts = {"resumed": 0, "skipped": 0, "failed": 0}

    def record(kind, value):
        # Failed runners may include arbitrary model/test output in their
        # exception text. Bound both the collected report and event stream.
        if kind != "resumed":
            reason = value["reason"]
            if len(reason) > MAX_RECOVERY_REASON_CHARS:
                reason = reason[:150] + " [middle omitted] " + reason[-230:]
                value = {"id": value["id"], "reason": reason}
        counts[kind] += 1
        if collect_results:
            report[kind].append(value)
        if on_event is not None:
            event = {"event": kind, "id": value if kind == "resumed" else value["id"]}
            if kind != "resumed":
                event["reason"] = value["reason"]
            # Never swallow output sink failures as model/test failures.
            on_event(event)

    for task in store.iter_blocked_checkpoints():
        if task["status"] != "blocked":
            continue
        if task["checkpoint_bytes"] > MAX_CHECKPOINT_BYTES:
            record("skipped", {
                "id": task["id"],
                "reason": f"Checkpoint exceeds {MAX_CHECKPOINT_BYTES}-byte safety limit",
            })
            continue
        try:
            checkpoint = json.loads(task["result"])
        except (ValueError, TypeError):
            record("skipped", {"id": task["id"], "reason": "No structured checkpoint"})
            continue
        if isinstance(checkpoint, dict) and checkpoint.get("phase") == "manual_review_required":
            record("skipped", {
                "id": task["id"],
                "reason": "Incomplete test evidence requires manual review; automatic retry disabled",
            })
            continue
        if (not isinstance(checkpoint, dict)
                or checkpoint.get("phase") not in {"test_pending", "repair_pending"}):
            record("skipped", {"id": task["id"], "reason": "No resumable checkpoint"})
            continue
        if (not isinstance(checkpoint.get("workspace"), str) or
                Path(checkpoint["workspace"]).resolve() !=
                (parent / f"task-{task['id']}").resolve()):
            record("skipped", {"id": task["id"], "reason": "Workspace location mismatch"})
            continue
        try:
            contract = _checkpoint_contract(checkpoint)
        except ValueError as exc:
            record("skipped", {"id": task["id"], "reason": str(exc)})
            continue
        if preset is not None and preset != contract["preset"]:
            record("skipped", {"id": task["id"], "reason": "Test preset override mismatch"})
            continue
        if max_repairs is not None and max_repairs != contract["max_repairs"]:
            record("skipped", {"id": task["id"], "reason": "Repair budget override mismatch"})
            continue
        try:
            if "managed_model_sha256" in contract:
                if managed_model_factory is None:
                    record("skipped", {
                        "id": task["id"],
                        "reason": (
                            "Managed GGUF checkpoint requires an authenticated "
                            "currently running ForgeOS-managed runtime"),
                    })
                    continue
                model = managed_model_factory(contract)
                expected = {
                    "backend": "llama.cpp",
                    "model": "forgeos-managed",
                    "base": contract["endpoint"],
                    "managed_model_sha256": contract["managed_model_sha256"],
                    "managed_runtime_sha256": contract["managed_runtime_sha256"],
                }
                actual = {name: getattr(model, name, None)
                          for name in expected}
                if actual != expected:
                    raise ValueError(
                        "Authenticated managed runtime does not match checkpoint identity")
                if getattr(model, "authenticated", False) is not True:
                    raise ValueError(
                        "Managed checkpoint recovery requires authenticated runtime")
            elif "backend" in contract:
                model = create_model(
                    backend=contract["backend"],
                    model=contract["model"],
                    endpoint=contract["endpoint"],
                )
            else:
                # Existing Ollama checkpoints and injected test factories
                # preserve the same behavior as before this extension.
                model = model_factory(model=contract["model"])
            qualified_resume = {}
            if "qualification_evidence_sha256" in contract:
                model.model = "qualified-auto"
                qualified_resume = {
                    "role": contract["qualification_role"],
                    "qualification_evidence_sha256":
                        contract["qualification_evidence_sha256"],
                    "qualification_directory": (
                        Path(contract["qualification_directory"])
                        if contract["qualification_directory"] else None),
                }
            result = execute(
                store, task["id"], parent, preset=contract["preset"],
                model=model, max_repairs=contract["max_repairs"],
                allow_host_tests=True, resume=True, **qualified_resume,
            )
            if result["passed"]:
                kind, value = "resumed", task["id"]
            else:
                kind, value = "failed", {"id": task["id"], "reason": "Tests failed"}
        except Exception as exc:
            kind, value = "failed", {"id": task["id"], "reason": str(exc)}
        record(kind, value)

    if not collect_results:
        report["counts"] = counts
    return report
