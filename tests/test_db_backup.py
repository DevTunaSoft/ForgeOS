"""SQLite online backup regressions. No network, models or user projects."""
from contextlib import closing
import json
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

    def test_backup_manifest_is_bounded_and_matches_published_bytes(self):
        target = self.root / "task-copy.sqlite3"
        manifest = self.root / "task-copy.json"
        self.store.backup_to(target, manifest=manifest)
        value = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(value["format"], "forgeos-sqlite-backup-v1")
        self.assertEqual(value["filename"], target.name)
        self.assertEqual(value["size"], target.stat().st_size)
        import hashlib
        self.assertEqual(
            value["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertLessEqual(manifest.stat().st_size, 1024)

    def test_backup_manifest_never_overwrites_an_existing_file(self):
        target = self.root / "task-copy.sqlite3"
        manifest = self.root / "task-copy.json"
        manifest.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "new file"):
            self.store.backup_to(target, manifest=manifest)
        self.assertFalse(target.exists())
        self.assertEqual(manifest.read_text(encoding="utf-8"), "preserve")

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
        with patch("forgeos.core._publish_database_copy", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                self.store.backup_to(target)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob(".forgeos-backup-*")), [])
        self.assertIsNone(self.store.check_integrity())

    def test_windows_publication_uses_exclusive_rename(self):
        staging = self.root / "staging.sqlite3"
        target = self.root / "published.sqlite3"
        staging.write_bytes(b"snapshot")
        with patch("forgeos.core.os.name", "nt"), \
                patch("forgeos.core.os.rename") as rename, \
                patch("forgeos.core.os.link") as link:
            from forgeos.core import _publish_database_copy
            _publish_database_copy(staging, target)
        rename.assert_called_once_with(staging, target)
        link.assert_not_called()

    def test_posix_publication_keeps_hard_link_path(self):
        staging = self.root / "staging.sqlite3"
        target = self.root / "published.sqlite3"
        staging.write_bytes(b"snapshot")
        with patch("forgeos.core.os.name", "posix"), \
                patch("forgeos.core.os.link") as link, \
                patch("forgeos.core.os.rename") as rename:
            from forgeos.core import _publish_database_copy
            _publish_database_copy(staging, target)
        link.assert_called_once_with(staging, target)
        rename.assert_not_called()

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
