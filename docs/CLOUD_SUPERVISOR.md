# ForgeOS Cloud Supervisor — phase 1

This is a **read-only cloud task-queue audit**, not autonomous AI coding. A GitHub Actions workflow runs every six hours (or via manual dispatch) and uploads a seven-day JSON report of open pull requests and issues explicitly labeled `forgeos-cloud-ready`. It does not create issues, assign agents, modify code, merge PRs, or use paid APIs.

## Cost and repository visibility

Standard GitHub-hosted Actions runners on a public repository generally do not consume paid Actions minutes, subject to GitHub's current policies. **Changing the repository to private changes the billing and allowance rules.** Before making ForgeOS private, review Actions usage limits and set spending controls. Disable this scheduled workflow if free private-repository capacity is insufficient. Never assume private runs are unlimited or free.

## Operation

Workflow: `.github/workflows/cloud-supervisor.yml`.
Script: `scripts/cloud_supervisor.py`.
GitHub Actions -> ForgeOS Cloud Supervisor (read-only) -> Run workflow. Inspect the `cloud-supervisor-report` artifact.

An issue becomes a candidate only with the exact label `forgeos-cloud-ready`; missing labels yield an empty candidate list. The report explicitly requires manual overlap/ownership review because PR linkage cannot be safely inferred from issue titles. GitHub API calls are bounded to five pages and 15-second request timeouts. The workflow uses read-only token permissions, a five-minute job timeout, and concurrency serialization.

## Phase 2 — not implemented

Before enabling unattended coding: verify a supported cloud coding agent and quota accounting; implement explicit task-to-PR ownership, per-run cost/request budgets, finite retries, audit logs, protected-branch safety, draft-only PR creation, and stop-on-uncertainty behavior. A personal Copilot subscription is not an API token for unattended Actions. Do not store browser cookies or personal session credentials as Actions secrets.

## Verification

This PR introduces a workflow and script for review. GitHub-hosted execution and AI dispatch are **not yet verified**. No claim of continuous unattended AI coding is made.
