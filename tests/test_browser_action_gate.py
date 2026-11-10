"""Browser action approval/recovery tests; zero browser or network access."""
from __future__ import annotations

import sqlite3
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from forgeos.browser_action_gate import (
    approve_scroll, propose_scroll, run_approved_scroll, scroll_status,
)
from forgeos.browser_observation import Observation
from forgeos.core import Store


class BrowserActionGateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        repo = self.path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        self.db_path = self.path / "tasks.sqlite3"
        self.store = Store(self.db_path)
        self.addCleanup(self.store.close)
        self.task = self.store.add(str(repo), "Approved browsing test on public site")
        self.origin = "https://example.com"
        self.key = "scroll-step-001"

    def proposed(self, **kwargs):
        return propose_scroll(self.store, self.task, self.key,
                              self.origin, "down", **kwargs)

    def approved(self):
        self.proposed()
        approve_scroll(self.store, self.task, self.key, self.origin, "down",
                       human_confirmed=True)

    def test_lazy_schema_not_created_for_normal_task(self):
        self.assertIsNone(scroll_status(self.store, self.task, self.key))
        self.assertFalse(self.store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='browser_scroll_intents'"
        ).fetchone())
        self.assertEqual(self.store.recover(), 0)
        self.assertIsNone(scroll_status(self.store, self.task, self.key))

    def test_immutable_proposal_and_no_raw_url_stored(self):
        self.proposed()
        self.assertEqual(scroll_status(self.store, self.task, self.key), "proposed")
        with self.assertRaises(sqlite3.IntegrityError):
            self.proposed()
        with self.assertRaises(sqlite3.IntegrityError):
            propose_scroll(self.store, self.task, self.key, self.origin, "up")
        row = self.store.db.execute(
            "SELECT * FROM browser_scroll_intents").fetchone()
        self.assertEqual(len(row["intent_hash"]), 64)
        self.assertNotIn("example.com", str(dict(row)))
        self.assertNotIn("down", str(dict(row)))
        self.assertEqual(self.store.get(self.task)["status"], "queued")

    def test_operator_confirmation_is_required(self):
        self.proposed()
        with self.assertRaisesRegex(ValueError, "human confirmation"):
            approve_scroll(self.store, self.task, self.key, self.origin, "down")
        self.assertEqual(scroll_status(self.store, self.task, self.key), "proposed")
        approve_scroll(self.store, self.task, self.key, self.origin, "down",
                       human_confirmed=True)
        self.assertEqual(scroll_status(self.store, self.task, self.key), "approved")
        with self.assertRaisesRegex(ValueError, "already decided"):
            approve_scroll(self.store, self.task, self.key, self.origin, "down",
                           human_confirmed=True)

    def test_mismatch_does_not_approve_or_execute(self):
        self.proposed()
        with self.assertRaises(ValueError):
            approve_scroll(self.store, self.task, self.key, self.origin, "up",
                           human_confirmed=True)
        with self.assertRaises(ValueError):
            approve_scroll(self.store, self.task, self.key, "https://other.example",
                           "down", human_confirmed=True)
        fn = Mock(return_value=Observation("Safe", 1, False))
        with self.assertRaises(ValueError):
            run_approved_scroll(self.store, self.task, self.key, self.origin, "down",
                                observer=fn)
        fn.assert_not_called()
        self.assertEqual(scroll_status(self.store, self.task, self.key), "proposed")

    def test_single_execution_checkpoint_and_no_duplicate(self):
        self.approved()
        observer = Mock(return_value=Observation("Controls", 1, False))
        first = run_approved_scroll(self.store, self.task, self.key, self.origin,
                                    "down", observer=observer)
        self.assertEqual(first.text, "Controls")
        observer.assert_called_once_with(
            permitted_origin=self.origin, scroll_direction="down",
            approve_scroll=True, changes_only=False)
        self.assertEqual(scroll_status(self.store, self.task, self.key), "succeeded")
        with self.assertRaisesRegex(ValueError, "already consumed"):
            run_approved_scroll(self.store, self.task, self.key, self.origin,
                                "down", observer=observer)
        observer.assert_called_once()
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM task_leases").fetchone()[0], 0)

    def test_two_connections_consume_at_most_once(self):
        self.approved()
        second = Store(self.db_path)
        self.addCleanup(second.close)
        browser = Mock(return_value=Observation("Controls", 1, False))

        def on_call(**kwargs):
            with self.assertRaises(ValueError):
                run_approved_scroll(second, self.task, self.key, self.origin,
                                    "down", observer=Mock())
            return Observation("Controls", 1, False)

        browser.side_effect = on_call
        run_approved_scroll(self.store, self.task, self.key, self.origin,
                            "down", observer=browser)
        browser.assert_called_once()
        self.assertEqual(scroll_status(second, self.task, self.key), "succeeded")

    def test_expiry_fails_closed_before_browser_side_effect(self):
        now = time.time()
        with patch("forgeos.browser_action_gate.time.time", return_value=now):
            self.proposed(ttl_seconds=30)
            approve_scroll(self.store, self.task, self.key, self.origin, "down",
                           human_confirmed=True)
        observer = Mock()
        with patch("forgeos.browser_action_gate.time.time", return_value=now + 31):
            with self.assertRaises(ValueError):
                run_approved_scroll(self.store, self.task, self.key, self.origin,
                                    "down", observer=observer)
        observer.assert_not_called()
        self.assertEqual(scroll_status(self.store, self.task, self.key), "approved")

    def test_lease_owned_by_other_worker_refuses_action(self):
        self.approved()
        self.assertTrue(self.store.acquire_lease(self.task, "another-worker"))
        observer = Mock()
        with self.assertRaisesRegex(ValueError, "lease"):
            run_approved_scroll(self.store, self.task, self.key, self.origin,
                                "down", observer=observer)
        observer.assert_not_called()
        self.assertEqual(scroll_status(self.store, self.task, self.key), "approved")
        self.assertTrue(self.store.release_lease(self.task, "another-worker"))

    def test_exception_yields_uncertain_with_no_auto_retry(self):
        self.approved()
        browser = Mock(side_effect=RuntimeError("PRIVATE_PAGE_URL_AND_TOKENS"))
        with self.assertRaises(RuntimeError) as error:
            run_approved_scroll(self.store, self.task, self.key, self.origin,
                                "down", observer=browser)
        self.assertNotIn("PRIVATE_PAGE_URL_AND_TOKENS", str(error.exception))
        self.assertEqual(scroll_status(self.store, self.task, self.key), "uncertain")
        with self.assertRaises(ValueError):
            run_approved_scroll(self.store, self.task, self.key, self.origin,
                                "down", observer=browser)
        browser.assert_called_once()
        row = self.store.db.execute(
            "SELECT * FROM browser_scroll_intents").fetchone()
        self.assertNotIn("PRIVATE_PAGE_URL", str(dict(row)))

    def test_simulated_process_death_recovered_as_uncertain(self):
        self.approved()
        browser = Mock(side_effect=KeyboardInterrupt("abrupt exit"))
        with self.assertRaises(KeyboardInterrupt):
            run_approved_scroll(self.store, self.task, self.key, self.origin,
                                "down", observer=browser)
        self.assertEqual(scroll_status(self.store, self.task, self.key), "attempted")
        self.assertEqual(self.store.recover(), 0)
        self.assertEqual(scroll_status(self.store, self.task, self.key), "uncertain")
        with self.assertRaises(ValueError):
            run_approved_scroll(self.store, self.task, self.key, self.origin,
                                "down", observer=Mock())

    def test_live_lease_prevents_recovery_of_active_attempt(self):
        self.proposed()
        self.assertTrue(self.store.acquire_lease(self.task, "ongoing-task"))
        with self.store.db:
            self.store.db.execute(
                "UPDATE browser_scroll_intents SET status='attempted' WHERE task_id=?",
                (self.task,),
            )
        self.assertEqual(self.store.recover(), 0)
        self.assertEqual(scroll_status(self.store, self.task, self.key), "attempted")
        self.store.release_lease(self.task, "ongoing-task")
        self.store.recover()
        self.assertEqual(scroll_status(self.store, self.task, self.key), "uncertain")

    def test_invalid_payloads_do_not_create_proposals(self):
        bad = (
            (True, self.key, self.origin, "down"),
            (self.task, "../escape", self.origin, "down"),
            (self.task, "", self.origin, "down"),
            (self.task, self.key, "http://example.com", "down"),
            (self.task, self.key, "https://example.com/path", "down"),
            (self.task, self.key, "https://localhost", "down"),
            (self.task, self.key, self.origin, "submit"),
        )
        for task_id, key, origin, direction in bad:
            with self.subTest(key=key, direction=direction), self.assertRaises(ValueError):
                propose_scroll(self.store, task_id, key, origin, direction)
        with self.assertRaises(ValueError):
            self.proposed(ttl_seconds=True)
        self.assertIsNone(scroll_status(self.store, self.task, self.key))

    def test_terminal_task_refuses_new_proposal_and_dispatch(self):
        self.store.update(self.task, "passed")
        with self.assertRaises(ValueError):
            self.proposed()
        self.store.update(self.task, "queued")
        self.approved()
        self.store.update(self.task, "testing")
        browser = Mock()
        with self.assertRaises(ValueError):
            run_approved_scroll(self.store, self.task, self.key, self.origin,
                                "down", observer=browser)
        browser.assert_not_called()
        self.assertEqual(scroll_status(self.store, self.task, self.key), "approved")

    def test_unexpected_observation_is_uncertain_and_never_retry(self):
        self.approved()
        browser = Mock(return_value="invalid")
        with self.assertRaises(RuntimeError):
            run_approved_scroll(self.store, self.task, self.key, self.origin,
                                "down", observer=browser)
        self.assertEqual(scroll_status(self.store, self.task, self.key), "uncertain")
        browser.assert_called_once()


if __name__ == "__main__":
    unittest.main()
