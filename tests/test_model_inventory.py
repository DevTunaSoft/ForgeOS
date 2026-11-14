"""Bounded offline GGUF inventory tests, no Hugging Face network or inference."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.gguf_download import _exclusive_download_lock
from forgeos.model_inventory import (
    VerificationCancelled, catalog_file_status, list_downloads,
    storage_usage, verify_download,
)


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

    def test_catalog_status_matches_exact_revision_hash_and_imported_bytes(self):
        file_info = {"path": self.path.name, "sha256": self.sha}
        records = list_downloads(self.root)
        self.assertIn("not reverified", catalog_file_status(
            "Owner/CodeModel", self.version, file_info, records))
        different = dict(file_info, sha256="f" * 64)
        self.assertIn("different SHA-256", catalog_file_status(
            "Owner/CodeModel", self.version, different, records))
        imported = [{"repo": "Local import (provenance unknown)",
                     "revision": None, "file": "elsewhere.gguf",
                     "sha256": self.sha, "status": "imported_not_reverified",
                     "kind": "imported"}]
        self.assertIn("source unknown", catalog_file_status(
            "Other/Repo", "b" * 40, file_info, imported))
        missing_import = [dict(imported[0], status="missing")]
        self.assertIn("local file is missing", catalog_file_status(
            "Other/Repo", "b" * 40, file_info, missing_import))
        size_mismatch_import = [dict(imported[0], status="size_mismatch")]
        self.assertIn("local size differs", catalog_file_status(
            "Other/Repo", "b" * 40, file_info, size_mismatch_import))
        self.assertEqual("not in local model inventory", catalog_file_status(
            "Other/Repo", "b" * 40, dict(file_info, sha256=None), []))

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

    def test_storage_usage_counts_active_partial_retired_and_quarantined_weights(self):
        partial = self.parent / "next.gguf.part"
        partial.write_bytes(b"partial")
        quarantine = self.parent / "bad.gguf.part.corrupt-123"
        quarantine.write_bytes(b"bad")
        retired_dir = self.root / ".forgeos-retired" / ("b" * 32)
        retired_dir.mkdir(parents=True)
        retired = retired_dir / "weights.gguf"
        retired.write_bytes(b"retired")
        (self.parent / "notes.txt").write_bytes(b"ignored")

        with patch("forgeos.model_inventory._digest") as hashing:
            usage = storage_usage(self.root)
        hashing.assert_not_called()
        self.assertEqual(usage, {
            "bytes": (len(self.content) + len(b"partial") + len(b"bad")
                      + len(b"retired")),
            "files": 4,
        })

    def test_storage_usage_fails_closed_on_reparse_point_entries(self):
        redirected = self.root / "redirected"
        redirected.mkdir()
        with patch("forgeos.model_inventory._is_reparse_point",
                   side_effect=lambda path: path == redirected):
            with self.assertRaisesRegex(RuntimeError, "reparse-point entry"):
                storage_usage(self.root)

    def test_explicit_verify_streams_bytes_and_detects_corruption(self):
        proof = verify_download(self.path, directory=self.root)
        self.assertEqual(proof["status"], "verified")
        self.assertEqual(proof["sha256"], self.sha)
        self.path.write_bytes(b"GGUF" + b"X" * 60)
        self.assertEqual(list_downloads(self.root)[0]["status"],
                         "present_not_reverified")
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            verify_download(self.path, directory=self.root)

    def test_explicit_verify_refuses_active_model_lifecycle(self):
        lock = self.path.with_name(self.path.name + ".lock")
        with _exclusive_download_lock(lock):
            with self.assertRaisesRegex(RuntimeError, "active elsewhere"):
                verify_download(self.path, directory=self.root)

    def test_explicit_verify_reports_progress_and_supports_cancel(self):
        progress = []
        result = verify_download(
            self.path, directory=self.root,
            progress_callback=lambda *event: progress.append(event))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(progress[-1], ("verify", len(self.content), len(self.content)))

        from threading import Event
        cancelled = Event()
        def cancel_after_first_chunk(_stage, _done, _total):
            cancelled.set()
        with self.assertRaisesRegex(VerificationCancelled, "cancelled"):
            verify_download(self.path, directory=self.root,
                            cancel_event=cancelled,
                            progress_callback=cancel_after_first_chunk)

    def test_explicit_verify_validates_controls_before_touching_storage(self):
        with patch("forgeos.model_inventory._safe_storage_root") as root:
            with self.assertRaisesRegex(TypeError, "threading.Event"):
                verify_download(self.path, cancel_event=object())
            with self.assertRaisesRegex(TypeError, "callable"):
                verify_download(self.path, progress_callback=object())
        root.assert_not_called()

    def test_explicit_verify_accepts_noncanonical_but_contained_path(self):
        # Equivalent paths must compare to the canonical inventory root,
        # not reject legitimate GGUF weights on Windows alias spellings.
        alias = self.parent / "unused" / ".." / self.path.name
        self.assertEqual(
            verify_download(alias, directory=self.root)["status"], "verified")
        self.assertEqual(
            verify_download(self.path, directory=self.root)["sha256"], self.sha)

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

    def test_cli_inventory_and_explicit_verification_use_existing_code(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from forgeos.cli import main
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "models-local", "--directory", str(self.root)]), 0)
        items = json.loads(output.getvalue())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "present_not_reverified")
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "model-verify-local", str(self.path),
                "--directory", str(self.root)]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "verified")

    def test_no_automatic_network_or_model_execution(self):
        with patch("forgeos.huggingface_catalog.search_gguf") as remote, \
             patch("forgeos.managed_llama.subprocess.Popen") as spawn:
            list_downloads(self.root)
            verify_download(self.path, directory=self.root)
            remote.assert_not_called()
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
