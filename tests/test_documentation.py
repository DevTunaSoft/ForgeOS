"""Regression tests for the offline documentation maintenance gate."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check_documentation as docs


class DocumentationTests(unittest.TestCase):
    def test_heading_and_fence_parsing(self):
        source = "# Main!\n\n```md\n# Not a heading\n```\n## Sub heading\n## Sub heading\n"
        self.assertEqual(docs.anchors(source), {"main", "sub-heading", "sub-heading-1"})

    def test_requires_doc_when_product_changes(self):
        self.assertTrue(docs.check_sync({"src/forgeos/engine.py"}))
        self.assertEqual(docs.check_sync({"src/forgeos/engine.py", "docs/HANDOFF.md"}), [])
        self.assertEqual(docs.check_sync({"tests/test_core.py"}), [])

    def test_gui_update_needs_user_guide(self):
        changed = {"src/forgeos/gui.py", "docs/ARCHITECTURE.md"}
        self.assertTrue(any("GUI/CLI" in item for item in docs.check_sync(changed)))
        self.assertEqual(docs.check_sync(changed | {"docs/USER_GUIDE.md"}), [])

    def test_security_update_needs_security_docs(self):
        changed = {"src/forgeos/secure_runner.py", "docs/HANDOFF.md"}
        self.assertTrue(any("Execution security" in item for item in docs.check_sync(changed)))
        self.assertEqual(docs.check_sync(changed | {"docs/ARCHITECTURE.md"}), [])

    def test_invalid_git_revisions_rejected(self):
        with self.assertRaises(ValueError):
            docs.changed_paths("--output", "HEAD")
        with self.assertRaises(ValueError):
            docs.changed_paths("main;echo", "HEAD")

    def test_built_in_help_uses_canonical_guide_and_packaging(self):
        gui = (docs.ROOT / "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        build = (docs.ROOT / ".github" / "workflows" / "windows-exe.yml").read_text(
            encoding="utf-8")
        guide = (docs.ROOT / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")
        self.assertIn('text="How to use ForgeOS"', gui)
        self.assertIn("docs/USER_GUIDE.md;forgeos", build)
        self.assertIn("offline_user_guide_available", build)
        self.assertIn("How to use ForgeOS", guide)
        self.assertTrue("AppContainer" in guide,
                        "User guide must explain the Windows AppContainer boundary")

    def test_public_product_scope_is_discoverable_and_honest(self):
        vision = (docs.ROOT / "docs" / "PRODUCT_VISION.md").read_text(encoding="utf-8")
        homepage = (docs.ROOT / "README.md").read_text(encoding="utf-8")
        policy = (docs.ROOT / "docs" / "DOCUMENTATION_POLICY.md").read_text(
            encoding="utf-8")
        for section in ("## The target experience", "## Capabilities: implemented versus planned",
                        "## What ForgeOS can actually do today",
                        "## What ForgeOS cannot do yet",
                        "## What ForgeOS is deliberately designed",
                        "## What must be proven before release"):
            with self.subTest(section=section):
                self.assertIn(section, vision)
        for warning in ("not yet verified", "not encrypted", "real Windows",
                        "not a general Windows app builder", "Android",
                        "Ollama", "AppContainer"):
            with self.subTest(warning=warning):
                self.assertIn(warning.lower(), vision.lower())
        self.assertIn("docs/PRODUCT_VISION.md", homepage)
        self.assertIn("PRODUCT_VISION.md", policy)
        self.assertIn("[step-by-step user manual](USER_GUIDE.md)", vision)

    def test_relative_links_in_repository(self):
        self.assertEqual(docs.check_links(), [])


if __name__ == "__main__":
    unittest.main()
