"""Read-only cloud task queue audit; no AI dispatch or repository mutation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.github.com"
READY_LABEL = "forgeos-cloud-ready"
MAX_PAGES = 5


def fetch_pages(repo: str, resource: str, token: str) -> list[dict]:
    results: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{API}/repos/{repo}/{resource}?state=open&per_page=100&page={page}"
        req = Request(url, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "ForgeOS-cloud-supervisor",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urlopen(req, timeout=15) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise ValueError("GitHub returned a non-list response")
        results.extend(batch)
        if len(batch) < 100:
            return results
    raise RuntimeError("Queue exceeds bounded pagination; refusing incomplete audit")


def build_report(issues: list[dict], prs: list[dict]) -> dict:
    active = sorted(
        [{"number": p["number"], "title": p["title"], "draft": p.get("draft", False)}
         for p in prs], key=lambda p: p["number"]
    )
    # A PR's linked issues are not reliably discoverable from titles alone.
    # Flag eligible issues for human review; never dispatch automatically.
    ready = sorted(
        [{"number": i["number"], "title": i["title"]}
         for i in issues
         if "pull_request" not in i
         and READY_LABEL in {label.get("name") for label in i.get("labels", [])}],
        key=lambda i: i["number"]
    )
    return {
        "schema_version": 1,
        "mode": "read-only",
        "ai_dispatch_enabled": False,
        "automatic_merges_enabled": False,
        "active_pull_requests": active,
        "ready_issue_candidates": ready,
        "requires_manual_ownership_review": True,
        "note": "Candidates may overlap open PRs. No task is dispatched without a verified cloud-agent integration.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="cloud-supervisor-report.json")
    args = parser.parse_args()
    repo, token = os.getenv("GH_REPOSITORY", ""), os.getenv("GH_TOKEN", "")
    if not repo or not token:
        parser.error("GH_REPOSITORY and GH_TOKEN must be set")
    try:
        issues = fetch_pages(repo, "issues", token)
        prs = fetch_pages(repo, "pulls", token)
        report = build_report(issues, prs)
    except (HTTPError, URLError, ValueError, RuntimeError, TimeoutError) as exc:
        report = {"schema_version": 1, "mode": "read-only", "error": type(exc).__name__,
                  "message": str(exc)[:500], "ai_dispatch_enabled": False}
        Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 1
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Open PRs: {len(report['active_pull_requests'])}; ready candidates: {len(report['ready_issue_candidates'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
