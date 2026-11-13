"""Crash injection and security regressions for reversible local model retirement."""
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
from forgeos.model_inventory import list_downloads, verify_download
from forgeos.model_retirement import retire_download, restore_download, list_retired


class ModelRetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model = self.root / "Owner" / "Code" / ("a"*40) / "coder.gguf"
        self.model.parent.mkdir(parents=True)
        self.data = b"GGUF" + b"x"*60
        self.model.write_bytes(self.data)
        self.manifest = self.model.with_name("coder.gguf.forgeos.json")
        self.info = {"repo": "Owner/Code", "revision": "a"*40, "file": "coder.gguf",
                     "bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}
        self.manifest.write_text(json.dumps(self.info))

    def retire(self):
        return retire_download(self.model, directory=self.root)

    def restore(self, token):
        return restore_download(token, directory=self.root)

    def test_retired_inventory_rejects_unbounded_invalid_entry_scans(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from forgeos.model_inventory import MAX_SCAN_ENTRIES

        (self.root / ".forgeos-retired").mkdir()

        @contextmanager
        def synthetic_entries(_folder):
            # Emulate hostile directory clutter without allocating or
            # creating thousands of files on the Windows CI host.
            yield (SimpleNamespace(name=f"ignored-{index}")
                   for index in range(MAX_SCAN_ENTRIES + 1))

        with patch("forgeos.model_retirement.os.scandir",
                   side_effect=synthetic_entries):
            with self.assertRaisesRegex(RuntimeError, "bounded scan limit"):
                list_retired(self.root)

    def test_full_recovery_and_inventory_hides_retired_weight_without_network(self):
        with patch("forgeos.huggingface_catalog.search_gguf") as remote, \
             patch("forgeos.managed_llama.subprocess.Popen") as spawned:
            ticket = self.retire()
            self.assertFalse(self.model.exists())
            self.assertFalse(self.manifest.exists())
            self.assertEqual(list_downloads(self.root), [])
            self.assertEqual(list_retired(self.root)[0]["status"], "retired")
            self.assertEqual(self.restore(ticket["id"])["status"], "restored")
            self.assertEqual(verify_download(
                self.model, directory=self.root)["sha256"], self.info["sha256"])
            self.assertEqual(list_retired(self.root), [])
            remote.assert_not_called()
            spawned.assert_not_called()

    def test_retirement_reports_hash_progress_and_cancellation_keeps_files(self):
        from forgeos.model_inventory import VerificationCancelled
        progress = []
        event = Event()

        def cancel_after_progress(stage, completed, total):
            progress.append((stage, completed, total))
            event.set()

        with self.assertRaises(VerificationCancelled):
            retire_download(self.model, directory=self.root,
                            cancel_event=event,
                            progress_callback=cancel_after_progress)
        self.assertEqual(progress, [("verify", len(self.data), len(self.data))])
        self.assertEqual(self.model.read_bytes(), self.data)
        self.assertTrue(self.manifest.exists())
        self.assertEqual(list_retired(self.root), [])

    def test_restore_cancellation_after_moves_keeps_retryable_journal(self):
        from forgeos.model_inventory import VerificationCancelled
        from forgeos import model_retirement as retirement
        ticket = self.retire()
        event = Event()
        move = retirement._move
        moves = 0

        def cancel_after_moves(source, target):
            nonlocal moves
            move(source, target)
            moves += 1
            if moves == 2:
                event.set()

        with patch("forgeos.model_retirement._move",
                   side_effect=cancel_after_moves):
            with self.assertRaises(VerificationCancelled):
                restore_download(ticket["id"], directory=self.root,
                                 cancel_event=event)
        self.assertTrue(self.model.exists())
        self.assertTrue(self.manifest.exists())
        self.assertEqual(list_retired(self.root)[0]["status"],
                         "interrupted_retirement_or_restore")
        self.assertEqual(self.restore(ticket["id"])["status"], "restored")

    def test_retirement_rejects_invalid_progress_options(self):
        with self.assertRaisesRegex(TypeError, "threading.Event"):
            retire_download(self.model, directory=self.root, cancel_event=object())
        with self.assertRaisesRegex(TypeError, "callable"):
            restore_download("a" * 32, directory=self.root,
                             progress_callback=object())

    def test_sibling_unchanged_and_modified_hash_rejected(self):
        sibling = self.model.with_name("other.gguf")
        sibling.write_bytes(b"keep me")
        ticket = self.retire()
        self.assertEqual(sibling.read_bytes(), b"keep me")
        self.restore(ticket["id"])
        self.model.write_bytes(b"GGUF" + b"z"*60)
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            self.retire()
        self.assertEqual(sibling.read_bytes(), b"keep me")

    def test_hardlinked_model_and_manifest_are_never_retired(self):
        import os
        outside = self.root.parent / (self.root.name + "-shared.gguf")
        try:
            os.link(self.model, outside)
        except (OSError, NotImplementedError):
            self.skipTest("Hard links unsupported on this filesystem")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaisesRegex(RuntimeError, "single-link"):
            self.retire()
        self.assertEqual(outside.read_bytes(), self.data)
        self.assertTrue(self.model.exists())
        outside.unlink()
        manifest_link = self.root.parent / (self.root.name + "-shared.json")
        try:
            os.link(self.manifest, manifest_link)
        except (OSError, NotImplementedError):
            self.skipTest("Manifest hard links unsupported")
        self.addCleanup(lambda: manifest_link.unlink(missing_ok=True))
        with self.assertRaisesRegex(RuntimeError, "single-link"):
            self.retire()
        self.assertTrue(self.manifest.exists())

    def test_hardlinked_retired_weight_blocks_restoration(self):
        import os
        ticket = self.retire()
        saved = self.root / ".forgeos-retired" / ticket["id"] / "weights.gguf"
        outside = self.root.parent / (self.root.name + "-retired-link.gguf")
        try:
            os.link(saved, outside)
        except (OSError, NotImplementedError):
            self.skipTest("Hard links unsupported on this filesystem")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaisesRegex(RuntimeError, "single-link"):
            self.restore(ticket["id"])
        self.assertFalse(self.model.exists())
        self.assertEqual(saved.read_bytes(), self.data)
        outside.unlink()
        self.assertEqual(self.restore(ticket["id"])["status"], "restored")

    def test_hardlinked_recovery_journal_is_not_trusted_or_deleted(self):
        import os
        ticket = self.retire()
        journal = (self.root / ".forgeos-retired" /
                   ticket["id"] / "journal.json")
        outside = self.root.parent / (self.root.name + "-journal-link.json")
        try:
            os.link(journal, outside)
        except (OSError, NotImplementedError):
            self.skipTest("Hard links unsupported on this filesystem")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaisesRegex(ValueError, "Hard-linked"):
            self.restore(ticket["id"])
        self.assertEqual(list_retired(self.root)[0]["status"],
                         "needs_manual_inspection")
        self.assertTrue(journal.exists())
        outside.unlink()
        self.assertEqual(self.restore(ticket["id"])["status"], "restored")

    def test_restore_detects_journal_swap_during_lock_wait(self):
        from forgeos import model_retirement as retirement
        ticket = self.retire()
        real_lock = retirement._exclusive_download_lock
        journal = (self.root / ".forgeos-retired" /
                   ticket["id"] / "journal.json")
        from contextlib import contextmanager
        @contextmanager
        def change_journal(lock):
            with real_lock(lock):
                data = json.loads(journal.read_text())
                data["identity"]["sha256"] = "f" * 64
                journal.write_text(json.dumps(data))
                yield
        with patch("forgeos.model_retirement._exclusive_download_lock",
                   side_effect=change_journal):
            with self.assertRaisesRegex(RuntimeError, "changed while waiting"):
                self.restore(ticket["id"])
        self.assertFalse(self.model.exists())
        self.assertTrue(journal.exists())

    def test_restore_detects_destination_created_during_hash(self):
        from forgeos import model_retirement as retirement
        ticket = self.retire()
        real_validate = retirement._validate_bytes
        def inject_conflict(path, root, info, **kwargs):
            real_validate(path, root, info, **kwargs)
            self.model.write_bytes(b"important independent data")
        with patch("forgeos.model_retirement._validate_bytes",
                   side_effect=inject_conflict):
            with self.assertRaisesRegex(RuntimeError, "overwrite"):
                self.restore(ticket["id"])
        self.assertEqual(self.model.read_bytes(), b"important independent data")
        self.assertTrue((self.root / ".forgeos-retired" /
                         ticket["id"] / "weights.gguf").exists())

    def test_partial_download_blocks_both_retire_and_restore(self):
        partial = self.model.with_name("coder.gguf.part")
        partial.write_bytes(b"partial")
        with self.assertRaisesRegex(RuntimeError, "Unresolved"):
            self.retire()
        partial.unlink()
        ticket = self.retire()
        partial.write_bytes(b"new transfer")
        with self.assertRaisesRegex(RuntimeError, "unfinished"):
            self.restore(ticket["id"])

    def test_interrupted_retirement_is_restorable(self):
        from forgeos import model_retirement as mod
        move = mod._move
        index = 0
        def fault(src, dst):
            nonlocal index
            index += 1
            if index == 2:
                raise OSError("power failure")
            move(src, dst)
        with patch("forgeos.model_retirement._move", side_effect=fault):
            with self.assertRaisesRegex(RuntimeError, "restore using identifier"):
                self.retire()
        entry = list_retired(self.root)[0]
        self.assertEqual(entry["status"], "interrupted_retirement_or_restore")
        self.restore(entry["id"])
        self.assertEqual(self.model.read_bytes(), self.data)

    def test_interrupted_restore_is_retryable(self):
        ticket = self.retire()
        from forgeos import model_retirement as mod
        move = mod._move
        count = 0
        def fault(src, dst):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("simulated crash")
            move(src, dst)
        with patch("forgeos.model_retirement._move", side_effect=fault):
            with self.assertRaisesRegex(OSError, "simulated crash"):
                self.restore(ticket["id"])
        self.assertTrue(self.model.exists())
        self.assertFalse(self.manifest.exists())
        self.assertEqual(self.restore(ticket["id"])["status"], "restored")

    def test_conflict_and_retired_data_corruption_fail_closed(self):
        ticket = self.retire()
        self.model.write_bytes(b"other important data")
        with self.assertRaisesRegex(RuntimeError, "overwrite"):
            self.restore(ticket["id"])
        self.assertEqual(self.model.read_bytes(), b"other important data")
        self.model.unlink()
        saved = self.root / ".forgeos-retired" / ticket["id"] / "weights.gguf"
        saved.write_bytes(b"GGUFtampered")
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            self.restore(ticket["id"])

    def test_unsafe_token_and_forged_record_rejected(self):
        for token in (None, "", "../danger", "z"*32, "a"*31):
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.restore(token)
        ticket = self.retire()
        record = self.root / ".forgeos-retired" / ticket["id"] / "journal.json"
        record.write_text(json.dumps({"version": 1, "path": "../../outside",
                                      "identity": self.info}))
        with self.assertRaisesRegex(ValueError, "disagrees"):
            self.restore(ticket["id"])
        self.assertEqual(list_retired(self.root)[0]["status"], "needs_manual_inspection")

    def test_malformed_journal_filename_fails_closed_without_file_moves(self):
        ticket = self.retire()
        journal = self.root / ".forgeos-retired" / ticket["id"] / "journal.json"
        original = json.loads(journal.read_text())
        archived = self.root / ".forgeos-retired" / ticket["id"] / "weights.gguf"
        for filename in (None, 7, {}, [], "../escape.gguf", ""):
            with self.subTest(filename=filename):
                record = json.loads(json.dumps(original))
                record["identity"]["file"] = filename
                journal.write_text(json.dumps(record))
                with self.assertRaises((ValueError, TypeError)):
                    self.restore(ticket["id"])
                self.assertTrue(archived.exists())
                self.assertFalse(self.model.exists())
        journal.write_text(json.dumps(original))
        self.assertEqual(self.restore(ticket["id"])["status"], "restored")

    def test_directory_sync_covers_journal_and_both_atomic_renames(self):
        from forgeos import model_retirement as retirement
        observed = []
        with patch("forgeos.model_retirement._sync_directory",
                   side_effect=lambda path: observed.append(path)):
            ticket = self.retire()
            saved = self.root / ".forgeos-retired" / ticket["id"]
            # Windows temp roots can use 8.3 aliases (RUNNER~1 vs
            # runneradmin). Compare canonical paths, not spellings.
            self.assertIn(saved.resolve(), [p.resolve() for p in observed])
            self.assertIn(self.model.parent.resolve(),
                          [p.resolve() for p in observed])
            observed.clear()
            self.restore(ticket["id"])
        self.assertIn(self.model.parent.resolve(),
                      [p.resolve() for p in observed])
        self.assertIn((self.root / ".forgeos-retired").resolve(),
                      [p.resolve() for p in observed])

    def test_directory_sync_fault_after_weight_move_retains_recovery_id(self):
        from forgeos import model_retirement as retirement
        real_move = retirement._move
        count = 0
        def fail_after_first_model_move(source, destination):
            nonlocal count
            count += 1
            real_move(source, destination)
            if count == 1:
                raise OSError("directory durability fault")
        with patch("forgeos.model_retirement._move",
                   side_effect=fail_after_first_model_move):
            with self.assertRaisesRegex(RuntimeError, "restore using identifier"):
                self.retire()
        entries = list_retired(self.root)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["status"], "interrupted_retirement_or_restore")
        self.restore(entries[0]["id"])
        self.assertEqual(self.model.read_bytes(), self.data)

    def test_cli_requires_confirm_then_restores_by_token(self):
        from forgeos.cli import main
        from contextlib import redirect_stdout
        from io import StringIO
        with self.assertRaisesRegex(ValueError, "confirm"):
            main(["model-retire-local", str(self.model),
                  "--directory", str(self.root)])
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["model-retire-local", str(self.model),
                                   "--directory", str(self.root),
                                   "--confirm"]), 0)
        ticket = json.loads(output.getvalue())
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["models-retired", "--directory", str(self.root)]), 0)
        self.assertEqual(json.loads(output.getvalue())[0]["id"], ticket["id"])
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["model-restore-local", ticket["id"],
                                   "--directory", str(self.root)]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "restored")

    def test_symlinked_model_target_rejected(self):
        ticket = self.retire()
        sibling = self.root / "important.gguf"
        sibling.write_bytes(b"protected")
        try:
            self.model.symlink_to(sibling)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(ValueError):
            self.restore(ticket["id"])
        self.assertEqual(sibling.read_bytes(), b"protected")
        self.model.unlink()


if __name__ == "__main__":
    unittest.main()
