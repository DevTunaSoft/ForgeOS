"""Fail-closed materialization of a validated recovery bundle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from .core import (
    MAX_JOURNAL_HANDOFF_MANIFEST_BYTES,
    _move_database_copy,
    _sync_publication_directory,
)
from .recovery_bundle import (
    MAX_RECOVERY_BUNDLE_EVIDENCE_BYTES,
    MAX_RECOVERY_BUNDLE_EVIDENCE_FILE_BYTES,
    MAX_RECOVERY_BUNDLE_HANDOFF_BYTES,
    MAX_RECOVERY_BUNDLE_INDEX_BYTES,
    _artifact_digest,
    _record_filename,
    check_recovery_bundle_index,
)
from .recovery_package import (
    MAX_RECOVERY_BUNDLE_BYTES,
    MAX_RECOVERY_BUNDLE_HEADER_BYTES,
    RECOVERY_BUNDLE_FORMAT,
    RECOVERY_BUNDLE_HEADER_NAME,
    RECOVERY_BUNDLE_INDEX_NAME,
    _bundle_entry_kind,
    _bundle_header,
    _index_rows,
    _materialize_entry,
    _row,
    _zip_bounds,
    check_recovery_bundle,
)
from .worktree_archive import MAX_WORKTREE_ARCHIVE_BYTES, _is_unsafe_zip_entry
from .worktree_manifest import _plain_info


RECOVERY_EXTRACTION_FORMAT = "forgeos-recovery-bundle-extraction-v1"
RECOVERY_EXTRACTION_STATE_NAME = "recovery-bundle-extraction-state.json"
MAX_RECOVERY_EXTRACTION_STATE_BYTES = 2048
MAX_RECOVERY_EXTRACTION_PROBLEM_BYTES = 512


def _destination(destination: str | Path) -> Path:
    try:
        target = Path(destination).expanduser().absolute()
    except TypeError as exc:
        raise ValueError("Recovery extraction destination is invalid") from exc
    if (not target.parent.is_dir() or target.parent.is_symlink()
            or target.exists() or target.is_symlink()):
        raise ValueError(
            "Recovery extraction destination must be a new directory in an existing directory")
    return target


def _state_payload(package_size: int, package_sha256: str, phase: str,
                   *, complete: bool, problem: str | None = None) -> dict:
    payload = {
        "complete": complete,
        "format": RECOVERY_EXTRACTION_FORMAT,
        "package_format": RECOVERY_BUNDLE_FORMAT,
        "package_sha256": package_sha256,
        "package_size": package_size,
        "phase": phase,
    }
    if problem:
        payload["problem"] = problem[:MAX_RECOVERY_EXTRACTION_PROBLEM_BYTES]
    return payload


def _write_state(root: Path, payload: dict) -> None:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"))
               + "\n").encode("utf-8")
    if len(encoded) > MAX_RECOVERY_EXTRACTION_STATE_BYTES:
        raise ValueError("Recovery extraction state is oversized")
    state = root / RECOVERY_EXTRACTION_STATE_NAME
    try:
        info = state.lstat()
    except FileNotFoundError:
        info = None
    except OSError as exc:
        raise ValueError("Recovery extraction state is unavailable") from exc
    if info is not None and (not state.is_file() or state.is_symlink()
                             or getattr(info, "st_nlink", 1) != 1):
        raise ValueError("Recovery extraction state is unsafe")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(state, flags, 0o600)
    except OSError as exc:
        raise ValueError("Recovery extraction state could not be written") from exc
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _sync_publication_directory(root)


def _read_state(root: Path) -> dict:
    state = root / RECOVERY_EXTRACTION_STATE_NAME
    _plain_info(state, directory=False)
    try:
        with state.open("rb") as stream:
            encoded = stream.read(MAX_RECOVERY_EXTRACTION_STATE_BYTES + 1)
    except OSError as exc:
        raise ValueError("Recovery extraction state could not be read") from exc
    if len(encoded) > MAX_RECOVERY_EXTRACTION_STATE_BYTES:
        raise ValueError("Recovery extraction state is oversized")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Recovery extraction state is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("Recovery extraction state must be an object")
    required = {
        "complete", "format", "package_format", "package_sha256",
        "package_size", "phase",
    }
    if not required.issubset(payload):
        raise ValueError("Recovery extraction state fields are incomplete")
    if (payload["format"] != RECOVERY_EXTRACTION_FORMAT
            or payload["package_format"] != RECOVERY_BUNDLE_FORMAT
            or payload["phase"] not in {"publishing", "complete", "failed"}
            or type(payload["complete"]) is not bool
            or type(payload["package_size"]) is not int
            or not 0 <= payload["package_size"] <= MAX_RECOVERY_BUNDLE_BYTES
            or not isinstance(payload["package_sha256"], str)
            or len(payload["package_sha256"]) != 64
            or any(character not in "0123456789abcdef"
                   for character in payload["package_sha256"])):
        raise ValueError("Recovery extraction state fields are invalid")
    if "problem" in payload and (
            not isinstance(payload["problem"], str)
            or len(payload["problem"]) > MAX_RECOVERY_EXTRACTION_PROBLEM_BYTES):
        raise ValueError("Recovery extraction state problem is invalid")
    return payload


def _paths_from_index(root: Path) -> tuple[Path, Path, Path | None, Path | None,
                                               set[str]]:
    rows = _index_rows(root / RECOVERY_BUNDLE_INDEX_NAME)
    handoff_record = _row(rows, "handoff")
    archive_record = _row(rows, "worktree-archive")
    manifest_records = [
        row for row in rows if row.get("record") == "handoff-manifest"]
    if len(manifest_records) > 1:
        raise ValueError("Recovery extraction index contains duplicate manifests")
    evidence_records = [
        row for row in rows if row.get("record") == "evidence"]
    _record_filename(handoff_record["filename"])
    _record_filename(archive_record["filename"])
    handoff = root / "handoff" / handoff_record["filename"]
    source = root / "source" / archive_record["filename"]
    manifest = None
    if manifest_records:
        _record_filename(manifest_records[0]["filename"])
        manifest = root / "manifest" / manifest_records[0]["filename"]
    evidence = {record["filename"] for record in evidence_records}
    for filename in evidence:
        _record_filename(filename)
    evidence_directory = root / "evidence"
    evidence_root = (evidence_directory
                     if evidence or evidence_directory.exists() else None)
    return handoff, source, manifest, evidence_root, evidence


def _check_materialized(root: Path, *, max_issues: int = 32) -> dict:
    header = root / RECOVERY_BUNDLE_HEADER_NAME
    _plain_info(header, directory=False)
    try:
        with header.open("rb") as stream:
            header_bytes = stream.read(1025)
    except OSError as exc:
        raise ValueError("Recovery bundle header could not be read") from exc
    if (len(header_bytes) > MAX_RECOVERY_BUNDLE_HEADER_BYTES
            or header_bytes != _bundle_header()):
        raise ValueError("Recovery bundle header is invalid")
    handoff, source, manifest, evidence_root, indexed_evidence = (
        _paths_from_index(root))
    report = check_recovery_bundle_index(
        root / RECOVERY_BUNDLE_INDEX_NAME, handoff, source,
        manifest=manifest, evidence_directory=evidence_root,
        max_issues=max_issues)
    if not report["valid"]:
        return report
    actual_evidence = set()
    if evidence_root is not None:
        _plain_info(evidence_root, directory=True)
        for entry in evidence_root.iterdir():
            actual_evidence.add(entry.name)
    if actual_evidence != indexed_evidence:
        return {
            **report,
            "valid": False,
            "complete": False,
            "issues": [{
                "line": 0,
                "path": "evidence",
                "problem": "materialized evidence entries do not match its index",
            }],
            "truncated": False,
        }
    return report


def _entry_limit(kind: str) -> int:
    if kind == RECOVERY_BUNDLE_HEADER_NAME:
        return MAX_RECOVERY_BUNDLE_HEADER_BYTES
    if kind == RECOVERY_BUNDLE_INDEX_NAME:
        return MAX_RECOVERY_BUNDLE_INDEX_BYTES
    if kind == "handoff":
        return MAX_RECOVERY_BUNDLE_HANDOFF_BYTES
    if kind == "source":
        return MAX_WORKTREE_ARCHIVE_BYTES
    if kind == "manifest":
        return MAX_JOURNAL_HANDOFF_MANIFEST_BYTES
    return MAX_RECOVERY_BUNDLE_EVIDENCE_FILE_BYTES


def _validated_entries(source: Path) -> dict[str, zipfile.ZipInfo]:
    entry_count, _central_size = _zip_bounds(source)
    try:
        archive = zipfile.ZipFile(source, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Recovery bundle ZIP could not be opened") from exc
    try:
        infos = archive.infolist()
        if len(infos) != entry_count:
            raise ValueError("Recovery bundle entry count is inconsistent")
        entries = {}
        total_bytes = 0
        evidence_bytes = 0
        for info in infos:
            kind, _filename = _bundle_entry_kind(info.filename)
            if (kind == "invalid" or info.is_dir()
                    or _is_unsafe_zip_entry(info)):
                raise ValueError("Recovery bundle contains an unsafe entry")
            if info.filename in entries:
                raise ValueError("Recovery bundle contains a duplicate entry")
            if info.file_size > _entry_limit(kind):
                raise ValueError("Recovery bundle entry exceeds size limit")
            total_bytes += info.file_size
            if total_bytes > MAX_RECOVERY_BUNDLE_BYTES:
                raise ValueError("Recovery bundle contents exceed size limit")
            if kind == "evidence":
                evidence_bytes += info.file_size
                if evidence_bytes > MAX_RECOVERY_BUNDLE_EVIDENCE_BYTES:
                    raise ValueError("Recovery bundle evidence exceeds size limit")
            entries[info.filename] = info
        return entries
    finally:
        archive.close()


def _zip_entry_digest(archive: zipfile.ZipFile, entry: zipfile.ZipInfo,
                      max_bytes: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with archive.open(entry, "r") as stream:
            while True:
                block = stream.read(64 * 1024)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise ValueError("Recovery bundle entry exceeds size limit")
                digest.update(block)
    except ValueError:
        raise
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
        raise ValueError("Recovery bundle entry could not be read") from exc
    if size != entry.file_size:
        raise ValueError("Recovery bundle entry size is inconsistent")
    return size, digest.hexdigest()


def _resume_root(destination: str | Path) -> tuple[Path, dict]:
    root = Path(destination).expanduser().absolute()
    _plain_info(root, directory=True)
    state = _read_state(root)
    if state["phase"] not in {"publishing", "failed"} or state["complete"]:
        raise ValueError("Recovery extraction destination is not resumable")
    return root, state


def _ensure_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _plain_info(path, directory=True)
        return
    try:
        path.mkdir()
    except OSError as exc:
        raise ValueError("Recovery extraction directory could not be created") from exc


def _require_authentication(bundle: Path, authentication, key) -> None:
    if authentication is None and key is None:
        return
    if (authentication is None) != (key is None):
        raise ValueError(
            "Recovery bundle authentication record and key are required together")
    from .recovery_auth import check_recovery_bundle_auth
    report = check_recovery_bundle_auth(bundle, authentication, key)
    if not report["valid"]:
        problem = (report["issues"][0]["problem"]
                   if report["issues"] else "authentication failed")
        raise ValueError("Recovery bundle authentication failed: " + problem)


def resume_recovery_bundle_extraction(source: str | Path,
                                      destination: str | Path, *,
                                      authentication: str | Path | None = None,
                                      key: str | Path | None = None) -> Path:
    """Resume only a marked incomplete extraction without overwriting files."""
    bundle = Path(source).expanduser().absolute()
    root, state = _resume_root(destination)
    package_size = state["package_size"]
    package_sha256 = state["package_sha256"]
    try:
        _plain_info(bundle, directory=False)
        source_size, source_sha256 = _artifact_digest(
            bundle, max_bytes=MAX_RECOVERY_BUNDLE_BYTES)
        if (source_size != package_size or source_sha256 != package_sha256):
            raise ValueError("Recovery bundle does not match extraction marker")
        report = check_recovery_bundle(bundle)
        if not report["valid"]:
            raise ValueError("Recovery bundle is invalid")
        _require_authentication(bundle, authentication, key)
        entries = _validated_entries(bundle)
        staging = Path(tempfile.mkdtemp(
            prefix=".forgeos-recovery-resume-", dir=root.parent))
        try:
            missing = []
            with zipfile.ZipFile(bundle, "r") as archive:
                expected = {}
                for name, entry in entries.items():
                    expected[name] = _zip_entry_digest(
                        archive, entry, _entry_limit(_bundle_entry_kind(name)[0]))
                    destination_path = root / Path(*name.split("/"))
                    if destination_path.exists() or destination_path.is_symlink():
                        try:
                            actual = _artifact_digest(
                                destination_path,
                                max_bytes=_entry_limit(_bundle_entry_kind(name)[0]))
                        except (OSError, ValueError) as exc:
                            raise ValueError(
                                f"Existing recovery entry is unsafe: {name}") from exc
                        if actual != expected[name]:
                            raise ValueError(
                                f"Existing recovery entry does not match: {name}")
                        continue
                    staged_path = staging / Path(*name.split("/"))
                    _materialize_entry(
                        archive, entry, staged_path,
                        _entry_limit(_bundle_entry_kind(name)[0]))
                    staged_digest = _artifact_digest(
                        staged_path,
                        max_bytes=_entry_limit(_bundle_entry_kind(name)[0]))
                    if staged_digest != expected[name]:
                        raise ValueError(
                            f"Staged recovery entry does not match: {name}")
                    missing.append((name, staged_path))
            _write_state(root, _state_payload(
                package_size, package_sha256, "publishing", complete=False))
            for name, staged_path in missing:
                destination_path = root / Path(*name.split("/"))
                _ensure_directory(destination_path.parent)
                _move_database_copy(staged_path, destination_path)
            final_size, final_sha256 = _artifact_digest(
                bundle, max_bytes=MAX_RECOVERY_BUNDLE_BYTES)
            if (final_size != package_size or final_sha256 != package_sha256):
                raise ValueError("Recovery bundle changed during resume")
            final_report = _check_materialized(root)
            if not final_report["valid"]:
                raise RuntimeError(
                    "Resumed recovery bundle failed integrity check")
            verified_size, verified_sha256 = _artifact_digest(
                bundle, max_bytes=MAX_RECOVERY_BUNDLE_BYTES)
            if (verified_size != package_size
                    or verified_sha256 != package_sha256):
                raise ValueError("Recovery bundle changed during final resume check")
            _write_state(root, _state_payload(
                package_size, package_sha256, "complete", complete=True))
            _sync_publication_directory(root)
            _sync_publication_directory(root.parent)
            return root
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    except Exception as exc:
        try:
            _write_state(root, _state_payload(
                package_size, package_sha256, "failed", complete=False,
                problem=str(exc)))
        except (OSError, ValueError):
            pass
        raise


def extract_recovery_bundle(source: str | Path,
                            destination: str | Path, *,
                            authentication: str | Path | None = None,
                            key: str | Path | None = None) -> Path:
    """Materialize one verified bundle into a new marked directory."""
    bundle = Path(source).expanduser().absolute()
    target = _destination(destination)
    _plain_info(bundle, directory=False)
    info = bundle.stat()
    if info.st_size > MAX_RECOVERY_BUNDLE_BYTES:
        raise ValueError("Recovery bundle is oversized")
    report = check_recovery_bundle(bundle)
    if not report["valid"]:
        raise ValueError("Recovery bundle is invalid")
    _require_authentication(bundle, authentication, key)
    package_size, package_sha256 = _artifact_digest(
        bundle, max_bytes=MAX_RECOVERY_BUNDLE_BYTES)
    staging = Path(tempfile.mkdtemp(
        prefix=".forgeos-recovery-extract-", dir=target.parent))
    target_created = False
    try:
        entries = _validated_entries(bundle)
        with zipfile.ZipFile(bundle, "r") as archive:
            for name, entry in entries.items():
                destination_path = staging / Path(*name.split("/"))
                _materialize_entry(
                    archive, entry, destination_path, _entry_limit(
                        _bundle_entry_kind(name)[0]))
        staged_report = _check_materialized(staging)
        if not staged_report["valid"]:
            raise RuntimeError("Recovery bundle extraction self-check failed")
        current_size, current_sha256 = _artifact_digest(
            bundle, max_bytes=MAX_RECOVERY_BUNDLE_BYTES)
        if (current_size != package_size or current_sha256 != package_sha256):
            raise ValueError("Recovery bundle changed during extraction")

        os.mkdir(target)
        target_created = True
        _write_state(target, _state_payload(
            package_size, package_sha256, "publishing", complete=False))
        for name in entries:
            destination_path = target / Path(*name.split("/"))
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            _move_database_copy(
                staging / Path(*name.split("/")), destination_path)
        final_size, final_sha256 = _artifact_digest(
            bundle, max_bytes=MAX_RECOVERY_BUNDLE_BYTES)
        if (final_size != package_size or final_sha256 != package_sha256):
            raise ValueError("Recovery bundle changed during publication")
        final_report = _check_materialized(target)
        if not final_report["valid"]:
            raise RuntimeError("Materialized recovery bundle failed integrity check")
        verified_size, verified_sha256 = _artifact_digest(
            bundle, max_bytes=MAX_RECOVERY_BUNDLE_BYTES)
        if (verified_size != package_size or verified_sha256 != package_sha256):
            raise ValueError("Recovery bundle changed during final verification")
        _write_state(target, _state_payload(
            package_size, package_sha256, "complete", complete=True))
        _sync_publication_directory(target)
        _sync_publication_directory(target.parent)
        return target
    except Exception as exc:
        if target_created and target.is_dir():
            try:
                _write_state(target, _state_payload(
                    package_size, package_sha256, "failed", complete=False,
                    problem=str(exc)))
            except (OSError, ValueError):
                pass
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def check_recovery_bundle_extraction(destination: str | Path,
                                     *, max_issues: int = 32) -> dict:
    """Check a materialized bundle and require its durable completion marker."""
    if (type(max_issues) is not int or isinstance(max_issues, bool)
            or not 1 <= max_issues <= 32):
        raise ValueError("Recovery extraction issue limit must be 1..32")
    root = Path(destination).expanduser().absolute()
    try:
        _plain_info(root, directory=True)
        state = _read_state(root)
        if state["phase"] != "complete" or state["complete"] is not True:
            return {
                "valid": False,
                "complete": False,
                "format": RECOVERY_EXTRACTION_FORMAT,
                "phase": state["phase"],
                "issues": [{
                    "path": RECOVERY_EXTRACTION_STATE_NAME,
                    "problem": "recovery extraction is not complete",
                }],
                "truncated": False,
            }
        report = _check_materialized(root, max_issues=max_issues)
        return {
            **report,
            "complete": bool(report["valid"]),
            "format": RECOVERY_EXTRACTION_FORMAT,
            "phase": state["phase"],
        }
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "valid": False,
            "complete": False,
            "format": "",
            "phase": "",
            "issues": [{"path": "", "problem": str(exc)}],
            "truncated": False,
        }
