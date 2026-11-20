"""Canonical Windows-portable path-component rules for ForgeOS validators.

Win32 treats a device stem such as ``NUL`` or ``COM1`` as a device even when an
extension follows (``NUL.txt``), removes trailing dots and spaces from path
components and compares names case-insensitively. The edit engine, the GGUF
downloader and the runtime installer validate untrusted names before a write,
so they share these predicates and cannot drift apart. Each caller keeps its own
exception type and any rule of its own, such as the engine's protected folders.
"""
from __future__ import annotations

import re

# Compare with casefold(). COM and LPT also reserve the superscript digits 1-3;
# the engine already rejected COM0 and LPT0 before this module existed.
DEVICE_STEMS = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$"})
_NUMBERED_DEVICE = re.compile(r"(?:com|lpt)[0-9¹²³]")


def has_trailing_dot_or_space(part: str) -> bool:
    """Return True when Win32 would silently strip a trailing dot or space."""
    return part != part.rstrip(" .")


def is_device_name(part: str) -> bool:
    """Return True when the component's stem names a Win32 device.

    The stem is the text before the first dot with trailing spaces removed, so
    ``NUL.txt``, ``nul .txt`` and ``COM¹.py`` are all devices, while
    ``console.py`` and ``COM10.py`` are not.
    """
    stem = part.split(".", 1)[0].rstrip(" ").casefold()
    return stem in DEVICE_STEMS or _NUMBERED_DEVICE.fullmatch(stem) is not None
