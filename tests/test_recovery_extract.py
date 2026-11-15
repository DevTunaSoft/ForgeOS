"""Recovery bundle materialization regressions."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.core import JOURNAL_HANDOFF_MANIFEST_FORMAT
from forgeos.diagnostics import EvidenceStore
from forgeos.recovery_extract import (
    RECOVERY_EXTRACTION_STATE_NAME,
    check_recovery_bundle_extraction,
    extract_recovery_bundle,
)
from forgeos.recovery_package import export_recovery_bundle
from forgeos.worktree_archive import export_worktree_archive


class RecoveryBundleExtractionTests(unittest.TestCase):
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

        evidence_root = self.root / "evidence"
        evidence_root.mkdir()
        reference = EvidenceStore(evidence_root).put("test output\n")
        handoff = self.root / "task.jsonl"
        rows = [
            {"record": "header", "format": "forgeos-journal-handoff-v1",
             "task_id": 1},
            {"record": "task", "task_id": 1, "status": "passed",
             "attempts": 1, "created": 1.0, "updated": 2.0},
            {"record": "event", "id": 1, "task_id": 1,
             "event_type": "test", "status": "passed", "actor": "engine",
             "created": 2.0, "details": "", "action": "test", "attempt": 1,
             "artifact_ref": "", "evidence_ref": reference},
            {"record": "summary", "task_id": 1, "event_count": 1,
             "last_event_id": 1, "complete": True},
        ]
        handoff.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n"
                    for row in rows), encoding="utf-8")
        manifest = self.root / "task.json"
        raw = handoff.read_bytes()
        manifest.write_text(json.dumps({
            "format": JOURNAL_HANDOFF_MANIFEST_FORMAT,
            "filename": handoff.name,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }, separators=(",", ":")) + "\n", encoding="utf-8")
        self.bundle = self.root / "recovery.zip"
        export_recovery_bundle(
            handoff, archive, self.bundle,
            manifest=manifest, evidence_directory=evidence_root)

    def test_extracts_and_checks_a_complete_new_directory(self):
        destination = self.root / "restored"
        self.assertEqual(extract_recovery_bundle(self.bundle, destination), destination)
        self.assertTrue((destination / RECOVERY_EXTRACTION_STATE_NAME).is_file())
        self.assertTrue((destination / "handoff" / "task.jsonl").is_file())
        self.assertTrue((destination / "source" / "source.zip").is_file())
        report = check_recovery_bundle_extraction(destination)
        self.assertTrue(report["valid"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["phase"], "complete")
        self.assertEqual(list(self.root.glob(".forgeos-recovery-extract-*")), [])

    def test_existing_destination_is_never_overwritten(self):
        destination = self.root / "restored"
        destination.mkdir()
        marker = destination / "operator-note.txt"
        marker.write_text("keep\n")
        with self.assertRaisesRegex(ValueError, "new directory"):
            extract_recovery_bundle(self.bundle, destination)
        self.assertEqual(marker.read_text(), "keep\n")

    def test_checker_detects_tampered_materialized_handoff(self):
        destination = self.root / "restored"
        extract_recovery_bundle(self.bundle, destination)
        handoff = destination / "handoff" / "task.jsonl"
        handoff.write_text(handoff.read_text() + "\n")
        report = check_recovery_bundle_extraction(destination)
        self.assertFalse(report["valid"])

    def test_failed_publication_leaves_a_failed_marker(self):
        destination = self.root / "restored"
        with patch("forgeos.recovery_extract._move_database_copy",
                   side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                extract_recovery_bundle(self.bundle, destination)
        self.assertTrue(destination.is_dir())
        state = json.loads(
            (destination / RECOVERY_EXTRACTION_STATE_NAME).read_text())
        self.assertEqual(state["phase"], "failed")
        report = check_recovery_bundle_extraction(destination)
        self.assertFalse(report["valid"])
        self.assertEqual(list(self.root.glob(".forgeos-recovery-extract-*")), [])


if __name__ == "__main__":
    unittest.main()
