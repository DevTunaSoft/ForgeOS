"""Minimal test entry point used only inside ForgeOS secure execution."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


def run_unittest() -> int:
    """Discover and run unittest tests from the granted execution snapshot."""
    if os.environ.get("FORGEOS_EXECUTION_SNAPSHOT") != "1":
        print("ForgeOS secure runner refused: execution snapshot marker missing")
        return 90
    root = Path.cwd().resolve()
    if not root.is_dir():
        print("ForgeOS secure runner refused: invalid working directory")
        return 91
    # Generated project modules must resolve from the disposable snapshot only.
    sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(str(root))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1
