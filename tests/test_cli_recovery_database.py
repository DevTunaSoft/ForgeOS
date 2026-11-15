"""Database-free CLI dispatch coverage for bundle database restore copies."""
import io
from contextlib import redirect_stdout
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.cli import main
from forgeos.core import Store
from forgeos.recovery_package import export_recovery_bundle
from forgeos.worktree_archive import export_worktree_archive


class RecoveryBundleDatabaseCliTests(unittest.TestCase):
    def test_restore_copy_does_not_initialize_active_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            (worktree / ".git").mkdir()
            (worktree / "main.py").write_text("print('ok')\n")
            archive = root / "source.zip"
            export_worktree_archive(worktree, archive)
            handoff = root / "task.jsonl"
            rows = [
                {"record": "header", "format": "forgeos-journal-handoff-v1",
                 "task_id": 1},
                {"record": "task", "task_id": 1, "status": "passed",
                 "attempts": 1, "created": 1.0, "updated": 2.0},
                {"record": "event", "id": 1, "task_id": 1,
                 "event_type": "test", "status": "passed", "actor": "engine",
                 "created": 2.0, "details": "", "action": "test", "attempt": 1,
                 "artifact_ref": "", "evidence_ref": ""},
                {"record": "summary", "task_id": 1, "event_count": 1,
                 "last_event_id": 1, "complete": True},
            ]
            handoff.write_text(
                "".join(json.dumps(row, separators=(",", ":")) + "\n"
                        for row in rows), encoding="utf-8")
            source_database = root / "source.sqlite3"
            database = root / "tasks-backup.sqlite3"
            database_manifest = root / "tasks-backup.json"
            store = Store(source_database)
            try:
                store.add(str(worktree), "cli database task")
                store.backup_to(database, manifest=database_manifest)
            finally:
                store.close()
            bundle = root / "recovery.zip"
            export_recovery_bundle(
                handoff, archive, bundle,
                database=database, database_manifest=database_manifest)
            destination = root / "restored.sqlite3"
            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "bundle database restore must remain Store-init-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "--db", str(root / "missing.sqlite3"),
                    "recovery-bundle-db-restore-copy",
                    str(bundle), str(destination)]), 0)
            self.assertIn("Restored recovery bundle database copy:",
                          output.getvalue())
            Store.inspect_existing_database(destination)


if __name__ == "__main__":
    unittest.main()
