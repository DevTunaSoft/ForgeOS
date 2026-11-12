# ForgeOS — engineering takeover dossier
**Prepared:** 2026-11-11. **Classification:** operational continuity document, not a production certification.

> **Start here when taking over development.** This is a concise, current-oriented bridge between [the original vision](PRODUCT_VISION.md), [the cumulative engineering history](HANDOFF.md), [the handoff standard](HANDOFF_STANDARD.md), [the architecture](ARCHITECTURE.md), and [the roadmap](ROADMAP.md). It is not a competing source of historical truth. Confirm all current GitHub facts at the time of use.

## 1. Why ForgeOS is being built

The underlying problem is that writing an entire application with AI still commonly requires the user to assemble and supervise many unrelated tools. Someone who is not a programmer may need to choose a coding model, maintain a repository, manage terminal commands, install dependencies, resolve failing tests, understand crashes, package an executable and remember what happened across conversations. ForgeOS aims to reduce that coordination burden.

The target is **a single Windows desktop application that accepts plain-language requirements and turns them into usable, tested software**, with AI actively assisting at every engineering phase rather than merely producing snippets in chat. The eventual workflow includes planning, coding, testing, diagnosing, repairing, building, documenting, packaging and producing a self-contained handoff.

This vision is intentionally more ambitious than the current prototype. The product must not pretend that an isolated successful test, a generated code patch or a CI-built EXE equals a finished, reliable app. The user needs understandable progress, explicit failure reasons and recoverable state.

### Why local-first matters

The user should not have to pay per request, purchase an expensive GPU, operate a server cluster or sign up for multiple subscriptions. Local models should be the normal path. Optional external models may be supported with explicit user authorization, disclosure and cost controls, but they must not become a hidden dependency. ForgeOS should make intelligent use of the hardware available rather than forcing a large model into memory.

### Why resource limits are foundational

The intended environment includes ordinary Windows laptops, including a practical 16 GiB baseline and an additional 32 GiB validation tier. CPU-only inference is important. Memory use must be controlled across the whole process tree, not just the Tkinter shell. Logs, database queries, source scans, prompts, model inventories, download buffers and UI activity displays should all have deliberate bounds. Faster and smaller should not come at the expense of correctness or security.

### Why continuity is foundational

AI development sessions end, assistants change, branches diverge, CI artifacts expire and machine failures occur. The repository must contain enough information to resume without reconstructing the project's mission from chat logs. Evidence must be attached to the exact code revision and environment. Failed approaches are part of the engineering record and should not be silently forgotten.

## 2. Product contract

**Must eventually do:** present one approachable GUI; receive requirements in ordinary language; preserve a durable task queue; understand an existing repository before editing; select local models appropriate to task and memory; apply safe recoverable changes; run appropriate isolated tests; investigate failures and retry within limits; capture diagnostics; resume eligible interrupted work; build supported deliverables; provide an understandable manual and handoff.

**Must preserve:** no Docker requirement; no mandatory paid APIs; Windows-first desktop experience; low resource overhead; protection of user data and credentials; clear approval boundaries; modularity; no duplicate features or conflicting branches; honest verification.

**Must not do:** silently transmit private source to remote services; auto-spend on cloud inference; run arbitrary untrusted generated code with host privileges; destroy dirty worktrees; claim that a mocked integration proves a real backend; invent test results; loop indefinitely without measurable progress; publish or deploy without the required approval.

**Future platforms:** Windows EXE creation, Android APK creation and web application generation are product goals. Do not mark them complete solely because ForgeOS itself packages as a Windows EXE.

## 3. Current source-backed prototype map

The following mapping is drawn from the repository's architecture and roadmap as inspected on 2026-11-11. Treat module names as starting points, not proof that every described behavior has been independently tested.

| Subsystem | Canonical location | Why it exists | Present limitation |
| --- | --- | --- | --- |
| Desktop GUI | `src/forgeos/gui.py` | Single front end, task management, activity and diagnostics | Real physical Windows UX acceptance outstanding |
| CLI | `src/forgeos/cli.py` | Scriptable development, recovery and diagnostics | Not a replacement for complete GUI workflows |
| Core task state | `src/forgeos/core.py` | SQLite persistence, leases and basic model/test integration | Broader workflow and migration acceptance required |
| Task actions | `src/forgeos/task_actions.py` | Shared GUI/CLI plan/test operations | Requires correct trust and lease boundaries |
| Task engine | `src/forgeos/engine.py` | Bounded edit/test/repair and checkpoints | Not universal autonomous software creation |
| Workspaces | `src/forgeos/workspace.py` | Separate Git worktrees and source protection | Worktree isolation is not process sandboxing |
| Transactions | `src/forgeos/transactions.py` | Staged edits, journaling, recovery | Real crash/power-loss acceptance incomplete |
| Recovery | `src/forgeos/recovery.py` | Safe resumption of eligible checkpoints | Not every phase can be resumed automatically |
| Test runtime | `src/forgeos/test_runtime.py` | Disposable test snapshots and cleanup | Coverage depends on supported presets |
| Windows containment | `src/forgeos/secure_runner.py`, `appcontainer.py`, `windows_containment.py`, `sandbox_runner.py` | Restrict selected packaged test workloads | Requires real Windows adversarial acceptance |
| Model adapters | See `docs/MODEL_COMPATIBILITY.md` and source ownership in `docs/MULTI_LLM_ROADMAP.md` | Ollama and experimental local backends | Actual multi-backend hardware verification incomplete |
| Packaging | `.github/workflows/windows-exe.yml`, `packaging/launcher.py` | Frozen development EXE and smoke tests | Not a signed production installer |

Do not assume module names or paths are unchanged after this document's date. Inspect the live tree before modifying code.

## 4. Architecture and execution lifecycle

**Entry:** user opens the Windows GUI, selects an existing Git repository, enters a task and chooses an available local model and test settings.

**Preparation:** the task is persisted with ownership/lease controls. The engine validates constraints and creates a dedicated Git workspace so model-generated changes do not directly overwrite the user's primary checkout.

**Generation:** a local model proposes structured, bounded edits. Paths, file sizes, traversal, symlinks and edit manifests are validated. The system must not accept free-form arbitrary filesystem instructions as trusted edit operations.

**Persistence:** edits are staged with a journal and hashes to support rollback and safe recovery. A crash must not silently convert an uncertain write into a successful task.

**Verification:** selected test presets execute, collect bounded output and produce traceable evidence. Frozen Windows containment has a narrower supported matrix than unrestricted source-mode tests.

**Repair:** failures may be summarized and passed to a model for bounded repair attempts. Repeating the same failing action without progress is not acceptable. Record the failure signature, attempts and stopping condition.

**Recovery:** eligible persisted checkpoints may be resumed only after verifying worktree identity, journal state and relevant hashes. Ambiguous or conflicting state should block rather than overwrite external work.

**Delivery:** current output is principally inspectable source changes and evidence. Automatic safe publication, complete multi-platform application packaging and production-ready installer delivery remain roadmap work.

## 5. Model ecosystem and routing direction

Ollama is the established local-first starting point and must remain supported. Experimental OpenAI-compatible loopback adapters, llama.cpp management and Hugging Face GGUF discovery/download work are described in the model compatibility and roadmap documents. They are not equivalent to a tested cross-backend intelligent router.

The intended progression is: discover installed models and runtimes; validate provenance and hashes; measure memory and quality on representative tasks; qualify models for roles such as planning, coding, review and diagnosis; choose the smallest qualified model for the task; enforce concurrency and memory budgets; recover gracefully from a model/runtime failure. Do not build a second router if existing selection logic can be extended.

For benchmarks, record model identifier and revision, quantization, context size, runtime version, CPU/RAM environment, prompt set, test outcomes, wall time, peak process-tree memory and failure categories. A model should not be promoted based on a single successful demonstration.

## 6. Security boundaries

Treat generated source and model output as untrusted. Path validation is necessary but insufficient: tests and build scripts can execute commands, access the network and read host files. Use supported containment paths and explicitly block unsupported workloads. A Git worktree isolates files from the main checkout but does not isolate privileges.

Never put credentials, tokens, private logs or sensitive customer data in GitHub issues, test fixtures, exported diagnostics or handoff documents. Redact secrets before archival. Record the retention policy and the failure behavior if archival cannot be completed safely.

External open-source code may be reused only after checking license, provenance, maintenance and security. Track the upstream reference, license obligations, adopted code and rationale; do not duplicate an existing ForgeOS component simply because a new repository is attractive.

## 7. Current roadmap priorities and live work to reconcile

As of the 2026-11-11 GitHub search, the following issues were returned as open. These are **tracking facts at inspection time**, not confirmation that their code is absent or that the issue's acceptance criteria are unmet.

| Issue | Theme | Next reconciliation |
| --- | --- | --- |
| [#53](https://github.com/DevTunaSoft/ForgeOS/issues/53) | Managed CPU-first llama.cpp runtime | Compare merged implementation to full lifecycle acceptance and real Windows behavior |
| [#54](https://github.com/DevTunaSoft/ForgeOS/issues/54) | Verified GGUF inventory and lifecycle | Verify pinned identity, update/remove safety and actual model usage |
| [#55](https://github.com/DevTunaSoft/ForgeOS/issues/55) | RAM governor and role routing | Resolve benchmark criteria and collect real measurements before routing |
| [#56](https://github.com/DevTunaSoft/ForgeOS/issues/56) | Real Windows multi-model acceptance | Test GUI, local inference, restart, containment, packaging and resource limits |
| [#65](https://github.com/DevTunaSoft/ForgeOS/issues/65) | Optional browser agent | Keep optional; verify token savings and security before broad integration |
| [#41](https://github.com/DevTunaSoft/ForgeOS/issues/41) | Repository governance | Confirm documentation CI and About metadata requirements |
| [#4](https://github.com/DevTunaSoft/ForgeOS/issues/4) | Durable journal/backups | Separate implemented edit journals from full backup/restore acceptance |
| [#3](https://github.com/DevTunaSoft/ForgeOS/issues/3) | Autonomous editing | Reconcile existing engine against the issue before adding more |
| [#2](https://github.com/DevTunaSoft/ForgeOS/issues/2) | Tkinter threading and Windows UX | Check whether issue remains open because of missing real Windows evidence |

The same search returned [PR #66](https://github.com/DevTunaSoft/ForgeOS/pull/66) (experimental bounded browser observation/Browsentic bridge) and [PR #29](https://github.com/DevTunaSoft/ForgeOS/pull/29) (older pytest AppContainer proposal). Check merge/replacement status before touching either. The roadmap indicates that a later pytest path was merged through PR #43; do not merge the older proposal just because it remains visible.

## 8. Known evidence gaps and release blockers

- **Physical Windows acceptance:** source/hosted CI smoke cannot establish that the complete workflow works on a user's actual laptop.
- **Real local-model execution:** mocked responses and endpoint adapters are insufficient proof of successful llama.cpp/GGUF model inference and recovery.
- **Process-tree RAM:** measuring GUI RSS alone does not account for the inference runtime and spawned tests.
- **General-purpose application building:** supported bounded edit/test flows are not a verified turnkey Windows/Android/web application factory.
- **Security matrix:** the packaged containment runner supports selected presets; do not extend trust to every test/build tool.
- **Installer and backup:** a CI-generated one-file EXE is not a signed production installer; full encrypted backup/restore is not production-verified.
- **Browser automation:** optional browser agent code and its token-economy claims require measured evaluation and adversarial testing.

Do not label the project release-ready until these are addressed with revision-specific evidence.

## 9. Fast takeover procedure

1. Open the [repository](https://github.com/DevTunaSoft/ForgeOS), inspect default branch, latest commit and any uncommitted work in the actual checkout.
2. Read this dossier, [HANDOFF.md](HANDOFF.md), [PRODUCT_VISION.md](PRODUCT_VISION.md), [ROADMAP.md](ROADMAP.md), [ARCHITECTURE.md](ARCHITECTURE.md) and [HANDOFF_STANDARD.md](HANDOFF_STANDARD.md).
3. Inspect open PRs, issues, recent commits, failed workflow runs and the exact source touched by the top unblocked issue.
4. Search the codebase for existing implementations before creating a new module or feature. Record the duplication audit.
5. Select one bounded improvement with explicit acceptance criteria and a rollback path.
6. Implement the smallest maintainable change; run focused regression tests, then broader checks when appropriate.
7. Capture command, platform, SHA, actual PASS/FAIL/NOT RUN/UNVERIFIED outcome and resource impact.
8. Update HANDOFF.md and affected canonical docs in the same checkpoint; never replace history with an unsupported status summary.
9. Commit to the intended branch only when permitted and safe; report the resulting SHA.
10. Repeat on the next unblocked priority until completion or a genuine blocker.

### Source checkout on Windows

```powershell
git clone https://github.com/DevTunaSoft/ForgeOS.git
cd ForgeOS
git status --short --branch
git log -1 --format="%H %s"
py -m pip install -e .
py -m unittest discover -s tests -v
py -m forgeos gui
```

The test command is a recommended check, **not a claim it was run for this dossier**. Opening the GUI does not require a model; AI functionality requires a compatible running local runtime. Consult [USER_GUIDE.md](USER_GUIDE.md) and [WINDOWS_BUILD.md](WINDOWS_BUILD.md) for current instructions and safety limitations.

### Safe recovery and diagnostic commands

```powershell
git status --porcelain=v1
git worktree list
py -m forgeos --help
py -m forgeos recover --help
py -m forgeos resume-safe --help
```

Check the CLI's current help before using a recovery command. Some operations require workspace paths and explicit trust flags. Never automatically authorize host tests on an untrusted project. Preserve journals, worktrees and task database backups before attempting repair.

## 10. Required per-checkpoint operational snapshot

Copy this compact snapshot into the cumulative ledger after meaningful work:

```text
Date and UTC time:
Repository / branch / exact HEAD:
Why this checkpoint matters:
Starting state and duplicate-work audit:
Changed source files:
Changed documentation:
Issue / PR / commit:
Tests executed (exact commands):
Results: PASS / FAIL / BLOCKED / NOT RUN / UNVERIFIED
Windows physical acceptance:
Real model/backend acceptance:
Peak RAM / CPU / disk / network (or NOT MEASURED):
Security/privacy implications:
Open defects and failed attempts:
Next highest-priority unblocked task:
Exact next command:
Rollback / recovery plan:
Handoff PDF export: SHA / pages / verified or NOT RUN
```

## 11. Handoff completeness and publication

The cumulative handoff is a chronological engineering ledger. This dossier is a navigation and recovery aid. The handoff standard requires a **minimum 35 substantive pages** in a rendered, self-contained publication; page count must be measured after rendering, not inferred from Markdown length. Include the source commit, PDF checksum, publication date, page count and export command. Do not use empty pages, oversized typography or duplicated paragraphs to inflate the count.

**Status of this dossier:** repository documentation only. No code tests, real-Windows execution, local-model benchmark or 35-page PDF rendering was performed as part of this documentation change. All such acceptance remains **NOT RUN** unless separately evidenced by revision-specific test records.

## 12. Principles for the next engineer

Never confuse historical intention with implemented behavior. Never confuse a merged PR with physical-machine acceptance. Never confuse a model response with a successful software build. Preserve user work. Prefer one well-tested implementation over two competing ones. Measure resource usage instead of guessing. Do not restart already completed tasks. Record why an approach failed and switch strategy when it stops producing progress. Keep the original mission visible: **ForgeOS exists to make reliable AI-driven software development accessible through one local-first, efficient Windows application.**
