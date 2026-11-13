"""CLI repository revision forwarding tests."""
import contextlib
import io
import unittest
from unittest.mock import patch

from forgeos.cli import main


class ModelDownloadCliTests(unittest.TestCase):
    def test_explicit_revision_is_forwarded(self):
        revision = "a" * 40
        output = io.StringIO()
        with patch("forgeos.gguf_download.download_gguf", return_value={
                "status": "verified", "revision": revision}) as download, \
             contextlib.redirect_stdout(output):
            self.assertEqual(main([
                "model-download", "Qwen/model-GGUF", "model.Q4_K_M.gguf",
                "--revision", revision,
            ]), 0)
        self.assertEqual(download.call_args.kwargs["revision"], revision)
        self.assertIn(revision, output.getvalue())

    def test_revision_remains_optional_for_existing_cli_usage(self):
        with patch("forgeos.gguf_download.download_gguf", return_value={
                "status": "verified"}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([
                "model-download", "Qwen/model-GGUF", "model.Q4_K_M.gguf",
            ]), 0)

    def test_catalog_inspect_exposes_pinned_revision_and_claims(self):
        data = {"repo": "Qwen/model-GGUF", "revision": "b" * 40,
                "claims": {"license": "apache-2.0"}, "files": []}
        output = io.StringIO()
        with patch("forgeos.huggingface_catalog.inspect_gguf_repo",
                   return_value=data) as inspect, \
             contextlib.redirect_stdout(output):
            self.assertEqual(main([
                "model-catalog", "inspect", "Qwen/model-GGUF",
            ]), 0)
        inspect.assert_called_once_with("Qwen/model-GGUF")
        self.assertIn(data["revision"], output.getvalue())


if __name__ == "__main__":
    unittest.main()
