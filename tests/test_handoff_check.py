"""Regression checks for the handoff document validator."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_handoff.py"
spec = importlib.util.spec_from_file_location("check_handoff", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class HandoffCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name, markers in module.REQUIRED.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(markers), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_documents(self):
        self.assertEqual(module.check(self.root), [])

    def test_missing_document(self):
        (self.root / "docs/HANDOFF.md").unlink()
        self.assertTrue(any("MISSING:" in e for e in module.check(self.root)))

    def test_broken_link(self):
        (self.root / "docs/HANDOFF.md").write_text("ForgeOS [bad](missing.md)")
        self.assertTrue(any("BROKEN LINK:" in e for e in module.check(self.root)))

    def test_fenced_example_is_ignored(self):
        (self.root / "docs/HANDOFF.md").write_text("ForgeOS\n```md\n[example](missing.md)\n```\n")
        self.assertEqual(module.check(self.root), [])

    def test_escaping_link_is_rejected(self):
        (self.root / "docs/HANDOFF.md").write_text("ForgeOS [outside](../../secret.txt)")
        self.assertTrue(any("ESCAPING LINK:" in e for e in module.check(self.root)))

if __name__ == "__main__":
    unittest.main()
