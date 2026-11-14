"""Opt-in, pinned official Windows CPU llama.cpp runtime installer.

No always-on service, Docker, shell scripts or Python dependencies.
The official release ZIP remains local as an immutable verification
reference. SHA-256 is transit and artifact integrity, not a vendor
signature, and does not eliminate user-session local TOCTOU attacks.

This deliberately supports ONE reviewed CPU x64 release, instead of
silently trusting "latest" or executing whatever a public API returns.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import uuid
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zipfile

from .gguf_download import (
    _exclusive_download_lock, _is_reparse_point, _reject_existing_link,
    _safe_storage_root,
)

MIB = 1024 * 1024
ZIP_LIMIT = 64 * MIB
UNPACK_LIMIT = 512 * MIB
FILE_LIMIT = 200 * MIB
FILE_COUNT_LIMIT = 240
SPARE_DISK_BYTES = 512 * MIB
READ_SIZE = MIB
ARCHIVE_FILE = "__forgeos_official_release.zip"
MANIFEST_FILE = "runtime.forgeos.json"
MAX_METADATA_BYTES = 256 * 1024
LICENSE_FILE = "llama.cpp-LICENSE.txt"
LICENSE_SHA256 = "1cb44076504dba081126e8a0a87e742927c7b03ce7fa4fbfe83038a1322a6374"
LLVM_OPENMP_LICENSE_FILE = "LICENSE-LLVM-OpenMP"
LLVM_OPENMP_LICENSE_SHA256 = "fdad1758a9e1f9d5a81e18879b3406772115edc92c24bfa36b70c654f325e8e4"
SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+() -]{0,127}\Z")
RELEASE_TAG = "b12199"
RELEASE_ASSET = "llama-b12199-bin-win-cpu-x64.zip"
RELEASE_SHA256 = "1cde1df073c2526ef3ad34a5b2884998802b837ed4b965a36d0af2920f10a1ae"
RELEASE_BYTES = 24758741
PINNED_RELEASE = {
    "tag": RELEASE_TAG, "name": RELEASE_ASSET,
    "sha256": RELEASE_SHA256, "bytes": RELEASE_BYTES,
    "upstream": "https://github.com/ggml-org/llama.cpp",
}
ORIGIN = ("github.com", "api.github.com",
          "release-assets.githubusercontent.com",
          "objects.githubusercontent.com")


class _RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl):
            raise RuntimeError("Official llama.cpp runtime redirected to an untrusted host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _trusted_url(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 4096:
        return False
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https"
                and parts.hostname in ORIGIN
                and parts.port in (None, 443)
                and parts.username is None and parts.password is None
                and parts.fragment == "")
    except ValueError:
        return False


def _open(request: Request, timeout: int = 30):
    if not _trusted_url(request.full_url):
        raise ValueError("Runtime download source must be official GitHub HTTPS")
    return build_opener(ProxyHandler({}), _RestrictedRedirect()).open(
        request, timeout=timeout)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as inp:
        for chunk in iter(lambda: inp.read(READ_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _license_notice() -> bytes:
    """Load the exact upstream license bundled with ForgeOS source/build."""
    notice = Path(__file__).resolve().parent / "data" / LICENSE_FILE
    if notice.is_symlink() or not notice.is_file() or notice.stat().st_size > 8192:
        raise RuntimeError("Bundled llama.cpp license notice is missing or unsafe")
    content = notice.read_bytes()
    if hashlib.sha256(content).hexdigest() != LICENSE_SHA256:
        raise RuntimeError("Bundled llama.cpp license notice failed SHA-256")
    return content


def _runtime_root(directory: Path | str | None):
    return _safe_storage_root(
        directory if directory is not None else
        Path.home() / ".forgeos" / "runtimes")


def _require_windows():
    if sys.platform != "win32" or (8 * __import__("struct").calcsize("P")) != 64:
        raise OSError("The pinned llama.cpp CPU runtime is for Windows x64 only")


def _official_release_metadata():
    url = ("https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/"
           + RELEASE_TAG)
    req = Request(url, headers={"User-Agent": "ForgeOS-cpu-installer/0.1",
                                "Accept": "application/vnd.github+json"})
    with _open(req, timeout=30) as response:
        content = response.read(MAX_METADATA_BYTES + 1)
    if len(content) > MAX_METADATA_BYTES:
        raise RuntimeError("Official release metadata exceeds bounded limit")
    try:
        data = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("Official release metadata was not valid JSON") from exc
    if not isinstance(data, dict) or data.get("tag_name") != RELEASE_TAG:
        raise RuntimeError("Unexpected official llama.cpp release identity")
    assets = data.get("assets")
    if not isinstance(assets, list) or len(assets) > 120:
        raise RuntimeError("Unexpected official llama.cpp asset catalog")
    matches = [a for a in assets if isinstance(a, dict)
               and a.get("name") == RELEASE_ASSET]
    if len(matches) != 1:
        raise RuntimeError("Pinned Windows CPU asset is unavailable or duplicated")
    asset = matches[0]
    canonical = ("https://github.com/ggml-org/llama.cpp/releases/download/"
                 + RELEASE_TAG + "/" + RELEASE_ASSET)
    if (asset.get("browser_download_url") != canonical
            or asset.get("size") != RELEASE_BYTES
            or asset.get("digest") != "sha256:" + RELEASE_SHA256):
        raise RuntimeError("Upstream release asset differs from pinned SHA-256/size")
    return canonical


def _safe_entry(name: str) -> tuple[str, ...]:
    if (not isinstance(name, str) or len(name) > 400
            or "\\" in name or ":" in name or name.startswith("/")):
        raise RuntimeError("Unsafe official runtime ZIP member path")
    candidate = name.rstrip("/")
    parts = candidate.split("/")
    if (len(parts) > 4
            or any(not SAFE_SEGMENT.fullmatch(p) or p in (".", "..")
                   or p.endswith((" ", "."))
                   or p.split(".", 1)[0].upper() in
                   {"CON", "AUX", "NUL", "PRN",
                    *(f"COM{x}" for x in range(1, 10)),
                    *(f"LPT{x}" for x in range(1, 10))}
                   for p in parts)
            or name in (MANIFEST_FILE, ARCHIVE_FILE)):
        raise RuntimeError("Unsafe official runtime ZIP member name")
    return tuple(parts)


def _members(archive: zipfile.ZipFile):
    infos = archive.infolist()
    if not infos or len(infos) > FILE_COUNT_LIMIT:
        raise RuntimeError("Unexpected count of official runtime ZIP members")
    contents = []
    total = 0
    paths = set()
    for info in infos:
        parts = _safe_entry(info.filename)
        if info.flag_bits & 1:
            raise RuntimeError("Encrypted ZIP entries are unsupported")
        if stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
            raise RuntimeError("Official runtime ZIP contains a symlink")
        if info.is_dir():
            continue
        key = "/".join(parts).casefold()
        if key in paths:
            raise RuntimeError("Runtime ZIP has duplicate or case-colliding paths")
        paths.add(key)
        if (info.file_size > FILE_LIMIT
                or info.file_size < 0 or info.compress_size < 0):
            raise RuntimeError("Runtime ZIP file exceeds size limit")
        total += info.file_size
        if total > UNPACK_LIMIT:
            raise RuntimeError("Runtime ZIP exceeds decompression limit")
        contents.append((info, parts))
    binaries = [parts for _, parts in contents
                if parts[-1].casefold() == "llama-server.exe"]
    if len(binaries) != 1:
        raise RuntimeError("Pinned runtime ZIP must contain exactly one llama-server.exe")
    licenses = [parts for _, parts in contents
                if "/".join(parts).casefold() == LLVM_OPENMP_LICENSE_FILE.casefold()]
    if licenses != [(LLVM_OPENMP_LICENSE_FILE,)]:
        raise RuntimeError("Pinned runtime ZIP is missing its LLVM OpenMP license notice")
    return contents, binaries[0]


def _extract(archive_path: Path, stage: Path):
    entries = {}
    with zipfile.ZipFile(archive_path, "r") as archive:
        contents, binary_parts = _members(archive)
        for info, parts in contents:
            path = stage.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            count = 0
            with archive.open(info, "r") as inp, path.open("xb") as out:
                while True:
                    chunk = inp.read(READ_SIZE)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > info.file_size:
                        raise RuntimeError("ZIP entry expanded beyond declared size")
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if count != info.file_size:
                raise RuntimeError("Truncated official runtime ZIP member")
            entries["/".join(parts)] = digest.hexdigest()
    return entries, binary_parts


def _verify_files(target: Path):
    archive_path = target / ARCHIVE_FILE
    manifest = target / MANIFEST_FILE
    if _is_reparse_point(archive_path) or _is_reparse_point(manifest):
        raise RuntimeError("Installed runtime archive or manifest is redirected")
    if (not archive_path.is_file()
            or archive_path.stat().st_size != RELEASE_BYTES
            or _sha256(archive_path) != RELEASE_SHA256):
        raise RuntimeError("Installed runtime source archive failed pinned SHA-256")
    license_path = target / LICENSE_FILE
    if (_is_reparse_point(license_path) or not license_path.is_file()
            or license_path.stat().st_size > 8192
            or _sha256(license_path) != LICENSE_SHA256
            or license_path.read_bytes() != _license_notice()):
        raise RuntimeError("Installed llama.cpp license notice is missing or modified")
    with zipfile.ZipFile(archive_path, "r") as archive:
        members, binary_parts = _members(archive)
        expected_names = {"/".join(parts).casefold() for _, parts in members}
        seen = set()
        for folder, dirs, filenames in os.walk(target, followlinks=False):
            relative = Path(folder).relative_to(target)
            for directory in dirs:
                d = Path(folder) / directory
                if _is_reparse_point(d):
                    raise RuntimeError(
                        "Installed runtime folder contains a reparse point")
            for name in filenames:
                current = Path(folder) / name
                if _is_reparse_point(current):
                    raise RuntimeError(
                        "Installed runtime contains a linked or redirected file")
                relative_path = current.relative_to(target).as_posix()
                if relative_path in (ARCHIVE_FILE, MANIFEST_FILE, LICENSE_FILE):
                    continue
                key = relative_path.casefold()
                if key not in expected_names or key in seen:
                    raise RuntimeError("Unexpected file inside official runtime installation")
                seen.add(key)
        if seen != expected_names:
            raise RuntimeError("Installed runtime is missing release files")
        checksums = {}
        for info, parts in members:
            path = target.joinpath(*parts)
            digest = hashlib.sha256()
            amount = 0
            with path.open("rb") as source:
                for block in iter(lambda: source.read(READ_SIZE), b""):
                    amount += len(block)
                    if amount > info.file_size:
                        raise RuntimeError("Modified runtime executable or DLL")
                    digest.update(block)
            if amount != info.file_size:
                raise RuntimeError("Modified runtime file size")
            checksums["/".join(parts)] = digest.hexdigest()
        if checksums.get(LLVM_OPENMP_LICENSE_FILE) != LLVM_OPENMP_LICENSE_SHA256:
            raise RuntimeError("Pinned LLVM OpenMP license notice failed SHA-256")
        if not manifest.is_file() or manifest.stat().st_size > 256 * 1024:
            raise RuntimeError("Missing official runtime inventory manifest")
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError("Corrupt official runtime inventory manifest") from exc
        if (not isinstance(data, dict)
                or data.get("release") != RELEASE_TAG
                or data.get("asset_sha256") != RELEASE_SHA256
                or data.get("license_sha256") != LICENSE_SHA256
                or data.get("files") != checksums):
            raise RuntimeError("Installed runtime manifest identity mismatch")
        # The manifest checksums alone aren't sufficient to authenticate
        # code, so re-derive every file checksum from the original ZIP that
        # was itself verified against the immutable pinned SHA-256.
        with zipfile.ZipFile(archive_path, "r") as source_zip:
            for info, parts in members:
                h = hashlib.sha256()
                with source_zip.open(info, "r") as source:
                    for block in iter(lambda: source.read(READ_SIZE), b""):
                        h.update(block)
                if checksums["/".join(parts)] != h.hexdigest():
                    raise RuntimeError("Installed runtime DLL/binary differs from pinned archive")
    return target.joinpath(*binary_parts), checksums["/".join(binary_parts)]


def verify_installed_cpu_runtime(directory: Path | str | None = None):
    """Recheck the complete official runtime and DLLs, not just the EXE."""
    _require_windows()
    root = _runtime_root(directory)
    target = root / "llama.cpp" / (RELEASE_TAG + "-" + RELEASE_SHA256[:12])
    _reject_existing_link(target, root)
    if not target.is_dir():
        raise FileNotFoundError("Official CPU llama.cpp runtime is not installed")
    binary, digest = _verify_files(target)
    return {"path": str(binary), "sha256": digest,
            "tag": RELEASE_TAG, "asset_sha256": RELEASE_SHA256,
            "status": "verified_official_cpu"}


def install_pinned_cpu_runtime(directory: Path | str | None = None):
    """Only explicit caller invocation performs opt-in public GitHub IO."""
    _require_windows()
    root = _runtime_root(directory)
    folder = root / "llama.cpp"
    _reject_existing_link(folder, root)
    folder.mkdir(exist_ok=True)
    target = folder / (RELEASE_TAG + "-" + RELEASE_SHA256[:12])
    lock = folder / (RELEASE_TAG + ".lock")
    _reject_existing_link(target, root)
    _reject_existing_link(lock, root)
    with _exclusive_download_lock(lock):
        if target.exists():
            return verify_installed_cpu_runtime(directory)
        if shutil.disk_usage(root).free < UNPACK_LIMIT + SPARE_DISK_BYTES:
            raise RuntimeError("At least 1 GiB free disk required for CPU runtime install")
        url = _official_release_metadata()
        stage = folder / (".install-" + uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        archive_path = stage / ARCHIVE_FILE
        try:
            digest = hashlib.sha256()
            count = 0
            request = Request(
                url, headers={"User-Agent": "ForgeOS-cpu-installer/0.1"})
            with _open(request, timeout=60) as response, \
                    archive_path.open("xb") as out:
                if response.getcode() != 200:
                    raise RuntimeError("Unexpected official CPU runtime download status")
                while True:
                    block = response.read(READ_SIZE)
                    if not block:
                        break
                    count += len(block)
                    if count > ZIP_LIMIT or count > RELEASE_BYTES:
                        raise RuntimeError("CPU runtime archive exceeds pinned size")
                    digest.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if count != RELEASE_BYTES or digest.hexdigest() != RELEASE_SHA256:
                raise RuntimeError("Official CPU runtime SHA-256 verification failed")
            entries, binary_parts = _extract(archive_path, stage)
            (stage / LICENSE_FILE).write_bytes(_license_notice())
            manifest_path = stage / MANIFEST_FILE
            with manifest_path.open("x", encoding="utf-8") as output:
                json.dump({"release": RELEASE_TAG,
                           "asset_sha256": RELEASE_SHA256,
                           "license_sha256": LICENSE_SHA256,
                           "binary": "/".join(binary_parts),
                           "files": entries}, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            # Verify the staged content against the pinned upstream archive
            # before atomically publishing. No scripts/installers are run.
            _verify_files(stage)
            stage.rename(target)
            return verify_installed_cpu_runtime(directory)
        finally:
            # Delete only our uncommitted uniquely named staging folder.
            if stage.exists():
                shutil.rmtree(stage)
