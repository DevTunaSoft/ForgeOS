"""Bounded worktree integrity-manifest regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.worktree_manifest import (
    check_worktree_manifest,
    export_worktree_manifest,
)


class WorktreeManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        (self.worktree / ".git").mkdir()
        (self.worktree / ".git" / "config").write_text("private git metadata")
        (self.worktree / "src").mkdir()
        (self.worktree / "src" / "app.py").write_text(
            "def run():\n    return True\n")
        (self.worktree / "README.md").write_text("safe source\n")
        (self.worktree / ".env").write_text("PASSWORD=private\n")
        (self.worktree / ".forgeos").mkdir()
        (self.worktree / ".forgeos" / "pending-edit").write_text("secret")
        self.manifest = self.root / "worktree.jsonl"

    def test_export_is_bounded_content_free_and_verifiable(self):
        self.assertEqual(
            export_worktree_manifest(self.worktree, self.manifest),
            self.manifest)
        rows = [json.loads(line) for line in self.manifest.read_text().splitlines()]
        self.assertEqual(rows[0], {
            "record": "header", "format": "forgeos-worktree-manifest-v1"})
        self.assertEqual(
            {row["path"] for row in rows if row["record"] == "file"},
            {"README.md", "src/app.py"})
        content = self.manifest.read_text()
        self.assertNotIn("private git metadata", content)
        self.assertNotIn("PASSWORD", content)
        self.assertNotIn("pending-edit", content)
        report = check_worktree_manifest(self.manifest, self.worktree)
        self.assertTrue(report["valid"])
        self.assertEqual(report["files_checked"], 2)
        self.assertEqual(report["manifest_files"], 2)

    def test_checker_detects_changed_missing_and_extra_files(self):
        export_worktree_manifest(self.worktree, self.manifest)
        (self.worktree / "src" / "app.py").write_text("changed\n")
        (self.worktree / "README.md").unlink()
        (self.worktree / "src" / "extra.py").write_text("extra\n")
        report = check_worktree_manifest(self.manifest, self.worktree)
        self.assertFalse(report["valid"])
        problems = " ".join(issue["problem"] for issue in report["issues"])
        self.assertIn("does not match", problems)
        self.assertIn("unlisted", problems)
        self.assertIn("missing", problems)

    def test_symlinked_source_is_rejected_before_publication(self):
        outside = self.root / "outside.py"
        outside.write_text("private\n")
        link = self.worktree / "src" / "link.py"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "symlink|reparse"):
            export_worktree_manifest(self.worktree, self.manifest)
        self.assertFalse(self.manifest.exists())

    def test_oversized_file_and_failed_publication_leave_no_output(self):
        large = self.worktree / "large.bin"
        large.write_bytes(b"12")
        with patch("forgeos.worktree_manifest.MAX_WORKTREE_FILE_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "size limit"):
                export_worktree_manifest(self.worktree, self.manifest)
        self.assertFalse(self.manifest.exists())

        failed = self.root / "failed.jsonl"
        with patch("forgeos.worktree_manifest._publish_database_copy",
                   side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                export_worktree_manifest(self.worktree, failed)
        self.assertFalse(failed.exists())
        self.assertEqual(list(self.root.glob(".forgeos-worktree-*")), [])

    def test_destination_inside_worktree_is_refused(self):
        with self.assertRaisesRegex(ValueError, "outside the worktree"):
            export_worktree_manifest(
                self.worktree, self.worktree / "manifest.jsonl")


if __name__ == "__main__":
    unittest.main()
