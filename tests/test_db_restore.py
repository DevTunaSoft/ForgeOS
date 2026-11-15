"""Fail-closed, on-demand SQLite task backup restoration tests."""
from contextlib import closing
import json
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

    def test_manifest_must_match_before_restore_copy(self):
        manifest = self.root / "saved-backup.json"
        self.store.backup_to(self.root / "manifest-backup.sqlite3", manifest=manifest)
        output = self.root / "manifest-restored.sqlite3"
        Store.restore_backup_to(self.root / "manifest-backup.sqlite3", output,
                                manifest=manifest)
        self.assertTrue(output.is_file())
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["sha256"] = "0" * 64
        manifest.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match"):
            Store.restore_backup_to(self.backup, self.root / "rejected.sqlite3",
                                    manifest=manifest)
        self.assertFalse((self.root / "rejected.sqlite3").exists())

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
        with patch("forgeos.core._publish_database_copy", side_effect=OSError("no disk space")):
            with self.assertRaisesRegex(OSError, "no disk space"):
                Store.restore_backup_to(self.backup, target)
        self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob(".forgeos-restore-*")))

    def test_source_identity_change_blocks_restore_publication(self):
        from forgeos import core
        output = self.root / "changed-source.sqlite3"
        replacement = self.database
        real_info = core._database_source_info
        calls = {"count": 0}

        def source_info(path, description):
            value = real_info(path, description)
            calls["count"] += 1
            if calls["count"] == 2:
                return replacement.lstat()
            return value

        with patch.object(
                core, "_database_source_info", side_effect=source_info):
            with self.assertRaisesRegex(ValueError, "changed during use"):
                Store.restore_backup_to(self.backup, output)
        self.assertEqual(calls["count"], 2)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".forgeos-restore-*")))

    def test_windows_publication_path_is_shared_with_restore(self):
        target = self.root / "windows-restored.sqlite3"
        with patch("forgeos.core._publish_database_copy") as publish:
            Store.restore_backup_to(self.backup, target)
        publish.assert_called_once()
        staging, published = publish.call_args.args
        self.assertEqual(published, target)
        self.assertEqual(staging.parent, self.root)
        self.assertTrue(staging.name.startswith(".forgeos-restore-"))
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
