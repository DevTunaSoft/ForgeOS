"""The Python runtime probe must never grant or revoke through a link or junction."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from forgeos import appcontainer
from forgeos.appcontainer import (
    _grant_path_read_execute, _grant_probe_access,
    _refuse_linked_runtime_targets, _revoke_probe_runtime_targets,
    _revoke_runtime_target_grant,
)

PROBE_SID = ("S-1-15-2-1000000001-1000000002-1000000003-"
             "1000000004-1000000005-1000000006-1000000007")
GRANT_DIRECTORY = ("/grant", f"*{PROBE_SID}:(OI)(CI)(RX)", "/L", "/Q")
GRANT_FILE = ("/grant", f"*{PROBE_SID}:(RX)", "/L", "/Q")
REVOKE_ARGUMENTS = ("/remove:g", f"*{PROBE_SID}", "/L", "/Q")


class _RuntimeFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.secret = self.outside / "secret.txt"
        self.secret.write_text("outside-secret")
        # A stand-in for one tree the probe grants, such as the stdlib Lib.
        self.runtime = self.base / "Lib"
        self.runtime.mkdir()
        (self.runtime / "os.py").write_text("x = 1\n")
        (self.runtime / "encodings").mkdir()
        (self.runtime / "encodings" / "__init__.py").write_text("")
        self.calls = []

    def record(self, path, *arguments):
        # icacls is called with exactly one object name and its arguments.
        self.assertIsInstance(path, (str, Path))
        self.calls.append((str(path), tuple(arguments)))

    def named(self):
        return [name for name, _arguments in self.calls]

    def assert_no_outside_name(self, names):
        for name in names:
            self.assertFalse(Path(name).is_relative_to(self.outside), name)

    def assert_nothing_below(self, names, link):
        # /L protects only the final path component. A name that runs through
        # a link would still reach the target, so none may be named.
        below = [name for name in names if name.startswith(str(link) + os.sep)]
        self.assertEqual(below, [])


class RuntimeGrantCommandTests(_RuntimeFixture):
    def test_directory_is_granted_one_object_per_call_and_never_recursively(self):
        skipped = _grant_path_read_execute(
            self.runtime, PROBE_SID, run_icacls=self.record)
        self.assertEqual(skipped, [])
        for _name, arguments in self.calls:
            self.assertNotIn("/T", arguments)
        expected = {
            str(self.runtime): GRANT_DIRECTORY,
            str(self.runtime / "os.py"): GRANT_FILE,
            str(self.runtime / "encodings"): GRANT_DIRECTORY,
            str(self.runtime / "encodings" / "__init__.py"): GRANT_FILE,
        }
        self.assertEqual(len(self.calls), len(expected))
        self.assertEqual(dict(self.calls), expected)

    def test_symlinks_are_listed_as_skipped_and_their_targets_never_entered(self):
        link = self.runtime / "linkdir"
        os.symlink(self.outside, link, target_is_directory=True)
        file_link = self.runtime / "linkfile.txt"
        os.symlink(self.secret, file_link)
        skipped = _grant_path_read_execute(
            self.runtime, PROBE_SID, run_icacls=self.record)
        self.assertEqual(set(skipped), {link, file_link})
        names = self.named()
        self.assertNotIn(str(link), names)
        self.assertNotIn(str(file_link), names)
        self.assert_no_outside_name(names)
        self.assert_nothing_below(names, link)
        for _name, arguments in self.calls:
            self.assertNotIn("/T", arguments)

    def test_file_target_is_granted_by_name_with_L_only(self):
        target = self.runtime / "os.py"
        _grant_path_read_execute(target, PROBE_SID, run_icacls=self.record)
        self.assertEqual(self.calls, [(str(target), GRANT_FILE)])

    def test_linked_root_is_refused_before_any_icacls_call(self):
        link_root = self.base / "link-root"
        os.symlink(self.runtime, link_root, target_is_directory=True)
        with self.assertRaisesRegex(OSError, "link or junction"):
            _grant_path_read_execute(
                link_root, PROBE_SID, run_icacls=self.record)
        self.assertEqual(self.calls, [])

    def test_failed_object_does_not_stop_the_rest_and_is_reported(self):
        attempts = []

        def fail_first(path, *arguments):
            attempts.append(str(path))
            if len(attempts) == 1:
                raise OSError("icacls failed for the first object")

        with self.assertRaisesRegex(
                OSError, "1 runtime objects were not granted"):
            _grant_path_read_execute(
                self.runtime, PROBE_SID, run_icacls=fail_first)
        self.assertEqual(len(attempts), 4)

    def test_a_linked_required_path_is_refused_before_any_grant(self):
        link = self.base / "pyvenv-link.cfg"
        os.symlink(self.secret, link)
        _refuse_linked_runtime_targets([self.runtime, self.runtime / "os.py"])
        with self.assertRaisesRegex(OSError, "link or junction"):
            _refuse_linked_runtime_targets([self.runtime, link])

    def test_workspace_grant_is_named_without_recursion(self):
        root = self.base / "probe-root"
        root.mkdir()
        allowed = root / "allowed"
        allowed.mkdir()
        with patch.object(appcontainer, "_icacls", side_effect=self.record), \
                patch.object(appcontainer, "wc", create=True) as containment:
            _grant_probe_access(root, allowed, PROBE_SID)
        self.assertEqual(self.calls, [
            (str(root), ("/grant", f"*{PROBE_SID}:(RX)", "/L", "/Q")),
            (str(allowed),
             ("/grant", f"*{PROBE_SID}:(OI)(CI)(M)", "/L", "/Q")),
        ])
        containment.prepare_low_integrity_workspace.assert_called_once_with(
            allowed)


class RuntimeRevokeCommandTests(_RuntimeFixture):
    def test_every_object_is_revoked_alone_with_L_and_never_recursively(self):
        link = self.runtime / "linkdir"
        os.symlink(self.outside, link, target_is_directory=True)
        _revoke_runtime_target_grant(
            self.runtime, PROBE_SID, run_icacls=self.record)
        for _name, arguments in self.calls:
            self.assertEqual(arguments, REVOKE_ARGUMENTS)
        named = self.named()
        self.assertEqual(len(named), len(set(named)))
        for expected in (self.runtime, self.runtime / "os.py",
                         self.runtime / "encodings",
                         self.runtime / "encodings" / "__init__.py", link):
            self.assertIn(str(expected), named)
        self.assert_no_outside_name(named)
        self.assert_nothing_below(named, link)

    def test_file_target_is_revoked_by_name_with_L(self):
        target = self.runtime / "os.py"
        _revoke_runtime_target_grant(target, PROBE_SID, run_icacls=self.record)
        self.assertEqual(self.calls, [(str(target), REVOKE_ARGUMENTS)])

    def test_target_that_became_a_link_is_refused_before_any_icacls_call(self):
        link_root = self.base / "became-link"
        os.symlink(self.runtime, link_root, target_is_directory=True)
        with self.assertRaisesRegex(OSError, "was not revoked"):
            _revoke_runtime_target_grant(
                link_root, PROBE_SID, run_icacls=self.record)
        self.assertEqual(self.calls, [])

    def test_cleanup_revokes_in_reverse_order_without_recursion(self):
        with patch.object(appcontainer, "_icacls", side_effect=self.record):
            _revoke_probe_runtime_targets(
                [self.runtime, self.runtime / "os.py"], PROBE_SID)
        self.assertEqual(self.named()[0], str(self.runtime / "os.py"))
        for _name, arguments in self.calls:
            self.assertNotIn("/T", arguments)

    def test_cleanup_raises_one_error_listing_every_failure(self):
        with patch.object(appcontainer, "_icacls",
                          side_effect=OSError("failed")):
            with self.assertRaisesRegex(
                    OSError,
                    r"Failed to revoke temporary Python runtime ACEs: failed; "
                    r"Runtime tree .+ was not fully revoked: "
                    r"4 snapshot objects were not revoked"):
                _revoke_probe_runtime_targets(
                    [self.runtime, self.runtime / "os.py"], PROBE_SID)


def _icacls(path, *arguments):
    result = subprocess.run(
        ["icacls.exe", str(path), *arguments], capture_output=True,
        text=True, timeout=60)
    if result.returncode != 0:
        raise AssertionError(f"icacls failed for {path}: {result.stderr}")
    return result.stdout


@unittest.skipUnless(os.name == "nt", "Windows reparse-point behaviour")
class RuntimeLinkWindowsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.secret = self.outside / "secret.txt"
        self.secret.write_text("outside-secret")
        # An unrelated holder of the probe SID on the outside objects. Grant and
        # revoke must leave them exactly as they are.
        _icacls(self.outside, "/grant", f"*{PROBE_SID}:(OI)(CI)(M)", "/Q")
        _icacls(self.secret, "/grant", f"*{PROBE_SID}:(M)", "/Q")
        self.outside_before = (_icacls(self.outside), _icacls(self.secret))
        self.runtime = self.base / "Lib"
        self.runtime.mkdir()
        (self.runtime / "os.py").write_text("x = 1\n")
        (self.runtime / "pkg").mkdir()
        (self.runtime / "pkg" / "mod.py").write_text("y = 2\n")
        # The link exists before the grant, as a planted link would.
        self.junction = self.runtime / "pkg" / "junction"
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(self.junction),
             str(self.outside)], check=True, capture_output=True)

    def assert_outside_unchanged(self):
        self.assertEqual(
            (_icacls(self.outside), _icacls(self.secret)), self.outside_before)
        self.assertIn(PROBE_SID, _icacls(self.outside))

    def test_grant_and_revoke_leave_a_junctioned_outside_target_unchanged(self):
        skipped = _grant_path_read_execute(self.runtime, PROBE_SID)
        self.assertEqual(skipped, [self.junction])
        self.assertIn(PROBE_SID, _icacls(self.runtime / "os.py"))
        self.assertIn(PROBE_SID, _icacls(self.runtime / "pkg" / "mod.py"))
        self.assert_outside_unchanged()
        _revoke_runtime_target_grant(self.runtime, PROBE_SID)
        for path in (self.runtime, self.runtime / "os.py",
                     self.runtime / "pkg", self.runtime / "pkg" / "mod.py"):
            self.assertNotIn(PROBE_SID, _icacls(path), str(path))
        self.assertNotIn(PROBE_SID, _icacls(self.junction, "/L"))
        self.assert_outside_unchanged()

    def test_directory_symlink_is_not_granted_or_revoked_through(self):
        link = self.runtime / "linkdir"
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/D", str(link),
             str(self.outside)], capture_output=True, text=True)
        if created.returncode != 0:
            self.skipTest("directory symlink creation is not permitted here")
        self.assertIn(link, _grant_path_read_execute(self.runtime, PROBE_SID))
        self.assert_outside_unchanged()
        _revoke_runtime_target_grant(self.runtime, PROBE_SID)
        self.assert_outside_unchanged()
        self.assertNotIn(PROBE_SID, _icacls(self.runtime / "os.py"))


if __name__ == "__main__":
    unittest.main()
