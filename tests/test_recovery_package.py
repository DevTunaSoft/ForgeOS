"""Bounded recovery-package regressions."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from forgeos.core import JOURNAL_HANDOFF_MANIFEST_FORMAT
from forgeos.diagnostics import EvidenceStore
from forgeos.recovery_package import (
    RECOVERY_BUNDLE_EVIDENCE_PREFIX,
    RECOVERY_BUNDLE_FORMAT,
    RECOVERY_BUNDLE_HEADER_NAME,
    RECOVERY_BUNDLE_INDEX_NAME,
    check_recovery_bundle,
    export_recovery_bundle,
)
from forgeos.worktree_archive import export_worktree_archive


class RecoveryBundlePackageTests(unittest.TestCase):
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
        self.bundle = self.root / "recovery.zip"

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

    def _export(self):
        return export_recovery_bundle(
            self.handoff, self.archive, self.bundle,
            manifest=self.manifest, evidence_directory=self.evidence_root)

    def test_package_contains_only_indexed_artifacts_and_verifies(self):
        self.assertEqual(self._export(), self.bundle)
        self.assertEqual(list(self.root.glob(".forgeos-recovery-bundle-*")), [])
        with zipfile.ZipFile(self.bundle) as archive:
            names = set(archive.namelist())
            self.assertEqual(names, {
                RECOVERY_BUNDLE_HEADER_NAME,
                RECOVERY_BUNDLE_INDEX_NAME,
                "handoff/task.jsonl",
                "manifest/task.json",
                "source/source.zip",
                f"{RECOVERY_BUNDLE_EVIDENCE_PREFIX}{self.reference}.txt.gz",
            })
            header = json.loads(
                archive.read(RECOVERY_BUNDLE_HEADER_NAME).decode("utf-8"))
            self.assertEqual(header["format"], RECOVERY_BUNDLE_FORMAT)
            self.assertNotIn(
                "test output", archive.read(RECOVERY_BUNDLE_INDEX_NAME).decode())
        report = check_recovery_bundle(self.bundle)
        self.assertTrue(report["valid"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["format"], RECOVERY_BUNDLE_FORMAT)
        self.assertEqual(report["entries"], 6)

    def test_checker_rejects_tampered_nested_handoff(self):
        self._export()
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(self.bundle) as source, zipfile.ZipFile(
                tampered, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=6, allowZip64=False) as output:
            for info in source.infolist():
                payload = source.read(info)
                if info.filename == "handoff/task.jsonl":
                    payload += b"\n"
                output.writestr(info, payload)
        report = check_recovery_bundle(tampered)
        self.assertFalse(report["valid"])

    def test_checker_rejects_unindexed_evidence_entry(self):
        self._export()
        tampered = self.root / "extra-evidence.zip"
        with zipfile.ZipFile(self.bundle) as source, zipfile.ZipFile(
                tampered, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=6, allowZip64=False) as output:
            for info in source.infolist():
                output.writestr(info, source.read(info))
            output.writestr(
                "evidence/not-indexed.txt.gz", b"not indexed")
        report = check_recovery_bundle(tampered)
        self.assertFalse(report["valid"])
        self.assertIn(
            "evidence entries", " ".join(issue["problem"] for issue in report["issues"]))

    def test_publication_failure_cleans_staging_and_output(self):
        with patch("forgeos.recovery_package._publish_database_copy",
                   side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                self._export()
        self.assertFalse(self.bundle.exists())
        self.assertEqual(list(self.root.glob(".forgeos-recovery-bundle-*")), [])
        self.assertEqual(list(self.root.glob(".forgeos-bundle-index-*")), [])


if __name__ == "__main__":
    unittest.main()
