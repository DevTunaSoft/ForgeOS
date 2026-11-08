"""Local endpoint security, compatibility and bounded-response regression tests."""
import io
import json
import unittest
from unittest.mock import patch

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
        self.assertNotIn("A" * 24, repr(secured.__dict__))


if __name__ == "__main__":
    unittest.main()
