"""Managed llama.cpp lifecycle, integrity, auth and failure regression tests.

All Popen and network calls are mocked: they do NOT constitute real CPU
inference or a real Windows process-tree ownership acceptance test.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from forgeos.local_models import LocalChatModel, create_model
from forgeos.managed_llama import (
    ALIAS, ManagedLlamaServer, _child_environment, _verified_model,
    _verified_runtime,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ManagedLlamaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forgeos-managed-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.model = self.root / "test.gguf"
        data = b"GGUF" + b"\x00" * 28
        self.model.write_bytes(data)
        self.model_sha = hashlib.sha256(data).hexdigest()
        (self.root / "test.gguf.forgeos.json").write_text(json.dumps({
            "repo": "Example/Model", "revision": "a" * 40,
            "file": "test.gguf", "bytes": len(data),
            "sha256": self.model_sha,
        }), encoding="utf-8")
        self.exe = self.root / ("llama-server.exe" if __import__("os").name == "nt"
                                else "llama-server")
        self.exe.write_bytes(b"trusted fixture only; not executable")
        if __import__("os").name != "nt":
            # An actual POSIX llama-server would have its executable bit set.
            # The fixture must too, before testing SHA mismatch behavior.
            self.exe.chmod(0o700)
        self.exe_sha = hashlib.sha256(self.exe.read_bytes()).hexdigest()

    def test_verified_gguf_requires_actual_bytes_and_manifest(self):
        with patch("forgeos.managed_llama.safe_model_file_budget", return_value=1024):
            path, identity = _verified_model(self.model)
        self.assertEqual(path, self.model)
        self.assertEqual(identity["sha256"], self.model_sha)

        self.model.write_bytes(b"GGUF" + b"X" * 28)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            _verified_model(self.model)

    def test_gguf_requires_manifest_and_true_gguf_header(self):
        metadata = self.model.with_name(self.model.name + ".forgeos.json")
        metadata.unlink()
        with self.assertRaisesRegex(ValueError, "ForgeOS GGUF downloads"):
            _verified_model(self.model)
        bad = b"FAKE" + b"\x00" * 28
        self.model.write_bytes(bad)
        metadata.write_text(json.dumps({
            "bytes": len(bad), "sha256": hashlib.sha256(bad).hexdigest()
        }))
        with patch("forgeos.managed_llama.safe_model_file_budget", return_value=1024):
            with self.assertRaisesRegex(ValueError, "GGUF header"):
                _verified_model(self.model)

    def test_memory_budget_preflight_is_conservative(self):
        with patch("forgeos.managed_llama.safe_model_file_budget", return_value=16):
            with self.assertRaisesRegex(RuntimeError, "free RAM budget"):
                _verified_model(self.model)

    def test_corrupt_manifest_is_bounded(self):
        metadata = self.model.with_name(self.model.name + ".forgeos.json")
        metadata.write_bytes(b"x" * 5000)
        with self.assertRaisesRegex(ValueError, "too large"):
            _verified_model(self.model)
        metadata.write_text("[]")
        with self.assertRaisesRegex(ValueError, "format"):
            _verified_model(self.model)
        metadata.write_text(json.dumps({"bytes": 1, "sha256": "1" * 64}))
        with self.assertRaisesRegex(ValueError, "identity"):
            _verified_model(self.model)

    def test_symlink_model_rejected_if_supported(self):
        link = self.root / "linked.gguf"
        try:
            link.symlink_to(self.model)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable without elevation")
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            _verified_model(link)

    def test_binary_requires_trusted_hash_and_exact_bytes(self):
        with self.assertRaisesRegex(ValueError, "64-character"):
            _verified_runtime(self.exe, "unsafe")
        with self.assertRaisesRegex(ValueError, "did not match"):
            _verified_runtime(self.exe, "f" * 64)
        if __import__("os").name != "nt":
            self.exe.chmod(0o700)
        self.assertEqual(_verified_runtime(self.exe, self.exe_sha), self.exe)

    def test_private_child_env_is_os_allowlist_not_prefix_denylist(self):
        inputs = {
            "PATH": "safe", "SYSTEMROOT": "C:/Windows",
            "TEMP": "C:/Temp", "HOME": "/tmp/user",
            "LLAMA_ARG_HOST": "0.0.0.0",
            "HF_TOKEN": "secret", "OPENAI_API_KEY": "secret",
            "ANTHROPIC_API_KEY": "secret", "HTTPS_PROXY": "proxy",
            "NO_PROXY": "localhost", "SAFE_VALUE": "untrusted",
            "GGML_CUDA_ENABLE_UNIFIED_MEMORY": "1",
            "OMP_NUM_THREADS": "128", "CUDA_VISIBLE_DEVICES": "0",
            "PYTHONPATH": "/tmp/untrusted", "LD_PRELOAD": "bad.so",
            "DYLD_INSERT_LIBRARIES": "bad.dylib",
            "FORGEOS_SECRET": "do-not-inherit",
        }
        with patch.dict("forgeos.managed_llama.os.environ", inputs, clear=True):
            self.assertEqual(_child_environment(), {
                "PATH": "safe", "SYSTEMROOT": "C:/Windows",
                "TEMP": "C:/Temp", "HOME": "/tmp/user"})

    def test_child_environment_preserves_only_required_case_insensitive_os_keys(self):
        with patch.dict("forgeos.managed_llama.os.environ", {
                "Path": "C:/bin", "SystemRoot": "C:/Windows",
                "LOCALAPPDATA": "C:/Users/u/AppData/Local",
                "COMSPEC": "C:/Windows/cmd.exe",
                "HUGGINGFACE_HUB_TOKEN": "secret",
                "HTTP_PROXY": "https://proxy.example",
                "GGML_LOG_LEVEL": "5"}, clear=True):
            # Windows' os.environ canonicalizes names to uppercase.
            # Compare intended keys/values on both Windows and POSIX.
            observed = {key.upper(): value
                        for key, value in _child_environment().items()}
            self.assertEqual(observed, {
                "PATH": "C:/bin", "SYSTEMROOT": "C:/Windows",
                "LOCALAPPDATA": "C:/Users/u/AppData/Local"})

    @unittest.skipIf(os.name == "nt", "POSIX shebang-based runtime harness")
    def test_real_local_process_authentication_inference_and_clean_shutdown(self):
        """Exercise actual OS process, loopback HTTP, bearer auth and teardown.

        Uses a tiny explicitly local Python HTTP stub, never downloads or
        runs llama.cpp weights. A real Windows/GGUF acceptance is separate.
        """
        self.exe.write_text(r'''#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser(add_help=False)
for arg in ("host", "port", "alias", "api-key-file"):
    parser.add_argument("--" + arg, required=True)
options, other = parser.parse_known_args()
with open(options.api_key_file, encoding="ascii") as secret:
    api_key = secret.read().strip()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, data):
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authenticated(self):
        return self.headers.get("Authorization") == "Bearer " + api_key

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ok"})
        elif self.path == "/v1/models":
            if not self.authenticated():
                self.reply(401, {"error": "missing bearer"})
                return
            self.reply(200, {"data": [{"id": options.alias}]})
        else:
            self.reply(404, {"error": "unknown path"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.reply(404, {"error": "unknown path"})
            return
        if not self.authenticated():
            self.reply(401, {"error": "missing bearer"})
            return
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > 65536:
            self.reply(413, {"error": "oversized request"})
            return
        request = json.loads(self.rfile.read(size))
        if not request.get("messages"):
            self.reply(400, {"error": "missing messages"})
            return
        self.reply(200, {"choices": [{
            "finish_reason": "stop",
            "message": {"content": '{"files":[]}'}
        }]})

ThreadingHTTPServer((options.host, int(options.port)), Handler).serve_forever()
''', encoding="utf-8")
        self.exe.chmod(0o700)
        binary_sha = hashlib.sha256(self.exe.read_bytes()).hexdigest()
        controller = ManagedLlamaServer()
        try:
            with patch("forgeos.managed_llama.safe_model_file_budget",
                       return_value=1024):
                details = controller.start(
                    executable=self.exe, executable_sha256=binary_sha,
                    model_path=self.model, timeout=12, context=512, threads=1)
            self.assertTrue(controller.running())
            self.assertGreater(details["pid"], 1)
            client = LocalChatModel(
                backend="llama.cpp", model=ALIAS,
                endpoint=details["endpoint"], api_key=controller.api_key)
            self.assertEqual(client.installed()[0]["name"], ALIAS)
            self.assertEqual(client.generate_edits("Create no files"),
                             '{"files":[]}')
            from forgeos.local_models import _open
            with self.assertRaises(HTTPError) as unauthenticated:
                with _open(Request(details["endpoint"] + "/models"), timeout=3):
                    pass
            self.assertEqual(unauthenticated.exception.code, 401)
        finally:
            controller.stop()
        self.assertFalse(controller.running())
        self.assertIsNone(controller.api_key)
        self.assertIsNone(controller.endpoint)

        # Exercise genuine unexpected process death as well as ordinary
        # shutdown: no silent restart, no lingering credential after stop.
        crashed = ManagedLlamaServer()
        try:
            with patch("forgeos.managed_llama.safe_model_file_budget",
                       return_value=1024):
                crashed.start(
                    executable=self.exe, executable_sha256=binary_sha,
                    model_path=self.model, timeout=12, context=512, threads=1)
            self.assertTrue(crashed.running())
            crashed.process.kill()
            crashed.process.wait(timeout=4)
            from forgeos.gui import _managed_session_event
            self.assertEqual(_managed_session_event(
                crashed, now=1000, last_used=999, task_busy=False,
                operation_busy=False), "crashed")
        finally:
            crashed.stop()
        self.assertIsNone(crashed.api_key)
        self.assertIsNone(crashed.endpoint)
        self.assertFalse(crashed.running())

    def test_installed_cpu_rechecks_official_archive_and_every_dll(self):
        controller = ManagedLlamaServer()
        verified = {
            "path": str(self.exe), "sha256": self.exe_sha,
            "tag": "fixture-release", "status": "verified_official_cpu",
        }
        with (patch("forgeos.runtime_installer.verify_installed_cpu_runtime",
                    return_value=verified) as check,
              patch.object(controller, "start",
                           return_value={"model": ALIAS}) as start):
            result = controller.start_installed_cpu(
                model_path=self.model, directory=self.root,
                timeout=18, threads=2, context=1024)
        self.assertEqual(result, {"model": ALIAS})
        check.assert_called_once_with(self.root)
        start.assert_called_once_with(
            executable=str(self.exe), executable_sha256=self.exe_sha,
            model_path=self.model, timeout=18, threads=2, context=1024)

    def test_tampered_installed_runtime_never_spawns_server(self):
        controller = ManagedLlamaServer()
        with (patch("forgeos.runtime_installer.verify_installed_cpu_runtime",
                    side_effect=RuntimeError("DLL differs from pinned archive"))
              as check,
              patch.object(controller, "start") as start):
            with self.assertRaisesRegex(RuntimeError, "pinned archive"):
                controller.start_installed_cpu(model_path=self.model)
            check.assert_called_once_with(None)
            start.assert_not_called()
        self.assertFalse(controller.running())

    def test_explicit_context_cannot_bypass_low_ram_profile(self):
        controller = ManagedLlamaServer()
        with (patch("forgeos.managed_llama._verified_model",
                    return_value=(self.model, {"sha256": self.model_sha})),
              patch("forgeos.managed_llama._verified_runtime",
                    return_value=self.exe),
              patch("forgeos.managed_llama.local_memory_settings",
                    return_value={"num_ctx": 1024}),
              patch("forgeos.managed_llama.subprocess.Popen") as spawn):
            with self.assertRaisesRegex(ValueError, "current RAM profile"):
                controller.start(executable=self.exe,
                                 executable_sha256=self.exe_sha,
                                 model_path=self.model,
                                 context=4096, threads=1)
            spawn.assert_not_called()
        self.assertIsNone(controller.process)
        self.assertIsNone(controller.api_key)

    def test_ram_pressure_between_sha_check_and_launch_fails_closed(self):
        controller = ManagedLlamaServer()
        with (patch("forgeos.managed_llama._verified_model",
                    return_value=(self.model, {"sha256": self.model_sha})),
              patch("forgeos.managed_llama._verified_runtime",
                    return_value=self.exe),
              patch("forgeos.managed_llama.local_memory_settings",
                    return_value={"num_ctx": 8192}),
              patch("forgeos.managed_llama.safe_model_file_budget",
                    return_value=8) as budget,
              patch("forgeos.managed_llama.system_memory"),
              patch("forgeos.managed_llama.subprocess.Popen") as spawn):
            with self.assertRaisesRegex(RuntimeError, "Free RAM fell"):
                controller.start(executable=self.exe,
                                 executable_sha256=self.exe_sha,
                                 model_path=self.model,
                                 context=1024, threads=1)
            budget.assert_called_once()
            spawn.assert_not_called()
        self.assertIsNone(controller.process)
        self.assertIsNone(controller.api_key)

    def test_managed_launch_is_local_cpu_authenticated_single_process(self):
        from forgeos.managed_llama import _verified_model, _verified_runtime
        proc = Mock()
        proc.poll.return_value = None
        proc.pid = 12345
        proc.wait.return_value = 0
        server = ManagedLlamaServer()
        open_calls = []
        def read_health(request, *, timeout):
            open_calls.append(request)
            return _Response(b'{"status":"ok"}')
        with (patch("forgeos.managed_llama._verified_model",
                    return_value=(self.model, {"sha256": self.model_sha})),
              patch("forgeos.managed_llama._verified_runtime",
                    return_value=self.exe),
              patch("forgeos.managed_llama._unused_loopback_port",
                    return_value=17089),
              patch("forgeos.managed_llama._open", side_effect=read_health),
              patch("forgeos.managed_llama.LocalChatModel.installed",
                    return_value=[{"name": ALIAS}]),
              patch("forgeos.managed_llama.subprocess.Popen",
                    return_value=proc) as launch,
              patch("forgeos.managed_llama._attach_windows_job",
                    return_value=1234) as attached,
              patch("forgeos.managed_llama._release_windows_job") as released,
              patch("forgeos.managed_llama._terminate_posix_group") as grouped):
            info = server.start(
                executable=self.exe, executable_sha256=self.exe_sha,
                model_path=self.model, context=1024, threads=2,
                timeout=1)
            self.assertEqual(info["endpoint"], "http://127.0.0.1:17089/v1")
            self.assertEqual(info["model"], ALIAS)
            self.assertEqual(info["sha256"], self.model_sha)
            self.assertEqual(info["runtime_sha256"], self.exe_sha)
            self.assertTrue(server.running())
            command = launch.call_args.args[0]
            self.assertEqual(command[0], str(self.exe))
            self.assertIn("--n-gpu-layers", command)
            self.assertEqual(command[command.index("--n-gpu-layers") + 1], "0")
            self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
            self.assertEqual(command[command.index("--port") + 1], "17089")
            self.assertEqual(command[command.index("--model") + 1], str(self.model))
            self.assertEqual(command[command.index("--parallel") + 1], "1")
            self.assertTrue(launch.call_args.kwargs["shell"] is False)
            self.assertEqual(len(open_calls), 1)
            self.assertEqual(
                open_calls[0].full_url,
                "http://127.0.0.1:17089/health")
            self.assertTrue(server.api_key)
            key_file = Path(command[command.index("--api-key-file") + 1])
            self.assertTrue(key_file.exists())
            self.assertEqual(key_file.read_text().strip(), server.api_key)
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                server.start(executable=self.exe, executable_sha256=self.exe_sha,
                             model_path=self.model)
            server.stop()
            self.assertFalse(key_file.exists())
            self.assertIsNone(server.api_key)
            self.assertIsNone(server.process)
            if __import__("os").name == "nt":
                attached.assert_called_once_with(proc)
                released.assert_called_once_with(1234)
                proc.terminate.assert_not_called()
            else:
                attached.assert_not_called()
                grouped.assert_called_once()
                proc.terminate.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object cleanup")
    def test_job_release_failure_still_terminates_process_and_erases_secret(self):
        controller = ManagedLlamaServer()
        proc = Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        controller.process = proc
        controller._windows_job = 1234
        controller.api_key = "sensitive-test-key"
        controller.endpoint = "http://127.0.0.1:12345/v1"
        with patch("forgeos.managed_llama._release_windows_job",
                   side_effect=OSError("native job release failed")):
            with self.assertRaisesRegex(OSError, "native job release failed"):
                controller.stop()
        proc.terminate.assert_called_once_with()
        proc.wait.assert_called_once_with(timeout=4)
        self.assertIsNone(controller.process)
        self.assertIsNone(controller.api_key)
        self.assertIsNone(controller.endpoint)

    def test_failed_server_start_does_not_leave_process_or_secret(self):
        proc = Mock()
        proc.poll.return_value = 7
        proc.wait.return_value = 7
        instance = ManagedLlamaServer()
        with (patch("forgeos.managed_llama._verified_model",
                    return_value=(self.model, {"sha256": self.model_sha})),
              patch("forgeos.managed_llama._verified_runtime",
                    return_value=self.exe),
              patch("forgeos.managed_llama._unused_loopback_port",
                    return_value=17090),
              patch("forgeos.managed_llama.subprocess.Popen",
                    return_value=proc),
              patch("forgeos.managed_llama._attach_windows_job",
                    return_value=1234),
              patch("forgeos.managed_llama._release_windows_job")):
            with self.assertRaisesRegex(RuntimeError, "exited"):
                instance.start(executable=self.exe, executable_sha256=self.exe_sha,
                               model_path=self.model, timeout=1)
        self.assertIsNone(instance.api_key)
        self.assertIsNone(instance.process)

    def test_connection_failure_times_out_and_terminates(self):
        proc = Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        controller = ManagedLlamaServer()
        with (patch("forgeos.managed_llama._verified_model",
                    return_value=(self.model, {"sha256": self.model_sha})),
              patch("forgeos.managed_llama._verified_runtime",
                    return_value=self.exe),
              patch("forgeos.managed_llama.subprocess.Popen",
                    return_value=proc),
              patch("forgeos.managed_llama._open",
                    side_effect=URLError("server unavailable")),
              patch("forgeos.managed_llama.time.monotonic",
                    side_effect=[0.0, 2.0]),
              patch("forgeos.managed_llama.time.sleep") as sleep,
              patch("forgeos.managed_llama._attach_windows_job",
                    return_value=1234),
              patch("forgeos.managed_llama._release_windows_job"),
              patch("forgeos.managed_llama._terminate_posix_group")):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                controller.start(executable=self.exe, executable_sha256=self.exe_sha,
                                 model_path=self.model, timeout=1)
            sleep.assert_not_called()
        if __import__("os").name != "nt":
            proc.terminate.assert_called_once()
        self.assertIsNone(controller.process)

    @unittest.skipUnless(__import__("os").name == "nt",
                         "Windows Job Object integration")
    def test_real_windows_job_kills_server_process(self):
        import subprocess
        import sys
        from forgeos.managed_llama import _attach_windows_job, _release_windows_job
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        job = None
        try:
            job = _attach_windows_job(proc)
            self.assertIsNotNone(job)
            _release_windows_job(job)
            job = None
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.returncode)
        finally:
            if job is not None:
                _release_windows_job(job)
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    def test_managed_checkpoint_fails_closed_without_pinned_session_identity(self):
        from forgeos.recovery import _checkpoint_contract
        checkpoint = {"contract": {
            "preset": "python-unittest", "max_repairs": 1,
            "model": "forgeos-managed", "backend": "llama.cpp",
            "endpoint": "http://127.0.0.1:17089/v1"}}
        with self.assertRaisesRegex(ValueError, "automatic recovery is not yet supported"):
            _checkpoint_contract(checkpoint)
        checkpoint["contract"].update(
            managed_model_sha256="a" * 64,
            managed_runtime_sha256="b" * 64)
        with self.assertRaisesRegex(ValueError, "authenticated process recreation"):
            _checkpoint_contract(checkpoint)
        checkpoint["contract"]["managed_model_sha256"] = "bad-hash"
        with self.assertRaisesRegex(ValueError, "Invalid managed GGUF checkpoint identity"):
            _checkpoint_contract(checkpoint)
        del checkpoint["contract"]["managed_model_sha256"]
        del checkpoint["contract"]["managed_runtime_sha256"]
        # Existing manually configured llama.cpp checkpoints still work.
        checkpoint["contract"]["model"] = "normal-coder"
        self.assertEqual(
            _checkpoint_contract(checkpoint)["model"], "normal-coder")

    def test_authenticated_api_key_transmitted_only_to_local_endpoint(self):
        client = LocalChatModel(backend="llama.cpp", model="forgeos-managed",
                                endpoint="http://127.0.0.1:17089/v1",
                                api_key="a" * 32)
        with patch("forgeos.local_models._open", return_value=_Response(
                b'{"data":[{"id":"forgeos-managed"}]}')) as opened:
            self.assertEqual(client.installed()[0]["name"], "forgeos-managed")
            headers = dict(opened.call_args.args[0].header_items())
            self.assertEqual(headers["Authorization"], "Bearer " + "a" * 32)
        with self.assertRaisesRegex(ValueError, "Invalid local server API"):
            LocalChatModel(backend="llama.cpp", model="foo",
                           api_key="bad\nheader: injection")
        with self.assertRaisesRegex(ValueError, "managed API key"):
            create_model(backend="ollama", model="auto", api_key="x" * 32)


if __name__ == "__main__":
    unittest.main()
