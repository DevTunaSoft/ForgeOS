"""Local endpoint security, compatibility and bounded-response regression tests."""
import io
import json
import unittest
from urllib.request import Request
from unittest.mock import Mock, patch

from forgeos.local_models import (
    BACKENDS, MAX_RESPONSE_BYTES, LocalChatModel, create_model,
    validate_endpoint,
)


class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


def payload(data):
    return Response(json.dumps(data).encode())


class LocalModelsTests(unittest.TestCase):
    def test_managed_listener_owner_check_runs_before_model_request(self):
        model = LocalChatModel(
            backend="llama.cpp", model="forgeos-managed",
            endpoint="http://127.0.0.1:40123/v1", api_key="k" * 32)
        model._listener_owner_check = Mock(
            side_effect=RuntimeError("listener ownership changed"))
        with patch("forgeos.local_models._read_json") as request:
            with self.assertRaisesRegex(RuntimeError, "ownership changed"):
                model.generate_edits("a bounded test prompt")
        model._listener_owner_check.assert_called_once_with()
        request.assert_not_called()

    def test_managed_listener_change_after_request_discards_response(self):
        model = LocalChatModel(
            backend="llama.cpp", model="forgeos-managed",
            endpoint="http://127.0.0.1:40123/v1", api_key="k" * 32)
        model._listener_owner_check = Mock(
            side_effect=[None, RuntimeError("listener ownership changed")])
        with patch("forgeos.local_models._read_json", return_value={
                "choices": [{"finish_reason": "stop",
                             "message": {"content": '{"files":[]}'}}]}) as request:
            with self.assertRaisesRegex(RuntimeError, "ownership changed"):
                model.generate_edits("a bounded test prompt")
        self.assertEqual(model._listener_owner_check.call_count, 2)
        request.assert_called_once()

    def test_real_loopback_http_trickle_has_bounded_deadline(self):
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from forgeos.local_models import _read_json

        class Drip(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                for item in b'{"data":[1,2,3,4,5,6,7,8,9]}':
                    try:
                        self.wfile.write(bytes([item]))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    time.sleep(0.15)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Drip)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/v1/models")
            with self.assertRaisesRegex(TimeoutError, "total deadline"):
                _read_json(request, limit=4096, timeout=1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_trickled_local_response_hits_total_deadline(self):
        from forgeos.local_models import _read_json
        class Trickle(Response):
            def read1(self, size=-1):
                return super().read(1)

        # Total time advances even though each socket read returns a byte.
        # A per-read timeout alone cannot stop this kind of slow response.
        ticks = iter((0.0, 0.5, 1.5, 4.5, 9.5, 10.0))
        slow = Trickle(b'{"data": [1, 2, 3]}')
        with (patch("forgeos.local_models._open", return_value=slow),
              patch("forgeos.local_models.time.monotonic",
                    side_effect=lambda: next(ticks))):
            with self.assertRaisesRegex(TimeoutError, "total deadline"):
                _read_json(Request("http://127.0.0.1:8080/v1/models"),
                           limit=1000, timeout=10)
        self.assertTrue(slow.closed)

    def test_local_response_reads_small_chunks_without_full_buffer_request(self):
        from forgeos.local_models import _read_json
        class TrackingResponse(Response):
            reads = 0
            max_size = 0
            def read1(self, size=-1):
                self.reads += 1
                self.max_size = max(self.max_size, size)
                return super().read(size)

        body = {"choices": [{"message": {"content": "x" * 120000}}]}
        response = TrackingResponse(json.dumps(body).encode("utf-8"))
        with patch("forgeos.local_models._open", return_value=response):
            value = _read_json(Request("http://127.0.0.1:8080/v1/chat/completions"),
                               limit=150000, timeout=5)
        self.assertEqual(value, body)
        self.assertGreater(response.reads, 2)
        self.assertLessEqual(response.max_size, 8192)
        self.assertTrue(response.closed)

    def test_local_response_limit_and_timeout_inputs_fail_before_connection(self):
        from forgeos.local_models import _read_json
        req = Request("http://127.0.0.1:8080/v1/models")
        with patch("forgeos.local_models._open") as opened:
            for limit, timeout in ((0, 10), (-1, 10), (1, 0),
                                   (1, 181), (True, 10), (10, False)):
                with self.subTest(limit=limit, timeout=timeout):
                    with self.assertRaises(ValueError):
                        _read_json(req, limit=limit, timeout=timeout)
            opened.assert_not_called()

    def test_local_chat_rejects_oversized_prompts_before_network(self):
        from forgeos.core import MAX_LOCAL_PROMPT_BYTES, MAX_TASK_DESCRIPTION_BYTES
        model = create_model(backend="llama.cpp", model="my-coder:1b")
        with (patch("forgeos.local_models._open") as connection,
              patch("forgeos.local_models.local_memory_settings") as memory):
            with self.assertRaisesRegex(ValueError, "64 KiB"):
                model.generate_edits("x" * (MAX_LOCAL_PROMPT_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "64 KiB"):
                model.generate_edits("界" * (MAX_LOCAL_PROMPT_BYTES // 3 + 1))
            with self.assertRaisesRegex(ValueError, "16 KiB"):
                model.plan("x" * (MAX_TASK_DESCRIPTION_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "invalid Unicode"):
                model.generate_edits("\ud800")
            connection.assert_not_called()
            memory.assert_not_called()

    def test_preserves_original_ollama_factory(self):
        from forgeos.core import Ollama
        self.assertIsInstance(create_model(backend="ollama", model="auto"), Ollama)
        with self.assertRaises(ValueError):
            create_model(backend="ollama", model="auto",
                         endpoint="http://127.0.0.1:1234/v1")

    def test_backends_available_with_local_defaults(self):
        for name in BACKENDS[1:]:
            with self.subTest(name=name):
                model = create_model(backend=name, model="my-coder:1b")
                self.assertEqual(model.backend, name)
                self.assertTrue(model.base.startswith("http://127.0.0.1:"))

    def test_endpoint_validation_blocks_remote_dns_proxy_redirect_sources(self):
        bad = (
            "https://127.0.0.1:8080/v1",
            "https://api.openai.com/v1",
            "http://localhost:8080/v1",
            "http://192.168.0.2:8080/v1",
            "http://127.0.0.2:8080/v1",
            "http://127.0.0.1:8080/v1?url=https://evil.example",
            "http://x@127.0.0.1:8080/v1",
            "http://127.0.0.1:8080/v1/completions",
            "http://127.0.0.1:99999/v1",
            "http://127.0.0.1:8080#bypass",
            "file:///etc/passwd",
            "http://127.0.0.1",
            "http://127.0.0.1:0/v1",
        )
        for url in bad:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_endpoint(url)
        self.assertEqual(validate_endpoint("http://127.0.0.1:1234"),
                         "http://127.0.0.1:1234/v1")
        self.assertEqual(validate_endpoint("http://[::1]:8080/v1/"),
                         "http://[::1]:8080/v1")

    def test_rejects_bad_backend_and_model(self):
        for backend, model in (("remote", "model"), ("llama.cpp", "../model"),
                               ("lm-studio", "bad name"), ("lm-studio", "")):
            with self.subTest(backend=backend, model=model):
                with self.assertRaises(ValueError):
                    create_model(backend=backend, model=model)

    def test_generate_edit_manifest_with_chat_completions(self):
        model = LocalChatModel(backend="llama.cpp", model="coder-3b")
        answer = '{"files":[{"path":"x.py","content":"pass"}]}'
        with patch("forgeos.local_models._open",
                   return_value=payload({"choices": [
                       {"finish_reason": "stop", "message": {"content": answer}}
                   ]})) as opened:
            self.assertEqual(model.generate_edits("Fix a test"), answer)
        req = opened.call_args.args[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:8080/v1/chat/completions")
        data = json.loads(req.data)
        self.assertEqual(data["model"], "coder-3b")
        self.assertFalse(data["stream"])
        self.assertIn("Fix a test", data["messages"][0]["content"])

    def test_planning_preserves_contract(self):
        model = LocalChatModel(backend="lm-studio", model="coder")
        with patch("forgeos.local_models._open",
                   return_value=payload({"choices": [
                       {"message": {"content": "Plan then test"}}
                   ]})) as opened:
            self.assertEqual(model.plan("Make a timer"), "Plan then test")
        request = json.loads(opened.call_args.args[0].data)
        self.assertIn("acceptance tests", request["messages"][0]["content"])
        self.assertLessEqual(request["max_tokens"], 900)

    def test_rejects_invalid_and_oversize_model_responses(self):
        model = LocalChatModel(backend="openai-local", model="coder")
        invalid = (
            b"{",
            b"[]",
            b'{"choices":[]}',
            b'{"choices":[{"message":{"content":""}}]}',
            b'{"choices":[{"message":{"content":[{"text":"foo"}]}}]}',
            b'{"choices":[{"finish_reason":"length","message":{"content":"partial"}}]}',
            b"x" * (MAX_RESPONSE_BYTES + 1),
        )
        for raw in invalid:
            with self.subTest(length=len(raw)):
                with patch("forgeos.local_models._open", return_value=Response(raw)):
                    with self.assertRaises(RuntimeError):
                        model.generate_edits("Do work")

    def test_model_catalog_filters_invalid_and_oversized(self):
        model = LocalChatModel(backend="llama.cpp", model="coder")
        with patch("forgeos.local_models._open", return_value=payload({
            "data": [{"id": "coder:1b", "size": 500_000_000},
                     {"id": "coder;rm", "size": 1},
                     {"id": "coder:3b", "meta": {"size": 2_000_000_000}},
                     {"size": 1}]
        })):
            self.assertEqual(model.installed(), [
                {"name": "coder:1b", "size": 500_000_000},
                {"name": "coder:3b", "size": 2_000_000_000},
            ])

    def test_auto_model_requires_reported_size(self):
        model = LocalChatModel(backend="llama.cpp", model="auto")
        with patch("forgeos.local_models._open", return_value=payload({
            "data": [{"id": "small-coder"}]
        })):
            with self.assertRaisesRegex(RuntimeError, "choose a model name"):
                model.generate_edits("Fix crash")

    def test_auto_model_obeys_ram_budget(self):
        from forgeos.model_routing import MemorySnapshot
        model = LocalChatModel(backend="llama.cpp", model="auto")
        gib = 1024 ** 3
        responses = [
            payload({"data": [
                {"id": "small-coder", "size": 1 * gib},
                {"id": "large-coder", "size": 12 * gib}]}),
            payload({"choices": [
                {"finish_reason": "stop", "message": {"content": "{}"}}]})
        ]
        with patch("forgeos.local_models._open", side_effect=responses) as opened, \
             patch("forgeos.local_models.system_memory",
                   return_value=MemorySnapshot(16*gib, 8*gib)):
            self.assertEqual(model.generate_edits("Fix crash"), "{}")
        sent = json.loads(opened.call_args.args[0].data)
        self.assertEqual(sent["model"], "small-coder")

    def test_rejects_large_request_without_network(self):
        model = LocalChatModel(backend="llama.cpp", model="small-coder")
        with patch("forgeos.local_models._open") as opened:
            with self.assertRaises(ValueError):
                model.generate_edits("x" * (1024 * 1024))
            opened.assert_not_called()

    def test_redirects_disabled_and_proxies_ignored(self):
        with patch("forgeos.local_models.build_opener") as build:
            from forgeos.local_models import _open
            req = unittest.mock.Mock()
            _open(req, timeout=1)
            self.assertEqual(len(build.call_args.args), 2)
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsNone(build.call_args.args[1].redirect_request(
                None, None, 302, "redirect", None, "https://example.com"))



class LocalAuthenticationStateTests(unittest.TestCase):
    def test_authentication_state_never_exposes_secret(self):
        from forgeos.local_models import LocalChatModel
        plain = LocalChatModel(
            backend="llama.cpp", model="test-model",
            endpoint="http://127.0.0.1:8080/v1")
        secured = LocalChatModel(
            backend="llama.cpp", model="test-model",
            endpoint="http://127.0.0.1:8080/v1",
            api_key="A" * 24)
        self.assertFalse(plain.authenticated)
        self.assertTrue(secured.authenticated)
        self.assertFalse(hasattr(secured, "api_key"))
        self.assertNotEqual(secured.authenticated, "A" * 24)


if __name__ == "__main__":
    unittest.main()
