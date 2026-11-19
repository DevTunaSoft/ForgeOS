"""Linux-runnable checks for the secure runner staging and its grant plan.

The runner is staged beside the snapshot before any grant is made, so that the
folder a user keeps the runner in is never granted. These tests cover the pure
helpers; the Windows tests in test_secure_run_sibling_acls.py cover the real
ACLs.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from forgeos.appcontainer import (
    _remove_staged_runner, _runner_argv, _secure_grant_plan, _stage_runner)


class SecureRunnerStagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="forgeos-staging-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.downloads = self.tmp / "downloads"
        self.downloads.mkdir()
        self.runner = self.downloads / "ForgeOS.exe"
        self.runner.write_bytes(b"MZ-test-runner")
        self.stage = self.tmp / "secure-runs"
        self.stage.mkdir()
        self.root = self.stage / ".forgeos-exec-app-deadbeef"

    def test_stage_copies_the_runner_beside_the_snapshot(self):
        staged = _stage_runner(self.runner, self.stage)
        self.assertEqual(staged.parent, self.stage)
        self.assertEqual(staged.suffix, ".exe")
        self.assertEqual(staged.read_bytes(), self.runner.read_bytes())
        self.assertNotEqual(staged, self.runner)

    def test_each_stage_gets_a_new_name_and_never_overwrites(self):
        first = _stage_runner(self.runner, self.stage)
        second = _stage_runner(self.runner, self.stage)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file() and second.is_file())

    def test_grant_plan_never_reaches_the_runner_folder(self):
        staged = _stage_runner(self.runner, self.stage)
        plan = _secure_grant_plan(self.root, staged)
        self.assertEqual(plan, [
            (self.stage, "RX", False),
            (self.root, "M", True),
            (staged, "RX", False),
        ])
        self.assertNotIn(self.downloads, [path for path, _, _ in plan])

    def test_grant_plan_refuses_a_runner_outside_the_snapshot_parent(self):
        with self.assertRaises(ValueError):
            _secure_grant_plan(self.root, self.runner)

    def test_argv_points_at_the_staged_copy(self):
        staged = _stage_runner(self.runner, self.stage)
        argv = _runner_argv(
            [str(self.runner), "--sandbox-unittest"], self.runner, staged)
        self.assertEqual(argv, [str(staged), "--sandbox-unittest"])

    def test_argv_must_start_with_the_runner(self):
        staged = _stage_runner(self.runner, self.stage)
        with self.assertRaises(ValueError):
            _runner_argv(["python.exe", "-c", "pass"], self.runner, staged)
        with self.assertRaises(ValueError):
            _runner_argv([], self.runner, staged)

    def test_staged_copy_is_removed_and_removal_is_idempotent(self):
        staged = _stage_runner(self.runner, self.stage)
        _remove_staged_runner(staged)
        self.assertFalse(staged.exists())
        _remove_staged_runner(staged)
        self.assertTrue(self.runner.is_file())
