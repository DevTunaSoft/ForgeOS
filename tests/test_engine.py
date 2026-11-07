import json
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
        self.assertEqual(self.store.get(self.task)["status"], "passed")
        self.assertEqual((self.repo / "app.py").read_text(), "def value():\n return 42\n")
        self.assertTrue((Path(result["workspace"]) / "app.py").exists())

    def test_host_tests_require_opt_in(self):
        with self.assertRaises(ValueError):
            execute_task(self.store, self.task, self.root)
        self.assertEqual(self.store.get(self.task)["status"], "queued")

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
        self.assertEqual(self.store.get(self.task)["status"], "failed")
