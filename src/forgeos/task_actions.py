"""Canonical, lease-fenced plan and manually requested test actions.

CLI and GUI share these operations. Generated project code is only executed
with explicit host-test permission through the engine's selected containment
runtime; packaged Windows tests must never silently fall back to host code.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from uuid import uuid4

from .core import LeaseLostError, Ollama, Store
from .engine import _run_workspace_tests

PLAN_STATUSES = frozenset({"queued", "planned"})
TEST_STATUSES = frozenset({"queued", "planned", "failed"})


@contextmanager
def _owned_task(store: Store, task_id: int):
    owner = uuid4().hex
    if not store.acquire_lease(task_id, owner):
        raise ValueError("Task already has an active execution lease")
    try:
        yield owner
    finally:
        store.release_lease(task_id, owner)


def _publish(store: Store, task_id: int, owner: str, expected, status, **changes):
    if not store.transition(task_id, expected, status,
                            lease_owner=owner, **changes):
        raise LeaseLostError("Task lease lost or status changed before publication")


def _heartbeat(store: Store, task_id: int, owner: str):
    if not store.heartbeat_lease(task_id, owner):
        raise LeaseLostError("Task execution lease was lost or expired")


def plan_task(store: Store, task_id: int, *, model: Ollama | None = None,
              model_factory=None) -> str:
    """Plan only eligible tasks; construct an AI model after lease acquisition."""
    if model is not None and model_factory is not None:
        raise ValueError("Specify a model or model factory, not both")
    if model_factory is not None and not callable(model_factory):
        raise ValueError("Model factory must be callable" )
    task = store.get(task_id)
    if task["status"] not in PLAN_STATUSES:
        raise ValueError("Task is not eligible for planning")
    with _owned_task(store, task_id) as owner:
        _publish(store, task_id, owner, PLAN_STATUSES, "planning", increment=True)
        try:
            chosen_model = (model if model is not None else
                            model_factory() if model_factory is not None else
                            Ollama())
            output = chosen_model.plan(task["description"])
            _heartbeat(store, task_id, owner)
            _publish(store, task_id, owner, {"planning"}, "planned",
                     result=output)
            return output
        except Exception as exc:
            # Stale workers and changed statuses fail closed.
            store.transition(task_id, {"planning"}, "blocked",
                             error=str(exc), result="", lease_owner=owner)
            raise


def test_task(store: Store, task_id: int, preset: str, *,
              allow_host_tests: bool = False) -> dict:
    """Run one manual test with the same containment as the task engine.

    Tests target the original repository, not a generated worktree. Do not
    execute untrusted project code without the explicit permission flag.
    """
    if not allow_host_tests:
        raise ValueError(
            "Test execution requires explicit --allow-host-tests permission")
    task = store.get(task_id)
    if task["status"] not in TEST_STATUSES:
        raise ValueError("Task is not eligible for a manual test run")
    with _owned_task(store, task_id) as owner:
        _publish(store, task_id, owner, TEST_STATUSES, "testing", increment=True)
        try:
            outcome = _run_workspace_tests(Path(task["project"]), preset)
            if not isinstance(outcome, dict) or type(outcome.get("passed")) is not bool:
                raise RuntimeError("Test runner returned an invalid outcome")
            if outcome.get("capture_complete") is False:
                raise RuntimeError(
                    "Test evidence was incomplete; automatic approval is blocked")
            _heartbeat(store, task_id, owner)
            result_text = json.dumps(outcome)
            _publish(
                store, task_id, owner, {"testing"},
                "passed" if outcome["passed"] else "failed",
                result=result_text,
                error="" if outcome["passed"] else str(outcome.get("output", "")),
            )
            return outcome
        except Exception as exc:
            store.transition(task_id, {"testing"}, "blocked",
                             error=str(exc), lease_owner=owner)
            raise
