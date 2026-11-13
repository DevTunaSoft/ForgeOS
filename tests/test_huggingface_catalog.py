"""Read-only Hugging Face catalog validation and memory-bounding tests."""
import io
import json
import unittest
from unittest.mock import patch
from forgeos.huggingface_catalog import (
    MAX_HUB_JSON_BYTES, list_gguf_files, search_gguf,
)


class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


class HuggingFaceCatalogTests(unittest.TestCase):
    def test_search_filters_non_gguf_and_gated_models(self):
        items = [
            {"id": "Qwen/coder-gguf", "tags": ["gguf", "license:apache-2.0"],
             "downloads": 10},
            {"id": "Other/secret-GGUF", "tags": ["gguf"], "gated": True},
            {"id": "Other/not-gguf", "tags": ["transformers"]},
            {"id": "Other/bad repo GGUF", "tags": ["gguf"]},
        ]
        with patch("forgeos.huggingface_catalog.urlopen",
                   return_value=Response(json.dumps(items).encode())) as opened:
            result = search_gguf("coder", limit=5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["repo"], "Qwen/coder-gguf")
        self.assertEqual(result[0]["license_tag"], "apache-2.0")
        self.assertFalse(result[0]["verified_compatible"])
        self.assertTrue(opened.call_args.args[0].full_url.startswith(
            "https://huggingface.co/api/models?"))

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
        with patch("forgeos.huggingface_catalog.urlopen",
                   return_value=Response(json.dumps(files).encode())) as opened:
            result = list_gguf_files("Qwen/coder-GGUF")
        self.assertEqual(result, [
            {"path": "model-Q4_K_M.gguf", "bytes": 1_500_000_000, "sha256": sha},
            {"path": "model-Q8.gguf", "bytes": 3_000_000_000, "sha256": None},
        ])
        self.assertIn("Qwen/coder-GGUF/tree/main", opened.call_args.args[0].full_url)

    def test_catalog_oversize_rejected_without_json_decode(self):
        with patch("forgeos.huggingface_catalog.urlopen",
                   return_value=Response(b"X" * (MAX_HUB_JSON_BYTES + 1))):
            with self.assertRaisesRegex(RuntimeError, "bounded metadata"):
                search_gguf("coder")


    def test_model_revision_is_pinned_and_gated_is_refused(self):
        from forgeos.huggingface_catalog import model_revision
        sha = "b" * 40
        with patch("forgeos.huggingface_catalog.urlopen",
                   return_value=Response(json.dumps({"sha": sha}).encode())):
            self.assertEqual(model_revision("Qwen/Coder-GGUF"), sha)
        for item in ({"sha": "main"}, {"sha": sha, "gated": True},
                     {"sha": sha, "private": True}):
            with patch("forgeos.huggingface_catalog.urlopen",
                       return_value=Response(json.dumps(item).encode())):
                with self.assertRaises(RuntimeError):
                    model_revision("Qwen/Coder-GGUF")

    def test_pinned_revision_is_part_of_file_listing(self):
        from forgeos.huggingface_catalog import list_gguf_files
        revision = "c" * 40
        with patch("forgeos.huggingface_catalog.urlopen",
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
            "context_length": 32768,
        }, "sha": "d" * 40}
        files = [{"type": "file", "path": "model.Q4_K_M.gguf",
                  "size": 100, "lfs": {"oid": "a" * 64}}]
        with patch("forgeos.huggingface_catalog.urlopen", side_effect=[
                Response(json.dumps(metadata).encode()),
                Response(json.dumps(files).encode())]) as opened:
            result = inspect_gguf_repo("Qwen/model-GGUF")
        self.assertEqual(result["claims"]["model_type"], "decoder-only")
        self.assertEqual(len(result["claims"]["author"]), 240)
        self.assertEqual(result["claims"]["language"], ["en", "zh"])
        self.assertEqual(result["claims"]["quantization"], "Q4_K_M")
        self.assertEqual(result["revision"], "d" * 40)
        self.assertNotIn("unsafe_unknown_field", result["claims"])
        self.assertEqual(len(result["files"]), 1)
        self.assertIn("/tree/" + "d" * 40, opened.call_args.args[0].full_url)

    def test_repo_inspection_rejects_private_or_gated_repo_before_file_lookup(self):
        from forgeos.huggingface_catalog import inspect_gguf_repo
        for flags in ({"private": True}, {"gated": True}):
            with patch("forgeos.huggingface_catalog.urlopen", return_value=
                       Response(json.dumps(flags).encode())) as opened:
                with self.assertRaisesRegex(RuntimeError, "Public"):
                    inspect_gguf_repo("Qwen/model-GGUF")
                self.assertEqual(opened.call_count, 1)
        with patch("forgeos.huggingface_catalog.urlopen", return_value=
                   Response(json.dumps({"sha": "main"}).encode())) as opened:
            with self.assertRaisesRegex(RuntimeError, "immutable revision"):
                inspect_gguf_repo("Qwen/model-GGUF")
            self.assertEqual(opened.call_count, 1)

    def test_invalid_file_response_and_path(self):
        for repo in ("../../passwd", "https://example.org/model", "name"):
            with self.assertRaises(ValueError):
                list_gguf_files(repo)
        with patch("forgeos.huggingface_catalog.urlopen",
                   return_value=Response(b'{"not":"array"}')):
            with self.assertRaisesRegex(RuntimeError, "Unexpected"):
                list_gguf_files("Qwen/model-GGUF")


if __name__ == "__main__":
    unittest.main()
