"""Linux-runnable check of the message for a snapshot labelling failure."""
import unittest
from pathlib import Path
from unittest import mock

from forgeos import test_runtime

LABELLER = "forgeos.windows_containment.prepare_low_integrity_workspace"


class SnapshotLabelErrorTests(unittest.TestCase):
    def test_label_failure_names_the_path_limit_and_keeps_the_cause(self):
        original = OSError(
            "Failed to label low-integrity workspace: Failed processing 1 files")
        with mock.patch(LABELLER, side_effect=original):
            with self.assertRaises(OSError) as raised:
                test_runtime._label_snapshot(Path("snapshot"))
        message = str(raised.exception)
        self.assertIn("260-character limit", message)
        self.assertIn("shorten the project path", message)
        self.assertIn("Failed processing 1 files", message)
        self.assertIs(raised.exception.__cause__, original)

    def test_successful_label_is_not_wrapped(self):
        with mock.patch(LABELLER, return_value=None) as labeller:
            test_runtime._label_snapshot(Path("snapshot"))
        labeller.assert_called_once_with(Path("snapshot"))
