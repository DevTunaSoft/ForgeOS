"""Frozen Windows GUI entry point with a headless packaging smoke check."""
from __future__ import annotations

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="ForgeOS Windows desktop application")
    parser.add_argument("--smoke", action="store_true",
                        help="Check packaged imports without opening a window")
    parser.add_argument("--sandbox-unittest", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--sandbox-pytest", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--secure-sandbox-smoke", action="store_true",
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.sandbox_unittest:
        from forgeos.sandbox_runner import run_unittest
        return run_unittest()
    if args.sandbox_pytest:
        from forgeos.sandbox_runner import run_pytest
        return run_pytest()
    if args.secure_sandbox_smoke:
        from forgeos.secure_smoke import run_secure_smoke
        return run_secure_smoke()
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
