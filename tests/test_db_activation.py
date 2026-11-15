"""Guarded, offline SQLite activation and rollback tests."""
from contextlib import closing
import errno
import os
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

    def _leave_interrupted_activation(self):
        real_move = core._move_database_copy

        def fail_active_moves(source, target):
            if target == self.active:
                raise OSError("active path unavailable")
            return real_move(source, target)

        with patch("forgeos.core._move_database_copy", side_effect=fail_active_moves):
            with self.assertRaisesRegex(OSError, "active path unavailable"):
                Store.activate_backup_to(
                    self.backup, self.active, self.rollback, confirm=True)

    def _leave_crashed_activation_with_staged_file(self):
        staged = self.root / ".forgeos-activate-crashed.sqlite3"
        Store.restore_backup_to(self.backup, staged)
        core._move_database_copy(self.active, self.rollback)
        marker = Path(str(self.active) + ".forgeos-activation.json")
        core._write_activation_marker(marker, {
            "format": core.ACTIVATION_MARKER_FORMAT,
            "source": str(self.backup),
            "destination": str(self.active),
            "rollback": str(self.rollback),
            "staged": str(staged),
            "phase": "old_moved",
        })
        return staged, marker

    def _leave_published_activation_with_marker(self):
        staged = self.root / ".forgeos-activate-published.sqlite3"
        Store.restore_backup_to(self.backup, staged)
        core._move_database_copy(self.active, self.rollback)
        core._move_database_copy(staged, self.active)
        marker = Path(str(self.active) + ".forgeos-activation.json")
        core._write_activation_marker(marker, {
            "format": core.ACTIVATION_MARKER_FORMAT,
            "source": str(self.backup),
            "destination": str(self.active),
            "rollback": str(self.rollback),
            "staged": str(staged),
            "phase": "published",
        })
        return marker, staged

    def _leave_prepared_activation_with_staged_file(self):
        staged = self.root / ".forgeos-activate-prepared.sqlite3"
        Store.restore_backup_to(self.backup, staged)
        marker = Path(str(self.active) + ".forgeos-activation.json")
        core._write_activation_marker(marker, {
            "format": core.ACTIVATION_MARKER_FORMAT,
            "source": str(self.backup),
            "destination": str(self.active),
            "rollback": str(self.rollback),
            "staged": str(staged),
            "phase": "prepared",
        })
        return marker, staged

    def test_activation_publishes_backup_and_retains_old_database(self):
        result = Store.activate_backup_to(
            self.backup, self.active, self.rollback, confirm=True)

        self.assertEqual(result, self.active)
        self.assertEqual(self._descriptions(self.active), ["restored task"])
        self.assertEqual(self._descriptions(self.rollback), ["old active task"])
        self.assertTrue(self.backup.is_file())
        self.assertFalse(Path(str(self.active) + ".forgeos-activation.json").exists())
        for database in (self.active, self.rollback):
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse(Path(str(database) + suffix).exists())
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

    def test_move_refuses_redirected_source(self):
        outside = self.root / "move-outside.sqlite3"
        outside.write_bytes(b"outside")
        alias = self.root / "move-alias.sqlite3"
        try:
            alias.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        target = self.root / "move-target.sqlite3"
        try:
            with self.assertRaisesRegex(ValueError, "Move source|regular"):
                core._move_database_copy(alias, target)
        finally:
            alias.unlink()
        self.assertFalse(target.exists())
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_move_rechecks_source_identity_before_unlink(self):
        source = self.root / "move-source.sqlite3"
        source.write_bytes(b"private source")
        target = self.root / "move-target.sqlite3"
        replacement = self.source_database
        real_info = core._publication_file_info
        calls = {"source": 0}

        def publication_info(path, description, *args, **kwargs):
            value = real_info(path, description, *args, **kwargs)
            if path == source:
                calls["source"] += 1
                if calls["source"] == 2:
                    return replacement.lstat()
            return value

        with patch.object(
                core, "_publication_file_info", side_effect=publication_info):
            with self.assertRaisesRegex(ValueError, "Move source changed"):
                core._move_database_copy(source, target)
        self.assertGreaterEqual(calls["source"], 2)
        self.assertTrue(source.exists())
        self.assertFalse(target.exists())

    def test_move_rechecks_destination_parent_identity(self):
        source = self.root / "move-source.sqlite3"
        source.write_bytes(b"private source")
        target = self.root / "move-target.sqlite3"
        replacement_parent = self.root / "replacement-parent"
        replacement_parent.mkdir()
        real_info = core._publication_directory_info
        calls = {"target": 0}

        def directory_info(path):
            value = real_info(path)
            if path == target.parent:
                calls["target"] += 1
                if calls["target"] == 2:
                    return replacement_parent.lstat()
            return value

        with patch.object(
                core, "_publication_directory_info", side_effect=directory_info):
            with self.assertRaisesRegex(
                    ValueError, "Move publication directory changed"):
                core._move_database_copy(source, target)
        self.assertGreaterEqual(calls["target"], 2)
        self.assertTrue(source.exists())
        self.assertFalse(target.exists())

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

    def test_enospc_before_publishing_marker_rolls_back_cleanly(self):
        real_write = core._write_activation_marker

        def fail_publishing_marker(marker, payload, *, replace=False):
            if replace and payload["phase"] == "publishing":
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_write(marker, payload, replace=replace)

        with patch("forgeos.core._write_activation_marker",
                   side_effect=fail_publishing_marker):
            with self.assertRaisesRegex(OSError, "No space"):
                Store.activate_backup_to(
                    self.backup, self.active, self.rollback, confirm=True)

        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertFalse(self.rollback.exists())
        self.assertFalse(Path(str(self.active) + ".forgeos-activation.json").exists())
        self.assertFalse(list(self.root.glob(".forgeos-activate-*")))

    def test_enospc_after_publication_leaves_recoverable_publishing_marker(self):
        real_write = core._write_activation_marker

        def fail_published_marker(marker, payload, *, replace=False):
            if replace and payload["phase"] == "published":
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_write(marker, payload, replace=replace)

        with patch("forgeos.core._write_activation_marker",
                   side_effect=fail_published_marker):
            with self.assertRaisesRegex(OSError, "No space"):
                Store.activate_backup_to(
                    self.backup, self.active, self.rollback, confirm=True)

        marker = Path(str(self.active) + ".forgeos-activation.json")
        self.assertEqual(self._descriptions(self.active), ["restored task"])
        self.assertEqual(self._descriptions(self.rollback), ["old active task"])
        self.assertEqual(Store.activation_status(self.active)["phase"],
                         "publishing")
        self.assertFalse(list(self.root.glob(".forgeos-activate-*")))

        result = Store.finalize_activation(self.active, confirm=True)
        self.assertEqual(result, self.active)
        self.assertFalse(marker.exists())
        self.assertTrue(self.rollback.exists())

    def test_rollback_refuses_a_substituted_valid_database(self):
        self._leave_interrupted_activation()
        self.rollback.write_bytes(self.backup.read_bytes())

        with self.assertRaisesRegex(ValueError, "database changed"):
            Store.rollback_activation(self.active, confirm=True)
        self.assertFalse(self.active.exists())
        self.assertTrue(self.rollback.exists())
        self.assertTrue(Path(str(self.active) + ".forgeos-activation.json").exists())

    def test_publishing_finalization_refuses_a_substituted_active_database(self):
        real_write = core._write_activation_marker

        def fail_published_marker(marker, payload, *, replace=False):
            if replace and payload["phase"] == "published":
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_write(marker, payload, replace=replace)

        with patch("forgeos.core._write_activation_marker",
                   side_effect=fail_published_marker):
            with self.assertRaisesRegex(OSError, "No space"):
                Store.activate_backup_to(
                    self.backup, self.active, self.rollback, confirm=True)
        self.active.write_bytes(self.rollback.read_bytes())

        with self.assertRaisesRegex(ValueError, "database changed"):
            Store.finalize_activation(self.active, confirm=True)
        self.assertTrue(self.active.exists())
        self.assertTrue(self.rollback.exists())
        self.assertTrue(Path(str(self.active) + ".forgeos-activation.json").exists())

    def test_failed_rollback_keeps_marker_and_old_database_for_recovery(self):
        self._leave_interrupted_activation()

        marker = Path(str(self.active) + ".forgeos-activation.json")
        self.assertFalse(self.active.exists())
        self.assertEqual(self._descriptions(self.rollback), ["old active task"])
        self.assertTrue(marker.is_file())
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["phase"],
                         "publishing")

    def test_marker_status_is_bounded_and_explicit_rollback_reconciles(self):
        self._leave_interrupted_activation()

        status = Store.activation_status(self.active)
        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["phase"], "publishing")
        self.assertFalse(status["destination_exists"])
        self.assertTrue(status["rollback_exists"])
        self.assertFalse(status["staged_exists"])

        result = Store.rollback_activation(self.active, confirm=True)
        self.assertEqual(result, self.active)
        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertFalse(self.rollback.exists())
        self.assertEqual(Store.activation_status(self.active)["status"], "clear")

    def test_rollback_confirmation_and_occupied_destination_are_fail_closed(self):
        self._leave_interrupted_activation()
        with self.assertRaisesRegex(ValueError, "confirmation"):
            Store.rollback_activation(self.active, confirm=False)
        self.assertTrue(self.rollback.exists())

        self.active.write_bytes(b"operator-owned destination")
        with self.assertRaisesRegex(ValueError, "occupied"):
            Store.rollback_activation(self.active, confirm=True)
        self.assertEqual(self.active.read_bytes(), b"operator-owned destination")
        self.assertTrue(self.rollback.exists())

    def test_rollback_refuses_sidecar_beside_retained_database(self):
        self._leave_interrupted_activation()
        sidecar = Path(str(self.rollback) + "-wal")
        sidecar.write_bytes(b"retained journal")
        try:
            with self.assertRaisesRegex(RuntimeError, "sidecars"):
                Store.rollback_activation(self.active, confirm=True)
        finally:
            sidecar.unlink()
        self.assertFalse(self.active.exists())
        self.assertTrue(self.rollback.exists())

    def test_rollback_preserves_crashed_staging_until_explicit_discard(self):
        staged, marker = self._leave_crashed_activation_with_staged_file()

        with self.assertRaisesRegex(RuntimeError, "staged activation file"):
            Store.rollback_activation(self.active, confirm=True)
        self.assertFalse(self.active.exists())
        self.assertTrue(self.rollback.exists())
        self.assertTrue(staged.exists())
        self.assertTrue(marker.exists())

        result = Store.rollback_activation(
            self.active, confirm=True, discard_staged=True)
        self.assertEqual(result, self.active)
        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertFalse(self.rollback.exists())
        self.assertFalse(staged.exists())
        self.assertFalse(marker.exists())

    def test_discard_staged_refuses_corrupt_file_without_moving_rollback(self):
        staged, marker = self._leave_crashed_activation_with_staged_file()
        staged.write_bytes(b"not a ForgeOS database")

        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            Store.rollback_activation(
                self.active, confirm=True, discard_staged=True)
        self.assertFalse(self.active.exists())
        self.assertTrue(self.rollback.exists())
        self.assertTrue(staged.exists())
        self.assertTrue(marker.exists())

    def test_published_marker_finalization_validates_both_databases(self):
        marker, staged = self._leave_published_activation_with_marker()

        with self.assertRaisesRegex(ValueError, "confirmation"):
            Store.finalize_activation(self.active)
        self.assertTrue(marker.exists())

        result = Store.finalize_activation(self.active, confirm=True)
        self.assertEqual(result, self.active)
        self.assertEqual(self._descriptions(self.active), ["restored task"])
        self.assertEqual(self._descriptions(self.rollback), ["old active task"])
        self.assertFalse(marker.exists())
        self.assertFalse(staged.exists())

    def test_published_finalization_refuses_a_staged_artifact(self):
        marker, staged = self._leave_published_activation_with_marker()
        staged.write_bytes(b"operator-owned ambiguity")

        with self.assertRaisesRegex(RuntimeError, "staged"):
            Store.finalize_activation(self.active, confirm=True)
        self.assertTrue(marker.exists())
        self.assertTrue(staged.exists())
        self.assertEqual(self._descriptions(self.active), ["restored task"])
        self.assertEqual(self._descriptions(self.rollback), ["old active task"])

    def test_prepared_abandonment_preserves_active_until_explicit_discard(self):
        marker, staged = self._leave_prepared_activation_with_staged_file()

        with self.assertRaisesRegex(ValueError, "confirmation"):
            Store.abandon_activation(self.active)
        with self.assertRaisesRegex(RuntimeError, "staged activation file"):
            Store.abandon_activation(self.active, confirm=True)
        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertFalse(self.rollback.exists())
        self.assertTrue(staged.exists())
        self.assertTrue(marker.exists())

        result = Store.abandon_activation(
            self.active, confirm=True, discard_staged=True)
        self.assertEqual(result, self.active)
        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertFalse(self.rollback.exists())
        self.assertFalse(staged.exists())
        self.assertFalse(marker.exists())

    def test_prepared_abandonment_refuses_corrupt_staging(self):
        marker, staged = self._leave_prepared_activation_with_staged_file()
        staged.write_bytes(b"not a ForgeOS database")

        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            Store.abandon_activation(
                self.active, confirm=True, discard_staged=True)
        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertTrue(staged.exists())
        self.assertTrue(marker.exists())

    def test_oversized_marker_is_rejected_without_unbounded_read(self):
        marker = Path(str(self.active) + ".forgeos-activation.json")
        marker.write_bytes(b"x" * 4097)
        with self.assertRaisesRegex(ValueError, "oversized"):
            Store.activation_status(self.active)

    def test_marker_read_refuses_hard_linked_marker(self):
        marker = Path(str(self.active) + ".forgeos-activation.json")
        outside = self.root / "marker-copy.json"
        outside.write_text(
            json.dumps({"format": core.ACTIVATION_MARKER_FORMAT,
                        "phase": "prepared"}),
            encoding="utf-8")
        try:
            marker.hardlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Hard links unavailable")
        with self.assertRaisesRegex(ValueError, "unlinked"):
            Store.activation_status(self.active)
        self.assertEqual(
            json.loads(outside.read_text(encoding="utf-8"))["phase"],
            "prepared")

    def test_marker_read_refuses_identity_change(self):
        marker = Path(str(self.active) + ".forgeos-activation.json")
        marker.write_text(
            json.dumps({"format": core.ACTIVATION_MARKER_FORMAT,
                        "phase": "prepared"}),
            encoding="utf-8")
        replacement = self.root / "replacement-marker.json"
        replacement.write_text("replacement", encoding="utf-8")
        real_info = core._publication_file_info
        calls = {"count": 0}

        def publication_info(path, description, *args, **kwargs):
            value = real_info(path, description, *args, **kwargs)
            if path == marker:
                calls["count"] += 1
                if calls["count"] == 2:
                    return replacement.lstat()
            return value

        with patch.object(
                core, "_publication_file_info", side_effect=publication_info):
            with self.assertRaisesRegex(
                    ValueError, "Activation marker changed during read"):
                core._read_activation_marker(marker)
        self.assertEqual(calls["count"], 2)

    def test_replacing_activation_marker_refuses_hard_linked_marker(self):
        marker = self.root / "activation-marker.json"
        outside = self.root / "marker-copy.json"
        outside.write_text("original", encoding="utf-8")
        try:
            marker.hardlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Hard links unavailable")
        payload = {"format": core.ACTIVATION_MARKER_FORMAT,
                   "phase": "prepared"}
        with patch("forgeos.core.os.replace") as replace:
            with self.assertRaisesRegex(ValueError, "unlinked"):
                core._write_activation_marker(marker, payload, replace=True)
        replace.assert_not_called()
        self.assertEqual(marker.read_text(encoding="utf-8"), "original")
        self.assertEqual(outside.read_text(encoding="utf-8"), "original")
        self.assertFalse(list(self.root.glob(".forgeos-activation-*")))

    def test_replacing_activation_marker_refuses_parent_identity_change(self):
        marker = self.root / "activation-marker.json"
        marker.write_text("original", encoding="utf-8")
        replacement = self.root / "replacement-parent"
        replacement.mkdir()
        payload = {"format": core.ACTIVATION_MARKER_FORMAT,
                   "phase": "prepared"}
        real_info = core._publication_directory_info
        calls = {"count": 0}

        def directory_info(path):
            value = real_info(path)
            if path == self.root:
                calls["count"] += 1
                if calls["count"] == 2:
                    return replacement.lstat()
            return value

        with patch.object(
                core, "_publication_directory_info", side_effect=directory_info):
            with self.assertRaisesRegex(
                    ValueError, "publication inputs changed"):
                core._write_activation_marker(marker, payload, replace=True)
        self.assertGreaterEqual(calls["count"], 2)
        self.assertEqual(marker.read_text(encoding="utf-8"), "original")
        self.assertFalse(list(self.root.glob(".forgeos-activation-*")))

    def test_marker_staging_substitution_is_preserved_and_not_deleted(self):
        marker = self.root / "activation-marker.json"
        marker.write_text("original", encoding="utf-8")
        replacement = self.root / "replacement-staging.tmp"
        replacement.write_text("operator evidence", encoding="utf-8")
        payload = {"format": core.ACTIVATION_MARKER_FORMAT,
                   "phase": "prepared"}
        real_info = core._publication_file_info
        calls = {"staging": 0}

        def publication_info(path, description, *args, **kwargs):
            value = real_info(path, description, *args, **kwargs)
            if path.name.startswith(".forgeos-activation-"):
                calls["staging"] += 1
                if calls["staging"] == 3:
                    return replacement.lstat()
            return value

        with patch.object(
                core, "_publication_file_info", side_effect=publication_info):
            with self.assertRaisesRegex(
                    ValueError, "publication inputs changed"):
                core._write_activation_marker(marker, payload, replace=True)
        self.assertGreaterEqual(calls["staging"], 4)
        self.assertEqual(marker.read_text(encoding="utf-8"), "original")
        self.assertEqual(
            replacement.read_text(encoding="utf-8"), "operator evidence")
        self.assertFalse(list(self.root.glob(".forgeos-activation-*")))

    def test_replacing_activation_marker_flushes_posix_directory(self):
        marker = self.root / "activation-marker.json"
        marker.write_text("original", encoding="utf-8")
        payload = {"format": "forgeos-db-activation-v1", "phase": "prepared"}
        real_replace = os.replace
        with patch("forgeos.core.os.replace", side_effect=real_replace) as replace, \
                patch("forgeos.core._sync_publication_directory") as sync:
            core._write_activation_marker(marker, payload, replace=True)
        replace.assert_called_once()
        sync.assert_called_once_with(self.root)
        self.assertEqual(
            json.loads(marker.read_text(encoding="utf-8"))["phase"],
            "prepared")

    def test_removing_activation_marker_flushes_posix_directory(self):
        marker = self.root / "activation-marker.json"
        marker.write_text("marker", encoding="utf-8")
        with patch("forgeos.core._sync_publication_directory") as sync:
            core._remove_activation_marker(marker)
        self.assertFalse(marker.exists())
        sync.assert_called_once_with(self.root)

    def _open_exclusive_windows_handle(self, path):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateFileW(
            str(path), 0x80000000, 0, None, 3, 0x80, None)
        invalid = ctypes.c_void_p(-1).value
        if handle == invalid or handle == -1:
            raise ctypes.WinError(ctypes.get_last_error())
        return kernel32, handle

    @unittest.skipUnless(os.name == "nt", "Windows share-mode contract")
    def test_windows_open_handle_refuses_activation_before_moving_active(self):
        kernel32, handle = self._open_exclusive_windows_handle(self.active)
        marker = Path(str(self.active) + ".forgeos-activation.json")
        try:
            with self.assertRaises((OSError, sqlite3.DatabaseError)):
                Store.activate_backup_to(
                    self.backup, self.active, self.rollback, confirm=True)
        finally:
            self.assertTrue(kernel32.CloseHandle(handle))
        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertFalse(self.rollback.exists())
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows share-mode contract")
    def test_windows_open_handle_refuses_rollback_before_moving_it(self):
        self._leave_interrupted_activation()
        marker = Path(str(self.active) + ".forgeos-activation.json")
        kernel32, handle = self._open_exclusive_windows_handle(self.rollback)
        try:
            with self.assertRaises((OSError, ValueError, sqlite3.DatabaseError)):
                Store.rollback_activation(self.active, confirm=True)
        finally:
            self.assertTrue(kernel32.CloseHandle(handle))
        self.assertFalse(self.active.exists())
        self.assertTrue(self.rollback.exists())
        self.assertTrue(marker.exists())

        result = Store.rollback_activation(self.active, confirm=True)
        self.assertEqual(result, self.active)
        self.assertEqual(self._descriptions(self.active), ["old active task"])
        self.assertFalse(self.rollback.exists())
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
