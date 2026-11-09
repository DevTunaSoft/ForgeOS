import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch, MagicMock

from forgeos.readiness import check_readiness


class ReadinessTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()

    def check(self, **overrides):
        args = dict(project=str(self.repo), workspace_parent=str(self.workspaces),
                    backend="ollama", model="qwen2.5-coder:3b", endpoint="")
        args.update(overrides)
        return check_readiness(**args)

    @patch("forgeos.readiness.find_git", return_value="C:/Git/git.exe")
    @patch("forgeos.readiness.build_opener")
    def test_all_ready(self, opener, _git):
        response = MagicMock()
        response.read.return_value = json.dumps({"models": [{"name": "qwen2.5-coder:3b"}]}).encode()
        opener.return_value.open.return_value.__enter__.return_value = response
        self.assertTrue(all(c.ok for c in self.check()))

    @patch("forgeos.readiness.find_git", return_value="git")
    def test_reject_wrong_ollama_endpoint(self, _git):
        checks = self.check(endpoint="http://localhost:11434")
        self.assertFalse(next(c for c in checks if c.name == "Ollama").ok)

    @patch("forgeos.readiness.find_git", return_value="git")
    @patch("forgeos.readiness.build_opener")
    def test_missing_model(self, opener, _git):
        response = MagicMock()
        response.read.return_value = b'{"models":[{"name":"other:1b"}]}'
        opener.return_value.open.return_value.__enter__.return_value = response
        self.assertFalse(next(c for c in self.check() if c.name == "Ollama").ok)

    @patch("forgeos.readiness.find_git", return_value="git")
    def test_missing_repository_and_workspace(self, _git):
        checks = self.check(project=str(self.root / "missing"), workspace_parent=str(self.root / "missing"))
        self.assertFalse(next(c for c in checks if c.name == "Project").ok)
        self.assertFalse(next(c for c in checks if c.name == "Workspaces").ok)

    @patch("forgeos.readiness.find_git", return_value="git")
    def test_non_ollama_does_not_call_network(self, _git):
        with patch("forgeos.readiness.build_opener") as opener:
            checks = self.check(backend="llama.cpp")
            opener.assert_not_called()
            self.assertTrue(next(c for c in checks if c.name == "Local AI").ok)
