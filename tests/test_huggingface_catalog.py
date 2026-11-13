"""Read-only Hugging Face catalog validation and memory-bounding tests."""
import io
import json
import socket
import unittest
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError, URLError
from unittest.mock import patch
from forgeos.huggingface_catalog import (
    MAX_HUB_JSON_BYTES, list_gguf_files, search_gguf,
)


class Response(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class HuggingFaceCatalogTests(unittest.TestCase):
    def test_redirect_handler_rejects_cross_origin_and_non_https(self):
        from urllib.request import Request
        from forgeos.huggingface_catalog import _HubRedirectHandler
        handler = _HubRedirectHandler()
        request = Request("https://huggingface.co/api/models?q=gguf")
        same_origin = handler.redirect_request(
            request, None, 302, "Found", {},
            "https://huggingface.co/api/models?page=2")
        self.assertEqual(same_origin.full_url,
                         "https://huggingface.co/api/models?page=2")
        for target in ("https://example.org/collect", "http://huggingface.co/api/models",
                       "https://huggingface.co.evil.example/api/models",
                       "https://user@huggingface.co/api/models"):
            with self.subTest(target=target), self.assertRaisesRegex(
                    RuntimeError, "outside its trusted HTTPS origin"):
                handler.redirect_request(request, None, 302, "Found", {}, target)

    def test_metadata_opener_bypasses_system_proxies(self):
        from urllib.request import Request
        from forgeos.huggingface_catalog import _open_hub
        response = Response(b"{}")
        with patch("forgeos.huggingface_catalog.build_opener") as build:
            build.return_value.open.return_value = response
            self.assertIs(_open_hub(Request("https://huggingface.co/api/models"),
                                   timeout=7), response)
            handlers = build.call_args.args
            self.assertTrue(any(type(handler).__name__ == "ProxyHandler"
                                and handler.proxies == {} for handler in handlers))
            self.assertTrue(any(type(handler).__name__ == "_HubRedirectHandler"
                                for handler in handlers))
            build.return_value.open.assert_called_once_with(
                unittest.mock.ANY, timeout=7)

    def test_hub_json_response_enforces_total_deadline_on_trickle(self):
        class Trickle(Response):
            def read1(self, size=-1):
                return super().read(1)

        response = Trickle(b'{"items":[]}')
        ticks = iter((0.0, 29.5, 29.75, 30.5))
        with patch("forgeos.huggingface_catalog._open_hub",
                   return_value=response), \
             patch("forgeos.huggingface_catalog.time.monotonic",
                   side_effect=lambda: next(ticks, 30.5)):
            with self.assertRaisesRegex(TimeoutError, "total response deadline"):
                from forgeos.huggingface_catalog import _hub_json_response
                _hub_json_response("https://huggingface.co/api/models")
        self.assertTrue(response.closed)

    def test_catalog_network_and_http_errors_have_actionable_messages(self):
        from forgeos.huggingface_catalog import catalog_error_message
        def http_message(code):
            error = HTTPError("https://huggingface.co", code, "test", {}, None)
            message = catalog_error_message(error)
            self.assertTrue(error.closed)
            return message
        dns = catalog_error_message(URLError(socket.gaierror(11001, "no host")))
        self.assertIn("DNS lookup failed", dns)
        self.assertNotIn("no host", dns)
        self.assertIn("private or gated", http_message(403))
        self.assertIn("could not find", http_message(404).lower())
        self.assertIn("rate limiting", http_message(429))
        self.assertIn("temporarily unavailable", http_message(503))

    def test_search_filters_non_gguf_and_gated_models(self):
        items = [
            {"id": "Qwen/coder-gguf", "tags": ["gguf", "license:apache-2.0"],
             "downloads": 10},
            {"id": "Other/secret-GGUF", "tags": ["gguf"], "gated": True},
            {"id": "Other/not-gguf", "tags": ["transformers"]},
            {"id": "Other/bad repo GGUF", "tags": ["gguf"]},
        ]
        with patch("forgeos.huggingface_catalog._open_hub",
                   return_value=Response(json.dumps(items).encode())) as opened:
            result = search_gguf("coder", limit=5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["repo"], "Qwen/coder-gguf")
        self.assertEqual(result[0]["license_tag"], "apache-2.0")
        self.assertFalse(result[0]["verified_compatible"])
        self.assertTrue(opened.call_args.args[0].full_url.startswith(
            "https://huggingface.co/api/models?"))

    def test_search_pagination_follows_opaque_same_origin_link(self):
        from urllib.parse import urlencode
        from forgeos.huggingface_catalog import search_gguf_page
        next_url = "https://huggingface.co/api/models?" + urlencode({
            "search": "coder", "limit": 2, "cursor": "opaque:value",
        })
        first = Response(json.dumps([{"id": "Owner/first-GGUF",
                                     "tags": ["gguf"]}]).encode(), {
            "Link": f'<{next_url}>; rel="next"',
        })
        second = Response(json.dumps([{"id": "Owner/second-GGUF",
                                      "tags": ["gguf"]}]).encode())
        with patch("forgeos.huggingface_catalog._open_hub",
                   side_effect=[first, second]) as opened:
            page1 = search_gguf_page("coder", limit=2)
            page2 = search_gguf_page(
                "coder", limit=2, next_page_url=page1["next_page_url"])
        self.assertEqual(page1["items"][0]["repo"], "Owner/first-GGUF")
        self.assertEqual(page2["items"][0]["repo"], "Owner/second-GGUF")
        self.assertEqual(opened.call_count, 2)
        self.assertIn("opaque%3Avalue", opened.call_args.args[0].full_url)

    def test_search_pagination_rejects_external_or_changed_query_link(self):
        from forgeos.huggingface_catalog import search_gguf_page
        links = (
            "https://evil.example/api/models?search=coder&limit=2&cursor=x",
            "https://huggingface.co/api/models?search=other&limit=2&cursor=x",
        )
        for next_url in links:
            with self.subTest(next_url=next_url):
                first = Response(json.dumps([]).encode(), {
                    "Link": f'<{next_url}>; rel="next"',
                })
                with patch("forgeos.huggingface_catalog._open_hub",
                           return_value=first) as opened:
                    with self.assertRaisesRegex(RuntimeError, "untrusted pagination"):
                        search_gguf_page("coder", limit=2)
                    self.assertEqual(opened.call_count, 1)

    def test_query_input_and_limit_rejected(self):
        for bad in ("", "\x00", "a", "x"*100):
            with self.assertRaises(ValueError):
                search_gguf(bad)
        with self.assertRaises(ValueError):
            search_gguf("coder", limit=100)
        with self.assertRaises(ValueError):
            search_gguf("coder", limit=True)

    def test_file_listing_carries_sha_only_from_lfs(self):
        sha = "a" * 64
        files = [
            {"type": "file", "path": "model-Q4_K_M.gguf", "size": 1_500_000_000,
             "lfs": {"oid": sha}},
            {"type": "file", "path": "model-Q8.gguf", "size": 3_000_000_000},
            {"type": "file", "path": "../bad.gguf", "size": 4},
            {"type": "directory", "path": "folder"},
        ]
        with patch("forgeos.huggingface_catalog._open_hub",
                   return_value=Response(json.dumps(files).encode())) as opened:
            result = list_gguf_files("Qwen/coder-GGUF")
        self.assertEqual(result, [
            {"path": "model-Q4_K_M.gguf", "bytes": 1_500_000_000, "sha256": sha},
            {"path": "model-Q8.gguf", "bytes": 3_000_000_000, "sha256": None},
        ])
        self.assertIn("Qwen/coder-GGUF/tree/main", opened.call_args.args[0].full_url)

    def test_catalog_oversize_rejected_without_json_decode(self):
        with patch("forgeos.huggingface_catalog._open_hub",
                   return_value=Response(b"X" * (MAX_HUB_JSON_BYTES + 1))):
            with self.assertRaisesRegex(RuntimeError, "bounded metadata"):
                search_gguf("coder")


    def test_model_revision_is_pinned_and_gated_is_refused(self):
        from forgeos.huggingface_catalog import model_revision
        sha = "b" * 40
        with patch("forgeos.huggingface_catalog._open_hub",
                   return_value=Response(json.dumps({"sha": sha}).encode())):
            self.assertEqual(model_revision("Qwen/Coder-GGUF"), sha)
        for item in ({"sha": "main"}, {"sha": sha, "gated": True},
                     {"sha": sha, "private": True}):
            with patch("forgeos.huggingface_catalog._open_hub",
                       return_value=Response(json.dumps(item).encode())):
                with self.assertRaises(RuntimeError):
                    model_revision("Qwen/Coder-GGUF")

    def test_pinned_revision_is_part_of_file_listing(self):
        from forgeos.huggingface_catalog import list_gguf_files
        revision = "c" * 40
        with patch("forgeos.huggingface_catalog._open_hub",
                   return_value=Response(b"[]")) as opened:
            self.assertEqual(list_gguf_files(
                "Qwen/Coder-GGUF", revision=revision), [])
        self.assertIn("/tree/" + revision, opened.call_args.args[0].full_url)
        with self.assertRaises(ValueError):
            list_gguf_files("Qwen/Coder-GGUF", revision="../main")

    def test_repo_inspection_returns_bounded_unverified_card_claims_and_files(self):
        from forgeos.huggingface_catalog import inspect_gguf_repo
        metadata = {"id": "Qwen/model-GGUF", "cardData": {
            "license": "apache-2.0", "model_type": " decoder-only  ",
            "author": "A" * 300, "unsafe_unknown_field": "ignored",
            "language": ["en", 42, " zh "], "quantization": "Q4_K_M",
            "context_length": 32768, "parameter_count": -1,
            "model_size": 10**100,
        }, "sha": "d" * 40}
        files = [{"type": "file", "path": "model.Q4_K_M.gguf",
                  "size": 100, "lfs": {"oid": "a" * 64}}]
        with patch("forgeos.huggingface_catalog._open_hub", side_effect=[
                Response(json.dumps(metadata).encode()),
                Response(json.dumps(files).encode())]) as opened:
            result = inspect_gguf_repo("Qwen/model-GGUF")
        self.assertEqual(result["claims"]["model_type"], "decoder-only")
        self.assertEqual(len(result["claims"]["author"]), 240)
        self.assertEqual(result["claims"]["language"], ["en", "zh"])
        self.assertEqual(result["claims"]["quantization"], "Q4_K_M")
        self.assertEqual(result["claims"]["context_length"], "32768")
        self.assertEqual(result["revision"], "d" * 40)
        self.assertNotIn("unsafe_unknown_field", result["claims"])
        self.assertNotIn("parameter_count", result["claims"])
        self.assertNotIn("model_size", result["claims"])
        self.assertEqual(len(result["files"]), 1)
        self.assertIn("/tree/" + "d" * 40, opened.call_args.args[0].full_url)
        details_url = opened.call_args_list[0].args[0].full_url
        self.assertEqual(parse_qs(urlsplit(details_url).query)["expand"],
                         ["cardData,sha,tags,author"])

    def test_untrusted_card_control_characters_are_normalized(self):
        from forgeos.huggingface_catalog import inspect_gguf_repo
        metadata = {
            "cardData": {"model_name": "safe\u202e\x00 name",
                         "language": ["en\nUS", "\x00"]},
            "tags": ["license:apache-2.0\u202e\nspoof"],
            "sha": "f" * 40,
        }
        with patch("forgeos.huggingface_catalog._open_hub", side_effect=[
                Response(json.dumps(metadata).encode()), Response(b"[]")]):
            result = inspect_gguf_repo("Qwen/model-GGUF")
        self.assertEqual(result["claims"]["model_name"], "safe name")
        self.assertEqual(result["claims"]["language"], ["en US"])
        self.assertEqual(result["claims"]["license"], "apache-2.0 spoof")

    def test_gguf_file_listing_rejects_unsafe_paths_and_oversized_sizes(self):
        from forgeos.huggingface_catalog import list_gguf_files
        payload = [
            {"type": "file", "path": "good/model-Q4_K_M.gguf", "size": 1024,
             "lfs": {"oid": "a" * 64}},
            {"type": "file", "path": "../escape.gguf", "size": 1024},
            {"type": "file", "path": "line\nbreak.gguf", "size": 1024},
            {"type": "file", "path": "CON.gguf", "size": 1024},
            {"type": "file", "path": "huge.gguf", "size": 16 * 1024**3 + 1},
        ]
        with patch("forgeos.huggingface_catalog._open_hub",
                   return_value=Response(json.dumps(payload).encode())):
            result = list_gguf_files("Qwen/model-GGUF", revision="a" * 40)
        self.assertEqual([row["path"] for row in result],
                         ["good/model-Q4_K_M.gguf"])

    def test_catalog_size_cap_matches_downloader_limit(self):
        from forgeos.gguf_download import MAX_MODEL_BYTES
        from forgeos.huggingface_catalog import MAX_GGUF_BYTES
        self.assertEqual(MAX_GGUF_BYTES, MAX_MODEL_BYTES)

    def test_repo_inspection_uses_expanded_author_when_card_data_omits_it(self):
        from forgeos.huggingface_catalog import inspect_gguf_repo
        metadata = {"author": "  Qwen  ", "sha": "e" * 40}
        with patch("forgeos.huggingface_catalog._open_hub", side_effect=[
                Response(json.dumps(metadata).encode()), Response(b"[]")]):
            result = inspect_gguf_repo("Qwen/model-GGUF")
        self.assertEqual(result["claims"]["author"], "Qwen")

    def test_repo_inspection_rejects_private_or_gated_repo_before_file_lookup(self):
        from forgeos.huggingface_catalog import inspect_gguf_repo
        for flags in ({"private": True}, {"gated": True}):
            with patch("forgeos.huggingface_catalog._open_hub", return_value=
                       Response(json.dumps(flags).encode())) as opened:
                with self.assertRaisesRegex(RuntimeError, "Public"):
                    inspect_gguf_repo("Qwen/model-GGUF")
                self.assertEqual(opened.call_count, 1)
        with patch("forgeos.huggingface_catalog._open_hub", return_value=
                   Response(json.dumps({"sha": "main"}).encode())) as opened:
            with self.assertRaisesRegex(RuntimeError, "immutable revision"):
                inspect_gguf_repo("Qwen/model-GGUF")
            self.assertEqual(opened.call_count, 1)

    def test_invalid_file_response_and_path(self):
        for repo in ("../../passwd", "https://example.org/model", "name"):
            with self.assertRaises(ValueError):
                list_gguf_files(repo)
        with patch("forgeos.huggingface_catalog._open_hub",
                   return_value=Response(b'{"not":"array"}')):
            with self.assertRaisesRegex(RuntimeError, "Unexpected"):
                list_gguf_files("Qwen/model-GGUF")


if __name__ == "__main__":
    unittest.main()
