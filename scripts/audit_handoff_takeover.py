"""Repository-only takeover readiness audit. Standard library; read-only."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REQUIRED = {
    "Mission and product rationale": ("docs/PRODUCT_VISION.md", "docs/REQUIREMENTS.md"),
    "Architecture and module ownership": ("docs/ARCHITECTURE.md", "docs/ENGINEERING_TAKEOVER.md"),
    "Operational and historical handoff": ("docs/HANDOFF.md", "docs/HANDOFF_STANDARD.md"),
    "Roadmap and current limitations": ("docs/ROADMAP.md", "docs/ENGINEERING_TAKEOVER.md"),
    "User and recovery instructions": ("docs/USER_GUIDE.md",),
    "Build and release instructions": ("docs/WINDOWS_BUILD.md",),
    "Automated documentation validation": ("scripts/check_handoff.py", "tests/test_handoff_check.py"),
    "Snapshot and recovery context": ("scripts/handoff_snapshot.py", "tests/test_handoff_snapshot.py"),
    "Master PDF publication": ("scripts/export_master_handoff.py", ".github/workflows/master-handoff.yml"),
}
MANUAL = {
    "Exact-commit CI and workflow evidence": "NOT RUN: verify workflow run URL, source SHA and job logs",
    "PDF content and visual inspection": "NOT RUN: inspect the actual published PDF and page renders",
    "Clean-room independent takeover": "NOT RUN: a separate engineer must reproduce the workflow",
    "Physical Windows and local model acceptance": "NOT RUN: real target-machine execution required",
}


def audit(root: Path) -> dict:
    root = root.resolve()
    checks = []
    for title, paths in REQUIRED.items():
        missing = [p for p in paths if not (root / p).is_file()]
        checks.append({
            "criterion": title,
            "status": "FAIL" if missing else "PASS",
            "evidence": list(paths),
            "missing": missing,
            "scope": "file-presence only; content and behavior not certified",
        })
    for title, note in MANUAL.items():
        checks.append({"criterion": title, "status": "NOT RUN", "evidence": note})
    try:
        p = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        sha = p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        sha = None
    return {"source_sha": sha, "checks": checks,
            "status": "FAIL" if any(c["status"] == "FAIL" for c in checks)
            else "INCOMPLETE" if any(c["status"] == "NOT RUN" for c in checks)
            else "PASS"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.root)
    report = json.dumps(result, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            parser.error("Refusing to overwrite an existing audit")
        args.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
