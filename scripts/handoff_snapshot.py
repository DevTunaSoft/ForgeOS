"""Create a deterministic, read-only ForgeOS repository handoff snapshot.

No GitHub API calls, network requests, secrets, background processes or AI inference.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def git(root: Path, *args: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return p.returncode == 0, p.stdout.strip()


def snapshot(root: Path) -> dict:
    root = root.resolve()
    valid, toplevel = git(root, "rev-parse", "--show-toplevel")
    if not valid or Path(toplevel).resolve() != root:
        raise ValueError("Expected the exact root of a Git repository")
    head_ok, head = git(root, "rev-parse", "HEAD")
    branch_ok, branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    status_ok, status = git(root, "status", "--porcelain=v1", "--untracked-files=normal")
    remote_ok, remote = git(root, "remote", "get-url", "origin")
    if not head_ok or not status_ok:
        raise RuntimeError("Cannot obtain Git HEAD or working-tree status")
    # Avoid serializing filenames, environment values, credentials or remote URLs.
    return {
        "schema_version": 1,
        "source": "local-git",
        "head": head,
        "branch": branch if branch_ok else None,
        "detached_head": not branch_ok,
        "dirty": bool(status),
        "changed_entry_count": len(status.splitlines()) if status else 0,
        "origin_configured": remote_ok and bool(remote),
        "github_issues": "UNVERIFIED: requires live GitHub reconciliation",
        "github_pull_requests": "UNVERIFIED: requires live GitHub reconciliation",
        "ci": "UNVERIFIED: requires exact-commit CI inspection",
        "physical_windows": "UNVERIFIED: requires real-device acceptance evidence",
        "model_runtime": "UNVERIFIED: requires executed inference evidence",
        "next_step": "Inspect docs/ENGINEERING_TAKEOVER.md and docs/HANDOFF.md; reconcile GitHub before editing",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="Optional JSON output file; no write by default")
    args = parser.parse_args()
    try:
        result = snapshot(args.root)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    result["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    data = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        # Avoid accidentally overwriting tracked source or existing user files.
        target = args.output.resolve()
        if target.exists():
            parser.error("Output exists; refusing to overwrite")
        if target.is_relative_to(args.root.resolve()):
            parser.error("Write snapshots outside the source checkout to avoid dirtying it")
        target.write_text(data, encoding="utf-8")
    else:
        print(data, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
