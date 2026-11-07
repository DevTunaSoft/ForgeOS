import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

from forgeos.workspace import (
    WorkspaceError, create_workspace, release_workspace, workspace_diff,
)


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
