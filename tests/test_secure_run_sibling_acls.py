"""Windows checks that a secure run leaves folders beside the workspace alone."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "Windows AppContainer only")
class SecureRunSiblingAclTests(unittest.TestCase):
    """A secure run must not change folders beside the workspace.

    The workspace's parent is the user's folder. A non-recursive icacls grant on
    that folder re-inherits the explicit ACEs of its children, and a later revoke
    cannot undo it. The secure run therefore grants nothing on that folder.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="forgeos-sibling-acl-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _icacls_path(self):
        return os.path.join(os.environ["SystemRoot"], "System32", "icacls.exe")

    def _icacls(self, path, *arguments):
        subprocess.run(
            [self._icacls_path(), str(path), *arguments, "/Q"],
            capture_output=True, text=True, check=True)

    def _acl(self, path):
        result = subprocess.run(
            [self._icacls_path(), str(path)],
            capture_output=True, text=True, check=True)
        return result.stdout

    def _copy_cmd(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        cmd = str(directory / "cmd.exe")
        shutil.copy2(
            os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe"), cmd)
        return cmd

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
        cmd = self._copy_cmd(self.tmp / "bin")

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

    def test_secure_run_grants_nothing_on_the_workspace_parent(self):
        from forgeos import appcontainer
        from forgeos.appcontainer import run_secure_appcontainer
        from forgeos.secure_runner import _bootstrap_environment
        from forgeos.test_runtime import (
            create_windows_low_integrity_snapshot, remove_test_snapshot)

        projects = self.tmp / "projects"
        workspace = projects / "ForgeOS"
        workspace.mkdir(parents=True)
        (workspace / "app.txt").write_text("workspace", encoding="utf-8")
        cmd = self._copy_cmd(self.tmp / "bin")

        calls = []
        original_icacls = appcontainer._icacls

        def recording_icacls(path, *arguments):
            calls.append((Path(path), arguments))
            return original_icacls(path, *arguments)

        runtime = create_windows_low_integrity_snapshot(workspace)
        appcontainer._icacls = recording_icacls
        try:
            result = run_secure_appcontainer(
                [cmd, "/c", "exit 0"], runtime.path,
                _bootstrap_environment(runtime), executable=cmd, timeout=60)
            self.assertTrue(result["passed"])
        finally:
            appcontainer._icacls = original_icacls
            remove_test_snapshot(workspace)
        granted = {path.resolve() for path, arguments in calls
                   if "/grant" in arguments}
        self.assertNotIn(projects.resolve(), granted)

    def test_non_recursive_grant_changes_children_on_this_fixture(self):
        # Control for the sibling test: the same folder shape must show the
        # re-inheritance that the old parent grant caused, or the unchanged-sibling
        # assertion above proves nothing on this host.
        from forgeos import appcontainer

        folder = self.tmp / "control" / "parent"
        child = folder / "child"
        child.mkdir(parents=True)
        (child / "inner.txt").write_text("inner", encoding="utf-8")
        (folder / "sibling.txt").write_text("sibling", encoding="utf-8")
        watched = (child, child / "inner.txt", folder / "sibling.txt")
        before = [self._acl(path) for path in watched]
        with appcontainer._temporary_probe_profile() as (_profile, sid):
            sid_string = appcontainer._sid_to_string(sid)
            self._icacls(folder, "/grant", f"*{sid_string}:(RX)")
        after = [self._acl(path) for path in watched]
        self.assertNotEqual(
            before, after,
            "fixture is not sensitive to re-inheritance on this host")
