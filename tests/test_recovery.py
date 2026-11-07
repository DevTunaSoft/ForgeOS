"""Recovery orchestrator tests; no untrusted code is executed."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from forgeos.recovery import resume_safe


class FakeStore:
    def __init__(self, tasks):
        self.tasks = tasks
        self.recovery_calls = 0

    def recover(self):
        self.recovery_calls += 1
        return 1

    def list(self):
        return self.tasks


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)

    def task(self, task_id, checkpoint):
        return {"id": task_id, "status": "blocked", "result": json.dumps(checkpoint)}

    def test_permission_denied_before_store_mutation(self):
        store = FakeStore([])
        with self.assertRaisesRegex(ValueError, "permission"):
            resume_safe(store, self.parent)
        self.assertEqual(store.recovery_calls, 0)

    def test_only_matching_checkpoint_resumes(self):
        valid = {"phase": "test_pending", "workspace": str(self.parent / "task-1")}
        tasks = [self.task(1, valid), self.task(2, {"phase": "repair_pending"}),
                 self.task(3, {"phase": "test_pending", "workspace": "/other/task-3"})]
        runner = Mock(return_value={"passed": True})
        report = resume_safe(FakeStore(tasks), self.parent, allow_host_tests=True, execute=runner)
        self.assertEqual(report["resumed"], [1])
        self.assertEqual(len(report["skipped"]), 2)
        self.assertEqual(runner.call_count, 1)
        self.assertTrue(runner.call_args.kwargs["resume"])

    def test_failure_is_isolated_per_task(self):
        tasks = [self.task(i, {"phase": "test_pending",
                 "workspace": str(self.parent / f"task-{i}")}) for i in (1, 2)]
        runner = Mock(side_effect=[RuntimeError("disk error"), {"passed": True}])
        report = resume_safe(FakeStore(tasks), self.parent, allow_host_tests=True, execute=runner)
        self.assertEqual(report["resumed"], [2])
        self.assertEqual(report["failed"][0]["id"], 1)

    def test_malformed_checkpoint_skipped(self):
        store = FakeStore([{"id": 1, "status": "blocked", "result": "{"}])
        runner = Mock()
        report = resume_safe(store, self.parent, allow_host_tests=True, execute=runner)
        self.assertEqual(len(report["skipped"]), 1)
        runner.assert_not_called()
