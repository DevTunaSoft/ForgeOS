# ForgeOS — Multi-LLM and intelligent model management engineering roadmap

**Canonical scope:** [ForgeOS engineering roadmap](ROADMAP.md) · [Model compatibility and setup](MODEL_COMPATIBILITY.md) · [Architecture and security](ARCHITECTURE.md) · [Full development handoff](HANDOFF.md) · [52 RTK-inspired enhancements](RTK_ENHANCEMENTS.md) and [deduplication audit](ENHANCEMENT_DEDUP_AUDIT.md).

**Audit baseline (refreshed 2026-11-11):** multi-LLM PR [#51](https://github.com/DevTunaSoft/ForgeOS/pull/51) was merged into `main` (merge commit `7c582f8`). The previous draft-branch head `f7241a3` and hosted CI evidence `67c2e74` are historical branch evidence, not current-main verification. Hosted CI is regression evidence only and is not proof of real llama.cpp, LM Studio, multi-GiB downloads, model quality, physical Windows stability or production readiness.

**Branch relationship snapshot (2026-11-13 at `f6a0e32`):** at that point,
`fix/safe-resume-malformed-checkpoints` was 72 commits ahead of `origin/main`
(`4a24799`), which was its ancestor. Newer follow-ups in this handoff are
available on the GitHub feature branch; they must not be described as merged
into `main` until a reviewed merge occurs.

This is a **requirements-to-evidence tracker, not a declaration that all capabilities are built**. Every row below carries a source owner, a status and an observable exit gate. Existing capabilities are extended in place; no second desktop application, duplicated task engine, duplicate database or parallel model selection subsystem.

## Nonnegotiable product constraints

- **One ForgeOS GUI** and beginner-friendly plain-language workflow; no mandatory Docker, paid APIs, cloud inference, subscriptions or separate model-manager GUI.
- Free local inference, no unapproved network access to project data. Hugging Face repository discovery and GGUF downloads are **optional user-initiated** network functions; once installed, inference works without internet.
- Favor the smallest sufficient coding model and bounded contexts. **At most one heavy resident inference model** by default; never assume model file size equals peak RAM.
- Windows 11 x64, CPU-first Intel Core i7-1370P / 32 GiB / integrated graphics is the primary acceptance target. Evaluate 16 GiB, 8 GiB and reduced-memory interface-only configurations **without claiming support before measured evidence**.
- Preserve canonical `core.Ollama`, `model_routing.py`, `engine.execute_task`, `task_actions.py`, `Store` SQLite task/lease state, `recovery.py`, existing transaction journals, `gui.py`, isolated worktrees, GitHub CI and fail-closed Windows AppContainer test execution.
- Do not treat untrusted model responses as commands. Transaction validation, lease fencing, test isolation and explicit publication permissions must remain intact.
- Never declare completion simply because code exists. **Implemented prototype**, **in progress**, **planned** and **verified on target Windows hardware** are distinct.
- Maintain source, tests, documentation and a recoverable handoff with every substantive change, including honest negative results.

## Status definitions and GitHub source evidence

- **Existing on main:** present in canonical `main`, within its documented limits; runtime acceptance may still be missing.
- **Experimental on main (merged PR #51):** code integrated into `main`; historical hosted CI is not physical Windows or production acceptance.
- **Partial:** initial implementation exists but an acceptance requirement, integration or safety gate is absent.
- **Planned:** not implemented; do not label as available.
- **Unverified:** a behavior, compatibility statement or performance figure without direct reproduced evidence on the named system.

| Capability | GitHub source or owner | Status | Next acceptance evidence |
| --- | --- | --- | --- |
| Existing Ollama plan/edit/repair client | `src/forgeos/core.py`, `engine.py` | Existing on main | Physical Windows end-to-end real model |
| Ollama installed models and RAM-aware heuristic | `src/forgeos/model_routing.py` | Existing on main | Measured runtime/headroom and accuracy |
| llama.cpp OpenAI-compatible adapter | `src/forgeos/local_models.py` | Experimental on main (merged PR #51) | Real local llama-server / model with structured edits |
| LM Studio and custom local OpenAI-style adapters | `src/forgeos/local_models.py` | Experimental on main (merged PR #51) | Real endpoint compatibility tests, no remote fallback |
| Public Hugging Face GGUF catalog and metadata | `src/forgeos/huggingface_catalog.py` | Experimental on main (merged PR #51) | Live API, model card/license, pagination, failure handling |
| Explicit pinned GGUF download, SHA-256, resume | `src/forgeos/gguf_download.py` | Experimental on main (merged PR #51) | Multi-GiB real download, interrupted resume, disk limits, Windows paths |
| User GUI backend/endpoint selection and HF browser/download | `src/forgeos/gui.py` | Experimental on main (merged PR #51) | Manual accessible GUI exercise with real backend/HF model |
| Backend-aware checkpoint recreation | `src/forgeos/recovery.py`, `engine.py` | Experimental on main (merged PR #51) | Crash/reboot, file/model identity and lease safety |
| Native managed llama.cpp CPU runtime | `src/forgeos/runtime_installer.py`, `managed_llama.py`, existing GUI | **Partial experimental on main:** pinned official Windows x64 CPU runtime installer, archive/DLL re-verification, upstream llama.cpp MIT and LLVM OpenMP notices, SHA-verified GGUF loading, authenticated loopback server, GUI start/stop, POSIX process-group cleanup and Windows suspended Job Object launch are implemented; other component attributions and real native GGUF inference remain unverified | Real Windows install → load → infer → stop/restart, with measured RAM/latency, dependency notices and process identity evidence |
| Installed GGUF lifecycle and profiles | `src/forgeos/model_inventory.py`, `model_retirement.py`, existing downloader/GUI | **Partial experimental on main:** bounded offline inventory, explicit SHA reverify, resumable downloads and reversible retirement/restore exist without a second database; safe import/update/repair and richer status UX remain incomplete | Real Windows lifecycle plus import/update/repair and active/checkpoint protection |
| Role-aware multi-model routing | Extend `model_routing.py` and engine orchestration | Planned | Repeatable task-level quality/performance benefit |
| RAM/process-tree budget and resource governor | Extend `model_routing.py` + managed runtime | Planned | Actual process-tree RSS/commit sampling and OOM failover |
| Fully autonomous development with multiple models | Existing engine + recovery + leases | Partial | Full plan/edit/test/review/recovery cycle on real runtimes |
| Hardware benchmarks and release acceptance | CI, benchmark scripts and evidence docs | Unverified | Measured target laptop, model hashes, build evidence |

## Work package A — audit, dependency boundaries, compatibility

**Owner:** canonical `core.Ollama`, `model_routing.py` and `local_models.py`.

- [x] Inventory existing Ollama client, deterministic routing, installed-model discovery, local memory profiles, GUI/CLI entry points and checkpoint contract.
- [x] Preserve local Ollama path and existing SQLite task lifecycle while adding **experimental** local OpenAI-compatible endpoints.
- [x] Implement fixed loopback-literal endpoint validation, disable proxies and redirects, cap catalogs/prompt/response, test malformed responses.
- [x] Make source-level llama.cpp and LM Studio adapters selectable in the same GUI.
- [ ] Verify each adapter against a **real** compatible server, including `/models`, `/chat/completions`, chat templates, JSON edit manifests, timeout and server version differences.
- [x] **Partial explicit token-limit profile:** the existing adapter can select `max_tokens` or `max_completion_tokens` in GUI/CLI and persists non-default choices in run checkpoints for recovery. Other server/version differences and live compatibility tests remain open.
- [ ] Maintain an explicit adapter capability matrix: tools, JSON schema, context/token limits, model file types, license and backend versions.
- [x] **Partial source-level matrix:** `docs/MODEL_COMPATIBILITY.md` now lists current ForgeOS request contracts, tool/schema behavior, known versus server-dependent context limits, file/license provenance and available version identity. Real backend/version compatibility profiles and measured limits remain open.
- [ ] Prove remote endpoints, loopback impersonation where detectable, proxy bypass, redirect changes and accidental cloud API credentials cannot leak project content.

**Exit:** measured endpoint feature matrix, real code-edit acceptance and no Ollama regressions on Windows.

## Work package B — integrated inference engine, no Docker

**Owner:** one new minimal managed-runtime module behind `local_models.create_model` or its successor; do **not** embed another GUI.

- [x] **Experimental implementation:** a specific official llama.cpp Windows x64 CPU release is pinned by tag, asset name, size and SHA-256 in `runtime_installer.py`; the exact upstream MIT notice is bundled into ForgeOS and installed beside the runtime with hash checks. The official b12199 Windows workflow enables LLVM OpenMP and its pinned license; installer acceptance now requires that notice in the ZIP and checks its SHA-256. Other embedded component attributions and physical installer acceptance remain open.
- [x] **Experimental implementation:** explicit GUI install downloads only the pinned official GitHub asset, disables proxies, restricts redirects, verifies upstream release metadata plus SHA-256/size, safely extracts, retains the source archive and re-verifies installed executable/DLL bytes before use. This is checksum/provenance hardening, not a vendor-signature claim.
- [x] **Experimental source path:** launch a user-provided `llama-server` with argument-array subprocess execution, explicit binary/model paths, random available local port, no shell and **localhost-only** bind.
- [ ] Prove port ownership and harden child process-tree cleanup on Windows. **Experimental owner check implemented:** startup verifies the IPv4 loopback listener PID against the launched child using Windows IP Helper or Linux `/proc`; managed model API calls check ownership before sending prompts and again before accepting responses. This narrows port takeover risk but does not bind the HTTP connection atomically to a process; live Windows IP Helper access is denied in the current managed session (error 5), so target-host ownership and race behavior remain unverified.
- [ ] Verify that the listener is the expected child process and version before passing project prompts; protect against port takeover/rebinding.
- [x] **Experimental source path:** one GUI-owned runtime lifecycle controller, authenticated readiness request, timeout, controlled stop and application shutdown.
- [x] **Experimental implementation:** reuse ForgeOS's Windows Job Object structs to attach managed llama-server with kill-on-close, and use a POSIX process group for stop/timeout cleanup.
- [x] **Experimental cross-process model lifecycle lock:** managed startup acquires the same OS-backed per-GGUF lock used by download and retirement before re-verifying weights, and holds it until child exit is confirmed. Retirement also scans blocked checkpoints by exact model SHA and fails closed when a checkpoint cannot be inspected. External llama-server processes and physical Windows lock/termination behavior still require acceptance.
- [x] Experimental Windows launch now creates llama-server suspended, assigns the kill-on-close Job Object before any child instruction can run, then resumes it. Hosted Windows integration must still verify the latest SHA; real llama.cpp child-tree/model reload/reboot acceptance remains required.
- [x] **Experimental source path:** allow manually selecting a SHA-verified GGUF and user-provided llama-server binary from the existing GUI without launching LM Studio.
- [ ] Make the binary and model installation fully turnkey, with official binary provenance and actual Windows CPU inference verified.
- [ ] Separate GPU/CPU capability detection from model selection; CPU-only must always remain usable even without a compatible GPU.
- [ ] No silent fallback to host-executed project tests, cloud inference or untrusted binaries.

**Exit:** from one ForgeOS window, select a downloaded GGUF, start managed inference, generate a valid plan/edit, stop/unload model and recover after killing the server.

## Work package C — Hugging Face model management and provenance

**Owners:** `huggingface_catalog.py`, `gguf_download.py`; add one canonical on-disk inventory; no duplicate downloader.

- [x] Read public repository metadata and GGUF file/size/hash candidates on explicit user request.
- [x] **Partial bounded search pagination:** the GUI follows opaque Hub-provided next links only when the origin/path and original query/page size validate; each continuation fetch is an explicit user action. Hub JSON responses also have a 2 MiB cap and 30-second total deadline. Live API and failure-mode acceptance remain open.
- [x] Resolve immutable repository revision for opt-in downloads; require trusted LFS hash; stream bounded chunks, reserve disk, retain partial journals, reject incompatible resumes and atomically publish verified bytes. The downloader also enforces a bounded total duration (default 24 hours, validated maximum) alongside the per-socket timeout, checking around reads and during hashing so slow-trickle responses cannot run forever; the deadline also covers the shared-quota lock wait, where cooperative pause is honored. Already committed partial chunks remain resumable after expiry or pause.
- [ ] Real Windows checks on LFS/Xet/CDN redirects, TLS, anti-abuse, 401/403/404/429, disk-full mid-write and physical acceptance. **Offline same-model lock contention and distinct-model shared-quota serialization now each have OS-process regressions:** the first process holds the relevant lock while the second overlaps; the second is refused before network open when its transfer would exceed the quota. These exercise the host OS lock implementation with mocked HTTP, but are not physical Windows acceptance. A mocked 429 response now proves signed CDN URL redaction, response closure, preservation of partial bytes and successful exact-range retry; live status behavior remains unverified. **Offline process-kill recovery** also has a subprocess regression: unbuffered chunk writes survive a hard exit and resume with the exact pinned range. **Partial mocked disk-full recovery** retains the exact journal and resumes the same pinned revision. Physical disk exhaustion, Windows-specific lock behavior, power-loss durability and live CDN behavior remain unverified.
- [ ] Confirm metadata-to-weights integrity through source revision and hash without accepting arbitrary third-party redirects, untrusted archives or symlink/path traversal. **Partial metadata transport guard:** public Hub JSON requests bypass system proxies and reject redirects outside the literal HTTPS `huggingface.co` origin; catalog metadata remains untrusted and the separate downloader still requires pinned revision and trusted LFS SHA-256. Live redirect/CDN acceptance remains open.
- [x] **Partial revision binding:** GUI downloads carry the exact immutable revision displayed during repository inspection through to `download_gguf`; CLI users can obtain the same pinned card/file snapshot with `model-catalog inspect` and pass its SHA to `model-download ... --revision <40-digit-sha>`. The downloader reuses that revision for file/hash lookup and the verified manifest instead of resolving a newer repository head. CLI downloads without the option retain resolve-current-revision behavior. Real network/CDN acceptance and end-to-end provenance review remain open.
- [ ] Show license, model author, repository, family, parameter count, quantization, context claim, chat template, tool/structured-output claims, download size and actual local verification status. Label unverified metadata as such.
- [x] **Partial card display:** explicit repository inspection now shows bounded Hub card claims for license, author, model name/type, pipeline/library, base model, language, datasets, tags and optional model-size/parameter/quantization/context/chat-template/tool fields when supplied, labeled unverified. The current repository commit is pinned and used for the GGUF tree listing. File rows also report exact local inventory status without rehashing; actual verification remains an explicit streaming action. Live card-change behavior and fuller compatibility integration remain open.
- [x] Support imported existing GGUF weights without copying them; store path/hash identity in ForgeOS's bounded model registry so read-only source folders work, and recheck SHA-256 before managed use.
- [x] **Partial local import/lifecycle:** the existing GUI explicitly hashes a user-selected GGUF without copying it, records unknown provenance in the local model registry, and uses that identity with the managed runtime. Identity and lifecycle locks remain in writable ForgeOS storage; legacy adjacent sidecars are validated and preserved when present. Runtime rehashes before inference; CLI inventory and explicit verification include imports. Import/lifecycle actions provide bounded byte progress and cooperative cancellation. Imported models can be unregistered/re-registered or permanently purged through the GUI/CLI with explicit confirmation, task-checkpoint protection, SHA-256 verification and retryable registry-last cleanup. Broader lifecycle acceptance remains open.
- [x] **Partial inventory baseline:** bounded offline inventory and explicit streaming re-verification exist in `model_inventory.py`; downloaded weights can be classified as missing, size-mismatch or present-not-reverified without an always-running database. Reverification is serialized by the per-model lifecycle lock; the existing GUI now provides background byte progress and cooperative cancellation. Imported/loaded/unsupported state coverage remains open.
- [x] **Partial lifecycle baseline:** reversible explicit retirement/restore with recovery journal exists in `model_retirement.py` and the existing GUI/CLI. Imported-weight unregistration and permanent purge now exist in `model_import.py` and the existing GUI/CLI. Update, repair and final atomic no-clobber Windows semantics remain open.
- [ ] Support model revision pinning, storage quotas, resume limits, deduplicated file references and readable diagnosis of failed operations. **Partial storage quota:** optional GUI/CLI caps serialize quota-checked downloads and count bounded active, retired, partial and quarantined weights without hashing; imported models outside the managed store are not counted. Resume limits, deduplication and broader failure diagnosis remain open.
- [ ] Complete malicious Hub metadata, invalid-name, missing-license, model-card-change and model-disappearance coverage. **Partial adversarial metadata coverage:** card strings remove Unicode control/format characters; catalog file paths reject Windows-reserved/device names, control characters and unsafe traversal; GGUF sizes above ForgeOS's 16 GiB downloader cap are ignored. Real Hub mutation/disappearance behavior remains unverified.

**Exit:** browse → examine terms/hardware → download/recover → verify → use with managed runtime → stop → update/remove safely, all in ForgeOS.

## Work package D — smaller coding LLM selection and benchmarks

**Owner:** `model_routing.py` and a reproducible test harness, not static quality guesses.

- [ ] Compare representative Qwen2.5-Coder 1.5B, 3B and 7B quantized GGUF candidates and newer Qwen3.x/other permissively licensed coding alternatives subject to live metadata and hardware fit.
- [ ] Record exact **model revision, SHA-256, source, license, quantization, runtime version, hardware, OS, RAM, thread count, context and sampling settings**.
- [ ] Use repeatable offline programming tasks: planning, functional code generation, bug finding, refactoring, tests, security review, documentation and summarization.
- [ ] Score build success, test correctness, defect rate, JSON-manifest acceptance, hallucinations, tool-call/schema compatibility, debugging fixes, time-to-first-token, tokens/s, total completion time and failure recovery.
- [ ] Compare Q4_K_M/Q5/Q8 where practical; distinguish downloaded weight bytes from peak **working set, commit, mapped pages and KV cache**.
- [ ] Test 1k/2k/4k/8k contexts and representative workspace sizes; prohibit unbounded context or prompt growth.
- [ ] Publish objective benchmark tables including task sample size, variance, timeouts, failures and limitations; recommend a model only when data supports its role.

**Exit:** repeatable benchmarks on the i7-1370P / 32 GiB Intel iGPU system and relevant 16/8 GiB tiers. Performance assumptions are **not** acceptance evidence.

## Work package E — intelligent task and multi-agent routing

**Owners:** enhance `model_routing.py`, `engine.py`, existing repair learning and task state; use one arbitration mechanism.

- [ ] Define task roles: architecture/planning, implementation, review/refactor, debugging, test generation, security, documentation and context summarization.
- [ ] Map measured model capabilities to roles, explicit preferences and supported runtime/format capabilities.
- [ ] Default to the smallest qualified model for low-complexity tasks; escalate when test evidence, structured-output failures or bounded confidence rules justify it.
- [ ] Run specialized roles **sequentially by default**; avoid several resident model processes on 16/32 GiB unless a measured budget proves safe.
- [ ] Record chosen backend/model/revision/reason/task role, token/latency metrics, retry count and quality outcome in existing task metadata and diagnostics.
- [ ] Model switch must preserve worktree, checkpoint, tool state and result integrity. Never silently regenerate applied edits after partial failure.
- [ ] Add optional manual per-role overrides and explain routing decisions in plain language.
- [ ] Keep reviewer/security findings as evidence; do not auto-approve dangerous commands based only on another model's text.

**Exit:** measurable benefit across role suite, deterministic policy tests, bounded model switching and complete recovery of intermediate artifacts.

## Work package F — RAM, CPU, context and failure control

**Owners:** `model_routing.py`, lifecycle controller, existing `MEMORY_BUDGET.md` and process telemetry.

- [x] **Partial experimental enforcement:** managed Windows llama-server receives a Job Object job-memory ceiling derived from current available physical RAM with a preserved OS/workload reserve. The existing GUI can optionally request a lower ceiling (minimum 512 MiB), but cannot raise the automatic safe limit. This is a managed-model cap, not yet a global ForgeOS/build/GPU budget; broader measured/global-budget pieces remain open.
- [ ] Admission preflight using conservative weights + context/KV estimate + reserve + competing child-process usage; reject or downshift before Windows approaches instability. **Partial implementation:** managed GGUF startup now parses bounded architecture/layer/head metadata, uses explicit key/value head widths when present and includes an estimated f16 K+V cache for the chosen context in the conservative admission check. Unsupported metadata falls back to the existing file-size heuristic. This is not exact native allocation or physical acceptance; runtime buffers, memory-mapped pages, external process races and target Windows measurements remain open.
- [ ] Lazy load one selected model; reuse warm model for adjacent tasks, idle-unload after policy timeout; bound the number of heavyweight executions.
- [ ] Native CPU-thread tuning and benchmarking (physical cores vs hyperthreads), Windows processor affinity where justified, safe worker queue limits and cancellation.
- [ ] Context pruning, source selection, summarization and prompt caches integrated with existing `context_index.py` and output pipeline (never a second context index).
- [ ] On out-of-memory, timeout or crash: freeze task state, stop/reap runtime, recheck journal/checkpoint, suggest or select a smaller **compatible** model only at a safe boundary.
- [ ] Guard repeated load/unload, rapid task switches, low disk, slow storage, CPU saturation and other concurrent builds.
- [x] **Partial on-demand resource visibility:** the existing managed-model GUI can request one-shot Windows Job Object peak committed-memory accounting and the active ceiling. No background polling or third-party monitoring agent is used; broader ForgeOS/build/GPU resource aggregation remains open.
- [ ] Measure idle RAM and peak process-tree memory at 4/8/16/32 GiB tiers; 4 GiB may only support UI operations, not a resident useful coding model.

**Exit:** real target-Windows soak tests, memory-pressure fault injection, stable reserve, no unexpected user-project modifications and reproducible RAM measurements.

## Work package G — autonomous workflow and recovery integration

**Owners:** existing `Store`, `task_actions.py`, `engine.py`, `transactions.py`, `recovery.py`, `error_learning.py`.

- [x] Existing task leases, edit transactions and bounded test/repair checkpoints on main.
- [x] PR #51 prototype validates and recreates saved non-Ollama backend, loopback endpoint and model name for eligible checkpoints.
- [ ] Bind checkpoints to **cryptographically verified GGUF/model revision and runtime binary identity**, not merely a model name; reject unknown identity on resume.
- [ ] Recover interrupted inference, model download, model load, planning, editing, tests, repair and role handoffs across app restart and OS reboot.
- [ ] Resume only from committed stage boundaries; prevent duplicate edits or publishing after stale leases/reclaimed work.
- [ ] Persist bounded progress events and meaningful explanations; keep full source/code/test evidence in local handoffs.
- [ ] Expand autonomous QA using the same secure test runner and trusted output rules; do not introduce unrestricted shell execution.
- [ ] Add complete source packaging, portable project handoff and verified restoration of interrupted work.

**Exit:** recorded kill/restart/corrupt-checkpoint and backend-switch tests prove no silent loss, unauthorized rerun or overwrite.

## Work package H — unified accessible model-management GUI

**Owner:** `gui.py`; no second model-manager application.

- [x] PR #51 adds backend selection, endpoint input, HF discovery/file inspection and explicit download controls to existing GUI.
- [ ] Add `Models` section/tab with installed/imported/available/corrupt, quantization, license, hashes, disk use, RAM estimate, suitability and runtime readiness. **Partial in existing picker:** local SHA re-verification and an on-demand weight-plus-KV estimate against the current safe model budget are available; unsupported metadata and target-hardware suitability remain explicit unknowns.
- [ ] Install/start/stop/reverify/update/remove controls; protect an active model or a checkpoint reference from unsafe deletion. **Implemented partial guard:** recoverable download retirement and imported-model unregistration check bounded blocked-task checkpoints by exact managed GGUF SHA-256 and fail closed when checkpoint identity cannot be inspected. A shared OS-backed per-model lock prevents managed runtime start, download, explicit SHA re-verification, retirement, import unregistration and imported-model purge from racing across ForgeOS processes. The GUI picker exposes missing, changed-size and unsafe inventory states while disabling use/lifecycle actions for those entries, and provides progress-reporting, cancelable background SHA-256 verification and permanent deletion. Retired downloads and imported weights can be permanently purged from the GUI or CLI after explicit confirmation, checkpoint clearance and locked SHA-256 verification. External inference servers remain outside this lock policy; updates and broader lifecycle acceptance remain open.
- [ ] Per-role model preference, manual routing override and benchmark history remain open. **Implemented experimental baseline:** the existing managed-model GUI now provides a one-click local inference self-test that requires valid empty-edit JSON, measures elapsed time and records available one-shot memory accounting without sending project content.
- [ ] Plain-language explanations: model too large, unavailable backend, unsupported template, missing hash, out of disk/RAM, blocked network, insufficient Windows permissions.
- [x] **Partial Hub diagnosis:** the catalog UI now translates DNS/network failures and common HTTP 401/403/404/429/5xx responses into actionable plain-language guidance. Other model/runtime and storage diagnoses remain open.
- [x] **Partial local endpoint diagnosis:** OpenAI-compatible adapter connection failures and common HTTP errors now produce URL-free guidance; HTTP error responses are closed. Runtime, model-load, storage and permission diagnoses remain open.
- [ ] Accessible keyboard navigation, screen reader labels, progress/cancel/finish affordances; all slow downloads/benchmarks off the Tk main thread.
- [ ] Minimal idle memory, no unbounded logs, no hidden background network operations; clearly state that bundled EXE **does not bundle model weights**.

**Exit:** new user can complete model discovery to local coding task without terminal commands, Docker or another GUI.

## Work package I — QA, fuzzing, security and regression gates

**Owner:** existing tests and GitHub Actions; add adversarial tests in the same test suites.

- [x] PR #51's hosted test matrix, documentation integrity and frozen EXE smoke passed on `ba985c9`.
- [ ] End-to-end real llama.cpp + LM Studio integration against pinned versions/models.
- [ ] Disrupted downloads: damaged journal, truncated weight, bogus hash, 206 mismatch, 429/500, TLS/redirect and physical disk full. Offline regressions cover abrupt process kill with exact-range resume, same-model lock rejection and distinct-model quota serialization across OS processes, plus injected 429 redaction/response closure and exact retry from retained partial bytes. Physical Windows disk-full acceptance and live transfer fault handling remain open; mocked ENOSPC mid-write and exact resume also have coverage.
- [ ] Corrupted GGUF, unsupported quantization/model architecture, missing chat template and structured-output mismatch.
- [ ] Runtime crash, fork/process escape, port takeover, bad response, OOM, runaway context, model cancellation, rapid launch/reap and restart loops.
- [ ] Stress concurrency in model routing with tasks, downloads, tests and checkpoints while enforcing leases, CPU and memory budgets.
- [ ] Preserve existing AppContainer fail-closed test guarantees; verify frozen Windows GUI and offline manual still work.
- [ ] Security review of third-party binary supply chain, Hub metadata, imported model files, local endpoint privacy, secrets and checkpoint integrity. **Partial Hub transport review:** catalog JSON calls now bypass ambient proxies and reject cross-origin redirects; author-supplied model claims and live redirect behavior remain unverified.
- [ ] Iterate unit, integration, mutation/property/fuzz, fault-injection, benchmark and regression runs; archive exact results, failures and root causes.

**Exit:** all required gates pass at the **latest SHA**, including physical target system acceptance. A green mocked adapter test does not prove a real model works.

## Work package J — documentation, releases and continuity

**Owners:** existing `README.md`, `USER_GUIDE.md`, `MODEL_COMPATIBILITY.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `MEMORY_BUDGET.md`, `WINDOWS_BUILD.md`, `HANDOFF.md`, CI documentation gate.

- [x] Preserve accurate main-versus-feature-branch status and full handoff; previous code and docs on PR #51.
- [x] Add this requirements-to-code and acceptance-criteria tracker.
- [ ] Complete the in-app beginner tutorial with screenshots of a real GUI and interactive guided recovery. **Partial in-app guide:** added a first-task workflow, clickable repository/readiness/model-discovery shortcuts, common failure explanations and keyboard navigation tips in the existing GUI. Real screenshots and interactive guided recovery remain open.
- [ ] Keep model cards/license provenance, runtime build instructions, Windows dependencies, security disclosures and reproducible benchmark results current.
- [ ] Keep cumulative GitHub source, tests and commits; do not replace stable main until reviewed and verified, and do not silently merge an unaccepted draft.
- [ ] Provide full cumulative source/package, exact setup/verification commands, test evidence, unresolved blockers, release provenance and recovery steps after each development segment.

**Exit:** source, user experience, model/runtime behavior and all visible documentation agree and all material changes have repeatable evidence.

## Active implementation issues on GitHub

Each workstream below is filed as a concrete GitHub issue, with acceptance checklists rather than vague promises. **Creating an issue is planning, not implementation or acceptance.** Link code and test evidence to the issue when work is completed.

| Workstream | Tracking issue | Roadmap and required outcome |
| --- | --- | --- |
| P1 managed CPU inference | [#53 — integrated llama.cpp runtime](https://github.com/DevTunaSoft/ForgeOS/issues/53) | Work packages A/B: trusted CPU binary lifecycle, real model, no extra GUI/Docker |
| P1 managed verified GGUF weights | [#54 — model lifecycle and pinned identity](https://github.com/DevTunaSoft/ForgeOS/issues/54) | C/G/H: live HF transfer, inventory, install/import, safe update/remove and recovery |
| P2 small-model optimization | [#55 — RAM governor, coding-model benchmarks and role routing](https://github.com/DevTunaSoft/ForgeOS/issues/55) | D/E/F: measurable performance, admission limits, idle unloading and automatic role selection |
| P2–P3 independent acceptance | [#56 — Windows QA and release gates](https://github.com/DevTunaSoft/ForgeOS/issues/56) | G/H/I/J: real Windows workflows, fuzz/recovery, beginner GUI and honest shipping evidence |
| Baseline experimental implementation | [PR #51 — multi-LLM adapters and HF GGUF downloads](https://github.com/DevTunaSoft/ForgeOS/pull/51) | Existing draft branch implementation; hosted tests passed, not merged into main |

## Prioritized execution and dependencies

| Priority | Phase | Work items | Objective merge/acceptance gate |
| --- | --- | --- | --- |
| P0 | **Integration correctness and baseline** | Confirm latest PR #51 GitHub status, check regression/workflows and publish this comprehensive roadmap with README/handoff links | Every referenced file exists and doc integrity CI passes; no claim of physical acceptance |
| P1 | **Managed local CPU inference** | Work packages A + B; verify binary source/licensing, managed llama.cpp lifecycle, real GGUF/JSON inference and Windows packaging | Full plan→edit→test through locally managed CPU llama-server, shutdown/restart, no Docker |
| P1 | **Model integrity and lifecycle** | Work packages C + G; real HF network/download acceptance, pinned model hashes and safe install/import/remove | End-to-end SHA-verified model reused safely after restart; no data loss |
| P2 | **Resource governance** | Package F plus targeted D benchmarks: low-memory admission control, idle unload, process-tree telemetry and OOM handling | Measured 16/32 GiB real Windows profiles, smaller fallback and no destabilization |
| P2 | **Intelligent autonomous role routing** | Package E + G; sequential roles, model preferences, routing explanations, capability grading | Role-level objective quality tests, recovery and bounded task runtime |
| P2 | **User experience and model manager** | Package H; familiar GUI, complete model lifecycle and beginner documentation | Novice can operate full workflow in one app, no command line |
| P3 | **Broader scale and release** | Packages I + J; 4/8 GiB experiments, extended model suite, harder fuzz/crash trials, signed distribution | Full release gate, hardware matrix and verified user acceptance; not just CI |

**Critical path:** managed CPU llama.cpp → pinned model identity → full real-model workflow → RAM admission/idle unloading → benchmark-informed role routing → complete GUI → real hardware and release signoff. All may be developed in parallel **only when they do not violate the one-heavy-model memory budget or edit the same canonical module concurrently**.

## Acceptance evidence template (required at each milestone)

Record: PR/commit SHA, changed files, requirement IDs, OS/CPU/RAM/GPU, inference backend/build hash, model repository/revision/GGUF SHA-256/license, quantization/context/threads, test commands, passed/failed/skipped counts, generation metrics and quality scores, process-tree peak/private/mapped RAM and idle usage, failure injection, GUI/EXE evidence, known regressions, limitations, and exact continuation commands.

**No claim of production readiness until genuine on-device tests satisfy this evidence template.**

## Current managed-runtime hardening increment

- [x] Tighten `managed_llama.py` child-process environment to a **strict OS allowlist** (rather than an extensible blacklist), preventing inherited provider tokens, native-library injection overrides, unknown GGML/llama environment switches and proxy settings. Regression tests cover both cloud credentials and mixed-case Windows keys. **Committed on PR #51**, not yet accepted on a real Windows model runtime.
- [ ] Verify this change on the latest GitHub CI head, then on physical Windows 11 with a pinned official CPU llama.cpp release and an actual verified GGUF. Preserve any required Windows OS variables only after testing.
- [x] Extend the existing `runtime_installer.py`, `managed_llama.py` and `gui.py` without duplicating the application: **explicitly opt-in pinned Windows x64 CPU installer** integrated into the managed GGUF dialog, verified installed-bundle start, preserved manual runtime fallback, and mock/failure tests. No runtime/model download on ordinary GUI startup. **Still experimental pending hosted CI + physical Windows real GGUF acceptance.**
- [ ] Complete real Windows 11 installation, llama.cpp boot, authenticated inference and measured RAM/latency plus process/DLL mutation and recovery fault injection. Do not treat hosted mock success as real native runtime acceptance.

### Verified hosted evidence for unified Windows CPU installer (2026-11-08)

- [x] Existing pinned `runtime_installer.py` is now opt-in via the single GUI, confirmation-gated, in a background worker with in-dialog status feedback; no implicit network calls, no second app.
- [x] Existing `managed_llama.py` delegates `start_installed_cpu` through a full official archive/DLL verification pass before model launch, with a preserved manual binary option.
- [x] New offline **real POSIX process** harness exercises actual spawned process, private localhost bearer token, `/health`, `/v1/models`, `/v1/chat/completions`, unauthorized rejection and process-group teardown; this lightweight HTTP stub is **not llama.cpp**.
- [x] Commit `c541041926c6c726e7fa034f6591318188bd2a17`: **6/6 hosted check jobs passed**, including 349-test Windows/Linux test suites and 18,524,708-byte Windows EXE packaging with frozen GUI and secure AppContainer smoke. Hosted test passes are not physical-device acceptance.
- [x] Detect unexpected exit of a GUI-owned managed model using the **existing 150ms event loop**, not a new watchdog/timer/process. Once tasks are idle, stop the dead process, clear temporary API-key material, switch selection back to Ollama, and warn in Activity. A cleanup failure is latched until manual retry to prevent an unbounded automatic stop loop. Unit tests **and a real POSIX subprocess kill/restart harness** cover classification and credential cleanup; **real native Windows llama.cpp failure injection still required**.
- [ ] **Blocker:** install the pinned real release on actual Windows 11, load a truly compatible GGUF, test planning/JSON edits, measure RAM and latency, stress start/stop and failure recovery, validate DLL loading and port ownership. Do not merge PR #51 or close #53 based on the stub.

## Security and supply-chain risks to resolve before a managed runtime release

1. Authenticate the spawned local process and protect its loopback port; a literal loopback URL alone does not establish server identity.
2. Verify and pin downloaded executable artifacts and dependencies, not only GGUF weights. Avoid arbitrary helper shell scripts, installers, elevated privileges or DLL search-path issues.
3. Maintain hash/revision identity through task checkpoints, so `resume-safe` cannot accidentally use changed weights under the same model name.
4. Preserve Windows process isolation for executing generated project tests; inference-server sandboxing and AppContainer test sandbox are different trust boundaries.
5. Treat Hugging Face model metadata, instructions and model output as untrusted; check licensing and redistribution before bundling anything.
6. Do not allow large model downloads, hash passes, model loading or diagnostics to block the GUI or grow ForgeOS memory without bound.

## Status reconciliation rules

A green PR #51 means **hosted tests passed for source committed to that PR**. It does not mean the feature is on `main`, that the user has a working llama.cpp runtime on their laptop, or that the application meets every requirement above. Update the roadmap when verified changes land; always link a precise commit and test evidence. Keep this document synchronized with `ROADMAP.md` and `HANDOFF.md`.


## Experimental progress reconciliation (next coding cycle)

The following **source prototypes** are now present on draft PR #51:
`managed_llama.py` (CPU-first user-selected binary lifecycle),
`model_inventory.py` (bounded metadata-only local inventory plus explicit
streamed SHA-256 verification), the existing Tk GUI's opt-in managed
GGUF/installed-download chooser, and tests
`test_managed_llama.py`, `test_model_inventory.py`. Startup session
credentials are temporary and **automatic resume is refused for
`forgeos-managed` until model/runtime hashes are part of the task
contract**. Real llama.cpp inference, multi-GiB HF download, Python
and Windows acceptance on the **latest** head, process-tree protection,
upstream binary installation, model import/update/removal, role router
and memory governor are **not yet accepted**. Follow issues #53–#56.

### Optional external providers are strictly non-default

A future optional extension to work package A must support user-authorized
OpenAI/ChatGPT and Anthropic/Claude APIs and other compatible providers,
without changing the local-first default or requiring a subscription.
Before sending code or metadata off-device, obtain explicit project/provider
consent and show potentially transmitted context, apply safe credential
storage, exact domain allowlists, token/spend budgets, audit traces, rate-limit
and fail-closed handling. No such provider or automatic cloud fallback is
currently implemented. Free consumer chat plans are not free API entitlements.


### GGUF atomic-publication recovery increment (2026-11-08)

- [x] Extend the existing GGUF downloader to verify cached hash, header, manifest and transfer journal; repair a missing manifest after interrupted publication without redownloading.
- [x] Preserve ambiguous partials and mismatched manifests rather than overwriting them. Reject Windows COM1–9, LPT1–9 and trailing-dot path segments.
- [x] Add injected interruption, forged metadata, orphan partial, invalid-content and Windows filename regression cases. No duplicate database or new model application.
- [ ] Confirm latest Linux/Windows CI and EXE packaging; test actual large Hugging Face weights, native Windows crash/restart and inference before closing #54.


### Cooperative download pause and bounded progress reporting (2026-11-08)

- [x] Extend the *existing* Hugging Face GGUF transfer path with optional threading.Event cancellation, bounded streamed SHA-256 verification, durable partial flush, explicit DownloadCancelled status, and download/verify progress callbacks. No new downloader or resident model process.
- [x] Add **Pause download** and a non-blocking transfer/verification progress label to the existing GUI. Background workers never access Tk widgets; a one-message queue replaces stale progress data, and closing the dialog during a download requests pause before closing.
- [x] Add offline regression coverage for pre-cancellation, interrupted chunks, exact-range resumption, post-transfer pre-commit pause, cached-model hash pause and invalid cancellation options.
- [ ] Verify the latest six CI checks, then validate actual socket timeout behavior, real multi-GiB weights, Windows 11 window shutdown and restart on physical hardware before claiming full #54 acceptance.


### Managed llama.cpp second-stage RAM admission (2026-11-08)

- [x] Reject explicitly requested context windows larger than the canonical current adaptive RAM profile (minimum 512 / maximum 8192 still apply).
- [x] Re-sample available physical RAM after the model/runtime SHA-256 passes and before native process startup. Block on budget failure without leaving a model process or secrets.
- [x] Add deterministic mocked regression tests for excessive manual context and a RAM drop during verification.
- [ ] Reproduce with real inference and measured peak process-tree RSS on Windows, low-RAM targets, and concurrent background memory pressure. Preflight alone is **not** a hard memory governor or finished issue #55.


### Hosted CI gate for pause/resume and managed model RAM preflight (2026-11-08)

On draft PR #51, the final code head `37af510c` passed **6/6** hosted checks after the `b40536b` test-fixture correction: Python 3.10/3.12 on Ubuntu and Windows, documentation, and packaged Windows EXE. Packaging ran **365 tests**, verified frozen GUI and AppContainer smoke, and produced an 18,531,029-byte executable. The release gate for physical Windows 11 with real model inference, large network downloads, native Job process isolation and measured RAM remains **open**; keep #53–#56 in progress.


### GGUF retirement and recovery — issue #54 (2026-11-08)

- [x] Extend the existing download/inventory lifecycle with explicit, reversible CLI model retirement and restoration; no second model database.
- [x] Require --confirm, rehash the complete pinned GGUF under the existing model download lock, and reject unfinished download journals.
- [x] Keep a versioned, bounded same-volume retirement journal and one unique random recovery ID. Rename only the selected model and matching manifest; leave sibling files untouched.
- [x] Support restoring from interrupted retirement or interrupted restoration without overwriting any existing model/manifest; fully rehash before and after restoration.
- [x] Exclude retired material from active inventory and add a bounded, metadata-only retired listing.
- [x] Add explicit permanent cleanup by recovery ID. Require the existing task database, blocked-checkpoint clearance, explicit confirmation and full SHA-256 verification under the lifecycle lock; keep the journal until retired bytes are removed so interrupted cleanup can retry.
- [x] Add offline regression tests for corruption, incomplete downloads, malformed journals/IDs, symlinks, collisions and fault-injected interruptions.
- [ ] Verify latest Linux/Windows Python 3.10 and 3.12 CI, documentation integrity and frozen Windows EXE/AppContainer.
- [ ] Real Windows 11 trial with a large GGUF, native inference stopped/running, reboot or power-loss, reparse points/hardlinks and actual disk usage. GUI retirement and permanent purge exist; external inference processes remain outside ForgeOS's lock. A reversible move to retired storage does not reclaim disk space.


### Local-model picker retirement control (2026-11-08)

- [x] Add a recoverable retirement command within the existing single-GUI GGUF selection dialog (no second app or model manager).
- [x] Refuse when an AI task or ForgeOS-managed llama.cpp session is active; use the same busy gate to prevent concurrent operations.
- [x] Hash and retire only on a worker thread, keep event queue bounded to one result and poll via existing Tk event loop, and retain the chooser while model retirement is in progress.
- [x] Return a model recovery ID, clear any selected retired model, and add GUI contract regression tests.
- [ ] Physically test model retirement and restore with real weights and native Windows file locks. Externally launched inference processes are not yet automatically detectable.


- [x] Harden journal and rename durability with POSIX directory fsync, plus fault injection for synchronization interruption. Windows directory durability requires physical NTFS acceptance before any power-loss guarantee.


### #55 measured-routing prerequisite: baseline capture

The current PR now has an explicit **Save benchmark baseline** path for a
running verified managed GGUF. It captures the fixed RAM-tier label, pinned
model/runtime identity, context/threads, startup and structured-output
self-test latency, configured managed-memory ceiling and available one-shot
Windows Job Object peak accounting. This is evidence collection only. The
fixed role corpus, deterministic scorers, qualification tables and role router
remain open and must not be enabled without real qualifying model data.


### #55 screening corpus status

A reproducible screening corpus now defines exactly five fixed tasks for each
of the eight routing roles. Deterministic scoring separates structured-output
success from objective rubric success and reuses ForgeOS's canonical edit
manifest parser for edit-producing roles. A role advances from screening only
at 4/5 structured contracts and 3/5 objective passes. This does **not** satisfy
the final 95% structured / 80% objective qualification gate and does not enable
automatic routing.


### #55 live screening runner status

The screening harness can now run the fixed synthetic corpus against an active
verified managed llama model, with optional per-role scope, progress callback
and cancellation. Results persist only objective/contract scores, timing,
response size and error type; raw model output is discarded. This is still a
screening mechanism, not final qualification, and it does not execute generated
code or enable role routing.


### #55 GUI screening status

The existing Managed local GGUF dialog now exposes five-task per-role
screening with progress and cooperative cancellation. Screening shares the
single managed runtime and existing Tk event queue, preserves partial evidence
when cancelled, and keeps normal model/task operations interlocked. It remains
a screening-only workflow; automatic role routing and final model
qualification remain disabled.


### #55 final qualification gate and fail-closed role selection

- [x] Add a separate final qualification contract; five-task screening evidence cannot be promoted into routing evidence.
- [x] Require at least 20 objective tasks per role and the fixed final thresholds: >=95% structured-contract success and >=80% objective success.
- [x] Bind every qualifying result to exact model and runtime SHA-256 identities and hash the normalized evidence itself.
- [x] Route only among qualified records that fit the measured memory budget, selecting the smallest model first with deterministic memory/latency/identity tie-breakers.
- [x] Fail closed when no measured-qualified candidate fits; never fall back to an unqualified model.
- [ ] Build and execute the larger final per-role corpus against real candidate models. Until those records exist, automatic production role routing remains intentionally unavailable.

Final qualification evidence and route requests are additionally restricted to
the canonical roles in `model_roles.py`; arbitrary role labels in local files
cannot create new routing categories. The check is covered by the focused
qualification suite. Real candidate qualification remains a target-Windows gate.


### Managed GGUF checkpoint re-attestation

- [x] Persisted managed checkpoints already bind to exact GGUF and runtime SHA-256 identities.
- [x] Recovery now accepts an already-running authenticated ForgeOS-managed runtime only when backend, loopback endpoint, model alias, model SHA-256 and runtime SHA-256 exactly match the checkpoint.
- [x] Missing authenticated runtime remains a skip, not an unsafe reconstruction; ordinary CLI/background recovery cannot silently recreate a managed session because the API secret is ephemeral.
- [x] Hash, endpoint and missing-auth mismatches fail before task execution.
- [ ] Wire the existing GUI-managed controller into the recovery action and validate interruption/resume against real llama.cpp on Windows.


### GUI safe blocked-task recovery with managed re-attestation

- [x] Add **Resume safe blocked tasks** to the existing Activity & diagnostics surface; no second recovery application.
- [x] Recovery runs off the Tk thread and uses the existing workspace/test-permission controls.
- [x] When a verified managed GGUF is already running, the GUI passes only its ephemeral authenticated endpoint plus exact model/runtime hashes into the recovery re-attestation path.
- [x] The managed runtime is held busy during recovery so idle-unload cannot race a resumed checkpoint.
- [x] Results are summarized in the existing Activity log and task list is refreshed when recovery completes.
- [ ] Exercise a real interrupted managed GGUF test/repair checkpoint on physical Windows and verify process restart/recovery across an application relaunch.


### #55 qualified execution integration

The autonomous build/test/repair engine now recognizes an explicit `qualified-auto`
mode for the ForgeOS-managed llama.cpp path. Before any prompt is sent it requires
the active process to expose pinned model and runtime SHA-256 identities, loads only
bounded local `qualification-*.json` evidence, applies the final 95% structured /
80% objective gate, enforces the current managed-runtime memory ceiling, and requires
the selected evidence to match the active model/runtime hashes. The evidence digest
and role are persisted in task checkpoints. Invalid or missing evidence fails closed.
Generic local endpoints and Ollama remain manual for final-qualified routing until
ForgeOS can cryptographically attest their runtime identity; no weak fallback is used.


### #55 final 20-task qualification evidence producer

- [x] Add a distinct 20-task final corpus for each of all eight routing roles (160 deterministic tasks total); the five-task screen remains only an early filter.
- [x] Reuse ForgeOS's canonical edit-manifest validator for code-producing qualification tasks and explicitly count traversal/protected-path attempts as safety violations.
- [x] Final evidence records structured success, objective success, runtime failures, safety violations, median latency, exact model/runtime SHA-256, model bytes and managed peak memory.
- [x] Tighten final qualification to >=95% structured success, >=80% objective success, <=5% runtime failures and zero safety violations.
- [x] Cancellation produces partial, non-routeable evidence. A complete run without measured managed peak memory fails closed.
- [ ] Expose final qualification in the existing managed-model GUI and execute it against real candidate GGUFs on target Windows hardware.


### #55 GUI final qualification workflow

- [x] Expose the 20-task final qualification runner in the existing Managed local GGUF dialog using the existing role selector.
- [x] Keep qualification off the Tk thread, reuse the bounded managed-event queue, show per-task progress and support cooperative cancellation.
- [x] Preserve partial cancelled evidence as explicitly non-routeable; only complete evidence satisfying every final gate can feed qualified-auto.
- [x] Show structured/objective counts, runtime failures, safety violations and routing qualification in the existing Activity diagnostics.
- [ ] Run final qualification against real candidate GGUFs on target Windows hardware and record the first genuine routeable model/role evidence.


### #55 qualified checkpoint recovery hardening

Qualified managed-model checkpoints now preserve the exact final-evidence SHA-256,
task role and canonical evidence directory. Safe recovery first re-attests the
authenticated running GGUF/runtime identity, switches the recovered request back
through the qualified-auto gate, and requires the exact same evidence digest before
resuming. A different newer qualification record cannot silently replace the saved
one. Symlinked evidence roots are rejected before path resolution.


### #55 qualified planning integration

The same final-evidence resolver now gates both autonomous implementation and
planning. Selecting `qualified-auto` with an authenticated ForgeOS-managed
llama.cpp session resolves the `planning` role against the existing bounded
qualification records, exact GGUF/runtime SHA-256 identities and current safe
memory ceiling before a planning prompt is sent. Missing, stale, over-budget or
unqualified evidence blocks the plan. The existing plain-text plan result
contract is preserved for compatibility; no second router or evidence format
was introduced.


### #55 authenticated qualified-auto admission

The shared qualified-model resolver now requires the model adapter to report an
authenticated ForgeOS-managed localhost session in addition to matching GGUF and
runtime SHA-256 identities. Possessing or spoofing the two hashes alone is not enough
to enter qualified-auto routing. The check occurs before qualification files are
loaded and before any model prompt is sent.


### #56 local physical-acceptance evidence recorder

- [x] Add an explicit bounded local acceptance snapshot using only standard-library/native probes already owned by ForgeOS.
- [x] Record OS/Python/machine identity, physical/system memory, ForgeOS process memory, exact source Git SHA when discoverable and frozen executable SHA-256 when running packaged.
- [x] When a verified managed GGUF is active, reuse its existing benchmark identity, inference self-test and process-tree memory report; never serialize the ephemeral API secret or raw response.
- [x] Save atomically under `~/.forgeos/acceptance`; no background sampler or second database.
- [x] Expose the recorder in Activity & diagnostics plus a host-only CLI command.
- [ ] Run the recorder on the target Windows 11 i7-1370P / 32 GiB machine using the exact shipping candidate and real managed GGUF; attach/report the resulting evidence before closing #53/#55/#56.
