"""Resumable, verified Hugging Face GGUF downloads; no inference runtime here.

Network access is opt-in. All repository revisions are pinned to a full
40-digit commit, weight bytes are hashed, and incomplete downloads stay in
private .part files until verified and atomically published.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import Event
import shutil
import time
import unicodedata
import uuid
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .huggingface_catalog import (
    MAX_GGUF_BYTES, REPO_ID, list_gguf_files, model_revision,
)

GIB = 1024 ** 3
MAX_MODEL_BYTES = MAX_GGUF_BYTES
MAX_MODEL_STORAGE_QUOTA_BYTES = 16 * 1024 ** 4
MAX_DOWNLOAD_DURATION_SECONDS = 24 * 60 * 60
CHUNK_BYTES = 1024 * 1024
RESERVED_DISK_BYTES = GIB
SAFE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
ALLOWED_CDN = ("huggingface.co", "cdn-lfs.huggingface.co",
               "cas-bridge.xethub.hf.co", "cas-server.xethub.hf.co")
WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{x}" for x in range(1, 10)),
    *(f"LPT{x}" for x in range(1, 10)),
})
HUB_CLAIM_FIELDS = frozenset({
    "license", "author", "model_name", "model_type", "pipeline_tag",
    "library_name", "base_model", "language", "datasets", "tags",
    "model_size", "parameter_count", "quantization", "context_length",
    "chat_template", "tool_use", "structured_output",
})
DOWNLOAD_MANIFEST_FIELDS = frozenset({
    "repo", "revision", "file", "bytes", "sha256",
})
MAX_HUB_CLAIMS_BYTES = 2048


def _catalog_claim_snapshot(claims: dict, revision: str) -> dict:
    """Bound repository-card text and label it as an unverified claim."""
    if (not isinstance(claims, dict) or len(claims) > len(HUB_CLAIM_FIELDS)
            or not isinstance(revision, str)
            or not re.fullmatch(r"[a-f0-9]{40}", revision)):
        raise ValueError("Invalid pinned Hugging Face card claims")
    normalized = {}
    for key, value in claims.items():
        if key not in HUB_CLAIM_FIELDS:
            raise ValueError("Unsupported Hugging Face card claim")
        values = value if isinstance(value, list) else [value]
        if (not values or len(values) > 12
                or any(not isinstance(item, str) for item in values)):
            raise ValueError("Invalid Hugging Face card claim value")
        limit = 100 if isinstance(value, list) else 240
        cleaned = []
        for item in values:
            if len(item) > limit:
                raise ValueError("Hugging Face card claim is unsafe or oversized")
            if any(unicodedata.category(char).startswith("C") for char in item):
                raise ValueError("Hugging Face card claim is unsafe or oversized")
            item = " ".join(item.split())
            if (not item or len(item) > limit
                    or any(unicodedata.category(char).startswith("C")
                           for char in item)):
                raise ValueError("Hugging Face card claim is unsafe or oversized")
            cleaned.append(item)
        normalized[key] = cleaned if isinstance(value, list) else cleaned[0]
    snapshot = {"source": "huggingface_card_unverified",
                "revision": revision, "claims": normalized}
    if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > MAX_HUB_CLAIMS_BYTES:
        raise ValueError("Hugging Face card claims exceed the storage limit")
    return snapshot


def _validated_catalog_claim_snapshot(snapshot: object,
                                      revision: str) -> dict | None:
    if snapshot is None:
        return None
    if (not isinstance(snapshot, dict)
            or set(snapshot) != {"source", "revision", "claims"}
            or snapshot.get("source") != "huggingface_card_unverified"
            or snapshot.get("revision") != revision):
        raise ValueError("Stored Hugging Face card claims have an invalid identity")
    return _catalog_claim_snapshot(snapshot["claims"], revision)


def _download_manifest_matches(installed: object, expected: dict) -> bool:
    if not isinstance(installed, dict):
        return False
    fields = set(installed)
    if fields not in (DOWNLOAD_MANIFEST_FIELDS,
                      DOWNLOAD_MANIFEST_FIELDS | {"hub_claims"}):
        return False
    if any(installed.get(key) != value for key, value in expected.items()):
        return False
    try:
        _validated_catalog_claim_snapshot(
            installed.get("hub_claims"), expected["revision"])
    except ValueError:
        return False
    return True


def _safe_name(name: str) -> tuple[str, ...]:
    if (not isinstance(name, str) or len(name) > 320
            or not name.lower().endswith(".gguf")
            or "\\" in name or ":" in name):
        raise ValueError("Invalid GGUF file name")
    segments = name.split("/")
    if len(segments) > 8 or not all(SAFE_PART.fullmatch(x) and
                                    x not in (".", "..") and
                                    not x.endswith((".", " ")) and
                                    x.split(".", 1)[0].upper()
                                    not in WINDOWS_RESERVED_NAMES
                                    for x in segments):
        raise ValueError("Unsafe GGUF file path")
    return tuple(segments)


def _safe_storage_root(directory: Path | str | None) -> Path:
    root = Path(directory) if directory is not None else (
        Path.home() / ".forgeos" / "models")
    root = root.expanduser().absolute()
    if any(_is_reparse_point(parent) for parent in (root, *root.parents)):
        raise ValueError("Refusing linked or reparse-point model storage")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve(strict=True)


def _is_reparse_point(path: Path) -> bool:
    """Detect symlinks, Windows junctions and other filesystem reparse points."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except FileNotFoundError:
        return False
    except AttributeError:
        return False
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & flag)


def _lock_contention(exc: OSError) -> bool:
    if os.name == "nt":
        return (exc.errno in (errno.EACCES, errno.EDEADLK)
                or getattr(exc, "winerror", None) in (33, 36))
    return exc.errno in (errno.EACCES, errno.EAGAIN)


def _reject_existing_link(target: Path, root: Path) -> None:
    if not target.resolve().is_relative_to(root):
        raise ValueError("Model path escaped the storage root")
    for item in (target, *target.parents):
        if item == root:
            return
        if _is_reparse_point(item):
            raise ValueError("Model path contains a link or reparse point")


@contextmanager
def _exclusive_download_lock(lock_path: Path, *, blocking: bool = False,
                             deadline: float | None = None,
                             cancel_event: Event | None = None):
    """Kernel-held lock releases on process exit without permanent stale locks."""
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+b") as fd:
        if fd.seek(0, os.SEEK_END) == 0:
            fd.write(b"\x00")
            fd.flush()
        fd.seek(0)
        if cancel_event is not None and not isinstance(cancel_event, Event):
            raise TypeError("cancel_event must be a threading.Event")
        if os.name == "nt":
            import msvcrt
            mode = msvcrt.LK_NBLCK
            while True:
                try:
                    msvcrt.locking(fd.fileno(), mode, 1)
                    break
                except OSError as exc:
                    if not blocking:
                        raise RuntimeError(
                            "This GGUF download is active elsewhere") from exc
                    if not _lock_contention(exc):
                        raise RuntimeError(
                            "Could not acquire the model storage quota lock") from exc
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelled(
                            "GGUF download paused while waiting for storage quota") from exc
                    _check_deadline(deadline)
                    if deadline is None:
                        delay = 0.1
                    else:
                        delay = min(0.1, max(0.0, deadline - time.monotonic()))
                    if cancel_event is not None:
                        cancel_event.wait(delay)
                    else:
                        time.sleep(delay)
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
                if not blocking:
                    raise RuntimeError(
                        "This GGUF download is active elsewhere") from exc
                if not _lock_contention(exc):
                    raise RuntimeError(
                        "Could not acquire the model storage quota lock") from exc
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise DownloadCancelled(
                            "GGUF download paused while waiting for storage quota") from exc
                    _check_deadline(deadline)
                    if deadline is None:
                        delay = 0.1
                    else:
                        delay = min(0.1, max(0.0, deadline - time.monotonic()))
                    if cancel_event is not None:
                        cancel_event.wait(delay)
                    else:
                        time.sleep(delay)
                    try:
                        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as retry_exc:
                        if not _lock_contention(retry_exc):
                            raise RuntimeError(
                                "Could not acquire the model storage quota lock") from retry_exc
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
    # Only Hugging Face's documented weight-delivery hosts are trusted.
    # A blanket *.hf.co wildcard would also permit unrelated domains.
    return host in ALLOWED_CDN or host.endswith(".xethub.hf.co")


class _SafeModelRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_download_url(newurl):
            raise RuntimeError("Model download redirected to an untrusted host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_weight(request: Request, *, timeout: int):
    return build_opener(ProxyHandler({}), _SafeModelRedirects()).open(
        request, timeout=timeout)


class DownloadCancelled(RuntimeError):
    """User-requested pause; retain all existing verified and partial bytes."""


def _check_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled(
            "GGUF download paused; partial bytes retained for resumption")


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError(
            "GGUF download exceeded its total time limit; partial bytes "
            "were retained for resumption")


def _emit_progress(callback, stage: str, count: int, total: int) -> None:
    if callback is not None:
        callback(stage, count, total)


def _digest(path: Path, *, cancel_event: Event | None = None,
            progress_callback=None, deadline: float | None = None) -> str:
    """Stream SHA-256 in bounded chunks, with optional cooperative pause."""
    checksum = hashlib.sha256()
    verified = 0
    total = path.stat().st_size if progress_callback is not None else 0
    with path.open("rb") as source:
        while True:
            _check_cancelled(cancel_event)
            _check_deadline(deadline)
            block = source.read(CHUNK_BYTES)
            _check_cancelled(cancel_event)
            _check_deadline(deadline)
            if not block:
                break
            checksum.update(block)
            verified += len(block)
            _emit_progress(progress_callback, "verify", verified, total)
    return checksum.hexdigest()


def _gguf_header(path: Path) -> bool:
    """Reject mislabeled or invalid weight headers before publishing."""
    with path.open("rb") as source:
        return source.read(4) == b"GGUF"


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


def _download_gguf_unlocked(repo: str, filename: str, *, revision: str | None = None,
                            directory: Path | str | None = None,
                            max_bytes: int = MAX_MODEL_BYTES, timeout: int = 60,
                            cancel_event: Event | None = None,
                            progress_callback=None,
                            storage_quota_bytes: int | None = None,
                            deadline: float | None = None,
                            repair_corrupt: bool = False,
                            checkpoint_check=None,
                            repair_state: dict | None = None,
                            catalog_claims_snapshot: dict | None = None) -> dict:
    """Fetch one explicitly chosen GGUF with revision and SHA-256 verification.

    A failure never publishes an incomplete file. Interrupted partial bytes
    may be resumed only against exactly the same pinned revision/digest.
    A user-supplied Event pauses at I/O chunk boundaries (not mid-socket
    read); progress callbacks run on this worker thread, never Tk.
    """
    if not isinstance(repo, str) or not REPO_ID.fullmatch(repo):
        raise ValueError("Invalid Hugging Face repository identifier")
    path_parts = _safe_name(filename)
    if (type(max_bytes) is not int or not 1 <= max_bytes <= MAX_MODEL_BYTES
            or type(timeout) is not int or not 1 <= timeout <= 180
            or (storage_quota_bytes is not None
                and (type(storage_quota_bytes) is not int
                     or not 1 <= storage_quota_bytes <= MAX_MODEL_STORAGE_QUOTA_BYTES))):
        raise ValueError("Invalid model download size or timeout")
    if cancel_event is not None and not isinstance(cancel_event, Event):
        raise TypeError("cancel_event must be a threading.Event")
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError("progress_callback must be callable")
    _check_cancelled(cancel_event)
    _check_deadline(deadline)
    if revision is None:
        revision = model_revision(repo)
    elif not isinstance(revision, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", revision):
        raise ValueError("Model download requires a full pinned revision")
    revision = revision.lower()
    _check_cancelled(cancel_event)
    _check_deadline(deadline)
    matches = [record for record in list_gguf_files(repo, revision=revision)
               if record["path"] == filename]
    if len(matches) != 1:
        raise ValueError("Selected GGUF file not found in pinned repository")
    _check_cancelled(cancel_event)
    _check_deadline(deadline)
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
        _check_cancelled(cancel_event)
        _check_deadline(deadline)
        requested_claims = _validated_catalog_claim_snapshot(
            catalog_claims_snapshot, revision)
        manifest_payload = dict(expected)
        installed_manifest = None
        if manifest.exists():
            manifest_info = manifest.stat()
            if (not stat.S_ISREG(manifest_info.st_mode)
                    or manifest_info.st_nlink != 1
                    or manifest_info.st_size > 4096):
                raise RuntimeError("Existing GGUF manifest is not a valid file")
            try:
                installed_manifest = json.loads(
                    manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise RuntimeError("Corrupt existing GGUF manifest") from exc
            if not _download_manifest_matches(installed_manifest, expected):
                raise RuntimeError("Existing GGUF manifest identity mismatch")
            existing_claims = _validated_catalog_claim_snapshot(
                installed_manifest.get("hub_claims"), revision)
            if existing_claims is not None:
                manifest_payload["hub_claims"] = existing_claims
        if ("hub_claims" not in manifest_payload
                and requested_claims is not None):
            manifest_payload["hub_claims"] = requested_claims
        if (repair_corrupt
                and checkpoint_check(checksum) is not False):
            raise RuntimeError(
                "A blocked task checkpoint references this GGUF; repair refused")
        if destination.exists():
            before = destination.stat()
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size < 0 or before.st_size > MAX_MODEL_BYTES):
                raise RuntimeError(
                    "Existing GGUF is not an ordinary single-link model file")
            matches = before.st_size == size
            if matches:
                matches = (_digest(
                    destination, cancel_event=cancel_event,
                    progress_callback=progress_callback,
                    deadline=deadline) == checksum)
            after = destination.stat()
            if ((before.st_size, before.st_mtime_ns, before.st_ino,
                 before.st_nlink) !=
                    (after.st_size, after.st_mtime_ns, after.st_ino,
                     after.st_nlink) or after.st_nlink != 1):
                raise RuntimeError("Existing GGUF changed during verification")
            if matches:
                if not _gguf_header(destination):
                    raise RuntimeError("Existing GGUF is missing the GGUF header")
                # Repair a lost manifest after a crash only when the bytes
                # already match the requested immutable source identity.
                if metadata.exists():
                    if (not metadata.is_file() or metadata.stat().st_nlink != 1
                            or metadata.stat().st_size > 4096):
                        raise RuntimeError("Corrupt published GGUF download journal")
                    try:
                        prior = json.loads(metadata.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, ValueError) as exc:
                        raise RuntimeError("Corrupt published GGUF download journal") from exc
                    if prior != expected:
                        raise RuntimeError("Published GGUF journal identifies different weights")
                if partial.exists():
                    raise RuntimeError("Published GGUF has unresolved partial download")
                _check_cancelled(cancel_event)
                _check_deadline(deadline)
                if not manifest.exists():
                    _write_json_atomic(manifest, manifest_payload)
                elif installed_manifest is None:
                    raise RuntimeError(
                        "GGUF manifest appeared during cached verification")
                elif manifest_payload != installed_manifest:
                    _write_json_atomic(manifest, manifest_payload)
                if metadata.exists():
                    metadata.unlink()
                return {"status": "already_verified", "path": str(destination),
                        **expected}

            if not repair_corrupt:
                raise RuntimeError("Existing GGUF does not match the pinned checksum")
            if partial.exists() or metadata.exists():
                raise RuntimeError(
                    "Existing GGUF has an unresolved download journal; repair refused")
            if not manifest.exists():
                raise RuntimeError(
                    "Repair requires the original single-link pinned GGUF manifest")
            quarantined = destination.with_name(
                destination.name + ".corrupt-" + uuid.uuid4().hex)
            _reject_existing_link(quarantined, root)
            if os.path.lexists(quarantined):
                raise RuntimeError("Could not reserve a unique corrupt-model quarantine path")
            current = destination.stat()
            if ((after.st_size, after.st_mtime_ns, after.st_ino,
                 after.st_nlink) !=
                    (current.st_size, current.st_mtime_ns, current.st_ino,
                     current.st_nlink) or current.st_nlink != 1):
                raise RuntimeError("Existing GGUF changed before repair quarantine")
            os.replace(destination, quarantined)
            if repair_state is not None:
                repair_state["quarantined"] = str(quarantined)
        if partial.exists() != metadata.exists():
            raise RuntimeError("Incomplete download journal needs manual inspection")
        if partial.exists():
            if (_is_reparse_point(partial)
                    or _is_reparse_point(metadata)):
                raise ValueError(
                    "Model download journal must not be linked or redirected")
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
        _check_cancelled(cancel_event)
        _emit_progress(progress_callback, "download", position, size)
        remaining = size - position
        if remaining and storage_quota_bytes is not None:
            from .model_inventory import storage_usage
            used = storage_usage(root)["bytes"]
            if used + remaining > storage_quota_bytes:
                raise RuntimeError(
                    "ForgeOS model storage cap reached "
                    f"({used / GIB:.2f} GiB used; "
                    f"{storage_quota_bytes / GIB:.0f} GiB limit). "
                    "Free space or raise the cap, then retry this same model "
                    "and revision; any partial download is retained.")
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
            _check_deadline(deadline)
            with _open_weight(req, timeout=timeout) as response:
                _check_deadline(deadline)
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
                try:
                    # Unbuffered writes make completed chunks visible if the
                    # process is terminated between progress callbacks. The
                    # OS cache is sufficient for process-crash recovery; the
                    # finally block still fsyncs on ordinary exits.
                    with partial.open("ab", buffering=0) as output:
                        try:
                            while position < size:
                                _check_cancelled(cancel_event)
                                _check_deadline(deadline)
                                block = response.read(min(CHUNK_BYTES, size - position + 1))
                                _check_cancelled(cancel_event)
                                _check_deadline(deadline)
                                if not block:
                                    break
                                position += len(block)
                                if position > size:
                                    raise RuntimeError("Download exceeded declared model file size")
                                output.write(block)
                                _emit_progress(progress_callback, "download", position, size)
                            _check_cancelled(cancel_event)
                            _check_deadline(deadline)
                            if response.read(1):
                                raise RuntimeError("Unexpected additional model bytes")
                        finally:
                            # Durable partial on pause, network failure, or
                            # progress callback exception; never publish it.
                            output.flush()
                            os.fsync(output.fileno())
                except OSError as exc:
                    if (exc.errno == errno.ENOSPC
                            or getattr(exc, "winerror", None) == 112):
                        raise RuntimeError(
                            "Disk space ran out during the GGUF download. "
                            "Free space, then retry the same model and revision; "
                            "ForgeOS retained the partial download for resume.") from exc
                    raise
        _check_cancelled(cancel_event)
        _check_deadline(deadline)
        if position != size:
            raise RuntimeError("Model download interrupted; partial bytes retained for resumption")
        error = None
        if _digest(
                partial, cancel_event=cancel_event,
                progress_callback=progress_callback,
                deadline=deadline) != checksum:
            error = "Downloaded GGUF failed SHA-256; corrupt file quarantined"
        elif not _gguf_header(partial):
            error = "Downloaded model lacks GGUF header; file quarantined"
        if error is not None:
            quarantined = partial.with_name(partial.name + ".corrupt-" + uuid.uuid4().hex)
            os.replace(partial, quarantined)
            metadata.unlink(missing_ok=True)
            raise RuntimeError(error)
        _check_cancelled(cancel_event)
        _check_deadline(deadline)
        os.replace(partial, destination)
        _write_json_atomic(manifest, manifest_payload)
        metadata.unlink(missing_ok=True)
        repaired_path = (repair_state.get("quarantined")
                         if repair_state is not None else None)
        return {"status": ("repaired" if repair_state is not None
                            and repair_state.get("requested") else "verified"),
                "path": str(destination), **expected,
                **({"quarantined_path": repaired_path}
                   if repaired_path else {})}


def download_gguf(repo: str, filename: str, *, revision: str | None = None,
                  directory: Path | str | None = None,
                  max_bytes: int = MAX_MODEL_BYTES, timeout: int = 60,
                  cancel_event: Event | None = None,
                  progress_callback=None,
                  storage_quota_bytes: int | None = None,
                  max_duration_seconds: int = MAX_DOWNLOAD_DURATION_SECONDS,
                  repair_corrupt: bool = False,
                  checkpoint_check=None,
                  catalog_claims: dict | None = None,
                  catalog_claims_revision: str | None = None) -> dict:
    """Download one verified GGUF, optionally enforcing a whole-store quota.

    Quota-checked transfers take a blocking per-store lock, measure existing
    active/retired/partial/quarantined weight bytes, then hold the lock until
    completion or pause. This prevents simultaneous capped downloads from
    oversubscribing the same store allowance. A total-duration limit bounds
    slow-trickle transfers in addition to the per-socket timeout.
    """
    if cancel_event is not None and not isinstance(cancel_event, Event):
        raise TypeError("cancel_event must be a threading.Event")
    if progress_callback is not None and not callable(progress_callback):
        raise TypeError("progress_callback must be callable")
    if not isinstance(repair_corrupt, bool):
        raise TypeError("repair_corrupt must be boolean")
    if repair_corrupt and not callable(checkpoint_check):
        raise TypeError("repair requires a checkpoint_check callback")
    if catalog_claims is None:
        if catalog_claims_revision is not None:
            raise ValueError("A card-claim revision requires card claims")
        claims_snapshot = None
    else:
        if (not isinstance(catalog_claims_revision, str)
                or not isinstance(revision, str)
                or revision.lower() != catalog_claims_revision.lower()):
            raise ValueError(
                "Hugging Face card claims require the explicit matching pinned revision")
        claims_snapshot = _catalog_claim_snapshot(
            catalog_claims, catalog_claims_revision.lower())
    if (type(max_duration_seconds) is not int
            or not 1 <= max_duration_seconds <= MAX_DOWNLOAD_DURATION_SECONDS):
        raise ValueError("Invalid total model download time limit")
    deadline = time.monotonic() + max_duration_seconds
    if storage_quota_bytes is not None and (
            type(storage_quota_bytes) is not int
            or not 1 <= storage_quota_bytes <= MAX_MODEL_STORAGE_QUOTA_BYTES):
        raise ValueError("Invalid model storage quota")
    root = (_safe_storage_root(directory) if storage_quota_bytes is not None
            else directory)
    repair_state = {"requested": repair_corrupt}

    def transfer():
        return _download_gguf_unlocked(
            repo, filename, revision=revision, directory=root,
            max_bytes=max_bytes, timeout=timeout, cancel_event=cancel_event,
            progress_callback=progress_callback,
            storage_quota_bytes=storage_quota_bytes, deadline=deadline,
            repair_corrupt=repair_corrupt,
            checkpoint_check=checkpoint_check, repair_state=repair_state,
            catalog_claims_snapshot=claims_snapshot)

    try:
        if storage_quota_bytes is None:
            return transfer()
        quota_lock = root / ".forgeos-storage-quota.lock"
        _reject_existing_link(quota_lock, root)
        if quota_lock.exists() and (not quota_lock.is_file()
                                    or quota_lock.stat().st_nlink != 1):
            raise ValueError("Model storage quota lock is not a safe regular file")
        with _exclusive_download_lock(
                quota_lock, blocking=True, deadline=deadline,
                cancel_event=cancel_event):
            return transfer()
    except Exception as exc:
        if repair_state.get("quarantined"):
            raise RuntimeError(
                "Repair stopped; the original bytes are preserved in quarantine "
                f"as {Path(repair_state['quarantined']).name}. Retry the same "
                "pinned revision after resolving the download or storage issue.") from exc
        raise


def repair_downloaded_gguf(path: Path | str, *,
                           directory: Path | str | None = None,
                           confirm: bool = False, checkpoint_check,
                           cancel_event: Event | None = None,
                           progress_callback=None,
                           storage_quota_bytes: int | None = None,
                           max_duration_seconds: int = MAX_DOWNLOAD_DURATION_SECONDS) -> dict:
    """Restore one selected download from its pinned local manifest.

    A mismatched one-link file is preserved under a unique quarantine name.
    Imported files and untrusted/missing manifests are never repaired here.
    """
    if confirm is not True:
        raise ValueError("Explicit confirmation is required to repair a GGUF")
    if not callable(checkpoint_check):
        raise TypeError("checkpoint_check must be callable")
    root = _safe_storage_root(directory)
    selected = Path(path).expanduser().absolute()
    _reject_existing_link(selected, root)
    selected = selected.resolve(strict=False)
    manifest = selected.with_name(selected.name + ".forgeos.json")
    _reject_existing_link(manifest, root)
    from .model_inventory import _manifest_model_file
    expected, info = _manifest_model_file(manifest, root)
    if expected != selected:
        raise ValueError(
            "Select a downloaded GGUF with its original pinned manifest")
    return download_gguf(
        info["repo"], info["file"], revision=info["revision"],
        directory=root, cancel_event=cancel_event,
        progress_callback=progress_callback,
        storage_quota_bytes=storage_quota_bytes,
        max_duration_seconds=max_duration_seconds,
        repair_corrupt=True, checkpoint_check=checkpoint_check)
