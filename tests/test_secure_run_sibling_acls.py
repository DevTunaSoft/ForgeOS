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
        exec_root = self.tmp / "secure-runs"
        exec_root.mkdir()
        runtime = create_windows_low_integrity_snapshot(
            workspace, runtime_root=exec_root)
        try:
            result = run_secure_appcontainer(
                [cmd, "/c", "exit 0"], runtime.path,
                _bootstrap_environment(runtime), executable=cmd, timeout=60)
            self.assertTrue(result["passed"])
        finally:
            remove_test_snapshot(workspace, runtime_root=exec_root)
        after = (self._acl(sibling), self._acl(sibling / "notes.txt"))
        self.assertEqual(before, after)

    def test_secure_run_grants_nothing_on_the_workspace_parent(self):
        # The workspace's parent folder is the user's folder. A grant on it rewrites
        # the explicit ACEs of its children, so the run must not grant it at all.
        from forgeos import appcontainer
        from forgeos.appcontainer import run_secure_appcontainer
        from forgeos.secure_runner import _bootstrap_environment
        from forgeos.test_runtime import (
            create_windows_low_integrity_snapshot, remove_test_snapshot)

        projects = self.tmp / "projects"
        workspace = projects / "ForgeOS"
        workspace.mkdir(parents=True)
        (workspace / "app.txt").write_text("workspace", encoding="utf-8")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        cmd = str(bin_dir / "cmd.exe")
        shutil.copy2(
            os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe"), cmd)

        calls = []
        original_icacls = appcontainer._icacls

        def recording_icacls(path, *arguments):
            calls.append((Path(path), arguments))
            return original_icacls(path, *arguments)

        exec_root = self.tmp / "secure-runs"
        exec_root.mkdir()
        runtime = create_windows_low_integrity_snapshot(
            workspace, runtime_root=exec_root)
        appcontainer._icacls = recording_icacls
        try:
            result = run_secure_appcontainer(
                [cmd, "/c", "exit 0"], runtime.path,
                _bootstrap_environment(runtime), executable=cmd, timeout=60)
            self.assertTrue(result["passed"])
        finally:
            appcontainer._icacls = original_icacls
            remove_test_snapshot(workspace, runtime_root=exec_root)
        granted = {path.resolve() for path, arguments in calls
                   if "/grant" in arguments}
        self.assertNotIn(projects.resolve(), granted)

    def test_secure_run_leaves_runner_folder_unchanged(self):
        # The runner is a user-owned executable: here, a copy of cmd.exe in a
        # Downloads-like folder that holds other files. A secure run must leave
        # that folder, and the files in it, exactly as they were.
        from forgeos.appcontainer import run_secure_appcontainer
        from forgeos.secure_runner import _bootstrap_environment
        from forgeos.test_runtime import (
            create_windows_low_integrity_snapshot, remove_test_snapshot)

        downloads = self.tmp / "downloads"
        downloads.mkdir()
        (downloads / "notes.txt").write_text("keep", encoding="utf-8")
        (downloads / "other.txt").write_text("keep", encoding="utf-8")
        cmd = str(downloads / "cmd.exe")
        shutil.copy2(
            os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe"), cmd)
        workspace = self.tmp / "projects" / "ForgeOS"
        workspace.mkdir(parents=True)
        (workspace / "app.txt").write_text("workspace", encoding="utf-8")
        exec_root = self.tmp / "secure-runs"
        exec_root.mkdir()
        watched = (downloads, downloads / "notes.txt", downloads / "other.txt")
        before = {path: self._acl(path) for path in watched}
        runtime = create_windows_low_integrity_snapshot(
            workspace, runtime_root=exec_root)
        try:
            result = run_secure_appcontainer(
                [cmd, "/c", "exit 0"], runtime.path,
                _bootstrap_environment(runtime), executable=cmd, timeout=60)
            self.assertTrue(result["passed"])
        finally:
            remove_test_snapshot(workspace, runtime_root=exec_root)
        after = {path: self._acl(path) for path in watched}
        self.assertEqual(before, after)

    def test_folder_grant_churn_diagnostic(self):
        # Evidence, not a gate. For each fixture and permission, records whether a
        # non-recursive grant on a folder changes the ACLs of its children. The
        # result is printed to the log; the test passes when icacls succeeds.
        import json
        report = {}
        for fixture in ("inherited", "explicit"):
            for permission in ("RX", "X"):
                report[f"{fixture}:{permission}"] = self._folder_grant_churn(
                    fixture, permission)
        print("FOLDER_GRANT_CHURN " + json.dumps(report, sort_keys=True))

    def _folder_grant_churn(self, fixture, permission):
        sid = ("*S-1-15-2-2965108426-1543893344-1388394524-"
               "1232123468-2134956741-1185458015-2187893000")
        users = "*S-1-5-32-545"
        parent = self.tmp / f"{fixture}-{permission}" / "parent"
        child = parent / "child"
        inner = child / "inner.txt"
        sibling = parent / "sibling.txt"
        child.mkdir(parents=True)
        inner.write_text("x", encoding="ascii")
        sibling.write_text("y", encoding="ascii")
        if fixture == "explicit":
            # An inheritable Users ACE on the parent, with explicit copies on the children.
            self._icacls(parent, "/grant", f"{users}:(OI)(CI)(RX)")
            for path in (child, inner, sibling):
                self._icacls(path, "/grant", f"{users}:(RX)")
        objects = {"child": child, "inner": inner, "sibling": sibling}
        before = {name: self._acl(path) for name, path in objects.items()}
        self._icacls(parent, "/grant", f"{sid}:({permission})")
        after_grant = {name: self._acl(path) for name, path in objects.items()}
        self._icacls(parent, "/remove:g", sid)
        after_revoke = {name: self._acl(path) for name, path in objects.items()}
        samples = {}
        for name in objects:
            if before[name] != after_revoke[name]:
                changed = sorted(set(before[name].splitlines())
                                 ^ set(after_revoke[name].splitlines()))
                samples[name] = [line.strip()[:160] for line in changed[:4]]
        return {
            "changed_by_grant": {n: before[n] != after_grant[n] for n in objects},
            "changed_overall": {n: before[n] != after_revoke[n] for n in objects},
            "sample": samples,
        }
