"""Database-free CLI coverage for recovery bundle indexes."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.cli import main
from forgeos.core import Store
from forgeos.diagnostics import EvidenceStore
from forgeos.recovery_bundle import export_recovery_bundle_index
from forgeos.worktree_archive import export_worktree_archive


class RecoveryBundleIndexCliTests(unittest.TestCase):
    def test_index_and_check_do_not_initialize_task_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            (worktree / ".git").mkdir()
            (worktree / "main.py").write_text("print('ok')\n")
            archive = root / "source.zip"
            export_worktree_archive(worktree, archive)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            reference = EvidenceStore(evidence_root).put("output\n")
            handoff = root / "task.jsonl"
            rows = [
                {"record": "header", "format": "forgeos-journal-handoff-v1",
                 "task_id": 1},
                {"record": "task", "task_id": 1, "status": "passed",
                 "attempts": 1, "created": 1.0, "updated": 2.0},
                {"record": "event", "id": 1, "task_id": 1,
                 "event_type": "test", "status": "passed", "actor": "engine",
                 "created": 2.0, "details": "", "action": "test", "attempt": 1,
                 "artifact_ref": "", "evidence_ref": reference},
                {"record": "summary", "task_id": 1, "event_count": 1,
                 "last_event_id": 1, "complete": True},
            ]
            handoff.write_text(
                "".join(json.dumps(row, separators=(",", ":")) + "\n"
                        for row in rows), encoding="utf-8")
            index = root / "index.jsonl"

            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Recovery bundle index must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "--db", str(root / "missing.sqlite3"),
                    "recovery-bundle-index", str(handoff), str(archive),
                    str(index), "--evidence-directory", str(evidence_root)]), 0)
            self.assertIn("Exported recovery bundle index:", output.getvalue())

            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Recovery bundle check must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "recovery-bundle-index-check", str(index),
                    "--handoff", str(handoff),
                    "--worktree-archive", str(archive),
                    "--evidence-directory", str(evidence_root)]), 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report["valid"])
            self.assertEqual(report["evidence_files"], 1)


if __name__ == "__main__":
    unittest.main()
