# ForgeOS permanent handoff standard

**Purpose:** Preserve the complete reasoning, scope, evidence, decisions and recovery path for ForgeOS so a new developer or AI can continue safely without redoing work.

**Authoritative cumulative ledger:** [HANDOFF.md](HANDOFF.md). This standard is an index and acceptance contract, **not** a second competing progress ledger. The cumulative ledger already contains substantial historical engineering notes. [Product vision](PRODUCT_VISION.md), [roadmap](ROADMAP.md), [architecture](ARCHITECTURE.md), [user guide](USER_GUIDE.md), [Windows build](WINDOWS_BUILD.md), and [multi-LLM roadmap](MULTI_LLM_ROADMAP.md) provide their respective authoritative detail.

## Why this project exists

ForgeOS is being built because conventional AI coding products still require users to coordinate tools, models, source control, testing, recovery, and packaging themselves. The target is a **single accessible Windows desktop GUI** where a non-programmer describes an application and local-first AI assists throughout planning, implementation, testing, debugging and delivery. ForgeOS should support multiple local coding models, intelligently control CPU/RAM consumption, resume safely after interruption, record reliable evidence, and eventually build complete apps. It must remain usable without Docker or mandatory paid API subscriptions. **The vision is not a claim of current capability.**

## Product invariants

1. **Local-first, low-cost:** Ollama remains supported; optional local runtimes and explicit-consent remote providers may be added without requiring paid services.
2. **Resource discipline:** minimal memory, CPU, disk and network use; bounded prompts, logs, queues and histories; no unnecessary polling or heavyweight background processes.
3. **Safety:** isolate generated code and tests; fail closed on unsupported security paths; protect credentials, source trees and user data; no silent destructive operations.
4. **Integrity:** atomic or recoverable edits, explicit checkpoints, verifiable hashes and safe recovery.
5. **Autonomy with evidence:** bounded plan/edit/test/repair cycles; independently verify results; stop unproductive loops; never claim tests passed without running them.
6. **Single coherent interface:** accessible, beginner-friendly Windows experience; clear activity, failures, progress, recovery and next actions.
7. **Modularity and reuse:** inspect existing code and work before modifying; avoid duplicate implementations, issues, branches and PRs.
8. **Accurate status:** distinguish implemented prototype, experimental, planned, blocked and independently verified production behavior.

## Minimum 35-page handoff publication requirement

The **human-readable export** of the authoritative cumulative handoff must contain **at least 35 substantive pages**, excluding blank pages, covers and filler. The Markdown source itself has no fixed page count: verify the actual count after PDF rendering and record it with the export's commit SHA. Do not artificially inflate font size, margins or spacing to meet the minimum. If the export is shorter, add missing engineering substance before publication. Prefer a versioned PDF export alongside the Markdown ledger when feasible; Markdown remains authoritative. A stale PDF must be labeled with its source commit and date.

### Required coverage (35 substantive chapter topics)

1. Executive project summary and scope.
2. Origin of ForgeOS and the problem it solves.
3. Intended audience, accessibility and non-coder experience.
4. Original product principles and constraints.
5. Intended complete user journey.
6. Explicit non-goals and unsupported claims.
7. Current implemented versus planned feature matrix.
8. Repository identity, branch and baseline commit.
9. Release history and milestone timeline.
10. Architecture overview and component boundaries.
11. Desktop GUI and user interaction design.
12. Task creation, persistence and deduplication.
13. AI model selection and Ollama integration.
14. llama.cpp, LM Studio and compatible endpoints.
15. Model acquisition, integrity and lifecycle.
16. Role routing and model benchmark acceptance.
17. Memory, CPU and storage budgets.
18. Git workspace isolation and source provenance.
19. Edit manifest validation and transactional writes.
20. Crash recovery, journals and checkpoints.
21. Autonomous task engine and repair limits.
22. Testing strategy, coverage and test inventories.
23. Windows security containment and threat model.
24. Diagnostics, logs, evidence retention and privacy.
25. Error classification, learning and regression prevention.
26. Packaging, installation and distribution.
27. Real Windows acceptance and hardware matrix.
28. External repositories, licensing and reuse audit.
29. Browser-agent strategy and token efficiency.
30. Current issues, PRs, CI and unresolved defects.
31. Failed approaches and anti-loop lessons.
32. Prioritized roadmap, dependencies and acceptance gates.
33. Beginner-friendly installation and operating instructions.
34. Exact recovery, setup and takeover procedure.
35. Decision log, change log, ownership and next work.

A chapter may refer to an authoritative companion document, but the exported handoff must still contain enough explanatory material to stand alone offline.

## Update protocol — every meaningful development checkpoint

- Read the current default branch, HEAD SHA, relevant open PRs/issues, workflows and existing ledger before work.
- Append to **HANDOFF.md**: timestamp, objective, rationale, changed paths, branch and commit/PR, acceptance criteria, exact test commands and PASS/FAIL/BLOCKED/NOT RUN/UNVERIFIED outcomes, measured resource impact, defects, security concerns, rejected approaches, remaining blockers and the next executable step.
- Reconcile README, roadmap, architecture, feature status and user guide **only where affected**. Avoid copy-pasting the same long narrative into multiple files.
- Keep the record cumulative; do not erase prior failed attempts or overwrite history with a status-only summary.
- At release or major milestone, render a versioned PDF, inspect the output, check substantive page count (>=35), and record the source SHA, export date and validation result.
- If export is not possible, leave Markdown available and mark PDF export **NOT RUN**; do not claim a verified 35-page artifact.

## Takeover checklist

1. Confirm repository and default branch, working-tree status, latest HEAD and outstanding PRs.
2. Read [HANDOFF.md](HANDOFF.md), this standard and [PRODUCT_VISION.md](PRODUCT_VISION.md).
3. Reconcile the latest ledger entries with actual source and CI, not prior chat statements.
4. Identify the highest-priority unblocked task; check for duplicate work.
5. Define a minimal change and measurable acceptance criteria.
6. Implement, test and capture actual output and resource impact.
7. Update the cumulative ledger and affected documentation in the same development checkpoint.
8. Commit to the designated branch only under repository protections; document any inability to commit.
9. Preserve exact commands, paths, test results and blockers so another developer can resume immediately.

**Current validation of the 35-page requirement:** NOT RUN. This standard establishes the requirement; it does not pretend that a page-count-verified PDF already exists.


## Engineering continuity system — required operating design

### Three-layer recovery model

**Layer A — immutable mission:** PRODUCT_VISION.md and this standard explain why ForgeOS exists, the intended user experience, product invariants and exclusions. Do not quietly change these in response to a transient implementation difficulty. Record a rationale and decision whenever the mission is changed.

**Layer B — append-only development history:** HANDOFF.md records verified changes and decisions over time. Entries must carry evidence references and avoid rewriting previous outcomes. Corrections should explicitly supersede an earlier statement rather than silently erasing it.

**Layer C — current operational snapshot:** the latest section of HANDOFF.md (or a dedicated concise index linked from it) must identify the checked branch and SHA, current feature readiness, active tasks and owners, exact blockers, and the next runnable command. Refresh it after each significant change. A newcomer should be able to determine the next safe action within five minutes, then follow Layer B for context.

The repository, source code, commit graph and current CI results are always stronger evidence than historical prose. A handoff is not authoritative evidence of a test that never ran.

### Detailed minimum content for each chapter

The following are substantive content requirements, not headings to fill with generic prose. Each chapter in a release export must explain *why the topic matters, the intended behavior, actual implementation, evidence, limitations and next decisions* where relevant.

| Chapter | Minimum substantive evidence |
| --- | --- |
| 1. Executive summary | Intended outcome, current product stage, major completed milestones, top risks, release gap |
| 2. Origin and rationale | Problem with fragmented AI coding workflows; motivation for a turnkey local-first solution; why existing alternatives do not fully satisfy the goals |
| 3. Audience and accessibility | Non-programmer user journeys, understandable error handling, accessible UI expectations, limitations of the prototype |
| 4. Original constraints | Windows-first, no Docker, local-first/free defaults, RAM efficiency, safe autonomy, one GUI, test evidence |
| 5. End-to-end experience | Desired request-to-artifact flow, phase transitions, human approvals, definition of finished |
| 6. Non-goals | No invented pass results, no silent uploads, no mandatory cloud payments, no unsafe unrestricted execution |
| 7. Feature matrix | Source-backed implemented/experimental/planned status with proof, caveats and dependency links |
| 8. Git baseline | Remote, default branch, exact SHA, working changes, active branches and conflict avoidance |
| 9. Timeline | Significant dated milestones, merges, reversions, superseded proposals and reasons |
| 10. Architecture | Component boundaries, call/data flow, persistence and trust boundaries; actual file paths |
| 11. GUI | Screens, controls, event ownership, background work, accessible feedback, error and restart behavior |
| 12. Task state | SQLite schema/migrations, deduplication, lease fencing, transition rules, interrupted work |
| 13. Ollama | Setup, model discovery, request limits, timeouts, diagnostics, tested versions and fallback |
| 14. Other endpoints | Compatibility contracts, local endpoint security, runtime ownership, real-server validation gaps |
| 15. Model lifecycle | Model provenance, SHA checks, download recovery, install/start/stop/remove, disk cleanup |
| 16. Model routing | Benchmark dataset, role qualifications, reproducibility, scoring, rejection thresholds and fallback |
| 17. Resource budgets | Baselines, peak RSS and process-tree measurements, CPU/disk/network, bounded queues, hardware matrix |
| 18. Git isolation | Worktree naming/ownership, branch conflicts, cleanup rules, user-change preservation |
| 19. Transactions | Manifest limits, staging, journal format, hash verification, rollback and fault injection |
| 20. Recovery | Checkpoint schema, restart behavior, idempotency, corrupt state handling, safe retry commands |
| 21. Autonomous engine | Planning/edit/test/repair phases, iteration caps, progress checks, stuck-loop avoidance |
| 22. QA | Unit/integration/E2E/fuzz/security/performance suites, command outputs, skips and unresolved failures |
| 23. Containment | Threat model, generated-code trust, Windows AppContainer limits, fail-closed behavior |
| 24. Diagnostics | Structured event schema, evidence hashes, redaction, log size/retention, export/replay |
| 25. Repair learning | Failure taxonomy, what may be learned, avoidance of unverified fix replay |
| 26. Packaging | Build reproducibility, dependencies, one-file EXE limitations, signing, artifact retention |
| 27. Real hardware | Windows versions, RAM tiers, CPU-only model acceptance, GUI/model/repair/restart observations |
| 28. External reuse | Dependency/license/provenance register, security review and deduplication findings |
| 29. Browser agent | Optional scope, permission boundaries, token savings methodology, recovery and test strategy |
| 30. Live GitHub work | Issues, PRs, CI URLs, commit SHAs, branch ownership, blockers, evidence freshness |
| 31. Failed approaches | Symptoms, attempts, root causes, why retries stopped, proven alternative |
| 32. Roadmap | Priorities, dependencies, measurable acceptance criteria, release gates, deferred scope |
| 33. Operating manual | First-run prerequisites, task entry, model setup, diagnostics, error recovery, screenshots where available |
| 34. Takeover runbook | Exact commands, expected output, safe rollback, local paths, environment differences |
| 35. Decisions and next actions | ADR references, scope changes, evidence-backed priorities, first actionable task |

### Permanent project rationale — context that must survive every handoff

ForgeOS originated from a desire to build software using natural-language instructions without having to manage a collection of separate coding agents, terminal tools, model launchers, Git interfaces and testing utilities. Its intended value is **orchestration and verified delivery**, not merely an embedded chat client. It should perform engineering steps for the user: interpret requirements, inspect an existing project, avoid duplicate changes, select an appropriate model, make bounded edits, run tests, diagnose failures, safely recover and ultimately package usable software.

The original design explicitly prioritizes machines without expensive GPUs. A 16 GiB RAM Windows machine is an important practical target; 32 GiB is a useful additional validation tier. Lower-memory operation is desirable but cannot be declared supported without measurements. CPU-first inference, model-size tradeoffs, streaming/bounded processing and minimal background work are therefore core design decisions, not optional polish.

The project must not depend on Docker or paid subscriptions. It must not sacrifice user data, security, correctness or maintainability for apparent autonomy. Autonomous progress means independently taking safe, authorized actions and stopping with accurate diagnostics when a real blocker occurs. It does not mean inventing tests, looping indefinitely, running untrusted code unrestricted or pushing unreviewed changes.

### Evidence contract — every meaningful claim

Each change record must contain:

```text
Checkpoint ID:
UTC timestamp:
Repository / default branch:
Base SHA / resulting SHA:
Task or issue / PR:
Why this work matters:
Acceptance criteria:
Changed paths:
Implementation summary:
Commands executed and environment:
PASS evidence:
FAIL evidence:
SKIPPED / NOT RUN / UNVERIFIED:
Security and privacy implications:
RAM/CPU/disk measurements (or NOT MEASURED):
Known limitations and regression risks:
Attempts rejected / root cause:
Documentation synchronized:
Next exact command / owner / blocker:
```

For every reported test result, retain the command, platform, revision and outcome. A previous green CI run does not prove a newer commit. A hosted Windows runner does not prove physical-user-machine behavior. A mocked local-model response does not prove real model inference. Distinguish these explicitly.

### Recovery and disaster scenarios

**Interrupted coding session:** fetch the latest branch, inspect the worktree and git status, read the newest handoff checkpoint, compare it with actual commits, and resume only the first unblocked task. Never assume uncommitted changes are disposable.

**Interrupted transaction:** inspect journals and the recovery command documented by the installed version. Do not delete pending-edit files or force-reset branches to silence an error. Validate hashes, detect external edits and fail closed if the state is ambiguous.

**Broken CI:** identify the exact failing SHA and workflow job, reproduce the failure when possible, fix the root cause and rerun. After two substantially identical unsuccessful attempts, record the failure and switch strategy; do not keep restarting identical jobs.

**Broken model/runtime:** collect runtime version, process exit code, bounded logs, selected model identity, memory availability, endpoint configuration and the smallest reproducible request. Separate missing prerequisites, timeout, unsupported hardware, memory pressure and application defects.

**Lost conversation or AI handoff:** do not rely on conversation history. Use the repository mission, the latest operational snapshot, the cumulative ledger, the issue/PR state and the code itself. Reconstruct any unsupported claim from evidence rather than treating notes as proof.

**Untrusted repository:** do not enable unrestricted host tests merely to make progress. Follow the currently supported containment matrix; unsupported presets are blocked until a verified safe runner is available.

### Measurable quality gates

| Gate | Required result |
| --- | --- |
| Mission preservation | Original rationale and constraints readable without chat history |
| Documentation continuity | Handoff and affected canonical docs updated in same meaningful checkpoint |
| Baseline integrity | Exact repo/branch/SHA recorded and reconciled with current GitHub state |
| Duplicate prevention | Existing source, issues, PRs and previous attempts checked |
| Evidence accuracy | Every PASS has executed evidence; unknowns marked UNVERIFIED |
| Recovery usability | Next step includes a runnable command or explicit access blocker |
| Resource discipline | No new unbounded allocation, polling or subprocess without rationale |
| Security | Credentials excluded; destructive operations and generated code guarded |
| Page minimum | Rendered handoff has >=35 substantive pages, counted and recorded |
| Freshness | Export states source SHA and date; stale exports visibly labeled |
| Takeover rehearsal | A second developer/agent can identify next safe task from repo alone |

### Release-export verification

A release export should be reproducible from repository text. Generate a PDF with readable body type, ordinary margins, navigable headings and a table of contents. Record **PDF SHA-256, source commit, generation command, date, actual page count, and any missing chapter**. Review a sample of pages and check for truncated tables or headings. Do not count empty pages, front matter or intentionally padded pages toward the 35 substantive pages. When the count fails, improve missing technical content rather than stretching layout.

**Export status at adoption:** NOT RUN. The 35-page threshold is an acceptance criterion, not an assertion that a validated PDF exists.

### Snapshot format to maintain at the top or end of the cumulative ledger

```text
FORGEOS CURRENT HANDOFF SNAPSHOT
Checked at:
Repo / branch / HEAD:
Product status:
Latest verified Linux / Windows / frozen EXE checks:
Real Windows + local-model acceptance:
Current implemented / experimental / planned differences:
Active issue / PR / owner:
P0 blockers:
Highest-priority unblocked task:
Exact next commands:
Expected acceptance evidence:
Rollback / recovery command:
Latest authoritative documentation links:
Handoff export source SHA / PDF pages / status:
```

The snapshot must not duplicate hundreds of historical entries. Link to the detailed cumulative record and maintain one source of truth for each topic.

### Documentation drift controls

Whenever code changes, ask whether the user guide, architecture, roadmap, product vision, feature status or Windows build guide has become inaccurate. Update only affected documents. Do not update the same information independently in several places when links would suffice. When a statement is contradicted by source or current CI, correct it and record the correction in HANDOFF.md.

**Required review before any handoff claim:** Does the new developer know why this project exists? What is working right now? What is still aspirational? Which commit was tested? Which tests were skipped? What should be run next? What must not be repeated? How can failures be recovered without losing user work?
