"""Frozen Windows GUI entry point with a headless packaging smoke check."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


def main(argv=None):
    parser = argparse.ArgumentParser(description="ForgeOS Windows desktop application")
    parser.add_argument("--smoke", action="store_true",
                        help="Check packaged imports without opening a window")
    parser.add_argument("--gui-acceptance-report", metavar="PATH",
                        help="Exercise actual Tk GUI and write a local JSON report")
    parser.add_argument("--sandbox-unittest", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--secure-sandbox-smoke", action="store_true",
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.sandbox_unittest:
        from forgeos.sandbox_runner import run_unittest
        return run_unittest()
    if args.secure_sandbox_smoke:
        from forgeos.secure_smoke import run_secure_smoke
        return run_secure_smoke()
    if args.gui_acceptance_report:
        # A temporary empty DB, no AI model loads and no network calls.
        from forgeos.gui import launch
        try:
            with tempfile.TemporaryDirectory(prefix="forgeos-gui-acceptance-") as tmp:
                result = launch(str(Path(tmp) / "tasks.sqlite3"), acceptance=True)
        except BaseException as exc:
            result = {"gui_started": False, "closed_cleanly": False,
                      "error": type(exc).__name__ + ": " + str(exc)}
        Path(args.gui_acceptance_report).write_text(
            json.dumps(result, indent=2), encoding="utf-8")
        return 0 if (result.get("gui_started")
                     and result.get("closed_cleanly")) else 12
    if args.smoke:
        from forgeos.core import Store, Ollama
        from forgeos.engine import execute_task
        from forgeos.gui import launch
        from forgeos.workspace import create_workspace
        print("ForgeOS packaged imports OK")
        return 0
    from forgeos.cli import default_db
    from forgeos.gui import launch
    launch(str(default_db()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
