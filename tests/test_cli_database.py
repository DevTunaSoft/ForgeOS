"""CLI integration for explicit SQLite verification and backup."""
import io
from contextlib import redirect_stdout
from pathlib import Path
import sqlite3
import tempfile
import unittest

from forgeos.cli import main
from forgeos.core import Store


class DatabaseCommandsTests(unittest.TestCase):
    def test_check_and_backup_using_explicit_db_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "state.sqlite3"
            store = Store(db_path)
            try:
                with store.db:
                    store.db.execute(
                        "INSERT INTO tasks(project,description,created,updated) "
                        "VALUES(?,?,?,?)", (str(root), "backup cli test", 1.0, 1.0))
            finally:
                store.close()
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--db", str(db_path), "db-check"]), 0)
            self.assertIn("SQLite integrity: ok", output.getvalue())
            target = root / "saved.sqlite3"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    "--db", str(db_path), "db-backup", str(target)]), 0)
            self.assertIn("Verified backup:", output.getvalue())
            with sqlite3.connect(target) as copied:
                self.assertEqual(
                    copied.execute("SELECT description FROM tasks").fetchone()[0],
                    "backup cli test")
            with self.assertRaisesRegex(ValueError, "new file"):
                main(["--db", str(db_path), "db-backup", str(target)])


if __name__ == "__main__":
    unittest.main()
