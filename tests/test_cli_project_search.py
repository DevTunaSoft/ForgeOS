import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from forgeos.cli import main


class ProjectSearchCliTests(unittest.TestCase):
    def test_local_symbol_search_emits_bounded_metadata_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app.py").write_text(
                "def recover_task(task_id):\n return 'PRIVATE_VALUE'\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["search-project", str(root), "recover task"])
            self.assertEqual(code, 0)
            records = json.loads(output.getvalue())
            self.assertEqual(records[0]["name"], "recover_task")
            self.assertEqual(records[0]["path"], "app.py")
            self.assertNotIn("PRIVATE_VALUE", output.getvalue())
