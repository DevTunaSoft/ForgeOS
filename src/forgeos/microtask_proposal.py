"""Bounded parsing of model-proposed microtask plans (roadmap P2 boundary).

A local model answers a planning request with one JSON object that holds a task
graph and its requirement traceability. This module is where that untrusted text
enters ForgeOS. It refuses oversized text before parsing, refuses malformed,
deeply nested or key-repeating JSON, and then applies the graph and traceability
validators. It never runs anything and never writes a file.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from .requirement_trace import TraceabilityError, validate_traceability
from .task_graph import TaskGraphError, validate_task_graph

MAX_PROPOSAL_CHARS = 256 * 1024
_KEYS = frozenset({"graph", "traceability"})


class ProposalError(ValueError):
    """A model proposal is oversized, malformed, or breaks a contract."""


@dataclass(frozen=True)
class Proposal:
    order: tuple[str, ...]
    coverage: dict[str, list[str]]


def _reject_repeated_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("repeated key")
        result[key] = value
    return result


def parse_proposal(text: object) -> Proposal:
    """Return the validated task order and requirement coverage, or raise ProposalError."""
    if not isinstance(text, str):
        raise ProposalError("Proposal must be text")
    if len(text) > MAX_PROPOSAL_CHARS:
        raise ProposalError("Proposal exceeds the size limit")
    try:
        document = json.loads(text, object_pairs_hook=_reject_repeated_keys)
    except (ValueError, RecursionError):
        raise ProposalError("Proposal is not valid JSON without repeated keys") from None
    if not isinstance(document, Mapping) or set(document) != _KEYS:
        raise ProposalError("Expected only graph and traceability")
    try:
        order = validate_task_graph(document["graph"])
        coverage = validate_traceability(document["traceability"], order)
    except (TaskGraphError, TraceabilityError) as exc:
        raise ProposalError(str(exc)) from None
    return Proposal(order=tuple(order), coverage=coverage)
