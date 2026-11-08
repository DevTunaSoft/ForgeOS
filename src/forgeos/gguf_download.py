"""Resumable, verified Hugging Face GGUF downloads; no inference runtime here.

Network access is opt-in. All repository revisions are pinned to a full
40-digit commit, weight bytes are hashed, and incomplete downloads stay in
private .part files until verified and atomically published.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .huggingface_catalog import REPO_ID, list_gguf_files, model_revision

GIB = 1024 ** 3
MAX_MODEL_BYTES = 16 * GIB
CHUNK_BYTES = 1024 * 1024
RESERVED_DISK_BYTES = GIB
SAFE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
ALLOWED_CDN = ("huggingface.co", "cdn-lfs.huggingface.co",
               "cas-bridge.xethub.hf.co", "cas-server.xethub.hf.co")


def _safe_name(name: str) -> tuple[str, ...]:
    if (not isinstance(name, str) or len(name) > 320
            or not name.lower().endswith(".gguf")
            or "\\" in name or ":" in name):
        raise ValueError("Invalid GGUF file name")
    segments = name.split("/")
    if len(segments) > 8 or not all(SAFE_PART.fullmatch(x) and
                                    x not in (".", "..") and
                                    not x.rstrip(". ").upper() in
                                    {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}
                                    for x in segments):
        raise ValueError("Unsafe GGUF file path")
    return tuple(segments)


def _safe_storage_root(directory: Path | str | None) -> Path:
    root = Path(directory) if directory is not None else (
        Path.home() / ".forgeos" / "models")
    root = root.expanduser().absolute()
    if any(parent.is_symlink() for parent in (root, *root.parents)):
        raise ValueError("Refusing symlinked model storage")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve(strict=True)


def _reject_existing_link(target: Path, root: Path) -> None:
    if not target.resolve().is_relative_to(root):
        raise ValueError("Model path escaped the storage root")
    for item in (target, *target.parents):
        if item == root:
            return
        if item.is_symlink():
            raise ValueError("Model path contains a symbolic link")


@contextmanager
def _exclusive_download_lock(lock_path: Path):
    """Kernel-held lock releases on process exit without permanent stale locks."""
    with lock_path.open("a+b") as fd:
        fd.seek(0)
        fd.write(b"\x00")
        fd.flush()
        fd.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("This GGUF download is active elsewhere") from exc
            try:
                yield
            finally:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("This GGUF download is active elsewhere") from exc
            try:
                yield
            finally:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)


def _allowed_download_url(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 4096:
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError:
        return False
    if (parsed.scheme != "https" or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)
            or not host):
        return False
    return (host in ALLOWED_CDN or host.endswith(".xethub.hf.co")
            or host.endswith(".hf.co"))


class _SafeModelRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_download_url(newurl):
            raise RuntimeError("Model download redirected to an untrusted host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_weight(request: Request, *, timeout: int):
    return build_opener(ProxyHandler({}), _SafeModelRedirects()).open(
        request, timeout=timeout)


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK_BYTES), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _write_json_atomic(destination: Path, value: dict):
    temporary = destination.with_name(destination.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def download_gguf(repo: str, filename: str, *, directory: Path | str | None = None,
                  max_bytes: int = MAX_MODEL_BYTES, timeout: int = 60) -> dict:
    """Fetch one explicitly chosen GGUF with revision and SHA-256 verification.

    A failure never publishes an incomplete file. Interrupted partial bytes
    may be resumed only against exactly the same pinned revision/digest.
    """
    if not isinstance(repo, str) or not REPO_ID.fullmatch(repo):
        raise ValueError("Invalid Hugging Face repository identifier")
    path_parts = _safe_name(filename)
    if (type(max_bytes) is not int or not 1 <= max_bytes <= MAX_MODEL_BYTES
            or type(timeout) is not int or not 1 <= timeout <= 180):
        raise ValueError("Invalid model download size or timeout")
    revision = model_revision(repo)
    matches = [record for record in list_gguf_files(repo, revision=revision)
               if record["path"] == filename]
    if len(matches) != 1:
        raise ValueError("Selected GGUF file not found in pinned repository")
    info = matches[0]
    size, checksum = info["bytes"], info["sha256"]
    if (size > max_bytes or not isinstance(checksum, str)
            or not re.fullmatch(r"[a-f0-9]{64}", checksum)):
        raise ValueError("GGUF exceeds the limit or lacks a trusted LFS SHA-256")
    root = _safe_storage_root(directory)
    destination = root.joinpath(*repo.split("/"), revision, *path_parts)
    _reject_existing_link(destination, root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_existing_link(destination, root)
    partial = destination.with_name(destination.name + ".part")
    metadata = destination.with_name(destination.name + ".part.json")
    lock = destination.with_name(destination.name + ".lock")
    manifest = destination.with_name(destination.name + ".forgeos.json")
    for path in (partial, metadata, lock, manifest):
        _reject_existing_link(path, root)
    expected = {"repo": repo, "revision": revision, "file": filename,
                "bytes": size, "sha256": checksum}
    with _exclusive_download_lock(lock):
        if destination.exists():
            if destination.is_file() and destination.stat().st_size == size and (
                    _digest(destination) == checksum):
                return {"status": "already_verified", "path": str(destination),
                        **expected}
            raise RuntimeError("Existing GGUF does not match the pinned checksum")
        if partial.exists() != metadata.exists():
            raise RuntimeError("Incomplete download journal needs manual inspection")
        if partial.exists():
            if partial.is_symlink() or metadata.is_symlink():
                raise ValueError("Model download journal must not be a symlink")
            if metadata.stat().st_size > 4096:
                raise RuntimeError("Model download journal is oversized")
            try:
                recovered = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError) as exc:
                raise RuntimeError("Corrupt model download journal") from exc
            if recovered != expected:
                raise RuntimeError("Model download journal identifies different weights")
            position = partial.stat().st_size
            if position > size:
                raise RuntimeError("Partial model file exceeds declared GGUF size")
        else:
            _write_json_atomic(metadata, expected)
            with partial.open("xb"):
                pass
            position = 0
        remaining = size - position
        if remaining and shutil.disk_usage(root).free < remaining + RESERVED_DISK_BYTES:
            raise RuntimeError("Insufficient disk space for model and safety reserve")
        if remaining:
            url = ("https://huggingface.co/" + "/".join(
                quote(part, safe="") for part in repo.split("/")) +
                "/resolve/" + revision + "/" +
                "/".join(quote(part, safe="") for part in path_parts) +
                "?download=true")
            if not _allowed_download_url(url):
                raise ValueError("Invalid model download destination")
            headers = {"User-Agent": "ForgeOS-GGUF-download/0.1"}
            if position:
                headers["Range"] = f"bytes={position}-"
            req = Request(url, headers=headers)
            with _open_weight(req, timeout=timeout) as response:
                code = response.getcode()
                if position:
                    expected_range = f"bytes {position}-{size - 1}/{size}"
                    if (code != 206 or response.headers.get("Content-Range") != expected_range):
                        raise RuntimeError("Server did not honor exact model resume range")
                elif code != 200:
                    raise RuntimeError("Unexpected initial model download response")
                content_length = response.headers.get("Content-Length")
                if (content_length is not None and
                        (not content_length.isdecimal()
                         or int(content_length) != remaining)):
                    raise RuntimeError("Model download content length mismatch")
                with partial.open("ab") as output:
                    while position < size:
                        block = response.read(min(CHUNK_BYTES, size - position + 1))
                        if not block:
                            break
                        position += len(block)
                        if position > size:
                            raise RuntimeError("Download exceeded declared model file size")
                        output.write(block)
                    if response.read(1):
                        raise RuntimeError("Unexpected additional model bytes")
                    output.flush()
                    os.fsync(output.fileno())
        if position != size:
            raise RuntimeError("Model download interrupted; partial bytes retained for resumption")
        if _digest(partial) != checksum:
            quarantined = partial.with_name(partial.name + ".corrupt-" + uuid.uuid4().hex)
            os.replace(partial, quarantined)
            metadata.unlink(missing_ok=True)
            raise RuntimeError("Downloaded GGUF failed SHA-256; corrupt file quarantined")
        os.replace(partial, destination)
        _write_json_atomic(manifest, expected)
        metadata.unlink(missing_ok=True)
        return {"status": "verified", "path": str(destination), **expected}
