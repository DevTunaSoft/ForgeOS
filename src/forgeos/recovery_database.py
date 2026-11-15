"""Bounded restoration and explicit activation from recovery bundles."""
from __future__ import annotations

from pathlib import Path
import tempfile
import zipfile

from .core import MAX_BACKUP_MANIFEST_BYTES, Store
from .recovery_bundle import (
    MAX_RECOVERY_BUNDLE_DATABASE_BYTES,
    _artifact_digest,
)
from .recovery_package import (
    MAX_RECOVERY_BUNDLE_BYTES,
    _bundle_entry_kind,
    _materialize_entry,
    check_recovery_bundle,
)


def _require_authentication(bundle: Path, authentication, key) -> None:
    from .recovery_auth import require_recovery_bundle_authentication
    require_recovery_bundle_authentication(bundle, authentication, key)


def _new_destination(destination: str | Path) -> Path:
    try:
        target = Path(destination).expanduser().absolute()
    except TypeError as exc:
        raise ValueError("Recovery database destination is invalid") from exc
    if (not target.parent.is_dir() or target.parent.is_symlink()
            or target.is_symlink() or target.exists()):
        raise ValueError(
            "Recovery database destination must be a new file in an existing directory")
    return target


def _stage_bundle_database(bundle: Path, staging_root: Path,
                            authentication, key) -> tuple[Path, Path | None]:
    """Validate a bundle and stage only its database while its digest is fixed."""
    report = check_recovery_bundle(bundle)
    if not report["valid"]:
        raise ValueError("Recovery bundle is invalid")
    if not report.get("database_present"):
        raise ValueError("Recovery bundle does not contain a database backup")
    _require_authentication(bundle, authentication, key)
    package_size, package_sha256 = _artifact_digest(
        bundle, max_bytes=MAX_RECOVERY_BUNDLE_BYTES)
    database_path = staging_root / "database.sqlite3"
    database_manifest_path = staging_root / "database-manifest.json"
    manifest_path = None
    try:
        with zipfile.ZipFile(bundle, "r") as archive:
            database_entries = []
            manifest_entries = []
            for info in archive.infolist():
                kind, _filename = _bundle_entry_kind(info.filename)
                if kind == "database":
                    database_entries.append(info)
                elif kind == "database-manifest":
                    manifest_entries.append(info)
            if len(database_entries) != 1:
                raise ValueError(
                    "Recovery bundle must contain exactly one database backup")
            if len(manifest_entries) > 1:
                raise ValueError(
                    "Recovery bundle contains duplicate database manifests")
            _materialize_entry(
                archive, database_entries[0], database_path,
                MAX_RECOVERY_BUNDLE_DATABASE_BYTES)
            if manifest_entries:
                _materialize_entry(
                    archive, manifest_entries[0], database_manifest_path,
                    MAX_BACKUP_MANIFEST_BYTES)
                manifest_path = database_manifest_path
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
        raise ValueError("Recovery bundle database could not be staged") from exc

    current_size, current_sha256 = _artifact_digest(
        bundle, max_bytes=MAX_RECOVERY_BUNDLE_BYTES)
    if (current_size != package_size or current_sha256 != package_sha256):
        raise ValueError("Recovery bundle changed during database staging")
    return database_path, manifest_path


def restore_recovery_bundle_database_to(
        source: str | Path, destination: str | Path, *,
        authentication: str | Path | None = None,
        key: str | Path | None = None) -> Path:
    """Restore a bundled SQLite backup only to a brand-new database file.

    The bundle is fully checked before any destination staging. The extracted
    database remains temporary until ``Store.restore_backup_to`` verifies it
    with SQLite's read-only source and publishes a new no-overwrite copy.
    This function never activates the running database or initializes Store.
    """
    bundle = Path(source).expanduser().absolute()
    target = _new_destination(destination)
    with tempfile.TemporaryDirectory(
            prefix=".forgeos-recovery-db-", dir=target.parent) as directory:
        database_path, manifest_path = _stage_bundle_database(
            bundle, Path(directory), authentication, key)
        return Store.restore_backup_to(
            database_path, target, manifest=manifest_path)


def activate_recovery_bundle_database_to(
        source: str | Path, destination: str | Path, rollback: str | Path, *,
        authentication: str | Path | None = None,
        key: str | Path | None = None,
        confirm: bool = False) -> Path:
    """Activate a bundled database through the explicit rollback boundary."""
    if confirm is not True:
        raise ValueError("Explicit activation confirmation is required")
    bundle = Path(source).expanduser().absolute()
    target = Path(destination).expanduser().absolute()
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("Activation destination directory is unsafe")
    with tempfile.TemporaryDirectory(
            prefix=".forgeos-recovery-activate-", dir=target.parent) as directory:
        database_path, manifest_path = _stage_bundle_database(
            bundle, Path(directory), authentication, key)
        return Store.activate_backup_to(
            database_path, target, rollback,
            manifest=manifest_path, confirm=True)
