"""Create a portable, bounded ForgeOS handoff ZIP from tracked documentation.

No secrets, model files, source tree, network calls, or external dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

DOCS = (
    "docs/PRODUCT_VISION.md", "docs/REQUIREMENTS.md",
    "docs/ENGINEERING_TAKEOVER.md", "docs/ARCHITECTURE.md",
    "docs/ROADMAP.md", "docs/HANDOFF_STANDARD.md",
    "docs/USER_GUIDE.md", "docs/WINDOWS_BUILD.md",
    "docs/HANDOFF.md",
)
TOOLS = ("scripts/check_handoff.py", "scripts/handoff_snapshot.py",
         "scripts/audit_handoff_takeover.py", "scripts/export_master_handoff.py")
MAX_FILE_BYTES = 3_000_000
MAX_TOTAL_BYTES = 12_000_000


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args],
                            capture_output=True, text=True, timeout=10, check=True)
    return result.stdout.strip()


def create(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root):
        raise ValueError("Output must be outside repository")
    if output.exists():
        raise FileExistsError("Refusing to overwrite existing package")
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("Expected exact Git repository root")
    if git(root, "status", "--porcelain=v1", "--untracked-files=normal"):
        raise ValueError("Refusing package from dirty checkout")
    revision = git(root, "rev-parse", "HEAD")
    entries = {}
    total = 0
    for rel in DOCS + TOOLS:
        file = root / rel
        if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(root):
            raise ValueError("Missing or unsafe file: " + rel)
        with file.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("Oversized file: " + rel)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Package exceeds size limit")
        entries[rel] = data
    manifest = {
        "schema_version": 1, "source_sha": revision,
        "scope": "Documentation and handoff tools only; not a full repository backup",
        "ci": "UNVERIFIED", "windows_acceptance": "NOT RUN",
        "independent_takeover": "NOT RUN",
        "files": {name: {"sha256": hashlib.sha256(data).hexdigest(),
                         "bytes": len(data)} for name, data in sorted(entries.items())},
    }
    guide = ("ForgeOS recovery handoff\n"
             "1. Compare manifest source_sha with the GitHub repository HEAD.\n"
             "2. Read docs/PRODUCT_VISION.md, docs/ENGINEERING_TAKEOVER.md and docs/HANDOFF.md.\n"
             "3. Reconcile current issues, pull requests and CI before editing.\n"
             "4. Treat historical test claims as unverified until reproduced.\n"
             "5. This ZIP is not a source-code backup or credential archive.\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    members = {**entries, "MANIFEST.json": json.dumps(manifest, indent=2) + "\n",
               "START_HERE.txt": guide}
    with tempfile.TemporaryDirectory(prefix=".forgeos-handoff-", dir=output.parent) as stage:
        staged = Path(stage) / "handoff.zip"
        with zipfile.ZipFile(staged, "x") as archive:
            for name, data in sorted(members.items()):
                member = zipfile.ZipInfo(name)
                member.create_system = 3
                archive.writestr(member, data, compress_type=zipfile.ZIP_DEFLATED,
                                 compresslevel=6)
        digest = hashlib.sha256()
        with staged.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(chunk)
        # Windows rename and POSIX link both refuse an existing destination.
        if os.name == "nt":
            staged.rename(output)
        else:
            os.link(staged, output)
    return {"status": "PASS", "source_sha": revision, "file_count": len(entries),
            "package_sha256": digest.hexdigest(),
            "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create(args.root, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
