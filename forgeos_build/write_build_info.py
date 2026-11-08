"""Write validated immutable build metadata for the frozen ForgeOS artifact."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re

SHA = re.compile(r"[a-fA-F0-9]{40}\Z")


def write_build_info(source_commit: str, destination: str | Path) -> Path:
    if not isinstance(source_commit, str) or not SHA.fullmatch(source_commit):
        raise ValueError("Build source commit must be a 40-character Git SHA")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"schema": 1, "source_commit": source_commit.lower()},
                   sort_keys=True) + "\n",
        encoding="utf-8")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", default="forgeos_build/build_info.json")
    args = parser.parse_args(argv)
    write_build_info(args.sha, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
