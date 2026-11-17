"""Regression: the planning request must not give up before its 180-second deadline."""
import unittest
from unittest.mock import patch

import forgeos.core as core


class PlanningTimeoutTests(unittest.TestCase):
    def test_socket_timeout_matches_the_response_deadline(self):
        seen = {}

        class Stop(Exception):
            pass

        def fake_urlopen(request, timeout):
            seen["timeout"] = timeout
            raise Stop()

        with patch.object(core, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(Stop):
                core.Ollama().plan("Add a unit test for add().")
        self.assertEqual(seen["timeout"], 180)


if __name__ == "__main__":
    unittest.main()
