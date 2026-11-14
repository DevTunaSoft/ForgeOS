"""Bounded local records of successful model/runtime structured-output tests.

These records describe a passing ForgeOS self-test for an exact pair of
SHA-256 identities. They are local mutable metadata, not publisher signatures
or proof that the current on-disk model has been reverified.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .gguf_download import _exclusive_download_lock, _is_reparse_point, _safe_storage_root

INDEX_NAME = ".forgeos-runtime-compatibility.json"
LOCK_NAME = ".forgeos-runtime-compatibility.lock"
MAX_INDEX_BYTES = 128 * 1024
MAX_PAIRS = 256
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
TESTED_AT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")


def _read_index(root: Path) -> list[dict]:
    path = root / INDEX_NAME
    if _is_reparse_point(path):
        raise ValueError("Runtime compatibility index is a reparse point")
    if not os.path.lexists(path):
        return []
    before = path.stat(follow_symlinks=False)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size > MAX_INDEX_BYTES):
        raise ValueError("Runtime compatibility index is unsafe or oversized")
    with path.open("rb") as stream:
        opened_before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened_before.st_mode)
                or opened_before.st_nlink != 1
                or opened_before.st_size > MAX_INDEX_BYTES):
            raise ValueError("Runtime compatibility index changed while opening")
        raw = stream.read(MAX_INDEX_BYTES + 1)
        opened_after = os.fstat(stream.fileno())
    after = path.stat(follow_symlinks=False)
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                             info.st_mtime_ns, info.st_nlink)
    if (len(raw) > MAX_INDEX_BYTES
            or identity(opened_before) != identity(opened_after)
            or identity(opened_after) != identity(after)):
        raise ValueError("Runtime compatibility index changed while reading")

    def reject_duplicate_keys(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Runtime compatibility index has duplicate keys")
            result[key] = value
        return result

    try:
        data = json.loads(raw.decode("utf-8"),
                          object_pairs_hook=reject_duplicate_keys)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Runtime compatibility index is unreadable") from exc
    if (not isinstance(data, dict) or set(data) != {"version", "pairs"}
            or type(data["version"]) is not int or data["version"] != 1
            or not isinstance(data["pairs"], list)
            or len(data["pairs"]) > MAX_PAIRS):
        raise ValueError("Runtime compatibility index has an invalid shape")
    seen = set()
    pairs = []
    for item in data["pairs"]:
        if (not isinstance(item, dict)
                or set(item) != {"model_sha256", "runtime_sha256", "tested_at"}
                or not isinstance(item.get("model_sha256"), str)
                or not SHA256.fullmatch(item["model_sha256"])
                or not isinstance(item.get("runtime_sha256"), str)
                or not SHA256.fullmatch(item["runtime_sha256"])
                or not isinstance(item.get("tested_at"), str)
                or not TESTED_AT.fullmatch(item["tested_at"])):
            raise ValueError("Runtime compatibility index contains an invalid pair")
        try:
            datetime.strptime(item["tested_at"], "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError(
                "Runtime compatibility index has an invalid timestamp") from exc
        key = (item["model_sha256"], item["runtime_sha256"])
        if key in seen:
            raise ValueError("Runtime compatibility index contains a duplicate pair")
        seen.add(key)
        pairs.append(dict(item))
    return pairs


def list_runtime_self_tests(*, directory: str | Path | None = None) -> dict[str, list[dict]]:
    """Return bounded test records keyed by model SHA-256, without model reads."""
    root = _safe_storage_root(directory)
    by_model: dict[str, list[dict]] = {}
    for item in _read_index(root):
        by_model.setdefault(item["model_sha256"], []).append({
            "runtime_sha256": item["runtime_sha256"],
            "tested_at": item["tested_at"],
        })
    return by_model


def record_runtime_self_test(controller, *,
                             directory: str | Path | None = None) -> dict:
    """Run ForgeOS's synthetic output test and record its exact identity pair.

    The controller supplies already verified startup identities. Both hashes
    are rechecked after the prompt so a stopped or replaced session cannot
    publish a passing record for a different model/runtime pair.
    """
    if not callable(getattr(controller, "running", None)) or not controller.running():
        raise RuntimeError("A verified managed model must be running")
    model_hash = getattr(controller, "model_sha256", None)
    runtime_hash = getattr(controller, "runtime_sha256", None)
    if (not isinstance(model_hash, str) or not SHA256.fullmatch(model_hash)
            or not isinstance(runtime_hash, str)
            or not SHA256.fullmatch(runtime_hash)):
        raise RuntimeError("Managed model/runtime identity is invalid")
    outcome = controller.inference_self_test()
    if (not isinstance(outcome, dict) or outcome.get("status") != "passed"
            or type(outcome.get("response_bytes")) is not int
            or not 1 <= outcome["response_bytes"] <= 4096):
        raise RuntimeError("ForgeOS structured-output self-test did not pass")
    if not controller.running():
        raise RuntimeError("Managed runtime stopped during its self-test")
    if (getattr(controller, "model_sha256", None) != model_hash
            or getattr(controller, "runtime_sha256", None) != runtime_hash):
        raise RuntimeError("Managed identity changed during its self-test")

    root = _safe_storage_root(directory)
    lock = root / LOCK_NAME
    if (_is_reparse_point(lock)
            or (os.path.lexists(lock)
                and (not lock.is_file() or lock.stat().st_nlink != 1))):
        raise ValueError("Runtime compatibility lock is unsafe")
    with _exclusive_download_lock(lock, blocking=True):
        pairs = _read_index(root)
        pairs = [item for item in pairs
                 if (item["model_sha256"], item["runtime_sha256"])
                 != (model_hash, runtime_hash)]
        tested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        pairs.append({"model_sha256": model_hash,
                      "runtime_sha256": runtime_hash,
                      "tested_at": tested_at})
        pairs = pairs[-MAX_PAIRS:]
        payload = json.dumps({"version": 1, "pairs": pairs},
                             sort_keys=True, separators=(",", ":"))
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_INDEX_BYTES:
            raise ValueError("Runtime compatibility index exceeds its size limit")
        fd, temporary_name = tempfile.mkstemp(
            prefix=".forgeos-compat-", suffix=".tmp", dir=root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            current = root / INDEX_NAME
            if _is_reparse_point(current):
                raise ValueError("Runtime compatibility index is a reparse point")
            if (os.path.lexists(current)
                    and (not current.is_file() or current.stat().st_nlink != 1)):
                raise ValueError("Runtime compatibility index is unsafe")
            os.replace(temporary, current)
            final = current.stat(follow_symlinks=False)
            if (not stat.S_ISREG(final.st_mode) or final.st_nlink != 1
                    or final.st_size != len(encoded)):
                raise ValueError("Runtime compatibility index failed publication checks")
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return {**outcome, "model_sha256": model_hash,
            "runtime_sha256": runtime_hash, "tested_at": tested_at}
