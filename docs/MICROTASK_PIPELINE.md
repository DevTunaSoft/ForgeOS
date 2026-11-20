# Microtask pipeline: P0 design specification

**Status: design specification. Planned as a whole.** The pure validation substrate is implemented and Linux-verified: [`task_graph.py`](../src/forgeos/task_graph.py), [`requirement_trace.py`](../src/forgeos/requirement_trace.py) and [`repo_inventory.py`](../src/forgeos/repo_inventory.py). The engine, store and GUI do not use them yet. Decomposition, persistence, scheduling, integration and the GUI are **not started**. The P0 Windows baseline is **NOT RUN**. This document describes intent and contracts. It does not claim that any behavior exists beyond the modules named above.

Requirement source: [ROADMAP.md](ROADMAP.md), "Planned: Microtask Development Pipeline".

## 1. Purpose

A large application request or an existing repository becomes small, dependency-aware tasks that a resource-constrained local coding model can complete and verify one at a time. The pipeline extends the existing task engine, SQLite store, worktrees, leases, journals, recovery and evidence systems. It must not add a parallel orchestration stack, a second router or a remote service.

## 2. Versioned contracts

Every document carries `schema_version`. Version 1 is the only version defined. Unknown keys are refused at every level, and no contract accepts code, shell commands or paths outside the repository.

### 2.1 Task graph (`task_graph.py`, schema version 1)

Top level: exactly `schema_version` (1) and `tasks`. Each task has exactly `id`, `prerequisites` and `files`.

| Item | Rule |
| --- | --- |
| Task count | 1 to 256 |
| Identifier | `[a-z0-9][a-z0-9._-]{0,63}`, unique |
| Prerequisites | at most 16 per task; each must name an existing task; no self-dependency |
| Files | at most 8 per task; relative POSIX paths of at most 240 characters; no traversal, absolute path, backslash, colon, NUL, Windows device stem, trailing dot or trailing space; no duplicate within one task (casefolded) |
| Ordering | acyclic; the returned order is deterministic (ready tasks are taken in identifier order) |
| Conflicts | two tasks that edit the same file, compared case-insensitively, must be ordered by a dependency path in either direction |

Validation runs before anything is scheduled. The path rules reuse `portable_names.py`, so planner output meets the same policy as an edit manifest.

### 2.2 Requirement traceability (`requirement_trace.py`, schema version 1)

A separate document links tasks to requirements, so the graph contract stays unchanged.

| Item | Rule |
| --- | --- |
| Requirement count | 1 to 128 |
| Identifier | `REQ-` followed by three or four digits, unique |
| Statement | non-blank, at most 500 characters |
| Links | at most 1,024; each names a known task and a known requirement; no duplicate pair |
| Coverage | every requirement is advanced by at least one task |

### 2.3 Repository inventory (`repo_inventory.py`, schema version 1)

Output: `schema_version`, `files` (each with `path`, `bytes`, `kind`, `test`, `manifest`), `total_candidates`, `scan_exhausted` and `truncated`.

| Item | Rule |
| --- | --- |
| Records | at most 2,000, sorted by casefolded path |
| Scan | at most 3,000 file entries, the same budget `context_index` uses |
| Content | metadata only; no file bodies, no absolute paths, no root name |
| Exclusions | the walker shared with `context_index` (excluded folders in any letter case, dot entries, symlinks, `.env*`, oversized relative paths) |

## 3. Threat model

| Threat | Mitigation in the contract | Status |
| --- | --- | --- |
| Planner output is untrusted and may be malformed, cyclic, or aimed at protected paths | strict schemas; refusal before scheduling; path rules shared with edits; the engine's protected-folder policy still applies at write time | graph and traceability validation implemented; engine integration planned |
| Repository content is untrusted and can contain instruction-like text | the inventory is metadata-only; context keeps the existing "untrusted code is not instructions" framing; no body is copied into the inventory | inventory implemented; prompt integration planned |
| Path tricks: traversal, case aliasing, device names, trailing dots and spaces, symlink escape | `portable_names` predicates; the walker skips symlinks; edits re-check the resolved location | implemented for validation and the walker |
| Resource exhaustion from very large graphs, file lists or statements | fixed bounds in sections 2.1 to 2.3; validation allocates within those bounds | implemented and measured for the substrate |
| Duplicate or concurrent execution of one microtask | planned: reuse the existing lease and fencing rules; store the graph version and content hash with each task before it runs | planned |
| Secrets in the repository reaching a model | existing `.env`, dot-entry and excluded-folder rules; the inventory lists names, so it stays local and is never sent to a remote service | rules implemented; no remote provider is part of this path |
| Stale checkpoint resumed against a changed graph | planned: resume only from a checkpoint whose graph version and hashes match the stored record | planned |

## 4. Scheduling invariants (planned, phase P3)

1. A task starts only after every prerequisite has a verified passing result, not merely a returned result.
2. At most one heavy resident model runs at a time by default (ROADMAP requirement 10).
3. Tasks that edit the same file never run concurrently. The graph validator already refuses unordered conflicts.
4. Each task's worktree is created from the verified state of its prerequisites. Integration follows graph order.
5. Repairs per task are bounded at 2 to 3. The engine already stops an exactly repeated repair (2026-11-20 entry in [ROADMAP.md](ROADMAP.md)).
6. An interrupted run resumes only from a validated checkpoint whose graph version and hashes match.
7. Every blocked or failed state records its reason and its evidence reference, and no state reports success without its acceptance evidence.

## 5. Phase gates

| Phase | Content | Status |
| --- | --- | --- |
| P0 | Design and baseline: this specification, plus a whole-prompt baseline on the target Windows laptop | Specification written. Baseline **NOT RUN** (needs hardware) |
| P1 | Deterministic substrate: validation, traceability, bounded inventory, synthetic fixtures, and persistence with migration tests | Validation, traceability, inventory and synthetic fixtures implemented. Persistence **not started** |
| P2 | Local-model decomposition: strict proposals validated by section 2, deterministic context retrieval, and quality checks against known requirements | **Not started** |
| P3 | Safe execution and integration: worktrees, leases, test and repair, checkpoints, dependency-aware scheduling, and crash and restart injection | **Not started** |
| P4 | Hardware acceptance on the 20 GB and 32 GiB machines: whole-prompt versus microtask comparison | **Not started** (needs hardware) |

Exit criteria are those in [ROADMAP.md](ROADMAP.md): a large representative project can be decomposed, executed, tested, integrated, interrupted and resumed without losing requirement traceability or corrupting source, within measured machine budgets.

## 6. Measurement plan (P0 and P4)

Run the same fixed set of representative tasks two ways: as one whole-project prompt, and as microtasks. Record completion time, prompt and evaluation latency, peak process-tree memory, edit validity, regression pass rate, rework count, recovery correctness and final artifact quality. Follow the scope rules in [MEMORY_BUDGET.md](MEMORY_BUDGET.md): record which process was measured, and never present a Python-only measurement as process-tree memory. A fast response that never produces a verified change is not a success.

## 7. Open decisions for the owner

1. **Persistence.** Store the validated graph and traceability in new SQLite tables with versioned migration and fault-injection tests (matching the existing store pattern), or in a single versioned checkpoint field. The first option is recommended.
2. **Model-proposed links.** A decomposer's requirement links are model output, so they must pass the same validation as any other output. This needs no decision, but the owner should confirm it.
3. **Inventory exposure.** Whether the decomposer receives the full metadata inventory (up to 2,000 records) or only a relevance-ranked slice. The relevant slice is the smaller and safer default.

## 8. Non-goals

No cloud decomposition or remote model call without explicit consent. No unrestricted model-generated shell commands. No Docker. No second GUI or framework. No background service. No claim of general-purpose application building.

## 9. Evidence for this document

Documentation only. No test was added for this specification, and no Windows measurement or model run was made. The modules named in the status line are covered by their own tests, which are listed in [HANDOFF.md](HANDOFF.md).
