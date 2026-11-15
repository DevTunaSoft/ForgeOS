# ForgeOS engineering documentation index

This is a navigation aid, not a second source of truth. Check the live branch, source, tests, CI, issues, and PRs: historical documentation can be stale. Use the specific documents below rather than repeatedly loading the full cumulative handoff.

## Fast orientation

1. [`AGENTS.md`](../AGENTS.md) — coding-agent workflow and safety rules.
2. [`README.md`](../README.md) — product summary, current capabilities, setup, and limitations.
3. [`PRODUCT_VISION.md`](PRODUCT_VISION.md) and [`REQUIREMENTS.md`](REQUIREMENTS.md) — mission, constraints, non-goals, and target behavior.
4. [`ROADMAP.md`](ROADMAP.md) — implementation status, priorities, and acceptance gates.
5. [`ENGINEERING_TAKEOVER.md`](ENGINEERING_TAKEOVER.md) — system rationale, design decisions, and takeover procedure.

## Find the right source

| Work area | Read first | Then inspect |
|---|---|---|
| Architecture, trust boundaries, module ownership | [`ARCHITECTURE.md`](ARCHITECTURE.md) | Owning `src/forgeos/` module and its tests |
| Duplicate-work and optimization proposals | [`ENHANCEMENT_DEDUP_AUDIT.md`](ENHANCEMENT_DEDUP_AUDIT.md), [`RTK_ENHANCEMENTS.md`](RTK_ENHANCEMENTS.md) | Canonical owner listed in the audit; live issues/PRs |
| GUI, CLI, user-visible workflow | [`USER_GUIDE.md`](USER_GUIDE.md) | `src/forgeos/gui.py`, `cli.py`, relevant contract/integration tests |
| Windows executable, test containment, release evidence | [`WINDOWS_BUILD.md`](WINDOWS_BUILD.md) | `.github/workflows/windows-exe.yml`, launcher, security tests |
| Memory, performance, model RAM | [`MEMORY_BUDGET.md`](MEMORY_BUDGET.md) | Existing telemetry/benchmark owner and tests; distinguish targets from measurements |
| Ollama, local endpoints, GGUF, model lifecycle | [`MODEL_COMPATIBILITY.md`](MODEL_COMPATIBILITY.md), [`MULTI_LLM_ROADMAP.md`](MULTI_LLM_ROADMAP.md) | `core.py`, `local_models.py`, `model_routing.py`, model lifecycle modules and tests |
| Tasks, edits, journals, database backup, or recovery | [`ARCHITECTURE.md`](ARCHITECTURE.md), [`DATABASE_BACKUP.md`](DATABASE_BACKUP.md), relevant [`USER_GUIDE.md`](USER_GUIDE.md) sections | `core.py`, `transactions.py`, `engine.py`, `recovery.py` and focused tests |
| CI, test policy, documentation changes | [`CI_POLICY.md`](CI_POLICY.md), [`DOCUMENTATION_POLICY.md`](DOCUMENTATION_POLICY.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md) | `.github/workflows/`, `tools/check_documentation.py`, tests |
| Historical rationale, failed approaches, evidence, next steps | [`HANDOFF.md`](HANDOFF.md) | Search for the relevant heading/date and read bounded sections |
| Handoff completeness/publication | [`HANDOFF_STANDARD.md`](HANDOFF_STANDARD.md) | `scripts/check_handoff.py`, snapshot/takeover tools, publication workflow |

## Efficient handoff review

`HANDOFF.md` is the append-only chronological record and is very large. For routine tasks, search it for the relevant subsystem, issue, failure, or latest checkpoint, then read the matching bounded ranges. For an explicitly required full review, read every section sequentially and keep a path/size/line-count ledger; do not describe a partial scan as a full read.

Architecture and decision rationale is recorded in `ENGINEERING_TAKEOVER.md` (including its foundational decisions and ADR guidance) and dated `HANDOFF.md` entries. Check the live GitHub issue/PR and branch state before relying on old PR numbers or status statements.

## Documentation and verification rules

- [`DOCUMENTATION_POLICY.md`](DOCUMENTATION_POLICY.md) defines the source of truth and which docs to update. Code and verified tests/CI outrank historical prose; correct stale claims without erasing history.
- `README.md` and `ROADMAP.md` are the primary current feature/status views; `USER_GUIDE.md` is the operational manual. Update applicable documents in the same PR.
- Standard local gates: `py tools/check_documentation.py --links`, `py scripts/check_handoff.py --json`, and `py -m unittest discover -s tests -v`. Also run focused tests and relevant Windows/Linux packaging or security workflows; state clearly when a gate is not run.
- Record exact commands, platform, SHA, actual PASS/FAIL/SKIP/BLOCKED/NOT RUN results, limitations, and next steps. Hosted CI does not establish physical Windows or real-model acceptance.
