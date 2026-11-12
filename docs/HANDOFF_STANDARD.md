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
