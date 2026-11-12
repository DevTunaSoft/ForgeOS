"""Unit tests for the read-only repository handoff snapshot."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "handoff_snapshot.py"
spec = importlib.util.spec_from_file_location("handoff_snapshot", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HandoffSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "commit",
                        "-q", "--allow-empty", "-m", "baseline"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_snapshot(self):
        data = module.snapshot(self.root)
        self.assertFalse(data["dirty"])
        self.assertEqual(len(data["head"]), 40)
        self.assertEqual(data["ci"][:10], "UNVERIFIED")
        self.assertNotIn("generated_at_utc", data)

    def test_dirty_snapshot_does_not_leak_filenames(self):
        (self.root / "sensitive-private-name.txt").write_text("secret")
        data = module.snapshot(self.root)
        self.assertTrue(data["dirty"])
        self.assertEqual(data["changed_entry_count"], 1)
        self.assertNotIn("sensitive-private-name", str(data))

    def test_non_repository_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(ValueError):
                module.snapshot(Path(other))

    def test_subdirectory_rejected(self):
        child = self.root / "child"
        child.mkdir()
        with self.assertRaises(ValueError):
            module.snapshot(child)


if __name__ == "__main__":
    unittest.main()
