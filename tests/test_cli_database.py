"""CLI integration for explicit SQLite verification and backup."""
import io
from contextlib import closing, redirect_stdout
from pathlib import Path
import sqlite3
import tempfile
import unittest

from forgeos.cli import main
from forgeos.core import Store


class DatabaseCommandsTests(unittest.TestCase):
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

    def test_restore_activate_is_explicit_and_does_not_open_active_store(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            active = root / "tasks.sqlite3"
            source = root / "source.sqlite3"
            backup = root / "backup.sqlite3"
            rollback = root / "before-activation.sqlite3"

            active_store = Store(active)
            try:
                active_store.add(str(repo), "old CLI task")
            finally:
                active_store.close()
            source_store = Store(source)
            try:
                source_store.add(str(repo), "new CLI task")
                source_store.backup_to(backup)
            finally:
                source_store.close()

            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Activation must not open the active task database")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "db-restore-activate", str(backup), str(active),
                    str(rollback), "--confirm"]), 0)
            self.assertIn("Activated database:", output.getvalue())
            with closing(sqlite3.connect(active)) as db:
                self.assertEqual(db.execute(
                    "SELECT description FROM tasks").fetchone()[0], "new CLI task")
            with closing(sqlite3.connect(rollback)) as db:
                self.assertEqual(db.execute(
                    "SELECT description FROM tasks").fetchone()[0], "old CLI task")

    def test_activation_status_cli_is_bounded_and_rollback_requires_confirmation(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "tasks.sqlite3"
            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Activation status must not open the active task database")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "db-activation-status", str(destination)]), 0)
            self.assertEqual(
                __import__("json").loads(output.getvalue())["status"], "clear")
            with self.assertRaisesRegex(ValueError, "confirmation"):
                main(["db-activation-rollback", str(destination)])

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
            manifest = root / "saved.json"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    "--db", str(db_path), "db-backup", str(target),
                    "--manifest", str(manifest)]), 0)
            self.assertIn("Verified backup:", output.getvalue())
            self.assertTrue(manifest.is_file())
            with closing(sqlite3.connect(target)) as copied:
                self.assertEqual(
                    copied.execute("SELECT description FROM tasks").fetchone()[0],
                    "backup cli test")
            with self.assertRaisesRegex(ValueError, "new file"):
                main(["--db", str(db_path), "db-backup", str(target)])

            restored = root / "saved-restored.sqlite3"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    "db-restore-copy", str(target), str(restored),
                    "--manifest", str(manifest)]), 0)
            self.assertIn("Verified restore copy:", output.getvalue())

    def test_journal_command_returns_bounded_task_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            db_path = root / "state.sqlite3"
            store = Store(db_path)
            try:
                task = store.add(str(root), "journal cli test")
            finally:
                store.close()
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    "--db", str(db_path), "journal", "--task-id", str(task),
                    "--limit", "1"]), 0)
            events = __import__('json').loads(output.getvalue())
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['event_type'], 'task.created')
            self.assertEqual(events[0]['task_id'], task)

    def test_journal_check_command_reports_clean_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            db_path = root / 'state.sqlite3'
            store = Store(db_path)
            try:
                store.add(str(root), 'journal check cli test')
            finally:
                store.close()
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    '--db', str(db_path), 'journal-check']), 0)
            report = __import__('json').loads(output.getvalue())
            self.assertEqual(report['issues'], [])
            self.assertEqual(report['checked'], 1)

    def test_journal_export_command_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            db_path = root / 'state.sqlite3'
            store = Store(db_path)
            try:
                task = store.add(str(root), 'journal export cli test')
            finally:
                store.close()
            target = root / 'journal.jsonl'
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    '--db', str(db_path), 'journal-export', str(target),
                    '--task-id', str(task)]), 0)
            self.assertIn('Exported journal:', output.getvalue())
            rows = [__import__('json').loads(line)
                    for line in target.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['task_id'], task)

    def test_journal_handoff_command_writes_replay_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            db_path = root / 'state.sqlite3'
            store = Store(db_path)
            try:
                task = store.add(str(root), 'journal handoff cli test')
            finally:
                store.close()
            target = root / 'handoff.jsonl'
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    '--db', str(db_path), 'journal-handoff', str(target),
                    '--task-id', str(task)]), 0)
            self.assertIn('Exported journal handoff:', output.getvalue())
            rows = [__import__('json').loads(line)
                    for line in target.read_text().splitlines()]
            self.assertEqual(rows[0]['format'], 'forgeos-journal-handoff-v1')
            self.assertEqual(rows[0]['task_id'], task)
            self.assertEqual(rows[-1]['complete'], True)

    def test_journal_handoff_check_command_is_offline_and_reports_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            db_path = root / 'state.sqlite3'
            store = Store(db_path)
            try:
                task = store.add(str(root), 'journal handoff check cli test')
                handoff = root / 'handoff.jsonl'
                manifest = root / 'handoff.json'
                store.export_handoff(handoff, task_id=task, manifest=manifest)
            finally:
                store.close()
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    '--db', str(root / 'missing.sqlite3'),
                    'journal-handoff-check', str(handoff),
                    '--manifest', str(manifest),
                    '--evidence-directory', str(root)]), 0)
            report = __import__('json').loads(output.getvalue())
            self.assertTrue(report['valid'])
            self.assertTrue(report['manifest_valid'])
            self.assertTrue(report['evidence_checked'])
            self.assertTrue(report['evidence_valid'])
            lines = handoff.read_text().splitlines()
            summary = __import__('json').loads(lines[-1])
            summary['event_count'] = 0
            lines[-1] = __import__('json').dumps(summary)
            handoff.write_text('\n'.join(lines) + '\n')
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    'journal-handoff-check', str(handoff),
                    '--manifest', str(manifest)]), 1)
            report = __import__('json').loads(output.getvalue())
            self.assertFalse(report['valid'])
            self.assertFalse(report['manifest_valid'])


if __name__ == "__main__":
    unittest.main()
