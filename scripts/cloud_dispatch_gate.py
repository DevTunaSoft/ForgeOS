"""Fail-closed policy for future cloud coding dispatch; does not dispatch agents."""
from __future__ import annotations

from dataclasses import dataclass

READY_LABEL = "forgeos-cloud-ready"
MAX_ACTIVE_AGENT_PRS = 1


@dataclass(frozen=True)
class DispatchDecision:
    allowed: bool
    reason: str
    issue_number: int | None = None


def evaluate_dispatch(
    issue: dict,
    active_prs: list[dict],
    *,
    cloud_agent_supported: bool = False,
    user_token_configured: bool = False,
    quota_verified: bool = False,
    zero_extra_cost_confirmed: bool = False,
    active_agent_sessions: int | None = None,
) -> DispatchDecision:
    """Only approve one explicitly opted-in issue with independently verified limits.

    This policy intentionally never calls GitHub or starts a coding agent.
    Unknown quota, ownership, session count or cost must deny dispatch.
    """
    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return DispatchDecision(False, "invalid-issue")
    if issue.get("state") != "open" or "pull_request" in issue:
        return DispatchDecision(False, "not-open-issue", number)
    labels = issue.get("labels")
    if not isinstance(labels, list) or READY_LABEL not in {
        x.get("name") for x in labels if isinstance(x, dict)
    }:
        return DispatchDecision(False, "not-opted-in", number)
    if issue.get("assignees"):
        return DispatchDecision(False, "already-assigned", number)
    if not cloud_agent_supported:
        return DispatchDecision(False, "cloud-agent-unverified", number)
    if not user_token_configured:
        return DispatchDecision(False, "user-token-unavailable", number)
    if not quota_verified or not zero_extra_cost_confirmed:
        return DispatchDecision(False, "quota-or-cost-unverified", number)
    if active_agent_sessions is None or active_agent_sessions < 0:
        return DispatchDecision(False, "active-session-count-unknown", number)
    if active_agent_sessions >= MAX_ACTIVE_AGENT_PRS:
        return DispatchDecision(False, "agent-capacity-exhausted", number)
    if not isinstance(active_prs, list):
        return DispatchDecision(False, "pr-inventory-unknown", number)
    # Issue-to-PR linkage must be proven, not guessed from a title or body.
    if not issue.get("pr_linkage_checked"):
        return DispatchDecision(False, "pr-linkage-unverified", number)
    if any(number in p.get("linked_issue_numbers", []) for p in active_prs):
        return DispatchDecision(False, "existing-pr-for-issue", number)
    return DispatchDecision(True, "eligible-for-explicit-dispatch", number)
