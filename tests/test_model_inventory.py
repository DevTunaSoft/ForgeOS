"""Bounded offline GGUF inventory tests, no Hugging Face network or inference."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.model_inventory import list_downloads, verify_download


class GGUFInventoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="forgeos-gguf-inventory-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.version = "a" * 40
        self.parent = self.root / "Owner" / "CodeModel" / self.version
        self.parent.mkdir(parents=True)
        self.path = self.parent / "model.Q4_K_M.gguf"
        self.content = b"GGUF" + b"\x00" * 60
        self.path.write_bytes(self.content)
        self.sha = hashlib.sha256(self.content).hexdigest()
        self.manifest = self.path.with_name(self.path.name + ".forgeos.json")
        self.write_manifest()

    def write_manifest(self, **changes):
        data = {"repo": "Owner/CodeModel", "revision": self.version,
                "file": self.path.name, "bytes": len(self.content),
                "sha256": self.sha}
        data.update(changes)
        self.manifest.write_text(json.dumps(data), encoding="utf-8")

    def test_metadata_only_listing_does_not_hash_model_weight_bytes(self):
        with patch("forgeos.model_inventory._digest") as hashing:
            items = list_downloads(self.root)
        hashing.assert_not_called()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "present_not_reverified")
        self.assertEqual(items[0]["repo"], "Owner/CodeModel")
        self.assertEqual(items[0]["sha256"], self.sha)

    def test_explicit_verify_streams_bytes_and_detects_corruption(self):
        proof = verify_download(self.path, directory=self.root)
        self.assertEqual(proof["status"], "verified")
        self.assertEqual(proof["sha256"], self.sha)
        self.path.write_bytes(b"GGUF" + b"X" * 60)
        self.assertEqual(list_downloads(self.root)[0]["status"],
                         "present_not_reverified")
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            verify_download(self.path, directory=self.root)

    def test_missing_or_different_size_does_not_appear_verified(self):
        self.path.write_bytes(b"GGUF")
        self.assertEqual(list_downloads(self.root)[0]["status"], "size_mismatch")
        self.path.unlink()
        self.assertEqual(list_downloads(self.root)[0]["status"], "missing")
        with self.assertRaises(ValueError):
            verify_download(self.path, directory=self.root)

    def test_bad_manifest_fails_closed_without_importing_weight(self):
        self.write_manifest(repo="Other/Model")
        self.assertEqual(list_downloads(self.root), [])
        with self.assertRaisesRegex(ValueError, "storage path"):
            verify_download(self.path, directory=self.root)
        self.write_manifest(sha256="0" * 20)
        self.assertEqual(list_downloads(self.root), [])
        self.write_manifest()
        self.manifest.write_bytes(b"X" * 6000)
        self.assertEqual(list_downloads(self.root), [])

    def test_prevent_symlink_from_becoming_installed_model(self):
        self.path.unlink()
        outside = self.root / "outside.gguf"
        outside.write_bytes(self.content)
        try:
            self.path.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Cannot create symlink in this environment")
        self.assertEqual(list_downloads(self.root), [])
        with self.assertRaises(ValueError):
            verify_download(self.path, directory=self.root)

    def test_scan_limit_fails_closed_for_many_entries(self):
        for index in range(5):
            (self.root / f"noise-{index}.txt").write_text("x")
        with patch("forgeos.model_inventory.MAX_SCAN_ENTRIES", 3):
            with self.assertRaisesRegex(RuntimeError, "scan limit"):
                list_downloads(self.root)

    def test_no_automatic_network_or_model_execution(self):
        with patch("forgeos.huggingface_catalog.search_gguf") as remote, \
             patch("forgeos.managed_llama.subprocess.Popen") as spawn:
            list_downloads(self.root)
            verify_download(self.path, directory=self.root)
            remote.assert_not_called()
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
