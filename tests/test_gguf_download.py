"""Fault injection for crash-resumable, SHA-256-verified GGUF downloads."""
import hashlib
import io
import json
import errno
from types import SimpleNamespace
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import Mock, patch

from forgeos.gguf_download import (
    DownloadCancelled, _allowed_download_url, _safe_name, download_gguf,
)

REPO = "Qwen/Coder-GGUF"
REVISION = "a" * 40
FILE = "coder-Q4_K_M.gguf"
BODY = b"GGUF0123456789abcdefghijklmnopqrstuvwxyz"
DIGEST = hashlib.sha256(BODY).hexdigest()


class Response(io.BytesIO):
    def __init__(self, data, *, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}
    def getcode(self):
        return self.status
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class GGUFDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.mock_revision = patch("forgeos.gguf_download.model_revision",
                                   return_value=REVISION)
        self.mock_catalog = patch("forgeos.gguf_download.list_gguf_files",
                                  return_value=[{"path": FILE, "bytes": len(BODY),
                                                 "sha256": DIGEST}])
        self.revision_mock = self.mock_revision.start()
        self.mock_catalog.start()
        self.addCleanup(self.mock_revision.stop)
        self.addCleanup(self.mock_catalog.stop)

    def download(self, **kwargs):
        return download_gguf(REPO, FILE, directory=self.root, **kwargs)

    def test_atomic_sha_verified_download_then_cached_reuse(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)) as opened:
            result = self.download()
        location = Path(result["path"])
        self.assertEqual(location.read_bytes(), BODY)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(json.loads(location.with_name(
            location.name + ".forgeos.json").read_text())["revision"], REVISION)
        self.assertEqual(list(location.parent.glob("*.part")), [])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, (
            f"https://huggingface.co/Qwen/Coder-GGUF/resolve/{REVISION}/"
            "coder-Q4_K_M.gguf?download=true"))
        with patch("forgeos.gguf_download._open_weight") as no_network:
            cached = self.download()
            self.assertEqual(cached["status"], "already_verified")
            no_network.assert_not_called()

    def test_explicit_inspected_revision_is_used_without_re_resolving_head(self):
        revision = "b" * 40
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            result = self.download(revision=revision)
        self.revision_mock.assert_not_called()
        self.assertIn("/" + revision + "/", result["path"].replace("\\", "/"))
        self.assertEqual(result["revision"], revision)

    def test_invalid_explicit_revision_rejected_before_metadata_or_disk(self):
        with self.assertRaisesRegex(ValueError, "full pinned revision"):
            self.download(revision="main")
        self.revision_mock.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_pre_cancelled_request_stops_before_metadata_or_disk(self):
        paused = Event()
        paused.set()
        with patch("forgeos.gguf_download.model_revision") as revision, \
             patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaises(DownloadCancelled):
                self.download(cancel_event=paused)
            revision.assert_not_called()
            network.assert_not_called()
        self.assertEqual(list(self.root.rglob("*.part")), [])

    def test_pause_during_transfer_saves_partial_then_resumes_exact_range(self):
        paused = Event()
        sizes = []
        paused_once = False
        def progress(stage, count, total):
            nonlocal paused_once
            sizes.append((stage, count, total))
            if stage == "download" and count == 12 and not paused_once:
                paused_once = True
                paused.set()
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY[:12], headers={
                       "Content-Length": str(len(BODY))})):
            with self.assertRaisesRegex(DownloadCancelled, "paused"):
                self.download(cancel_event=paused, progress_callback=progress)
        partial = next(self.root.rglob("*.part"))
        self.assertEqual(partial.read_bytes(), BODY[:12])
        self.assertEqual(sizes[0], ("download", 0, len(BODY)))
        self.assertIn(("download", 12, len(BODY)), sizes)
        self.assertEqual(list(self.root.rglob("*.gguf")), [])
        paused.clear()
        remaining = BODY[12:]
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(remaining, status=206, headers={
                       "Content-Length": str(len(remaining)),
                       "Content-Range": f"bytes 12-{len(BODY)-1}/{len(BODY)}"})) as opened:
            result = self.download(cancel_event=paused, progress_callback=progress)
        self.assertEqual(opened.call_args.args[0].get_header("Range"), "bytes=12-")
        self.assertEqual(Path(result["path"]).read_bytes(), BODY)
        self.assertIn(("verify", len(BODY), len(BODY)), sizes)

    def test_pause_after_transfer_before_promotion_resumes_without_network(self):
        paused = Event()
        def pause_on_complete(stage, count, total):
            if stage == "download" and count == total:
                paused.set()
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            with self.assertRaises(DownloadCancelled):
                self.download(cancel_event=paused,
                              progress_callback=pause_on_complete)
        self.assertEqual(next(self.root.rglob("*.part")).read_bytes(), BODY)
        self.assertEqual(list(self.root.rglob("*.gguf")), [])
        paused.clear()
        with patch("forgeos.gguf_download._open_weight") as network:
            result = self.download(cancel_event=paused)
            network.assert_not_called()
        self.assertEqual(result["status"], "verified")

    def test_invalid_pause_control_rejected_before_metadata(self):
        with patch("forgeos.gguf_download.model_revision") as revision:
            with self.assertRaisesRegex(TypeError, "cancel_event"):
                self.download(cancel_event=True)
            with self.assertRaisesRegex(TypeError, "progress_callback"):
                self.download(progress_callback="not callable")
            revision.assert_not_called()

    def test_pause_during_cache_hash_retains_published_model(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        paused = Event()
        def stop_on_verification(stage, count, total):
            if stage == "verify" and count == total:
                paused.set()
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaises(DownloadCancelled):
                self.download(cancel_event=paused,
                              progress_callback=stop_on_verification)
            network.assert_not_called()
        self.assertEqual(model.read_bytes(), BODY)
        self.assertTrue(model.with_name(FILE + ".forgeos.json").is_file())

    def test_crash_after_weight_promotion_recovers_manifest_offline(self):
        from forgeos import gguf_download as gd
        original = gd._write_json_atomic
        def fail_manifest(dest, value):
            if dest.name.endswith(".forgeos.json"):
                raise OSError("simulated manifest crash")
            return original(dest, value)
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)), \
             patch("forgeos.gguf_download._write_json_atomic",
                   side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "manifest crash"):
                self.download()
        model = self.root / "Qwen" / "Coder-GGUF" / REVISION / FILE
        manifest = model.with_name(FILE + ".forgeos.json")
        journal = model.with_name(FILE + ".part.json")
        self.assertEqual(model.read_bytes(), BODY)
        self.assertFalse(manifest.exists())
        self.assertTrue(journal.exists())
        with patch("forgeos.gguf_download._open_weight") as network:
            self.assertEqual(self.download()["status"], "already_verified")
            network.assert_not_called()
        self.assertEqual(json.loads(manifest.read_text())["sha256"], DIGEST)
        self.assertFalse(journal.exists())

    def test_cache_rejects_forged_manifest_and_journal_without_overwrite(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        manifest = model.with_name(FILE + ".forgeos.json")
        journal = model.with_name(FILE + ".part.json")
        manifest.write_text(json.dumps({"sha256": "f" * 64}))
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "manifest identity mismatch"):
                self.download()
            network.assert_not_called()
        manifest.unlink()
        journal.write_text(json.dumps({"repo": "wrong"}))
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "journal identifies different"):
                self.download()
            network.assert_not_called()
        self.assertEqual(model.read_bytes(), BODY)
        self.assertFalse(manifest.exists())

    def test_cache_refuses_ambiguous_orphan_partial(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        partial = model.with_name(FILE + ".part")
        partial.write_bytes(b"important unfinished bytes")
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "unresolved partial"):
                self.download()
            network.assert_not_called()
        self.assertEqual(partial.read_bytes(), b"important unfinished bytes")

    def test_matching_hash_but_invalid_gguf_magic_is_quarantined(self):
        invalid = b"FAKE" + BODY[4:]
        with patch("forgeos.gguf_download.list_gguf_files",
                   return_value=[{"path": FILE, "bytes": len(invalid),
                                  "sha256": hashlib.sha256(invalid).hexdigest()}]), \
             patch("forgeos.gguf_download._open_weight",
                   return_value=Response(invalid)):
            with self.assertRaisesRegex(RuntimeError, "lacks GGUF header"):
                self.download()
        self.assertFalse(list(self.root.rglob("*.gguf")))
        self.assertEqual(len(list(self.root.rglob("*.corrupt-*"))), 1)

    def test_existing_correct_file_recreates_lost_manifest(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        manifest = model.with_name(FILE + ".forgeos.json")
        manifest.unlink()
        with patch("forgeos.gguf_download._open_weight") as network:
            self.assertEqual(self.download()["status"], "already_verified")
            network.assert_not_called()
        self.assertEqual(json.loads(manifest.read_text())["sha256"], DIGEST)

    def test_interruption_preserves_partial_and_exact_resume(self):
        first = BODY[:13]
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(first, headers={
                       "Content-Length": str(len(BODY))})):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.download()
        part = next(self.root.rglob("*.part"))
        self.assertEqual(part.read_bytes(), first)
        remaining = BODY[13:]
        response = Response(remaining, status=206, headers={
            "Content-Length": str(len(remaining)),
            "Content-Range": f"bytes 13-{len(BODY)-1}/{len(BODY)}"})
        with patch("forgeos.gguf_download._open_weight",
                   return_value=response) as opened:
            result = self.download()
        self.assertEqual(Path(result["path"]).read_bytes(), BODY)
        self.assertEqual(opened.call_args.args[0].get_header("Range"), "bytes=13-")

    def test_resume_refuses_server_ignoring_range(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY[:8], headers={
                       "Content-Length": str(len(BODY))})):
            with self.assertRaises(RuntimeError):
                self.download()
        part = next(self.root.rglob("*.part"))
        initial = part.read_bytes()
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY[8:])) as opened:
            with self.assertRaisesRegex(RuntimeError, "resume range"):
                self.download()
        self.assertEqual(part.read_bytes(), initial)
        self.assertEqual(opened.call_args.args[0].get_header("Range"), "bytes=8-")

    def test_hash_mismatch_is_quarantined_without_publishing(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(b"x" * len(BODY))):
            with self.assertRaisesRegex(RuntimeError, "failed SHA-256"):
                self.download()
        self.assertEqual(list(self.root.rglob("*.gguf")), [])
        quarantined = list(self.root.rglob("*.corrupt-*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_bytes(), b"x" * len(BODY))

    def test_rejects_bad_paths_and_dangerous_redirects(self):
        for file in ("../coder.gguf", "bad\\coder.gguf", "CON.gguf", "COM2/x.gguf", "LPT9/x.gguf",
                     "lpt4/dir/x.gguf", "trailing./coder.gguf", "aux/x.gguf",
                     "a//x.gguf", "a/../x.gguf", "/x.gguf",
                     "evil.gguf:stream", "x" * 400 + ".gguf"):
            with self.subTest(file=file), self.assertRaises(ValueError):
                _safe_name(file)
        for url in ("http://huggingface.co/model",
                    "https://evil.example/model",
                    "http://127.0.0.1:8080",
                    "file:///etc/passwd",
                    "https://attacker@huggingface.co/model",
                    "https://unrelated.hf.co/weights",
                    "https://hf.co/weights",
                    "https://xethub.hf.co.evil.example/model",
                    "https://fake-xethub.hf.co/weights",
                    "https://huggingface.co:444/model",
                    "https://huggingface.co.evil.example/weights"):
            self.assertFalse(_allowed_download_url(url))
        for url in ("https://cas-bridge.xethub.hf.co/x",
                    "https://cas-server.xethub.hf.co/x",
                    "https://cdn-lfs.huggingface.co/model",
                    "https://huggingface.co/owner/repo",
                    "https://transfer.xethub.hf.co/x"):
            with self.subTest(url=url):
                self.assertTrue(_allowed_download_url(url))

    def test_redirect_handler_rejects_untrusted_destination_before_request(self):
        from urllib.request import Request
        from forgeos.gguf_download import _SafeModelRedirects
        handler = _SafeModelRedirects()
        source = Request("https://huggingface.co/Owner/Model/resolve/main/a.gguf")
        for destination in (
                "https://unrelated.hf.co/weights",
                "https://huggingface.co.attacker.example/weights",
                "http://cas-bridge.xethub.hf.co/weights",
                "https://attacker@cas-bridge.xethub.hf.co/weights"):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(RuntimeError, "untrusted host"):
                    handler.redirect_request(source, None, 302, "Found", {},
                                             destination)
        allowed = handler.redirect_request(
            source, None, 302, "Found", {},
            "https://transfer.xethub.hf.co/weights")
        self.assertEqual(allowed.full_url,
                         "https://transfer.xethub.hf.co/weights")

    def test_missing_hash_and_oversize_rejected_before_disk_or_fetch(self):
        with patch("forgeos.gguf_download.list_gguf_files",
                   return_value=[{"path": FILE, "bytes": len(BODY), "sha256": None}]), \
             patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                self.download()
            network.assert_not_called()
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(ValueError, "limit"):
                self.download(max_bytes=3)
            network.assert_not_called()

    def test_bad_download_journal_fails_closed(self):
        location = self.root / "Qwen" / "Coder-GGUF" / REVISION / FILE
        location.parent.mkdir(parents=True)
        location.with_name(FILE + ".part").write_bytes(BODY[:3])
        location.with_name(FILE + ".part.json").write_text('{"repo":"wrong"}')
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "different weights"):
                self.download()
            network.assert_not_called()
        self.assertFalse(location.exists())

    def test_existing_destination_never_overwritten(self):
        location = self.root / "Qwen" / "Coder-GGUF" / REVISION / FILE
        location.parent.mkdir(parents=True)
        location.write_bytes(b"important preexisting model")
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "Existing GGUF"):
                self.download()
            network.assert_not_called()
        self.assertEqual(location.read_bytes(), b"important preexisting model")

    def test_symlinked_destination_refused(self):
        external = self.root / "external"
        external.mkdir()
        location = self.root / "Qwen" / "Coder-GGUF"
        location.parent.mkdir(exist_ok=True)
        try:
            location.symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaises(ValueError):
                self.download()
            network.assert_not_called()

    def test_disk_pressure_blocks_request_and_keeps_journal(self):
        # Drive measurement is injected, not the actual disk capacity.
        with patch("forgeos.gguf_download.shutil.disk_usage",
                   return_value=SimpleNamespace(free=1)), \
             patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "Insufficient disk"):
                self.download()
            network.assert_not_called()

    def test_mid_transfer_disk_full_retains_partial_and_resumes(self):
        original_open = Path.open

        class DiskFullWriter:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def write(self, data):
                self.stream.write(data[:5])
                raise OSError(errno.ENOSPC, "No space left on device")

            def flush(self):
                return self.stream.flush()

            def fileno(self):
                return self.stream.fileno()

        def open_with_disk_full(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if mode == "ab" and str(path).endswith(".gguf.part"):
                return DiskFullWriter(stream)
            return stream

        with patch.object(Path, "open", new=open_with_disk_full), \
             patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            with self.assertRaisesRegex(RuntimeError, "Disk space ran out"):
                self.download()
        partial = next(self.root.rglob("*.gguf.part"))
        self.assertEqual(partial.read_bytes(), BODY[:5])
        self.assertTrue(partial.with_name(partial.name + ".json").is_file())
        self.assertFalse(partial.with_name(partial.name.removesuffix(".part")).exists())

        remaining = BODY[5:]
        resumed = Response(remaining, status=206, headers={
            "Content-Length": str(len(remaining)),
            "Content-Range": f"bytes 5-{len(BODY)-1}/{len(BODY)}"})
        with patch("forgeos.gguf_download._open_weight", return_value=resumed) as opened:
            result = self.download()
        self.assertEqual(Path(result["path"]).read_bytes(), BODY)
        self.assertEqual(opened.call_args.args[0].get_header("Range"), "bytes=5-")


if __name__ == "__main__":
    unittest.main()
