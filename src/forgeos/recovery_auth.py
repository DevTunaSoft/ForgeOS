"""Bounded shared-secret authentication for recovery packages."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile

from .core import _publish_database_copy
from .recovery_bundle import (
    _artifact_digest,
    _filename,
    _new_destination,
    _record_filename,
)
from .recovery_package import (
    MAX_RECOVERY_BUNDLE_BYTES,
    check_recovery_bundle,
)
from .worktree_manifest import _is_reparse, _plain_info, _same_file


RECOVERY_AUTH_FORMAT = "forgeos-recovery-bundle-auth-v1"
MAX_RECOVERY_AUTH_BYTES = 4096
MAX_RECOVERY_AUTH_KEY_BYTES = 4096
MIN_RECOVERY_AUTH_KEY_BYTES = 32


def generate_recovery_bundle_auth_key(destination: str | Path, *,
                                      size: int = MIN_RECOVERY_AUTH_KEY_BYTES) -> Path:
    """Create one bounded OS-random shared-secret key without overwriting."""
    if (type(size) is not int
            or not MIN_RECOVERY_AUTH_KEY_BYTES <= size <= MAX_RECOVERY_AUTH_KEY_BYTES):
        raise ValueError(
            "Recovery authentication key size must be between 32 and 4096 bytes")
    target = _new_destination(destination, ())
    key = secrets.token_bytes(size)
    if len(key) != size:
        raise RuntimeError("Recovery authentication key generation was incomplete")
    fd, name = tempfile.mkstemp(
        prefix=".forgeos-recovery-auth-key-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    staging = Path(name)
    try:
        os.chmod(staging, 0o600)
        with staging.open("wb") as output:
            output.write(key)
            output.flush()
            os.fsync(output.fileno())
        _publish_database_copy(staging, target)
        return target
    finally:
        staging.unlink(missing_ok=True)


def _read_bounded_file(source: Path, *, max_bytes: int,
                       description: str) -> bytes:
    """Read one small regular file while preserving its identity checks."""
    info = _plain_info(source, directory=False)
    if info.st_size > max_bytes:
        raise ValueError(f"{description} is oversized")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ValueError(f"{description} could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (_is_reparse(opened) or not stat.S_ISREG(opened.st_mode)
                or not _same_file(info, opened)
                or getattr(opened, "st_nlink", 1) != 1):
            raise ValueError(f"{description} changed or became a link")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            content = stream.read(max_bytes + 1)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"{description} could not be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = source.lstat()
    except OSError as exc:
        raise ValueError(f"{description} changed during read") from exc
    if (not _same_file(info, after)
            or getattr(after, "st_nlink", 1) != 1
            or len(content) > max_bytes):
        raise ValueError(f"{description} changed during read")
    return content


def _read_key(source: str | Path) -> bytes:
    key_path = Path(source).expanduser().absolute()
    key = _read_bounded_file(
        key_path, max_bytes=MAX_RECOVERY_AUTH_KEY_BYTES,
        description="Recovery authentication key")
    if len(key) < MIN_RECOVERY_AUTH_KEY_BYTES:
        raise ValueError("Recovery authentication key is too short")
    return key


def _authenticate(package: Path, key: bytes) -> tuple[int, str, str]:
    authenticator = hmac.new(key, digestmod=hashlib.sha256)
    size, digest = _artifact_digest(
        package, max_bytes=MAX_RECOVERY_BUNDLE_BYTES,
        consumer=authenticator.update)
    return size, digest, authenticator.hexdigest()


def _payload(package: Path, size: int, digest: str, authenticator: str) -> dict:
    return {
        "complete": True,
        "format": RECOVERY_AUTH_FORMAT,
        "package": {
            "filename": _filename(package),
            "hmac_sha256": authenticator,
            "sha256": digest,
            "size": size,
        },
    }


def export_recovery_bundle_auth(package: str | Path, key: str | Path,
                                destination: str | Path) -> Path:
    """Publish a detached HMAC-SHA-256 record for one valid package."""
    package_path = Path(package).expanduser().absolute()
    key_path = Path(key).expanduser().absolute()
    target = _new_destination(destination, (package_path, key_path))
    report = check_recovery_bundle(package_path)
    if not report["valid"]:
        raise ValueError("Recovery bundle is invalid")
    key_bytes = _read_key(key_path)
    size, digest, authenticator = _authenticate(package_path, key_bytes)
    payload = (json.dumps(_payload(package_path, size, digest, authenticator),
                          sort_keys=True, separators=(",", ":"))
               + "\n").encode("utf-8")
    if len(payload) > MAX_RECOVERY_AUTH_BYTES:
        raise RuntimeError("Recovery authentication record is oversized")
    fd, name = tempfile.mkstemp(
        prefix=".forgeos-recovery-auth-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    staging = Path(name)
    try:
        with staging.open("wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        _publish_database_copy(staging, target)
        return target
    finally:
        staging.unlink(missing_ok=True)


def _read_authentication_record(source: Path) -> dict:
    encoded = _read_bounded_file(
        source, max_bytes=MAX_RECOVERY_AUTH_BYTES,
        description="Recovery authentication record")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Recovery authentication record is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("Recovery authentication record must be an object")
    if set(payload) != {"complete", "format", "package"}:
        raise ValueError("Recovery authentication record fields are invalid")
    package = payload.get("package")
    if (payload.get("complete") is not True
            or payload.get("format") != RECOVERY_AUTH_FORMAT
            or not isinstance(package, dict)
            or set(package) != {"filename", "hmac_sha256", "sha256", "size"}):
        raise ValueError("Recovery authentication record fields are invalid")
    try:
        _record_filename(package["filename"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Recovery authentication package filename is invalid") from exc
    for field in ("hmac_sha256", "sha256"):
        value = package[field]
        if (not isinstance(value, str) or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)):
            raise ValueError("Recovery authentication digest is invalid")
    if type(package["size"]) is not int or not 0 <= package["size"] <= MAX_RECOVERY_BUNDLE_BYTES:
        raise ValueError("Recovery authentication package size is invalid")
    return payload


def check_recovery_bundle_auth(package: str | Path, authentication: str | Path,
                               key: str | Path) -> dict:
    """Verify package validity and a detached shared-secret authentication record."""
    package_path = Path(package).expanduser().absolute()
    auth_path = Path(authentication).expanduser().absolute()
    issues = []
    try:
        package_report = check_recovery_bundle(package_path)
        if not package_report["valid"]:
            issues.append({"path": package_path.name,
                           "problem": "recovery bundle is invalid"})
        record = _read_authentication_record(auth_path)
        indexed = record["package"]
        if indexed["filename"] != _filename(package_path):
            issues.append({"path": auth_path.name,
                           "problem": "authentication filename does not match package"})
        key_bytes = _read_key(key)
        size, digest, authenticator = _authenticate(package_path, key_bytes)
        if size != indexed["size"] or digest != indexed["sha256"]:
            issues.append({"path": package_path.name,
                           "problem": "package digest does not match authentication record"})
        if not hmac.compare_digest(authenticator, indexed["hmac_sha256"]):
            issues.append({"path": package_path.name,
                           "problem": "package HMAC does not match authentication record"})
    except (OSError, TypeError, ValueError) as exc:
        issues.append({"path": "", "problem": str(exc)})
    return {
        "valid": not issues,
        "complete": not issues,
        "format": RECOVERY_AUTH_FORMAT if not issues else "",
        "issues": issues,
        "truncated": False,
    }


def check_recovery_bundle_authenticated(package: str | Path,
                                        authentication: str | Path,
                                        key: str | Path, *,
                                        max_issues: int = 32) -> dict:
    """Combine package coherence and detached-authentication reports."""
    report = check_recovery_bundle(package, max_issues=max_issues)
    authentication_report = check_recovery_bundle_auth(
        package, authentication, key)
    if authentication_report["valid"]:
        return {**report, "authentication": authentication_report}
    issues = list(report.get("issues", []))
    for issue in authentication_report["issues"]:
        if len(issues) >= max_issues:
            break
        issues.append({
            "path": issue.get("path", ""),
            "problem": "authentication: " + issue.get(
                "problem", "authentication failed"),
        })
    return {
        **report,
        "valid": False,
        "complete": False,
        "format": "",
        "issues": issues,
        "truncated": report.get("truncated", False)
        or len(issues) < len(report.get("issues", []))
        + len(authentication_report["issues"]),
        "authentication": authentication_report,
    }
