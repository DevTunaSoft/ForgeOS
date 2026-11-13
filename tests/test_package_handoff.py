"""Recovery package security and integrity regression tests."""
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "package_handoff.py"
spec = importlib.util.spec_from_file_location("package_handoff", SCRIPT)
pkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg)


class RecoveryPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        for rel in pkg.DOCS + pkg.TOOLS:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(rel + "\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)
        self.out = Path(self.tmp.name) / "recovery.zip"

    def test_archive_manifest_and_hashes(self):
        result = pkg.create(self.root, self.out)
        self.assertEqual(result["status"], "PASS")
        with zipfile.ZipFile(self.out) as z:
            manifest = json.loads(z.read("MANIFEST.json"))
            self.assertEqual(manifest["source_sha"], result["source_sha"])
            for name, item in manifest["files"].items():
                self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(), item["sha256"])
            guide = z.read("START_HERE.txt").decode("utf-8")
            self.assertGreaterEqual(len(guide.splitlines()), 5)
            self.assertIn("START_HERE.txt", z.namelist())

    def test_refuses_overwrite(self):
        pkg.create(self.root, self.out)
        with self.assertRaises(FileExistsError):
            pkg.create(self.root, self.out)

    def test_archive_is_reproducible(self):
        first = pkg.create(self.root, self.out)
        second_path = self.out.with_name("second.zip")
        second = pkg.create(self.root, second_path)
        self.assertEqual(first["package_sha256"], second["package_sha256"])
        with zipfile.ZipFile(self.out) as archive:
            self.assertTrue(all(member.date_time == (1980, 1, 1, 0, 0, 0)
                                for member in archive.infolist()))

    def test_refuses_dirty_checkout(self):
        (self.root / "docs" / "HANDOFF.md").write_text("changed")
        with self.assertRaises(ValueError):
            pkg.create(self.root, self.out)

    def test_refuses_output_inside_repository(self):
        with self.assertRaises(ValueError):
            pkg.create(self.root, self.root / "bundle.zip")


if __name__ == "__main__":
    unittest.main()
