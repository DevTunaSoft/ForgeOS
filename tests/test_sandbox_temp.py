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
