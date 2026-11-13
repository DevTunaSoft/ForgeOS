"""SQLite online backup regressions. No network, models or user projects."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from forgeos.core import Store


class OnlineBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.store = Store(self.root / "tasks.sqlite3")
        self.addCleanup(self.store.close)

    def test_committed_wal_data_is_copied_consistently(self):
        task = self.store.add(str(self.repo), "Verify backup")
        self.store.update(task, "planned", result="checkpoint")
        target = self.root / "task-copy.sqlite3"
        self.assertEqual(self.store.backup_to(target), target)
        with closing(sqlite3.connect(target)) as copy:
            self.assertEqual(
                copy.execute("PRAGMA integrity_check(1)").fetchone()[0], "ok")
            self.assertEqual(
                copy.execute("SELECT status,result FROM tasks WHERE id=?",
                             (task,)).fetchone(), ("planned", "checkpoint"))
        self.assertEqual(self.store.get_status(task), "planned")
        self.assertEqual(list(self.root.glob(".forgeos-backup-*")), [])

    def test_never_overwrites_an_existing_file(self):
        target = self.root / "prior.sqlite3"
        target.write_bytes(b"preserve this previous backup")
        with self.assertRaisesRegex(ValueError, "new file"):
            self.store.backup_to(target)
        self.assertEqual(target.read_bytes(), b"preserve this previous backup")
        for invalid in (self.store.path, self.root / "missing" / "new.sqlite3"):
            with self.subTest(path=str(invalid)), self.assertRaises(ValueError):
                self.store.backup_to(invalid)

    def test_failed_publication_cleans_temporary_file(self):
        target = self.root / "not-published.sqlite3"
        with patch("forgeos.core._publish_database_copy",
                   side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                self.store.backup_to(target)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob(".forgeos-backup-*")), [])
        self.assertIsNone(self.store.check_integrity())

    def test_active_transaction_is_rejected_without_a_partial_backup(self):
        target = self.root / "not-committed.sqlite3"
        self.store.db.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaisesRegex(RuntimeError, "active task transaction"):
                self.store.backup_to(target)
            self.assertFalse(target.exists())
        finally:
            self.store.db.rollback()


if __name__ == "__main__":
    unittest.main()
