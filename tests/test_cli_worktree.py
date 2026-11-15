"""Database-free CLI coverage for worktree integrity manifests."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.cli import main
from forgeos.core import Store


class WorktreeManifestCliTests(unittest.TestCase):
    def test_export_and_check_do_not_initialize_task_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            (worktree / ".git").mkdir()
            (worktree / "main.py").write_text("print('ok')\n")
            manifest = root / "manifest.jsonl"
            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Worktree manifest must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "--db", str(root / "missing.sqlite3"),
                    "worktree-manifest", str(worktree), str(manifest)]), 0)
            self.assertIn("Exported worktree manifest:", output.getvalue())

            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Worktree check must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "worktree-manifest-check", str(manifest), str(worktree)]), 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report["valid"])
            self.assertEqual(report["files_checked"], 1)


if __name__ == "__main__":
    unittest.main()
