"""Windows checks that a secure run leaves folders beside the workspace alone."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WINDOWS = os.name == "nt"
USERS = "*S-1-5-32-545"


@unittest.skipUnless(WINDOWS, "Windows AppContainer only")
class SecureRunSiblingAclTests(unittest.TestCase):
    """A grant on the snapshot's parent folder must not rewrite its children.

    The secure runner grants read and execute on the snapshot's parent, which is
    the user's folder beside the workspace. icacls /grant without /T on that
    folder re-inherits the explicit ACEs of its children, and the later revoke
    cannot undo it. A secure run must leave every sibling ACL exactly as it was.
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

    def _build(self, name):
        """A parent with an inheritable Users ACE and children holding explicit copies.

        A re-inheritance pass turns each explicit copy into an inherited ACE,
        which changes the icacls output of that child.
        """
        parent = self.tmp / name / "parent"
        parent.mkdir(parents=True)
        self._icacls(parent, "/grant", f"{USERS}:(OI)(CI)(RX)")
        child = parent / "child"
        child.mkdir()
        inner = child / "inner.txt"
        inner.write_text("inner", encoding="utf-8")
        sibling = parent / "sibling.txt"
        sibling.write_text("sibling", encoding="utf-8")
        self._icacls(child, "/grant", f"{USERS}:(OI)(CI)(RX)")
        self._icacls(inner, "/grant", f"{USERS}:(RX)")
        self._icacls(sibling, "/grant", f"{USERS}:(RX)")
        return {"parent": parent, "child": child,
                "inner": inner, "sibling": sibling}

    def _snapshot(self, fixture):
        return [self._acl(fixture[key]) for key in ("child", "inner", "sibling")]

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

        # The parent gets an inheritable Users ACE and the sibling an explicit copy.
        self._icacls(projects, "/grant", f"{USERS}:(OI)(CI)(RX)")
        self._icacls(sibling, "/grant", f"{USERS}:(OI)(CI)(RX)")
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

    def test_grant_and_revoke_leave_children_unchanged(self):
        from forgeos import appcontainer

        with appcontainer._temporary_probe_profile() as (_profile, sid):
            sid_string = appcontainer._sid_to_string(sid)

            # Control: the icacls grant must change a child on this fixture,
            # otherwise the unchanged-children assertions below prove nothing.
            control = self._build("control")
            before = self._snapshot(control)
            self._icacls(control["parent"], "/grant", f"*{sid_string}:(RX)")
            self.assertNotEqual(
                before, self._snapshot(control),
                "fixture is not sensitive to re-inheritance on this host")

            fixture = self._build("candidate")
            before = self._snapshot(fixture)
            appcontainer._grant_read_execute_no_propagation(
                fixture["parent"], sid)
            self.assertEqual(before, self._snapshot(fixture))
            appcontainer._revoke_read_execute_no_propagation(
                fixture["parent"], sid)
            self.assertEqual(before, self._snapshot(fixture))
