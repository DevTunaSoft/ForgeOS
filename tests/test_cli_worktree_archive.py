"""Database-free CLI coverage for source-only worktree archives."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.cli import main
from forgeos.core import Store


class WorktreeArchiveCliTests(unittest.TestCase):
    def test_export_and_check_do_not_initialize_task_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            (worktree / ".git").mkdir()
            (worktree / "main.py").write_text("print('ok')\n")
            archive = root / "worktree.zip"
            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Worktree archive must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "--db", str(root / "missing.sqlite3"),
                    "worktree-archive", str(worktree), str(archive)]), 0)
            self.assertIn("Exported worktree archive:", output.getvalue())

            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Worktree archive check must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "worktree-archive-check", str(archive)]), 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report["valid"])
            self.assertEqual(report["files_checked"], 1)


if __name__ == "__main__":
    unittest.main()
