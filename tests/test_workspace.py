import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from forgeos.workspace import (
    DEFAULT_DIFF_PATCH_BYTES,
    WorkspaceDiffSummary,
    WorkspaceError,
    _git_bounded,
    _github_desktop_git,
    create_workspace,
    find_git,
    release_workspace,
    workspace_diff,
    workspace_diff_summary,
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

    def test_diff_summary_empty_and_concise_without_modifying_source(self):
        ws = create_workspace(self.repo, 11, self.root / "w_summary")
        empty = workspace_diff_summary(ws)
        self.assertIsInstance(empty, WorkspaceDiffSummary)
        self.assertEqual(empty.text, "No tracked changes.")
        self.assertEqual(empty.files_changed, 0)
        self.assertEqual(empty.insertions, 0)
        self.assertEqual(empty.deletions, 0)
        self.assertFalse(empty.truncated)

        (ws.path / "app.txt").write_text("baseline\nadded line\n")
        summary = workspace_diff_summary(ws)
        self.assertFalse(summary.truncated)
        self.assertEqual(summary.files_changed, 1)
        self.assertEqual(summary.insertions, 1)
        self.assertEqual(summary.deletions, 0)
        self.assertIn("Files changed: 1 (+1/-0)", summary.text)
        self.assertIn("app.txt (+1/-0)", summary.text)
        self.assertIn("+added line", summary.text)
        self.assertNotIn("truncated", summary.text)

        # Full workspace_diff contract remains unchanged.
        full = workspace_diff(ws)
        self.assertIn("diff --git a/app.txt b/app.txt", full)
        self.assertIn("+added line", full)

        # Source checkout is untouched.
        self.assertEqual((self.repo / "app.txt").read_text(), "baseline\n")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_diff_summary_hard_char_bound_and_visible_truncation(self):
        ws = create_workspace(self.repo, 12, self.root / "w_bound")
        lines = [f"line-{i:04d}-" + ("x" * 60) for i in range(400)]
        (ws.path / "app.txt").write_text("\n".join(lines) + "\n")

        summary = workspace_diff_summary(ws, max_chars=400)
        self.assertTrue(summary.truncated)
        self.assertLessEqual(len(summary.text), 400)
        self.assertIn("truncated", summary.text)
        self.assertIn("max_chars=400", summary.text)

        # Very small bounds still respect max_chars and mark truncation.
        for tiny in (40, 12, 5):
            with self.subTest(tiny=tiny):
                small = workspace_diff_summary(ws, max_chars=tiny)
                self.assertTrue(small.truncated)
                self.assertLessEqual(len(small.text), tiny)
                self.assertTrue(small.text)

        # Full diff remains complete and larger than the bounded patch cap.
        full = workspace_diff(ws)
        self.assertGreater(len(full), DEFAULT_DIFF_PATCH_BYTES)
        self.assertIn("line-0399-", full)
        self.assertEqual((self.repo / "app.txt").read_text(), "baseline\n")

    def test_diff_summary_max_files_limit_is_explicitly_marked(self):
        for name in ("b.txt", "c.txt"):
            (self.repo / name).write_text("one\n")
        self.git("add", "b.txt", "c.txt")
        self.git("commit", "-qm", "add extra files")

        ws = create_workspace(self.repo, 13, self.root / "w_files")
        (ws.path / "app.txt").write_text("changed a\n")
        (ws.path / "b.txt").write_text("changed b\n")
        (ws.path / "c.txt").write_text("changed c\n")

        summary = workspace_diff_summary(ws, max_chars=4000, max_files=2)
        self.assertTrue(summary.truncated)
        self.assertEqual(summary.files_changed, 3)
        self.assertEqual(summary.max_files, 2)
        self.assertLessEqual(len(summary.text), 4000)
        self.assertIn("files 2/3", summary.text)
        self.assertIn("max_files=2", summary.text)
        self.assertIn("truncated", summary.text)

    def test_diff_summary_rejects_invalid_limits(self):
        ws = create_workspace(self.repo, 14, self.root / "w_invalid")
        for bad in (0, -5, True, False, "100", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    workspace_diff_summary(ws, max_chars=bad)
                with self.assertRaises(ValueError):
                    workspace_diff_summary(ws, max_files=bad)

    def test_diff_summary_patch_bytes_marker_distinct_from_max_chars(self):
        ws = create_workspace(self.repo, 15, self.root / "w_patch_marker")
        lines = [f"line-{i:04d}-" + ("x" * 60) for i in range(400)]
        (ws.path / "app.txt").write_text("\n".join(lines) + "\n")

        summary = workspace_diff_summary(ws, max_chars=50000)
        self.assertTrue(summary.truncated)
        self.assertLessEqual(len(summary.text), 50000)
        self.assertIn(f"patch_bytes={DEFAULT_DIFF_PATCH_BYTES}", summary.text)
        self.assertNotIn("max_chars=50000", summary.text)

    def test_diff_summary_numstat_byte_cap_truncation(self):
        for i in range(6):
            (self.repo / f"file_{i:02d}_long_name_for_numstat.txt").write_text("one\n")
        self.git("add", ".")
        self.git("commit", "-qm", "add files for numstat cap")

        ws = create_workspace(self.repo, 16, self.root / "w_numstat_cap")
        for i in range(6):
            (ws.path / f"file_{i:02d}_long_name_for_numstat.txt").write_text("two\n")

        with patch("forgeos.workspace.DEFAULT_DIFF_NUMSTAT_BYTES", 64):
            summary = workspace_diff_summary(ws, max_chars=4000, max_files=20)
        self.assertTrue(summary.truncated)
        self.assertLessEqual(len(summary.text), 4000)
        self.assertIn("Files changed:", summary.text)
        self.assertIn("+", summary.text.splitlines()[0])
        self.assertIn("truncated", summary.text)

    def test_git_bounded_stderr_flood_drains_without_deadlock_and_bounds_tail(self):
        with patch("forgeos.workspace.find_git", return_value=sys.executable):
            text, truncated = _git_bounded(
                self.repo,
                "-c",
                "import sys; sys.stderr.write('E' * 200000); sys.stderr.flush(); sys.stdout.write('ok\\n')",
                max_bytes=1024,
                timeout=5,
            )
            self.assertEqual(text, "ok")
            self.assertFalse(truncated)

            with self.assertRaises(WorkspaceError) as ctx:
                _git_bounded(
                    self.repo,
                    "-c",
                    "import sys; sys.stderr.write('A' * 200000 + 'TAIL_MARKER'); sys.stderr.flush(); sys.exit(2)",
                    max_bytes=1024,
                    timeout=5,
                )
        msg = str(ctx.exception)
        self.assertIn("Git operation failed (2)", msg)
        self.assertIn("TAIL_MARKER", msg)
        self.assertLess(len(msg), 2500)

    def test_git_bounded_timeout_nonzero_and_race_safe_exit(self):
        with patch("forgeos.workspace.find_git", return_value=sys.executable):
            with self.assertRaises(WorkspaceError) as timeout_ctx:
                _git_bounded(
                    self.repo,
                    "-c",
                    "import time; time.sleep(5)",
                    max_bytes=1024,
                    timeout=0.1,
                )
            self.assertIn("timed out", str(timeout_ctx.exception))

            with self.assertRaises(WorkspaceError) as err_ctx:
                _git_bounded(
                    self.repo,
                    "-c",
                    "import sys; sys.stderr.write('boom'); sys.exit(3)",
                    max_bytes=1024,
                    timeout=5,
                )
            self.assertIn("Git operation failed (3): boom", str(err_ctx.exception))

            real_popen = subprocess.Popen
            real_timer = __import__("threading").Timer
            captured_callbacks = []

            def capturing_timer(interval, function, *t_args, **t_kwargs):
                t = real_timer(interval, function, *t_args, **t_kwargs)
                captured_callbacks.append(function)
                return t

            def delayed_wait_popen(*p_args, **p_kwargs):
                proc = real_popen(*p_args, **p_kwargs)
                orig_wait = proc.wait

                def post_exit_wait(*w_args, **w_kwargs):
                    rc = orig_wait(*w_args, **w_kwargs)
                    # Invoke the timeout callback after proc exited but before wait() returns.
                    if captured_callbacks:
                        captured_callbacks[-1]()
                    return rc

                proc.wait = post_exit_wait
                return proc

            with (
                patch("forgeos.workspace.threading.Timer", side_effect=capturing_timer),
                patch("forgeos.workspace.subprocess.Popen", side_effect=delayed_wait_popen),
            ):
                text, truncated = _git_bounded(
                    self.repo,
                    "-c",
                    "import sys; sys.stdout.write('clean\\n')",
                    max_bytes=1024,
                    timeout=5,
                )
            self.assertEqual(text, "clean")
            self.assertFalse(truncated)
