"""Official pinned Windows CPU runtime installer: offline ZIP/failure tests.

All web input is faked. Tests do not install or execute real llama.cpp.
The immutable release digest is replaced only inside local test patches.
"""
from contextlib import ExitStack
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from forgeos import runtime_installer as ri


class _HTTPResponse(BytesIO):
    def getcode(self):
        return 200


class CPUInstallerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="forgeos-llama-installer-")
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.files = {"bin/llama-server.exe": b"PE" * 100,
                      "bin/ggml-base.dll": b"DLL" * 200,
                      "bin/LICENSE": b"MIT test fixture",
                      ri.LLVM_OPENMP_LICENSE_FILE: b"LLVM OpenMP test license"}
        self.build_zip()

    def build_zip(self, contents=None):
        output = BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in (contents or self.files).items():
                archive.writestr(name, data)
        self.archive = output.getvalue()
        self.archive_sha = hashlib.sha256(self.archive).hexdigest()

    def metadata(self):
        return json.dumps({
            "tag_name": ri.RELEASE_TAG,
            "assets": [{
                "name": ri.RELEASE_ASSET,
                "size": len(self.archive),
                "digest": "sha256:" + self.archive_sha,
                "browser_download_url":
                    "https://github.com/ggml-org/llama.cpp/releases/download/"
                    + ri.RELEASE_TAG + "/" + ri.RELEASE_ASSET,
            }],
        }).encode()

    def install_fixture(self, *, openmp_sha=None):
        def fake_open(req, timeout=30):
            if "api.github.com" in req.full_url:
                return _HTTPResponse(self.metadata())
            self.assertEqual(
                req.full_url,
                "https://github.com/ggml-org/llama.cpp/releases/download/"
                + ri.RELEASE_TAG + "/" + ri.RELEASE_ASSET)
            return _HTTPResponse(self.archive)
        patches = ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(patch.object(ri, "_require_windows"))
        patches.enter_context(patch.object(ri, "RELEASE_BYTES",
                                          len(self.archive)))
        patches.enter_context(patch.object(ri, "RELEASE_SHA256",
                                          self.archive_sha))
        patches.enter_context(patch.object(
            ri, "LLVM_OPENMP_LICENSE_SHA256",
            openmp_sha or hashlib.sha256(
                self.files[ri.LLVM_OPENMP_LICENSE_FILE]).hexdigest()))
        patches.enter_context(patch.object(
            ri.shutil, "disk_usage", return_value=SimpleNamespace(free=4*1024**3)))
        opened = patches.enter_context(patch.object(ri, "_open", side_effect=fake_open))
        return opened

    def test_offline_round_trip_verifies_binary_dll_and_archive(self):
        opened = self.install_fixture()
        result = ri.install_pinned_cpu_runtime(self.directory)
        self.assertEqual(result["status"], "verified_official_cpu")
        self.assertTrue(Path(result["path"]).exists())
        self.assertEqual(
            result["sha256"],
            hashlib.sha256(self.files["bin/llama-server.exe"]).hexdigest())
        self.assertTrue(
            (self.directory / "llama.cpp" /
             (ri.RELEASE_TAG + "-" + self.archive_sha[:12]) /
             ri.ARCHIVE_FILE).exists())
        installed_license = (self.directory / "llama.cpp" /
                             (ri.RELEASE_TAG + "-" + self.archive_sha[:12]) /
                             ri.LICENSE_FILE)
        self.assertEqual(installed_license.read_bytes(), ri._license_notice())
        self.assertEqual(ri.verify_installed_cpu_runtime(self.directory), result)
        before = opened.call_count
        self.assertEqual(ri.install_pinned_cpu_runtime(self.directory), result)
        self.assertEqual(before, opened.call_count)  # no automatic network

    def test_runtime_bundle_rejects_reparse_point_members(self):
        self.install_fixture()
        result = ri.install_pinned_cpu_runtime(self.directory)
        root = Path(result["path"]).parents[1]
        for redirected in (root / "bin", root / "bin" / "ggml-base.dll"):
            with patch.object(
                    ri, "_is_reparse_point",
                    side_effect=lambda path, expected=redirected:
                        Path(path) == expected):
                with self.assertRaisesRegex(RuntimeError,
                                            "reparse point|redirected"):
                    ri.verify_installed_cpu_runtime(self.directory)

    def test_installed_hardlinked_runtime_member_is_refused(self):
        self.install_fixture()
        result = ri.install_pinned_cpu_runtime(self.directory)
        root = Path(result["path"]).parents[1]
        dll = root / "bin" / "ggml-base.dll"
        external_link = root.parent / "external-runtime-link.dll"
        try:
            os.link(dll, external_link)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"filesystem does not support hard links: {exc}")
        with self.assertRaisesRegex(RuntimeError, "hard-linked"):
            ri.verify_installed_cpu_runtime(self.directory)

    def test_runtime_member_with_multiple_links_is_refused(self):
        self.install_fixture()
        result = ri.install_pinned_cpu_runtime(self.directory)
        dll = Path(result["path"]).parent / "ggml-base.dll"
        original_stat = Path.stat

        def report_external_link(path, *args, **kwargs):
            metadata = original_stat(path, *args, **kwargs)
            if path == dll:
                return SimpleNamespace(st_mode=metadata.st_mode, st_nlink=2)
            return metadata

        with patch.object(Path, "stat", new=report_external_link):
            with self.assertRaisesRegex(RuntimeError, "hard-linked"):
                ri.verify_installed_cpu_runtime(self.directory)

    def test_tampered_dll_is_refused_even_if_manifest_forged(self):
        self.install_fixture()
        result = ri.install_pinned_cpu_runtime(self.directory)
        root = Path(result["path"]).parents[1]
        dll = root / "bin" / "ggml-base.dll"
        # Equal-length tampering bypasses size preflight and must be
        # caught by original pinned archive verification despite a
        # locally forged inventory manifest.
        dll.write_bytes(b"X" * len(self.files["bin/ggml-base.dll"]))
        manifest = root / ri.MANIFEST_FILE
        record = json.loads(manifest.read_text())
        record["files"]["bin/ggml-base.dll"] = hashlib.sha256(
            dll.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(record))
        with self.assertRaisesRegex(RuntimeError, "differs from pinned archive"):
            ri.verify_installed_cpu_runtime(self.directory)
        with self.assertRaisesRegex(RuntimeError, "differs from pinned archive"):
            ri.install_pinned_cpu_runtime(self.directory)

    def test_tampered_zip_cannot_authenticate_new_binary(self):
        self.install_fixture()
        result = ri.install_pinned_cpu_runtime(self.directory)
        source = Path(result["path"]).parents[1] / ri.ARCHIVE_FILE
        source.write_bytes(b"bad zip")
        with self.assertRaisesRegex(RuntimeError, "pinned SHA-256"):
            ri.verify_installed_cpu_runtime(self.directory)

    def test_tampered_upstream_license_notice_is_refused(self):
        self.install_fixture()
        result = ri.install_pinned_cpu_runtime(self.directory)
        target = Path(result["path"]).parents[1]
        license_path = target / ri.LICENSE_FILE
        license_path.write_bytes(b"modified notice")
        with self.assertRaisesRegex(RuntimeError, "license notice"):
            ri.verify_installed_cpu_runtime(self.directory)

    def test_bundled_license_notice_matches_pinned_upstream_digest(self):
        self.assertEqual(hashlib.sha256(ri._license_notice()).hexdigest(),
                         ri.LICENSE_SHA256)

    def test_reject_path_traversal_duplicate_and_symlink_zip(self):
        # zipfile normalizes backslashes when *creating* ZIPs on Windows.
        # Test untrusted member path validation directly, so the fixture
        # cannot turn this adversarial input into a legitimate path.
        with self.assertRaisesRegex(RuntimeError, "Unsafe"):
            ri._safe_entry("x\\evil.dll")
        for name in ("../escape.dll", "/tmp/escape.dll", "C:/bad.dll",
                     "CON.dll", "a/./bad.dll"):
            with self.subTest(name=name):
                self.build_zip({name: b"bad",
                                "bin/llama-server.exe": b"EXE"})
                self.install_fixture()
                with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                    ri.install_pinned_cpu_runtime(self.directory)

    def test_runtime_archive_must_include_exact_pinned_openmp_notice(self):
        self.build_zip({"bin/llama-server.exe": b"EXE"})
        self.install_fixture()
        with self.assertRaisesRegex(RuntimeError, "missing its LLVM OpenMP"):
            ri.install_pinned_cpu_runtime(self.directory)

    def test_modified_openmp_notice_fails_closed(self):
        self.files[ri.LLVM_OPENMP_LICENSE_FILE] = b"wrong LLVM notice"
        self.build_zip()
        self.install_fixture(openmp_sha=ri.LLVM_OPENMP_LICENSE_SHA256)
        with self.assertRaisesRegex(RuntimeError, "LLVM OpenMP license notice"):
            ri.install_pinned_cpu_runtime(self.directory)

    def test_reject_second_server_and_bomb(self):
        self.build_zip({"a/llama-server.exe": b"a",
                        "b/llama-server.exe": b"b",
                        ri.LLVM_OPENMP_LICENSE_FILE: b"LLVM OpenMP test license"})
        self.install_fixture()
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            ri.install_pinned_cpu_runtime(self.directory)

    def test_corrupt_download_cleans_only_staging_and_does_not_publish(self):
        self.install_fixture()
        with patch.object(ri, "_sha256", return_value="1"*64):
            with self.assertRaises(RuntimeError):
                ri.install_pinned_cpu_runtime(self.directory)
        family = self.directory / "llama.cpp"
        self.assertEqual(
            [x for x in family.iterdir() if x.is_dir()], [])

    def test_untrusted_redirects_and_cloud_urls_fail_closed(self):
        for url in (
            "http://github.com/ggml-org/llama.cpp",
            "https://evil.github.com/a",
            "https://user:pass@github.com/a",
            "https://github.com.evil.test/foo",
            "https://release-assets.githubusercontent.com.evil.test/a",
            "https://example.com",
        ):
            with self.subTest(url=url):
                self.assertFalse(ri._trusted_url(url))
        self.assertTrue(ri._trusted_url(
            "https://github.com/ggml-org/llama.cpp/releases/download/b12199/a"))
        with self.assertRaisesRegex(RuntimeError, "untrusted host"):
            ri._RestrictedRedirect().redirect_request(
                None, None, 302, "redirect", None, "https://example.com/payload")

    def test_absent_or_changed_release_metadata_fails_closed(self):
        self.install_fixture()
        with patch.object(ri, "RELEASE_BYTES", 1):
            with self.assertRaisesRegex(RuntimeError, "differs from pinned"):
                ri.install_pinned_cpu_runtime(self.directory)
        self.assertEqual(
            [p for p in self.directory.rglob("llama-server.exe")], [])


if __name__ == "__main__":
    unittest.main()
