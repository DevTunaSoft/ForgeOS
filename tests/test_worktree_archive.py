"""Bounded source-only worktree archive regressions."""
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from forgeos.worktree_archive import (
    WORKTREE_ARCHIVE_MANIFEST,
    check_worktree_archive,
    export_worktree_archive,
)


class WorktreeArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        (self.worktree / ".git").mkdir()
        (self.worktree / "src").mkdir()
        (self.worktree / "src" / "app.py").write_text(
            "def run():\n    return True\n")
        (self.worktree / "README.md").write_text("safe source\n")
        (self.worktree / ".env").write_text("PASSWORD=private\n")
        (self.worktree / ".forgeos").mkdir()
        (self.worktree / ".forgeos" / "pending-edit").write_text("secret")
        self.archive = self.root / "worktree.zip"

    def test_export_is_source_only_and_verifiable(self):
        self.assertEqual(
            export_worktree_archive(self.worktree, self.archive), self.archive)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"README.md", "src/app.py", WORKTREE_ARCHIVE_MANIFEST})
            manifest = archive.read(WORKTREE_ARCHIVE_MANIFEST).decode("utf-8")
            rows = [json.loads(line) for line in manifest.splitlines()]
        self.assertEqual(rows[0]["format"], "forgeos-worktree-manifest-v1")
        self.assertNotIn("PASSWORD", manifest)
        self.assertNotIn("pending-edit", manifest)
        report = check_worktree_archive(self.archive)
        self.assertTrue(report["valid"])
        self.assertEqual(report["archive_entries"], 2)
        self.assertEqual(report["files_checked"], 2)

    def test_checker_detects_changed_content_with_original_manifest(self):
        export_worktree_archive(self.worktree, self.archive)
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(self.archive, "r") as source, zipfile.ZipFile(
                tampered, "w", compression=zipfile.ZIP_DEFLATED,
                allowZip64=False) as target:
            for info in source.infolist():
                content = source.read(info)
                if info.filename == "src/app.py":
                    content = b"tampered\n"
                target_info = zipfile.ZipInfo(info.filename)
                target_info.create_system = 3
                target_info.external_attr = 0o600 << 16
                target.writestr(target_info, content)
        report = check_worktree_archive(tampered)
        self.assertFalse(report["valid"])
        problems = " ".join(issue["problem"] for issue in report["issues"])
        self.assertIn("does not match", problems)

    def test_checker_rejects_traversal_and_symlink_entries(self):
        unsafe = self.root / "unsafe.zip"
        link = zipfile.ZipInfo("link.py")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(
                unsafe, "w", compression=zipfile.ZIP_DEFLATED,
                allowZip64=False) as archive:
            archive.writestr("../escape.py", b"no")
            archive.writestr(link, b"target")
        report = check_worktree_archive(unsafe)
        self.assertFalse(report["valid"])
        problems = " ".join(issue["problem"] for issue in report["issues"])
        self.assertIn("path", problems)
        self.assertIn("unsafe", problems)

    def test_publication_failure_cleans_staging_and_output(self):
        with patch("forgeos.worktree_archive._publish_database_copy",
                   side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                export_worktree_archive(self.worktree, self.archive)
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.root.glob(".forgeos-worktree-archive-*")), [])

    def test_publication_failure_preserves_substituted_staging(self):
        def substitute(staging, target):
            staging.unlink()
            staging.write_bytes(b"operator-owned archive staging")
            raise OSError("disk unavailable")

        with patch("forgeos.worktree_archive._publish_database_copy",
                   side_effect=substitute):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                export_worktree_archive(self.worktree, self.archive)
        self.assertFalse(self.archive.exists())
        staging = list(self.root.glob(".forgeos-worktree-archive-*"))
        self.assertEqual(len(staging), 1)
        self.assertEqual(staging[0].read_bytes(), b"operator-owned archive staging")

    def test_reserved_manifest_name_and_archive_size_are_refused(self):
        (self.worktree / WORKTREE_ARCHIVE_MANIFEST).write_text("source")
        with self.assertRaisesRegex(ValueError, "reserved archive name"):
            export_worktree_archive(self.worktree, self.archive)
        self.assertFalse(self.archive.exists())

        (self.worktree / WORKTREE_ARCHIVE_MANIFEST).unlink()
        (self.worktree / WORKTREE_ARCHIVE_MANIFEST.upper()).write_text("source")
        with self.assertRaisesRegex(ValueError, "reserved archive name"):
            export_worktree_archive(self.worktree, self.archive)
        self.assertFalse(self.archive.exists())
        (self.worktree / WORKTREE_ARCHIVE_MANIFEST.upper()).unlink()

        with patch("forgeos.worktree_archive.MAX_WORKTREE_ARCHIVE_BYTES", 1):
            with self.assertRaisesRegex(RuntimeError, "size limit"):
                export_worktree_archive(self.worktree, self.archive)
        self.assertFalse(self.archive.exists())


if __name__ == "__main__":
    unittest.main()
