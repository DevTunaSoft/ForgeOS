"""Dependency-free checks for ForgeOS handoff documentation."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

REQUIRED = {
    "docs/HANDOFF.md": ("ForgeOS",),
    "docs/HANDOFF_STANDARD.md": ("Why this project exists", "35"),
    "docs/ENGINEERING_TAKEOVER.md": ("Why ForgeOS is being built", "takeover"),
    "docs/PRODUCT_VISION.md": ("ForgeOS",),
    "docs/ARCHITECTURE.md": ("ForgeOS",),
    "docs/ROADMAP.md": ("ForgeOS",),
    "docs/USER_GUIDE.md": ("ForgeOS",),
}
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FENCE = re.compile(r"^\s*(```|~~~)")

def strip_code(text: str) -> str:
    out = []
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            out.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)

def check(root: Path) -> list[str]:
    root = root.resolve()
    errors = []
    for name, markers in REQUIRED.items():
        path = root / name
        if not path.is_file():
            errors.append(f"MISSING: {name}")
            continue
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            errors.append(f"EMPTY: {name}")
        for marker in markers:
            if marker.casefold() not in content.casefold():
                errors.append(f"MISSING TOPIC: {name}: {marker}")
    docs = root / "docs"
    if not docs.is_dir():
        return errors
    for path in sorted(docs.rglob("*.md")):
        for raw in LINK.findall(strip_code(path.read_text(encoding="utf-8"))):
            url = raw.split(" ", 1)[0].strip("<>")
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or url.startswith(("#", "/", "mailto:")):
                continue
            target = (path.parent / unquote(parsed.path)).resolve()
            if not target.is_relative_to(root):
                errors.append(f"ESCAPING LINK: {path.relative_to(root)} -> {url}")
            elif not target.exists():
                errors.append(f"BROKEN LINK: {path.relative_to(root)} -> {url}")
    return errors

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    errors = check(args.root)
    try:
        result = subprocess.run(["git", "-C", str(args.root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        sha = result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        sha = None
    report = {"status": "FAIL" if errors else "PASS", "source_sha": sha, "errors": errors}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Handoff check: {report['status']} (HEAD: {sha or 'unknown'})")
        for error in errors:
            print(error)
    return int(bool(errors))

if __name__ == "__main__":
    raise SystemExit(main())
