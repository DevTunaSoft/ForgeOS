"""Execution snapshot safety and lifecycle tests."""
from pathlib import Path
import json
import tempfile
import os
import subprocess
import unittest
from unittest.mock import patch

from forgeos import test_runtime
from forgeos.test_runtime import (
    MARKER, MAX_MARKER_BYTES, create_test_snapshot, remove_test_snapshot,
)


class TestRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "task-7"
        self.workspace.mkdir()
        (self.workspace / ".git").write_text("gitdir: example")
        (self.workspace / "app.py").write_text("print('source')\n")
        (self.workspace / ".env").write_text("SECRET=do-not-copy")
        (self.workspace / ".env.local").write_text("TOKEN=do-not-copy")
        internal = self.workspace / ".forgeos"
        internal.mkdir()
        (internal / "secret-journal").write_text("do-not-copy")

    def test_snapshot_excludes_case_variant_environment_files(self):
        (self.workspace / ".ENV.production").write_text("TOKEN=case-variant")
        (self.workspace / ".Env").write_text("SECRET=mixed-case")
        runtime = create_test_snapshot(self.workspace)
        self.addCleanup(remove_test_snapshot, self.workspace)
        self.assertFalse((runtime.path / ".ENV.production").exists())
        self.assertFalse((runtime.path / ".Env").exists())
        self.assertEqual((runtime.path / "app.py").read_text(), "print('source')\n")

    def test_snapshot_is_outside_workspace_and_excludes_internal_journal(self):
        runtime = create_test_snapshot(self.workspace)
        self.assertFalse(runtime.path.is_relative_to(self.workspace))
        self.assertEqual((runtime.path / "app.py").read_text(), "print('source')\n")
        self.assertFalse((runtime.path / ".forgeos").exists())
        self.assertFalse((runtime.path / ".env").exists())
        self.assertFalse((runtime.path / ".env.local").exists())
        self.assertEqual(runtime.env["FORGEOS_EXECUTION_SNAPSHOT"], "1")
        self.assertEqual(runtime.env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"], "1")
        self.assertTrue(Path(runtime.env["TEMP"]).is_relative_to(runtime.path))
        self.assertTrue(Path(runtime.env["GOCACHE"]).is_relative_to(runtime.path))
        self.assertTrue(remove_test_snapshot(self.workspace))
        self.assertFalse(runtime.path.exists())

    def test_snapshot_mutation_never_changes_editable_workspace(self):
        runtime = create_test_snapshot(self.workspace)
        (runtime.path / "app.py").write_text("mutated")
        (runtime.path / "new.txt").write_text("generated")
        self.assertEqual((self.workspace / "app.py").read_text(), "print('source')\n")
        self.assertFalse((self.workspace / "new.txt").exists())

    def test_fresh_snapshot_discards_prior_test_artifacts(self):
        first = create_test_snapshot(self.workspace)
        (first.path / "artifact.tmp").write_text("old")
        second = create_test_snapshot(self.workspace)
        self.assertEqual(first.path, second.path)
        self.assertFalse((second.path / "artifact.tmp").exists())
        self.assertEqual((second.path / "app.py").read_text(), "print('source')\n")

    def test_snapshot_environment_does_not_inherit_parent_secrets(self):
        key = "FORGEOS_PARENT_SECRET_TEST"
        previous = os.environ.get(key)
        os.environ[key] = "must-not-leak"
        try:
            runtime = create_test_snapshot(self.workspace)
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        self.assertNotIn(key, runtime.env)
        self.assertEqual(runtime.env["FORGEOS_EXECUTION_SNAPSHOT"], "1")
        self.assertEqual(runtime.env["PYTHONNOUSERSITE"], "1")
        self.assertTrue(Path(runtime.env["USERPROFILE"]).is_relative_to(runtime.path))

    def test_refuses_to_delete_unowned_existing_directory(self):
        runtime = self.root / ".forgeos-exec-task-7"
        runtime.mkdir()
        (runtime / "valuable.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "unowned"):
            create_test_snapshot(self.workspace)
        self.assertEqual((runtime / "valuable.txt").read_text(), "keep")

    def test_refuses_tampered_ownership_marker(self):
        runtime = create_test_snapshot(self.workspace)
        runtime.path.with_name(runtime.path.name + MARKER).write_text(json.dumps({
            "version": 1, "workspace": str(self.root / "other"),
        }))
        with self.assertRaisesRegex(ValueError, "ownership marker"):
            remove_test_snapshot(self.workspace)
        self.assertTrue(runtime.path.exists())
        self.assertTrue(
            runtime.path.with_name(runtime.path.name + MARKER).exists())

    def test_snapshot_cleanup_preserves_substituted_runtime(self):
        runtime = create_test_snapshot(self.workspace)
        marker = runtime.path.with_name(runtime.path.name + MARKER)
        retained = runtime.path.with_name(runtime.path.name + "-retained")
        real_validate = test_runtime._validate_marker

        def validate_then_substitute(runtime_path, workspace, *args, **kwargs):
            result = real_validate(runtime_path, workspace, *args, **kwargs)
            runtime_path.rename(retained)
            runtime_path.mkdir()
            (runtime_path / "operator-owned.txt").write_text("keep")
            return result

        with patch.object(
                test_runtime, "_validate_marker",
                side_effect=validate_then_substitute):
            with self.assertRaisesRegex(ValueError, "runtime directory changed"):
                remove_test_snapshot(self.workspace)
        self.assertEqual((retained / "app.py").read_text(), "print('source')\n")
        self.assertEqual(
            (runtime.path / "operator-owned.txt").read_text(), "keep")
        self.assertTrue(marker.exists())

    def test_snapshot_cleanup_preserves_substituted_marker(self):
        runtime = create_test_snapshot(self.workspace)
        marker = runtime.path.with_name(runtime.path.name + MARKER)
        real_validate = test_runtime._validate_marker

        def validate_then_substitute(runtime_path, workspace, *args, **kwargs):
            result = real_validate(runtime_path, workspace, *args, **kwargs)
            marker.unlink()
            marker.write_text("operator-owned-marker")
            return result

        with patch.object(
                test_runtime, "_validate_marker",
                side_effect=validate_then_substitute):
            with self.assertRaisesRegex(ValueError, "ownership marker changed"):
                remove_test_snapshot(self.workspace)
        self.assertTrue(runtime.path.exists())
        self.assertEqual(marker.read_text(), "operator-owned-marker")

    def test_snapshot_cleanup_bounds_ownership_marker_read(self):
        runtime = create_test_snapshot(self.workspace)
        marker = runtime.path.with_name(runtime.path.name + MARKER)
        marker.write_bytes(b"x" * (MAX_MARKER_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "oversized"):
            remove_test_snapshot(self.workspace)
        self.assertTrue(runtime.path.exists())
        self.assertTrue(marker.exists())

    def test_snapshot_creation_does_not_overwrite_appearing_marker(self):
        runtime = self.root / ".forgeos-exec-task-7"
        marker = runtime.with_name(runtime.name + MARKER)
        real_write = test_runtime._write_snapshot_marker

        def write_after_operator_race(marker_path, payload):
            marker_path.write_text("operator-owned-marker")
            return real_write(marker_path, payload)

        with patch.object(
                test_runtime, "_write_snapshot_marker",
                side_effect=write_after_operator_race):
            with self.assertRaisesRegex(ValueError, "appeared"):
                create_test_snapshot(self.workspace)
        self.assertTrue(runtime.exists())
        self.assertEqual(marker.read_text(), "operator-owned-marker")


class SnapshotLinkRefusalTests(unittest.TestCase):
    """A worktree link fails the snapshot. It is never copied or followed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "task-7"
        self.workspace.mkdir()
        (self.workspace / "app.py").write_text("print('source')\n")
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("outside-secret")
        self.runtime = self.root / ".forgeos-exec-task-7"
        self.marker = self.runtime.with_name(self.runtime.name + MARKER)

    def assert_refused_without_residue(self, pattern="link or junction"):
        with self.assertRaisesRegex(ValueError, pattern):
            create_test_snapshot(self.workspace)
        self.assertFalse(self.runtime.exists())
        self.assertFalse(self.marker.exists())
        self.assertEqual((self.outside / "secret.txt").read_text(), "outside-secret")
        self.assertEqual((self.workspace / "app.py").read_text(), "print('source')\n")

    def symlink_or_skip(self, target, link, *, directory):
        try:
            os.symlink(target, link, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symbolic links unavailable here: {exc}")

    def test_refuses_symlinked_directory_in_worktree(self):
        self.symlink_or_skip(self.outside, self.workspace / "linked-dir", directory=True)
        self.assert_refused_without_residue()

    def test_refuses_symlinked_file_in_worktree(self):
        self.symlink_or_skip(self.outside / "secret.txt",
                             self.workspace / "linked.txt", directory=False)
        self.assert_refused_without_residue()

    def test_refuses_link_nested_below_worktree_root(self):
        nested = self.workspace / "pkg" / "data"
        nested.mkdir(parents=True)
        self.symlink_or_skip(self.outside, nested / "linked-dir", directory=True)
        self.assert_refused_without_residue()

    def test_refuses_any_reparse_point_not_only_symlinks(self):
        # Simulates a junction on any platform: a real directory whose reparse
        # check reports true. Real junctions are covered by the Windows class.
        simulated = self.workspace / "simulated-junction"
        simulated.mkdir()
        identity = simulated.lstat().st_ino
        real_check = test_runtime._is_reparse_point

        def reparse_for_simulated(info):
            return getattr(info, "st_ino", None) == identity or real_check(info)

        with patch.object(test_runtime, "_is_reparse_point",
                          side_effect=reparse_for_simulated):
            self.assert_refused_without_residue()

    def test_refuses_symlinked_workspace_root_before_creating_anything(self):
        alias = self.root / "alias"
        self.symlink_or_skip(self.workspace, alias, directory=True)
        with self.assertRaisesRegex(ValueError, "symlink or junction"):
            create_test_snapshot(alias)
        self.assertFalse(self.runtime.exists())
        self.assertFalse(self.marker.exists())

    def test_excluded_metadata_links_do_not_block_snapshot(self):
        # Names under the .env rule are never copied, so a link there is harmless.
        self.symlink_or_skip(self.outside / "secret.txt",
                             self.workspace / ".env-link", directory=False)
        runtime = create_test_snapshot(self.workspace)
        self.addCleanup(remove_test_snapshot, self.workspace)
        self.assertFalse(os.path.lexists(runtime.path / ".env-link"))
        self.assertEqual((runtime.path / "app.py").read_text(), "print('source')\n")


@unittest.skipUnless(os.name == "nt", "NTFS junctions exist only on Windows")
class WindowsJunctionSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "task-7"
        self.workspace.mkdir()
        (self.workspace / "app.py").write_text("print('source')\n")
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("outside-secret")
        self.runtime = self.root / ".forgeos-exec-task-7"
        self.marker = self.runtime.with_name(self.runtime.name + MARKER)

    def make_link(self, flags, link, target):
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", *flags, str(link), str(target)],
            capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            self.skipTest(f"mklink {' '.join(flags)} unavailable: "
                          f"{result.stdout}{result.stderr}")

    def test_junction_in_worktree_is_refused_and_outside_content_survives(self):
        self.make_link(["/J"], self.workspace / "junction", self.outside)
        with self.assertRaisesRegex(ValueError, "link or junction"):
            create_test_snapshot(self.workspace)
        self.assertFalse(self.runtime.exists())
        self.assertFalse(self.marker.exists())
        self.assertEqual((self.outside / "secret.txt").read_text(), "outside-secret")

    def test_directory_symlink_in_worktree_is_refused(self):
        self.make_link(["/D"], self.workspace / "linked-dir", self.outside)
        with self.assertRaisesRegex(ValueError, "link or junction"):
            create_test_snapshot(self.workspace)
        self.assertFalse(self.runtime.exists())

    def test_junction_workspace_root_is_refused(self):
        alias = self.root / "alias"
        self.make_link(["/J"], alias, self.workspace)
        with self.assertRaisesRegex(ValueError, "symlink or junction"):
            create_test_snapshot(alias)
        self.assertFalse(self.runtime.exists())


class SecureRuntimeRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exec_root = self.root / "secure-runs"
        self.exec_root.mkdir()

    def _workspace(self, *parts):
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        (path / "app.py").write_text("print('source')\n")
        return path

    def test_snapshot_goes_under_runtime_root_not_beside_workspace(self):
        workspace = self._workspace("projects", "ForgeOS")
        runtime = create_test_snapshot(workspace, runtime_root=self.exec_root)
        self.addCleanup(remove_test_snapshot, workspace, runtime_root=self.exec_root)
        self.assertEqual(runtime.path.parent, self.exec_root)
        self.assertFalse((workspace.parent / ".forgeos-exec-ForgeOS").exists())
        self.assertEqual((runtime.path / "app.py").read_text(), "print('source')\n")

    def test_same_named_workspaces_get_distinct_runtimes(self):
        first = self._workspace("a", "ForgeOS")
        second = self._workspace("b", "ForgeOS")
        self.assertNotEqual(
            test_runtime._runtime_path(first, self.exec_root),
            test_runtime._runtime_path(second, self.exec_root))

    def test_remove_uses_the_same_runtime_root(self):
        workspace = self._workspace("projects", "ForgeOS")
        runtime = create_test_snapshot(workspace, runtime_root=self.exec_root)
        self.assertTrue(remove_test_snapshot(workspace, runtime_root=self.exec_root))
        self.assertFalse(runtime.path.exists())

    def test_missing_runtime_root_is_refused(self):
        workspace = self._workspace("projects", "ForgeOS")
        with self.assertRaises(ValueError):
            create_test_snapshot(workspace, runtime_root=self.root / "absent")
