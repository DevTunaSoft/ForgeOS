# ForgeOS engineering roadmap

**Read [What ForgeOS Will Do — and What It Will Not Do](PRODUCT_VISION.md) for the full product vision, shipped prototype capabilities, and explicit non-goals.** The [user manual](USER_GUIDE.md) remains the canonical click-by-click operating guide.

**Status as of 2026-11-11: active prototype.** This document distinguishes **implemented on `main`**, **in development**, and **future goals**. It is not a claim that the entire product is production-ready. Each milestone needs source changes, regression evidence, Windows frozen build evidence where relevant, and real end-user testing before release claims.

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
| Resumable autonomous cycle | **Implemented prototype:** limited test/repair checkpoints, `resume-safe`, lease-fenced engine state publication, fail-closed timeout/runtime errors and strict test outcome validation | Resume additional phases safely, long-run crash/restart and concurrent worker acceptance |
| Execution isolation | **Implemented prototype:** packaged Windows `unittest` AppContainer path; fail closed on unsupported presets | Secure coverage for additional frameworks, adversarial Windows acceptance |
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
