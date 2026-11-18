import unittest

from forgeos.brand import ICON_FILE, PALETTE, WORDMARK_DARK_FILE, brand_asset_path


class BrandTests(unittest.TestCase):
    def test_packaged_brand_assets_exist(self):
        for name in (ICON_FILE, WORDMARK_DARK_FILE):
            path = brand_asset_path(name)
            self.assertIsNotNone(path, name)
            self.assertGreater(path.stat().st_size, 0)

    def test_palette_matches_brand_standards(self):
        self.assertEqual(PALETTE["midnight"], "#080D1A")
        self.assertEqual(PALETTE["forge"], "#FF6F1A")
        self.assertEqual(PALETTE["ember"], "#FFB44A")
