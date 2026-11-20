"""Regression tests for the microtask dependency-graph validator (P1 slice)."""
from __future__ import annotations

import tracemalloc
import unittest

from forgeos.task_graph import (
    MAX_FILES, MAX_PREREQUISITES, MAX_TASKS, SCHEMA_VERSION, TaskGraphError,
    validate_task_graph)


def task(task_id, prerequisites=(), files=()):
    return {"id": task_id, "prerequisites": list(prerequisites), "files": list(files)}


def graph(*tasks):
    return {"schema_version": SCHEMA_VERSION, "tasks": list(tasks)}


class TaskGraphOrderTests(unittest.TestCase):
    def test_order_respects_dependencies_and_ignores_input_order(self):
        tasks = [task("ui", ["models"], ["ui/app.py"]),
                 task("docs", [], ["docs/a.md"]),
                 task("models", [], ["models.py"]),
                 task("api", ["models"], ["api.py"])]
        expected = ["docs", "models", "api", "ui"]
        self.assertEqual(validate_task_graph(graph(*tasks)), expected)
        self.assertEqual(validate_task_graph(graph(*reversed(tasks))), expected)

    def test_transitive_ordering_satisfies_the_same_file_rule(self):
        tasks = [task("a", [], ["app.py"]),
                 task("c", ["a"], []),
                 task("b", ["c"], ["app.py"])]
        self.assertEqual(validate_task_graph(graph(*tasks)), ["a", "c", "b"])


class TaskGraphRejectionTests(unittest.TestCase):
    def assertRejected(self, data, pattern):
        with self.assertRaisesRegex(TaskGraphError, pattern):
            validate_task_graph(data)

    def test_cycles_and_self_dependencies_are_refused(self):
        self.assertRejected(graph(task("a", ["b"]), task("b", ["a"])), "dependency cycle")
        self.assertRejected(graph(task("a", ["a"])), "cannot depend on itself")

    def test_missing_duplicate_and_invalid_identifiers_are_refused(self):
        self.assertRejected(graph(task("a", ["ghost"])), "Missing prerequisite")
        self.assertRejected(graph(task("a"), task("a")), "Duplicate task identifier")
        for name in ("Bad Id", "", "../x", "x" * 65, "CapitalLetters"):
            with self.subTest(name=name):
                self.assertRejected(graph(task(name)), "Invalid task identifier")

    def test_unordered_edits_to_one_file_are_refused_case_insensitively(self):
        self.assertRejected(
            graph(task("a", files=["src/app.py"]), task("b", files=["src/app.py"])),
            "Unordered edits")
        self.assertRejected(
            graph(task("a", files=["src/App.py"]), task("b", files=["src/app.py"])),
            "Unordered edits")

    def test_unsafe_or_non_portable_paths_are_refused(self):
        for path in ("../x.py", "/abs.py", "a//b.py", "a\\b.py", "C:/x.py",
                     "con.py", "docs/NUL .md", "docs/trailing./x.md", ""):
            with self.subTest(path=path):
                self.assertRejected(
                    graph(task("a", files=[path])),
                    "Invalid file path|Path traversal|Non-portable file name")

    def test_duplicate_file_inside_one_task_is_refused(self):
        self.assertRejected(graph(task("a", files=["x.py", "X.py"])),
                            "Duplicate file within one task")

    def test_schema_version_shape_and_bounds_are_enforced(self):
        self.assertRejected({"schema_version": 2, "tasks": [task("a")]},
                            "Unsupported task-graph schema version")
        self.assertRejected({"schema_version": True, "tasks": [task("a")]},
                            "Unsupported task-graph schema version")
        self.assertRejected({"schema_version": 1, "tasks": []}, "Task count")
        self.assertRejected(
            graph(*[task(f"t{index}") for index in range(MAX_TASKS + 1)]),
            "Task count")
        self.assertRejected(
            graph(task("a", files=[f"f{index}.py" for index in range(MAX_FILES + 1)])),
            "Too many or malformed files")
        parents = [task(f"p{index}") for index in range(MAX_PREREQUISITES + 1)]
        self.assertRejected(
            graph(*parents, task("z", [f"p{index}" for index in range(MAX_PREREQUISITES + 1)])),
            "Too many or malformed prerequisites")
        self.assertRejected(
            {"schema_version": 1,
             "tasks": [{"id": "a", "prerequisites": [], "files": [], "extra": 1}]},
            "only")
        self.assertRejected({"schema_version": 1, "tasks": [task("a")], "note": "x"},
                            "only")


class TaskGraphMemoryTests(unittest.TestCase):
    def test_maximum_graph_validates_within_a_fixed_allocation(self):
        # Synthetic fixture at the task limit: each task edits its own file and
        # depends on up to MAX_PREREQUISITES earlier tasks, so every task has a
        # long ancestor chain.
        tasks = [task(f"t{index}",
                      [f"t{earlier}" for earlier in range(max(0, index - MAX_PREREQUISITES), index)],
                      [f"pkg/module{index}.py"])
                 for index in range(MAX_TASKS)]
        data = graph(*tasks)
        tracemalloc.start()
        try:
            order = validate_task_graph(data)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(len(order), MAX_TASKS)
        self.assertEqual(order[0], "t0")
        self.assertLess(peak, 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
