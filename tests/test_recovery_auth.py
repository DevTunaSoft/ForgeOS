"""Detached recovery-package authentication regressions."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from forgeos.recovery_auth import (
    check_recovery_bundle_auth,
    export_recovery_bundle_auth,
    generate_recovery_bundle_auth_key,
)
from forgeos.recovery_package import export_recovery_bundle
from forgeos.worktree_archive import export_worktree_archive


class RecoveryBundleAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        worktree = self.root / "worktree"
        worktree.mkdir()
        (worktree / ".git").mkdir()
        (worktree / "app.py").write_text("print('safe')\n")
        archive = self.root / "source.zip"
        export_worktree_archive(worktree, archive)
        handoff = self.root / "task.jsonl"
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
        self.bundle = self.root / "recovery.zip"
        export_recovery_bundle(handoff, archive, self.bundle)
        self.key = self.root / "auth.key"
        self.key_bytes = bytes(range(64))
        self.key.write_bytes(self.key_bytes)
        self.authentication = self.root / "recovery.auth.json"

    def test_auth_record_verifies_without_copying_the_key(self):
        self.assertEqual(
            export_recovery_bundle_auth(
                self.bundle, self.key, self.authentication),
            self.authentication)
        self.assertNotIn(
            self.key_bytes.hex(), self.authentication.read_text(encoding="utf-8"))
        record = json.loads(self.authentication.read_text(encoding="utf-8"))
        self.assertEqual(record["format"], "forgeos-recovery-bundle-auth-v1")
        report = check_recovery_bundle_auth(
            self.bundle, self.authentication, self.key)
        self.assertTrue(report["valid"])
        self.assertTrue(report["complete"])

    def test_key_generation_is_bounded_private_and_non_overwriting(self):
        generated = self.root / "generated.key"
        self.assertEqual(
            generate_recovery_bundle_auth_key(generated, size=48), generated)
        key = generated.read_bytes()
        self.assertEqual(len(key), 48)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(generated.stat().st_mode), 0o600)
        with self.assertRaisesRegex(ValueError, "new file"):
            generate_recovery_bundle_auth_key(generated)
        with self.assertRaisesRegex(ValueError, "between 32 and 4096"):
            generate_recovery_bundle_auth_key(self.root / "short.key", size=31)
        with self.assertRaisesRegex(ValueError, "between 32 and 4096"):
            generate_recovery_bundle_auth_key(self.root / "large.key", size=4097)

    def test_key_generation_publication_failure_cleans_staging(self):
        destination = self.root / "generated.key"
        with patch("forgeos.recovery_auth._publish_database_copy",
                   side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                generate_recovery_bundle_auth_key(destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".forgeos-recovery-auth-key-*")), [])

    def test_wrong_key_and_tampered_record_fail_closed(self):
        export_recovery_bundle_auth(self.bundle, self.key, self.authentication)
        wrong_key = self.root / "wrong.key"
        wrong_key.write_bytes(b"wrong secret" * 4)
        report = check_recovery_bundle_auth(
            self.bundle, self.authentication, wrong_key)
        self.assertFalse(report["valid"])

        record = json.loads(self.authentication.read_text())
        record["package"]["hmac_sha256"] = "0" * 64
        self.authentication.write_text(json.dumps(record), encoding="utf-8")
        report = check_recovery_bundle_auth(
            self.bundle, self.authentication, self.key)
        self.assertFalse(report["valid"])

    def test_tampered_package_fails_even_with_original_authentication_record(self):
        export_recovery_bundle_auth(self.bundle, self.key, self.authentication)
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(self.bundle) as source, zipfile.ZipFile(
                tampered, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for entry in source.infolist():
                content = source.read(entry.filename)
                if entry.filename == "recovery-bundle-header.json":
                    content = content.replace(b"forgeos-recovery-bundle-v1",
                                              b"forgeos-recovery-bundle-x1")
                target.writestr(entry, content)
        report = check_recovery_bundle_auth(
            tampered, self.authentication, self.key)
        self.assertFalse(report["valid"])
        self.assertTrue(any("HMAC" in issue["problem"]
                            for issue in report["issues"]))

    def test_short_key_and_publication_failure_leave_no_auth_record(self):
        short_key = self.root / "short.key"
        short_key.write_bytes(b"short")
        with self.assertRaisesRegex(ValueError, "too short"):
            export_recovery_bundle_auth(self.bundle, short_key, self.authentication)
        with patch("forgeos.recovery_auth._publish_database_copy",
                   side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                export_recovery_bundle_auth(
                    self.bundle, self.key, self.authentication)
        self.assertFalse(self.authentication.exists())
        self.assertEqual(list(self.root.glob(".forgeos-recovery-auth-*")), [])


if __name__ == "__main__":
    unittest.main()
