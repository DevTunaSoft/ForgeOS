import unittest
from forgeos.gui import PRESETS, STATUS_LABELS, _format_task


class DashboardContractTests(unittest.TestCase):
    def test_statuses_cover_engine_lifecycle(self):
        for status in ("queued", "planning", "planned", "editing",
                       "testing", "passed", "failed", "blocked"):
            self.assertIn(status, STATUS_LABELS)

    def test_task_row_truncates_untrusted_description(self):
        row = _format_task({"id": 3, "status": "editing", "description": "x" * 300})
        self.assertEqual(row[0], 3)
        self.assertEqual(row[1], "Editing")
        self.assertEqual(len(row[2]), 140)

    def test_brief_rows_work_in_gui_contract(self):
        self.assertEqual(_format_task(
            {"id": 9, "status": "testing", "description": "optimizing"}),
            (9, "Testing", "optimizing"))

    def test_pagination_uses_bounded_sqlite_data_not_complete_task_blobs(self):
        from pathlib import Path
        source = Path(__file__).resolve().parents[1] / "src" / "forgeos" / "gui.py"
        content = source.read_text(encoding="utf-8")
        self.assertIn("store.list(brief=True, limit=page_size", content)
        self.assertIn('page_size = 150', content)
        self.assertNotIn("store.list(brief=True)\n", content)
        self.assertIn('totals = store.counts()', content)

    def test_memory_snapshot_is_on_demand_in_existing_activity_tab(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] /
                "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('text="Show local memory snapshot"', text)
        self.assertIn('from .model_routing import memory_report', text)
        self.assertNotIn("root.after(1000, show_memory)", text)

    def test_acceptance_exercises_actual_tk_event_loop(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("root.withdraw()", source)
        self.assertIn("root.after(300, verify_gui)", source)
        self.assertIn('queue_initialized', source)
        self.assertIn("root.mainloop()", source)

    def test_frozen_acceptance_really_pages_queue_and_samples_rss(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] /
                "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("older_button.invoke()", text)
        self.assertIn("newer_button.invoke()", text)
        self.assertIn('rss_growth_during_paging_bytes', text)
        self.assertIn('paginated_history_ok', text)

    def test_editable_checkout_provides_offline_user_manual(self):
        from forgeos.gui import user_manual_path
        path = user_manual_path()
        self.assertIsNotNone(path)
        self.assertEqual(path.name, "USER_MANUAL.md")
        self.assertIn("# ForgeOS User Manual", path.read_text(encoding="utf-8"))

    def test_presets_are_unique(self):
        self.assertEqual(len(PRESETS), len(set(PRESETS)))
        self.assertIn("python-unittest", PRESETS)
