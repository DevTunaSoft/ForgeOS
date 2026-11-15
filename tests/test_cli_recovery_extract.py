"""Database-free CLI coverage for recovery bundle materialization."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.cli import main
from forgeos.core import Store, _move_database_copy
from forgeos.diagnostics import EvidenceStore
from forgeos.recovery_auth import export_recovery_bundle_auth
from forgeos.recovery_extract import extract_recovery_bundle
from forgeos.recovery_package import export_recovery_bundle
from forgeos.worktree_archive import export_worktree_archive


class RecoveryBundleExtractionCliTests(unittest.TestCase):
    def test_extract_and_check_do_not_initialize_task_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            (worktree / ".git").mkdir()
            (worktree / "main.py").write_text("print('ok')\n")
            archive = root / "source.zip"
            export_worktree_archive(worktree, archive)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            reference = EvidenceStore(evidence_root).put("output\n")
            handoff = root / "task.jsonl"
            rows = [
                {"record": "header", "format": "forgeos-journal-handoff-v1",
                 "task_id": 1},
                {"record": "task", "task_id": 1, "status": "passed",
                 "attempts": 1, "created": 1.0, "updated": 2.0},
                {"record": "event", "id": 1, "task_id": 1,
                 "event_type": "test", "status": "passed", "actor": "engine",
                 "created": 2.0, "details": "", "action": "test", "attempt": 1,
                 "artifact_ref": "", "evidence_ref": reference},
                {"record": "summary", "task_id": 1, "event_count": 1,
                 "last_event_id": 1, "complete": True},
            ]
            handoff.write_text(
                "".join(json.dumps(row, separators=(",", ":")) + "\n"
                        for row in rows), encoding="utf-8")
            bundle = root / "recovery.zip"
            export_recovery_bundle(
                handoff, archive, bundle, evidence_directory=evidence_root)
            key = root / "recovery.key"
            key.write_bytes(b"k" * 64)
            authentication = root / "recovery.auth.json"
            export_recovery_bundle_auth(bundle, key, authentication)
            destination = root / "restored"

            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Recovery bundle check must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "recovery-bundle-check", str(bundle),
                    "--authentication", str(authentication),
                    "--key", str(key)]), 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report["valid"])
            self.assertTrue(report["authentication"]["valid"])

            wrong_key = root / "wrong.key"
            wrong_key.write_bytes(b"w" * 64)
            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Recovery bundle authentication check must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "recovery-bundle-check", str(bundle),
                    "--authentication", str(authentication),
                    "--key", str(wrong_key)]), 1)
            report = json.loads(output.getvalue())
            self.assertFalse(report["valid"])

            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Recovery extraction must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "--db", str(root / "missing.sqlite3"),
                    "recovery-bundle-extract", str(bundle),
                    str(destination), "--authentication", str(authentication),
                    "--key", str(key)]), 0)
            self.assertIn("Extracted recovery bundle:", output.getvalue())

            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Recovery extraction check must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "recovery-bundle-extract-check", str(destination)]), 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report["valid"])
            self.assertTrue(report["complete"])

    def test_resume_dispatch_is_database_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            (worktree / ".git").mkdir()
            (worktree / "main.py").write_text("print('ok')\n")
            archive = root / "source.zip"
            export_worktree_archive(worktree, archive)
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            reference = EvidenceStore(evidence_root).put("output\n")
            handoff = root / "task.jsonl"
            rows = [
                {"record": "header", "format": "forgeos-journal-handoff-v1",
                 "task_id": 1},
                {"record": "task", "task_id": 1, "status": "passed",
                 "attempts": 1, "created": 1.0, "updated": 2.0},
                {"record": "event", "id": 1, "task_id": 1,
                 "event_type": "test", "status": "passed", "actor": "engine",
                 "created": 2.0, "details": "", "action": "test", "attempt": 1,
                 "artifact_ref": "", "evidence_ref": reference},
                {"record": "summary", "task_id": 1, "event_count": 1,
                 "last_event_id": 1, "complete": True},
            ]
            handoff.write_text(
                "".join(json.dumps(row, separators=(",", ":")) + "\n"
                        for row in rows), encoding="utf-8")
            bundle = root / "recovery.zip"
            export_recovery_bundle(
                handoff, archive, bundle, evidence_directory=evidence_root)
            destination = root / "restored"
            calls = 0

            def fail_after_two(source, target):
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("interrupted publication")
                return _move_database_copy(source, target)

            with patch("forgeos.recovery_extract._move_database_copy",
                       side_effect=fail_after_two):
                with self.assertRaises(OSError):
                    extract_recovery_bundle(bundle, destination)
            output = io.StringIO()
            with patch.object(Store, "__init__", side_effect=AssertionError(
                    "Recovery extraction resume must remain database-free")), \
                    redirect_stdout(output):
                self.assertEqual(main([
                    "recovery-bundle-extract-resume", str(bundle),
                    str(destination)]), 0)
            self.assertIn("Resumed recovery bundle extraction:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
