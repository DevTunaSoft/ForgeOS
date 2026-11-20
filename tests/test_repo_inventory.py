"""Regression tests for the bounded repository inventory (roadmap P1 slice)."""
from __future__ import annotations

import json
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

from forgeos.repo_inventory import MAX_INVENTORY_FILES, build_inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        (self.root / "README.md").write_text("# Sample\n")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("def run():\n    return 1\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_app.py").write_text("def test_run():\n    pass\n")
        (self.root / "node_modules" / "pkg").mkdir(parents=True)
        (self.root / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1;\n")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("secret = 1\n")
        (self.root / ".env").write_text("TOKEN=1\n")

    def paths(self, inventory):
        return [item["path"] for item in inventory["files"]]

    def test_lists_metadata_sorted_and_skips_excluded_folders(self):
        inventory = build_inventory(self.root)
        # Sorted by casefolded path: "pyproject.toml" precedes "readme.md".
        self.assertEqual(self.paths(inventory),
                         ["pyproject.toml", "README.md", "src/app.py", "tests/test_app.py"])
        by_path = {item["path"]: item for item in inventory["files"]}
        self.assertEqual(by_path["src/app.py"]["kind"], "python")
        self.assertEqual(by_path["README.md"]["kind"], "markdown")
        self.assertTrue(by_path["pyproject.toml"]["manifest"])
        self.assertTrue(by_path["tests/test_app.py"]["test"])
        self.assertFalse(by_path["src/app.py"]["test"])
        self.assertEqual(by_path["src/app.py"]["bytes"],
                         (self.root / "src" / "app.py").stat().st_size)
        self.assertFalse(inventory["truncated"])

    def test_repeated_calls_on_an_unchanged_tree_are_identical(self):
        self.assertEqual(build_inventory(self.root), build_inventory(self.root))

    def test_reports_no_absolute_paths_or_file_bodies(self):
        text = json.dumps(build_inventory(self.root))
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("def run", text)
        self.assertNotIn("secret", text)

    def test_limit_truncates_in_sorted_order(self):
        inventory = build_inventory(self.root, limit=2)
        self.assertEqual(self.paths(inventory), ["pyproject.toml", "README.md"])
        self.assertEqual(inventory["total_candidates"], 4)
        self.assertTrue(inventory["truncated"])

    def test_invalid_limits_are_refused(self):
        for limit in (0, True, MAX_INVENTORY_FILES + 1, 2.0):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                build_inventory(self.root, limit=limit)

    def test_symlinked_files_are_not_listed(self):
        outside = Path(self.temp.name) / "outside.py"
        outside.write_text("def private():\n    pass\n")
        try:
            (self.root / "linked.py").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        self.assertNotIn("linked.py", self.paths(build_inventory(self.root)))

    def test_scan_budget_exhaustion_is_reported(self):
        with patch("forgeos.context_index.MAX_SCAN", 2):
            inventory = build_inventory(self.root)
        self.assertTrue(inventory["scan_exhausted"])
        self.assertTrue(inventory["truncated"])


class SyntheticLargeProjectTests(unittest.TestCase):
    def test_large_tree_stays_within_the_fixed_limit_and_allocation_guard(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for folder in range(21):
            directory = root / f"pkg{folder:02d}"
            directory.mkdir()
            for index in range(100):
                (directory / f"module{index:03d}.py").write_text(f"VALUE = {index}\n")
        tracemalloc.start()
        try:
            inventory = build_inventory(root)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(inventory["total_candidates"], 2100)
        self.assertEqual(len(inventory["files"]), MAX_INVENTORY_FILES)
        self.assertTrue(inventory["truncated"])
        self.assertLess(peak, 8 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
