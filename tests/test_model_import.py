import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from forgeos.managed_llama import _verified_model
from forgeos.model_import import import_local_gguf


class LocalModelImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forgeos-import-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / "existing.gguf"
        self.data = b"GGUF" + b"x" * 64
        self.model.write_bytes(self.data)

    def test_import_records_sha_without_copying_and_runtime_reverifies(self):
        original_inode = self.model.stat().st_ino
        result = import_local_gguf(self.model)
        sidecar = self.model.with_name(self.model.name + ".forgeos.json")
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(record["provenance"], "unknown")
        self.assertEqual(record["source_path"], str(self.model.resolve()))
        self.assertEqual(self.model.stat().st_ino, original_inode)
        self.assertEqual(self.model.read_bytes(), self.data)
        self.assertEqual(_verified_model(self.model)[1], record)

        self.model.write_bytes(self.data + b"changed")
        with self.assertRaisesRegex(ValueError, "identity or file size"):
            _verified_model(self.model)

    def test_import_does_not_overwrite_an_existing_sidecar(self):
        sidecar = self.model.with_name(self.model.name + ".forgeos.json")
        sidecar.write_text("keep this", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            import_local_gguf(self.model)
        self.assertEqual(sidecar.read_text(encoding="utf-8"), "keep this")

    def test_invalid_header_and_hardlinks_are_refused(self):
        self.model.write_bytes(b"not a GGUF file" * 4)
        with self.assertRaisesRegex(ValueError, "GGUF header"):
            import_local_gguf(self.model)
        linked = self.root / "linked.gguf"
        linked.write_bytes(b"GGUF" + b"y" * 32)
        alias = self.root / "linked-alias.gguf"
        try:
            alias.hardlink_to(linked)
        except (OSError, NotImplementedError):
            self.skipTest("Hard links are unavailable")
        with self.assertRaisesRegex(ValueError, "single-link"):
            import_local_gguf(linked)


if __name__ == "__main__":
    unittest.main()
