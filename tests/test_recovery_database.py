"""Database restoration and explicit activation from validated bundles."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import forgeos.core as core
from forgeos.core import Store
from forgeos.recovery_auth import export_recovery_bundle_auth
from forgeos.recovery_database import (
    activate_recovery_bundle_database_to,
    restore_recovery_bundle_database_to,
)
from forgeos.recovery_package import (
    RECOVERY_BUNDLE_DATABASE_PREFIX,
    export_recovery_bundle,
)
from forgeos.worktree_archive import export_worktree_archive


class RecoveryBundleDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        (self.worktree / ".git").mkdir()
        (self.worktree / "app.py").write_text("print('safe')\n")
        self.archive = self.root / "source.zip"
        export_worktree_archive(self.worktree, self.archive)

        self.handoff = self.root / "task.jsonl"
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
        self.handoff.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n"
                    for row in rows), encoding="utf-8")

        source_database = self.root / "source.sqlite3"
        self.database = self.root / "tasks-backup.sqlite3"
        self.database_manifest = self.root / "tasks-backup.json"
        store = Store(source_database)
        try:
            store.add(str(self.worktree), "restored database task")
            store.backup_to(self.database, manifest=self.database_manifest)
        finally:
            store.close()

        self.bundle = self.root / "recovery.zip"
        export_recovery_bundle(
            self.handoff, self.archive, self.bundle,
            database=self.database, database_manifest=self.database_manifest)
        self.key = self.root / "recovery.key"
        self.key.write_bytes(b"k" * 64)
        if os.name != "nt":
            self.key.chmod(0o600)
        self.authentication = self.root / "recovery.auth.json"
        export_recovery_bundle_auth(
            self.bundle, self.key, self.authentication)

    def test_restores_bundled_database_to_a_new_copy(self):
        destination = self.root / "restored.sqlite3"
        self.assertEqual(
            restore_recovery_bundle_database_to(self.bundle, destination),
            destination)
        Store.inspect_existing_database(destination)
        with closing(sqlite3.connect(
                destination.as_uri() + "?mode=ro&immutable=1", uri=True)) as database:
            descriptions = [row[0] for row in database.execute(
                "SELECT description FROM tasks ORDER BY id")]
        self.assertEqual(descriptions, ["restored database task"])
        self.assertFalse(Path(str(destination) + "-wal").exists())
        self.assertFalse(Path(str(destination) + "-shm").exists())

    def _descriptions(self, path):
        with closing(sqlite3.connect(
                path.as_uri() + "?mode=ro&immutable=1", uri=True)) as database:
            return [row[0] for row in database.execute(
                "SELECT description FROM tasks ORDER BY id")]

    def _create_active_database(self):
        source = self.root / "active-source.sqlite3"
        active = self.root / "active.sqlite3"
        store = Store(source)
        try:
            store.add(str(self.worktree), "old active task")
            store.backup_to(active)
        finally:
            store.close()
        return active

    def test_activates_bundled_database_with_explicit_rollback(self):
        active = self._create_active_database()
        rollback = self.root / "rollback.sqlite3"
        self.assertEqual(
            activate_recovery_bundle_database_to(
                self.bundle, active, rollback,
                authentication=self.authentication, key=self.key, confirm=True),
            active)
        self.assertEqual(self._descriptions(active), ["restored database task"])
        self.assertEqual(self._descriptions(rollback), ["old active task"])
        self.assertFalse(Path(str(active) + ".forgeos-activation.json").exists())

    def test_activation_requires_confirmation_without_side_effects(self):
        active = self.root / "active.sqlite3"
        rollback = self.root / "rollback.sqlite3"
        with self.assertRaisesRegex(ValueError, "confirmation"):
            activate_recovery_bundle_database_to(
                self.bundle, active, rollback, confirm=False)
        self.assertFalse(active.exists())
        self.assertFalse(rollback.exists())

    def test_activation_authentication_rejects_before_publication(self):
        wrong_key = self.root / "wrong.key"
        wrong_key.write_bytes(b"w" * 64)
        if os.name != "nt":
            wrong_key.chmod(0o600)
        active = self.root / "active.sqlite3"
        rollback = self.root / "rollback.sqlite3"
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            activate_recovery_bundle_database_to(
                self.bundle, active, rollback,
                authentication=self.authentication, key=wrong_key, confirm=True)
        self.assertFalse(active.exists())
        self.assertFalse(rollback.exists())

    def test_failed_bundle_activation_preserves_marker_and_cleans_staging(self):
        active = self._create_active_database()
        rollback = self.root / "rollback.sqlite3"
        real_move = core._move_database_copy

        def fail_active_moves(source, target):
            if target == active:
                raise OSError("active path unavailable")
            return real_move(source, target)

        with patch("forgeos.core._move_database_copy",
                   side_effect=fail_active_moves):
            with self.assertRaisesRegex(OSError, "active path unavailable"):
                activate_recovery_bundle_database_to(
                    self.bundle, active, rollback,
                    authentication=self.authentication, key=self.key,
                    confirm=True)

        marker = Path(str(active) + ".forgeos-activation.json")
        self.assertFalse(active.exists())
        self.assertEqual(self._descriptions(rollback), ["old active task"])
        self.assertTrue(marker.is_file())
        status = Store.activation_status(active)
        self.assertEqual(status["phase"], "publishing")
        self.assertFalse(status["staged_exists"])
        self.assertFalse(list(self.root.glob(".forgeos-recovery-activate-*")))

        self.assertEqual(Store.rollback_activation(active, confirm=True), active)
        self.assertEqual(self._descriptions(active), ["old active task"])
        self.assertFalse(rollback.exists())

    def test_authentication_gate_rejects_wrong_key_before_copy(self):
        wrong_key = self.root / "wrong.key"
        wrong_key.write_bytes(b"w" * 64)
        if os.name != "nt":
            wrong_key.chmod(0o600)
        destination = self.root / "restored.sqlite3"
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            restore_recovery_bundle_database_to(
                self.bundle, destination,
                authentication=self.authentication, key=wrong_key)
        self.assertFalse(destination.exists())

    def test_existing_destination_is_never_overwritten(self):
        destination = self.root / "restored.sqlite3"
        original = b"operator-owned database placeholder"
        destination.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "new file"):
            restore_recovery_bundle_database_to(self.bundle, destination)
        self.assertEqual(destination.read_bytes(), original)

    def test_bundle_without_database_is_rejected(self):
        bundle = self.root / "without-database.zip"
        export_recovery_bundle(self.handoff, self.archive, bundle)
        destination = self.root / "restored.sqlite3"
        with self.assertRaisesRegex(ValueError, "does not contain"):
            restore_recovery_bundle_database_to(bundle, destination)
        self.assertFalse(destination.exists())

    def test_tampered_database_entry_is_rejected(self):
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(self.bundle) as source, zipfile.ZipFile(
                tampered, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=6, allowZip64=False) as output:
            for info in source.infolist():
                payload = source.read(info)
                if info.filename == (
                        f"{RECOVERY_BUNDLE_DATABASE_PREFIX}{self.database.name}"):
                    payload = b"tampered database"
                output.writestr(info, payload)
        destination = self.root / "restored.sqlite3"
        with self.assertRaisesRegex(ValueError, "invalid"):
            restore_recovery_bundle_database_to(tampered, destination)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
