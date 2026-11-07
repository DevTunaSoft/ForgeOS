"""Roadmap ownership checks: avoid duplicate numbered work or shadow subsystems.

This audits documented owner mapping, not runtime release acceptance.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "ENHANCEMENT_DEDUP_AUDIT.md"
ROADMAP = ROOT / "docs" / "RTK_ENHANCEMENTS.md"
ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*\*\*"
    r"(EXISTING|EXTEND|NEW|PR31|MERGED)"
    r"\*\*\s*·\s*([A-M])\s*\|\s*"
    r"\x60([^\x60]+)\x60\s*\|",
    re.MULTILINE,
)


class EnhancementOwnershipContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = AUDIT.read_text(encoding="utf-8")
        cls.roadmap = ROADMAP.read_text(encoding="utf-8")
        cls.rows = [
            (int(number), title, status, group, path)
            for number, title, status, group, path in ROW.findall(cls.audit)
        ]

    def test_every_enhancement_mapped_once(self):
        self.assertEqual(len(self.rows), 52)
        self.assertEqual(sorted(row[0] for row in self.rows), list(range(1, 53)))
        self.assertEqual(len({row[1] for row in self.rows}), 52)

    def test_all_roadmap_titles_match_canonical_audit(self):
        source = [
            re.sub(r"\s+\([^)]*\)$", "", match.strip())
            for match in re.findall(r"^- \[[x ]\] (.+)$", self.roadmap, re.MULTILINE)
        ]
        self.assertEqual(len(source), 52)
        self.assertEqual(source, [row[1] for row in self.rows])

    def test_each_owner_is_existing_source_or_ci_path(self):
        for number, title, status, group, path in self.rows:
            with self.subTest(feature=number, source=path):
                self.assertFalse(Path(path).is_absolute())
                self.assertNotIn("..", Path(path).parts)
                self.assertTrue((ROOT / path).is_file(),
                                f"Feature #{number} has no source owner {path}")

    def test_related_capabilities_reuse_owners(self):
        by_number = {row[0]: row for row in self.rows}
        for pair in ((1, 2), (1, 7), (11, 14), (11, 15),
                     (11, 29), (19, 25), (26, 27), (9, 35),
                     (46, 51)):
            with self.subTest(pair=pair):
                self.assertEqual(by_number[pair[0]][3], by_number[pair[1]][3],
                                 f"Duplicate owner group introduced for {pair}")

    def test_no_duplicate_failure_classifier_module(self):
        self.assertFalse((ROOT / "src" / "forgeos" / "failure_types.py").exists())
        from forgeos.error_learning import classify_failure, fingerprint
        self.assertEqual(classify_failure("SyntaxError", passed=False)["category"], "syntax")
        self.assertEqual(fingerprint("AssertionError: mismatch")[0], "AssertionError")

    def test_documentation_keeps_windows_acceptance_as_gate(self):
        text = self.audit.lower()
        for word in ("production", "appcontainer", "checkpoint", "real windows"):
            self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main()
