"""The sandboxed child's temp variables must point at a folder it can use."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from forgeos import sandbox_runner

_VARIABLES = ("TEMP", "TMP", "TMPDIR", "LOCALAPPDATA", "APPDATA",
              "FORGEOS_BOOTSTRAP_LOCALAPPDATA")


class SandboxTempTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="forgeos-sandbox-temp-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        saved = {name: os.environ.get(name) for name in _VARIABLES}
        saved_tempdir = tempfile.tempdir
        tempfile.tempdir = None

        def restore():
            tempfile.tempdir = saved_tempdir
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.addCleanup(restore)

    def test_temp_variables_point_inside_the_snapshot_and_work(self):
        os.environ["FORGEOS_BOOTSTRAP_LOCALAPPDATA"] = "1"
        sandbox_runner._scrub_bootstrap_environment(self.root)
        expected = self.root / ".forgeos-runtime" / "home" / "AppData" / "Local" / "Temp"
        for name in ("TEMP", "TMP", "TMPDIR"):
            self.assertEqual(Path(os.environ[name]), expected)
        self.assertTrue(expected.is_dir())
        self.assertEqual(Path(tempfile.gettempdir()), expected)
        folder = Path(tempfile.mkdtemp())
        self.assertTrue(folder.is_dir())
        self.assertTrue(folder.is_relative_to(expected))


class InheritingMkdirTests(unittest.TestCase):
    def test_private_mode_is_replaced_with_the_default_mode(self):
        calls = []

        def fake_mkdir(path, mode=0o777, *, dir_fd=None):
            calls.append((path, mode, dir_fd))

        wrapped = sandbox_runner._inheriting_mkdir(fake_mkdir)
        wrapped("private", 0o700)
        wrapped("shared", 0o755)
        wrapped("default")
        self.assertEqual(calls, [("private", 0o777, None),
                                 ("shared", 0o755, None),
                                 ("default", 0o777, None)])

    def test_private_mode_creates_a_real_folder(self):
        with tempfile.TemporaryDirectory() as parent:
            target = Path(parent) / "private"
            sandbox_runner._inheriting_mkdir(os.mkdir)(str(target), 0o700)
            self.assertTrue(target.is_dir())

    @unittest.skipIf(os.name == "nt", "the install is only active on Windows")
    def test_install_is_a_no_op_outside_windows(self):
        original = os.mkdir
        sandbox_runner._install_inheriting_mkdir()
        self.assertIs(os.mkdir, original)


class PathResolverTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="forgeos-resolver-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "folder").mkdir()
        (self.root / "folder" / "file.txt").write_text("x", encoding="ascii")

    @staticmethod
    def _error(winerror):
        error = OSError(13, "Windows error")
        error.winerror = winerror
        return error

    def _resolver(self, original):
        return sandbox_runner._links_checked_path_resolver(original, base=str(self.root))

    def test_access_denied_gives_the_absolute_path_when_nothing_is_a_link(self):
        def denied(path):
            raise self._error(5)

        target = self.root / "folder" / "file.txt"
        self.assertEqual(self._resolver(denied)(str(target)), os.path.abspath(target))

    def test_other_failures_still_raise(self):
        def missing(path):
            raise self._error(2)

        with self.assertRaises(OSError):
            self._resolver(missing)(str(self.root / "folder"))

    def test_paths_outside_the_snapshot_still_raise(self):
        def denied(path):
            raise self._error(5)

        outside = Path(tempfile.mkdtemp(prefix="forgeos-outside-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        with self.assertRaises(OSError):
            self._resolver(denied)(str(outside))

    @unittest.skipIf(os.name == "nt", "creating a symlink needs privilege on Windows")
    def test_a_link_inside_the_snapshot_still_raises(self):
        def denied(path):
            raise self._error(5)

        link = self.root / "link"
        os.symlink(self.root / "folder", link, target_is_directory=True)
        with self.assertRaises(OSError):
            self._resolver(denied)(str(link / "file.txt"))

    @unittest.skipIf(os.name == "nt", "the install is only active on Windows")
    def test_resolver_install_is_a_no_op_outside_windows(self):
        import ntpath

        before = getattr(ntpath, "_getfinalpathname", None)
        sandbox_runner._install_windows_path_resolver()
        self.assertIs(getattr(ntpath, "_getfinalpathname", None), before)
