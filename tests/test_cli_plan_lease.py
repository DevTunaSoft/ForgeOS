"""Planning must respect task leases and never overwrite completed work."""
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.cli import main
from forgeos.core import Store


class PlanningLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.db = self.root / "tasks.sqlite3"
        store = Store(self.db)
        try:
            self.task = store.add(str(self.repo), "Create a plan")
        finally:
            store.close()

    def test_live_planner_lease_excludes_concurrent_owner_and_recover(self):
        def plan(description):
            other = Store(self.db)
            try:
                self.assertEqual(other.get(self.task)["status"], "planning")
                self.assertFalse(other.acquire_lease(self.task, "intruder"))
                self.assertEqual(other.recover(), 0)
                return "Safe plan"
            finally:
                other.close()
        output = io.StringIO()
        with patch("forgeos.cli.Ollama") as mocked, redirect_stdout(output):
            mocked.return_value.plan.side_effect = plan
            self.assertEqual(main(["--db", str(self.db), "plan", str(self.task)]), 0)
        self.assertIn("Safe plan", output.getvalue())
        store = Store(self.db)
        try:
            self.assertEqual(store.get(self.task)["status"], "planned")
            self.assertEqual(store.get(self.task)["result"], "Safe plan")
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM task_leases").fetchone()[0], 0)
        finally:
            store.close()

    def test_completed_task_not_replanned_or_overwritten(self):
        store = Store(self.db)
        try:
            store.update(self.task, "passed", result="verified")
        finally:
            store.close()
        with patch("forgeos.cli.Ollama") as mocked:
            with self.assertRaisesRegex(ValueError, "eligible"):
                main(["--db", str(self.db), "plan", str(self.task)])
            mocked.assert_not_called()
        store = Store(self.db)
        try:
            self.assertEqual(store.get(self.task)["result"], "verified")
            self.assertEqual(store.get(self.task)["status"], "passed")
        finally:
            store.close()

    def test_existing_execution_lease_blocks_planner_before_mutation(self):
        store = Store(self.db)
        self.assertTrue(store.acquire_lease(self.task, "other"))
        try:
            with self.assertRaisesRegex(ValueError, "active execution lease"):
                main(["--db", str(self.db), "plan", str(self.task)])
            self.assertEqual(store.get(self.task)["status"], "queued")
        finally:
            store.release_lease(self.task, "other")
            store.close()

    def test_planning_error_blocks_owned_task_and_releases_lease(self):
        with patch("forgeos.cli.Ollama") as mocked:
            mocked.return_value.plan.side_effect = RuntimeError("model unavailable")
            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                main(["--db", str(self.db), "plan", str(self.task)])
        store = Store(self.db)
        try:
            task = store.get(self.task)
            self.assertEqual(task["status"], "blocked")
            self.assertIn("model unavailable", task["last_error"])
            self.assertEqual(store.db.execute(
                "SELECT COUNT(*) FROM task_leases").fetchone()[0], 0)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
