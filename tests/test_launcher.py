import contextlib
import io
import unittest
from unittest.mock import patch

from forgeos_build.launcher import main


class LauncherTests(unittest.TestCase):
    def test_smoke_imports_without_gui(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--smoke"]), 0)
        self.assertIn("packaged imports OK", output.getvalue())

    def test_launch_routes_to_gui(self):
        with patch("forgeos.gui.launch") as launch, patch(
            "forgeos.cli.default_db", return_value="/tmp/forgeos-test.sqlite"
        ):
            self.assertEqual(main([]), 0)
        launch.assert_called_once_with("/tmp/forgeos-test.sqlite")
