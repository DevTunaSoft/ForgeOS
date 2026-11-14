"""Integrity and packaging checks for the supplied ForgeOS brand kit."""
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from forgeos.gui import application_icon_path


ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "assets" / "branding" / "ForgeOS_Master_Branding_Kit"
READY = KIT / "04_Application_Ready"


class BrandingTests(unittest.TestCase):
    def test_all_supplied_brand_assets_match_the_checksum_manifest(self):
        manifest = KIT / "SHA256SUMS.txt"
        self.assertTrue(manifest.is_file())
        checked = 0
        for row in manifest.read_text(encoding="utf-8").splitlines():
            if not row.strip():
                continue
            expected, relative = row.split(maxsplit=1)
            asset = KIT / relative.lstrip("* ")
            self.assertTrue(asset.is_file(), relative)
            digest = hashlib.sha256()
            with asset.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            self.assertEqual(digest.hexdigest(), expected, relative)
            checked += 1
        self.assertEqual(checked, 64)

    def test_source_application_icon_and_branding_references_exist(self):
        self.assertEqual(
            application_icon_path(),
            READY / "ForgeOS_Transparent_Icon_128.png")
        self.assertTrue((READY / "ForgeOS_Windows_Icon.ico").is_file())
        self.assertTrue((READY / "GitHub_Banner.png").is_file())

    def test_frozen_application_icon_resolves_from_bundle_data(self):
        with tempfile.TemporaryDirectory() as folder:
            branding = Path(folder) / "branding"
            branding.mkdir()
            bundled = branding / "ForgeOS_Transparent_Icon_128.png"
            shutil.copyfile(
                READY / "ForgeOS_Transparent_Icon_128.png", bundled)
            with patch.object(sys, "_MEIPASS", folder, create=True):
                self.assertEqual(application_icon_path(), bundled)

    def test_frozen_build_and_readme_include_the_supplied_branding(self):
        workflow = (ROOT / ".github" / "workflows" /
                    "windows-exe.yml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "WINDOWS_BUILD.md").read_text(encoding="utf-8")
        self.assertIn("--icon \"assets/branding/", workflow)
        self.assertIn("ForgeOS_Transparent_Icon_128.png;branding", workflow)
        self.assertIn("ForgeOS branded banner", readme)
        self.assertIn("--icon \"assets/branding/", guide)


if __name__ == "__main__":
    unittest.main()
