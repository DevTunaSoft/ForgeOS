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

    def test_presets_are_unique(self):
        self.assertEqual(len(PRESETS), len(set(PRESETS)))
        self.assertIn("python-unittest", PRESETS)
