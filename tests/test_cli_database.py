"""CLI integration for explicit SQLite verification and backup."""
import io
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
import sqlite3
import tempfile
import unittest

from forgeos.cli import main
from forgeos.core import Store


class DatabaseCommandsTests(unittest.TestCase):
    def test_publication_collision_reports_a_new_destination(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            Store(source).close()
            destination = str(Path(directory) / "copy.sqlite3")
            for command in (
                    ["--db", str(source), "db-backup", destination],
                    ["db-restore-copy", str(source), destination]):
                with self.subTest(command=command):
                    error = io.StringIO()
                    with patch("forgeos.core._publish_database_copy",
                               side_effect=FileExistsError), redirect_stderr(error):
                        self.assertEqual(main(command), 1)
                    self.assertIn("Choose a new file.", error.getvalue())

    def test_restore_copy_does_not_open_default_active_database(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.sqlite3"
            store = Store(source)
            try:
                with store.db:
                    store.db.execute(
                        "INSERT INTO tasks(project,description,created,updated) "
                        "VALUES(?,?,?,?)", (str(root), "safe restore", 1.0, 1.0))
            finally:
                store.close()
            destination = root / "restored.sqlite3"
            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Restore must not open the active task database")), \
                 redirect_stdout(output):
                self.assertEqual(main([
                    "db-restore-copy", str(source), str(destination)]), 0)
            self.assertIn("Verified restore copy:", output.getvalue())
            with closing(sqlite3.connect(destination)) as restored:
                self.assertEqual(restored.execute(
                    "SELECT description FROM tasks").fetchone()[0], "safe restore")
            with self.assertRaisesRegex(ValueError, "new file"):
                main(["db-restore-copy", str(source), str(destination)])

    def test_database_commands_never_initialize_a_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "nonexistent.sqlite3"
            destination = root / "no-backup.sqlite3"
            with self.assertRaisesRegex(ValueError, "existing regular file"):
                main(["--db", str(missing), "db-check"])
            self.assertFalse(missing.exists())
            with self.assertRaisesRegex(ValueError, "existing regular file"):
                main(["--db", str(missing), "db-backup", str(destination)])
            self.assertFalse(missing.exists())
            self.assertFalse(destination.exists())

    def test_database_commands_refuse_incompatible_schema_without_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incompatible = root / "unrelated.sqlite3"
            destination = root / "no-backup.sqlite3"
            with closing(sqlite3.connect(incompatible)) as db:
                db.execute("CREATE TABLE unrelated (value INTEGER)")
                db.commit()
            for operation in (
                    ["db-check"], ["db-backup", str(destination)]):
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(ValueError, "compatible ForgeOS"):
                        main(["--db", str(incompatible), *operation])
            with closing(sqlite3.connect(incompatible)) as db:
                tables = {r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"unrelated"})
            self.assertFalse(destination.exists())

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
            with closing(sqlite3.connect(target)) as copied:
                self.assertEqual(
                    copied.execute("SELECT description FROM tasks").fetchone()[0],
                    "backup cli test")
            with self.assertRaisesRegex(ValueError, "new file"):
                main(["--db", str(db_path), "db-backup", str(target)])


if __name__ == "__main__":
    unittest.main()
