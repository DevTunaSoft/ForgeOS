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


---

# Part II — Deep engineering dossier and institutional knowledge

> **Evidence boundary:** The sections below describe engineering intent, design invariants, investigation protocols and proposed acceptance gates. They do not certify that the corresponding implementation exists or has passed tests. For current behavior, inspect source, exact-commit CI and the latest cumulative handoff. This part was added to preserve decisions and avoid costly rediscovery.

## 13. Foundational design decisions and tradeoffs

### 13.1 Why a Windows desktop GUI rather than a command-line-only tool

The target user should be able to create and supervise software without memorizing CLI syntax or understanding Git internals. The GUI is therefore not a thin decoration over an otherwise inaccessible tool; it is the primary operating environment. CLI entry points remain valuable for deterministic testing, automation and recovery. They should share the same core task and persistence logic rather than create competing behavior.

**Tradeoff:** Tkinter is comparatively lightweight and widely available, but it requires strict UI-thread ownership, disciplined event queues and accessibility validation. A richer framework is not justified solely by visual polish if it adds significant RAM, packaging size and complexity. Evaluate any replacement using measured cold-start time, idle RSS, peak RSS, keyboard navigation, screen-reader behavior, frozen EXE size and regression cost.

**Design rule:** A worker may perform blocking inference, filesystem operations or tests, but must not directly mutate Tk widgets. GUI event reporting must be bounded and must not poll at excessive frequency.

### 13.2 Why Git worktrees

A per-task worktree separates model edits from the user's primary checkout, enabling inspection, diffing and controlled rollback. It also supports tracing a change to a task and branch. A worktree does not protect the operating system against malicious build scripts or tests.

**Tradeoff:** Worktrees require Git and introduce ownership/cleanup complexity. An abandoned worktree may contain important user changes. Cleanup must inspect tracked and untracked state, refuse destructive removal, and provide an actionable explanation.

**Invariant:** No automatic cleanup may destroy user-authored or externally modified content.

### 13.3 Why SQLite and bounded local records

A local SQLite store can preserve tasks, leases and checkpoints without running a separate database service. This aligns with offline operation, low resource overhead and simple installation.

**Tradeoff:** Schema changes, concurrent writers, disk-full conditions, corrupted databases and abrupt shutdowns require explicit handling. A backup must use a consistent SQLite backup mechanism rather than blindly copying an active database file.

**Invariant:** A task's durable status must never claim success merely because a worker returned without throwing; success requires the intended acceptance evidence.

### 13.4 Why structured edits instead of unrestricted model-generated shell commands

Language-model output is untrusted. Structured edit manifests make it possible to validate destination paths, sizes, duplicate targets and expected ownership before changing files.

**Tradeoff:** A constrained edit protocol is less flexible than allowing the model to run arbitrary shell commands. This restriction is deliberate until the system has a tested permissioned execution model.

**Invariant:** Validation must precede side effects, and a partially completed multi-file operation must have an inspectable recovery path.

### 13.5 Why local-first and CPU-first inference

Local inference offers control over privacy, recurring costs and offline operation. The project must remain useful on conventional Windows hardware without a dedicated GPU. Smaller coding models can respond quickly, but quality varies by task and model.

**Tradeoff:** The smallest model is not necessarily the cheapest end-to-end if poor edits trigger many retries. Qualification must include test success and total wall time, not model parameter count alone.

**Invariant:** Model choice should optimize verified task success within resource budgets, not merely maximize token throughput.

## 14. System contracts and state ownership

### 14.1 Task identity

Each task needs a durable identifier, requested outcome, repository identity, execution policy, creation time and lifecycle state. The same active request should not create uncontrolled duplicate jobs. The exact deduplication key and database constraints must be inspected in source before changing them.

**Expected transitions:** created/queued → claimed/running → checkpointed → tested → completed or blocked/failed. This is a conceptual model, not an assertion of the exact implemented enum. Compare actual transitions to the code and tests.

**Failure questions:** What if two workers claim the same task? What if a worker dies after writing files but before updating SQLite? What if a stale worker tries to publish a result after its lease expires? What if a user presses retry twice? What if a task is re-opened after an application restart?

### 14.2 Workspace identity

Workspace ownership must be tied to a task, source repository and expected branch/worktree registration. Reusing a directory solely because its name matches is unsafe. A recovered task should verify Git metadata, expected branch and edit hashes before proceeding.

**Failure questions:** What if the source repository was moved? The branch was deleted? The worktree contains untracked files? The source commit changed? The user edited generated files between runs?

### 14.3 Model session identity

A model session must identify the backend, endpoint, model name or immutable identity where possible, runtime configuration, context budget and task owner. For managed processes, record PID/ownership information only when it can be safely validated; a stale PID must not authorize killing an unrelated process.

**Failure questions:** What if a local server is already listening on the expected port? What if the model changes under the same display name? What if a download is truncated? What if the backend hangs after accepting a request? What if a server returns malformed JSON or a giant response?

### 14.4 Test evidence identity

Every test outcome should be associated with a specific task, code revision or worktree state, command/preset, platform, runtime version, start/end time, exit status and bounded evidence location. SHA-256 can establish file integrity but not that a test was meaningful or that a result applies to a newer commit.

**Invariant:** A test result for one source state must not silently authorize a different state.

## 15. Failure-mode and effects analysis

The table below is a **design review checklist**, not a list of confirmed defects.

| Failure mode | Potential impact | Detection | Required safe response |
| --- | --- | --- | --- |
| Model request times out | Task stalls; GUI appears frozen | Monotonic elapsed time and timeout record | Cancel/terminate safely where supported; retain checkpoint |
| Model produces invalid edit manifest | Unsafe or unusable edit | Strict schema/path/size validation | Reject before filesystem changes |
| Crash during multi-file replacement | Inconsistent source tree | Journal state and file hashes | Recover or block without clobbering external edits |
| SQLite locked/corrupted | Task state inaccessible | Transaction failure/integrity check | Stop writes; preserve database and diagnostics |
| Disk becomes full | Incomplete logs, edits or model downloads | OS error, space check, failed atomic publish | Fail closed; retain recoverable partials |
| Two workers claim one task | Duplicate edits, racing results | Lease/token fencing and DB constraints | Reject stale publisher |
| Generated test escapes containment | Data disclosure or host compromise | Adversarial security tests | Block unsupported execution, investigate immediately |
| Child model process survives shutdown | Memory leak and port conflict | Process ownership and lifecycle checks | Terminate only verified owned child |
| Runtime uses excessive RAM | Thrashing, OOM or machine instability | Whole-process-tree RSS/commit tracking | Limit concurrency, unload or block safely |
| Log output grows indefinitely | Disk/RAM exhaustion | Per-record and total size caps | Truncate with explicit marker, retain evidence metadata |
| Download hash mismatch | Untrusted executable/model | SHA-256 against pinned expected digest | Quarantine/delete safely; never execute |
| Stale GitHub status | Duplicate or conflicting work | Refresh branch/PR/issue before edits | Reconcile; do not open duplicate PR |
| CI passes but real PC fails | False readiness claim | Hardware acceptance matrix | Mark release blocked until real acceptance |
| User edits a generated worktree | Potential data loss | Dirty/untracked inspection and hashes | Preserve and require explicit resolution |
| Remote provider selected accidentally | Privacy or cost exposure | Explicit opt-in and endpoint audit | Block network disclosure without consent |
| AI repeats identical failed repair | Wasted time, compute and tokens | Attempt signatures and progress deltas | Stop after bounded attempts; change strategy |

### Fault-injection methodology

Use disposable repositories and synthetic data. Inject failures at pre-write validation, staging, first replacement, later replacement, journal finalization, checkpoint publication, test startup, test timeout and restart. Record the exact interruption point and expected recoverable state. Validate both data integrity and absence of unwanted side effects. A recovery test that merely returns a success code without comparing files and journals is insufficient.

## 16. Performance engineering and resource-accounting plan

Resource efficiency is a product requirement, not a late-stage optimization. Measurements should include the desktop process, model server, helper processes, test subprocesses and transient build processes.

### Measurement definitions

- **Idle baseline:** app opened, no task running, after startup stabilization; record private bytes/RSS and CPU.
- **Task peak:** maximum total process-tree committed memory during plan/edit/test/repair.
- **Cold-start latency:** time from process launch to responsive GUI.
- **First useful output:** time from task submission to first meaningful progress or result.
- **Sustained task throughput:** verified completed tasks per hour, not generated tokens per second alone.
- **Memory growth:** idle memory after repeated tasks compared with initial idle state.
- **Storage growth:** database, logs, caches, model weights, worktrees and artifacts.
- **Recovery cost:** time and memory needed to restore a checkpoint after an interruption.

### Hardware acceptance matrix

| Environment | Purpose | Current proof status |
| --- | --- | --- |
| Windows 11, 16 GiB RAM, CPU-first | Practical baseline | Physical end-to-end evidence not established in this dossier |
| Windows 11, 32 GiB RAM, CPU-first | Broader model and multitask capacity | Physical end-to-end evidence not established in this dossier |
| Windows 11, 8 GiB RAM | Stretch target for limited workloads | Not a promised supported configuration |
| Windows 11, 4 GiB RAM | Research only unless proven feasible | No support claim |
| Hosted Windows runner | Regression, frozen build and containment smoke | Does not substitute for real hardware |

**Proposed benchmark acceptance criteria:** no unbounded queue or log growth; no unexplained persistent process after shutdown; stable task state after restart; no user-visible GUI freeze during a model timeout; and reproducible peak process-tree memory. Numerical thresholds should be set from measured baselines, not invented in this dossier. If issue #55 specifies contradictory RAM targets, reconcile them before using the issue as a release gate.

### Model comparison protocol

Hold prompt/task set, context size, quantization class and hardware conditions as consistent as possible. Run a fixed set of representative tasks: small bug fix, multi-file change, test generation, failure diagnosis, API integration and simple app scaffold. Capture syntax validity, accepted edit rate, test pass rate, repair count, wall time, prompt/completion tokens where available, memory peak and task-level success. A fast response that never produces a valid tested change should not be preferred.

## 17. Observability and forensic diagnostics

A useful diagnostic event should answer: *what operation was attempted, by which task and revision, on what backend, under which policy, what happened, and what should happen next?*

### Suggested event fields

```json
{
  "schema_version": 1,
  "timestamp_utc": "YYYY-MM-DDTHH:MM:SS.sssZ",
  "monotonic_ms": 0,
  "task_id": "example-task",
  "run_id": "example-run",
  "phase": "test",
  "operation": "execute_preset",
  "result": "UNVERIFIED",
  "duration_ms": 0,
  "backend": "local",
  "code_revision": "example-sha",
  "error_category": null,
  "evidence_sha256": null,
  "recovery_hint": "inspect the recorded test output"
}
```

This is a proposed event contract; it is not proof that the current application emits this exact schema. Any implementation must keep event fields bounded, avoid logging secrets, and define compatibility and retention behavior.

### Diagnostic quality tests

A developer should be able to distinguish a missing Git executable from an invalid Git repository; an Ollama connection refusal from a model inference timeout; malformed model JSON from a valid but unsafe edit; a failed test from an unsupported test preset; and a recoverable journal from an externally modified worktree.

Every blocked state should expose an actionable reason and the exact next safe step. Do not report a generic `TimeoutError` when the operation, timeout budget, backend and last progress event are known.

## 18. Security threat model and permission matrix

### Assets to protect

Source repositories, local credentials, SSH keys, cloud tokens, user documents, browser sessions, local AI model files, task history, diagnostics, Windows host integrity and network access.

### Threat actors and mistakes

Untrusted repository code, malicious dependency scripts, prompt-injected model context, compromised model downloads, accidental user approvals, stale process ownership, overly permissive filesystem access and leaked diagnostic archives.

### Permission principles

| Operation | Intended policy |
| --- | --- |
| Read source files for planning | Only authorized project scope; bounded collection |
| Write source files | Validated paths inside owned worktree; journaled changes |
| Execute tests/build scripts | Supported containment or explicit trusted-host authorization |
| Access network during generated-code execution | Deny by default unless explicitly authorized and safely implemented |
| Send source to remote AI | Separate informed opt-in, credential and cost controls |
| Delete/overwrite user changes | Never silently; require explicit consequential approval |
| Commit/push/merge | Controlled workflow with source and review protections |
| Install model binaries | Verify source, immutable version/digest and dependencies |

A security review must test both expected success and denial of forbidden actions. A test that verifies only the happy path is not sufficient evidence of containment.

## 19. Reproducible release and supply-chain record

For each proposed release, retain:

1. Source repository, exact commit and dirty-tree status.
2. Build OS, architecture, Python version, dependency versions and toolchain hashes where available.
3. Build commands, environment variables excluding secrets, and packaging configuration.
4. Executable filename, size, SHA-256, signing status and provenance.
5. CI workflow URL, run ID, commit SHA, job outcomes and artifact retention deadline.
6. Frozen startup/import, GUI lifecycle, supported test-containment and recovery evidence.
7. Physical Windows acceptance: machine class, RAM, OS build, model/backend, observed failures and successful tasks.
8. Installation, upgrade, rollback and uninstall instructions.
9. Security exceptions, known defects and release decision with approver.
10. Source, documentation, test evidence and handoff export bundled or linked by immutable revision.

A successful PyInstaller workflow proves that an artifact was built in that environment. It does not prove the installer is signed, the application is self-contained with model weights, or the user can build arbitrary software without dependencies.

## 20. Open-source reuse and architecture compatibility

Before borrowing from external projects, record the precise problem, current ForgeOS implementation, the candidate repository and pinned upstream revision, its license and obligations, its security posture, integration cost, runtime footprint and whether it replaces or duplicates existing logic.

Use a decision record:

```text
Candidate and upstream commit:
License / attribution:
Specific capability to reuse:
Existing ForgeOS equivalent:
Measured benefit expected:
New dependencies / processes:
RAM, CPU, disk and network impact:
Security boundary:
Adopt / adapt / reject:
Reason and evidence:
Rollback path:
```

This applies particularly to browser-agent libraries, token-efficient observation techniques, local model managers and multi-agent frameworks. Avoid importing a heavyweight stack when a small audited adapter achieves the same measurable outcome.

## 21. Human-facing user experience and accessibility acceptance

A first-time user should understand the difference between **Create task**, **Generate plan**, **Run**, **Retry**, **Recover**, **View diagnostics**, and **Export results** without reading source code. Errors should explain what failed, whether anything was modified, and what the user can safely do next.

**Scenario A — missing Git:** startup readiness should identify the missing prerequisite and explain installation without creating duplicate tasks.

**Scenario B — model unavailable:** GUI remains responsive, task state remains durable, and the user sees the selected backend and connection failure rather than a silent hang.

**Scenario C — repair blocked:** display the last verified source state, tests that ran, reason retries stopped and whether the worktree is safe to inspect.

**Scenario D — crash/restart:** the user sees recoverable tasks and a safe, explicit recovery action, not a false success or an endless spinner.

**Scenario E — resource pressure:** indicate when a model exceeds the practical RAM budget and offer a smaller qualified model or sequential execution; do not silently kill unrelated processes.

**Scenario F — accessibility:** keyboard navigation, visible focus, readable contrast, high-DPI scaling and screen-reader behavior require real testing; screenshots alone are not sufficient.

## 22. Autonomous development and multi-agent governance

Multiple agents are useful only when their responsibilities and write boundaries are clear. Parallelize independent research, testing and code review where possible. Avoid concurrent edits to the same files, simultaneous migrations of one SQLite schema or competing PRs for the same requirement.

A task assignment should include: objective, owner, base SHA, permitted paths, forbidden paths, dependencies, acceptance criteria, test plan, expected resource impact and integration owner. Before integration, reconcile all agent outputs against the current branch and rerun affected tests.

**Progress measurement:** a cycle counts as progress only when it produces a verified code change, a newly identified root cause, a resolved blocker, a meaningful new regression test or a concrete reduction in risk. Repeating an unchanged test or generating another status report is not progress.

**Anti-loop rule:** after two substantially identical failures, stop the failing approach, inspect evidence and previous attempts, change strategy and verify. After two cycles without measurable progress, document the blocker and move to independent work when available.

## 23. Decision records and change rationale

An architecture decision record (ADR) should be short but complete:

```text
ADR identifier / date / author:
Problem and constraints:
Options considered:
Chosen approach:
Why chosen:
Alternatives rejected:
Security and resource tradeoffs:
Implementation location:
Evidence / benchmarks:
Known risks:
Revisit trigger:
Supersedes / superseded by:
```

Recommended initial ADR subjects include Windows/Tkinter versus heavier GUI stacks, SQLite versus service database, worktrees versus direct edits, structured edit manifests, local-first model routing, fail-closed test containment, bounded logs and event queues, transactional journaling, and explicit cloud consent.

Do not rewrite a historical ADR merely because the implementation later changed. Add a superseding decision and retain the reasoning.

## 24. Detailed takeover rehearsal

A second engineer or AI should be given **only** a clean repository checkout and ordinary GitHub access, not the conversation that produced it. The rehearsal should measure whether they can:

1. Explain ForgeOS's mission and non-negotiable constraints accurately.
2. Distinguish current implemented prototypes from future promises.
3. Identify the default branch, exact current HEAD and relevant open PRs/issues.
4. Locate the GUI, task engine, persistence, transactions, recovery and security modules.
5. Describe why generated code must not run unrestricted on the host.
6. Find and run a documented test command without damaging user work.
7. Identify the highest-priority unblocked engineering task without duplicating work.
8. Explain which Windows and local-model acceptance tests remain unverified.
9. Recover a disposable interrupted task using documented safe procedures.
10. Record a new checkpoint with evidence, rollback and exact next steps.

**Scoring proposal:** each criterion PASS/FAIL/NOT RUN, with direct evidence. A rehearsal cannot pass solely because the reviewer says the documentation is good. Any failed criterion should produce a targeted documentation or implementation fix and a repeat of the failed step.

## 25. Project continuity and knowledge retention

### What must survive an assistant change

- The complete original purpose and design constraints.
- A canonical inventory of modules and responsibility boundaries.
- Exact GitHub state, relevant branches, commits, PRs and issues.
- The latest verified test and release evidence, with environment and SHA.
- Current blockers and their root causes, including abandoned approaches.
- Safety constraints and permission requirements.
- Resource budgets and measurement methodology.
- The next task, acceptance criteria, exact commands and rollback plan.

### What must not be stored in the dossier

API keys, personal credentials, access tokens, private customer records, raw confidential transcripts, device secrets, copied proprietary code without authorization or unverifiable claims presented as fact. Store references to protected evidence, not sensitive payloads.

### Freshness model

The mission is comparatively stable. Architecture changes when component boundaries change. The cumulative ledger changes after meaningful engineering checkpoints. The operational snapshot changes whenever branch, blockers, priorities or verification status changes. A PDF export is a dated immutable snapshot and should never masquerade as live state.

## 26. Practical readiness rubric

| Dimension | Prototype evidence | Production acceptance needed |
| --- | --- | --- |
| GUI | Existing Tkinter interface and hosted smoke described in docs | Real-user Windows workflow, accessibility and restart |
| AI | Ollama integration and experimental local adapters | Real models, sustained workloads, correct failures and fallback |
| Editing | Bounded manifests, worktrees, journals | Broad fault injection and real Windows crash recovery |
| Testing | Selected presets and packaged containment smoke | Multi-framework coverage and adversarial isolation |
| Memory | Bounded internal structures and some CI measurements | Whole process-tree 16/32 GiB workload measurements |
| Diagnostics | Bounded archives and summaries | Privacy-safe retention, export, actionable incident reconstruction |
| Autonomy | Limited plan/edit/test/repair cycle | Reliable end-to-end software delivery with independent QA |
| Packaging | Development Windows EXE workflow | Signed/verified distribution and installation/rollback |
| Backup | Partial persistence/recovery features | Tested versioned backup and full restore |
| Handoff | Cumulative history and takeover dossier | Automated drift checks, verified 35+ substantive-page export and independent takeover rehearsal |

**No aggregate percentage of completion should be invented.** Readiness must be evaluated against explicit acceptance criteria and actual evidence for the target release.

## 27. First-week plan for a new lead engineer

**Day 1 — reality reconciliation:** verify branch, HEAD, PRs, issues, CI and documentation. Identify stale or contradictory status statements. Do not modify core code before understanding the baseline.

**Day 2 — reproducible local setup:** create a disposable test repository, run the documented suite, exercise GUI startup and verify installed local model connectivity if hardware is available. Record environmental differences.

**Day 3 — critical safety audit:** inspect worktree ownership, journal recovery, task lease fencing and test containment. Confirm no unbounded input or output path has been introduced.

**Day 4 — resource and model baseline:** capture idle and task process-tree memory, backend latency and model identity. Compare against realistic hardware targets.

**Day 5 — highest-priority scoped correction:** implement one unblocked improvement, add regression coverage and document exact results.

**Days 6–7 — integrated acceptance and handoff:** run affected Windows and Linux CI, reconcile outstanding issues, update the cumulative ledger and prepare a verified handoff export. This is an illustrative sequencing plan, not a claim that work will occur on a calendar schedule.

## 28. Final acceptance for this deep dossier

The dossier is complete as a *knowledge artifact* only when an independent reader can understand the mission, identify source responsibilities, assess current readiness, reproduce the development baseline, avoid known failed approaches and safely take the next action. A minimum page count is a separate publication gate.

**Verification for this extension:** documentation authored and committed to GitHub; source code tests **NOT RUN**; Windows physical tests **NOT RUN**; PDF page count **NOT RUN**; independent takeover rehearsal **NOT RUN**. No claim of production readiness is made.


---

## 29. Handoff automation — initial implemented controls (2026-11-11)

The repository now contains `scripts/check_handoff.py`, `tests/test_handoff_check.py` and `.github/workflows/handoff-integrity.yml`. The checker uses the Python standard library and does not start an AI model, browser, database or resident service. It checks that the canonical mission, handoff, architecture, roadmap and user guide documents exist and contain minimum expected topics. It scans relative Markdown links under `docs/` for missing targets and paths that escape the repository. Markdown fenced-code examples are excluded from the link scan.

**Local usage:**

```powershell
py scripts/check_handoff.py --json
py -m unittest discover -s tests -p "test_handoff_check.py" -v
```

The GitHub Actions workflow runs the validator and its unit tests on documentation, checker, test or workflow changes; it can also be manually dispatched. It uses a five-minute timeout, read-only repository permissions and per-ref concurrency cancellation. It does not trigger a second workflow, push commits, or rewrite historical records.

**Verification boundary:** five equivalent unit tests passed in the isolated authoring environment before repository publication. The actual committed GitHub files and their workflow have **NOT YET** been verified by a completed GitHub Actions run in this checkpoint. The validator does not yet check Markdown heading anchors, compare statements with source code, prove that a historical test passed, measure PDF pages, or generate a current-state snapshot. Those require distinct, evidence-backed implementations.

**Next implementation priorities:** (1) inspect the first GitHub workflow outcome and correct any real link or validator failures; (2) add a deterministic read-only snapshot generator that records branch, SHA, clean/dirty status and available CI metadata without inventing readiness; (3) add a reproducible PDF export with an actual 35-substantive-page gate; (4) perform an independent repository-only takeover rehearsal and record failures; (5) only then consider blocking branch protection on the check. Do not make a newly introduced unverified check mandatory before observing its results.

## 30. Deeper operational continuity — source of truth by question

| Question | Primary source | Evidence requirement |
| --- | --- | --- |
| Why does ForgeOS exist? | `PRODUCT_VISION.md`, `REQUIREMENTS.md` | Stable original requirements and recorded change decisions |
| What code exists? | Live source tree at exact SHA | Actual module inspection and executed tests |
| What changed and why? | Git commit graph, PR discussion, `HANDOFF.md` | Traceable commit and decision rationale |
| What is being worked on? | Live GitHub issues/PRs plus current snapshot | Checked timestamp, issue/PR identity, branch |
| What passed? | CI run or local evidence record | Exact revision, command, environment and output |
| What fails or remains blocked? | Latest verified diagnostics and issue state | Repro steps, failure category and next safe action |
| How do I operate the app? | `USER_GUIDE.md` | Instructions verified against current GUI |
| How do I rebuild the app? | `WINDOWS_BUILD.md` and workflows | Toolchain, commands, artifacts, hashes |
| How do I recover interrupted work? | `HANDOFF.md`, recovery code and CLI help | Journal and lease validation on disposable fixture |
| Is this release ready? | Explicit release gate and real hardware results | No unsupported extrapolation from hosted CI |

The handoff is successful only when these sources agree. If they conflict, do not average claims; investigate the live implementation and record a correction with evidence.
