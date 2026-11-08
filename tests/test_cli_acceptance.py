import io
import json
import unittest
from unittest.mock import patch

from forgeos.cli import main


class AcceptanceCLIContractTests(unittest.TestCase):
    def test_acceptance_command_writes_snapshot_without_opening_task_store(self):
        result = {"path": "C:/tmp/report.json", "record": {"schema": 1}}
        with patch("forgeos.acceptance.write_acceptance_snapshot",
                   return_value=result) as writer, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            code = main(["acceptance"])
        self.assertEqual(code, 0)
        writer.assert_called_once_with()
        self.assertEqual(json.loads(output.getvalue()), result)


if __name__ == "__main__":
    unittest.main()
