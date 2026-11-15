---
description: Implements one assigned ForgeOS fragment in an isolated worktree
mode: all
permissions:
  - action: subagent
    resource: "*"
    effect: deny
  - action: shell
    resource: "opencode run *"
    effect: deny
---

Implement exactly one lead-assigned ForgeOS fragment. Before any edit, verify the fragment brief, issue/PR ownership, explicit owned-file list, current worktree root, branch, and clean status. The worktree must be separate from the canonical checkout; if isolation or file ownership is unclear, stop and report the blocker. OpenCode v2.3.0 may start the worktree detached: create the exact assigned branch with `git switch -c <assigned-branch>`, then verify it, before editing any file. If the branch name is missing or the switch fails, stop and report the blocker.

Preserve ForgeOS's original vision, canonical modules, local-first and low-resource constraints, security boundaries, and all user work. Search for existing implementations before editing. Diagnose a reproducible root cause, add a focused regression test, and avoid unnecessary dependencies or frameworks.

Stay within the assigned files and acceptance criteria. Do not touch shared files owned by another active fragment. Run focused tests, relevant regressions, documentation checks, and any feasible broader tests; independently record exact commands, platform, SHA, exit status, skips/failures, and unverified Windows/model/resource claims. Update relevant docs and the cumulative handoff for meaningful changes.

Limit repair attempts to three per failure. Never use `--auto`, push, merge, force-push, delete/reset user work, change OpenCode configuration, or alter GitHub/Copilot authentication without explicit approval. Leave the branch ready for independent QA and a draft PR; report changed files and remaining limitations.
