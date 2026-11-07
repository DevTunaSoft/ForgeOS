# ForgeOS / RTK integration: 52-enhancement implementation program

Status: **in progress**. Items here are scope commitments, not claims of finished implementation.
The intended application is a single local-first ForgeOS Windows app; no Docker, paid APIs or second interface required.

## AI efficiency & token optimization (10)

- [x] Intelligent output compression (prototype integrated)
- [ ] Smart test summaries
- [ ] Optimized Git command output
- [x] Relevant source code reading (bounded AST metadata prototype)
- [ ] Optimized project search
- [ ] Adaptive context budgets
- [ ] Context-aware compression
- [x] Repeated log deduplication (noncritical run collapse)
- [x] Configurable rules (optional pinned JSON literal rules)
- [x] Token and savings analytics (local aggregate prototype)

## Learning & autonomous improvement (8)

- [x] Persistent error learning (test-verified signatures only)
- [ ] Automatic command correction
- [ ] Reusable repair knowledge
- [x] Recurring failure pattern detection (fingerprint baseline)
- [x] Correction confidence scoring (review threshold baseline)
- [ ] Development session analysis
- [ ] Automatic optimization discovery
- [ ] Validated optimization improvements

## Intelligent local AI orchestration (7)

- [x] Complexity-aware model routing (opt-in installed Ollama models)
- [ ] Lightweight summarization workers
- [ ] Separate planning/coding/testing/review roles
- [ ] Dynamic context allocation
- [ ] Model performance benchmarking
- [ ] Automatic escalation
- [x] Free local-first routing (installed models only; opt-in)

## Reliability, diagnostics & recovery (10)

- [ ] Complete raw-output retention (bounded prototype integrated)
- [x] Retrieval of original diagnostic evidence (prototype integrated)
- [x] Structured/text/raw parsing tiers (baseline implemented)
- [x] Error classifications (initial taxonomy)
- [x] SHA-256 diagnostic integrity (prototype integrated)
- [ ] Safe command interpretation
- [ ] Transactional configuration updates
- [x] Fail-open-to-raw on archive errors (prototype integrated)
- [ ] Checkpoint-linked evidence
- [x] Trusted optimization rule verification (content hash pinning)

## ForgeOS GUI & visibility (7)

- [x] AI efficiency dashboard (basic activity tab totals)
- [ ] Development performance metrics
- [ ] Raw vs summarized output comparison
- [ ] Learning activity view
- [ ] Model routing visibility
- [ ] Optimization health
- [ ] Unified settings

## QA, performance & security (10)

- [x] Compression fidelity tests (initial suite added)
- [x] Adversarial/fuzz diagnostics tests (initial suite added)
- [ ] Before/after benchmarks
- [ ] Full regression suite
- [ ] Containment regression tests
- [ ] Licensing and attribution audit
- [ ] Real Windows GUI and packaged acceptance
- [ ] Structured parser golden-fixture tests
- [ ] Cross-platform optimization tests
- [ ] End-to-end recovery exercise

## Implemented baseline: diagnostic output archive

The source test runner and Windows contained runner now return complete captured output to the test-result optimizer rather than silently keeping the final 16,000 characters. The optimizer archives raw UTF-8 evidence first in an atomic SHA-256-keyed gzip file, then gives the local AI a bounded summary with an on-demand recall reference.

Retrieve evidence by running:

    python -m forgeos diagnostics show <64-character-sha256>

The result retains the authoritative exit code, passed/failed value, and containment attributes. When archiving fails, it returns uncompressed output. An evidence reference is verified by SHA-256 during retrieval. This is only a start: archives are bounded at 32 MiB per command and the process still captures output in memory.

Threat model and release restrictions:
- Raw test output may contain secrets. Never upload the private evidence folder to GitHub or include it in release exports.
- Files have owner-only permissions on Unix. Windows ACL hardening and at-rest encryption remain future work.
- Compression ratios are output-byte estimates, not token or time savings.
- On Windows, execution isolation remains governed by the separate AppContainer PR and its acceptance tests.
- New code is not release-ready until CI, Windows packaging and interactive acceptance are verified.

Next: structured parsers for common test frameworks; chunked output capture and disk retention policy; learned failure signatures; model-routing evidence; GUI metrics.

## Phase 1 additional integration (pending final CI)

Source modules: src/forgeos/diagnostics.py, src/forgeos/error_learning.py, src/forgeos/optimizer_metrics.py. Test modules: tests/test_diagnostics.py, tests/test_error_learning.py, tests/test_optimizer_metrics.py. Verified repair outcomes are persisted only as normalized SHA-256 signatures, classification, model/preset labels and numerical counters; never store original log text in the learning database. No automatic application of past fixes is enabled.

CLI commands: python -m forgeos diagnostics stats; python -m forgeos learning stats. GUI Activity & diagnostics tab includes a button to view local aggregate efficiency and validated repair counts.

Windows CI uncovered leaked SQLite connections; the connection managers now close handles explicitly after every transaction. Treat these fixes as pending verification until all required CI jobs pass on the final head commit.

## Additional development cycle: source context + local model routing

- src/forgeos/context_index.py: deterministic, ranked file metadata limited to
  80 files and 8000 characters, with Python AST class and function signatures.
  No values, source bodies, comments, hidden paths, .env files, or symlinks
  are copied into the model prompt.
- src/forgeos/model_routing.py: opt-in --model auto chooses from local Ollama
  /api/tags on 127.0.0.1 only. No paid provider, downloads, or external API.
  Code-model preference, task complexity, deterministic size ranking, and a
  conservative 16 GiB model file limit. File size is NOT actual RAM usage.
- The engine fixes a chosen model into the checkpoint execution contract
  before generating edits, so resumption checks the same identity.
- Test modules added: test_context_index.py, test_model_routing.py.
- Repair-memory accounting now records failed repair attempts as well as
  successful verified corrections, so confidence estimates are not inflated.

Remaining work includes actual project-source context with explicit privacy
controls; CI proof for the latest commit; trusted filter rules; robust model
availability/performance probes; dynamic escalation; bounded output streaming;
Windows ACL/encryption; and full real-user Windows acceptance.


## Phase 2: trusted output rule integration

Diagnostic output is archived before adjacent noncritical duplicates are
collapsed. Lines containing errors, failures, assertions, warnings or test
summaries remain untouched. Custom JSON rules are opt-in, use literal matching,
and require the user to pin their SHA-256 hash. Modified and untrusted files
are skipped. No regex or executable commands are accepted.

Example local config ~/.forgeos/output_filters.json:

    {"version":1,"rules":[{"preset":"npm-test","contains":"Progress","min_repeats":3}]}

Use: python -m forgeos filters status
Use: python -m forgeos filters trust

The trust record is under ~/.forgeos/trusted_output_filters.json. This
provides user consent and tamper evidence, not a same-user security boundary.
No optimization is allowed to overwrite the original diagnostic archive or
the test exit status.
