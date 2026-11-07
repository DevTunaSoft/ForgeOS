# ForgeOS cumulative engineering handoff — v0.1.0

## Current state
Initial foundation only. Python standard-library runtime, Tkinter desktop prototype, SQLite task store and recovery, Ollama planning, explicit allowlisted tests, CLI. This is **not** an autonomous software builder or production release.

## Architecture
- src/forgeos/core.py: task persistence, local Ollama planning and test runner.
- src/forgeos/cli.py: entry point and task workflows.
- src/forgeos/gui.py: Tk GUI, background worker and results display.
- tests/test_core.py: persistence, recovery, test safety and failure behavior.
- docs/REQUIREMENTS.md and docs/ROADMAP.md: cumulative mission and phases.
- .github/workflows/ci.yml: proposed Windows and Linux test execution.

## Verified local evidence before GitHub import
10 unit tests passed and Python syntax compilation passed under Linux. GitHub-hosted Windows validation is pending workflow execution. No true Windows GUI smoke test, local Ollama model run or EXE/APK build has yet been performed.

## Known limitations
- Worker thread reads Tk variables, requiring UI-thread handoff before native Windows validation.
- Host tests execute arbitrary trusted-project code without sandboxing.
- SQLite records are not encrypted, and interrupted operations restart from queued.
- No event journal, backup/restore or granular progress checkpoints.
- Ollama requests cannot yet be cancelled; no autonomous fix loop.
- No GitHub integration from the application itself.
- GitHub repository visibility may be public; never upload credentials or private data.

## Run and recover
On Windows: install Python 3.10+ and Git; from repository root run 'py -m pip install -e .' then 'py -m forgeos gui'. For tests, set PYTHONPATH=src and run 'py -m unittest discover -s tests -v'. Ollama is optional for planning. DB is ~/.forgeos/tasks.sqlite3. Use 'py -m forgeos recover' to requeue interrupted tasks. Safeguard SQLite with its backup API.

## Next autonomous work
1. Observe CI results; fix every failure and rerun.
2. Eliminate Tk cross-thread access, introduce GUI-level smoke tests and cancellation.
3. Introduce structured event journal, integrity checks and transactional backup.
4. Add safe Git worktree operations and bounded patch application.
5. Add independent test-and-repair loop with auditable evidence.
6. Build and verify a real Windows installer and update user-facing documentation.
7. Keep cumulative complete-source deliveries and comprehensive handoffs.

## Hardening round — 2026-11-07

- Corrected Tkinter cross-thread operations: GUI state captured before worker creation; worker results sent through a Queue; main GUI thread polls and updates widgets. Root.after is no longer invoked by workers.
- Added seeded malformed/Unicode task fuzzing, 75-task interruption/recovery stress, invalid test-preset fuzzing, and injected executable-missing/timeout tests.
- Pending: observe Windows/Linux CI for these changes, inspect failures, and perform real Windows GUI interaction tests. No production-ready claim.
- Known: closing during a running job remains blocked until completion; Ollama planning can take up to 180 seconds. No cancellation, sandbox or worker subprocess isolation. Test runner executes trusted repository code on the host. Full autonomous code-editing pipeline remains future work.

## Milestone: isolated Git workspaces (2026-11-07)

- Added src/forgeos/workspace.py with Git-root validation, task-derived branch names, out-of-tree worktree creation, tracked diff inspection, and refusal to remove dirty/untracked workspaces.
- Added tests/test_workspace.py: isolated edits, safe cleanup, duplicate task branches, nested/existing destination rejection, invalid IDs, untracked data protection and non-repository rejection.
- Git must be installed for worktree operations. Tests skip if Git is absent; this skip must not be confused with verified worktree behavior.
- This is NOT a sandbox: worktree code can still access host resources. No AI patch application, autonomous repair, model orchestration, or Windows GUI smoke validation yet.
- Next: verify CI, review platform behavior, add safe patch validation and execution isolation, then connect workspace orchestration to task engine.

## Autonomous task-engine prototype — 2026-11-07

- New src/forgeos/engine.py: strict JSON edit manifests, bounded files/size, traversal and symlink rejection, local Ollama generation, Git worktree editing, allowlisted test presets, up to 5 repair retries, persisted task status and retained worktree.
- CLI: `python -m forgeos run TASK_ID --workspace-parent PATH --allow-host-tests --max-repairs 2` (requires local Ollama and installed Git). No automatic commit, merge or deployment.
- New tests/test_engine.py: malicious paths, symlink escape, atomic validation, duplicate/oversized edits, successful write, end-to-end failing-first then repaired test, exhaustion, invalid model output and opt-in gating.
- CRITICAL: host tests are NOT sandboxed. Never enable --allow-host-tests for untrusted repositories or model-generated code on a sensitive host. Worktrees isolate source changes, not process privileges or network/filesystem access.
- Known: writes are not transactional across disk failure; repair model may replace existing files; Git branch names are one-shot per task; recovery currently requeues interrupted tasks but does not automatically attach to retained worktrees. No GUI run button, no autonomous commits, no actual Ollama runtime test.
- Follow-up: CI Windows/Linux validation, then process sandbox, durable execution journal and resumable workspaces before unattended operation.

## Desktop dashboard milestone — 2026-11-07

- Replaced minimal Tkinter screen with a two-tab Development / Activity & diagnostics dashboard. Added project picker, natural-language request, local model and test preset, repair retries, worktree folder, status counts and task queue.
- GUI now invokes execute_task() in a worker thread and displays JSON results; Tk operations remain on main thread through Queue polling. The user must explicitly enable host test execution because generated code is not sandboxed.
- Existing planning and tests-only actions retained; interrupted tasks are requeued without silently running them. This is not a fully unattended queue and does not provide real pause/resume/cancel yet.
- Added headless GUI contract tests. Actual interactive Windows GUI smoke testing, screen reader usability, and display scaling remain unverified.
- IMPORTANT: tests-only mode executes project code on host as before. Neither test execution path is isolated from host resources. Do not run untrusted projects on a sensitive machine.

## Windows EXE packaging milestone

- packaging/launcher.py provides GUI entry point and headless frozen --smoke mode.
- .github/workflows/windows-exe.yml runs Windows tests, PyInstaller one-file GUI build, EXE existence/size check, packaged import smoke test, and uploads a 14-day artifact.
- docs/WINDOWS_BUILD.md explains artifact retrieval, runtime prerequisites, local build, and limitations.
- Next: verify Windows packaging CI, run real Windows GUI interaction tests, and test a real Ollama-driven edit/repair loop. No claim of fully portable or production-ready app.

## Journaled edits and interrupted-task recovery (2026-11-07)

- Multi-file edits stage new files and backups in workspace/.forgeos/pending-edit with SHA-256 checksums and a journal before replacement.
- Python-level write failures attempt rollback. After a hard crash, run: python -m forgeos recover-edits PATH_TO_TASK_WORKSPACE. Recovery refuses to overwrite conflicting external edits.
- Startup recovery blocks interrupted editing/testing tasks for inspection rather than silently requeueing them. Planning tasks may requeue. Existing worktrees are preserved.
- This is not filesystem-wide atomicity, not automatic job continuation, and not a sandbox. Real Windows power-loss and interactive acceptance tests remain outstanding.

## PR #17 — conservative automatic recovery orchestration (2026-11-07)

- Added `src/forgeos/recovery.py` with `resume_safe`: first marks interrupted execution tasks blocked, then inspects only blocked tasks with a persisted `test_pending` checkpoint and workspace location matching the task-derived worktree path.
- Added CLI `python -m forgeos resume-safe --workspace-parent PATH --allow-host-tests` with optional test preset and repair budget.
- Each eligible task is passed through the existing guarded `execute_task(..., resume=True)` path, which validates Git worktree registration, branch, transaction journal and edited-file SHA-256 hashes. Per-task exceptions are recorded; other tasks continue.
- Fail-closed: without explicit `--allow-host-tests`, orchestration refuses execution before modifying the task database. This is **not sandboxed**; do not use on untrusted projects.
- Added `tests/test_recovery.py`: authorization, filtering, malformed checkpoints, task-by-task error isolation.
- Limitations: this is **explicitly invoked** recovery, not background startup execution. AI-generation and repair-pending phases are not resumable. It does not run untrusted tests in a restricted Windows environment. No actual physical Windows GUI/Ollama acceptance yet.
- Next: verify Linux/Windows CI and EXE packaging; resolve any failures; implement persisted repair-pending checkpoints and workspace-wide integrity checks; add Windows-native restricted execution before enabling unattended host tests; update GUI and full Windows acceptance.

## PR #18 — repair-pending checkpoint recovery (2026-11-07)

- Fixed a recovery data-loss gap: after a failed test, the transition back to `editing` previously cleared `tasks.result`, so interruption during AI repair generation could erase the last safe recovery context.
- The engine now persists `repair_pending` checkpoints containing workspace, branch, prior edited paths, SHA-256 hashes, next repair attempt number and bounded prior test output.
- Resume now supports both `test_pending` and `repair_pending`. Repair-pending resume reconstructs the repair prompt from the stored failure and generates the next edit without rerunning the already-failed test.
- Integrity validation remains fail-closed: workspace, branch, edited-path list, hashes and saved attempt bounds must validate before continuing.
- The recovery orchestrator now treats valid repair-pending checkpoints as eligible.
- Added interruption injection covering a crash/KeyboardInterrupt during repair generation and successful continuation at attempt 2, plus orchestrator coverage for repair-pending eligibility.
- Still not a security sandbox. Host test execution remains explicitly gated. Windows-native restricted execution and physical Windows/Ollama acceptance remain outstanding.

## PR #19 — persisted execution-contract recovery (2026-11-07)

- Resumable checkpoints now bind the task to the original execution contract: test preset, maximum repair budget, and local model identifier.
- Direct engine resume fails closed if the caller supplies different execution settings from the saved checkpoint.
- `resume-safe` now validates the saved contract and replays each task using its own preset, repair budget and Ollama model instead of applying one global default to every interrupted task.
- Optional `resume-safe --preset` and `--max-repairs` arguments are now safety overrides: if supplied, they must match the checkpoint rather than silently changing behavior.
- Legacy resumable checkpoints that predate contract persistence are deliberately skipped by automatic recovery because their original execution settings cannot be proven.
- Added tests for per-task contract replay, mismatched overrides, missing contracts and direct-engine rejection of changed settings.
- Host execution remains unsandboxed and explicitly gated; Windows-native restricted execution remains the next major security milestone.
