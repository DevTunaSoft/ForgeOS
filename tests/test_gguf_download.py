"""Fault injection for crash-resumable, SHA-256-verified GGUF downloads."""
import hashlib
import io
import json
import errno
import os
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from urllib.error import HTTPError
from types import SimpleNamespace
from pathlib import Path
from threading import Event, Thread
import tempfile
import unittest
from unittest.mock import Mock, patch

from forgeos.gguf_download import (
    DownloadCancelled, _allowed_download_url, _is_reparse_point, _safe_name,
    _safe_storage_root, download_gguf, repair_downloaded_gguf,
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

    def test_download_persists_unverified_pinned_card_claims_in_inventory(self):
        claims = {
            "license": "apache-2.0", "quantization": "Q4_K_M",
            "context_length": "32768",
        }
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            result = self.download(
                revision=REVISION, catalog_claims=claims,
                catalog_claims_revision=REVISION)
        manifest = json.loads(Path(result["path"]).with_name(
            FILE + ".forgeos.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["hub_claims"], {
            "source": "huggingface_card_unverified",
            "revision": REVISION, "claims": claims,
        })
        from forgeos.model_inventory import list_downloads
        record = list_downloads(self.root)[0]
        self.assertEqual(record["repo"], REPO)
        self.assertEqual(record["revision"], REVISION)
        self.assertEqual(record["format"], "GGUF")
        self.assertEqual(record["disk_bytes"], len(BODY))
        self.assertEqual(record["metadata_claims"], claims)
        self.assertEqual(record["metadata_claims_status"], "hub-card-unverified")
        self.assertEqual(record["runtime_compatibility"], "not-recorded")
        with patch("forgeos.gguf_download._open_weight") as network:
            self.assertEqual(self.download()["status"], "already_verified")
            network.assert_not_called()
        self.assertEqual(json.loads(Path(result["path"]).with_name(
            FILE + ".forgeos.json").read_text(encoding="utf-8"))["hub_claims"],
            manifest["hub_claims"])

    def test_card_claims_require_matching_pinned_revision_and_bounded_text(self):
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(ValueError, "matching pinned revision"):
                self.download(
                    catalog_claims={"license": "mit"},
                    revision=REVISION,
                    catalog_claims_revision="b" * 40)
            with self.assertRaisesRegex(ValueError, "unsafe or oversized"):
                self.download(
                    catalog_claims={"license": "x\x01"},
                    revision=REVISION,
                    catalog_claims_revision=REVISION)
            network.assert_not_called()

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

    def test_process_crash_after_chunk_resumes_exact_pinned_range(self):
        body = b"GGUF" + b"x" * (2 * 1024 * 1024)
        digest = hashlib.sha256(body).hexdigest()
        child = textwrap.dedent("""
            import hashlib, io, os, sys
            from forgeos import gguf_download as gd
            body = b"GGUF" + b"x" * (2 * 1024 * 1024)
            class Response(io.BytesIO):
                def getcode(self): return 200
                def __enter__(self): return self
                def __exit__(self, *args): self.close()
                @property
                def headers(self): return {"Content-Length": str(len(body))}
            gd.list_gguf_files = lambda repo, revision=None: [{
                "path": "coder-Q4_K_M.gguf", "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest()}]
            gd._open_weight = lambda request, timeout: Response(body)
            def progress(stage, count, total):
                if stage == "download" and count >= gd.CHUNK_BYTES:
                    os._exit(73)
            gd.download_gguf(
                "Qwen/Coder-GGUF", "coder-Q4_K_M.gguf",
                revision="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                directory=sys.argv[1], progress_callback=progress)
        """)
        child_env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, "-c", child, str(self.root)],
            cwd=Path(__file__).resolve().parents[1], env=child_env,
            capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 73, result.stderr.decode(errors="replace"))
        partial = next(self.root.rglob("*.part"))
        self.assertEqual(partial.stat().st_size, 1024 * 1024)
        self.assertEqual(partial.read_bytes()[:4], b"GGUF")

        with patch("forgeos.gguf_download.list_gguf_files", return_value=[{
                "path": FILE, "bytes": len(body), "sha256": digest}]), \
             patch("forgeos.gguf_download._open_weight", return_value=Response(
                 body[1024 * 1024:], status=206, headers={
                     "Content-Length": str(len(body) - 1024 * 1024),
                     "Content-Range": (
                         f"bytes {1024 * 1024}-{len(body)-1}/{len(body)}")})) as opened:
            restored = self.download(revision=REVISION)
        self.assertEqual(opened.call_args.args[0].get_header("Range"),
                         "bytes=1048576-")
        self.assertEqual(Path(restored["path"]).read_bytes(), body)

    def test_competing_same_model_download_fails_before_second_network_open(self):
        entered = Event()
        release = Event()
        outcomes = []
        def open_first(request, *, timeout):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test transfer was not released")
            return Response(BODY)
        def first_download():
            try:
                outcomes.append(self.download(revision=REVISION))
            except BaseException as exc:
                outcomes.append(exc)

        with patch("forgeos.gguf_download._open_weight",
                   side_effect=open_first) as opened:
            worker = Thread(target=first_download, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(5), "first transfer did not start")
                with self.assertRaisesRegex(RuntimeError, "active elsewhere"):
                    self.download(revision=REVISION)
                self.assertEqual(opened.call_count, 1)
            finally:
                release.set()
                worker.join(10)
            self.assertFalse(worker.is_alive(), "first transfer did not finish")
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], dict)
        self.assertEqual(outcomes[0]["status"], "verified")

    def test_competing_processes_share_same_model_download_lock(self):
        ready = self.root / "first-transfer-ready"
        release = self.root / "release-first-transfer"
        worker_code = textwrap.dedent("""
            import hashlib, io, sys, time
            from pathlib import Path
            from forgeos import gguf_download as gd
            mode, root, ready_name, release_name = sys.argv[1:]
            ready, release = Path(ready_name), Path(release_name)
            body = b"GGUF0123456789abcdefghijklmnopqrstuvwxyz"
            gd.list_gguf_files = lambda repo, revision=None: [{
                "path": "coder-Q4_K_M.gguf", "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest()}]
            class Response(io.BytesIO):
                def getcode(self): return 200
                def __enter__(self): return self
                def __exit__(self, *args): self.close()
                @property
                def headers(self): return {"Content-Length": str(len(body))}
            def open_weight(request, *, timeout):
                if mode != "first":
                    raise AssertionError("second process opened the network")
                ready.write_text("ready")
                deadline = time.monotonic() + 15
                while not release.exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("test release marker did not arrive")
                    time.sleep(0.02)
                return Response(body)
            gd._open_weight = open_weight
            try:
                gd.download_gguf(
                    "Qwen/Coder-GGUF", "coder-Q4_K_M.gguf",
                    revision="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    directory=root)
            except RuntimeError as exc:
                if mode == "second" and "active elsewhere" in str(exc):
                    sys.exit(42)
                raise
            if mode == "second":
                sys.exit(44)
        """)
        env = os.environ.copy()
        command = [sys.executable, "-c", worker_code]
        args = [str(self.root), str(ready), str(release)]
        cwd = Path(__file__).resolve().parents[1]
        first = subprocess.Popen(
            [*command, "first", *args], cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and first.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("first process did not acquire the download lock")
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "first process did not reach network open")
            second = subprocess.run(
                [*command, "second", *args], cwd=cwd, env=env,
                capture_output=True, timeout=15)
            self.assertEqual(second.returncode, 42,
                             second.stderr.decode(errors="replace"))
        finally:
            release.write_text("release")
            stdout, stderr = first.communicate(timeout=20)
        self.assertEqual(first.returncode, 0, stderr.decode(errors="replace"))
        model = next(self.root.rglob("*.gguf"))
        self.assertEqual(model.read_bytes(), BODY)

    def test_competing_processes_cannot_oversubscribe_shared_quota(self):
        ready = self.root / "quota-first-transfer-ready"
        second_started = self.root / "quota-second-started"
        release = self.root / "quota-release-first-transfer"
        worker_code = textwrap.dedent("""
            import hashlib, io, sys, time
            from pathlib import Path
            from forgeos import gguf_download as gd
            mode, root, ready_name, second_name, release_name = sys.argv[1:]
            ready, second_started, release = (
                Path(ready_name), Path(second_name), Path(release_name))
            body = b"GGUF0123456789abcdefghijklmnopqrstuvwxyz"
            models = {
                "first": ("Qwen/Coder-One", "coder-one.gguf"),
                "second": ("Qwen/Coder-Two", "coder-two.gguf"),
            }
            repo, filename = models[mode]
            gd.list_gguf_files = lambda requested_repo, revision=None: [{
                "path": filename, "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest()}]
            class Response(io.BytesIO):
                def getcode(self): return 200
                def __enter__(self): return self
                def __exit__(self, *args): self.close()
                @property
                def headers(self): return {"Content-Length": str(len(body))}
            def open_weight(request, *, timeout):
                if mode != "first":
                    raise AssertionError("quota-rejected process opened network")
                ready.write_text("ready")
                deadline = time.monotonic() + 60
                while not release.exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("quota release marker did not arrive")
                    time.sleep(0.02)
                return Response(body)
            gd._open_weight = open_weight
            if mode == "second":
                second_started.write_text("started")
            try:
                gd.download_gguf(
                    repo, filename,
                    revision="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    directory=root, storage_quota_bytes=len(body) + 5)
            except RuntimeError as exc:
                if mode == "second" and "storage cap reached" in str(exc):
                    sys.exit(43)
                raise
            if mode == "second":
                sys.exit(45)
        """)
        env = os.environ.copy()
        command = [sys.executable, "-c", worker_code]
        args = [str(self.root), str(ready), str(second_started), str(release)]
        cwd = Path(__file__).resolve().parents[1]
        first = subprocess.Popen(
            [*command, "first", *args], cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and first.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("first quota process did not acquire the quota lock")
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "first quota transfer did not start")
            second = subprocess.Popen(
                [*command, "second", *args], cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 10
            while not second_started.exists() and second.poll() is None:
                if time.monotonic() >= deadline:
                    self.fail("second quota process did not start")
                time.sleep(0.02)
            self.assertTrue(second_started.exists(), "second quota process did not start")
        finally:
            release.write_text("release")
            stdout, stderr = first.communicate(timeout=20)
        self.assertEqual(first.returncode, 0, stderr.decode(errors="replace"))
        second_stdout, second_stderr = second.communicate(timeout=20)
        self.assertEqual(second.returncode, 43,
                         second_stderr.decode(errors="replace"))
        models = list(self.root.rglob("*.gguf"))
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].read_bytes(), BODY)

    def test_competing_different_models_cannot_oversubscribe_storage_quota(self):
        from forgeos.gguf_download import download_gguf
        entered = Event()
        release = Event()
        second_started = Event()
        outcomes = []
        models = {
            "Qwen/Coder-One": "coder-one.gguf",
            "Qwen/Coder-Two": "coder-two.gguf",
        }
        records = [{"path": filename, "bytes": len(BODY), "sha256": DIGEST}
                   for filename in models.values()]
        def catalog(repo, revision=None):
            filename = models[repo]
            return [record for record in records if record["path"] == filename]
        def open_response(request, *, timeout):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("quota test transfer was not released")
            return Response(BODY)
        def transfer(label, repo, filename, started=None):
            if started is not None:
                started.set()
            try:
                result = download_gguf(
                    repo, filename, revision=REVISION, directory=self.root,
                    storage_quota_bytes=len(BODY) + 5)
                outcomes.append((label, result))
            except BaseException as exc:
                outcomes.append((label, exc))

        with patch("forgeos.gguf_download.list_gguf_files", side_effect=catalog), \
             patch("forgeos.gguf_download._open_weight",
                   side_effect=open_response) as opened:
            first = Thread(target=transfer, args=(
                "first", "Qwen/Coder-One", models["Qwen/Coder-One"]), daemon=True)
            second = Thread(target=transfer, args=(
                "second", "Qwen/Coder-Two", models["Qwen/Coder-Two"],
                second_started), daemon=True)
            first.start()
            self.assertTrue(entered.wait(5), "first quota transfer did not start")
            second.start()
            try:
                self.assertTrue(second_started.wait(5), "second request did not start")
            finally:
                release.set()
                first.join(10)
                second.join(10)
            self.assertFalse(first.is_alive(), "first quota transfer did not finish")
            self.assertFalse(second.is_alive(), "second quota transfer did not finish")
            self.assertEqual(opened.call_count, 1)
        self.assertEqual(len(outcomes), 2)
        successes = [value for _, value in outcomes if isinstance(value, dict)]
        failures = [value for _, value in outcomes if isinstance(value, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(successes[0]["status"], "verified")
        self.assertEqual(len(failures), 1)
        self.assertRegex(str(failures[0]), "storage cap reached")

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

    def test_explicit_repair_quarantines_corrupt_bytes_and_restores_pinned_file(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        corrupt = b"X" * len(BODY)
        model.write_bytes(corrupt)
        checked = []
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)) as network:
            result = repair_downloaded_gguf(
                model, directory=self.root, confirm=True,
                checkpoint_check=lambda digest: checked.append(digest) or False)
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(result["sha256"], DIGEST)
        self.assertEqual(model.read_bytes(), BODY)
        self.assertEqual(checked, [DIGEST])
        quarantined = Path(result["quarantined_path"])
        self.assertEqual(quarantined.read_bytes(), corrupt)
        self.assertEqual(network.call_count, 1)
        from forgeos.model_inventory import storage_usage
        self.assertEqual(storage_usage(self.root), {
            "bytes": len(BODY) * 2, "files": 2})

    def test_repair_refuses_checkpoint_dependency_and_preserves_corrupt_file(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        corrupt = b"X" * len(BODY)
        model.write_bytes(corrupt)
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "checkpoint references"):
                repair_downloaded_gguf(
                    model, directory=self.root, confirm=True,
                    checkpoint_check=lambda _digest: True)
            network.assert_not_called()
        self.assertEqual(model.read_bytes(), corrupt)
        self.assertEqual(list(self.root.rglob("*.corrupt-*")), [])

    def test_repair_checks_checkpoint_before_replacing_a_missing_download(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        model.unlink()
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "checkpoint references"):
                repair_downloaded_gguf(
                    model, directory=self.root, confirm=True,
                    checkpoint_check=lambda _digest: True)
            network.assert_not_called()
        self.assertFalse(model.exists())

    def test_explicit_repair_restores_a_missing_pinned_download(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        model.unlink()
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            result = repair_downloaded_gguf(
                model, directory=self.root, confirm=True,
                checkpoint_check=lambda _digest: False)
        self.assertEqual(result["status"], "repaired")
        self.assertNotIn("quarantined_path", result)
        self.assertEqual(model.read_bytes(), BODY)

    def test_repair_quota_exhaustion_preserves_old_bytes_and_can_retry(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        corrupt = b"X" * len(BODY)
        model.write_bytes(corrupt)
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "preserved in quarantine"):
                repair_downloaded_gguf(
                    model, directory=self.root, confirm=True,
                    checkpoint_check=lambda _digest: False,
                    storage_quota_bytes=len(BODY))
            network.assert_not_called()
        quarantine = next(self.root.rglob(FILE + ".corrupt-*"))
        self.assertEqual(quarantine.read_bytes(), corrupt)
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            result = repair_downloaded_gguf(
                model, directory=self.root, confirm=True,
                checkpoint_check=lambda _digest: False,
                storage_quota_bytes=2 * len(BODY))
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(model.read_bytes(), BODY)
        self.assertEqual(quarantine.read_bytes(), corrupt)

    def test_repair_refuses_synthetic_hardlinked_destination(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        model.write_bytes(b"X" * len(BODY))
        original_stat = Path.stat

        def report_hardlink(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if path == model:
                return SimpleNamespace(
                    st_mode=info.st_mode, st_size=info.st_size,
                    st_mtime_ns=info.st_mtime_ns, st_ino=info.st_ino,
                    st_nlink=2)
            return info

        with patch.object(Path, "stat", report_hardlink), \
                patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "single-link"):
                repair_downloaded_gguf(
                    model, directory=self.root, confirm=True,
                    checkpoint_check=lambda _digest: False)
            network.assert_not_called()
        self.assertEqual(model.read_bytes(), b"X" * len(BODY))
        self.assertEqual(list(self.root.rglob("*.corrupt-*")), [])

    def test_interrupted_repair_preserves_quarantine_and_resumes_exact_revision(self):
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)):
            model = Path(self.download()["path"])
        corrupt = b"X" * len(BODY)
        model.write_bytes(corrupt)
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY[:13], headers={
                       "Content-Length": str(len(BODY))})):
            with self.assertRaisesRegex(RuntimeError, "preserved in quarantine"):
                repair_downloaded_gguf(
                    model, directory=self.root, confirm=True,
                    checkpoint_check=lambda _digest: False)
        quarantine = next(self.root.rglob(FILE + ".corrupt-*"))
        self.assertEqual(quarantine.read_bytes(), corrupt)
        self.assertFalse(model.exists())
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY[13:], status=206, headers={
                       "Content-Range": f"bytes 13-{len(BODY)-1}/{len(BODY)}",
                       "Content-Length": str(len(BODY)-13)})) as network:
            result = repair_downloaded_gguf(
                model, directory=self.root, confirm=True,
                checkpoint_check=lambda _digest: False)
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(model.read_bytes(), BODY)
        self.assertEqual(quarantine.read_bytes(), corrupt)
        self.assertEqual(network.call_args.args[0].headers.get("Range"),
                         "bytes=13-")

    def test_repair_requires_confirmation_and_valid_checkpoint_guard(self):
        with self.assertRaisesRegex(ValueError, "confirmation"):
            repair_downloaded_gguf(
                self.root / FILE, directory=self.root,
                checkpoint_check=lambda _digest: False)
        with self.assertRaisesRegex(TypeError, "checkpoint_check"):
            repair_downloaded_gguf(
                self.root / FILE, directory=self.root, confirm=True,
                checkpoint_check=None)

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

    def test_http_rate_limit_retains_partial_and_reports_redacted_retry_guidance(self):
        from forgeos.huggingface_catalog import catalog_error_message
        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY[:12], headers={
                       "Content-Length": str(len(BODY))})):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.download(revision=REVISION)
        partial = next(self.root.rglob("*.part"))
        retained = partial.read_bytes()
        self.assertEqual(retained, BODY[:12])

        response_body = Response(b"private response body")
        error = HTTPError(
            "https://cdn-lfs.huggingface.co/file?X-Amz-Signature=secret-value",
            429, "rate limited", {}, response_body)
        with patch("forgeos.gguf_download._open_weight", side_effect=error):
            with self.assertRaises(HTTPError) as caught:
                self.download(revision=REVISION)
        message = catalog_error_message(caught.exception)
        self.assertIn("rate limiting", message)
        self.assertIn("retry", message)
        self.assertNotIn("secret-value", message)
        self.assertNotIn("cdn-lfs", message)
        self.assertTrue(response_body.closed)
        self.assertEqual(partial.read_bytes(), retained)

        remaining = BODY[12:]
        with patch("forgeos.gguf_download._open_weight", return_value=Response(
                remaining, status=206, headers={
                    "Content-Length": str(len(remaining)),
                    "Content-Range": f"bytes 12-{len(BODY)-1}/{len(BODY)}"})) as opened:
            result = self.download(revision=REVISION)
        self.assertEqual(opened.call_args.args[0].get_header("Range"), "bytes=12-")
        self.assertEqual(Path(result["path"]).read_bytes(), BODY)

    def test_total_download_deadline_retains_partial_then_allows_resume(self):
        class SlowResponse(Response):
            def __init__(self, data):
                super().__init__(data)
                self.read_count = 0
            def read(self, size=-1):
                self.read_count += 1
                if self.read_count > 1:
                    time.sleep(1.05)
                return super().read(min(size, 7))

        with patch("forgeos.gguf_download._open_weight",
                   return_value=SlowResponse(BODY)):
            with self.assertRaisesRegex(TimeoutError, "total time limit"):
                self.download(revision=REVISION, max_duration_seconds=1)
        partial = next(self.root.rglob("*.part"))
        self.assertEqual(partial.read_bytes(), BODY[:7])

        remaining = BODY[7:]
        with patch("forgeos.gguf_download._open_weight", return_value=Response(
                remaining, status=206, headers={
                    "Content-Length": str(len(remaining)),
                    "Content-Range": f"bytes 7-{len(BODY)-1}/{len(BODY)}"})) as opened:
            result = self.download(
                revision=REVISION, max_duration_seconds=10)
        self.assertEqual(opened.call_args.args[0].get_header("Range"), "bytes=7-")
        self.assertEqual(Path(result["path"]).read_bytes(), BODY)

    def test_total_download_deadline_is_bounded_and_validated_before_io(self):
        from forgeos.gguf_download import MAX_DOWNLOAD_DURATION_SECONDS
        with patch("forgeos.gguf_download.model_revision") as revision, \
             patch("forgeos.gguf_download.list_gguf_files") as catalog:
            for invalid in (0, -1, True, 1.5,
                            MAX_DOWNLOAD_DURATION_SECONDS + 1):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(
                        ValueError, "total model download time limit"):
                    self.download(max_duration_seconds=invalid)
            revision.assert_not_called()
            catalog.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_pause_cancels_while_waiting_for_storage_quota_lock(self):
        from forgeos import gguf_download as gd
        quota_lock = self.root / ".forgeos-storage-quota.lock"
        waiting = Event()
        cancelled = Event()
        outcomes = []
        real_lock = gd._exclusive_download_lock

        @contextmanager
        def observe_quota_wait(path, **kwargs):
            if Path(path).name == quota_lock.name:
                waiting.set()
            with real_lock(path, **kwargs):
                yield

        def download_in_worker():
            try:
                outcomes.append(self.download(
                    revision=REVISION, storage_quota_bytes=100,
                    cancel_event=cancelled))
            except BaseException as exc:
                outcomes.append(exc)

        with real_lock(quota_lock):
            with patch("forgeos.gguf_download._exclusive_download_lock",
                       side_effect=observe_quota_wait), \
                 patch("forgeos.gguf_download._open_weight") as network:
                worker = Thread(target=download_in_worker, daemon=True)
                worker.start()
                self.assertTrue(waiting.wait(5), "download never queued for quota")
                cancelled.set()
                worker.join(5)
                self.assertFalse(worker.is_alive(), "cancel did not release quota wait")
                network.assert_not_called()
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], DownloadCancelled)
        self.assertIn("storage quota", str(outcomes[0]))

    def test_total_deadline_expires_while_waiting_for_storage_quota_lock(self):
        from forgeos import gguf_download as gd
        quota_lock = self.root / ".forgeos-storage-quota.lock"
        waiting = Event()
        outcomes = []
        real_lock = gd._exclusive_download_lock

        @contextmanager
        def observe_quota_wait(path, **kwargs):
            if Path(path).name == quota_lock.name:
                waiting.set()
            with real_lock(path, **kwargs):
                yield

        def download_in_worker():
            try:
                outcomes.append(self.download(
                    revision=REVISION, storage_quota_bytes=100,
                    max_duration_seconds=1))
            except BaseException as exc:
                outcomes.append(exc)

        with real_lock(quota_lock):
            with patch("forgeos.gguf_download._exclusive_download_lock",
                       side_effect=observe_quota_wait), \
                 patch("forgeos.gguf_download._open_weight") as network:
                worker = Thread(target=download_in_worker, daemon=True)
                worker.start()
                self.assertTrue(waiting.wait(5), "download never queued for quota")
                worker.join(3)
                self.assertFalse(worker.is_alive(), "deadline did not release quota wait")
                network.assert_not_called()
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], TimeoutError)
        self.assertIn("total time limit", str(outcomes[0]))

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

    def test_windows_reparse_attribute_is_detected_without_junction_api(self):
        path = Mock(spec=["is_symlink", "lstat"])
        path.is_symlink.return_value = False
        path.lstat.return_value = SimpleNamespace(
            st_file_attributes=0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
        self.assertTrue(_is_reparse_point(path))

    def test_storage_root_rejects_reparse_points_before_use(self):
        with patch("forgeos.gguf_download._is_reparse_point",
                   side_effect=lambda path: path == self.root):
            with self.assertRaisesRegex(ValueError, "reparse-point"):
                _safe_storage_root(self.root)

    def test_disk_pressure_blocks_request_and_keeps_journal(self):
        # Drive measurement is injected, not the actual disk capacity.
        with patch("forgeos.gguf_download.shutil.disk_usage",
                   return_value=SimpleNamespace(free=1)), \
             patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "Insufficient disk"):
                self.download()
            network.assert_not_called()

    def test_storage_quota_counts_existing_weights_and_can_resume_after_raising_cap(self):
        existing = self.root / "existing.gguf"
        existing.write_bytes(b"old-model")
        quota = existing.stat().st_size + len(BODY) - 1
        with patch("forgeos.gguf_download._open_weight") as network:
            with self.assertRaisesRegex(RuntimeError, "storage cap reached"):
                self.download(storage_quota_bytes=quota)
            network.assert_not_called()
        partial = next(self.root.rglob("*.gguf.part"))
        self.assertEqual(partial.stat().st_size, 0)
        self.assertTrue(partial.with_name(partial.name + ".json").is_file())

        with patch("forgeos.gguf_download._open_weight",
                   return_value=Response(BODY)) as network:
            result = self.download(storage_quota_bytes=quota + 1)
        self.assertEqual(Path(result["path"]).read_bytes(), BODY)
        network.assert_called_once()

    def test_invalid_storage_quota_is_rejected_before_catalog_or_disk(self):
        with patch("forgeos.gguf_download.list_gguf_files") as catalog:
            for quota in (0, -1, True, "10", 16 * 1024**4 + 1):
                with self.subTest(quota=quota), self.assertRaises(ValueError):
                    self.download(storage_quota_bytes=quota)
            catalog.assert_not_called()

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
