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

    def test_relative_links_in_repository(self):
        self.assertEqual(docs.check_links(), [])


if __name__ == "__main__":
    unittest.main()
