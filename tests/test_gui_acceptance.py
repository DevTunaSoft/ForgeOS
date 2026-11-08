"""Executable GUI smoke contract without Linux display dependencies."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos_build.launcher import main


class GuiAcceptanceCommandTests(unittest.TestCase):
    def test_success_writes_local_report_and_cleans_temp_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "gui-report.json"
            fake = {"gui_started": True, "closed_cleanly": True,
                    "queue_initialized": True,
                    "ram_after_gui_startup_bytes": 28000000,
                    "tabs": ["Development", "Activity & diagnostics"]}
            with patch("forgeos.gui.launch", return_value=fake) as launch:
                code = main(["--gui-acceptance-report", str(destination)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(destination.read_text()), fake)
            self.assertEqual(launch.call_args.kwargs, {"acceptance": True})
            self.assertFalse(Path(launch.call_args.args[0]).exists())

    def test_missing_rss_rejects_apparent_gui_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "gui-report.json"
            with patch("forgeos.gui.launch", return_value={
                "gui_started": True, "queue_initialized": True,
                "closed_cleanly": True,
                "ram_after_gui_startup_bytes": None,
            }):
                code = main(["--gui-acceptance-report", str(destination)])
            self.assertEqual(code, 12)

    def test_tk_failure_writes_false_report_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "gui-report.json"
            with patch("forgeos.gui.launch",
                       side_effect=RuntimeError("test Tk failure")):
                code = main(["--gui-acceptance-report", str(destination)])
            self.assertEqual(code, 12)
            info = json.loads(destination.read_text())
            self.assertFalse(info["gui_started"])
            self.assertFalse(info["closed_cleanly"])
            self.assertIn("test Tk failure", info["error"])

    def test_tk_initialization_failure_closes_store(self):
        from forgeos.gui import launch
        with patch("forgeos.gui.Store") as store, \
             patch("forgeos.gui.tk.Tk",
                   side_effect=RuntimeError("no desktop")):
            with self.assertRaisesRegex(RuntimeError, "no desktop"):
                launch("test.db", acceptance=True)
            store.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
