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
        self.assertIn("✅", output.getvalue())


if __name__ == "__main__":
    unittest.main()
