"""Offline tests for the cloud supervisor's fail-closed queue selection."""
import unittest

from scripts.cloud_supervisor import build_report


class CloudSupervisorTests(unittest.TestCase):
    def test_only_explicitly_labeled_issues_are_candidates(self):
        issues = [
            {"number": 2, "title": "Ready", "labels": [{"name": "forgeos-cloud-ready"}]},
            {"number": 3, "title": "Not ready", "labels": []},
            {"number": 4, "title": "PR disguised as issue",
             "pull_request": {"url": "https://example.invalid"},
             "labels": [{"name": "forgeos-cloud-ready"}]},
        ]
        result = build_report(issues, [{"number": 8, "title": "Existing PR", "draft": True}])
        self.assertEqual(result["ready_issue_candidates"], [{"number": 2, "title": "Ready"}])
        self.assertEqual(result["active_pull_requests"][0]["number"], 8)
        self.assertTrue(result["requires_manual_ownership_review"])
        self.assertFalse(result["ai_dispatch_enabled"])
        self.assertFalse(result["automatic_merges_enabled"])

    def test_empty_queue_stays_empty(self):
        result = build_report([], [])
        self.assertEqual(result["ready_issue_candidates"], [])
        self.assertEqual(result["active_pull_requests"], [])


if __name__ == "__main__":
    unittest.main()
