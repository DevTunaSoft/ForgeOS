"""Bounded, deterministic repository inventory for microtask planning (roadmap P1).

The inventory holds metadata only: a relative POSIX path, a byte size, a coarse
kind, and test and manifest flags. It never reads file bodies and never reports
absolute paths. It takes its files from the walker in context_index, so the
planner and the local model see the same exclusions and symlink rules. Results
are sorted by casefolded path, so an unchanged tree gives the same inventory.
"""
from __future__ import annotations

import stat
from pathlib import Path

from .context_index import candidate_sources

SCHEMA_VERSION = 1
MAX_INVENTORY_FILES = 2000
MANIFEST_NAMES = frozenset({
    "pyproject.toml", "setup.cfg", "setup.py", "requirements.txt",
    "package.json", "go.mod", "Cargo.toml"})
_KINDS = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".cs": "csharp", ".java": "java", ".md": "markdown", ".json": "json",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml"}


def _is_test(relative: Path) -> bool:
    return (relative.name.startswith("test_") or relative.name.endswith("_test.py")
            or any(part.lower() in ("test", "tests") for part in relative.parts[:-1]))


def build_inventory(root: str | Path, *, limit: int = MAX_INVENTORY_FILES) -> dict:
    """Return a bounded inventory of source metadata for one repository root."""
    if type(limit) is not int or not 1 <= limit <= MAX_INVENTORY_FILES:
        raise ValueError(f"Inventory limit must be 1 to {MAX_INVENTORY_FILES}")
    origin = Path(root).resolve(strict=True)
    if not origin.is_dir():
        raise ValueError("Inventory root is not a directory")
    candidates, scan_exhausted = candidate_sources(origin)
    candidates.sort(key=lambda item: item[0].as_posix().casefold())
    files = []
    for relative, source in candidates:
        if len(files) >= limit:
            break
        try:
            info = source.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        files.append({
            "path": relative.as_posix(),
            "bytes": info.st_size,
            "kind": _KINDS.get(relative.suffix.lower(), "other"),
            "test": _is_test(relative),
            "manifest": relative.name in MANIFEST_NAMES,
        })
    total = len(candidates)
    return {
        "schema_version": SCHEMA_VERSION,
        "files": files,
        "total_candidates": total,
        "scan_exhausted": scan_exhausted,
        "truncated": scan_exhausted or total > len(files),
    }
