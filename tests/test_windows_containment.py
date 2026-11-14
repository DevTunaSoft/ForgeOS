"""Windows Job Object containment runtime tests."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import subprocess
import unittest

from forgeos.windows_containment import prepare_low_integrity_workspace, run_contained


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
        self.assertTrue(result["handle_allowlist"])
        self.assertEqual(result["ui_restrictions"], 0xFF)

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
        with self.assertRaises(subprocess.TimeoutExpired):
            run_contained([sys.executable, "-c", parent_code],
                          self.root, timeout=1)
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

    def test_child_privileges_are_reduced(self):
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
advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                         ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
advapi32.GetTokenInformation.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
token = wintypes.HANDLE()
if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008,
                                 ctypes.byref(token)):
    raise OSError(ctypes.get_last_error(), 'OpenProcessToken failed')
try:
    needed = wintypes.DWORD()
    advapi32.GetTokenInformation(token, 3, None, 0, ctypes.byref(needed))
    buf = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetTokenInformation(token, 3, buf, needed.value,
                                         ctypes.byref(needed)):
        raise OSError(ctypes.get_last_error(), 'GetTokenInformation failed')
    count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    print('privileges=' + str(count))
finally:
    kernel32.CloseHandle(token)
"""
        result = run_contained([sys.executable, "-c", code],
                               self.root, timeout=15)
        self.assertTrue(result["passed"], result)
        counts = [int(line.split("=", 1)[1])
                  for line in result["output"].splitlines()
                  if line.startswith("privileges=")]
        self.assertEqual(len(counts), 1, result)
        self.assertLessEqual(counts[0], 1, result)
        self.assertEqual(result["containment"],
                         "windows-restricted-token+job-object")

    def test_unrestricted_mode_is_explicit_only(self):
        result = run_contained(
            [sys.executable, "-c", "print('diagnostic-unrestricted')"],
            self.root, timeout=15, restrict_privileges=False)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["containment"], "windows-job-object")


    def test_low_integrity_workspace_blocks_write_up(self):
        workspace = self.root / "low-workspace"
        workspace.mkdir()
        outside = self.root / "medium-outside.txt"
        prepare_low_integrity_workspace(workspace)
        code = (
            "from pathlib import Path\n"
            "Path('inside.txt').write_text('inside')\n"
            f"outside=Path({str(outside)!r})\n"
            "try:\n"
            "    outside.write_text('escaped')\n"
            "except OSError as exc:\n"
            "    print('outside=blocked:' + type(exc).__name__)\n"
            "else:\n"
            "    print('outside=wrote')\n"
        )
        result = run_contained(
            [sys.executable, "-c", code], workspace, timeout=15,
            low_integrity=True)
        self.assertTrue(result["passed"], result)
        self.assertEqual(
            result["containment"],
            "windows-restricted-token+job-object+low-integrity")
        self.assertEqual((workspace / "inside.txt").read_text(), "inside")
        self.assertFalse(outside.exists(), result)
        self.assertIn("outside=blocked:", result["output"])
        self.assertNotIn("outside=wrote", result["output"])

    def test_low_integrity_requires_privilege_reduction(self):
        workspace = self.root / "low-workspace-invalid"
        workspace.mkdir()
        with self.assertRaisesRegex(ValueError, "requires privilege-reduced"):
            run_contained(
                [sys.executable, "-c", "pass"], workspace, timeout=15,
                low_integrity=True, restrict_privileges=False)


    def test_controlled_environment_reaches_child(self):
        env = dict(os.environ)
        env["FORGEOS_ENV_PROBE"] = "isolated-value"
        result = run_contained(
            [sys.executable, "-c",
             "import os; print(os.environ.get('FORGEOS_ENV_PROBE','missing'))"],
            self.root, timeout=15, env=env)
        self.assertTrue(result["passed"], result)
        self.assertIn("isolated-value", result["output"])


    def test_unlisted_inheritable_file_handle_does_not_reach_child(self):
        import msvcrt
        leak_path = self.root / "parent-only-handle.bin"
        with leak_path.open("w+b") as stream:
            handle = msvcrt.get_osfhandle(stream.fileno())
            os.set_handle_inheritable(handle, True)
            code = f"""
import ctypes
from ctypes import wintypes
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                               wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                               ctypes.c_void_p]
kernel32.WriteFile.restype = wintypes.BOOL
payload = ctypes.create_string_buffer(b'LEAKED')
written = wintypes.DWORD()
try:
    ok = kernel32.WriteFile(wintypes.HANDLE({handle}), payload, 6,
                             ctypes.byref(written), None)
except OSError:
    # Python 3.15 may surface ERROR_INVALID_HANDLE as OSError instead of
    # returning a false BOOL. That is the expected result when the parent
    # handle was correctly excluded from the child handle list.
    ok = False
print('write=' + str(int(bool(ok))))
"""
            try:
                result = run_contained(
                    [sys.executable, "-c", code], self.root, timeout=15)
            finally:
                os.set_handle_inheritable(handle, False)
            self.assertTrue(result["passed"], result)
            self.assertIn("write=0", result["output"])
            stream.flush()
            stream.seek(0)
            self.assertEqual(stream.read(), b"")

    def test_ui_restrictions_can_only_be_disabled_explicitly(self):
        result = run_contained(
            [sys.executable, "-c", "print('diagnostic-ui')"],
            self.root, timeout=15, restrict_ui=False)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["ui_restrictions"], 0)
