"""Windows Job Object containment runtime tests."""
from __future__ import annotations

import os
import csv
from pathlib import Path
import sys
import tempfile
import time
import subprocess
import unittest
from unittest.mock import patch

from forgeos.test_runtime import create_windows_low_integrity_snapshot, remove_test_snapshot
from forgeos.windows_containment import (
    _account_grant_argv, _grant_failure_message, _parse_whoami_user_csv,
    _real_directory, current_account_sid, grant_account_full_control,
    prepare_low_integrity_workspace, run_contained,
)


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
ok = kernel32.WriteFile(wintypes.HANDLE({handle}), payload, 6,
                         ctypes.byref(written), None)
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


class AccountGrantCommandTests(unittest.TestCase):
    """Checks for the snapshot account grant that run on every platform."""

    def test_grant_command_is_single_root_inheritable_and_replaces_one_sid(self):
        argv = _account_grant_argv(
            Path(r"D:\work\.forgeos-exec-task-1"), "S-1-5-21-1-2-3-1001")
        self.assertTrue(argv[0].lower().endswith("icacls.exe"), argv)
        self.assertIn("System32", argv[0])
        self.assertEqual(argv[1], r"D:\work\.forgeos-exec-task-1")
        self.assertEqual(argv[2:], ["/grant:r", "*S-1-5-21-1-2-3-1001:(OI)(CI)F", "/Q"])
        self.assertNotIn("/T", argv)

    def test_whoami_csv_yields_the_sid_and_rejects_malformed_output(self):
        self.assertEqual(
            _parse_whoami_user_csv('"DESKTOP-ABC\\alice","S-1-5-21-1-2-3-1001"\r\n'),
            "S-1-5-21-1-2-3-1001")
        # A header row is not a SID and is ignored.
        self.assertEqual(
            _parse_whoami_user_csv('"User Name","SID"\r\n'
                                   '"DESKTOP-ABC\\alice","S-1-5-21-1-2-3-1001"\r\n'),
            "S-1-5-21-1-2-3-1001")
        for bad in ("", '"only-one-field"\n', '"name","not-a-sid"\n',
                    '"a","S-1-5-21-1-2"\n"b","S-1-5-21-1-3"\n'):
            with self.subTest(output=bad), self.assertRaises(OSError):
                _parse_whoami_user_csv(bad)
        # A csv parse error is reported with the same clear message, not a raw csv.Error.
        with patch("forgeos.windows_containment.csv.reader",
                   side_effect=csv.Error("line contains NUL")):
            with self.assertRaisesRegex(OSError, "current account SID"):
                _parse_whoami_user_csv("anything")

    def test_grant_failure_message_names_the_snapshot_folder_and_permission(self):
        message = _grant_failure_message(
            Path(r"D:\work\.forgeos-exec-task-1"), 5, "Access is denied.")
        self.assertIn("disposable snapshot folder", message)
        self.assertIn(r"D:\work\.forgeos-exec-task-1", message)
        self.assertIn("permission", message)
        self.assertIn("icacls exit 5", message)
        self.assertIn("Access is denied.", message)

    def test_real_directory_refuses_links_and_missing_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target"
            target.mkdir()
            link = Path(temp) / "link"
            try:
                os.symlink(target, link, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symbolic links unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "real directory"):
                _real_directory(link)
            with self.assertRaisesRegex(ValueError, "real directory"):
                _real_directory(Path(temp) / "missing")
            self.assertEqual(_real_directory(target), target.resolve())

    @unittest.skipIf(os.name == "nt", "The refusal path applies off Windows")
    def test_grant_and_sid_lookup_refuse_non_windows(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(OSError, "only available on Windows"):
                grant_account_full_control(temp)
        with self.assertRaisesRegex(OSError, "only available on Windows"):
            current_account_sid()


@unittest.skipUnless(os.name == "nt", "Windows-only account grant tests")
class WindowsAccountGrantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_granted_sid_is_the_restricted_child_identity(self):
        code = r"""
import os, subprocess
exe = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'whoami.exe')
out = subprocess.run([exe, '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True).stdout
print('whoami=' + out.strip())
"""
        workspace = self.root / "sid-check"
        workspace.mkdir()
        result = run_contained([sys.executable, "-c", code], workspace,
                               timeout=30, low_integrity=True)
        self.assertTrue(result["passed"], result)
        line = next(item for item in result["output"].splitlines()
                    if item.startswith("whoami="))
        self.assertEqual(_parse_whoami_user_csv(line[len("whoami="):]),
                         current_account_sid())

    def test_grant_then_label_lets_low_child_write_inside_snapshot_only(self):
        workspace = self.root / "task-1"
        workspace.mkdir()
        source = workspace / "app.py"
        source.write_text("source", encoding="utf-8")
        outside = self.root / "medium-sibling.txt"
        runtime = create_windows_low_integrity_snapshot(workspace)
        self.addCleanup(remove_test_snapshot, workspace)
        code = (
            "from pathlib import Path\n"
            "Path('inside.txt').write_text('inside')\n"
            f"for target in ({str(outside)!r}, {str(source)!r}):\n"
            "    try:\n"
            "        Path(target).write_text('escaped')\n"
            "    except OSError as exc:\n"
            "        print('blocked:' + type(exc).__name__)\n"
            "    else:\n"
            "        print('wrote:' + target)\n"
        )
        result = run_contained([sys.executable, "-c", code], runtime.path,
                               timeout=30, low_integrity=True, env=runtime.env)
        self.assertTrue(result["passed"], result)
        self.assertEqual((runtime.path / "inside.txt").read_text(encoding="utf-8"),
                         "inside")
        self.assertEqual(result["output"].count("blocked:"), 2, result)
        self.assertNotIn("wrote:", result["output"])
        self.assertFalse(outside.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "source")
