"""Crash injection and security regressions for reversible local model retirement."""
import hashlib
import json
from pathlib import Path
import tempfile
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
