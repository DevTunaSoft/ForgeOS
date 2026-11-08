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
                    "ram_after_paging_bytes": 28500000,
                    "rss_growth_during_paging_bytes": 500000,
                    "paginated_history_ok": True,
                    "offline_user_guide_available": True,
                    "tasks_verified": 520,
                    "page_sizes": [150, 150, 150, 70],
                    "tabs": ["Development", "Activity & diagnostics"]}
            with patch("forgeos.gui.launch", return_value=fake) as launch:
                code = main(["--gui-acceptance-report", str(destination)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(destination.read_text()), fake)
            self.assertEqual(launch.call_args.kwargs, {"acceptance": True})
            self.assertFalse(Path(launch.call_args.args[0]).exists())

    def test_synthetic_history_fixture_remains_local_and_complete(self):
        from forgeos_build.launcher import seed_gui_acceptance_history
        from forgeos.core import Store
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "tasks.sqlite3"
            self.assertEqual(seed_gui_acceptance_history(db), 520)
            store = Store(db)
            try:
                self.assertEqual(store.counts()["total"], 520)
                self.assertEqual(store.counts()["passed"], 260)
                self.assertEqual(store.counts()["failed"], 260)
                self.assertEqual(len(store.list(brief=True, limit=150, offset=450)), 70)
                self.assertGreater(len(store.get(520)["result"]), 4000)
            finally:
                store.close()

    def test_launcher_rejects_nonpaged_gui_or_runaway_rss(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "report.json"
            base = {
                "gui_started": True, "queue_initialized": True,
                "closed_cleanly": True, "ram_after_gui_startup_bytes": 28000000,
                "ram_after_paging_bytes": 90000000, "tasks_verified": 520,
                "page_sizes": [150, 150, 150, 70],
                "paginated_history_ok": False,
                "offline_user_guide_available": True,
                "rss_growth_during_paging_bytes": 500000,
            }
            with patch("forgeos.gui.launch", return_value=base):
                self.assertEqual(main(["--gui-acceptance-report", str(dest)]), 12)
            base["paginated_history_ok"] = True
            base["rss_growth_during_paging_bytes"] = 65*1024*1024
            with patch("forgeos.gui.launch", return_value=base):
                self.assertEqual(main(["--gui-acceptance-report", str(dest)]), 12)

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

    def test_missing_offline_guide_fails_packaged_gui_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "gui-report.json"
            with patch("forgeos.gui.launch", return_value={
                "gui_started": True, "queue_initialized": True,
                "closed_cleanly": True,
                "ram_after_gui_startup_bytes": 28000000,
                "ram_after_paging_bytes": 28500000,
                "paginated_history_ok": True,
                "tasks_verified": 520,
                "page_sizes": [150, 150, 150, 70],
                "rss_growth_during_paging_bytes": 500000,
            }):
                self.assertEqual(main(["--gui-acceptance-report", str(report)]), 12)

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
