import json
from pathlib import Path
import tempfile
import unittest

from forgeos_build.write_build_info import write_build_info


class BuildInfoTests(unittest.TestCase):
    def test_write_build_info_normalizes_and_round_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "build_info.json"
            write_build_info("A" * 40, target)
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload, {"schema": 1, "source_commit": "a" * 40})

    def test_invalid_sha_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                write_build_info("../bad", Path(temp) / "build_info.json")


if __name__ == "__main__":
    unittest.main()
