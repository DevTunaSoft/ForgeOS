import tempfile
from pathlib import Path
import unittest
from forgeos.context_index import render_context, search_project

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

    def test_low_memory_budget_limits_repository_index(self):
        small = render_context(self.root, "engine repair", max_chars=512)
        self.assertLessEqual(len(small), 512)
        self.assertIn("Repository files", small)
        with self.assertRaises(ValueError):
            render_context(self.root, max_chars=511)
        with self.assertRaises(ValueError):
            render_context(self.root, max_chars=True)

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

    def test_symbol_search_prioritizes_names_and_never_returns_bodies(self):
        for index in range(81):
            (self.root / f"needle_{index:03}.py").write_text(
                f"def unrelated_{index}():\n return 'PRIVATE_VALUE'\n")
        (self.root / "z_target.py").write_text(
            "def needle_symbol(value):\n return 'PRIVATE_VALUE'\n")
        results = search_project(self.root, "needle")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["path"], "z_target.py")
        self.assertEqual(results[0]["line"], 1)
        self.assertEqual(results[0]["signature"], "def needle_symbol(value)")
        self.assertEqual(results[0]["matched_terms"], ["needle"])
        self.assertNotIn("PRIVATE_VALUE", str(results))

    def test_symbol_search_excludes_hidden_paths_and_validates_bounds(self):
        (self.root / ".hidden.py").write_text("def secret_symbol(): pass\n")
        (self.root / "visible.py").write_text("def safe_symbol(): pass\n")
        self.assertEqual(search_project(self.root, "secret"), [])
        self.assertEqual(search_project(self.root, "safe")[0]["path"], "visible.py")
        for query, limit in (("", 20), ("\0", 20), ("x" * 257, 20),
                             ("safe", 0), ("safe", 101), ("safe", True)):
            with self.subTest(query=query, limit=limit), self.assertRaises(ValueError):
                search_project(self.root, query, limit=limit)

if __name__ == "__main__":
    unittest.main()
