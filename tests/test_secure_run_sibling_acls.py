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
    that folder re-processes the security descriptors of its children, and a
    later revoke cannot undo it. The secure run therefore writes the parent's
    read/execute ACE with the folder writer, which leaves the children alone.
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

    def test_secure_run_writes_the_parent_only_through_the_folder_writer(self):
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

        icacls_grants = []
        folder_grants = []
        original_icacls = appcontainer._icacls
        original_folder = appcontainer._grant_directory_read_execute

        def recording_icacls(path, *arguments):
            if "/grant" in arguments:
                icacls_grants.append(Path(path).resolve())
            return original_icacls(path, *arguments)

        def recording_folder(path, sid):
            folder_grants.append(Path(path).resolve())
            return original_folder(path, sid)

        runtime = create_windows_low_integrity_snapshot(workspace)
        appcontainer._icacls = recording_icacls
        appcontainer._grant_directory_read_execute = recording_folder
        try:
            result = run_secure_appcontainer(
                [cmd, "/c", "exit 0"], runtime.path,
                _bootstrap_environment(runtime), executable=cmd, timeout=60)
            self.assertTrue(result["passed"])
        finally:
            appcontainer._icacls = original_icacls
            appcontainer._grant_directory_read_execute = original_folder
            remove_test_snapshot(workspace)
        self.assertNotIn(projects.resolve(), icacls_grants)
        self.assertIn(projects.resolve(), folder_grants)

    def test_folder_grant_and_revoke_leave_children_unchanged(self):
        # Control: the same folder shape must show the re-processing that icacls
        # causes, or the unchanged-children assertions below prove nothing here.
        from forgeos import appcontainer

        def build(name):
            folder = self.tmp / name / "parent"
            child = folder / "child"
            child.mkdir(parents=True)
            (child / "inner.txt").write_text("inner", encoding="utf-8")
            (folder / "sibling.txt").write_text("sibling", encoding="utf-8")
            return folder, (child, child / "inner.txt", folder / "sibling.txt")

        with appcontainer._temporary_probe_profile() as (_profile, sid):
            sid_string = appcontainer._sid_to_string(sid)

            folder, watched = build("control")
            before = [self._acl(path) for path in watched]
            self._icacls(folder, "/grant", f"*{sid_string}:(RX)")
            self.assertNotEqual(
                before, [self._acl(path) for path in watched],
                "fixture is not sensitive to re-processing on this host")

            folder, watched = build("candidate")
            before = [self._acl(path) for path in watched]
            appcontainer._grant_directory_read_execute(folder, sid)
            self.assertEqual(before, [self._acl(path) for path in watched])
            appcontainer._revoke_directory_read_execute(folder, sid)
            self.assertEqual(before, [self._acl(path) for path in watched])
