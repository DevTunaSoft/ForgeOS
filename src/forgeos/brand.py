"""ForgeOS brand palette and packaged brand assets.

Source: the ForgeOS branding kit (assets/branding/ForgeOS_Master_Branding_Kit on
fix/safe-resume-malformed-checkpoints) and its Brand_Standards draft v1.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Brand standards draft v1 (06_Guidelines/Brand_Standards.md).
PALETTE = {
    "midnight": "#080D1A",  # background; the dark wordmark's own background colour
    "steel": "#F5F8FF",
    "forge": "#FF6F1A",
    "ember": "#FFB44A",
    "electric": "#00BFFF",
    "deep_steel": "#232A36",
}

ICON_FILE = "ForgeOS_Windows_Icon.ico"
WORDMARK_DARK_FILE = "ForgeOS_Horizontal_Wordmark_dark.png"


def brand_asset_path(name: str) -> Path | None:
    """Return a packaged brand asset from the frozen bundle or the source tree."""
    candidates = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / "forgeos" / "brand_assets" / name)
    candidates.append(Path(__file__).resolve().parent / "brand_assets" / name)
    for candidate in candidates:
        try:
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
        except OSError:
            continue
    return None
