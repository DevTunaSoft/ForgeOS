# ForgeOS engineering roadmap

**Status as of 2026-11-07: active prototype.** This document distinguishes **implemented on `main`**, **in development**, and **future goals**. It is not a claim that the entire product is production-ready. Each milestone needs source changes, regression evidence, Windows frozen build evidence where relevant, and real end-user testing before release claims.

## Milestone overview

| Workstream | Status on `main` | Outstanding exit criteria |
| --- | --- | --- |
| Single GUI and persistent task queue | **Implemented prototype:** Tkinter UI, SQLite tasks/leases, paginated history, activity views | Beginner usability, accessibility and real machine soak |
| Local AI planning/editing | **Implemented prototype:** local Ollama planning, JSON file replacements, bounded source metadata | Deeper planning and whole-app creation, robust real-model acceptance |
| Safe Git workspaces | **Implemented prototype:** per-task worktrees, path/budget validation, transactional edits | Broader recovery fault injection, demonstrated end-to-end rollback on Windows |
| Resumable autonomous cycle | **Implemented prototype:** limited test/repair checkpoints and `resume-safe` | Resume additional phases safely, long-run crash/restart acceptance |
| Execution isolation | **Implemented prototype:** packaged Windows `unittest` AppContainer path; fail closed on unsupported presets | Secure coverage for additional frameworks, adversarial Windows acceptance |
| Python `pytest` packaged isolation | **Implemented experimental (PR #43):** rebased successor to [PR #29](https://github.com/DevTunaSoft/ForgeOS/pull/29), avoids Windows NUL logging denial | Hosted frozen Windows pytest AppContainer smoke and regression suite passed; still needs actual end-user Windows acceptance |
| Diagnostics and repair learning | **Implemented experimental:** SHA-256 evidence, bounded summaries, aggregate learning | Encrypted retention, broader structured parsers, verified learning decisions |
| RAM-aware performance | **Implemented experimental:** pagination, bounded logs, memory-aware selection, on-demand RSS | Real Windows 4/8 GiB physical/VM tests, full process tree/model memory |
| Windows executable | **Implemented prototype:** PyInstaller packaging, hosted GUI lifecycle/pagination and AppContainer smoke | Real Windows install, local model, task lifecycle and safe distribution |
| GitHub workflows inside app | **Planned** | Authentication, import, PR/branch/review, safe controlled publication |
| Android APK and web builders | **Planned** | Reproducible toolchains, sandboxing, build artifacts and user acceptance |
| Multi-agent workflow orchestration | **Planned** | Tested model roles, routing, budgets, no mandatory cloud cost |
| General testing and autonomous QA | **Partial prototype** | Multi-framework secure runtimes, fuzz/fault-injection cross-module and UI/system tests |
| Backups, restore, handoff and installer | **Partial / planned** | Encrypted versioned backups, verified restore, complete artifact export, signed installer |
| Production readiness | **Not reached** | End-to-end acceptance matrix with release evidence and no blocking defects |

## Near-term engineering priorities

1. **Secure broader test execution without Docker.** With hosted pytest + unittest AppContainer smoke now passing, extend the same fail-closed guarantees to other selected frameworks and real user Windows machines.
2. **Finish transactional recovery and interruption handling.** Inject crashes, partial writes, invalid journals, stale leases, process kills, malformed checkpoints, and resume across real Windows restarts.
3. **Strengthen memory efficiency and visibility.** Avoid unbounded subprocess/output/context allocations; capture process-tree usage under workloads representative of 4/8/16/32 GiB Windows systems, carefully distinguishing model and application RAM.
4. **Validate the complete Windows usage path.** Launch installed EXE, connect to local Ollama, run generated edits, test/repair, stop/restart, inspect worktree and archive evidence on a real desktop.
5. **Improve documentation along with development.** Keep user manual, architecture, README feature matrix, this roadmap, security warnings, Windows build instructions and engineering handoff in sync via the [documentation policy](DOCUMENTATION_POLICY.md) and PR checks.

## Medium-term priorities

- Extend secure runtime presets and deterministic dependency management without executing arbitrary generated commands on the host.
- Improve local model selection and multi-role orchestration without duplicating Ollama clients or requiring paid services.
- Add actual build adapters for Windows EXE, Android APK and web projects with reproducible artifacts and platform-specific QA.
- Integrate GitHub repository management, PR reviews, CI status and **explicit approval** for external/publishing operations.
- Establish encrypted versioned backup and tested restore with tamper detection, retention and full source/handoff exports.
- Expand fuzz, property-based, randomized, security, integration, stress and recovery acceptance with documented regressions.

## Long-term acceptance gates

| Gate | Evidence required |
| --- | --- |
| Functionality | Real end-to-end natural-language request → code changes → builds/tests → reviewed usable artifact |
| Reliability | Multiple rounds of distinct fault injection, root-cause fixes and full regression passes |
| Safety | Tested isolation/permissions, no silent host or network fallback, explicit consequential-action approval |
| Recovery | Crash-safe transactions, checkpoint hashes, bounded retries, verified restoration without overwriting user work |
| Performance | Repeatable RAM/CPU/latency measurements on actual target machines; distinction between ForgeOS and Ollama/build subprocesses |
| Packaging | Clean Windows install/uninstall, EXE behavior, release and supply-chain review |
| Usability | Click-by-click manual, real screenshots only, accessible understandable error messages, nontechnical recovery |
| Documentation | Current feature states, tests and known limits in README, user manual, architecture, roadmap and handoff |

## The 52 RTK-inspired improvements

All outcomes and deduplication concerns are tracked separately in [RTK_ENHANCEMENTS.md](RTK_ENHANCEMENTS.md) and [ENHANCEMENT_DEDUP_AUDIT.md](ENHANCEMENT_DEDUP_AUDIT.md). A checkbox in an engineering tracker may mean **prototype integrated**, not mature or independently accepted. Keep the canonical subsystem where an enhancement overlaps existing ForgeOS code.

**Release rule:** Do not classify any milestone as complete or production-ready without evidence beyond existence of code or a green badge. Hosted GitHub Actions checks alone are not proof of real Windows end-to-end behavior.
