import unittest
from pathlib import Path

from forgeos.windows_containment import _owner_grant_arguments, _parse_whoami_sid


class WorkspaceGrantTests(unittest.TestCase):
    def test_parses_account_sid_from_whoami_csv(self):
        output = '"EXAMPLE-PC\\account","S-1-5-21-1111-2222-3333-1001"\n'
        self.assertEqual(_parse_whoami_sid(output), "S-1-5-21-1111-2222-3333-1001")

    def test_rejects_output_without_a_sid(self):
        with self.assertRaises(OSError):
            _parse_whoami_sid("not a csv row")

    def test_grant_covers_the_tree_and_only_the_account(self):
        sid = "S-1-5-21-1111-2222-3333-1001"
        args = _owner_grant_arguments(Path("C:/work/task"), sid)
        self.assertEqual(args[0], "icacls.exe")
        self.assertIn("/grant:r", args)
        self.assertIn(f"*{sid}:(OI)(CI)F", args)
        self.assertIn("/T", args)
        self.assertNotIn("Everyone", " ".join(args))


if __name__ == "__main__":
    unittest.main()
