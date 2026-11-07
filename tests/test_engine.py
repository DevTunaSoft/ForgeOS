import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from forgeos.core import Store
from forgeos.engine import EditError, apply_edits, execute_task, parse_edits


class EditManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_reject_traversal_and_protected_paths(self):
        for name in ("../escape.py", "/tmp/escape.py", ".git/config.py",
                     ".github/workflows/ci.yml", ".env.py", "foo\\bar.py",
                     "src/../../escape.py", "C:/drive.py", "app.exe"):
            with self.subTest(name=name), self.assertRaises(EditError):
                parse_edits(self.root, json.dumps({"files": [{"path": name, "content": "x"}]}))

    def test_validate_all_before_any_write(self):
        manifest = json.dumps({"files": [
            {"path": "safe.py", "content": "good"},
            {"path": "../bad.py", "content": "bad"},
        ]})
        with self.assertRaises(EditError):
            apply_edits(self.root, manifest)
        self.assertFalse((self.root / "safe.py").exists())

    def test_reject_symlink_escape(self):
        outside = self.root / "outside"
        outside.mkdir()
        workspace = self.root / "repo"
        workspace.mkdir()
        try:
            (workspace / "link").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(EditError):
            parse_edits(workspace, json.dumps({"files": [
                {"path": "link/escape.py", "content": "bad"}
            ]}))

    def test_reject_duplicate_and_oversize(self):
        with self.assertRaises(EditError):
            parse_edits(self.root, json.dumps({"files": [
                {"path": "a.py", "content": "1"}, {"path": "a.py", "content": "2"}]}))
        with self.assertRaises(EditError):
            parse_edits(self.root, json.dumps({"files": [
                {"path": "a.py", "content": "x" * 128001}]}))

    def test_apply_valid_file(self):
        paths = apply_edits(self.root, json.dumps({"files": [
            {"path": "src/hello.py", "content": "print('ok')\n"}]}))
        self.assertEqual(paths, ["src/hello.py"])
        self.assertEqual((self.root / "src/hello.py").read_text(), "print('ok')\n")


@unittest.skipUnless(shutil.which("git"), "Git required")
class EngineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        def git(*args):
            subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        git("config", "user.name", "ForgeOS Test")
        git("config", "user.email", "forgeos@example.invalid")
        (self.repo / "test_app.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            " def test_value(self):\n  from app import value\n  self.assertEqual(value(), 42)\n"
        )
        (self.repo / "app.py").write_text("def value():\n return 42\n")
        git("add", ".")
        git("commit", "-qm", "initial")
        self.store = Store(self.root / "state.sqlite")
        self.addCleanup(self.store.close)
        self.task = self.store.add(str(self.repo), "Keep value() returning 42")

    def test_repair_loop_and_original_untouched(self):
        responses = iter([
            json.dumps({"files": [{"path": "app.py", "content": "def value():\n return 0\n"}]}),
            json.dumps({"files": [{"path": "app.py", "content": "def value():\n return 42\n"}]}),
        ])
        result = execute_task(self.store, self.task, self.root, max_repairs=2,
                              allow_host_tests=True, generate=lambda model, prompt: next(responses))
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["iterations"], 2)
        from forgeos.error_learning import RepairMemory
        records = RepairMemory(self.root / "repair_memory.sqlite3").stats()
        self.assertEqual(records["verified_repairs"], 1)
        self.assertEqual(records["failed_repairs"], 0)
        self.assertEqual(self.store.get(self.task)["status"], "passed")
        self.assertEqual((self.repo / "app.py").read_text(), "def value():\n return 42\n")
        self.assertTrue((Path(result["workspace"]) / "app.py").exists())

    def test_host_tests_require_opt_in(self):
        with self.assertRaises(ValueError):
            execute_task(self.store, self.task, self.root)
        self.assertEqual(self.store.get(self.task)["status"], "queued")
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM task_leases").fetchone()[0], 0)

    def test_invalid_model_response_blocks_and_preserves_source(self):
        with self.assertRaises(EditError):
            execute_task(self.store, self.task, self.root, allow_host_tests=True,
                         generate=lambda model, prompt: '{"files":[{"path":"../bad.py","content":"x"}]}')
        self.assertEqual(self.store.get(self.task)["status"], "blocked")
        self.assertEqual((self.repo / "app.py").read_text(), "def value():\n return 42\n")

    def test_exhausted_repairs(self):
        response = json.dumps({"files": [{"path": "app.py", "content": "def value():\n return -1\n"}]})
        result = execute_task(self.store, self.task, self.root, max_repairs=1,
                              allow_host_tests=True, generate=lambda model, prompt: response)
        self.assertFalse(result["passed"])
        self.assertEqual(result["iterations"], 2)
        from forgeos.error_learning import RepairMemory
        records = RepairMemory(self.root / "repair_memory.sqlite3").stats()
        self.assertEqual(records["failed_repairs"], 1)
        self.assertEqual(records["verified_repairs"], 0)
        self.assertEqual(self.store.get(self.task)["status"], "failed")


class FrozenSecureRoutingTests(unittest.TestCase):
    def test_frozen_windows_workspace_tests_never_fall_back(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from forgeos.engine import _run_workspace_tests
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = SimpleNamespace(path=root, env={"FORGEOS_EXECUTION_SNAPSHOT": "1"})
            with patch("forgeos.engine.os.name", "nt"), \
                 patch("forgeos.engine.sys.frozen", True, create=True), \
                 patch("forgeos.test_runtime.create_windows_low_integrity_snapshot",
                       return_value=runtime), \
                 patch("forgeos.test_runtime.remove_test_snapshot") as cleanup, \
                 patch("forgeos.secure_runner.run_secure_windows_test",
                       return_value={"passed": True}) as secure, \
                 patch("forgeos.engine.run_test",
                       side_effect=AssertionError("weaker runner fallback")):
                result = _run_workspace_tests(root, "python-unittest")
            self.assertTrue(result["passed"])
            secure.assert_called_once_with(runtime, "python-unittest")
            cleanup.assert_called_once_with(root)


class AuditRegressionTests(unittest.TestCase):
    def test_generator_uses_edit_api_not_plan(self):
        from forgeos.engine import _generate
        class FakeModel:
            def generate_edits(self, prompt):
                return '{"files":[{"path":"app.py","content":"pass"}]}'
            def plan(self, prompt):
                raise AssertionError("Planning endpoint used for code generation")
        self.assertIn('"files"', _generate(FakeModel(), "request"))

    def test_interrupted_workspace_requires_inspection(self):
        import shutil
        if not shutil.which("git"):
            self.skipTest("Git required")
        import subprocess
        from forgeos.core import Store
        from forgeos.engine import execute_task
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            for args in (("init", "-q", "-b", "main"),
                         ("config", "user.name", "Test"),
                         ("config", "user.email", "test@example.invalid")):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
            (repo / "app.py").write_text("pass\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True, capture_output=True)
            store = Store(root / "state.sqlite")
            try:
                task = store.add(str(repo), "Edit app")
                existing = root / f"task-{task}"
                existing.mkdir()
                (existing / "important.txt").write_text("preserve")
                with self.assertRaisesRegex(ValueError, "requires inspection"):
                    execute_task(store, task, root, allow_host_tests=True)
                self.assertEqual((existing / "important.txt").read_text(), "preserve")
                self.assertEqual(store.get(task)["status"], "queued")
            finally:
                store.close()


class TransactionRecoveryTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = lambda files: json.dumps({"files": files})

    def test_multi_file_replace_failure_rolls_back(self):
        from unittest.mock import patch
        import os
        (self.root / "first.py").write_text("original")
        real_replace = os.replace
        calls = {"n": 0}
        def fail_second(source, target):
            if str(source).endswith(".new"):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise OSError("simulated disk failure")
            return real_replace(source, target)
        with patch("forgeos.transactions.os.replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                apply_edits(self.root, self.manifest([
                    {"path": "first.py", "content": "changed"},
                    {"path": "second.py", "content": "new"}]))
        self.assertEqual((self.root / "first.py").read_text(), "original")
        self.assertFalse((self.root / "second.py").exists())
        self.assertFalse((self.root / ".forgeos" / "pending-edit").exists())

    def test_manual_recovery_of_interrupted_journal(self):
        from forgeos.transactions import recover_edits, _digest
        (self.root / "first.py").write_text("original")
        txn = self.root / ".forgeos" / "pending-edit"
        txn.mkdir(parents=True)
        (txn / "0.bak").write_bytes(b"original")
        (self.root / "first.py").write_text("changed")
        (txn / "manifest.json").write_text(json.dumps({
            "version": 1, "state": "pending", "entries": [{
                "path": "first.py", "before": _digest(b"original"),
                "after": _digest(b"changed") }]}))
        self.assertTrue(recover_edits(self.root))
        self.assertEqual((self.root / "first.py").read_text(), "original")
        self.assertFalse(txn.exists())
        self.assertFalse(recover_edits(self.root))

    def test_conflicting_external_change_preserved(self):
        from forgeos.transactions import recover_edits, _digest, TransactionError
        (self.root / "first.py").write_text("external change")
        txn = self.root / ".forgeos" / "pending-edit"
        txn.mkdir(parents=True)
        (txn / "0.bak").write_bytes(b"original")
        (txn / "manifest.json").write_text(json.dumps({
            "version": 1, "state": "pending", "entries": [{
                "path": "first.py", "before": _digest(b"original"),
                "after": _digest(b"changed") }]}))
        with self.assertRaises(TransactionError):
            recover_edits(self.root)
        self.assertEqual((self.root / "first.py").read_text(), "external change")
        self.assertTrue(txn.exists())


    def test_corrupted_backup_blocks_recovery_without_mutation(self):
        from forgeos.transactions import recover_edits, _digest, TransactionError
        txn = self.root / ".forgeos" / "pending-edit"
        txn.mkdir(parents=True)
        (self.root / "first.py").write_text("new")
        (txn / "0.bak").write_bytes(b"corrupt")
        (txn / "manifest.json").write_text(json.dumps({
            "version": 1, "state": "pending", "entries": [{
                "path": "first.py", "before": _digest(b"old"),
                "after": _digest(b"new") }]}))
        with self.assertRaises(TransactionError):
            recover_edits(self.root)
        self.assertEqual((self.root / "first.py").read_text(), "new")
        self.assertTrue(txn.exists())

    def test_missing_manifest_preserves_journal(self):
        from forgeos.transactions import recover_edits, TransactionError
        txn = self.root / ".forgeos" / "pending-edit"
        txn.mkdir(parents=True)
        (txn / "0.bak").write_bytes(b"original")
        with self.assertRaises(TransactionError):
            recover_edits(self.root)
        self.assertTrue((txn / "0.bak").exists())

    def test_rollback_failure_preserves_journal_for_retry(self):
        from unittest.mock import patch
        from forgeos.transactions import recover_edits, _digest
        import os
        txn = self.root / ".forgeos" / "pending-edit"
        txn.mkdir(parents=True)
        (self.root / "first.py").write_text("new")
        (txn / "0.bak").write_bytes(b"old")
        (txn / "manifest.json").write_text(json.dumps({
            "version": 1, "state": "pending", "entries": [{
                "path": "first.py", "before": _digest(b"old"),
                "after": _digest(b"new") }]}))
        with patch("forgeos.transactions.os.replace", side_effect=OSError("disk locked")):
            with self.assertRaises(OSError):
                recover_edits(self.root)
        self.assertTrue(txn.exists())
        self.assertEqual((self.root / "first.py").read_text(), "new")
        self.assertTrue(recover_edits(self.root))
        self.assertEqual((self.root / "first.py").read_text(), "old")

    def test_new_file_crash_rollback_removes_only_transaction_file(self):
        from forgeos.transactions import recover_edits, _digest
        txn = self.root / ".forgeos" / "pending-edit"
        txn.mkdir(parents=True)
        (self.root / "created.py").write_text("new")
        (txn / "manifest.json").write_text(json.dumps({
            "version": 1, "state": "pending", "entries": [{
                "path": "created.py", "before": None,
                "after": _digest(b"new") }]}))
        self.assertTrue(recover_edits(self.root))
        self.assertFalse((self.root / "created.py").exists())


    def test_tampered_journal_path_cannot_escape_workspace(self):
        from forgeos.transactions import recover_edits, _digest
        outside = self.root.parent / ("forgeos-outside-" + self.root.name + ".py")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        outside.write_text("untouched")
        txn = self.root / ".forgeos" / "pending-edit"
        txn.mkdir(parents=True)
        (txn / "manifest.json").write_text(json.dumps({
            "version": 1, "state": "pending", "entries": [{
                "path": "../" + outside.name, "before": None,
                "after": _digest(b"untouched") }]}))
        with self.assertRaises(ValueError):
            recover_edits(self.root)
        self.assertEqual(outside.read_text(), "untouched")
        self.assertTrue(txn.exists())

@unittest.skipUnless(shutil.which("git"), "Git required")
class CheckpointResumeTests(EngineIntegrationTests):
    def test_resume_pending_test_without_regeneration(self):
        from unittest.mock import patch
        response = json.dumps({"files": [{"path": "app.py", "content": "def value():\\n return 42\\n"}]})
        with patch("forgeos.engine.run_test", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                execute_task(self.store, self.task, self.root, allow_host_tests=True,
                             generate=lambda model, prompt: response)
        self.assertEqual(self.store.get(self.task)["status"], "testing")
        self.store.recover()
        self.assertEqual(self.store.get(self.task)["status"], "blocked")
        with patch("forgeos.engine.run_test", return_value={"passed": True, "returncode": 0, "output": "ok"}):
            result = execute_task(self.store, self.task, self.root,
                                  allow_host_tests=True, resume=True,
                                  generate=lambda model, prompt: (_ for _ in ()).throw(
                                      AssertionError("Unexpected regeneration")))
        self.assertTrue(result["passed"])
        self.assertEqual(self.store.get(self.task)["status"], "passed")

    def test_resume_refuses_missing_workspace(self):
        self.store.update(self.task, "blocked", result=json.dumps({
            "workspace": str(self.root / f"task-{self.task}"),
            "branch": f"forgeos/task-{self.task}", "phase": "test_pending",
            "edited": ["app.py"]}))
        with self.assertRaisesRegex(ValueError, "existing workspace"):
            execute_task(self.store, self.task, self.root, resume=True,
                         allow_host_tests=True)
        self.assertEqual(self.store.get(self.task)["status"], "blocked")

    def test_resume_refuses_unverified_checkpoint(self):
        response = json.dumps({"files": [{"path": "app.py", "content": "def value():\\n return 42\\n"}]})
        from unittest.mock import patch
        with patch("forgeos.engine.run_test", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                execute_task(self.store, self.task, self.root, allow_host_tests=True,
                             generate=lambda model, prompt: response)
        self.store.recover()
        self.store.update(self.task, "blocked", result=json.dumps({
            "workspace": str(self.root / f"task-{self.task}"),
            "branch": f"forgeos/task-{self.task}", "phase": "generating",
            "edited": ["app.py"]}))
        with self.assertRaisesRegex(ValueError, "test-pending checkpoint"):
            execute_task(self.store, self.task, self.root, resume=True,
                         allow_host_tests=True)
        self.assertEqual(self.store.get(self.task)["status"], "blocked")

    def test_resume_refuses_tampered_file(self):
        from unittest.mock import patch
        response = json.dumps({"files": [{"path": "app.py", "content": "def value():\\n return 42\\n"}]})
        with patch("forgeos.engine.run_test", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                execute_task(self.store, self.task, self.root, allow_host_tests=True,
                             generate=lambda model, prompt: response)
        self.store.recover()
        workspace = self.root / f"task-{self.task}"
        (workspace / "app.py").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "Checkpoint file changed"):
            execute_task(self.store, self.task, self.root, allow_host_tests=True, resume=True)
        self.assertEqual((workspace / "app.py").read_text(), "tampered")

    def test_resume_rejects_missing_integrity_hashes(self):
        from unittest.mock import patch
        response = json.dumps({"files": [{"path": "app.py", "content": "def value():\\n return 42\\n"}]})
        with patch("forgeos.engine.run_test", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                execute_task(self.store, self.task, self.root, allow_host_tests=True,
                             generate=lambda model, prompt: response)
        self.store.recover()
        checkpoint = json.loads(self.store.get(self.task)["result"])
        checkpoint.pop("sha256")
        self.store.update(self.task, "blocked", result=json.dumps(checkpoint))
        with self.assertRaisesRegex(ValueError, "integrity hashes"):
            execute_task(self.store, self.task, self.root, allow_host_tests=True, resume=True)

    def test_resume_repair_pending_after_generation_interrupt(self):
        from unittest.mock import patch
        failing = json.dumps({"files": [{"path": "app.py", "content": "def value():\n return 0\n"}]})
        repaired = json.dumps({"files": [{"path": "app.py", "content": "def value():\n return 42\n"}]})
        calls = []

        def interrupted_generator(model, prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return failing
            raise KeyboardInterrupt()

        with patch("forgeos.engine.run_test",
                   return_value={"passed": False, "returncode": 1, "output": "expected 42, got 0"}):
            with self.assertRaises(KeyboardInterrupt):
                execute_task(self.store, self.task, self.root, allow_host_tests=True,
                             max_repairs=2, generate=interrupted_generator)
        saved = json.loads(self.store.get(self.task)["result"])
        self.assertEqual(self.store.get(self.task)["status"], "editing")
        self.assertEqual(saved["phase"], "repair_pending")
        self.assertEqual(saved["attempt"], 2)
        self.assertIn("expected 42", saved["last_test_output"])

        self.store.recover()
        with patch("forgeos.engine.run_test",
                   return_value={"passed": True, "returncode": 0, "output": "ok"}):
            result = execute_task(self.store, self.task, self.root, allow_host_tests=True,
                                  max_repairs=2, resume=True,
                                  generate=lambda model, prompt: repaired)
        self.assertTrue(result["passed"])
        self.assertEqual(result["iterations"], 2)
        self.assertEqual((Path(result["workspace"]) / "app.py").read_text(),
                         "def value():\n return 42\n")

    def test_resume_rejects_changed_execution_contract(self):
        from unittest.mock import patch
        response = json.dumps({"files": [{"path": "app.py", "content": "def value():\\n return 42\\n"}]})
        with patch("forgeos.engine.run_test", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                execute_task(self.store, self.task, self.root, allow_host_tests=True,
                             generate=lambda model, prompt: response)
        self.store.recover()
        before = self.store.get(self.task)["result"]
        with self.assertRaisesRegex(ValueError, "execution contract mismatch"):
            execute_task(self.store, self.task, self.root, preset="python-pytest",
                         allow_host_tests=True, resume=True)
        self.assertEqual(self.store.get(self.task)["status"], "blocked")
        self.assertEqual(self.store.get(self.task)["result"], before)

    def test_active_execution_lease_blocks_duplicate_run(self):
        owner = "already-running"
        self.assertTrue(self.store.acquire_lease(self.task, owner))
        response = json.dumps({"files": [{"path": "app.py",
                               "content": "def value():\n return 42\n"}]})
        with self.assertRaisesRegex(ValueError, "active execution lease"):
            execute_task(self.store, self.task, self.root, allow_host_tests=True,
                         generate=lambda model, prompt: response)
        self.assertEqual(self.store.get(self.task)["status"], "queued")
        self.assertFalse((self.root / f"task-{self.task}").exists())
        self.assertTrue(self.store.release_lease(self.task, owner))

    def test_keyboard_interrupt_releases_execution_lease(self):
        from unittest.mock import patch
        response = json.dumps({"files": [{"path": "app.py",
                               "content": "def value():\n return 42\n"}]})
        with patch("forgeos.engine.run_test", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                execute_task(self.store, self.task, self.root, allow_host_tests=True,
                             generate=lambda model, prompt: response)
        self.assertTrue(self.store.acquire_lease(self.task, "probe"))
        self.assertTrue(self.store.release_lease(self.task, "probe"))


    @unittest.skipUnless(os.name == "nt", "Windows Low-integrity runtime required")
    def test_windows_snapshot_blocks_write_back_to_editable_worktree(self):
        escape_test = (
            "import os, unittest\n"
            "from pathlib import Path\n"
            "class EscapeTest(unittest.TestCase):\n"
            " def test_editable_worktree_is_write_protected(self):\n"
            "  self.assertEqual(os.environ.get('FORGEOS_EXECUTION_SNAPSHOT'), '1')\n"
            "  runtime = Path(__file__).resolve().parent\n"
            "  self.assertTrue(runtime.name.startswith('.forgeos-exec-'))\n"
            "  editable = runtime.parent / runtime.name.replace('.forgeos-exec-', '', 1)\n"
            "  target = editable / 'escaped-from-test.txt'\n"
            "  with self.assertRaises(OSError):\n"
            "   target.write_text('escape')\n"
        )
        (self.repo / "test_escape.py").write_text(escape_test)
        subprocess.run(["git", "add", "test_escape.py"], cwd=self.repo,
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "add escape regression"],
                       cwd=self.repo, check=True, capture_output=True)
        response = json.dumps({"files": [{"path": "app.py",
                               "content": "def value():\n return 42\n"}]})
        result = execute_task(
            self.store, self.task, self.root, allow_host_tests=True,
            generate=lambda model, prompt: response)
        self.assertTrue(result["passed"], result)
        workspace = Path(result["workspace"])
        self.assertFalse((workspace / "escaped-from-test.txt").exists())
        runtime = workspace.parent / f".forgeos-exec-{workspace.name}"
        self.assertFalse(runtime.exists())
        self.assertFalse(
            runtime.with_name(
                runtime.name + ".forgeos-execution-owner.json").exists())
