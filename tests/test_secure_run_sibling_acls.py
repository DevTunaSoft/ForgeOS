import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "Windows AppContainer only")
class SecureRunSiblingAclTests(unittest.TestCase):
    """A secure run must not rewrite the ACLs of folders beside the workspace.

    The AppContainer runner grants read and execute on the snapshot's parent
    folder. On a user's project folder, that grant rewrites the ACEs of the
    folder's children, so unrelated projects beside the workspace change. The
    run must leave their ACLs exactly as they were.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="forgeos-sibling-acl-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _icacls(self, path, *arguments):
        icacls = os.path.join(os.environ["SystemRoot"], "System32", "icacls.exe")
        subprocess.run(
            [icacls, str(path), *arguments, "/Q"],
            capture_output=True, text=True, check=True)

    def _acl(self, path):
        icacls = os.path.join(os.environ["SystemRoot"], "System32", "icacls.exe")
        result = subprocess.run(
            [icacls, str(path)], capture_output=True, text=True, check=True)
        return result.stdout

    def test_secure_run_leaves_sibling_acls_unchanged(self):
        from forgeos.appcontainer import run_secure_appcontainer
        from forgeos.secure_runner import _bootstrap_environment
        from forgeos.test_runtime import (
            create_windows_low_integrity_snapshot, remove_test_snapshot)

        projects = self.tmp / "projects"
        sibling = projects / "other-project"
        sibling.mkdir(parents=True)
        (sibling / "notes.txt").write_text("keep", encoding="utf-8")
        workspace = projects / "ForgeOS"
        workspace.mkdir()
        (workspace / "app.txt").write_text("workspace", encoding="utf-8")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        cmd = str(bin_dir / "cmd.exe")
        shutil.copy2(
            os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe"), cmd)

        # The parent gets an inheritable ACE for Users, and the sibling gets an explicit
        # copy of it. A re-inheritance pass turns that explicit copy into an inherited
        # one, which changes the icacls output.
        users = "*S-1-5-32-545"
        self._icacls(projects, "/grant", f"{users}:(OI)(CI)(RX)")
        self._icacls(sibling, "/grant", f"{users}:(OI)(CI)(RX)")
        before = (self._acl(sibling), self._acl(sibling / "notes.txt"))
        runtime = create_windows_low_integrity_snapshot(workspace)
        try:
            result = run_secure_appcontainer(
                [cmd, "/c", "exit 0"], runtime.path,
                _bootstrap_environment(runtime), executable=cmd, timeout=60)
            self.assertTrue(result["passed"])
        finally:
            remove_test_snapshot(workspace)
        after = (self._acl(sibling), self._acl(sibling / "notes.txt"))
        self.assertEqual(before, after)
