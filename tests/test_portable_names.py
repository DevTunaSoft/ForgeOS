"""Canonical Windows-portable name rules and agreement between ForgeOS validators."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from forgeos import gguf_download, runtime_installer
from forgeos.engine import EditError, parse_edits
from forgeos.portable_names import has_trailing_dot_or_space, is_device_name

DEVICE_STEMS = ("CON", "nul", "Com1", "lpt9", "COM0", "LPT0")


class PortableNameRuleTests(unittest.TestCase):
    def test_device_stems_are_devices_with_or_without_extension(self):
        for part in ("CON", "con.txt", "Nul.py", "PRN.md", "aux.json", "CONIN$.log",
                     "conout$", "COM1", "com9.json", "LPT0.md", "COM0.py", "lpt9",
                     "COM¹.py", "com²", "LPT³.txt", "NUL .txt", "CON  .md"):
            with self.subTest(part=part):
                self.assertTrue(is_device_name(part))

    def test_ordinary_names_are_not_devices(self):
        for part in ("console.py", "aux_helpers.py", "COM10.py", "LPT10", "com", "lpt",
                     "nul_", "cons.txt", "a.nul", "prn1", "COM 1.py", "nullable.md",
                     "conin.txt", "conout"):
            with self.subTest(part=part):
                self.assertFalse(is_device_name(part))

    def test_trailing_dot_or_space_is_detected(self):
        for part in ("docs.", "docs ", "docs. ", ".github.", "NUL ."):
            with self.subTest(part=part):
                self.assertTrue(has_trailing_dot_or_space(part))
        for part in ("", ".github", "a.b", "a b", "docs"):
            with self.subTest(part=part):
                self.assertFalse(has_trailing_dot_or_space(part))


class ValidatorAgreementTests(unittest.TestCase):
    """The edit engine, GGUF downloader and runtime installer share one device rule."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _engine_parse(self, name):
        return parse_edits(self.root, json.dumps({"files": [{"path": name, "content": "x"}]}))

    def test_device_names_are_rejected_by_every_validator(self):
        for stem in DEVICE_STEMS:
            with self.subTest(stem=stem):
                with self.assertRaises(EditError):
                    self._engine_parse(f"docs/{stem}.md")
                with self.assertRaises(ValueError):
                    gguf_download._safe_name(f"models/{stem}.gguf")
                with self.assertRaises(RuntimeError):
                    runtime_installer._safe_entry(f"bin/{stem}.dll")

    def test_space_before_extension_is_rejected_by_every_validator(self):
        with self.assertRaises(EditError):
            self._engine_parse("docs/NUL .md")
        with self.assertRaises(ValueError):
            gguf_download._safe_name("models/NUL .gguf")
        # The installer previously compared the raw stem, so "NUL .dll" passed.
        with self.assertRaises(RuntimeError):
            runtime_installer._safe_entry("bin/NUL .dll")

    def test_ordinary_names_are_accepted_by_every_validator(self):
        self.assertEqual(len(self._engine_parse("tools/COM10.py")), 1)
        self.assertEqual(len(self._engine_parse("console_tools/aux_helpers.py")), 1)
        self.assertEqual(gguf_download._safe_name("models/coder-COM10.gguf"),
                         ("models", "coder-COM10.gguf"))
        self.assertEqual(runtime_installer._safe_entry("bin/COM10.dll"), ("bin", "COM10.dll"))


if __name__ == "__main__":
    unittest.main()
