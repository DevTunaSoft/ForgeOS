"""SQLite online backup regressions. No network, models or user projects."""
from contextlib import closing
import json
import os
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

    def test_backup_manifest_rejects_replacement_during_open(self):
        target = self.root / "task-copy.sqlite3"
        manifest = self.root / "task-copy.json"
        self.store.backup_to(target, manifest=manifest)

        def replace_before_open(path, flags):
            path.unlink()
            path.write_bytes(b"operator-owned manifest\n")
            return os.open(path, flags)

        from forgeos import core
        with patch.object(
                core, "_open_bounded_publication_file",
                side_effect=replace_before_open):
            with self.assertRaisesRegex(ValueError, "changed or became a link"):
                core._read_backup_manifest(manifest)
        self.assertEqual(manifest.read_bytes(), b"operator-owned manifest\n")

    def test_streamed_digest_refuses_source_identity_change(self):
        from forgeos import core
        replacement = self.root / "replacement.sqlite3"
        replacement.write_bytes(b"replacement")
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
            with self.assertRaisesRegex(
                    ValueError, "Database artifact changed during use"):
                core._database_file_digest(self.store.path)
        self.assertEqual(calls["count"], 2)

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

    def test_failed_publication_preserves_substituted_temporary_file(self):
        target = self.root / "not-published.sqlite3"

        def substitute(staging, destination):
            staging.unlink()
            staging.write_bytes(b"operator-owned backup staging")
            raise OSError("disk unavailable")

        with patch("forgeos.core._publish_database_copy",
                   side_effect=substitute):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                self.store.backup_to(target)
        self.assertFalse(target.exists())
        staging = list(self.root.glob(".forgeos-backup-*"))
        self.assertEqual(len(staging), 1)
        self.assertEqual(staging[0].read_bytes(), b"operator-owned backup staging")

    def test_windows_publication_uses_exclusive_rename(self):
        staging = self.root / "staging.sqlite3"
        target = self.root / "published.sqlite3"
        staging.write_bytes(b"snapshot")
        real_rename = os.rename
        with patch("forgeos.core.os.name", "nt"), \
                patch("forgeos.core.os.rename", wraps=real_rename) as rename, \
                patch("forgeos.core.os.link") as link, \
                patch("forgeos.core._sync_publication_directory") as sync:
            from forgeos.core import _publish_database_copy
            _publish_database_copy(staging, target)
        rename.assert_called_once_with(staging, target)
        link.assert_not_called()
        sync.assert_not_called()

    def test_posix_publication_keeps_hard_link_path(self):
        staging = self.root / "staging.sqlite3"
        target = self.root / "published.sqlite3"
        staging.write_bytes(b"snapshot")
        real_link = os.link
        with patch("forgeos.core.os.name", "posix"), \
                patch("forgeos.core.os.link", wraps=real_link) as link, \
                patch("forgeos.core.os.rename") as rename, \
                patch("forgeos.core._sync_publication_directory") as sync:
            from forgeos.core import _publish_database_copy
            _publish_database_copy(staging, target)
        link.assert_called_once_with(staging, target)
        rename.assert_not_called()
        sync.assert_called_once_with(target.parent)

    def test_publication_refuses_redirected_staging(self):
        staging = self.root / "staging.sqlite3"
        outside = self.root / "outside.sqlite3"
        outside.write_bytes(b"outside")
        try:
            staging.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("File symlinks unavailable")
        target = self.root / "published.sqlite3"
        from forgeos.core import _publish_database_copy
        with self.assertRaisesRegex(ValueError, "staging|regular"):
            _publish_database_copy(staging, target)
        self.assertFalse(target.exists())
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_publication_refuses_redirected_parent(self):
        staging = self.root / "staging.sqlite3"
        staging.write_bytes(b"snapshot")
        outside = self.root / "outside"
        outside.mkdir()
        redirected = self.root / "redirected"
        try:
            redirected.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable")
        target = redirected / "published.sqlite3"
        from forgeos.core import _publish_database_copy
        with self.assertRaisesRegex(ValueError, "Publication directory"):
            _publish_database_copy(staging, target)
        self.assertFalse((outside / "published.sqlite3").exists())
        self.assertTrue(staging.exists())

    def test_publication_rechecks_staging_identity(self):
        from forgeos import core
        staging = self.root / "staging.sqlite3"
        staging.write_bytes(b"snapshot")
        outside = self.root / "outside.sqlite3"
        outside.write_bytes(b"outside")
        try:
            probe = self.root / "symlink-probe.sqlite3"
            probe.symlink_to(outside)
            probe.unlink()
        except (OSError, NotImplementedError):
            self.skipTest("File symlinks unavailable")
        target = self.root / "published.sqlite3"
        real_info = core._publication_file_info
        swapped = {"value": False}

        def info_then_swap(path, description, *args, **kwargs):
            value = real_info(path, description, *args, **kwargs)
            if not swapped["value"]:
                staging.unlink()
                staging.symlink_to(outside)
                swapped["value"] = True
            return value

        with patch.object(
                core, "_publication_file_info", side_effect=info_then_swap):
            with self.assertRaisesRegex(ValueError, "staging|regular"):
                core._publish_database_copy(staging, target)
        self.assertTrue(swapped["value"])
        self.assertTrue(staging.is_symlink())
        self.assertFalse(target.exists())
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_posix_directory_sync_flushes_and_closes_descriptor(self):
        from forgeos.core import _sync_publication_directory
        with patch("forgeos.core.os.name", "posix"), \
                patch("forgeos.core.os.open", return_value=17) as open_, \
                patch("forgeos.core.os.fsync") as fsync, \
                patch("forgeos.core.os.close") as close:
            _sync_publication_directory(self.root)
        open_.assert_called_once_with(self.root, os.O_RDONLY)
        fsync.assert_called_once_with(17)
        close.assert_called_once_with(17)

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
