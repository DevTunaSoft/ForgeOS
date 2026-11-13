# ForgeOS low-cost unattended GitHub development: activation gate

Status: DESIGN / NOT ACTIVATED (2026-11-12). This file documents a proposed *external GitHub automation*, not a shipped ForgeOS feature.

## Verified repository baseline
- ForgeOS is a **public**, personal-account repository with default branch `main`.
- `.github/workflows/ci.yml` already runs Linux/Windows Python 3.10/3.12 compile, unittest and memory benchmark for non-draft PRs and main pushes.
- `.github/workflows/windows-exe.yml` already builds a Windows EXE, verifies embedded source identity, frozen GUI startup and AppContainer smoke.
- Existing ForgeOS internal `engine.py`, `core.py`, `recovery.py`, `transactions.py` and related components handle local prototype edit/test/recovery. Do not duplicate them for this external scheduler.
- Open issues and PRs contain active work and dependencies. Check live status before selecting any task.

## Critical capability gate
GitHub Copilot **Automations** currently require private/internal repositories; they are not available for this public repository. Do not imply that purchasing Copilot Pro will enable scheduled Automations here. Source: https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/about-automations

GitHub Agentic Workflows (`gh aw`) are a separate possible route: Markdown workflows compiled to GitHub Actions. For personal repositories, the Copilot engine needs a fine-grained personal token with Copilot Requests permission as a repository secret; do not put tokens in code or issue bodies. See https://docs.github.com/en/actions/tutorials/develop-agentic-workflows-in-github-actions . Any AI usage may consume credits; never assume unlimited included hours.

## Proposed phases (all pending unless explicitly verified)
1. **Free dry-run:** Build a deterministic, read-only selector that lists unblocked, appropriately labeled GitHub Issues. Never launch an agent or create a PR from the dry run. Test dependency, duplicate PR, issue state, concurrency, budget and missing-permission cases.
2. **Manual one-task trial:** Enable a single cloud coding assignment only after confirming plan eligibility, permissions and spending cap. Measure credits, completion quality, CI and human interventions.
3. **Bounded scheduled queue:** After the trial, optionally use a compiled GitHub Agentic Workflow with least-privilege credentials, explicit concurrency lock, a maximum one task per scheduled run, a stop flag and a strict credit budget. Treat skipped/blocked work as recorded outcomes, not successful completion.
4. **Verification:** Existing CI remains authoritative. Attempt bounded repairs; never rewrite test expectations to mask failures. Respect draft PR gating. Review and merge only under configured human/branch protections.
5. **Continuity:** Update the existing issue, roadmap and cumulative handoff with actual commit IDs, test evidence and precise next step. Avoid concurrent agents touching the same issue/PR.

## Cost/approval gate
- No subscription, token, repository visibility change, secret, schedule or paid workflow has been enabled by this document.
- Before spending: inspect GitHub plan, free Actions minutes, current AI credits, budget settings, repo settings, and whether public visibility is required.
- Default additional AI spend = $0. Never enable pay-as-you-go without explicit approval.
- Keep repository public unless the owner explicitly requests otherwise.
- The system must fail closed when token missing, budget unknown/exhausted, CI failing, task selection ambiguous, or an approval is required.

## Success criteria
A real trial must show: task assigned without handholding, bounded code/test/repair, reproducible Windows/Linux CI evidence, complete PR handoff, no unauthorized merges, and measurable credits consumed. Only then decide whether the $10 plan is worthwhile.

## Immediate next action
Implement the no-AI, no-secret dry-run selector and tests on an isolated PR, using the existing repository's test conventions. Do not schedule or activate an AI workflow before verifying the supported authentication and spending controls.
