"""Requirement IDs and traceability for microtask graphs (roadmap P1 slice).

A requirement list gives stable IDs and short statements. A link list records
which task advances which requirement. Traceability is a separate versioned
document, so the task-graph schema does not change. The check refuses unknown
or duplicate identifiers, unknown tasks, duplicate links and any requirement
that no task advances. It is pure Python with fixed bounds.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

SCHEMA_VERSION = 1
MAX_REQUIREMENTS = 128
MAX_LINKS = 1024
MAX_TEXT_LENGTH = 500
_REQUIREMENT_ID = re.compile(r"REQ-[0-9]{3,4}")


class TraceabilityError(ValueError):
    """A requirement or link document breaks the traceability contract."""


def validate_traceability(document: object, task_ids: Sequence[str]) -> dict[str, list[str]]:
    """Return {requirement_id: sorted task identifiers}, or raise TraceabilityError.

    Pass the order returned by task_graph.validate_task_graph, so every task has
    already passed identifier and graph checks.
    """
    if not isinstance(document, Mapping) or set(document) != {"schema_version", "requirements", "links"}:
        raise TraceabilityError("Expected only schema_version, requirements and links")
    version = document["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise TraceabilityError("Unsupported traceability schema version")

    requirements = document["requirements"]
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= MAX_REQUIREMENTS:
        raise TraceabilityError(f"Requirement count must be 1 to {MAX_REQUIREMENTS}")
    known: dict[str, None] = {}
    for entry in requirements:
        if not isinstance(entry, Mapping) or set(entry) != {"id", "text"}:
            raise TraceabilityError("Each requirement needs id and text only")
        requirement_id = entry["id"]
        if not isinstance(requirement_id, str) or not _REQUIREMENT_ID.fullmatch(requirement_id):
            raise TraceabilityError("Invalid requirement identifier")
        if requirement_id in known:
            raise TraceabilityError("Duplicate requirement identifier")
        text = entry["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
            raise TraceabilityError(f"Requirement text must be 1 to {MAX_TEXT_LENGTH} characters")
        known[requirement_id] = None

    links = document["links"]
    if not isinstance(links, list) or len(links) > MAX_LINKS:
        raise TraceabilityError(f"Link count must be at most {MAX_LINKS}")
    tasks = set(task_ids)
    covered: dict[str, set[str]] = {requirement_id: set() for requirement_id in known}
    seen: set[tuple[str, str]] = set()
    for link in links:
        if not isinstance(link, Mapping) or set(link) != {"task", "requirement"}:
            raise TraceabilityError("Each link needs task and requirement only")
        task, requirement = link["task"], link["requirement"]
        if not isinstance(task, str) or task not in tasks:
            raise TraceabilityError("Link names an unknown task")
        if not isinstance(requirement, str) or requirement not in covered:
            raise TraceabilityError("Link names an unknown requirement")
        if (task, requirement) in seen:
            raise TraceabilityError("Duplicate link")
        seen.add((task, requirement))
        covered[requirement].add(task)

    uncovered = [requirement_id for requirement_id in known if not covered[requirement_id]]
    if uncovered:
        raise TraceabilityError("Requirement has no task: " + ", ".join(uncovered[:8]))
    return {requirement_id: sorted(covered[requirement_id]) for requirement_id in known}
