"""Streamed task CLI output retains complete JSON and constant row memory."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from forgeos.cli import main
from forgeos.core import Store


class TaskListStreamingContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "state.sqlite"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()

    def test_empty_history_valid_json(self):
        output = io.StringIO()
        with patch.object(Store, "list",
                          side_effect=AssertionError("CLI loaded full task list")), \
             redirect_stdout(output):
            self.assertEqual(main(["--db", str(self.db), "list"]), 0)
        self.assertEqual(json.loads(output.getvalue()), [])

    def test_resume_safe_stream_report_emits_json_lines_and_failure_exit(self):
        output = io.StringIO()
        def fake_resume(*args, **kwargs):
            self.assertFalse(kwargs["collect_results"])
            kwargs["on_event"]({"event": "skipped", "id": 8,
                                "reason": "No structured checkpoint"})
            kwargs["on_event"]({"event": "failed", "id": 7,
                                "reason": "Tests failed"})
            return {"interrupted": 2, "resumed": [], "skipped": [], "failed": [],
                    "counts": {"resumed": 0, "skipped": 1, "failed": 1}}
        with patch("forgeos.recovery.resume_safe", side_effect=fake_resume), \
             redirect_stdout(output):
            code = main(["--db", str(self.db), "resume-safe",
                         "--workspace-parent", str(self.root),
                         "--allow-host-tests", "--stream-report"])
        self.assertEqual(code, 1)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events, [
            {"event": "skipped", "id": 8, "reason": "No structured checkpoint"},
            {"event": "failed", "id": 7, "reason": "Tests failed"},
            {"event": "summary", "interrupted": 2, "resumed": 0,
             "skipped": 1, "failed": 1},
        ])

    def test_stream_preserves_every_original_field_and_unicode(self):
        store = Store(self.db)
        try:
            before = []
            for index in range(35):
                task = store.add(str(self.repo), f"Line\nΩ字 {index}")
                store.update(task, "passed", result="x" * 20000 + "✅",
                             error="error:" + str(index))
            before = store.list()
        finally:
            store.close()
        output = io.StringIO()
        with patch.object(Store, "list",
                          side_effect=AssertionError("CLI loaded full task list")), \
             redirect_stdout(output):
            self.assertEqual(main(["--db", str(self.db), "list"]), 0)
        self.assertEqual(json.loads(output.getvalue()), before)
        self.assertIn("✅", json.loads(output.getvalue())[0]["result"])


if __name__ == "__main__":
    unittest.main()
