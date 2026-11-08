"""Shared CLI/GUI task actions: leases, safe state, and frozen routing."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from forgeos.cli import main
from forgeos.core import DEFAULT_LEASE_SECONDS, LeaseLostError, Store
from forgeos.task_actions import plan_task, test_task


class TaskActionsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        self.db = self.root / "tasks.sqlite3"
        self.store = Store(self.db)
        self.addCleanup(self.store.close)
        self.task = self.store.add(str(repo), "Plan a safe test")

    def test_shared_planning_excludes_other_owners_and_survives_recover(self):
        other = Store(self.db)
        self.addCleanup(other.close)
        def plan(description):
            self.assertEqual(other.get(self.task)["status"], "planning")
            self.assertFalse(other.acquire_lease(self.task, "other"))
            self.assertEqual(other.recover(), 0)
            return "Plan succeeded"
        text = plan_task(self.store, self.task, model=Mock(plan=plan))
        self.assertEqual(text, "Plan succeeded")
        self.assertEqual(self.store.get(self.task)["status"], "planned")
        self.assertEqual(self.store.get(self.task)["result"], text)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM task_leases").fetchone()[0], 0)

    def test_completed_task_skips_model_factory_before_lease(self):
        self.store.update(self.task, "passed", result="previous success")
        factory = Mock()
        with self.assertRaisesRegex(ValueError, "eligible"):
            plan_task(self.store, self.task, model_factory=factory)
        factory.assert_not_called()
        self.assertEqual(self.store.get(self.task)["result"], "previous success")
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM task_leases").fetchone()[0], 0)

    def test_factory_is_invoked_once_after_exclusive_planning_lease(self):
        other = Store(self.db)
        self.addCleanup(other.close)
        def create():
            self.assertEqual(other.get(self.task)["status"], "planning")
            self.assertFalse(other.acquire_lease(self.task, "intruder"))
            return Mock(plan=Mock(return_value="Deferred model response"))
        factory = Mock(side_effect=create)
        self.assertEqual(plan_task(self.store, self.task, model_factory=factory),
                         "Deferred model response")
        factory.assert_called_once()

    def test_qualified_auto_planning_uses_attested_final_evidence(self):
        from forgeos.model_routing import MemorySnapshot
        raw = {
            "schema": 1, "kind": "role-qualification",
            "model_sha256": "a" * 64, "runtime_sha256": "b" * 64,
            "role": "planning", "backend": "llama.cpp",
            "model_name": "forgeos-managed", "tasks": 20,
            "json_successes": 20, "objective_successes": 18,
            "runtime_failures": 0, "safety_violations": 0,
            "model_bytes": 1_000_000_000,
            "peak_memory_bytes": 2_000_000_000,
            "median_latency_seconds": 1.0,
        }
        model = Mock()
        model.model = "qualified-auto"
        model.backend = "llama.cpp"
        model.managed_model_sha256 = "a" * 64
        model.managed_runtime_sha256 = "b" * 64
        model.plan.return_value = "Qualified planning result"
        with patch(
                "forgeos.model_qualification.load_qualification_records",
                return_value=[raw]), patch(
                "forgeos.model_routing.system_memory",
                return_value=MemorySnapshot(16 * 1024**3, 12 * 1024**3)):
            result = plan_task(self.store, self.task, model=model)
        self.assertEqual(result, "Qualified planning result")
        self.assertEqual(model.model, "forgeos-managed")
        model.plan.assert_called_once_with("Plan a safe test")
        self.assertEqual(self.store.get(self.task)["result"], result)

    def test_qualified_auto_planning_fails_before_prompt_without_evidence(self):
        from forgeos.model_routing import MemorySnapshot
        model = Mock()
        model.model = "qualified-auto"
        model.backend = "llama.cpp"
        model.managed_model_sha256 = "a" * 64
        model.managed_runtime_sha256 = "b" * 64
        with patch(
                "forgeos.model_qualification.load_qualification_records",
                return_value=[]), patch(
                "forgeos.model_routing.system_memory",
                return_value=MemorySnapshot(16 * 1024**3, 12 * 1024**3)):
            with self.assertRaisesRegex(RuntimeError, "No measured-qualified"):
                plan_task(self.store, self.task, model=model)
        model.plan.assert_not_called()
        self.assertEqual(self.store.get(self.task)["status"], "blocked")

    def test_test_permission_refused_before_mutation(self):
        with patch("forgeos.task_actions._run_workspace_tests") as tests:
            with self.assertRaisesRegex(ValueError, "permission"):
                test_task(self.store, self.task, "python-unittest")
            tests.assert_not_called()
        self.assertEqual(self.store.get(self.task)["status"], "queued")

    def test_manual_tests_use_shared_isolated_runtime_and_store_result(self):
        outcome = {"passed": True, "output": "ok", "returncode": 0,
                   "containment": "test-fixture"}
        with patch("forgeos.task_actions._run_workspace_tests",
                   return_value=outcome) as tests:
            result = test_task(self.store, self.task, "python-unittest",
                               allow_host_tests=True)
        self.assertEqual(result, outcome)
        # Windows temp directories may have 8.3 aliases; compare against the
        # canonical Git project path persisted by Store.add().
        tests.assert_called_once_with(
            Path(self.store.get(self.task)["project"]), "python-unittest")
        record = self.store.get(self.task)
        self.assertEqual(record["status"], "passed")
        self.assertEqual(json.loads(record["result"]), outcome)
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM task_leases").fetchone()[0], 0)

    def test_failed_or_incomplete_tests_never_approve_a_task(self):
        with patch("forgeos.task_actions._run_workspace_tests",
                   return_value={"passed": False, "output": "failure"}):
            failed = test_task(self.store, self.task, "python-unittest",
                               allow_host_tests=True)
        self.assertFalse(failed["passed"])
        self.assertEqual(self.store.get(self.task)["status"], "failed")
        with patch("forgeos.task_actions._run_workspace_tests",
                   return_value={"passed": True, "output": "truncated",
                                 "capture_complete": False}):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                test_task(self.store, self.task, "python-unittest",
                          allow_host_tests=True)
        self.assertEqual(self.store.get(self.task)["status"], "blocked")

    def test_completed_or_blocked_task_cannot_be_retested(self):
        self.store.update(self.task, "passed", result="verified")
        with patch("forgeos.task_actions._run_workspace_tests") as tests:
            with self.assertRaisesRegex(ValueError, "eligible"):
                test_task(self.store, self.task, "python-unittest",
                          allow_host_tests=True)
            tests.assert_not_called()
        self.assertEqual(self.store.get(self.task)["result"], "verified")

    def test_runner_takeover_cannot_publish_old_pass_or_block_new_worker(self):
        other = Store(self.db)
        self.addCleanup(other.close)
        def takeover(path, preset):
            with other.db:
                other.db.execute(
                    "UPDATE task_leases SET heartbeat=? WHERE task_id=?",
                    (time.time() - DEFAULT_LEASE_SECONDS - 5, self.task))
            self.assertEqual(other.recover(), 1)
            self.assertTrue(other.acquire_lease(self.task, "replacement"))
            self.assertTrue(other.transition(
                self.task, {"blocked"}, "editing",
                lease_owner="replacement", result="new checkpoint"))
            return {"passed": True, "output": "obsolete"}
        with patch("forgeos.task_actions._run_workspace_tests",
                   side_effect=takeover):
            with self.assertRaisesRegex(LeaseLostError, "lease"):
                test_task(self.store, self.task, "python-unittest",
                          allow_host_tests=True)
        self.assertEqual(self.store.get(self.task)["status"], "editing")
        self.assertEqual(self.store.get(self.task)["result"], "new checkpoint")
        self.assertTrue(other.release_lease(self.task, "replacement"))

    def test_cli_test_refuses_implicit_host_execution(self):
        with patch("forgeos.task_actions._run_workspace_tests") as tests:
            with self.assertRaisesRegex(ValueError, "permission"):
                main(["--db", str(self.db), "test", str(self.task),
                      "--preset", "python-unittest"])
            tests.assert_not_called()
        self.assertEqual(self.store.get(self.task)["status"], "queued")

    def test_cli_test_explicit_opt_in_uses_shared_runtime(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with patch("forgeos.task_actions._run_workspace_tests",
                   return_value={"passed": True, "output": "ok"}):
            with redirect_stdout(buf):
                self.assertEqual(main(["--db", str(self.db), "test",
                                       str(self.task), "--preset", "python-unittest",
                                       "--allow-host-tests"]), 0)
        self.assertIn('"passed": true', buf.getvalue())
        self.assertEqual(self.store.get(self.task)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
