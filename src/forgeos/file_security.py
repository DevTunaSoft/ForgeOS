"""Small identity-bound, bounded reads for user-controlled regular files."""
from __future__ import annotations

import os
from pathlib import Path
import stat


def _is_reparse(info) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _same_file(before, after) -> bool:
    return all(getattr(before, field, None) == getattr(after, field, None)
               for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns"))


def _open_bounded_file(path: Path, flags: int) -> int:
    """Open one bounded file through a narrow test seam."""
    return os.open(path, flags)


def read_bounded_file(path: Path, *, max_bytes: int,
                      description: str, opener=None,
                      require_unlinked: bool = True) -> bytes:
    """Read one regular file without losing its path identity."""
    try:
        before = path.lstat()
    except OSError as exc:
        raise ValueError(f"{description} is unavailable") from exc
    if (stat.S_ISLNK(before.st_mode) or _is_reparse(before)
            or not stat.S_ISREG(before.st_mode)
            or (require_unlinked and getattr(before, "st_nlink", 1) != 1)):
        raise ValueError(f"{description} is not a plain file")
    if before.st_size > max_bytes:
        raise ValueError(f"{description} is oversized")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = (opener or _open_bounded_file)(path, flags)
    except OSError as exc:
        raise ValueError(f"{description} could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (_is_reparse(opened) or not stat.S_ISREG(opened.st_mode)
                or (require_unlinked and getattr(opened, "st_nlink", 1) != 1)
                or not _same_file(before, opened)):
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
        after = path.lstat()
    except OSError as exc:
        raise ValueError(f"{description} changed during read") from exc
    if (not _same_file(before, after)
            or (require_unlinked and getattr(after, "st_nlink", 1) != 1)
            or len(content) > max_bytes):
        raise ValueError(f"{description} changed during read")
    return content
