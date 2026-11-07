import tempfile
from pathlib import Path
import unittest
from forgeos.context_index import render_context

class ContextIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("VERY_PRIVATE_SECRET")
        (self.root / ".env").write_text("DB_PASSWORD=secret")
        (self.root / "app.py").write_text(
            "API_KEY='SECRET_VALUE'\nclass Engine:\n def build(self, project):\n"
            "  return 'SECRET_VALUE'\ndef repair(value):\n return value\n")
        (self.root / "ui.py").write_text("def screen():\n return 1\n")

    def test_safe_symbols_without_source_bodies(self):
        out = render_context(self.root, "engine build")
        self.assertIn("class Engine", out)
        self.assertIn("def build(self, project)", out)
        self.assertNotIn("SECRET_VALUE", out)
        self.assertNotIn("VERY_PRIVATE_SECRET", out)
        self.assertNotIn("DB_PASSWORD", out)
        self.assertNotIn(".env", out)

    def test_repeatable_relevance_order(self):
        a = render_context(self.root, "ui")
        self.assertEqual(a, render_context(self.root, "ui"))
        self.assertLess(a.index("ui.py"), a.index("app.py"))

    def test_skip_symlink_escape(self):
        outside = Path(self.temp.name) / "outside.py"
        outside.write_text("def private_symbol():\n pass\n")
        link = self.root / "escaped.py"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        self.assertNotIn("escaped.py", render_context(self.root, "escaped"))

    def test_large_ast_not_loaded(self):
        (self.root / "huge.py").write_text(
            "def private_signature():\n pass\n" + "# filler\n" * 14000)
        out = render_context(self.root, "huge")
        self.assertIn("huge.py", out)
        self.assertNotIn("private_signature", out)

    def test_boundaries(self):
        for index in range(140):
            (self.root / f"file_{index:03}.py").write_text(
                f"def foo_{index}():\n return True\n")
        out = render_context(self.root, "file")
        self.assertLessEqual(len(out), 8000)
        self.assertLessEqual(out.count("\n- "), 80)

if __name__ == "__main__":
    unittest.main()
