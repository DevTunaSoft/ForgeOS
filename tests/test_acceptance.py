import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from forgeos.acceptance import (
    MAX_RECORD_BYTES, collect_acceptance_snapshot, write_acceptance_snapshot,
)


class AcceptanceSnapshotTests(unittest.TestCase):
    def test_modified_checkout_omits_commit_identity(self):
        from subprocess import CompletedProcess
        from forgeos.acceptance import _source_commit

        with patch("forgeos.acceptance.subprocess.run", side_effect=[
            CompletedProcess([], 0, stdout="a" * 40 + "\n"),
            CompletedProcess([], 1),
        ]) as git:
            self.assertIsNone(_source_commit())
        self.assertIn("--no-ext-diff", git.call_args.args[0])

    def test_frozen_report_does_not_inspect_the_current_checkout(self):
        from forgeos.acceptance import _source_commit

        with patch("forgeos.acceptance.sys.frozen", True, create=True), \
             patch("forgeos.acceptance.subprocess.run") as git:
            self.assertIsNone(_source_commit())
        git.assert_not_called()

    def test_host_snapshot_is_bounded_and_secret_free(self):
        with patch("forgeos.acceptance.platform.system", return_value="Windows"), \
             patch("forgeos.acceptance._source_commit", return_value="a" * 40), \
             patch("forgeos.acceptance._frozen_identity",
                   return_value={"frozen_executable": True,
                                 "executable_sha256": "b" * 64,
                                 "executable_bytes": 123,
                                 "build_source_commit": "a" * 40}), \
             patch("forgeos.model_routing.memory_report",
                   return_value={"total_system_ram_bytes": 32 * 1024**3}):
            record = collect_acceptance_snapshot(physical_target=True)
        self.assertTrue(record["windows_host_evidence"])
        self.assertTrue(record["physical_windows_target_asserted"])
        self.assertEqual(record["source_commit"], "a" * 40)
        self.assertIsNone(record["managed_model"])
        self.assertFalse(record["scope"]["project_source_collected"])
        self.assertFalse(record["scope"]["credentials_collected"])
        payload = json.dumps(record)
        self.assertNotIn("api_key", payload)
        self.assertNotIn("token", payload.lower())

    def test_frozen_identity_binds_executable_hash_to_embedded_source_sha(self):
        from forgeos.acceptance import _frozen_identity
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exe = root / "ForgeOS.exe"
            exe.write_bytes(b"frozen-bytes")
            bundle = root / "bundle" / "forgeos"
            bundle.mkdir(parents=True)
            (bundle / "build_info.json").write_text(
                json.dumps({"source_commit": "c" * 40}), encoding="utf-8")
            with patch("forgeos.acceptance.sys.frozen", True, create=True), \
                 patch("forgeos.acceptance.sys.executable", str(exe)), \
                 patch("forgeos.acceptance.sys._MEIPASS",
                       str(root / "bundle"), create=True):
                identity = _frozen_identity()
        self.assertTrue(identity["frozen_executable"])
        self.assertEqual(identity["build_source_commit"], "c" * 40)
        self.assertEqual(
            identity["executable_sha256"],
            __import__("hashlib").sha256(b"frozen-bytes").hexdigest())

    def test_invalid_embedded_build_sha_is_not_accepted(self):
        from forgeos.acceptance import _embedded_build_commit
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "forgeos"
            bundle.mkdir()
            (bundle / "build_info.json").write_text(
                '{"source_commit":"../not-a-sha"}', encoding="utf-8")
            with patch("forgeos.acceptance.sys._MEIPASS",
                       str(Path(temp)), create=True):
                self.assertIsNone(_embedded_build_commit())

    def test_embedded_metadata_size_boundary(self):
        from forgeos.acceptance import MAX_BUILD_INFO_BYTES, _embedded_build_commit

        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "forgeos"
            bundle.mkdir()
            metadata = bundle / "build_info.json"
            payload = json.dumps({"source_commit": "a" * 40}).encode("utf-8")
            padded = payload.ljust(MAX_BUILD_INFO_BYTES)
            with patch("forgeos.acceptance.sys._MEIPASS", temp, create=True):
                metadata.write_bytes(padded)
                self.assertEqual(_embedded_build_commit(), "a" * 40)
                metadata.write_bytes(padded + b" ")
                self.assertIsNone(_embedded_build_commit())

    def test_physical_target_cannot_be_claimed_on_non_windows(self):
        with patch("forgeos.acceptance.platform.system", return_value="Linux"), \
             patch("forgeos.acceptance._frozen_identity",
                   return_value={"frozen_executable": False,
                                 "executable_sha256": None,
                                 "executable_bytes": None,
                                 "build_source_commit": None}), \
             patch("forgeos.model_routing.memory_report", return_value={}):
            with self.assertRaisesRegex(ValueError, "only be asserted on Windows"):
                collect_acceptance_snapshot(physical_target=True)

    def test_managed_snapshot_uses_allowlisted_identity_and_self_test(self):
        controller = Mock()
        controller.running.return_value = True
        controller.benchmark_identity.return_value = {
            "model": {"repo": "org/model", "revision": "r" * 40,
                      "file": "model.gguf", "bytes": 1000,
                      "sha256": "a" * 64, "secret": "do-not-save"},
            "runtime": {"sha256": "b" * 64, "release_tag": "b12199",
                        "endpoint": "http://127.0.0.1:1234/v1"},
            "settings": {"context_tokens": 2048, "threads": 4,
                         "memory_ceiling_bytes": 4 * 1024**3,
                         "api_key": "private"},
            "startup_elapsed_seconds": 2.5,
        }
        controller.inference_self_test.return_value = {
            "status": "passed", "elapsed_seconds": 1.1,
            "response_bytes": 12, "memory": {"peak_job_memory_used_bytes": 99},
            "response": "never persist me",
        }
        controller.memory_report.return_value = {
            "peak_job_memory_used_bytes": 99}
        with patch("forgeos.acceptance._source_commit", return_value=None), \
             patch("forgeos.acceptance._frozen_identity",
                   return_value={"frozen_executable": False,
                                 "executable_sha256": None,
                                 "executable_bytes": None,
                                 "build_source_commit": None}), \
             patch("forgeos.model_routing.memory_report", return_value={}):
            record = collect_acceptance_snapshot(controller)
        managed = record["managed_model"]
        self.assertEqual(managed["model"]["sha256"], "a" * 64)
        self.assertEqual(managed["runtime"]["sha256"], "b" * 64)
        payload = json.dumps(record)
        self.assertNotIn("do-not-save", payload)
        self.assertNotIn("private", payload)
        self.assertNotIn("never persist me", payload)
        self.assertNotIn("endpoint", payload)

    def test_nonrunning_controller_fails_closed(self):
        controller = Mock()
        controller.running.return_value = False
        with self.assertRaisesRegex(RuntimeError, "not running"):
            collect_acceptance_snapshot(controller)

    def test_atomic_write_is_bounded_unique_and_loadable(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch("forgeos.acceptance.collect_acceptance_snapshot",
                   return_value={"schema": 1, "kind": "test"}):
            first = write_acceptance_snapshot(directory=temp)
            second = write_acceptance_snapshot(directory=temp)
            self.assertNotEqual(first["path"], second["path"])
            for item in (first, second):
                path = Path(item["path"])
                self.assertTrue(path.is_file())
                self.assertLess(path.stat().st_size, MAX_RECORD_BYTES)
                self.assertEqual(
                    json.loads(path.read_text(encoding="utf-8"))["kind"], "test")
            self.assertFalse(list(Path(temp).glob(".*.tmp")))

    def test_symlinked_evidence_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symlinks unavailable")
            with self.assertRaisesRegex(OSError, "unsafe"):
                write_acceptance_snapshot(directory=link)


if __name__ == "__main__":
    unittest.main()
