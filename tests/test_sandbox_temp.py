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
