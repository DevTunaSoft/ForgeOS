# ForgeOS engineering roadmap

**Read [What ForgeOS Will Do — and What It Will Not Do](PRODUCT_VISION.md) for the full product vision, shipped prototype capabilities, and explicit non-goals.** The [user manual](USER_GUIDE.md) remains the canonical click-by-click operating guide.

**Status as of 2026-11-15: active prototype.** This document distinguishes **implemented on `main`**, **in development**, and **future goals**. It is not a claim that the entire product is production-ready. Each milestone needs source changes, regression evidence, Windows frozen build evidence where relevant, and real end-user testing before release claims.

## Dedicated multi-LLM requirements and implementation plan

The [comprehensive multi-LLM roadmap](MULTI_LLM_ROADMAP.md) now tracks **all ten major work packages** (A–J): existing integration audit and compatibility; integrated CPU-first llama.cpp; Hugging Face discovery, secure downloads, installation, updates and removal; small coding-model benchmarks; specialized-role routing; resource governance; autonomous workflow/recovery; unified accessibility-first GUI; fault-injection and comprehensive QA; and documentation/handoff. It includes source ownership, concrete acceptance gates, dependencies and **P0–P3 execution sequencing**.

**Prioritization following merged PR #51 and subsequent increments:** (P0) preserve existing work and verify latest checks, (P1) manage a native local inference runtime and pinned model identity, (P2) implement full model lifecycle plus memory/fallback governance and evidence-based role routing, and (P3) validate the combined application on real Windows hardware before release. All unimplemented capabilities remain **planned**; real llama.cpp, LM Studio and downloaded GGUF execution are **not verified** by the hosted CI result.

Do not introduce second copies of `model_routing.py`, `Store`, `engine.py`, output summarization or GUI management. Use the [52-enhancement deduplication audit](ENHANCEMENT_DEDUP_AUDIT.md) to coordinate adjacent work.

## Milestone overview

| Workstream | Status on `main` | Outstanding exit criteria |
| --- | --- | --- |
| Single GUI and persistent task queue | **Implemented prototype:** Tkinter UI, SQLite tasks/leases, indexed atomic prevention of duplicate active tasks, paginated history, activity views and shared GUI/CLI plan/test actions | Beginner usability, accessibility and real machine soak |
| Local AI planning/editing | **Implemented prototype:** local Ollama planning, JSON file replacements, bounded source metadata | Deeper planning and whole-app creation, robust real-model acceptance |
| Experimental local multi-backend adapters | **Implemented experimental on `main` (merged PR #51):** loopback chat adapters for llama.cpp, LM Studio and custom local endpoints; GUI selector | CI, real servers, packaged EXE, real Windows restart validation |
| Hugging Face GGUF catalog and verified downloads | **Implemented experimental on `main` (merged PR #51):** opt-in metadata browser plus pinned, SHA-256 checked resumable GGUF weight downloads | Real downloads, runtime install/update/delete and end-to-end Windows acceptance |
| Experimental CPU llama.cpp session controller | **Implemented experimental on `main` (merged PR #51):** user-supplied SHA-pinned binary + verified GGUF, one local API-key-protected server, GUI start/stop | Latest CI, real model, server ownership/child cleanup, binary installation and hardware memory acceptance |
| Offline local GGUF inventory | **Implemented experimental on `main` (merged PR #51):** bounded existing-manifest scan, no large-file hash on list; explicit full SHA-256 verification; selection in current GUI | Import, update/remove, live model use and installed-model lifecycle acceptance |
| Optional cloud LLM adapters | **Planned / not implemented:** user-authorized ChatGPT/OpenAI, Claude/Anthropic and compatible services | Consent per disclosure, credential isolation, spending limits, no default cloud/API or required subscription |
| Safe Git workspaces | **Implemented prototype:** per-task worktrees, path/budget validation, transactional edits | Broader recovery fault injection, demonstrated end-to-end rollback on Windows |
| Resumable autonomous cycle | **Implemented prototype:** limited test/repair checkpoints, `resume-safe`, GUI constant-size recovery summaries (12 recent outcomes and totals), 400-character recovery reason caps, lease-fenced engine state publication, bounded streamed checkpoint hashing, fail-closed timeout/runtime errors and strict test outcome validation | Resume additional phases safely, long-run crash/restart and concurrent worker acceptance |
| Execution isolation | **Implemented prototype:** packaged Windows `unittest` AppContainer path; fail closed on unsupported presets, missing/unreadable evidence, combined 32 MiB output cap, and a 1–3600 second test timeout budget | Secure coverage for additional frameworks, adversarial Windows acceptance |
| Python `pytest` packaged isolation | **Implemented experimental (PR #43):** rebased successor to [PR #29](https://github.com/DevTunaSoft/ForgeOS/pull/29), avoids Windows NUL logging denial | Hosted frozen Windows pytest AppContainer smoke and regression suite passed; still needs actual end-user Windows acceptance |
| Diagnostics and repair learning | **Implemented experimental:** SHA-256 evidence, bounded summaries, fail-closed archival errors, 64 KiB task-action result storage and 4,000-character failure diagnostics, aggregate learning | Encrypted retention, broader structured parsers, verified learning decisions |
| RAM-aware performance | **Implemented experimental:** pagination, status-only GUI monitoring, bounded SQLite task views, 64 KiB resume checkpoints, bounded logs, 16 KiB task-description and 64 KiB local prompt limits, memory-aware selection, on-demand RSS | Real Windows 4/8 GiB physical/VM tests, full process tree/model memory |
| Windows executable | **Implemented prototype:** PyInstaller packaging, hosted GUI lifecycle/pagination and AppContainer smoke | Real Windows install, local model, task lifecycle and safe distribution |
| GitHub workflows inside app | **Planned** | Authentication, import, PR/branch/review, safe controlled publication |
| Android APK and web builders | **Planned** | Reproducible toolchains, sandboxing, build artifacts and user acceptance |
| Multi-agent workflow orchestration | **Planned** | Tested model roles, routing, budgets, no mandatory cloud cost |
| General testing and autonomous QA | **Partial prototype** | Multi-framework secure runtimes, fuzz/fault-injection cross-module and UI/system tests |
| Backups, restore, handoff and installer | **Partial / planned** | Encrypted versioned backups, verified restore, complete artifact export, signed installer |
| Production readiness | **Not reached** | End-to-end acceptance matrix with release evidence and no blocking defects |

## Multi-LLM dependency-free integration

The merged multi-model implementation still requires Python CI, Windows frozen packaging,
real llama.cpp/LM Studio local endpoint acceptance, and clean recovery tests
before its status advances beyond experimental. The next required increments
are managed CPU llama.cpp **installer and full lifecycle** (experimental
SHA-pinned runtime installation and launch/stop are now coded on `main`),
real Windows HF download tests, canonical model profiles, measured backend
recovery and 16/32 GB Windows memory benchmarks. Cross-agent automatic routing, OOM failover and task-specialized
capability scoring are **planned**, not complete. See
[MODEL_COMPATIBILITY.md](MODEL_COMPATIBILITY.md).

## GitHub tracking for the next multi-LLM milestones

The [detailed multi-LLM roadmap](MULTI_LLM_ROADMAP.md#active-implementation-issues-on-github) now links actual GitHub work items: [managed llama.cpp #53](https://github.com/DevTunaSoft/ForgeOS/issues/53), [verified model inventory and lifecycle #54](https://github.com/DevTunaSoft/ForgeOS/issues/54), [memory governance and role routing #55](https://github.com/DevTunaSoft/ForgeOS/issues/55), and [real-Windows acceptance #56](https://github.com/DevTunaSoft/ForgeOS/issues/56). [Multi-LLM PR #51](https://github.com/DevTunaSoft/ForgeOS/pull/51) was merged into `main` (merge commit `7c582f8`); its experimental implementations do not close the outstanding acceptance milestones. No issue is counted as implemented until its required tests and source exist and have been verified.

## Open-source integration and token-efficient browser agent (tracked 2026-11-10)

**Status: experimental prototype on unmerged PR #66; not production-ready or Windows-verified.** Track the optional browser agent in [issue #65](https://github.com/DevTunaSoft/ForgeOS/issues/65). This work must not displace the existing P0/P1 runtime, recovery, security and real-Windows acceptance gates. All external source-code reuse requires license, provenance and security review first.

| Candidate repositories | Potential ForgeOS improvement | Existing owner / duplication rule | Priority / gate |
| --- | --- | --- | --- |
| [Dedeep007/browse_code](https://github.com/Dedeep007/browse_code), [1129Aliasgar/OpenBrowser](https://github.com/1129Aliasgar/OpenBrowser) | Browser-assisted coding, structured observation and diff previews | Reuse `engine.py`, `transactions.py`, `context_index.py`; do not add another editor | P2: source/license audit, isolated read-only prototype |
| [Tencent/BrowserSkill](https://github.com/Tencent/BrowserSkill), [BeatAPI/BeatBrowser](https://github.com/BeatAPI/BeatBrowser), [browser-use/browser-harness](https://github.com/browser-use/browser-harness) | Browser control, human takeover, observable actions | Reuse `gui.py`, `core.py` task leases, `recovery.py`, `diagnostics.py`; optional on-demand worker only | P2: bounded observation/action adapter; security and Windows tests |
| [andeya/token-free-gateway](https://github.com/andeya/token-free-gateway), [RubCut/ZeroAPI](https://github.com/RubCut/ZeroAPI), [PruhaNLP/web-to-api](https://github.com/PruhaNLP/web-to-api) | Optional external provider adapters | Extend existing model factory/routing; never require browser-auth gateway, bypass provider terms or disclose project files silently | P3: explicit opt-in, credential isolation, compliance and failure tests |
| [ypollak2/llm-router](https://github.com/ypollak2/llm-router), [SainagShetty/smart-router](https://github.com/SainagShetty/smart-router) | Capability-based routing, circuit breakers and token accounting | **Issue #55** owns `model_routing.py`; do not create another router | P2 after #53/#54 and measured model qualification |
| [andrefetch/postal](https://github.com/andrefetch/postal), [huisezhiyin/sdd-riper](https://github.com/huisezhiyin/sdd-riper), [stewie-sh/agent-crystallize](https://github.com/stewie-sh/agent-crystallize) | Transactional edits, compact context, checkpoint recovery | Extend `transactions.py`, `recovery.py`, `context_index.py` and current SQLite store | P1/P2 only where fault-injection proves a gap |

**Browser-agent acceptance proposal:** DOM/accessibility-first redacted observations with bounded bytes/nodes; screenshots only as necessary; user-authorized browser sessions; origin/action allowlists; timeouts, cancellation, human takeover and no automatic irreversible submission. Browser workers must be absent when unused. Use existing task leases and checkpoint verification to prevent duplicated consequential actions. Benchmark fixed tasks for completion rate, model calls, tokens, screenshot count, latency and peak process-tree RAM; a 50% token reduction and 90% completion rate are **targets, not observed results**. No CAPTCHA/bot-detection evasion or unauthorized session access.

**Next sequence:** inspect source/PR overlap and licenses → write fake-adapter security/regression tests → implement minimal optional read-only browser observation → add guarded actions and evidence → test frozen Windows packaging and physical Windows behavior. Keep the [user guide](USER_GUIDE.md), [architecture](ARCHITECTURE.md), [handoff](HANDOFF.md) and README aligned when code actually lands.

## Near-term engineering priorities

1. **Secure broader test execution without Docker.** With hosted pytest + unittest AppContainer smoke now passing, extend the same fail-closed guarantees to other selected frameworks and real user Windows machines.
2. **Finish transactional recovery and interruption handling.** Inject crashes, partial writes, invalid journals, stale leases, process kills, malformed checkpoints, and resume across real Windows restarts.
3. **Strengthen memory efficiency and visibility.** Avoid unbounded subprocess/output/context allocations; capture process-tree usage under workloads representative of 4/8/16/32 GiB Windows systems, carefully distinguishing model and application RAM.
4. **Validate the complete Windows usage path.** Launch installed EXE, connect to local Ollama, run generated edits, test/repair, stop/restart, inspect worktree and archive evidence on a real desktop.
5. **Improve documentation along with development.** Keep user manual, architecture, README feature matrix, this roadmap, security warnings, Windows build instructions and engineering handoff in sync via the [documentation policy](DOCUMENTATION_POLICY.md) and PR checks.

## Planned: Microtask Development Pipeline for large projects (2026-11-14)

**Status: PLANNED — requirements only.** ForgeOS must be able to transform a large application request or existing repository into small, dependency-aware, independently verifiable engineering tasks suitable for resource-constrained local coding models. This is not a claim that the decomposer, scheduler, or integration gates exist today. Extend the canonical task engine, SQLite store, model routing, worktree, recovery, and evidence systems; do not create a parallel orchestration stack.

### Required behavior

1. **Deterministic inventory first:** inspect repository structure, manifests, tests, interfaces, dependency boundaries, and existing documentation with bounded scans; avoid sending the entire repository to an LLM.
2. **Requirement traceability:** record project goals, constraints, acceptance criteria, and stable requirement IDs; preserve a compact architecture contract so microtasks do not drift from the original product.
3. **Dependency DAG:** decompose requested outcomes into atomic, testable tasks with explicit prerequisites, affected interfaces, expected files, and success conditions. Detect cycles, missing prerequisites, and conflicting edits before execution.
4. **Context budgeting:** assemble minimal relevant source excerpts, interface contracts, prior verified decisions, and test failures; use token budgets and deterministic retrieval. Never silently truncate a critical requirement or pretend incomplete context is sufficient.
5. **Model-sized snacks:** prefer one focused behavior and roughly 1–2 files per task, with an initial experimental input target of 1–2K tokens and approximately 20–80 changed lines when appropriate. These are tuning hypotheses, not hard limits; permit larger coherent tasks when correctness requires them, and split or escalate oversized work.
6. **Bounded execution:** run through existing validated edit manifests, isolated Git worktrees, lease-fenced task state, transactional journals, and explicit test-execution safety gates. No unrestricted model-generated shell commands.
7. **Verification and integration:** require focused tests, relevant regressions, interface/contract checks, and independent review before marking a task verified. Integrate in dependency order with conflict detection; never treat an isolated green test as whole-project acceptance.
8. **Failure handling:** capture root cause and evidence, attempt a small bounded repair budget (initially 2–3), re-run regression checks, and mark tasks blocked or escalate when progress is unproven; avoid infinite loops and fabricated success.
9. **Crash-safe orchestration:** persist task graph version, statuses, model/backend identity, context references, file hashes, checkpoints, integration state, and reasons for blocking. Resume only from validated checkpoints and prevent duplicate/concurrent conflicting writes.
10. **Resource governance:** default to one heavy resident model, bounded queues and logs, CPU/RAM budgets, and backpressure. Optimize end-to-end verified task throughput rather than chat latency alone.
11. **Human-readable progress:** show which requirement each snack advances, its dependencies, changed files, test evidence, failures, and overall project completion in the existing ForgeOS GUI.
12. **Final acceptance:** execute project-wide builds, integration/security/regression tests, artifact checks, and complete source/evidence/handoff export before claiming delivery. Preserve explicit approval for publication or external actions.

### Delivery phases and evidence gates

- **P0 — design and baseline:** map existing task/engine/store interfaces; specify versioned task-graph schema, limits, scheduling invariants, and threat model. Benchmark current whole-prompt behavior on the target Windows laptop.
- **P1 — deterministic decomposition substrate:** bounded inventory, requirement IDs, DAG persistence, cycle/conflict validation, and synthetic large-project fixtures. Verify low-memory operation without a model.
- **P2 — local-model decomposition and context retrieval:** structured task proposals with strict schema validation, deterministic relevant-context assembly, oversized-task split/escalation, and quality checks against known project requirements.
- **P3 — safe execution and integration:** reuse worktrees, leases, existing test/repair and checkpoint mechanisms; add dependency-aware scheduling, integration review, conflict handling, and crash/restart fault injection.
- **P4 — real-hardware acceptance:** compare whole-project prompt versus microtasks on the i5-1135G7/20 GB test laptop and the primary i7-1370P/32 GiB machine. Record end-to-end completion time, prompt/eval latency, peak process-tree RAM, edit validity, regression pass rate, rework, recovery correctness, and final artifact quality. Verify physical Windows restarts and packaging where applicable.

**Exit criteria:** a large representative project can be decomposed, executed, independently tested, integrated, interrupted/restarted, and resumed without losing requirement traceability or corrupting source; resource use stays within measured machine budgets; blocked work is reported honestly. A working prototype is not production readiness.

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

### 2026-11-08 — Official CPU llama.cpp installation and unified GUI milestone

Merged PR #51 added an experimental pinned official **Windows x64 CPU** `runtime_installer.py` behind an **explicit opt-in Install** action in the same managed GGUF dialog. The same controller verifies the installed ZIP and all DLLs immediately before launch; manual binary selection remains available. Unit/GUI contract regressions cover the verified start and tampering failure. **Experimental on `main`; real Windows model runtime acceptance is still unverified**. Follow [multi-LLM roadmap](MULTI_LLM_ROADMAP.md) and [user guide](USER_GUIDE.md). Next: real Windows 11 + GGUF inference, secure process identity, memory and crash recovery, issue #53.


### 2026-11-11 — native cleanup and model-retirement inventory safeguards

- **Implemented on `main`, test results pending:** native `llama.cpp` shutdown attempts forceful process cleanup if poll/terminate/OS wait or POSIX process-group signaling fails. Added mocked failure-path regression tests and one actual subprocess lifecycle integration test per OS family (POSIX process-group, Windows Job Object), but the repository's current Linux/Windows CI and physical Windows inference have not been verified.
- **Implemented on `main`, test results pending:** the existing retired GGUF inventory reuses the active-model `MAX_SCAN_ENTRIES=4096` cap to fail closed on oversized folders instead of scanning unlimited invalid entries. Added a synthetic 4,097-entry regression without filesystem-heavy test fixtures. Isolated Python reproduction passed; actual repository CI remains unverified.
- **Still required (#53, #54, #56):** exact-current-head Windows/Linux regression suite, frozen EXE smoke, native Windows process-tree cleanup under injected faults, real CPU GGUF start/infer/stop, 16/32 GiB memory/latency acceptance. Avoid describing these safeguards as production certification. Full evidence and commit SHAs are in [handoff](HANDOFF.md).


### 2026-11-11 — low-allocation deterministic routing checkpoint

- **Code on `main`:** `model_routing.choose_installed_model()` now rejects blank task input without copying it and avoids full-string lowercase conversion/regex scans for tasks of 700+ characters (those already select the largest eligible installed model under the unchanged heuristic). This extends issue #55's existing canonical router, rather than creating an independent router. Source commit `20a3b0f`, regression commit `d30a9d6`.
- **Verified isolated benchmark (not full ForgeOS):** same selected route for a 1,375,000-character task with traced temporary Python allocation peak reduced from 1,376,712 to 68 bytes and one-call routing time from 192.28 ms to 0.01 ms. Two focused isolated checks and 1,200 randomized differential cases passed. Full repository regression matrix and real Windows/llama.cpp/RAM benchmark are **NOT RUN / UNVERIFIED**.
- **Next exit gates:** current-head Linux/Windows CI and frozen EXE checks, physical Windows full development loop, measured model role qualifications and memory budgets. Keep #55 open; do not claim model quality improvements based solely on text-routing allocation tests.

### 2026-11-11 — reject malformed saved desktop preferences

- **Implemented on `main`:** fix the existing desktop preference sanitizer to reject actual NUL control characters instead of mistakenly rejecting the printable backslash-x00 sequence. Validate the task role as well as project, workspace, endpoint and model values, on both save and reload. This is an on-demand string check, with no new dependencies, workers or polling.
- **Verification:** one isolated Linux unittest regression covering save, legacy restore and safe printable text passed prior to commit; full repository CI and physical Windows GUI verification require the resulting commit's checks. This is a reliability fix, not a completed release milestone.

### 2026-11-11 — fail-closed workspace-path recovery

- **Implemented in source on main:** malformed checkpoint workspace paths are skipped individually, including embedded-NUL paths, path resolution failures and symlinked task roots. The direct execution path also refuses live/dangling symlinks before inspecting a task workspace.
- **Regression coverage committed:** a malformed checkpoint followed by a valid checkpoint must still resume the valid task; symlinked saved workspaces must be skipped; direct execution must reject a symlink without modifying the original repo or leaving a task lease. No new dependency, service or scan loop.
- **Acceptance:** hosted Linux/Windows tests and Windows EXE are pending this commit's actual Actions results; physical Windows restart, multi-process concurrency and model memory remain unverified. Continue issue #4 integrity, backup and interruption work.

### 2026-11-11 — avoid unnecessary AI context scans on recovery

- **Implemented on main:** generate repository file context only immediately before a needed AI edit or repair. Test-only resume, malformed checkpoint rejection, and exhausted repair loops do not build extra unused contexts.
- **Regression coverage:** existing test-pending and invalid-resume flows now assert no context scan, with a new final-failure test asserting exactly one context construction. No extra agent, service or dependency.
- **Acceptance:** verify exact-head Linux/Windows tests and hosted Windows packaging. Measured full-process memory, actual local-model sessions and Windows laptop crash recovery remain unverified.

### 2026-11-11 — verified online SQLite database backups (issue #4)

- **Experimental implementation on main:** the existing task Store provides an
  explicit SQLite integrity check and 64-page online backup of committed WAL
  state, with exclusive non-overwriting atomic publication. CLI commands
  `db-check` and `db-backup` expose the feature without new dependencies.
- **Added regression tests:** WAL-preserving snapshot, saved task round trip,
  no-overwrite, interrupted publication cleanup, transaction refusal and CLI
  integration. A local isolated Python smoke passed; the new exact-head
  cross-platform CI remains the source of truth for repository tests.
- **Not complete:** full backup/restore workflow, encryption, retention,
  crash/power-loss and physical Windows media tests. Keep issue #4 open.
  See [database backup instructions](DATABASE_BACKUP.md).

### 2026-11-11 — verified, non-destructive SQLite restore copy

- **Implemented (experimental):** the existing Store gains an explicit
  read-only-source `restore_backup_to()`, which checks the source SQLite
  integrity and ForgeOS task/lease schema, copies using 64-page native
  SQLite backup batches, verifies the staged copy, and exclusively publishes
  it to a new destination. CLI `db-restore-copy` works without opening or
  creating the active application database.
- **Regressions added:** WAL backup round trip with a URI-special-character
  filename, existing-destination protection, corrupt and incompatible DB
  rejection, failed publication cleanup, symlink source refusal and isolated
  CLI dispatch. No new dependencies, threads, model loading or polling.
- **Still outstanding (#4):** authenticated/guided active-database replacement,
  encryption, backup bundle for worktrees/evidence, power-loss and physical
  Windows disk/permission acceptance. Do not confuse a verified copy with a
  certified disaster-recovery procedure.

### 2026-11-11 — read-only database inspection before backup/check

- **Implemented:** `db-check` and `db-backup` verify an existing SQLite
  file and ForgeOS schema in read-only mode before the canonical task Store
  is initialized. Missing, symlinked and incompatible databases are refused
  rather than silently created or migrated. Restore and check/backup now
  share one bounded-result integrity/schema verifier; restored copies are
  checked after copying as well.
- **Regression coverage:** missing-path check/backup do not create files;
  unrelated SQLite database cannot be mutated or backed up through either
  command. No extra process or polling and no new dependencies.
- **Status:** source and regression committed, exact-head CI required;
  physical Windows/other-volume acceptance remains unverified.

### 2026-11-11 — Windows regression-harness correction

- GitHub Foundation at `79321b8` **FAILED on Windows Python 3.10 and
  3.12**, while both hosted Linux jobs passed. Actual Windows logs showed
  `WinError 123` because a test backup filename contained the invalid
  Windows character `?`, and `WinError 32` during temporary-directory
  cleanup because test SQLite connections were not explicitly closed.
- Corrected all three new database test modules to use legal URI-special
  filenames (`#`) and close SQLite connections. This is a cross-platform
  test-fixture fix, not a reason to weaken or bypass any assertions.
- Require a fresh full Windows/Linux matrix on the corrected head before
  claiming the backup and restore regressions pass. Real physical Windows
  recovery remains UNVERIFIED.

### 2026-11-14 — bounded Windows evidence allocation

Windows diagnostic archival now allocates temporary evidence files with a
bounded exclusive `os.open` loop instead of an unbounded `tempfile.mkstemp`
retry path. Access failures therefore return promptly and preserve the
existing fail-closed recovery decision. Focused regression coverage passes;
exact-head Linux/Windows CI, frozen EXE packaging and physical filesystem
fault-injection acceptance remain required.

### 2026-11-14 — portable database-copy publication

The verified SQLite backup and restore-copy path now shares one publication
helper. Windows uses an exclusive same-directory rename, while POSIX keeps
same-volume hard-link publication. This removes the Windows hard-link
assumption without permitting destination replacement. Regression tests cover
both publication branches and restore reuses the same helper. The change is
source-level and requires the exact-head Linux/Windows matrix plus physical
Windows NTFS fault-injection evidence; encrypted backup, power-loss recovery,
and active-database replacement remain open.

### 2026-11-14 — append-only recovery event journal

The task database now records bounded lifecycle events for task creation,
guarded transitions, lease ownership and stale-recovery reconciliation in an
append-only `forgeos_events` table. State changes and their journal rows share
one SQLite transaction, and update/delete triggers prevent recovery history
rewriting. The bounded `journal` CLI view supports diagnosis without loading
task result blobs. Full active-database restoration, encryption, external
evidence/worktree bundles and physical power-loss acceptance remain open.

### 2026-11-14 — streamed backup digest manifests

`db-backup` can now publish an optional new JSON manifest containing the
backup's byte size and streamed SHA-256. `db-restore-copy` can require the
manifest before opening the source, detecting accidental transfer/storage
corruption without buffering the database or replacing an active database.
Regression coverage includes bounded manifest contents, no-overwrite
preflight, and mismatch refusal. This is not encryption, authentication,
active-database activation, or physical power-loss acceptance; issue #4 stays
open.

### 2026-11-14 — bounded journal action metadata

The append-only lifecycle journal now migrates and exposes bounded action,
attempt, artifact-reference and evidence-reference fields. Canonical planning
and manual-test transitions populate action metadata, while evidence remains a
validated identifier rather than raw test output. Existing journal databases
migrate in place; regression coverage verifies migration, bounds and linkage.
Full replayable action/artifact export and power-loss acceptance remain open.

### 2026-11-15 — autonomous engine journal linkage

The autonomous edit/test/repair engine now labels its lifecycle publications
and records bounded repair-attempt numbers in the same append-only journal.
When a test runner supplies an existing validated evidence digest, the engine
links that identifier without copying output. Regression coverage verifies
repair-loop attempt ordering. Replayable artifact export and interruption
acceptance remain open.

### 2026-11-15 — bounded journal semantic integrity check

The read-only `journal-check` command now streams lifecycle rows through one
SQLite cursor, validates task references, event metadata, statuses and finite
timestamps, and caps reported corruption diagnostics at 32 rows. It returns a
failure status without rewriting data when malformed records are found.
Regression coverage verifies clean CLI output and capped corruption reports.
This is a diagnostic integrity gate, not replay, repair, encryption or
physical power-loss acceptance.

### 2026-11-15 — streamed journal handoff export

`journal-export` now publishes a new JSONL artifact from one bounded event-row
projection, optionally filtered to a task. Publication is same-directory and
no-overwrite on Windows and POSIX, and the exporter does not materialize the
full history in Python. Regression coverage verifies ordering, filtering and
CLI routing. This is a diagnostic journal handoff, not a complete database,
worktree/evidence bundle or import/replay implementation.

### 2026-11-15 — guarded offline database activation

The recovery path now provides an explicit `db-restore-activate` command that
verifies a backup, stages it through the existing low-memory SQLite restore
boundary, retains the old active database at a new rollback path, and records
bounded activation phases in a flushed marker. It refuses symlinks, occupied
rollback/marker paths and SQLite sidecars, and attempts to restore the old file
if publication fails. Regression coverage includes confirmation, sidecar and
occupied-path refusal, manifest mismatch, successful rollback retention and
injected publication/rollback faults. This remains an operator-guided offline
prototype: no running-process coordination, journal replay, encryption,
complete evidence/worktree bundle or physical power-loss/Windows acceptance.

### 2026-11-15 — bounded activation-marker reconciliation

Interrupted activation is now inspectable with `db-activation-status` and can
be explicitly rolled back from the durable `old_moved` phase with
`db-activation-rollback`. Marker reads are capped at 4 KiB, marker paths and
phases are validated, no active Store is initialized, and rollback refuses
occupied destinations, sidecars and leftover staged files. Read-only SQLite
preflight uses immutable mode when no sidecars exist so diagnostics do not
create journal files. This is still cooperative offline recovery, not marker
replay, encryption, a complete evidence/worktree bundle or physical
power-loss acceptance.

### 2026-11-15 — task-scoped bounded journal replay plan

`journal-handoff` now streams a self-describing JSONL plan for one task: a
scalar current-state projection, ordered lifecycle events, validated
artifact/evidence identifiers and a completion summary. SQL bounds journal
text before Python materializes it, and the artifact excludes prompts, project
paths, checkpoints, leases, raw errors and test output. It is intentionally
read-only and non-executing; import, evidence resolution, action replay,
encryption, complete worktree bundles and physical power-loss acceptance
remain open.

### 2026-11-15 — offline bounded journal handoff verification

`journal-handoff-check` now validates one exported task handoff without
opening SQLite or initializing a Store. It streams bounded JSONL records,
requires the header/task/event/summary structure, checks increasing event IDs,
validated references and the completion count, and caps diagnostics at 32
issues. This detects truncation and format tampering but does not authenticate
the file, resolve evidence, import rows, replay actions, encrypt data or prove
physical power-loss recovery.

### 2026-11-15 — streamed handoff integrity manifests

`journal-handoff` can now publish an optional bounded JSON manifest with the
handoff byte size and streamed SHA-256. `journal-handoff-check --manifest`
verifies that digest without buffering the JSONL artifact, detecting transfer
or storage corruption before a future import/replay step. The manifest is not
a signature, encryption, complete evidence/worktree bundle or physical
power-loss guarantee.

### 2026-11-15 — optional streamed evidence-reference verification

`journal-handoff-check --evidence-directory` now verifies each referenced
SHA-256 diagnostic gzip archive through a bounded decompression/hash stream.
It fails closed on missing, symlinked, corrupt or oversized evidence without
creating directories or loading raw output into memory. The check is opt-in;
complete evidence/worktree bundles, encrypted retention, action replay and
physical power-loss acceptance remain open.

### 2026-11-15 — handoff publication fault-injection coverage

Handoff export regression coverage now exercises both publication phases:
failed primary publication removes all staged artifacts, while a failure after
the handoff is published retains the valid handoff and does not leave a partial
manifest. The behavior is documented as a no-overwrite, staged publication
boundary; directory durability on physical power loss and complete
evidence/worktree bundles remain open.

### 2026-11-15 — POSIX publication directory flush

The shared no-overwrite publisher now flushes the parent directory after its
POSIX hard-link publication, while Windows retains the exclusive same-directory
rename and staged-file flush. Regression coverage verifies the platform split.
This improves crash-durability evidence on POSIX but does not establish
physical Windows NTFS interruption recovery or complete source/evidence
bundles.

### 2026-11-15 — bounded worktree integrity manifest

`worktree-manifest` now streams bounded SHA-256 metadata for regular files in
one Git worktree, and `worktree-manifest-check` rehashes the referenced tree to
detect changed, missing or extra files without opening SQLite or copying source
contents. Git/runtime/environment metadata is excluded and symlink, hard-link,
reparse-point and special-file inputs fail closed. This is an integrity
inventory toward a future complete source/evidence bundle, not an archive,
signature, encryption layer or Git-object backup.

### 2026-11-15 — bounded source-only worktree archive

`worktree-archive` now streams the selected regular source files into a new
bounded ZIP and embeds the existing JSONL SHA-256 manifest;
`worktree-archive-check` validates the archive and hashes decompressed entries
without extraction or SQLite access. The checker bounds classic ZIP central
directory metadata, rejects ZIP64, traversal, noncanonical, encrypted and
symlink-like entries, and preserves the no-overwrite publication boundary.
This is a source-transfer artifact, not a task database/evidence bundle,
Git-object backup, encrypted or signed retention format, restore/import/replay
mechanism or physical power-loss acceptance.

### 2026-11-15 — bounded recovery bundle coherence index

`recovery-bundle-index` now validates a handoff, source-only worktree archive
and every referenced evidence archive, then publishes a bounded JSONL index of
their filenames, sizes and streamed hashes. The offline checker revalidates
all three artifact classes, including the nested source archive, without
opening SQLite or extracting content. This binds separately stored recovery
artifacts before a future copy/encryption step; the index itself is not a
complete bundle, signature, encrypted retention format, restore/import/replay
mechanism or physical power-loss acceptance.

### 2026-11-15 — bounded recovery transfer package

`recovery-bundle` now packages the validated handoff, source-only archive,
optional handoff manifest and referenced evidence together with the coherence
index in one classic DEFLATE ZIP. The exporter streams and hashes every input,
self-checks the finished package through private bounded validation copies and
publishes with the existing no-overwrite boundary. `recovery-bundle-check`
rejects unsafe ZIP layouts and revalidates the nested index, source archive,
handoff and evidence without opening SQLite. The 1 GiB package and 256 MiB
aggregate compressed-evidence limits keep this a low-memory transfer artifact;
encryption, signing, database restore, replay and physical power-loss
acceptance remain future work.

### 2026-11-15 — marked recovery bundle materialization

`recovery-bundle-extract` now provides the receiving-side recovery boundary for
the complete passive transfer artifact. It validates the package before
copying, streams entries through private staging, reserves a new destination,
and writes a bounded phase marker before no-overwrite file moves. The marker is
promoted to `complete` only after the package digest and copied coherence index
are revalidated; `recovery-bundle-extract-check` rejects publishing, failed or
tampered directories. This does not initialize SQLite, execute a task or replay
actions, and physical power-loss acceptance remains open.

### 2026-11-15 — resumable marked recovery materialization

`recovery-bundle-extract-resume` now resumes only directories marked
`publishing` or `failed`. It verifies the source package against the durable
marker, hashes existing entries before preserving them, refuses mismatches or
links, stages only missing entries and promotes the marker to `complete` after
the same final index and package checks. This is an explicit single-writer
operator path with no background retry, task execution, journal replay or
physical power-loss acceptance.

### 2026-11-15 — detached recovery package authentication

`recovery-bundle-auth` and `recovery-bundle-auth-check` now provide a detached
shared-secret HMAC-SHA-256 record for a validated recovery ZIP. The package is
streamed with the existing 64 KiB safety boundary; key and record sizes are
bounded, the secret is never copied into the artifact, and wrong keys or
package/record tampering fail closed. This adds operator-controlled
authenticity, not encryption, public-key signer identity, database restore or
physical power-loss durability; protected key storage and rotation remain
future work.

### 2026-11-15 — authenticated recovery materialization gate

The detached recovery HMAC is now an optional gate for
`recovery-bundle-check`, `recovery-bundle-extract` and
`recovery-bundle-extract-resume`. Operators can require the package and
shared-secret record to agree before any receiving-side staging or file move;
wrong or incomplete credentials fail closed while existing unsigned integrity
flows remain compatible. The extraction marker stores no secret, so the
operator must supply the pair again for a resumed operation. Encryption,
public-key identity and physical power-loss acceptance remain future work.

### 2026-11-15 — bounded recovery authentication key provisioning

`recovery-bundle-auth-keygen` now creates a new 32-byte-to-4 KiB shared secret
from the operating system CSPRNG, writes it through flushed private staging
and publishes it without overwriting an existing path or printing key bytes.
The command is database-free and uses the existing Windows rename/POSIX
hard-link publication split. Operators still own key storage, ACL review,
rotation and backup; this does not add encryption or public-key identity.

### 2026-11-15 — private POSIX recovery authentication keys

Recovery HMAC verification now rejects POSIX key files with group/other
permission bits, while preserving Windows compatibility and the documented
operator ACL-review boundary. Regression coverage proves the rejection on
POSIX without pretending that mode bits are a Windows NTFS security model.

### 2026-11-15 — optional database artifact in passive recovery bundles

`recovery-bundle-index` and `recovery-bundle` can now include a previously
created `db-backup` SQLite file and optional backup manifest. The index checks
the database read-only, rejects SQLite sidecars and caps the database artifact
at 512 MiB; package checking and receiving-side extraction revalidate and
materialize it as a passive file. This connects verified task-state export to
the existing handoff/source/evidence transfer without activating a database,
initializing Store, replaying actions, encrypting the package or claiming
physical power-loss durability. Database restore/activation remains an
explicit operator step.

### 2026-11-15 — copy-only restore from passive recovery bundles

`recovery-bundle-db-restore-copy` now validates a package, optionally enforces
its detached HMAC, stages only the bundled database and manifest, and delegates
to the existing read-only SQLite restore-copy boundary. The destination must
be new; the active database is never opened or replaced, and bundles without
database artifacts are refused. Explicit offline activation, journal replay,
encryption and physical power-loss acceptance remain separate concerns; the
bundle activation bridge is tracked in the next entry.

### 2026-11-15 — explicit activation from passive recovery bundles

`recovery-bundle-db-restore-activate` now validates an optional detached HMAC,
stages only the bundled database and manifest, and delegates to the existing
guarded offline activation boundary. It requires `--confirm`, retains the old
active database at a new rollback path, and preserves the core marker and
fail-closed recovery behavior. The command never initializes the active Store,
replays the handoff, merges histories or performs automatic failover. Regression
coverage includes successful rollback retention, confirmation refusal,
authentication refusal and database-free CLI dispatch. Encryption, journal
replay and physical power-loss acceptance remain open.

Focused recovery database and CLI tests passed 10/10; the full isolated suite
passed 696 tests with 8 expected skips. Documentation/link coverage, compileall
and `git diff --check` passed. The bounded memory benchmark passed for three
12 MiB cycles with a 1,982,228-byte Python tracemalloc peak and a
29,941,760-byte process peak RSS; this remains a synthetic process benchmark,
not physical Windows or model-memory acceptance.

### 2026-11-15 — bundled activation fault-injection coverage

The recovery-bundle activation bridge now has an interruption regression that
injects failure at both publication and automatic restoration of the old active
file. It verifies the durable `old_moved` marker and rollback copy remain
inspectable, the temporary bundle-staging directory is removed, and the
operator can complete the explicit `db-activation-rollback` path. This closes
the wrapper-level fault-injection gap without claiming physical power-loss or
Windows NTFS interruption acceptance.

Focused recovery database and CLI tests passed 11/11; the full isolated suite
passed 697 tests with 8 expected skips. Documentation/link coverage and
`git diff --check` passed. The bounded memory benchmark passed for three
12 MiB cycles with a 1,982,228-byte Python tracemalloc peak and a
30,404,608-byte process peak RSS. These remain local synthetic/process
measurements, not physical Windows or model-memory acceptance.

### 2026-11-15 — activation marker directory durability

Activation phase-marker replacement and removal now flush the containing POSIX
directory entry, closing the window where a moved rollback file could be paired
with a stale `prepared` marker after a crash. Windows retains the existing
directory-sync no-op and exclusive rename behavior; this is a one-shot
durability improvement, not physical NTFS power-loss acceptance.

Focused activation tests passed 14/14; the full isolated suite passed 699 tests
with 8 expected skips. Documentation/link coverage, compileall and
`git diff --check` passed. The bounded memory benchmark passed for three
12 MiB cycles with a 1,982,228-byte Python tracemalloc peak and a
30,031,872-byte process peak RSS. These remain local synthetic/process
measurements, not physical Windows or model-memory acceptance.

The first hosted PR Foundation attempt exposed a Python 3.10 Windows test seam:
patching `os.name` before `Path` construction selected `PosixPath` on Windows.
The follow-up removes that global platform patch and verifies the existing
directory-sync call directly; the corrected head remains cross-version and
Windows-compatible.

### 2026-11-15 — Windows open-handle activation refusal

The activation regression suite now holds the active database with a real
Windows `CreateFileW` share mode that permits no sharing, then verifies
activation fails before moving the active file, creating a rollback copy or
leaving a marker. The test is skipped on non-Windows hosts; hosted Windows
coverage is useful compatibility evidence, not physical reboot or NTFS
power-loss acceptance.

Focused activation tests passed 15/15 on Windows; the full isolated suite
passed 700 tests with 8 expected skips. The bounded memory benchmark passed
for three 12 MiB cycles with a 1,982,228-byte Python tracemalloc peak and a
30,179,328-byte process peak RSS. Documentation/link coverage, compileall and
`git diff --check` passed; physical reboot and NTFS power-loss acceptance
remain open.

### 2026-11-15 — explicit rollback of crash-retained activation staging

An interrupted offline activation can leave the verified replacement in its
private staging path after the `old_moved` marker is durable. Rollback now
preserves that file by default, while `db-activation-rollback --confirm
--discard-staged` validates it as a regular, sidecar-free ForgeOS SQLite
database and then removes only that staged artifact before restoring the
retained active database. Corrupt, linked, sidecar-bearing or unreadable
staging fails closed without moving the rollback copy. This is bounded
process-crash recovery evidence, not physical reboot or NTFS power-loss
acceptance.

### 2026-11-15 — published activation marker finalization

An activation crash after the replacement is published can now be reconciled
with `db-activation-finalize ACTIVE --confirm`. The command accepts only the
`published` marker phase, validates both the new active and retained rollback
databases through bounded read-only inspection, refuses sidecars and unexpected
staging, and removes only the completed-operation marker. `prepared` and
`old_moved` remain separate, explicit recovery states; physical reboot and
NTFS power-loss acceptance remain open.
