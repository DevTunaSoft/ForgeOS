import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from forgeos.gguf_download import _exclusive_download_lock
from forgeos.managed_llama import _verified_model
from forgeos.model_inventory import list_downloads, verify_download
from forgeos.model_import import import_local_gguf, unregister_imported_gguf


class LocalModelImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forgeos-import-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.models = self.root / "models"
        self.model = self.root / "existing.gguf"
        self.data = b"GGUF" + b"x" * 64
        self.model.write_bytes(self.data)

    def test_import_records_sha_without_copying_and_runtime_reverifies(self):
        original_inode = self.model.stat().st_ino
        result = import_local_gguf(self.model, directory=self.models)
        sidecar = self.model.with_name(self.model.name + ".forgeos.json")
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(record["provenance"], "unknown")
        self.assertEqual(record["source_path"], str(self.model.resolve()))
        self.assertEqual(self.model.stat().st_ino, original_inode)
        self.assertEqual(self.model.read_bytes(), self.data)
        self.assertEqual(_verified_model(self.model)[1], record)
        items = list_downloads(self.models)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "imported_not_reverified")
        self.assertEqual(items[0]["kind"], "imported")
        self.assertIsNone(items[0]["revision"])
        self.assertEqual(verify_download(self.model, directory=self.models)["status"],
                         "verified")

        self.model.write_bytes(self.data + b"changed")
        with self.assertRaisesRegex(ValueError, "identity or file size"):
            _verified_model(self.model)

    def test_import_reports_bounded_progress_and_cancels_before_publication(self):
        from threading import Event
        from forgeos.model_import import ImportCancelled
        progress = []
        cancel = Event()
        def cancel_after_chunk(stage, done, total):
            progress.append((stage, done, total))
            cancel.set()
        with self.assertRaisesRegex(ImportCancelled, "no identity was published"):
            import_local_gguf(
                self.model, directory=self.models, cancel_event=cancel,
                progress_callback=cancel_after_chunk)
        self.assertEqual(progress, [("verify", len(self.data), len(self.data))])
        self.assertFalse(self.model.with_name(
            self.model.name + ".forgeos.json").exists())
        self.assertFalse((self.models / ".forgeos-imports.json").exists())
        self.assertEqual(self.model.read_bytes(), self.data)

    def test_import_validates_cancel_and_progress_controls_before_storage(self):
        with self.assertRaisesRegex(TypeError, "threading.Event"):
            import_local_gguf(self.model, directory=self.models, cancel_event=True)
        with self.assertRaisesRegex(TypeError, "callable"):
            import_local_gguf(
                self.model, directory=self.models, progress_callback=object())
        self.assertFalse(self.models.exists())

    def test_import_does_not_overwrite_an_existing_sidecar(self):
        sidecar = self.model.with_name(self.model.name + ".forgeos.json")
        sidecar.write_text("keep this", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            import_local_gguf(self.model, directory=self.models)
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "keep this")

    def test_invalid_header_and_hardlinks_are_refused(self):
        self.model.write_bytes(b"not a GGUF file" * 4)
        with self.assertRaisesRegex(ValueError, "GGUF header"):
            import_local_gguf(self.model, directory=self.models)
        linked = self.root / "linked.gguf"
        linked.write_bytes(b"GGUF" + b"y" * 32)
        alias = self.root / "linked-alias.gguf"
        try:
            alias.hardlink_to(linked)
        except (OSError, NotImplementedError):
            self.skipTest("Hard links are unavailable")
        with self.assertRaisesRegex(ValueError, "single-link"):
            import_local_gguf(linked, directory=self.models)

    def test_corrupt_import_index_fails_closed_and_does_not_list_model(self):
        import_local_gguf(self.model, directory=self.models)
        (self.models / ".forgeos-imports.json").write_text(
            "{broken", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "unreadable"):
            list_downloads(self.models)

    def test_failed_inventory_registration_rolls_back_only_new_sidecar(self):
        self.models.mkdir()
        (self.models / ".forgeos-imports.json").write_text(
            "{broken", encoding="utf-8")
        sidecar = self.model.with_name(self.model.name + ".forgeos.json")
        with self.assertRaisesRegex(ValueError, "unreadable"):
            import_local_gguf(self.model, directory=self.models)
        self.assertFalse(sidecar.exists())
        self.assertEqual(self.model.read_bytes(), self.data)

    def test_cli_lists_and_verifies_imported_models(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from forgeos.cli import main
        import_local_gguf(self.model, directory=self.models)
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "models-local", "--directory", str(self.models)]), 0)
        items = json.loads(output.getvalue())
        self.assertEqual(items[0]["kind"], "imported")
        self.assertEqual(items[0]["status"], "imported_not_reverified")
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "model-verify-local", str(self.model),
                "--directory", str(self.models)]), 0)
        self.assertEqual(json.loads(output.getvalue())["provenance"], "unknown")

    def test_unregister_preserves_file_and_sidecar_and_reimport_restores_inventory(self):
        import_local_gguf(self.model, directory=self.models)
        sidecar = self.model.with_name(self.model.name + ".forgeos.json")
        sidecar_bytes = sidecar.read_bytes()
        outcome = unregister_imported_gguf(self.model, directory=self.models)
        self.assertTrue(outcome["weights_preserved"])
        self.assertTrue(outcome["sidecar_preserved"])
        self.assertEqual(self.model.read_bytes(), self.data)
        self.assertEqual(sidecar.read_bytes(), sidecar_bytes)
        self.assertEqual(list_downloads(self.models), [])

        import_local_gguf(self.model, directory=self.models)
        self.assertEqual(list_downloads(self.models)[0]["status"],
                         "imported_not_reverified")

    def test_unregister_refuses_runtime_lock_and_tampered_model(self):
        import_local_gguf(self.model, directory=self.models)
        lock = self.model.with_name(self.model.name + ".lock")
        with _exclusive_download_lock(lock):
            with self.assertRaisesRegex(RuntimeError, "active elsewhere"):
                unregister_imported_gguf(self.model, directory=self.models)
        self.model.write_bytes(self.data + b"changed")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            unregister_imported_gguf(self.model, directory=self.models)
        self.assertEqual(len(list_downloads(self.models)), 1)

    def test_reverify_refuses_active_managed_runtime_lock(self):
        import_local_gguf(self.model, directory=self.models)
        lock = self.model.with_name(self.model.name + ".lock")
        with _exclusive_download_lock(lock):
            with self.assertRaisesRegex(RuntimeError, "active elsewhere"):
                verify_download(self.model, directory=self.models)


if __name__ == "__main__":
    unittest.main()
