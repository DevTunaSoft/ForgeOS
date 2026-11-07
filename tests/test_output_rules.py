"""Custom output filter and evidence-preservation regression tests."""
import json
from pathlib import Path
import tempfile
import unittest
from forgeos.output_rules import TrustedOutputRules, deduplicate_output

class OutputRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "rules.json"
        self.rules = TrustedOutputRules(self.config, self.root / "trust.json")

    def write(self, rules):
        self.config.write_text(json.dumps({"version": 1, "rules": rules}))

    def test_noncritical_repetition(self):
        raw = "Downloading packages\n" * 15 + "ERROR: failed build\n" * 6
        summary, removed = deduplicate_output(raw, "npm-test")
        self.assertEqual(removed, 14)
        self.assertIn("14 repeated", summary)
        self.assertEqual(summary.count("ERROR: failed build"), 6)

    def test_short_noncritical_repetition_kept_without_rule(self):
        summary, count = deduplicate_output("progress\n" * 3, "npm-test")
        self.assertEqual(summary, "progress\n" * 3)
        self.assertEqual(count, 0)

    def test_untrusted_and_modified_configs_skipped(self):
        rule = {"preset": "npm-test", "contains": "progress", "min_repeats": 3}
        self.write([rule])
        self.assertEqual(self.rules.enabled_rules(), ([], "untrusted-or-modified"))
        self.assertEqual(len(self.rules.trust_config()), 64)
        enabled, status = self.rules.enabled_rules()
        self.assertEqual(status, "trusted")
        summary, removed = deduplicate_output("progress\n" * 3, "npm-test", enabled)
        self.assertEqual(removed, 2)
        self.write([{"preset": "*", "contains": "progress", "min_repeats": 4}])
        self.assertEqual(self.rules.enabled_rules(), ([], "untrusted-or-modified"))

    def test_trusted_rule_uses_same_bytes_for_hash_and_parse(self):
        from unittest.mock import patch
        from forgeos import output_rules
        self.write([{"preset":"npm-test","contains":"progress","min_repeats":3}])
        self.rules.trust_config()
        real_read = output_rules._read_safe
        reads = []
        def audited_read(path):
            reads.append(Path(path))
            return real_read(path)
        with patch("forgeos.output_rules._read_safe", side_effect=audited_read):
            enabled, status = self.rules.enabled_rules()
        self.assertEqual(status, "trusted")
        self.assertEqual(len(enabled), 1)
        self.assertEqual(sum(p == self.config for p in reads), 1)

    def test_unexpected_schema_and_execution_fields_rejected(self):
        for item in (
            {"preset": "npm-test", "contains": "x", "min_repeats": 3},
            {"preset": "npm-test", "contains": "progress", "min_repeats": True},
            {"preset": "npm-test", "contains": "progress", "min_repeats": 2},
            {"preset": "npm-test", "contains": "progress", "min_repeats": 3,
             "command": "rm -rf /"}
        ):
            self.write([item])
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.rules.trust_config()

    def test_symlinked_configuration_rejected(self):
        real = self.root / "real.json"
        real.write_text(json.dumps({"version":1, "rules":[]}))
        link = self.root / "symlink.json"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink not supported")
        trust = TrustedOutputRules(link, self.root / "trust.json")
        self.assertEqual(trust.status(), "invalid")
        with self.assertRaises(ValueError):
            trust.trust_config()

    def test_unknown_repeated_lines_are_not_assumed_safe_noise(self):
        raw = "HTTP 500 database connection dropped\n" * 15
        result, removed = deduplicate_output(raw, "npm-test")
        self.assertEqual(result, raw)
        self.assertEqual(removed, 0)

    def test_unicode_and_nonadjacent_output(self):
        raw = "INFO Ω漢字\n" * 9 + "different\n" + "INFO Ω漢字\n" * 2
        text, removed = deduplicate_output(raw, "python-unittest")
        self.assertEqual(removed, 8)
        self.assertTrue(text.endswith("different\nINFO Ω漢字\nINFO Ω漢字\n"))

if __name__ == "__main__":
    unittest.main()
