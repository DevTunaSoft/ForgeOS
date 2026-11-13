# ForgeOS 52-enhancement duplication and ownership audit

**Scope:** all 52 entries in [RTK_ENHANCEMENTS.md](RTK_ENHANCEMENTS.md), compared against the actual ForgeOS source modules on `main` at `51f7e55fb2c6e5774931cff1ee0dd76981068fdc` and the pending PR #31 changes.

**Purpose:** avoid reimplementing what ForgeOS already has. The number 52 describes requested **outcomes**, **not 52 independent systems**, modules, or tickets. An existing basic implementation is not a claim that the production acceptance criteria are complete.

**Disposition summary:** 22 existing baselines, 13 extend-in-place items, 12 genuinely new capability gaps, 5 Phase 3 additions under review. **52 total, 52 mapped, zero omitted.** "Existing" means source exists; "extend" means an acceptance gap remains. PR31 capabilities are not in released main until merged.

## One owner per capability, without duplicate infrastructure

| Canonical owner | Scope | Shared enhancements |
|---|---|---|
| A | Raw evidence, parsing and compression | #1, #2, #7, #8, #26, #27, #28, #30, #33, #50 |
| B | Repository navigation | #3, #4, #5 |
| C | Model context budgets | #6, #22 |
| D | Trusted rules and transactional configuration | #9, #32, #35 |
| E | Optimization metrics and benchmarks | #10, #16, #17, #18, #36, #37, #41, #45 |
| F | Repair knowledge and learning | #11, #13, #14, #15, #29, #39 |
| G | Command safety and correction | #12, #31 |
| H | Local model routing and workers | #19, #20, #23, #24, #25, #40 |
| I | Development orchestration roles | #21 |
| J | Task resumption and recovery | #34, #52 |
| K | One integrated GUI | #38, #42 |
| L | QA, regression and Windows acceptance | #43, #44, #46, #47, #49, #51 |
| M | Licensing and attribution | #48 |

## Source-backed 52-item mapping

Dispositions: **EXISTING** = code already provides this baseline; **EXTEND** = reuse its module to close a gap; **NEW** = behavior not implemented; **PR31** = code is in draft review and CI, not yet merged. Source paths name the **canonical owner**, including for future additions.

| # | Enhancement | Disposition | Owner / source | Distinct acceptance or dedupe decision |
|---:|---|---|---|---|
| 1 | Intelligent output compression | **EXISTING** · A | `src/forgeos/diagnostics.py` | Existing lossless-by-recall output pipeline. Do not build another compressor. |
| 2 | Smart test summaries | **EXISTING** · A | `src/forgeos/diagnostics.py` | Already parses test counts and prioritizes error lines; #28 supplies parser. Add fixtures, not a second summarizer. |
| 3 | Optimized Git command output | **NEW at audit baseline** · B | `src/forgeos/workspace.py` | Since this historical baseline, the feature branch extends the existing shell=False Git wrapper with capped streamed output and marked bounded diff previews; no second Git client. |
| 4 | Relevant source code reading | **EXISTING** · B | `src/forgeos/context_index.py` | AST-based bounded file inventory already exists; add useful precision tests rather than a new index. |
| 5 | Optimized project search | **EXTEND** · B | `src/forgeos/context_index.py` | Existing ranked file/symbol inventory is not full code search. Extend context_index; do not introduce another scanner. |
| 6 | Adaptive context budgets | **NEW** · C | `src/forgeos/diagnostics.py` | Current 12k budget is fixed. Add validated per-model sizing in the existing diagnostics pipeline. |
| 7 | Context-aware compression | **EXTEND** · A | `src/forgeos/diagnostics.py` | Failure-aware filtering already exists; extend task- and model-aware policy within it. |
| 8 | Repeated log deduplication | **EXISTING** · A | `src/forgeos/output_rules.py` | Existing noncritical adjacent-line dedup. Preserve failures and raw evidence. |
| 9 | Configurable rules | **EXISTING** · D | `src/forgeos/output_rules.py` | Existing explicit hash-pinned literal rules. No plugin-based execution. |
| 10 | Token and savings analytics | **EXISTING** · E | `src/forgeos/optimizer_metrics.py` | SQLite byte counters exist. Do not count estimated bytes as measured tokens. |
| 11 | Persistent error learning | **EXISTING** · F | `src/forgeos/error_learning.py` | Persisted signatures and verified repair outcomes already exist. Never store raw secrets. |
| 12 | Automatic command correction | **NEW** · G | `src/forgeos/core.py` | Allowlisted commands exist but are not autonomously repaired. Any correction must stay in the allowlist. |
| 13 | Reusable repair knowledge | **EXTEND** · F | `src/forgeos/error_learning.py` | Current database stores counts, not executable repair recipes. Extend it with safe non-executable references. |
| 14 | Recurring failure pattern detection | **EXISTING** · F | `src/forgeos/error_learning.py` | Recurring pattern counts are part of #11's memory store; not a second learning subsystem. |
| 15 | Correction confidence scoring | **EXISTING** · F | `src/forgeos/error_learning.py` | Empirical success rate is already part of #11. Improve sample sizes and calibration in place. |
| 16 | Development session analysis | **EXTEND** · E | `src/forgeos/optimizer_metrics.py` | Aggregate metrics exist but no session lifecycle. Add session grouping to the existing DB. |
| 17 | Automatic optimization discovery | **NEW** · E | `src/forgeos/optimizer_metrics.py` | Opportunity detection must use existing metrics rather than a new telemetry collector. |
| 18 | Validated optimization improvements | **EXTEND** · E | `src/forgeos/optimizer_metrics.py` | Byte reduction exists; require benchmark evidence that repairs still work and test fidelity is maintained. |
| 19 | Complexity-aware model routing | **EXISTING** · H | `src/forgeos/model_routing.py` | Opt-in auto routing chooses installed local models. Preserve single local Ollama route. |
| 20 | Lightweight summarization workers | **NEW** · H | `src/forgeos/core.py` | Current Ollama call is direct. Add internal worker only when it improves measured results; no second application. |
| 21 | Separate planning/coding/testing/review roles | **EXTEND** · I | `src/forgeos/engine.py` | Plan/edit/test/repair workflow exists. Add role boundaries in the engine, not a separate orchestration app. |
| 22 | Dynamic context allocation | **NEW** · C | `src/forgeos/model_routing.py` | File-size model selection isn't dynamic prompt budgeting. Extend routing plus diagnostics. |
| 23 | Model performance benchmarking | **NEW** · H | `src/forgeos/model_routing.py` | No speed/quality benchmark yet. Collect on-device measures via existing metrics. |
| 24 | Automatic escalation | **NEW** · H | `src/forgeos/model_routing.py` | No automatic model escalation today. Add bounded policy to the existing router and checkpoint. |
| 25 | Free local-first routing | **EXISTING** · H | `src/forgeos/model_routing.py` | Local-first routing is already covered by #19. Do not implement another router. |
| 26 | Complete raw-output retention | **EXTEND** · A | `src/forgeos/diagnostics.py` | Full raw output is archived only up to 32MiB and command capture remains in memory; implement streaming/retention. |
| 27 | Retrieval of original diagnostic evidence | **EXISTING** · A | `src/forgeos/diagnostics.py` | Content-addressed retrieval already implemented. Do not introduce a second evidence store. |
| 28 | Structured/text/raw parsing tiers | **EXISTING** · A | `src/forgeos/diagnostics.py` | Structured/text/raw parser already exists (#2); extend golden fixtures, not duplicate parser. |
| 29 | Error classifications | **PR31** · F | `src/forgeos/error_learning.py` | Basic fingerprint categories already exist; PR31 adds advisory taxonomy within the same memory module. |
| 30 | SHA-256 diagnostic integrity | **EXISTING** · A | `src/forgeos/diagnostics.py` | SHA-256 validation implemented in existing archive. No new integrity service. |
| 31 | Safe command interpretation | **EXISTING** · G | `src/forgeos/core.py` | Preset allowlisting and shell=False Git/test execution exist. Extend rule audits, not a generic command interpreter. |
| 32 | Transactional configuration updates | **EXTEND** · D | `src/forgeos/transactions.py` | Journaled transactional edits exist; adapt proven mechanism to config changes, not another transaction engine. |
| 33 | Fail-open-to-raw on archive errors | **EXISTING** · A | `src/forgeos/diagnostics.py` | Raw result is returned if evidence archive fails; keep that invariant. |
| 34 | Checkpoint-linked evidence | **NEW** · J | `src/forgeos/engine.py` | Checkpoint saving exists but lacks diagnostic SHA reference; extend checkpoint format compatibly. |
| 35 | Trusted optimization rule verification | **EXISTING** · D | `src/forgeos/output_rules.py` | File-hash verification and explicit trust implemented in #9, not another rule verifier. |
| 36 | AI efficiency dashboard | **EXTEND** · E | `src/forgeos/gui.py` | Activity tab already shows efficiency totals. Build richer view in same GUI, not a second dashboard. |
| 37 | Development performance metrics | **EXTEND** · E | `src/forgeos/optimizer_metrics.py` | Attempts + total saved bytes exist; add run duration/session KPI into same store. |
| 38 | Raw vs summarized output comparison | **PR31** · K | `src/forgeos/insights.py` | PR31 adds side-by-side result and SHA-verified original view in existing GUI. |
| 39 | Learning activity view | **PR31** · F | `src/forgeos/gui.py` | PR31 adds read-only repair candidates in existing Activity tab, reusing #11. |
| 40 | Model routing visibility | **PR31** · H | `src/forgeos/engine.py` | PR31 records chosen model and displays it; extend with routing reason later. |
| 41 | Optimization health | **PR31** · E | `src/forgeos/gui.py` | PR31 exposes rule trust health and parser stats in existing Activity tab. |
| 42 | Unified settings | **EXTEND** · K | `src/forgeos/gui.py` | GUI already has execution settings; unify diagnostics/filters into same window. |
| 43 | Compression fidelity tests | **EXISTING** · L | `tests/test_diagnostics.py` | Compression fidelity tests exist; extend coverage without a second test runner. |
| 44 | Adversarial/fuzz diagnostics tests | **EXISTING** · L | `tests/test_output_rules.py` | Seeded fuzz/corruption/symlink coverage exists. Add new failure modes per cycle. |
| 45 | Before/after benchmarks | **NEW** · E | `src/forgeos/optimizer_metrics.py` | Existing byte estimates aren't reproducible timing/token/repair benchmarks. Add fixture corpus. |
| 46 | Full regression suite | **EXISTING** · L | `.github/workflows/ci.yml` | CI already discovers and executes unit/regression tests on both OSes. Expand suites rather than reimplement. |
| 47 | Containment regression tests | **EXISTING** · L | `src/forgeos/secure_smoke.py` | AppContainer boundary smoke and security tests already exist, including Windows packaged run. |
| 48 | Licensing and attribution audit | **NEW** · M | `pyproject.toml` | License policy review still outstanding; inspect dependencies and attribution, do not copy restricted RTK code. |
| 49 | Real Windows GUI and packaged acceptance | **NEW** · L | `.github/workflows/windows-exe.yml` | Packaged headless smoke exists; real user-installed GUI and Ollama integration remain unverified. |
| 50 | Structured parser golden-fixture tests | **EXTEND** · A | `tests/test_diagnostics.py` | Several inline parser examples exist; add standalone golden fixtures rather than another parser. |
| 51 | Cross-platform optimization tests | **EXISTING** · L | `.github/workflows/ci.yml` | Existing CI matrix runs optimizer tests on Linux and Windows. Expand specific platform edge cases. |
| 52 | End-to-end recovery exercise | **EXTEND** · J | `src/forgeos/recovery.py` | Resume-safe and recovery tests exist. Add actual process-crash rehearsal with checkpoint/evidence continuity. |

## Duplicate/overlap findings to enforce

1. **Diagnostics** — #1, #2, #7, #8, #26–30, #33 and the related tests are facets of one evidence/summary pipeline. Do not create a new archiver, parser or independent "error summarizer" for each.
2. **Repair learning** — #11, #13–15, #29 and #39 share `error_learning.py`. The earlier SHA fingerprint taxonomy and PR31's broad advisory categories are different representations; the classifier was consolidated into the existing module during the audit. Preserve legacy signatures and historical DB integrity. Do not create a second repair-history DB.
3. **Local AI** — #19, #20, #23–25 and #40 belong to the same model-routing subsystem. Use installed Ollama models, preserve the pinned checkpoint contract, and do not require external services or a second GUI.
4. **Metrics/UI** — #10, #16–18, #36–37, #41 and #45 must reuse the optimization metrics data model and current Activity pane. `saved_bytes / 4` is an estimate, not verified token savings.
5. **Command policies** — #12 is *not* the same as #31. The allowlisted `shell=False` runner already exists; automated correction is missing and must not bypass command policy or Windows AppContainer boundaries.
6. **Recovery** — #34 and #52 must extend the existing SQLite checkpoint + transactional edit/recovery mechanisms. Missing link: journal a retained evidence SHA and verify across interruption; do not build a second task queue.
7. **Tests** — #43–44, #46–47, #50–51 must add cases to the same CI/regression suite. The Linux/Windows Python 3.10/3.12 matrix and frozen AppContainer smoke already exist. These are not separate testing frameworks to implement.
8. **Configuration** — #9 and #35 are one trusted-rule implementation. #32 needs an extension to the existing journaled transaction design, not another file-edit subsystem.
9. **GUI** — #36 and #38–42 stay inside the existing Tkinter app. Existing settings, metrics and diagnostics should be enhanced instead of adding apps or duplicate tabs.

## Non-negotiable integration gates

- Before a new subsystem is proposed, check its canonical owner and the paths above, then record the missing acceptance behavior and regression test.
- "Existing" or a checked roadmap item is **not** proof of real Windows runtime quality, safe unattended execution, full fuzz coverage, or production release.
- A new library/module is justified only if it provides a separate responsibility; reuse existing persistence, test runner, evidence archive, workspace, and GUI.
- New model choices must remain free/local by default; no mandatory Docker, subscriptions, or external model APIs.
- Preserve Windows fail-closed AppContainer security and host-test opt-in. Never route new tests through a weaker runner to improve speed.
- Preserve original test exit codes and evidence. Never hide critical output or overwrite historical checkpoints during upgrades.
- Mark work complete only with a linked code change and repeatable automated/real-machine acceptance appropriate to the claim.

## Next implementation order after PR #31

1. **#34 + #52**: checkpoint-linked evidence references and restart/crash recovery validation (existing engine/recovery; no new task queue).
2. **#26**: bounded streaming output capture, archive retention policy, integrity and privacy safeguards (existing diagnostics).
3. **#6 + #22**: per-model bounded context budgets (existing model router and diagnostics).
4. **#3 + #5**: repository Git output and search/context improvements using existing workspace and index.
5. **#23 + #24 + #45**: measured on-device model benchmarking, escalation only after validated tests, regression-safe routing policy.
6. **#49**: interactive Windows GUI/Ollama/full task acceptance remains a release gate. Preserve AppContainer.

**Audit limitations:** This is a source-level functional overlap review. It is not formal proof of architecture uniqueness, absence of race conditions, or validation of all Windows installations. CI successes provide regression evidence only for the checks that ran. Features marked EXTEND and NEW still require implementation.


## PR #51 re-audit — 2026-11-08

A fresh comparison of the current multi-LLM branch, the open implementation issues (#53–#56), the last 30 commits and this ownership map found **no justification for another local-model subsystem, downloader, GUI, router, recovery engine, test framework or model database**.

### Capabilities that already exist on PR #51 and must not be reimplemented

- Local OpenAI-compatible adapters for llama.cpp / LM Studio / loopback endpoints: extend `local_models.py`.
- Hugging Face discovery and resumable verified GGUF transfer: extend `huggingface_catalog.py` and `gguf_download.py`.
- Managed CPU-first llama.cpp lifecycle, runtime verification and Windows Job Object containment: extend `managed_llama.py` and existing `windows_containment.py`.
- Adaptive RAM/context preflight: extend `model_routing.py`; do not create another router.
- GGUF lifecycle/recovery: extend `model_inventory.py` and `model_retirement.py`; do not create a second inventory database.
- GUI model controls: remain inside the existing Tk GUI.
- Autonomous task recovery: remain in the existing Store/engine/recovery/transaction pipeline.

### Remaining work that is genuinely new acceptance work

1. Real Windows llama.cpp + GGUF execution and full plan/edit/test/recovery acceptance.
2. Process-tree memory measurement/governance and measured model benchmarks.
3. Windows reparse/junction and native filesystem acceptance not covered by current source-level link checks.
4. Real LM Studio/other loopback endpoint compatibility runs.
5. Model metadata/license/suitability UX and safe import/update/repair behavior not already covered by retirement/restore.
6. Physical Windows accessibility, installer/use walkthrough and release evidence.

### Mandatory anti-duplication gate

Before implementation, compare the proposed behavior against:
(1) this file's canonical owner table, (2) `MULTI_LLM_ROADMAP.md`, (3) open issues #53–#56, and (4) recent commits on the active branch. If a source owner already exists, improve it in place. A new module is allowed only for a distinct responsibility that cannot be cleanly owned by an existing module. Repeated hardening of the same risk without a newly identified failure mode or acceptance criterion is not a roadmap item.
