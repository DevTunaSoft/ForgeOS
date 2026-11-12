# ForgeOS repository coding-agent instructions

Read README.md, docs/ROADMAP.md, docs/ARCHITECTURE.md, docs/HANDOFF.md, and relevant feature-status / user-guide files before editing. Reconcile with the live default branch, open issues, PRs and CI. Existing documentation may be stale; report conflicts rather than assuming claims are verified.

## Mission
ForgeOS is a beginner-friendly, Windows-first, local-first autonomous software engineering desktop application. Preserve free/local Ollama support, one GUI, clean modular architecture, low RAM/CPU/disk overhead, no mandatory Docker, and robust recovery, testing and diagnostics. Do not confuse this GitHub coding agent with ForgeOS's own runtime.

## Scope and implementation
- Reuse existing task engine, SQLite store, checkpoints, worktrees, transaction journal, model router, and diagnostics. Never create parallel systems without a documented need.
- Choose one small, unblocked roadmap/issue slice per assignment, with measurable acceptance criteria. Check dependencies, existing PR ownership and conflicting edits first. Never silently duplicate an open PR.
- Prefer minimal, reversible changes and existing standard-library components; audit licenses before copying external code.
- Keep work isolated on the assigned branch; do not force push, rewrite history, publish releases, deploy, change billing, or merge without required approval.
- Never execute untrusted generated code outside an approved restricted test environment. Preserve explicit user approvals for sensitive host execution, credentials, destructive actions and external services.
- No fabricated passing tests, Windows hardware results, benchmarks, releases or agent progress. A hosted CI pass is not physical Windows acceptance.

## Verification and recovery
- Run focused tests first, then relevant regression, Windows/Linux CI and packaging checks when supported. Diagnose root cause and perform bounded repairs, rerunning affected tests.
- Log exact commands, environment, pass/fail/skip, commit SHA, remaining risks and blockers. Do not loop indefinitely on repeated failures.
- Treat failed or interrupted work as resumable only when the persisted state, hashes and existing recovery contract permit it; otherwise stop safely.
- Preserve RAM efficiency, fail-closed security boundaries and user data. Do not weaken tests to make checks green.

## Handoff and task continuity
- Update authoritative roadmap, feature status, user manual and cumulative engineering handoff alongside meaningful changes; distinguish implemented, proposed and unverified.
- Each PR must summarize scope, issue/dependency, changed files, actual test evidence, resource/security implications, limitations and next unblocked step.
- At the end of an assignment, leave a precise resumption note. Do not claim that completing one assignment automatically launches the next; only an explicitly configured, authorized automation can do that.
