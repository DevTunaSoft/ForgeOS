"""Regression tests for requirement IDs and traceability (roadmap P1 slice)."""
from __future__ import annotations

import unittest

from forgeos.requirement_trace import (
    MAX_LINKS, MAX_REQUIREMENTS, MAX_TEXT_LENGTH, SCHEMA_VERSION, TraceabilityError,
    validate_traceability)
from forgeos.task_graph import validate_task_graph

TASKS = ["api", "docs", "models", "ui"]


def document(requirements, links, version=SCHEMA_VERSION):
    return {"schema_version": version,
            "requirements": [{"id": rid, "text": "Statement " + rid} for rid in requirements],
            "links": [{"task": task, "requirement": rid} for task, rid in links]}


class TraceabilityTests(unittest.TestCase):
    def assertRejected(self, data, pattern, tasks=TASKS):
        with self.assertRaisesRegex(TraceabilityError, pattern):
            validate_traceability(data, tasks)

    def test_valid_trace_maps_each_requirement_to_sorted_tasks(self):
        data = document(["REQ-001", "REQ-002"],
                        [("ui", "REQ-001"), ("api", "REQ-001"), ("models", "REQ-002")])
        self.assertEqual(validate_traceability(data, TASKS),
                         {"REQ-001": ["api", "ui"], "REQ-002": ["models"]})

    def test_integrates_with_the_task_graph_order(self):
        graph = {"schema_version": 1, "tasks": [
            {"id": "models", "prerequisites": [], "files": ["m.py"]},
            {"id": "api", "prerequisites": ["models"], "files": ["a.py"]}]}
        order = validate_task_graph(graph)
        self.assertEqual(validate_traceability(document(["REQ-001"], [("api", "REQ-001")]), order),
                         {"REQ-001": ["api"]})

    def test_unknown_task_and_requirement_links_are_refused(self):
        self.assertRejected(document(["REQ-001"], [("ghost", "REQ-001")]), "unknown task")
        self.assertRejected(document(["REQ-001"], [("api", "REQ-999")]), "unknown requirement")

    def test_uncovered_requirement_is_refused(self):
        self.assertRejected(document(["REQ-001", "REQ-002"], [("api", "REQ-001")]),
                            "has no task: REQ-002")

    def test_duplicate_and_malformed_identifiers_are_refused(self):
        self.assertRejected(document(["REQ-001", "REQ-001"], [("api", "REQ-001")]),
                            "Duplicate requirement identifier")
        for identifier in ("REQ-1", "req-001", "REQ-00001", "REQ001", ""):
            with self.subTest(identifier=identifier):
                self.assertRejected(document([identifier], [("api", identifier)]),
                                    "Invalid requirement identifier")

    def test_requirement_text_must_be_bounded_and_not_blank(self):
        for text in ("", "   ", "x" * (MAX_TEXT_LENGTH + 1)):
            with self.subTest(length=len(text)):
                data = {"schema_version": 1,
                        "requirements": [{"id": "REQ-001", "text": text}],
                        "links": [{"task": "api", "requirement": "REQ-001"}]}
                self.assertRejected(data, "Requirement text")

    def test_duplicate_links_are_refused(self):
        self.assertRejected(document(["REQ-001"], [("api", "REQ-001"), ("api", "REQ-001")]),
                            "Duplicate link")

    def test_unhashable_link_values_are_refused_without_a_crash(self):
        data = {"schema_version": 1,
                "requirements": [{"id": "REQ-001", "text": "x"}],
                "links": [{"task": ["api"], "requirement": "REQ-001"}]}
        self.assertRejected(data, "unknown task")
        data = {"schema_version": 1,
                "requirements": [{"id": "REQ-001", "text": "x"}],
                "links": [{"task": "api", "requirement": ["REQ-001"]}]}
        self.assertRejected(data, "unknown requirement")

    def test_schema_shape_and_bounds_are_enforced(self):
        self.assertRejected(document(["REQ-001"], [("api", "REQ-001")], version=2),
                            "Unsupported traceability schema version")
        self.assertRejected(document(["REQ-001"], [("api", "REQ-001")], version=True),
                            "Unsupported traceability schema version")
        self.assertRejected({"schema_version": 1, "requirements": [], "links": []},
                            "Requirement count")
        many = [f"REQ-{index:03d}" for index in range(1, MAX_REQUIREMENTS + 2)]
        self.assertRejected(document(many, [("api", "REQ-001")]), "Requirement count")
        self.assertRejected({**document(["REQ-001"], [("api", "REQ-001")]), "note": "x"}, "only")
        self.assertRejected(
            {"schema_version": 1,
             "requirements": [{"id": "REQ-001", "text": "x", "owner": "y"}],
             "links": [{"task": "api", "requirement": "REQ-001"}]},
            "id and text only")
        self.assertRejected(
            {"schema_version": 1,
             "requirements": [{"id": "REQ-001", "text": "x"}],
             "links": [{"task": "api", "requirement": "REQ-001", "weight": 2}]},
            "task and requirement only")
        self.assertRejected(document(["REQ-001"], [("api", "REQ-001")] * (MAX_LINKS + 1)),
                            "Link count")


if __name__ == "__main__":
    unittest.main()
