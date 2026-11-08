"""Living user manual coverage against source-level GUI/CLI contracts.

Avoid fake screenshots, stale labels, and incorrectly advertised capabilities.
This test is intentionally lightweight and runs on Windows/Linux without Tk.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs" / "USER_MANUAL.md"
GUI = ROOT / "src" / "forgeos" / "gui.py"
CLI = ROOT / "src" / "forgeos" / "cli.py"
README = ROOT / "README.md"
WORKFLOW = ROOT / ".github" / "workflows" / "windows-exe.yml"
PR_TEMPLATE = ROOT / ".github" / "pull_request_template.md"


class LivingManualContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manual = MANUAL.read_text(encoding="utf-8")
        cls.gui = GUI.read_text(encoding="utf-8")
        cls.cli = CLI.read_text(encoding="utf-8")

    def test_referenced_from_homepage_and_pr_template(self):
        self.assertIn("docs/USER_MANUAL.md", README.read_text(encoding="utf-8"))
        self.assertIn("docs/USER_MANUAL.md", PR_TEMPLATE.read_text(encoding="utf-8"))

    def test_non_developer_chapters_are_present(self):
        headings = [
            "What ForgeOS does today",
            "Before you begin",
            "Get ForgeOS running",
            "Your first task in the desktop application",
            "Understand buttons and results",
            "Find errors, logs, and memory information",
            "Recover interrupted work",
            "Optional command-line reference",
            "Troubleshooting",
            "Safety, data storage, and limitations",
            "New-feature documentation and release checklist",
        ]
        for heading in headings:
            with self.subTest(heading=heading):
                self.assertIn(heading, self.manual)

    def test_every_live_button_and_risk_warning_is_explained(self):
        labels = [
            "Add task", "Generate plan", "Build / test / repair",
            "Run tests only", "Refresh", "Older tasks", "Newer tasks",
            "AI efficiency and repair learning", "Show local memory snapshot",
            "Compare selected task: original vs AI summary",
            "Allow tests to run on this computer",
        ]
        for label in labels:
            with self.subTest(label=label):
                self.assertIn(label, self.gui)
                self.assertIn(label, self.manual)

    def test_documented_commands_are_actual_cli_actions(self):
        actions = [
            "gui", "list", "add", "plan", "test", "run", "recover",
            "recover-edits", "resume-safe", "memory",
            "diagnostics", "learning", "filters",
        ]
        for action in actions:
            with self.subTest(action=action):
                self.assertRegex(
                    self.cli, r"(?:sub\.add_parser|[a-z_]+)\(['\"]"
                    + re.escape(action) + r"['\"]")
                self.assertIn("forgeos " + action, self.manual)

    def test_manual_describes_actual_presets_and_not_future_features_as_done(self):
        for preset in ["python-unittest", "python-pytest", "npm-test",
                       "cargo-test", "go-test", "dotnet-test"]:
            self.assertIn('"' + preset + '"', self.gui)
            self.assertIn(preset, self.manual)
        for caution in [
            "not yet a completed turnkey product", "not encrypted at",
            "not yet", "host", "Ollama", "4 GB", "8 GB", "AppContainer",
        ]:
            self.assertIn(caution.lower(), self.manual.lower())

    def test_download_artifact_and_memory_guidance_match_build(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("ForgeOS-Windows-EXE", workflow)
        self.assertIn("ForgeOS-Windows-EXE", self.manual)
        self.assertIn("retention-days: 14", workflow)
        self.assertIn("14 days", self.manual)
        self.assertIn("150 tasks", self.manual)
        self.assertIn("32 MiB", self.manual)

    def test_readme_links_only_a_real_manual_path(self):
        self.assertTrue(MANUAL.is_file())
        self.assertGreater(len(self.manual), 7000)


if __name__ == "__main__":
    unittest.main()
