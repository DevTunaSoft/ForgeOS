import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from forgeos import appcontainer


class RecursiveGrantLinkSafetyTests(unittest.TestCase):
    """Recursive icacls grants pass /L, so a grant stops at a link. Links are refused before any grant."""

    def test_read_execute_grant_passes_link_flag(self):
        with TemporaryDirectory() as tmp, patch.object(appcontainer, "_icacls") as icacls:
            appcontainer._grant_path_read_execute(Path(tmp), "S-1-15-2-1")
        args = icacls.call_args.args
        self.assertIn("/T", args)
        self.assertIn("/L", args)

    def test_revoke_of_a_directory_passes_link_flag(self):
        with TemporaryDirectory() as tmp, patch.object(appcontainer, "_icacls") as icacls:
            appcontainer._revoke_path_grant(Path(tmp), "S-1-15-2-1")
        args = icacls.call_args.args
        self.assertIn("/T", args)
        self.assertIn("/L", args)

    def test_probe_grant_passes_link_flag_on_the_recursive_call(self):
        with TemporaryDirectory() as tmp, \
                patch.object(appcontainer, "_icacls") as icacls, \
                patch.object(appcontainer, "wc", create=True):
            appcontainer._grant_probe_access(Path(tmp), Path(tmp), "S-1-15-2-1")
        recursive = [call.args for call in icacls.call_args_list if "/T" in call.args]
        self.assertEqual(len(recursive), 1)
        self.assertIn("/L", recursive[0])


if __name__ == "__main__":
    unittest.main()
