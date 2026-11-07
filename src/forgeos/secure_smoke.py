"""Frozen-executable acceptance check for the production AppContainer path."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import tempfile

from .secure_runner import run_secure_windows_test
from .test_runtime import create_windows_low_integrity_snapshot, remove_test_snapshot


def run_secure_smoke() -> int:
    if os.name != "nt":
        print("Secure sandbox smoke is Windows-only")
        return 2
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.5)
    port = listener.getsockname()[1]
    old_secret = os.environ.get("FORGEOS_SMOKE_PARENT_SECRET")
    os.environ["FORGEOS_SMOKE_PARENT_SECRET"] = "must-not-leak"
    try:
        with tempfile.TemporaryDirectory(prefix="forgeos-secure-smoke-") as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.txt"
            test_file = workspace / "test_secure_boundary.py"
            test_file.write_text(
                "import os\n"
                "from pathlib import Path\n"
                "import socket\n"
                "import unittest\n"
                f"OUTSIDE = Path({str(outside)!r})\n"
                f"PORT = {port}\n"
                "class SecureBoundary(unittest.TestCase):\n"
                "    def test_inside_write(self):\n"
                "        Path('inside.txt').write_text('ok')\n"
                "        self.assertEqual(Path('inside.txt').read_text(), 'ok')\n"
                "    def test_parent_secret_not_inherited(self):\n"
                "        self.assertIsNone(os.environ.get('FORGEOS_SMOKE_PARENT_SECRET'))\n"
                "    def test_outside_write_blocked(self):\n"
                "        with self.assertRaises(OSError):\n"
                "            OUTSIDE.write_text('escaped')\n"
                "    def test_loopback_network_blocked(self):\n"
                "        with self.assertRaises(OSError):\n"
                "            socket.create_connection(('127.0.0.1', PORT), timeout=1)\n",
                encoding="utf-8",
            )
            runtime = create_windows_low_integrity_snapshot(workspace)
            try:
                result = run_secure_windows_test(
                    runtime, "python-unittest", timeout=45)
            finally:
                remove_test_snapshot(workspace)
            if not result.get("passed"):
                print(result.get("output", "secure runner failed"))
                return 3
            if result.get("capability_count") != 0:
                print("Secure runner unexpectedly received capabilities")
                return 4
            if result.get("network_capabilities_granted"):
                print("Secure runner unexpectedly received network capability")
                return 5
            if outside.exists():
                print("Secure runner wrote outside disposable snapshot")
                return 6
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                pass
            else:
                connection.close()
                print("Secure runner reached parent localhost listener")
                return 7
            print("ForgeOS secure AppContainer smoke OK")
            return 0
    finally:
        if old_secret is None:
            os.environ.pop("FORGEOS_SMOKE_PARENT_SECRET", None)
        else:
            os.environ["FORGEOS_SMOKE_PARENT_SECRET"] = old_secret
        listener.close()
