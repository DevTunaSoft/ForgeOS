import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from forgeos.workspace import (
    WorkspaceError, _git, _github_desktop_git, create_workspace, find_git,
    release_workspace, workspace_diff,
)


class GitDiscoveryTests(unittest.TestCase):
    def test_github_desktop_versioned_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "GitHubDesktop"
            old = root / "app-3.6.9" / "resources" / "app" / "git" / "cmd" / "git.exe"
            new = root / "app-3.6.10" / "resources" / "app" / "git" / "cmd" / "git.exe"
            for executable in (old, new):
                executable.parent.mkdir(parents=True)
                executable.write_bytes(b"test")
            old.touch()
            new.touch()
            import os
            os.utime(old, (100, 100))
            os.utime(new, (200, 200))
            self.assertEqual(_github_desktop_git(tmp), str(new))

    def test_no_desktop_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_github_desktop_git(tmp))

    def test_git_on_path_preferred(self):
        with patch("forgeos.workspace.shutil.which", return_value="/usr/bin/git"):
            self.assertEqual(find_git(), "/usr/bin/git")


@unittest.skipUnless(shutil.which("git"), "Git required")
class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "ForgeOS Test")
        self.git("config", "user.email", "forgeos@example.invalid")
        (self.repo / "app.txt").write_text("baseline\n")
        self.git("add", "app.txt")
        self.git("commit", "-qm", "initial")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_isolation_and_cleanup(self):
        ws = create_workspace(self.repo, 7, self.root / "workspace")
        self.assertEqual((ws.path / "app.txt").read_text(), "baseline\n")
        (ws.path / "app.txt").write_text("changed\n")
        self.assertEqual((self.repo / "app.txt").read_text(), "baseline\n")
        self.assertIn("changed", workspace_diff(ws))
        with self.assertRaises(WorkspaceError):
            release_workspace(ws)
        (ws.path / "app.txt").write_text("baseline\n")
        release_workspace(ws)
        self.assertFalse(ws.path.exists())

    def test_diff_preview_is_bounded_and_marks_omitted_patch(self):
        ws = create_workspace(self.repo, 8, self.root / "large-diff")
        (ws.path / "app.txt").write_text("changed-" * 2_000 + "\n")
        preview = workspace_diff(ws, max_bytes=1024)
        self.assertIn("diff --git", preview)
        self.assertIn("diff preview truncated", preview)
        self.assertLessEqual(len(preview.encode("utf-8")), 1024)
        self.assertNotEqual((ws.source / "app.txt").read_text(),
                            (ws.path / "app.txt").read_text())
        with self.assertRaisesRegex(WorkspaceError, "safety limit"):
            _git(ws.path, "diff", "--", max_output_bytes=1024)

    def test_diff_preview_limit_is_validated(self):
        ws = create_workspace(self.repo, 9, self.root / "bad-diff-limit")
        for limit in (0, 100, 1024 * 1024 + 1, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                workspace_diff(ws, max_bytes=limit)

    def test_reject_duplicate_branch(self):
        create_workspace(self.repo, 1, self.root / "a")
        with self.assertRaises(ValueError):
            create_workspace(self.repo, 1, self.root / "b")

    def test_reject_nested_and_parent_paths(self):
        with self.assertRaises(ValueError):
            create_workspace(self.repo, 1, self.repo / "nested")
        with self.assertRaises(ValueError):
            create_workspace(self.repo, 1, self.root)

    def test_reject_existing_destination(self):
        dest = self.root / "existing"
        dest.mkdir()
        (dest / "important").write_text("keep")
        with self.assertRaises(ValueError):
            create_workspace(self.repo, 1, dest)
        self.assertEqual((dest / "important").read_text(), "keep")

    def test_reject_bad_ids(self):
        for bad in (0, -1, True, "1", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                create_workspace(self.repo, bad, self.root / "workspace")

    def test_untracked_files_block_cleanup(self):
        ws = create_workspace(self.repo, 3, self.root / "w")
        (ws.path / "new.txt").write_text("important")
        with self.assertRaises(WorkspaceError):
            release_workspace(ws)
        self.assertTrue((ws.path / "new.txt").exists())

    def test_reject_non_repository(self):
        with self.assertRaises(WorkspaceError):
            create_workspace(self.root, 1, self.root / "w")
