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

    def _link_or_skip(self, link: Path, target: Path, *, directory: bool) -> None:
        try:
            os.symlink(target, link, target_is_directory=directory)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links are unavailable on this host")

    def _junction_or_skip(self, link: Path, target: Path) -> None:
        made = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True)
        if made.returncode != 0 or not os.path.lexists(link):
            self.skipTest("Junction creation is unavailable on this host")
        # Remove the link object only. Registered after TemporaryDirectory
        # cleanup, so it runs first (LIFO), before any recursive removal.
        self.addCleanup(os.rmdir, link)

    def test_snapshot_refuses_file_symlink_before_copying(self):
        outside = self.root / "outside-secret.txt"
        outside.write_text("do-not-copy")
        self._link_or_skip(self.workspace / "linked.txt", outside, directory=False)
        with self.assertRaisesRegex(ValueError, "symlink or junction"):
            create_test_snapshot(self.workspace)
        self.assertFalse((self.root / ".forgeos-exec-task-7").exists())
        self.assertEqual(outside.read_text(), "do-not-copy")

    def test_snapshot_refuses_directory_symlink_to_outside_folder(self):
        outside = self.root / "outside-dir"
        outside.mkdir()
        (outside / "secret.txt").write_text("do-not-copy")
        self._link_or_skip(self.workspace / "linked-dir", outside, directory=True)
        with self.assertRaisesRegex(ValueError, "symlink or junction"):
            create_test_snapshot(self.workspace)
        self.assertFalse((self.root / ".forgeos-exec-task-7").exists())
        self.assertEqual((outside / "secret.txt").read_text(), "do-not-copy")

    @unittest.skipUnless(os.name == "nt", "Junctions are a Windows reparse point")
    def test_snapshot_refuses_junction_before_copying_its_target(self):
        outside = self.root / "outside-junction-target"
        outside.mkdir()
        (outside / "secret.txt").write_text("do-not-copy")
        self._junction_or_skip(self.workspace / "junction", outside)
        with self.assertRaisesRegex(ValueError, "symlink or junction"):
            create_test_snapshot(self.workspace)
        self.assertFalse((self.root / ".forgeos-exec-task-7").exists())
        self.assertEqual((outside / "secret.txt").read_text(), "do-not-copy")

    @unittest.skipUnless(os.name == "nt", "Junctions are a Windows reparse point")
    def test_snapshot_refuses_junction_as_workspace_root(self):
        alias = self.root / "task-junction"
        self._junction_or_skip(alias, self.workspace)
        with self.assertRaisesRegex(ValueError, "symlink or junction"):
            create_test_snapshot(alias)
        self.assertFalse((self.root / ".forgeos-exec-task-7").exists())

    def test_snapshot_refuses_symlinked_workspace_root(self):
        alias = self.root / "task-alias"
        self._link_or_skip(alias, self.workspace, directory=True)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            create_test_snapshot(alias)
        self.assertFalse((self.root / ".forgeos-exec-task-7").exists())

    def test_links_under_excluded_names_do_not_block_snapshot(self):
        outside = self.root / "outside.txt"
        outside.write_text("do-not-copy")
        self._link_or_skip(self.workspace / ".forgeos" / "linked", outside, directory=False)
        self._link_or_skip(self.workspace / ".ENV.linked", outside, directory=False)
        runtime = create_test_snapshot(self.workspace)
        self.addCleanup(remove_test_snapshot, self.workspace)
        self.assertFalse((runtime.path / ".forgeos").exists())
        self.assertFalse((runtime.path / ".ENV.linked").exists())
        self.assertEqual((runtime.path / "app.py").read_text(), "print('source')\n")
