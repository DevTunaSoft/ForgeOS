"""Policy-only tests: no network, model usage, or dispatch."""
import unittest
from scripts.cloud_dispatch_gate import evaluate_dispatch


def eligible():
    return {
        "number": 144, "state": "open", "labels": [{"name": "forgeos-cloud-ready"}],
        "assignees": [], "pr_linkage_checked": True,
    }


def verified():
    return dict(cloud_agent_supported=True, user_token_configured=True,
                quota_verified=True, zero_extra_cost_confirmed=True,
                active_agent_sessions=0)


class DispatchGateTests(unittest.TestCase):
    def test_default_is_denied(self):
        self.assertEqual(evaluate_dispatch(eligible(), []).reason, "cloud-agent-unverified")

    def test_requires_explicit_label(self):
        issue = eligible()
        issue["labels"] = []
        self.assertEqual(evaluate_dispatch(issue, [], **verified()).reason, "not-opted-in")

    def test_requires_verified_quota_and_zero_cost(self):
        settings = verified()
        settings["zero_extra_cost_confirmed"] = False
        self.assertEqual(evaluate_dispatch(eligible(), [], **settings).reason,
                         "quota-or-cost-unverified")

    def test_unknown_active_sessions_denied(self):
        settings = verified()
        settings["active_agent_sessions"] = None
        self.assertEqual(evaluate_dispatch(eligible(), [], **settings).reason,
                         "active-session-count-unknown")

    def test_existing_pr_denied(self):
        self.assertEqual(evaluate_dispatch(
            eligible(), [{"linked_issue_numbers": [144]}], **verified()
        ).reason, "existing-pr-for-issue")

    def test_unverified_linkage_denied(self):
        issue = eligible()
        issue["pr_linkage_checked"] = False
        self.assertEqual(evaluate_dispatch(issue, [], **verified()).reason,
                         "pr-linkage-unverified")

    def test_existing_assignment_denied(self):
        issue = eligible()
        issue["assignees"] = [{"login": "some-agent"}]
        self.assertEqual(evaluate_dispatch(issue, [], **verified()).reason,
                         "already-assigned")

    def test_capacity_denied(self):
        settings = verified()
        settings["active_agent_sessions"] = 1
        self.assertEqual(evaluate_dispatch(eligible(), [], **settings).reason,
                         "agent-capacity-exhausted")

    def test_explicitly_verified_candidate_only(self):
        decision = evaluate_dispatch(eligible(), [], **verified())
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.issue_number, 144)


if __name__ == "__main__":
    unittest.main()
