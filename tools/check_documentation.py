"""Offline documentation link and pull-request code/docs sync checker."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)]+)\)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def visible_lines(source: str):
    """Skip fenced example code without mistaking sample links for live ones."""
    fence = None
    for number, line in enumerate(source.splitlines(), 1):
        marker = re.match(r"^(`{3,}|~{3,})", line.lstrip())
        if marker:
            current = marker.group(1)
            if fence is None:
                fence = current
            elif current[0] == fence[0] and len(current) >= len(fence):
                fence = None
            continue
        if fence is None:
            yield number, line


def heading_slug(heading: str) -> str:
    cleaned = re.sub(r"<[^>]+>", "", heading).replace("`", "")
    cleaned = re.sub(r"[^\w\s-]", "", cleaned.lower(), flags=re.UNICODE).strip()
    return re.sub(r"\s+", "-", cleaned)


def anchors(markdown: str) -> set[str]:
    found, repeated = set(), {}
    for _, line in visible_lines(markdown):
        match = HEADING.match(line)
        if not match:
            continue
        slug = heading_slug(match.group(1))
        count = repeated.get(slug, 0)
        repeated[slug] = count + 1
        found.add(slug if count == 0 else f"{slug}-{count}")
    return found


def markdown_files(root: Path):
    return sorted([root / "README.md", root / "CONTRIBUTING.md",
                   *list((root / "docs").glob("*.md"))])


def check_links(root: Path = ROOT) -> list[str]:
    failures = []
    for markdown in markdown_files(root):
        if not markdown.is_file():
            failures.append(f"Missing canonical file: {markdown.relative_to(root)}")
            continue
        for number, line in visible_lines(markdown.read_text(encoding="utf-8")):
            for match in LINK.finditer(line):
                href = match.group(1).strip().strip("<>")
                if not href or href.startswith(("https://", "http://", "mailto:", "data:")):
                    continue
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", href):
                    continue
                path_part, _, fragment = href.partition("#")
                candidate = (markdown.parent / unquote(path_part)).resolve() if path_part else markdown
                display = f"{markdown.relative_to(root)}:{number}: {href}"
                if not candidate.is_relative_to(root.resolve()):
                    failures.append(f"Escapes repository: {display}")
                    continue
                if not candidate.exists():
                    failures.append(f"Missing target: {display}")
                    continue
                if fragment and candidate.is_file() and candidate.suffix.lower() == ".md":
                    if unquote(fragment).lower() not in anchors(candidate.read_text(encoding="utf-8")):
                        failures.append(f"Missing heading: {display}")
    return failures


def changed_paths(base: str, head: str, root: Path = ROOT) -> set[str]:
    for revision in (base, head):
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", revision) or revision.startswith("-"):
            raise ValueError("Invalid Git revision")
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", base, head],
        cwd=root, check=True, capture_output=True, text=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def check_sync(changed: set[str]) -> list[str]:
    code = {path for path in changed
            if path.startswith(("src/forgeos/", "forgeos_build/"))
            or path == "pyproject.toml"}
    if not code:
        return []
    docs = {path for path in changed if path.endswith(".md") and (
        path.startswith("docs/") or path in ("README.md", "CONTRIBUTING.md"))}
    failures = []
    if not docs:
        failures.append("Product code changed without a README/docs Markdown update")
    if code & {"src/forgeos/gui.py", "src/forgeos/cli.py"} and not docs & {
            "README.md", "docs/USER_GUIDE.md"}:
        failures.append("GUI/CLI changes require README.md or docs/USER_GUIDE.md")
    if "forgeos_build/launcher.py" in code and not docs & {
            "README.md", "docs/WINDOWS_BUILD.md"}:
        failures.append("EXE launcher changes require Windows build documentation")
    security = {"src/forgeos/appcontainer.py", "src/forgeos/windows_containment.py",
                "src/forgeos/secure_runner.py", "src/forgeos/sandbox_runner.py"}
    if code & security and not docs & {
            "README.md", "docs/ARCHITECTURE.md", "docs/WINDOWS_BUILD.md"}:
        failures.append("Execution security changes require trust-boundary documentation")
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--links", action="store_true", help="Check local Markdown links and headings")
    parser.add_argument("--base", help="Base Git revision for PR documentation coverage")
    parser.add_argument("--head", help="Head Git revision for PR documentation coverage")
    args = parser.parse_args(argv)
    if bool(args.base) != bool(args.head):
        parser.error("--base and --head must be supplied together")
    if not args.links and not args.base:
        parser.error("specify --links and/or --base with --head")
    failures = check_links() if args.links else []
    if args.base:
        try:
            failures.extend(check_sync(changed_paths(args.base, args.head)))
        except (ValueError, subprocess.CalledProcessError) as exc:
            failures.append(f"Unable to inspect Git changes: {exc}")
    if failures:
        for failure in failures:
            print("FAIL:", failure, file=sys.stderr)
        return 1
    print("Documentation links / change coverage: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
