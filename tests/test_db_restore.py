"""Fail-closed, on-demand SQLite task backup restoration tests."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from forgeos.core import Store


class RestoreBackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.database = self.root / "state.sqlite3"
        self.store = Store(self.database)
        self.addCleanup(self.store.close)
        self.task = self.store.add(str(self.repo), "Restore source")
        self.store.update(self.task, "blocked", result="saved checkpoint")
        self.backup = self.root / "saved backup #1.sqlite3"
        self.store.backup_to(self.backup)

    def test_verified_restore_copy_keeps_records_and_originals(self):
        output = self.root / "safe-restored.sqlite3"
        self.assertEqual(Store.restore_backup_to(self.backup, output), output)
        with closing(sqlite3.connect(output)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check(1)").fetchone()[0],
                             "ok")
            record = db.execute(
                "SELECT status,result FROM tasks WHERE id=?",
                (self.task,)).fetchone()
            self.assertEqual(record, ("blocked", "saved checkpoint"))
        self.assertTrue(self.backup.is_file())
        self.assertEqual(self.store.get_status(self.task), "blocked")
        self.assertFalse(list(self.root.glob(".forgeos-restore-*")))

    def test_existing_destination_is_never_overwritten(self):
        output = self.root / "existing.sqlite3"
        output.write_bytes(b"previous unmodified database")
        with self.assertRaisesRegex(ValueError, "new file"):
            Store.restore_backup_to(self.backup, output)
        self.assertEqual(output.read_bytes(), b"previous unmodified database")
        with self.assertRaisesRegex(ValueError, "new file"):
            Store.restore_backup_to(self.backup, self.backup)
        with self.assertRaisesRegex(ValueError, "new file"):
            Store.restore_backup_to(self.backup, self.root / "missing" / "out.db")

    def test_incompatible_schema_rejected_without_published_file(self):
        other = self.root / "unrelated.sqlite3"
        with closing(sqlite3.connect(other)) as db:
            db.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
            db.commit()
        destination = self.root / "not-restored.sqlite3"
        with self.assertRaisesRegex(ValueError, "compatible ForgeOS"):
            Store.restore_backup_to(other, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob(".forgeos-restore-*")))

    def test_corrupt_source_cannot_publish_restored_copy(self):
        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a SQLite backup")
        destination = self.root / "not-published.sqlite3"
        with self.assertRaises(sqlite3.DatabaseError):
            Store.restore_backup_to(corrupt, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob(".forgeos-restore-*")))

    def test_failed_publication_cleans_staging_file(self):
        target = self.root / "never-published.sqlite3"
        with patch("forgeos.core.os.link", side_effect=OSError("no disk space")):
            with self.assertRaisesRegex(OSError, "no disk space"):
                Store.restore_backup_to(self.backup, target)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".forgeos-restore-*")))

    def test_symlinked_restore_source_is_rejected(self):
        alias = self.root / "alias.sqlite3"
        try:
            alias.symlink_to(self.backup)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        output = self.root / "not-restored.sqlite3"
        with self.assertRaisesRegex(ValueError, "regular SQLite"):
            Store.restore_backup_to(alias, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
