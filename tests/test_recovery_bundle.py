"""Bounded recovery bundle-index regressions."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.core import JOURNAL_HANDOFF_MANIFEST_FORMAT, Store
from forgeos.diagnostics import EvidenceStore
from forgeos.recovery_bundle import (
    check_recovery_bundle_index,
    export_recovery_bundle_index,
)
from forgeos.worktree_archive import export_worktree_archive


class RecoveryBundleIndexTests(unittest.TestCase):
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

        self.evidence_root = self.root / "evidence"
        self.evidence_root.mkdir()
        self.reference = EvidenceStore(self.evidence_root).put("test output\n")
        self.handoff = self.root / "task.jsonl"
        self._write_handoff()
        self.manifest = self.root / "task.json"
        self._write_manifest()
        self.index = self.root / "bundle-index.jsonl"

    def _write_handoff(self):
        rows = [
            {"record": "header", "format": "forgeos-journal-handoff-v1",
             "task_id": 1},
            {"record": "task", "task_id": 1, "status": "passed",
             "attempts": 1, "created": 1.0, "updated": 2.0},
            {"record": "event", "id": 1, "task_id": 1,
             "event_type": "test", "status": "passed", "actor": "engine",
             "created": 2.0, "details": "", "action": "test", "attempt": 1,
             "artifact_ref": "", "evidence_ref": self.reference},
            {"record": "summary", "task_id": 1, "event_count": 1,
             "last_event_id": 1, "complete": True},
        ]
        self.handoff.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n"
                    for row in rows), encoding="utf-8")

    def _write_manifest(self):
        raw = self.handoff.read_bytes()
        self.manifest.write_text(json.dumps({
            "format": JOURNAL_HANDOFF_MANIFEST_FORMAT,
            "filename": self.handoff.name,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }, separators=(",", ":")) + "\n", encoding="utf-8")

    def test_index_binds_handoff_archive_manifest_and_evidence(self):
        self.assertEqual(
            export_recovery_bundle_index(
                self.handoff, self.archive, self.index,
                manifest=self.manifest,
                evidence_directory=self.evidence_root),
            self.index)
        content = self.index.read_text(encoding="utf-8")
        self.assertNotIn("test output", content)
        rows = [json.loads(line) for line in content.splitlines()]
        self.assertEqual(rows[0]["format"], "forgeos-recovery-bundle-index-v1")
        self.assertEqual(rows[-1]["evidence_count"], 1)
        report = check_recovery_bundle_index(
            self.index, self.handoff, self.archive,
            manifest=self.manifest, evidence_directory=self.evidence_root)
        self.assertTrue(report["valid"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["evidence_files"], 1)
        self.assertEqual(report["artifacts_checked"], 4)

    def test_checker_detects_tampered_handoff_and_evidence(self):
        export_recovery_bundle_index(
            self.handoff, self.archive, self.index,
            manifest=self.manifest, evidence_directory=self.evidence_root)
        original = self.handoff.read_text(encoding="utf-8")
        self.handoff.write_text(original + "\n", encoding="utf-8")
        report = check_recovery_bundle_index(
            self.index, self.handoff, self.archive,
            manifest=self.manifest, evidence_directory=self.evidence_root)
        self.assertFalse(report["valid"])

        self.handoff.write_text(original, encoding="utf-8")
        evidence = self.evidence_root / (self.reference + ".txt.gz")
        evidence.write_bytes(b"tampered")
        report = check_recovery_bundle_index(
            self.index, self.handoff, self.archive,
            manifest=self.manifest, evidence_directory=self.evidence_root)
        self.assertFalse(report["valid"])

    def test_checker_rejects_index_missing_handoff_evidence(self):
        export_recovery_bundle_index(
            self.handoff, self.archive, self.index,
            manifest=self.manifest, evidence_directory=self.evidence_root)
        rows = [json.loads(line) for line in self.index.read_text().splitlines()]
        rows = [row for row in rows if row["record"] != "evidence"]
        rows[-1]["evidence_count"] = 0
        self.index.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n"
                    for row in rows), encoding="utf-8")
        report = check_recovery_bundle_index(
            self.index, self.handoff, self.archive,
            manifest=self.manifest, evidence_directory=self.evidence_root)
        self.assertFalse(report["valid"])
        problems = " ".join(issue["problem"] for issue in report["issues"])
        self.assertIn("evidence references", problems)

    def test_referenced_evidence_requires_a_directory(self):
        with self.assertRaisesRegex(ValueError, "Evidence directory"):
            export_recovery_bundle_index(self.handoff, self.archive, self.index)
        self.assertFalse(self.index.exists())

    def test_publication_failure_cleans_staging_and_output(self):
        with patch("forgeos.recovery_bundle._publish_database_copy",
                   side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                export_recovery_bundle_index(
                    self.handoff, self.archive, self.index,
                    manifest=self.manifest,
                    evidence_directory=self.evidence_root)
        self.assertFalse(self.index.exists())
        self.assertEqual(list(self.root.glob(".forgeos-recovery-index-*")), [])


if __name__ == "__main__":
    unittest.main()
