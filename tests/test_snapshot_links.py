import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from forgeos.test_runtime import _refuse_links
from forgeos.windows_containment import remove_reparse_points, system_exe


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
            made = subprocess.run(
                [system_exe("cmd.exe"), "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True)
            if made.returncode != 0:
                self.skipTest("junctions could not be created here")
            try:
                with self.assertRaises(ValueError):
                    _refuse_links(root)
                remove_reparse_points(root)
                self.assertFalse(os.path.lexists(link))
                self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep")
            finally:
                if os.path.lexists(link):
                    os.rmdir(link)  # removes the junction, not its target

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

    def test_refuses_a_file_symlink_inside_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "snapshot"
            root.mkdir()
            target = Path(tmp) / "outside.txt"
            target.write_text("x", encoding="utf-8")
            try:
                os.symlink(target, root / "link.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not available here")
            with self.assertRaises(ValueError):
                _refuse_links(root)

    def test_refuses_a_link_as_the_root_and_leaves_its_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "outside"
            target.mkdir()
            (target / "keep.txt").write_text("keep", encoding="utf-8")
            link = Path(tmp) / "snapshot"
            try:
                os.symlink(target, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not available here")
            with self.assertRaises(ValueError):
                remove_reparse_points(link)
            with self.assertRaises(ValueError):
                _refuse_links(link)
            self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_accepts_a_snapshot_without_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "a" / "file.txt").write_text("x", encoding="utf-8")
            _refuse_links(root)


if __name__ == "__main__":
    unittest.main()
