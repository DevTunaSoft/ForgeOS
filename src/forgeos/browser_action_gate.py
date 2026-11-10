"""Durable one-use browser scroll approvals using ForgeOS's existing task SQLite DB.

This is a fail-closed foundation for a trusted *future GUI approval event*.
It is not an authentication boundary: never let an LLM call approve_scroll().
No background worker, separate database, browser dependency, or auto-retry.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from uuid import uuid4

from .browser_observation import Observation, allowed_origin
from .browsentic_readonly import _check_scroll
from .core import DEFAULT_LEASE_SECONDS, Store

_SCROLL_SCHEMA = """CREATE TABLE IF NOT EXISTS browser_scroll_intents (
    task_id INTEGER NOT NULL,
    action_key TEXT NOT NULL,
    intent_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('proposed', 'approved', 'attempted', 'succeeded', 'uncertain')
    ),
    created REAL NOT NULL,
    updated REAL NOT NULL,
    expires REAL NOT NULL,
    PRIMARY KEY (task_id, action_key)
)"""
_ACTION_KEY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
_ACTIVE_TASK_STATES = ("queued", "planned")


def _validated(task_id: int, action_key: str, origin: str,
               direction: str) -> str:
    if type(task_id) is not int or task_id < 1:
        raise ValueError("A real task ID is required")
    if not isinstance(action_key, str) or not _ACTION_KEY.fullmatch(action_key):
        raise ValueError("Action key must be 1..64 safe ASCII characters")
    if not isinstance(origin, str) or allowed_origin(origin) != origin:
        raise ValueError("An exact public HTTPS origin is required")
    _check_scroll(direction, True)
    spec = json.dumps({"origin": origin, "direction": direction},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(spec.encode("utf-8")).hexdigest()


def _require_task(store: Store, task_id: int) -> None:
    task = store.get(task_id)
    if task["status"] not in _ACTIVE_TASK_STATES:
        raise ValueError("Browser actions are not permitted for this task state")


def _init(store: Store) -> None:
    # Reuse the authoritative ForgeOS SQLite connection and WAL.
    store.db.execute(_SCROLL_SCHEMA)


def propose_scroll(store: Store, task_id: int, action_key: str,
                   origin: str, direction: str, *,
                   ttl_seconds: int = 180) -> None:
    """Prepare an immutable proposal; never overwrite any previous attempt."""
    intent_hash = _validated(task_id, action_key, origin, direction)
    if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 3600:
        raise ValueError("Approval lifetime must be 30..3600 seconds")
    _require_task(store, task_id)
    now = time.time()
    with store.db:
        _init(store)
        # A repeated key is an error even for an expired or finished action.
        store.db.execute(
            "INSERT INTO browser_scroll_intents "
            "(task_id,action_key,intent_hash,status,created,updated,expires) "
            "VALUES(?,?,?,'proposed',?,?,?)",
            (task_id, action_key, intent_hash, now, now, now + ttl_seconds),
        )


def approve_scroll(store: Store, task_id: int, action_key: str,
                   origin: str, direction: str, *,
                   human_confirmed: bool = False) -> None:
    """An explicit, trusted operator/UI event must invoke this; never an AI."""
    digest = _validated(task_id, action_key, origin, direction)
    if human_confirmed is not True:
        raise ValueError("Approval requires an explicit human confirmation")
    _require_task(store, task_id)
    now = time.time()
    with store.db:
        _init(store)
        result = store.db.execute(
            "UPDATE browser_scroll_intents SET status='approved',updated=? "
            "WHERE task_id=? AND action_key=? AND intent_hash=? "
            "AND status='proposed' AND expires>?",
            (now, task_id, action_key, digest, now),
        )
        if result.rowcount != 1:
            raise ValueError("Scroll proposal missing, expired or already decided")


def scroll_status(store: Store, task_id: int, action_key: str) -> str | None:
    """Return only the state; do not return origin, page text or token hashes."""
    if type(task_id) is not int or task_id <= 0:
        raise ValueError("Invalid task ID")
    if not isinstance(action_key, str) or not _ACTION_KEY.fullmatch(action_key):
        raise ValueError("Invalid action key")
    row = store.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='browser_scroll_intents'"
    ).fetchone()
    if row is None:
        return None
    row = store.db.execute(
        "SELECT status FROM browser_scroll_intents "
        "WHERE task_id=? AND action_key=?", (task_id, action_key),
    ).fetchone()
    return str(row[0]) if row else None


def run_approved_scroll(store: Store, task_id: int, action_key: str,
                        origin: str, direction: str, *,
                        observer=None) -> Observation:
    """Consume an approved one-shot intent BEFORE issuing the MCP action.

    A crash after the consume commit leaves 'attempted' (then 'uncertain' on
    store.recover); neither state can execute again automatically. Approval,
    execution and checkpointing are all on the same ForgeOS task DB.
    """
    digest = _validated(task_id, action_key, origin, direction)
    _require_task(store, task_id)
    owner = uuid4().hex
    if not store.acquire_lease(task_id, owner):
        raise ValueError("Task already has an active execution lease")
    started = False
    try:
        now = time.time()
        with store.db:
            changed = store.db.execute(
                "UPDATE browser_scroll_intents SET status='attempted',updated=? "
                "WHERE task_id=? AND action_key=? AND intent_hash=? "
                "AND status='approved' AND expires>? "
                "AND EXISTS (SELECT 1 FROM task_leases l WHERE l.task_id=? "
                "AND l.owner=? AND l.heartbeat>=? AND l.heartbeat<=?) "
                "AND EXISTS (SELECT 1 FROM tasks t WHERE t.id=? "
                "AND t.status IN ('queued','planned'))",
                (now, task_id, action_key, digest, now, task_id, owner,
                 now - DEFAULT_LEASE_SECONDS, now + 60, task_id),
            )
            if changed.rowcount != 1:
                raise ValueError("Approval is missing, expired or already consumed")
        started = True  # Durable before any external side effect.
        if observer is None:
            from .browsentic_readonly import observe_paired_browser
            observer = observe_paired_browser
        if not callable(observer):
            raise ValueError("Browser observer must be callable")
        result = observer(permitted_origin=origin, scroll_direction=direction,
                          approve_scroll=True, changes_only=False)
        if not isinstance(result, Observation) or len(result.text) > 2400:
            raise RuntimeError("Browser observation could not be verified")
        if not store.heartbeat_lease(task_id, owner):
            raise RuntimeError("Task lease expired during browser operation")
        now = time.time()
        with store.db:
            changed = store.db.execute(
                "UPDATE browser_scroll_intents SET status='succeeded',updated=? "
                "WHERE task_id=? AND action_key=? AND status='attempted' "
                "AND EXISTS (SELECT 1 FROM task_leases l WHERE "
                "l.task_id=? AND l.owner=? AND l.heartbeat>=?)",
                (now, task_id, action_key, task_id, owner,
                 now - DEFAULT_LEASE_SECONDS),
            )
            if changed.rowcount != 1:
                raise RuntimeError("Browser result checkpoint could not be published")
        return result
    except Exception:
        # Error details may include secret URLs. Neither persist nor rethrow
        # raw browser exceptions after a consequential operation.
        if started:
            with store.db:
                store.db.execute(
                    "UPDATE browser_scroll_intents SET status='uncertain',updated=? "
                    "WHERE task_id=? AND action_key=? AND status='attempted'",
                    (time.time(), task_id, action_key),
                )
            raise RuntimeError("Browser action outcome uncertain; inspect before "
                               "proposing a different action") from None
        raise
    finally:
        store.release_lease(task_id, owner)
