import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forgeos.gguf_download import _exclusive_download_lock
from forgeos.managed_llama import _verified_model
from forgeos.model_inventory import (
    catalog_file_status, list_downloads, verify_download,
)
from forgeos.model_import import (
    import_local_gguf, imported_model_lock_path, purge_imported_gguf,
    unregister_imported_gguf,
)


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
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(result["provenance"], "unknown")
        self.assertEqual(result["source_path"], str(self.model.resolve()))
        self.assertEqual(self.model.stat().st_ino, original_inode)
        self.assertEqual(self.model.read_bytes(), self.data)
        self.assertFalse(sidecar.exists())
        self.assertEqual(_verified_model(
            self.model, directory=self.models)[1]["sha256"], result["sha256"])
        items = list_downloads(self.models)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "imported_not_reverified")
        self.assertEqual(items[0]["kind"], "imported")
        self.assertIsNone(items[0]["revision"])
        self.assertEqual(verify_download(self.model, directory=self.models)["status"],
                         "verified")

        self.model.write_bytes(self.data + b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            _verified_model(self.model, directory=self.models)

    def test_import_supports_read_only_source_directory(self):
        from forgeos import model_import
        home = self.root / "home"
        model_store = home / ".forgeos" / "models"
        original_open = Path.open

        def deny_source_metadata_writes(path, mode="r", *args, **kwargs):
            if path.parent == self.root and mode != "r" and mode != "rb":
                raise PermissionError("simulated read-only model directory")
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "home", return_value=home), \
                patch.object(Path, "open", deny_source_metadata_writes):
            result = import_local_gguf(self.model)
            verified = _verified_model(self.model)
            records = list_downloads()
        self.assertEqual(verified[1]["sha256"], result["sha256"])
        self.assertFalse(self.model.with_name(
            self.model.name + ".forgeos.json").exists())
        self.assertEqual(len(records), 1)
        self.assertTrue((model_store / ".forgeos-imports.json").is_file())
        self.assertTrue(model_import._import_lifecycle_lock_path(
            self.model.resolve(), model_store.resolve()).is_file())

    def test_imported_model_lock_rejects_reparse_source_and_storage(self):
        with patch("forgeos.model_import._is_reparse_point",
                   side_effect=lambda path: path == self.model):
            with self.assertRaisesRegex(ValueError, "reparse point"):
                imported_model_lock_path(self.model, directory=self.models)

        with patch("forgeos.model_import._is_reparse_point",
                   side_effect=lambda path: path == self.models):
            with self.assertRaisesRegex(ValueError, "reparse-point"):
                imported_model_lock_path(self.model, directory=self.models)

    def test_legacy_adjacent_sidecar_remains_compatible(self):
        from forgeos.model_import import IMPORT_FORMAT
        import_local_gguf(self.model, directory=self.models)
        sidecar = self.model.with_name(self.model.name + ".forgeos.json")
        identity = {
            "format": IMPORT_FORMAT,
            "source_path": str(self.model.resolve()),
            "file": self.model.name,
            "bytes": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "provenance": "unknown",
        }
        sidecar.write_text(json.dumps(identity), encoding="utf-8")
        self.assertEqual(_verified_model(
            self.model, directory=self.models)[1], identity)
        outcome = unregister_imported_gguf(self.model, directory=self.models)
        self.assertTrue(outcome["sidecar_preserved"])
        self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8")), identity)

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

    def test_cli_purges_imported_model_only_with_confirm_and_existing_database(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from forgeos.cli import main
        import_local_gguf(self.model, directory=self.models)
        database = self.root / "tasks.sqlite3"
        from forgeos.core import Store
        store = Store(database)
        store.close()
        with self.assertRaisesRegex(ValueError, "Explicit --confirm"):
            main(["--db", str(database), "model-purge-imported", str(self.model),
                  "--directory", str(self.models)])
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([
                "--db", str(database), "model-purge-imported", str(self.model),
                "--directory", str(self.models), "--confirm"]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "purged")
        self.assertFalse(self.model.exists())
        self.assertEqual(list_downloads(self.models), [])

    def test_import_purge_refuses_missing_or_unreadable_task_database(self):
        from forgeos.cli import main
        import_local_gguf(self.model, directory=self.models)
        with self.assertRaises((FileNotFoundError, ValueError)):
            main(["--db", str(self.root / "missing.sqlite3"),
                  "model-purge-imported", str(self.model), "--confirm"])
        self.assertTrue(self.model.exists())

    def test_unregister_preserves_file_and_reimport_restores_inventory(self):
        import_local_gguf(self.model, directory=self.models)
        sidecar = self.model.with_name(self.model.name + ".forgeos.json")
        outcome = unregister_imported_gguf(self.model, directory=self.models)
        self.assertTrue(outcome["weights_preserved"])
        self.assertFalse(outcome["sidecar_preserved"])
        self.assertEqual(self.model.read_bytes(), self.data)
        self.assertFalse(sidecar.exists())
        self.assertEqual(list_downloads(self.models), [])

        import_local_gguf(self.model, directory=self.models)
        self.assertEqual(list_downloads(self.models)[0]["status"],
                         "imported_not_reverified")

    def test_unregister_reports_progress_and_cancel_keeps_inventory(self):
        from threading import Event
        from forgeos.model_import import ImportUnregisterCancelled
        import_local_gguf(self.model, directory=self.models)
        progress = []
        event = Event()

        def cancel_after_progress(stage, done, total):
            progress.append((stage, done, total))
            event.set()

        with self.assertRaises(ImportUnregisterCancelled):
            unregister_imported_gguf(
                self.model, directory=self.models, cancel_event=event,
                progress_callback=cancel_after_progress)
        self.assertEqual(progress, [("verify", len(self.data), len(self.data))])
        self.assertTrue(self.model.exists())
        self.assertFalse(self.model.with_name(
            self.model.name + ".forgeos.json").exists())
        self.assertEqual(len(list_downloads(self.models)), 1)

    def test_unregister_refuses_runtime_lock_and_tampered_model(self):
        import_local_gguf(self.model, directory=self.models)
        lock = imported_model_lock_path(self.model, directory=self.models)
        with _exclusive_download_lock(lock):
            with self.assertRaisesRegex(RuntimeError, "active elsewhere"):
                unregister_imported_gguf(self.model, directory=self.models)
        self.model.write_bytes(self.data + b"changed")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            unregister_imported_gguf(self.model, directory=self.models)
        self.assertEqual(len(list_downloads(self.models)), 1)

    def test_import_purge_requires_confirmation_and_checkpoint_clearance(self):
        import_local_gguf(self.model, directory=self.models)
        for kwargs in (
                {"confirm": False, "checkpoint_check": lambda _sha: False},
                {"confirm": True, "checkpoint_check": lambda _sha: True},
                {"confirm": True, "checkpoint_check": lambda _sha: None}):
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, RuntimeError)):
                purge_imported_gguf(
                    self.model, directory=self.models, **kwargs)
        self.assertTrue(self.model.exists())
        self.assertFalse(self.model.with_name(
            self.model.name + ".forgeos.json").exists())
        self.assertEqual(len(list_downloads(self.models)), 1)

    def test_confirmed_import_purge_deletes_only_selected_import(self):
        sibling = self.root / "sibling.gguf"
        sibling.write_bytes(b"GGUF" + b"s" * 32)
        import_local_gguf(self.model, directory=self.models)
        import_local_gguf(sibling, directory=self.models)
        result = purge_imported_gguf(
            self.model, directory=self.models, confirm=True,
            checkpoint_check=lambda digest: digest == "unrelated")
        self.assertEqual(result["status"], "purged")
        self.assertEqual(result["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertFalse(self.model.exists())
        self.assertFalse(self.model.with_name(
            self.model.name + ".forgeos.json").exists())
        self.assertTrue(sibling.exists())
        self.assertEqual([row["path"] for row in list_downloads(self.models)],
                         [str(sibling.resolve())])

    def test_import_purge_refuses_checkpoint_and_active_lock(self):
        import_local_gguf(self.model, directory=self.models)
        with self.assertRaisesRegex(RuntimeError, "checkpoint references"):
            purge_imported_gguf(
                self.model, directory=self.models, confirm=True,
                checkpoint_check=lambda _sha: True)
        lock = imported_model_lock_path(self.model, directory=self.models)
        with _exclusive_download_lock(lock):
            with self.assertRaisesRegex(RuntimeError, "active elsewhere"):
                purge_imported_gguf(
                    self.model, directory=self.models, confirm=True,
                    checkpoint_check=lambda _sha: False)
        self.assertTrue(self.model.exists())

    def test_import_purge_retries_after_sidecar_or_model_removal(self):
        import_local_gguf(self.model, directory=self.models)
        sidecar = self.model.with_name(self.model.name + ".forgeos.json")
        unlink = Path.unlink

        def fail_model_once(path, *args, **kwargs):
            if path == self.model:
                raise OSError("injected model unlink failure")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_model_once):
            with self.assertRaisesRegex(OSError, "injected"):
                purge_imported_gguf(
                    self.model, directory=self.models, confirm=True,
                    checkpoint_check=lambda _sha: False)
        self.assertFalse(sidecar.exists())
        self.assertTrue(self.model.exists())
        result = purge_imported_gguf(
            self.model, directory=self.models, confirm=True,
            checkpoint_check=lambda _sha: False)
        self.assertEqual(result["status"], "purged")
        self.assertFalse(self.model.exists())
        self.assertEqual(list_downloads(self.models), [])

    def test_import_purge_retries_when_registry_publication_fails_after_unlink(self):
        import_local_gguf(self.model, directory=self.models)
        sidecar = self.model.with_name(self.model.name + ".forgeos.json")
        index = self.models / ".forgeos-imports.json"
        from forgeos import model_import
        replace = model_import.os.replace

        def fail_registry_once(source, destination):
            if Path(destination) == index:
                raise OSError("injected registry publication failure")
            return replace(source, destination)

        with patch.object(model_import.os, "replace", fail_registry_once):
            with self.assertRaisesRegex(OSError, "injected registry publication"):
                purge_imported_gguf(
                    self.model, directory=self.models, confirm=True,
                    checkpoint_check=lambda _sha: False)
        self.assertFalse(sidecar.exists())
        self.assertFalse(self.model.exists())
        self.assertEqual(len(json.loads(index.read_text())["imports"]), 1)

        result = purge_imported_gguf(
            self.model, directory=self.models, confirm=True,
            checkpoint_check=lambda _sha: False)
        self.assertEqual(result["status"], "purged")
        self.assertEqual(list_downloads(self.models), [])

    def test_import_purge_cancellation_preserves_all_import_files(self):
        from threading import Event
        from forgeos.model_import import ImportPurgeCancelled
        import_local_gguf(self.model, directory=self.models)
        event = Event()

        def cancel_after_hash(_stage, _completed, _total):
            event.set()

        with self.assertRaises(ImportPurgeCancelled):
            purge_imported_gguf(
                self.model, directory=self.models, confirm=True,
                checkpoint_check=lambda _sha: False, cancel_event=event,
                progress_callback=cancel_after_hash)
        self.assertTrue(self.model.exists())
        self.assertFalse(self.model.with_name(
            self.model.name + ".forgeos.json").exists())
        self.assertEqual(len(list_downloads(self.models)), 1)

    def test_reverify_refuses_active_managed_runtime_lock(self):
        import_local_gguf(self.model, directory=self.models)
        lock = imported_model_lock_path(self.model, directory=self.models)
        with _exclusive_download_lock(lock):
            with self.assertRaisesRegex(RuntimeError, "active elsewhere"):
                verify_download(self.model, directory=self.models)

    def test_reverify_detects_imported_link_count_change_during_hash(self):
        import_local_gguf(self.model, directory=self.models)
        original_stat = Path.stat
        state = {"hash_started": False}

        def report_link_after_hash(path, *args, **kwargs):
            metadata = original_stat(path, *args, **kwargs)
            if path == self.model and state["hash_started"]:
                return SimpleNamespace(st_mode=metadata.st_mode,
                                       st_size=metadata.st_size,
                                       st_nlink=2,
                                       st_mtime_ns=metadata.st_mtime_ns,
                                       st_ino=metadata.st_ino)
            return metadata

        def finish_hash(*_args, **_kwargs):
            state["hash_started"] = True
            return hashlib.sha256(self.data).hexdigest()

        with patch.object(Path, "stat", new=report_link_after_hash), \
             patch("forgeos.model_inventory._digest", side_effect=finish_hash):
            with self.assertRaisesRegex(RuntimeError, "changed while verifying"):
                verify_download(self.model, directory=self.models)

    def test_import_inventory_marks_hardlinked_model_unsafe(self):
        import_local_gguf(self.model, directory=self.models)
        original_stat = Path.stat

        def report_hardlink(path, *args, **kwargs):
            metadata = original_stat(path, *args, **kwargs)
            if path == self.model:
                return SimpleNamespace(st_mode=metadata.st_mode,
                                       st_size=metadata.st_size,
                                       st_nlink=2)
            return metadata

        with patch.object(Path, "stat", new=report_hardlink):
            records = list_downloads(self.models)
        self.assertEqual(records[0]["status"], "unsafe_path")
        file_info = {"path": "candidate.gguf", "sha256": records[0]["sha256"]}
        self.assertIn("linked", catalog_file_status(
            "Other/Repo", "a" * 40, file_info, records))


if __name__ == "__main__":
    unittest.main()
