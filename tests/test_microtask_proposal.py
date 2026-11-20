"""Regression tests for the bounded model-proposal parser (roadmap P2 boundary)."""
from __future__ import annotations

import json
import tracemalloc
import unittest

from forgeos.microtask_proposal import MAX_PROPOSAL_CHARS, ProposalError, parse_proposal
from forgeos.requirement_trace import MAX_LINKS, MAX_REQUIREMENTS, MAX_TEXT_LENGTH
from forgeos.task_graph import MAX_FILES, MAX_PREREQUISITES, MAX_TASKS


def proposal(graph=None, traceability=None):
    graph = graph or {"schema_version": 1, "tasks": [
        {"id": "models", "prerequisites": [], "files": ["models.py"]},
        {"id": "api", "prerequisites": ["models"], "files": ["api.py"]}]}
    traceability = traceability or {"schema_version": 1,
                                    "requirements": [{"id": "REQ-001", "text": "Serve models."}],
                                    "links": [{"task": "api", "requirement": "REQ-001"}]}
    return json.dumps({"graph": graph, "traceability": traceability})


def largest_legal_proposal():
    """Maximum task count, prerequisites and files, plus maximum-size traceability."""
    tasks = []
    for index in range(MAX_TASKS):
        prerequisites = [f"t{earlier}" for earlier in range(max(0, index - MAX_PREREQUISITES), index)]
        files = [f"pkg/m{index}_{slot}.py" for slot in range(MAX_FILES)]
        tasks.append({"id": f"t{index}", "prerequisites": prerequisites, "files": files})
    requirements = [{"id": f"REQ-{number:03d}", "text": "x" * MAX_TEXT_LENGTH}
                    for number in range(1, MAX_REQUIREMENTS + 1)]
    links = [{"task": f"t{(number * 8 + slot) % MAX_TASKS}", "requirement": f"REQ-{number:03d}"}
             for number in range(1, MAX_REQUIREMENTS + 1) for slot in range(MAX_LINKS // MAX_REQUIREMENTS)]
    return json.dumps({
        "graph": {"schema_version": 1, "tasks": tasks},
        "traceability": {"schema_version": 1, "requirements": requirements, "links": links}})


class ProposalParsingTests(unittest.TestCase):
    def test_valid_proposal_returns_order_and_coverage(self):
        result = parse_proposal(proposal())
        self.assertEqual(result.order, ("models", "api"))
        self.assertEqual(result.coverage, {"REQ-001": ["api"]})

    def test_non_text_and_oversized_text_are_refused_before_parsing(self):
        with self.assertRaisesRegex(ProposalError, "must be text"):
            parse_proposal(b"{}")
        with self.assertRaisesRegex(ProposalError, "size limit"):
            parse_proposal(" " * (MAX_PROPOSAL_CHARS + 1))

    def test_malformed_and_fenced_json_is_refused(self):
        for text in ("not json", "```json\n" + proposal() + "\n```", "[1, 2"):
            with self.subTest(text=text[:20]):
                with self.assertRaisesRegex(ProposalError, "not valid JSON"):
                    parse_proposal(text)

    def test_deep_nesting_is_refused_without_crashing(self):
        # Stay under the size limit, so the input reaches the JSON decoder.
        depth = 100000
        self.assertLess(2 * depth, MAX_PROPOSAL_CHARS)
        with self.assertRaisesRegex(ProposalError, "not valid JSON"):
            parse_proposal("[" * depth + "]" * depth)

    def test_repeated_keys_are_refused_rather_than_silently_replaced(self):
        graph = json.dumps({"schema_version": 1, "tasks": [{"id": "a", "prerequisites": [], "files": []}]})
        text = ('{"graph": ' + graph + ', "graph": ' + graph + ', "traceability": {}}')
        with self.assertRaisesRegex(ProposalError, "not valid JSON"):
            parse_proposal(text)

    def test_wrong_top_level_shape_is_refused(self):
        with self.assertRaisesRegex(ProposalError, "Expected only graph and traceability"):
            parse_proposal(json.dumps({"graph": {}}))
        with self.assertRaisesRegex(ProposalError, "Expected only graph and traceability"):
            parse_proposal(json.dumps([1, 2]))

    def test_graph_and_traceability_violations_are_reported(self):
        cyclic = {"schema_version": 1, "tasks": [
            {"id": "a", "prerequisites": ["b"], "files": []},
            {"id": "b", "prerequisites": ["a"], "files": []}]}
        with self.assertRaisesRegex(ProposalError, "dependency cycle"):
            parse_proposal(proposal(graph=cyclic))
        uncovered = {"schema_version": 1, "requirements": [{"id": "REQ-001", "text": "x"}],
                     "links": []}
        with self.assertRaisesRegex(ProposalError, "has no task"):
            parse_proposal(proposal(traceability=uncovered))

    def test_largest_legal_proposal_parses_within_a_fixed_allocation(self):
        text = largest_legal_proposal()
        self.assertLess(len(text), MAX_PROPOSAL_CHARS)
        tracemalloc.start()
        try:
            result = parse_proposal(text)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(len(result.order), MAX_TASKS)
        self.assertEqual(len(result.coverage), MAX_REQUIREMENTS)
        self.assertLess(peak, 16 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
