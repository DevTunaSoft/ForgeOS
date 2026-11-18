import os
import tempfile
import unittest
from pathlib import Path

import subprocess

from forgeos.test_runtime import _refuse_links
from forgeos.windows_containment import remove_reparse_points


class SnapshotLinkRefusalTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "junctions are Windows-only")
    def test_refuses_a_junction_and_leaves_its_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "snapshot"
            root.mkdir()
            target = Path(tmp) / "outside"
            target.mkdir()
            (target / "keep.txt").write_text("keep", encoding="utf-8")
            link = root / "junction"
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           check=True, capture_output=True)
            with self.assertRaises(ValueError):
                _refuse_links(root)
            remove_reparse_points(root)
            self.assertFalse(os.path.lexists(link))
            self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_unlinking_a_symlink_keeps_its_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "snapshot"
            root.mkdir()
            target = Path(tmp) / "outside"
            target.mkdir()
            (target / "keep.txt").write_text("keep", encoding="utf-8")
            try:
                os.symlink(target, root / "link", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not available here")
            remove_reparse_points(root)
            self.assertFalse(os.path.lexists(root / "link"))
            self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep")

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
