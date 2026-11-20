"""Deterministic validation of microtask dependency graphs (roadmap P1 slice).

A planned microtask pipeline decomposes a large request into small tasks with
explicit prerequisites and expected files. Before any task runs, this module
checks the graph: schema version, identifiers, prerequisite existence, cycles,
portable relative paths and unordered edits to the same file. It is pure Python
with no I/O, model, database or GUI dependency, and it is not yet wired to the
task engine. Fixed bounds keep the memory cost of any graph small.
"""
from __future__ import annotations

import heapq
import re
from collections.abc import Mapping

from .portable_names import has_trailing_dot_or_space, is_device_name

SCHEMA_VERSION = 1
MAX_TASKS = 256
MAX_PREREQUISITES = 16
MAX_FILES = 8  # Same bound as the edit manifest in engine.MAX_FILES.
MAX_PATH_LENGTH = 240
_TASK_KEYS = frozenset({"id", "prerequisites", "files"})
_TASK_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class TaskGraphError(ValueError):
    """A microtask graph violates the schema or a safety rule."""


def _task_id(value: object) -> str:
    if not isinstance(value, str) or not _TASK_ID.fullmatch(value):
        raise TaskGraphError("Invalid task identifier")
    return value


def _relative_path(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > MAX_PATH_LENGTH
            or "\\" in value or ":" in value or "\x00" in value):
        raise TaskGraphError("Invalid file path")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise TaskGraphError("Path traversal or absolute path")
    if any(has_trailing_dot_or_space(part) or is_device_name(part) for part in parts):
        raise TaskGraphError("Non-portable file name")
    return value


def validate_task_graph(graph: object) -> list[str]:
    """Return task identifiers in deterministic dependency order.

    Raises TaskGraphError before anything is scheduled. Ties are broken by
    identifier, so an equal graph always produces the same order.
    """
    if not isinstance(graph, Mapping) or set(graph) != {"schema_version", "tasks"}:
        raise TaskGraphError("Expected only schema_version and tasks")
    version = graph["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise TaskGraphError("Unsupported task-graph schema version")
    tasks = graph["tasks"]
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS:
        raise TaskGraphError(f"Task count must be 1 to {MAX_TASKS}")

    ids: list[str] = []
    position: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, Mapping) or set(task) != _TASK_KEYS:
            raise TaskGraphError("Each task needs id, prerequisites and files only")
        task_id = _task_id(task["id"])
        if task_id in position:
            raise TaskGraphError("Duplicate task identifier")
        position[task_id] = len(ids)
        ids.append(task_id)

    prerequisites: list[list[int]] = []
    files: list[dict[str, str]] = []  # casefolded path -> path as written
    for task in tasks:
        raw_parents = task["prerequisites"]
        if not isinstance(raw_parents, list) or len(raw_parents) > MAX_PREREQUISITES:
            raise TaskGraphError("Too many or malformed prerequisites")
        parents = set()
        for name in raw_parents:
            parent = _task_id(name)
            if parent == task["id"]:
                raise TaskGraphError("A task cannot depend on itself")
            if parent not in position:
                raise TaskGraphError("Missing prerequisite")
            parents.add(position[parent])
        prerequisites.append(sorted(parents))

        raw_files = task["files"]
        if not isinstance(raw_files, list) or len(raw_files) > MAX_FILES:
            raise TaskGraphError("Too many or malformed files")
        touched: dict[str, str] = {}
        for name in raw_files:
            path = _relative_path(name)
            if path.casefold() in touched:
                raise TaskGraphError("Duplicate file within one task")
            touched[path.casefold()] = path
        files.append(touched)

    count = len(ids)
    dependents: list[list[int]] = [[] for _ in range(count)]
    waiting = [len(parents) for parents in prerequisites]
    for child, parents in enumerate(prerequisites):
        for parent in parents:
            dependents[parent].append(child)
    ready = [ids[index] for index in range(count) if waiting[index] == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        current = position[heapq.heappop(ready)]
        order.append(current)
        for child in dependents[current]:
            waiting[child] -= 1
            if waiting[child] == 0:
                heapq.heappush(ready, ids[child])
    if len(order) != count:
        stuck = sorted(ids[index] for index in range(count) if waiting[index] > 0)
        raise TaskGraphError(
            "Tasks cannot be ordered because of a dependency cycle: "
            + ", ".join(stuck[:8]))

    # Bit i of ancestors[t] is set when task i is a transitive prerequisite of t.
    ancestors = [0] * count
    for current in order:
        mask = 0
        for parent in prerequisites[current]:
            mask |= ancestors[parent] | (1 << parent)
        ancestors[current] = mask

    editors: dict[str, list[int]] = {}
    for current, touched in enumerate(files):
        for key in touched:
            editors.setdefault(key, []).append(current)
    for key in sorted(editors):
        group = editors[key]
        for offset, first in enumerate(group):
            for second in group[offset + 1:]:
                ordered = ((ancestors[second] >> first) & 1
                           or (ancestors[first] >> second) & 1)
                if not ordered:
                    raise TaskGraphError(
                        f"Unordered edits to {files[first][key]}: "
                        f"{ids[first]} and {ids[second]}")
    return [ids[index] for index in order]
