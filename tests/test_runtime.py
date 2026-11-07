"""Execution snapshot safety and lifecycle tests."""
from pathlib import Path
import json
import tempfile
import unittest

from forgeos.test_runtime import (
    MARKER, create_test_snapshot, remove_test_snapshot,
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

    def test_snapshot_is_outside_workspace_and_excludes_internal_journal(self):
        runtime = create_test_snapshot(self.workspace)
        self.assertFalse(runtime.path.is_relative_to(self.workspace))
        self.assertEqual((runtime.path / "app.py").read_text(), "print('source')\n")
        self.assertFalse((runtime.path / ".forgeos").exists())
        self.assertFalse((runtime.path / ".env").exists())
        self.assertFalse((runtime.path / ".env.local").exists())
        self.assertEqual(runtime.env["FORGEOS_EXECUTION_SNAPSHOT"], "1")
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
