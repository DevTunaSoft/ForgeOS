import os
import tempfile
import unittest
from pathlib import Path

from forgeos.test_runtime import _refuse_links


class SnapshotLinkRefusalTests(unittest.TestCase):
    def test_refuses_a_directory_symlink_inside_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "snapshot"
            root.mkdir()
            target = Path(tmp) / "outside"
            target.mkdir()
            try:
                os.symlink(target, root / "link", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not available here")
            with self.assertRaises(ValueError):
                _refuse_links(root)

    def test_accepts_a_snapshot_without_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "a" / "file.txt").write_text("x", encoding="utf-8")
            _refuse_links(root)


if __name__ == "__main__":
    unittest.main()
