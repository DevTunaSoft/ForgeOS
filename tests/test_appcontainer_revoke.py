"""Snapshot grant revocation must never follow a link or junction out of it."""
from pathlib import Path
import os
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from forgeos import appcontainer
from forgeos.appcontainer import (
    _is_reparse_point, _revoke_secure_grants, _revoke_snapshot_grant,
    _snapshot_objects,
)

PROBE_SID = ("S-1-15-2-1000000001-1000000002-1000000003-"
             "1000000004-1000000005-1000000006-1000000007")
REVOKE_ARGUMENTS = ("/remove:g", f"*{PROBE_SID}", "/L", "/Q")


class _SnapshotFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.secret = self.outside / "secret.txt"
        self.secret.write_text("outside-secret")
        self.snap = self.base / "snap"
        self.snap.mkdir()
        (self.snap / "app.py").write_text("print('source')\n")
        (self.snap / "pkg").mkdir()
        (self.snap / "pkg" / "mod.py").write_text("x = 1\n")
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
        # a link would still reach the target, so none may be listed.
        below = [name for name in names if name.startswith(str(link) + os.sep)]
        self.assertEqual(below, [])


class SnapshotListingTests(_SnapshotFixture):
    def test_directory_symlink_is_listed_but_its_target_is_never_entered(self):
        link = self.snap / "linkdir"
        os.symlink(self.outside, link, target_is_directory=True)
        names = {str(path) for path in _snapshot_objects(self.snap)}
        self.assertIn(str(link), names)
        self.assertNotIn(str(self.secret), names)
        self.assert_no_outside_name(names)
        self.assert_nothing_below(names, link)

    def test_relative_directory_symlink_is_not_entered(self):
        link = self.snap / "linkrel"
        os.symlink("../outside", link, target_is_directory=True)
        names = {str(path) for path in _snapshot_objects(self.snap)}
        self.assertIn(str(link), names)
        self.assertNotIn(str(self.secret), names)
        self.assert_no_outside_name(names)
        self.assert_nothing_below(names, link)

    def test_file_symlink_is_listed_but_not_followed(self):
        os.symlink(self.secret, self.snap / "linkfile.txt")
        names = {str(path) for path in _snapshot_objects(self.snap)}
        self.assertIn(str(self.snap / "linkfile.txt"), names)
        self.assertNotIn(str(self.secret), names)

    def test_reparse_point_predicate_stops_descent_at_every_directory(self):
        # Stands in for junctions: every real directory reports a reparse point.
        # Identity is not used here, because DirEntry.stat() on Windows does not
        # fill st_ino.
        def every_directory_is_a_reparse_point(info):
            return stat.S_ISDIR(info.st_mode) or _is_reparse_point(info)

        names = {str(path) for path in _snapshot_objects(
            self.snap, is_reparse_point=every_directory_is_a_reparse_point)}
        self.assertIn(str(self.snap / "pkg"), names)
        self.assertNotIn(str(self.snap / "pkg" / "mod.py"), names)


class SnapshotRevokeCommandTests(_SnapshotFixture):
    def test_every_object_is_named_alone_with_L_and_never_recursively(self):
        link = self.snap / "linkdir"
        os.symlink(self.outside, link, target_is_directory=True)
        _revoke_snapshot_grant(self.snap, PROBE_SID, run_icacls=self.record)
        for _name, arguments in self.calls:
            self.assertEqual(arguments, REVOKE_ARGUMENTS)
            self.assertNotIn("/T", arguments)
        named = self.named()
        self.assertEqual(len(named), len(set(named)))
        for expected in (self.snap, self.snap / "app.py", self.snap / "pkg",
                         self.snap / "pkg" / "mod.py", link):
            self.assertIn(str(expected), named)
        self.assert_no_outside_name(named)
        self.assert_nothing_below(named, link)

    def test_large_tree_is_revoked_one_object_per_call(self):
        bulk = self.snap / "bulk"
        bulk.mkdir()
        for index in range(300):
            (bulk / f"file-{index:04d}.txt").write_text("x")
        expected = [str(path) for path in _snapshot_objects(self.snap)]
        _revoke_snapshot_grant(self.snap, PROBE_SID, run_icacls=self.record)
        self.assertEqual(self.named(), expected)
        self.assertIn(str(bulk / "file-0299.txt"), self.named())

    def test_link_root_is_refused_before_any_icacls_call(self):
        link_root = self.base / "link-root"
        os.symlink(self.snap, link_root, target_is_directory=True)
        with self.assertRaisesRegex(OSError, "not a real directory"):
            _revoke_snapshot_grant(link_root, PROBE_SID, run_icacls=self.record)
        self.assertEqual(self.calls, [])

    def test_failed_object_does_not_stop_the_rest_and_is_reported(self):
        expected = len(_snapshot_objects(self.snap))
        attempts = []

        def fail_first(path, *arguments):
            attempts.append(str(path))
            if len(attempts) == 1:
                raise OSError("icacls failed for the first object")

        with self.assertRaisesRegex(OSError, "1 snapshot objects were not revoked"):
            _revoke_snapshot_grant(self.snap, PROBE_SID, run_icacls=fail_first)
        self.assertEqual(len(attempts), expected)


class SecureGrantCleanupTests(_SnapshotFixture):
    def test_cleanup_revokes_snapshot_by_object_and_outside_path_without_recursion(self):
        link = self.snap / "linkdir"
        os.symlink(self.outside, link, target_is_directory=True)
        grants = [(self.outside, False), (self.snap, True)]
        with patch.object(appcontainer, "_icacls", side_effect=self.record):
            _revoke_secure_grants(grants, PROBE_SID)
        names = self.named()
        for name, arguments in self.calls:
            if Path(name).is_relative_to(self.snap):
                self.assertEqual(arguments, REVOKE_ARGUMENTS, name)
        # The outside directory is revoked only as itself, non-recursively.
        self.assertIn(
            (str(self.outside), ("/remove:g", f"*{PROBE_SID}", "/Q")), self.calls)
        self.assertEqual(
            [name for name in names if Path(name).is_relative_to(self.outside)],
            [str(self.outside)])
        self.assert_nothing_below(names, link)

    def test_cleanup_raises_one_error_listing_every_failure(self):
        with patch.object(appcontainer, "_icacls",
                          side_effect=OSError("failed")):
            with self.assertRaisesRegex(
                    OSError,
                    r"Failed to revoke secure AppContainer grants: "
                    r"\d+ snapshot objects were not revoked: failed"):
                _revoke_secure_grants(
                    [(self.outside, False), (self.snap, True)], PROBE_SID)


def _icacls(path, *arguments):
    result = subprocess.run(
        ["icacls.exe", str(path), *arguments], capture_output=True,
        text=True, timeout=60)
    if result.returncode != 0:
        raise AssertionError(f"icacls failed for {path}: {result.stderr}")
    return result.stdout


@unittest.skipUnless(os.name == "nt", "Windows reparse-point behaviour")
class SnapshotRevokeWindowsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.secret = self.outside / "secret.txt"
        self.secret.write_text("outside-secret")
        # An unrelated holder of the same SID on the outside objects. The
        # revoke must leave them exactly as they are.
        _icacls(self.outside, "/grant", f"*{PROBE_SID}:(OI)(CI)(M)", "/Q")
        _icacls(self.secret, "/grant", f"*{PROBE_SID}:(M)", "/Q")
        self.outside_before = (_icacls(self.outside), _icacls(self.secret))
        self.snap = self.base / "snap"
        self.snap.mkdir()
        (self.snap / "app.py").write_text("print('source')\n")
        # Granted as production does: recursively, before any link exists.
        _icacls(self.snap, "/grant", f"*{PROBE_SID}:(OI)(CI)(M)", "/T", "/Q")

    def assert_outside_unchanged(self):
        self.assertEqual(
            (_icacls(self.outside), _icacls(self.secret)), self.outside_before)
        self.assertIn(PROBE_SID, _icacls(self.outside))

    def test_junction_created_after_the_grant_is_not_followed(self):
        junction = self.snap / "junction"
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction),
             str(self.outside)], check=True, capture_output=True)
        _revoke_snapshot_grant(self.snap, PROBE_SID)
        self.assert_outside_unchanged()
        self.assertNotIn(PROBE_SID, _icacls(self.snap))
        self.assertNotIn(PROBE_SID, _icacls(self.snap / "app.py"))
        self.assertNotIn(PROBE_SID, _icacls(junction, "/L"))

    def test_directory_symlink_created_after_the_grant_is_not_followed(self):
        link = self.snap / "linkdir"
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/D", str(link),
             str(self.outside)], capture_output=True, text=True)
        if created.returncode != 0:
            self.skipTest("directory symlink creation is not permitted here")
        _revoke_snapshot_grant(self.snap, PROBE_SID)
        self.assert_outside_unchanged()
        self.assertNotIn(PROBE_SID, _icacls(self.snap))
        self.assertNotIn(PROBE_SID, _icacls(link, "/L"))

    def test_objects_created_after_the_grant_lose_their_inherited_ace(self):
        late_file = self.snap / "late.txt"
        late_file.write_text("created during the run")
        late_dir = self.snap / "late-dir"
        late_dir.mkdir()
        (late_dir / "nested.txt").write_text("nested")
        _revoke_snapshot_grant(self.snap, PROBE_SID)
        for path in (late_file, late_dir, late_dir / "nested.txt",
                     self.snap / "app.py"):
            self.assertNotIn(PROBE_SID, _icacls(path), str(path))
        self.assert_outside_unchanged()


if __name__ == "__main__":
    unittest.main()
