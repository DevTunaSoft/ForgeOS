"""Guarded, offline SQLite activation and rollback tests."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import forgeos.core as core
from forgeos.core import Store


class ActivateBackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.active = self.root / "tasks.sqlite3"
        self.source_database = self.root / "source.sqlite3"
        self.backup = self.root / "verified-backup.sqlite3"
        self.rollback = self.root / "tasks-before-activation.sqlite3"

        active_store = Store(self.active)
        try:
            self.old_task = active_store.add(str(self.repo), "old active task")
        finally:
            active_store.close()

        source_store = Store(self.source_database)
        try:
            self.new_task = source_store.add(str(self.repo), "restored task")
            source_store.backup_to(self.backup)
        finally:
            source_store.close()

    def _descriptions(self, path):
        with closing(sqlite3.connect(path)) as db:
            return [row[0] for row in db.execute(
                "SELECT description FROM tasks ORDER BY id")]

    def test_activation_publishes_backup_and_retains_old_database(self):
        result = Store.activate_backup_to(
            self.backup, self.active, self.rollback, confirm=True)

        self.assertEqual(result, self.active)
        self.assertEqual(self._descriptions(self.active), ["restored task"])
        self.assertEqual(self._descriptions(self.rollback), ["old active task"])
        self.assertTrue(self.backup.is_file())
        self.assertFalse(Path(str(self.active) + ".forgeos-activation.json").exists())
        self.assertFalse(list(self.root.glob(".forgeos-activate-*")))
        self.assertFalse(list(self.root.glob(".forgeos-restore-*")))

    def test_confirmation_is_required_without_side_effects(self):
        with self.assertRaisesRegex(ValueError, "confirmation"):
            Store.activate_backup_to(
                self.backup, self.active, self.rollback, confirm=False)

        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertFalse(self.rollback.exists())
        self.assertFalse(Path(str(self.active) + ".forgeos-activation.json").exists())

    def test_existing_sqlite_sidecars_refuse_activation(self):
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.active) + suffix)
                sidecar.write_bytes(b"occupied sidecar")
                try:
                    with self.assertRaisesRegex(RuntimeError, "sidecars"):
                        Store.activate_backup_to(
                            self.backup, self.active, self.rollback,
                            confirm=True)
                finally:
                    sidecar.unlink()
                self.assertEqual(self._descriptions(self.active), ["old active task"])
                self.assertFalse(self.rollback.exists())

    def test_existing_rollback_path_is_never_overwritten(self):
        original = b"operator-owned rollback placeholder"
        self.rollback.write_bytes(original)

        with self.assertRaisesRegex(ValueError, "new files"):
            Store.activate_backup_to(
                self.backup, self.active, self.rollback, confirm=True)

        self.assertEqual(self.rollback.read_bytes(), original)
        self.assertEqual(self._descriptions(self.active), ["old active task"])

    def test_symlinked_activation_paths_are_refused(self):
        marker = Path(str(self.active) + ".forgeos-activation.json")
        aliases = (self.root / "active-alias.sqlite3", self.rollback, marker)
        for alias in aliases:
            with self.subTest(alias=alias.name):
                try:
                    alias.symlink_to(self.active)
                except (OSError, NotImplementedError):
                    self.skipTest("Symlinks unavailable")
                try:
                    with self.assertRaisesRegex(ValueError, "new files"):
                        Store.activate_backup_to(
                            self.backup,
                            alias if alias != self.rollback and alias != marker
                            else self.active,
                            self.rollback if alias != self.rollback else alias,
                            confirm=True)
                finally:
                    alias.unlink()

    def test_manifest_mismatch_prevents_activation_before_marker(self):
        manifest = self.root / "backup.json"
        manifest_store = Store(self.source_database)
        try:
            manifest_backup = self.root / "manifest-backup.sqlite3"
            manifest_store.backup_to(manifest_backup, manifest=manifest)
        finally:
            manifest_store.close()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["sha256"] = "0" * 64
        manifest.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "does not match"):
            Store.activate_backup_to(
                manifest_backup, self.active, self.rollback,
                manifest=manifest, confirm=True)

        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertFalse(self.rollback.exists())
        self.assertFalse(Path(str(self.active) + ".forgeos-activation.json").exists())

    def test_failed_publish_restores_original_and_removes_marker(self):
        real_move = core._move_database_copy
        failed = False

        def fail_once(source, target):
            nonlocal failed
            if target == self.active and not failed:
                failed = True
                raise OSError("publish unavailable")
            return real_move(source, target)

        with patch("forgeos.core._move_database_copy", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "publish unavailable"):
                Store.activate_backup_to(
                    self.backup, self.active, self.rollback, confirm=True)

        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertFalse(self.rollback.exists())
        self.assertFalse(Path(str(self.active) + ".forgeos-activation.json").exists())

    def test_failed_rollback_keeps_marker_and_old_database_for_recovery(self):
        real_move = core._move_database_copy

        def fail_active_moves(source, target):
            if target == self.active:
                raise OSError("active path unavailable")
            return real_move(source, target)

        with patch("forgeos.core._move_database_copy", side_effect=fail_active_moves):
            with self.assertRaisesRegex(OSError, "active path unavailable"):
                Store.activate_backup_to(
                    self.backup, self.active, self.rollback, confirm=True)

        marker = Path(str(self.active) + ".forgeos-activation.json")
        self.assertFalse(self.active.exists())
        self.assertEqual(self._descriptions(self.rollback), ["old active task"])
        self.assertTrue(marker.is_file())
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["phase"],
                         "old_moved")


if __name__ == "__main__":
    unittest.main()
