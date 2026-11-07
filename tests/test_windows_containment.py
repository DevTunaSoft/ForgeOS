"""Windows Job Object containment runtime tests."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from forgeos.windows_containment import run_contained


@unittest.skipUnless(os.name == "nt", "Windows-only containment tests")
class WindowsContainmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_success_and_output_capture(self):
        result = run_contained(
            [sys.executable, "-c", "print('contained-ok')"],
            self.root, timeout=15)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["returncode"], 0)
        self.assertIn("contained-ok", result["output"])
        self.assertEqual(result["containment"], "windows-restricted-token+job-object")

    def test_nonzero_exit_is_reported(self):
        result = run_contained(
            [sys.executable, "-c", "import sys; print('bad'); sys.exit(7)"],
            self.root, timeout=15)
        self.assertFalse(result["passed"])
        self.assertEqual(result["returncode"], 7)
        self.assertIn("bad", result["output"])

    def test_timeout_terminates_process_tree(self):
        marker = self.root / "child-survived.txt"
        child_code = (
            "import time,pathlib; time.sleep(4); "
            f"pathlib.Path({str(marker)!r}).write_text('escaped')"
        )
        parent_code = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
            "time.sleep(30)"
        )
        start = time.monotonic()
        with self.assertRaises(Exception) as caught:
            run_contained([sys.executable, "-c", parent_code],
                          self.root, timeout=1)
        self.assertIn("TimeoutExpired", type(caught.exception).__name__)
        self.assertLess(time.monotonic() - start, 10)
        time.sleep(5)
        self.assertFalse(marker.exists(), "child process escaped the terminated job")

    def test_active_process_limit_is_enforced(self):
        code = (
            "import subprocess,sys,time; children=[]\n"
            "for i in range(4):\n"
            "  try: children.append(subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)']))\n"
            "  except OSError: pass\n"
            "print(len(children))\n"
            "[p.wait() for p in children]\n"
        )
        result = run_contained([sys.executable, "-c", code],
                               self.root, timeout=15,
                               active_process_limit=2)
        self.assertIn(result["returncode"], (0, 1))
        # Parent + at most one child may be active when the limit is two.
        numbers = [int(line) for line in result["output"].splitlines()
                   if line.strip().isdigit()]
        self.assertTrue(numbers)
        self.assertLessEqual(numbers[-1], 1)

    def test_child_token_is_actually_restricted(self):
        code = r"""
import ctypes
from ctypes import wintypes
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.HANDLE)]
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.IsTokenRestricted.argtypes = [wintypes.HANDLE]
advapi32.IsTokenRestricted.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
token = wintypes.HANDLE()
if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008,
                                 ctypes.byref(token)):
    raise OSError(ctypes.get_last_error(), 'OpenProcessToken failed')
try:
    print('restricted=' + str(int(bool(advapi32.IsTokenRestricted(token)))))
finally:
    kernel32.CloseHandle(token)
"""
        result = run_contained([sys.executable, "-c", code],
                               self.root, timeout=15)
        self.assertTrue(result["passed"], result)
        self.assertIn("restricted=1", result["output"])
        self.assertEqual(result["containment"],
                         "windows-restricted-token+job-object")

    def test_unrestricted_mode_is_explicit_only(self):
        result = run_contained(
            [sys.executable, "-c", "print('diagnostic-unrestricted')"],
            self.root, timeout=15, restrict_privileges=False)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["containment"], "windows-job-object")
