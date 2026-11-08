# ForgeOS — Multi-LLM and intelligent model management engineering roadmap

**Canonical scope:** [ForgeOS engineering roadmap](ROADMAP.md) · [Model compatibility and setup](MODEL_COMPATIBILITY.md) · [Architecture and security](ARCHITECTURE.md) · [Full development handoff](HANDOFF.md) · [52 RTK-inspired enhancements](RTK_ENHANCEMENTS.md) and [deduplication audit](ENHANCEMENT_DEDUP_AUDIT.md).

**Audit baseline (2026-11-07/08 GitHub Actions):** `main` commit `c4f017b3957f1f492903bf17804bbf06e19bb844`; feature PR [#51](https://github.com/DevTunaSoft/ForgeOS/pull/51), reviewed head `ba985c9dda192b2d991b4642b6bbf7c21c434e68`. All six required hosted GitHub check jobs passed on that feature head: four Windows/Linux Python 3.10/3.12 test matrix jobs, documentation integrity and packaged Windows EXE acceptance. The feature PR is **draft and unmerged**. Hosted CI is not proof of real llama.cpp, LM Studio, multi-GiB downloads, model quality, physical Windows stability or production readiness.

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
- **PR #51 + hosted CI:** code committed to the unmerged multi-LLM branch and passed its automated test matrix; not a released feature.
- **Partial:** initial implementation exists but an acceptance requirement, integration or safety gate is absent.
- **Planned:** not implemented; do not label as available.
- **Unverified:** a behavior, compatibility statement or performance figure without direct reproduced evidence on the named system.

| Capability | GitHub source or owner | Status | Next acceptance evidence |
| --- | --- | --- | --- |
| Existing Ollama plan/edit/repair client | `src/forgeos/core.py`, `engine.py` | Existing on main | Physical Windows end-to-end real model |
| Ollama installed models and RAM-aware heuristic | `src/forgeos/model_routing.py` | Existing on main | Measured runtime/headroom and accuracy |
| llama.cpp OpenAI-compatible adapter | `src/forgeos/local_models.py` | PR #51 + hosted CI | Real local llama-server / model with structured edits |
| LM Studio and custom local OpenAI-style adapters | `src/forgeos/local_models.py` | PR #51 + hosted CI | Real endpoint compatibility tests, no remote fallback |
| Public Hugging Face GGUF catalog and metadata | `src/forgeos/huggingface_catalog.py` | PR #51 + hosted CI | Live API, model card/license, pagination, failure handling |
| Explicit pinned GGUF download, SHA-256, resume | `src/forgeos/gguf_download.py` | PR #51 + hosted CI | Multi-GiB real download, interrupted resume, disk limits, Windows paths |
| User GUI backend/endpoint selection and HF browser/download | `src/forgeos/gui.py` | PR #51 + hosted CI | Manual accessible GUI exercise with real backend/HF model |
| Backend-aware checkpoint recreation | `src/forgeos/recovery.py`, `engine.py` | PR #51 + hosted CI | Crash/reboot, file/model identity and lease safety |
| Native managed llama.cpp CPU runtime | `src/forgeos/managed_llama.py` plus one existing GUI control | **Partial experimental PR #51:** SHA-pinned manual binary, GGUF verified, private CPU-only single server and GUI start/stop; automatic installer, complete child cleanup and live inference remain planned | Verified versioned binary, install/connect/unload/restart without extra GUI |
| Installed GGUF lifecycle and profiles | New bounded model inventory, reusing existing routing | Planned | Install/import/update/remove/repair, pinned hashes and atomic storage |
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
- [ ] Add compatibility profiles for unsupported/variant OpenAI API request parameters without a parallel inference client.
- [ ] Maintain an explicit adapter capability matrix: tools, JSON schema, context/token limits, model file types, license and backend versions.
- [ ] Prove remote endpoints, loopback impersonation where detectable, proxy bypass, redirect changes and accidental cloud API credentials cannot leak project content.

**Exit:** measured endpoint feature matrix, real code-edit acceptance and no Ollama regressions on Windows.

## Work package B — integrated inference engine, no Docker

**Owner:** one new minimal managed-runtime module behind `local_models.create_model` or its successor; do **not** embed another GUI.

- [ ] Select a version-pinned CPU-compatible llama.cpp Windows binary distribution; record exact license, transitive licenses, binary hashes, source and update policy.
- [ ] Download a signed/checksummed runtime from trusted upstream release metadata with explicit consent; use safe binary origin validation and fail closed when authenticity cannot be established.
- [x] **Experimental source path:** launch a user-provided `llama-server` with argument-array subprocess execution, explicit binary/model paths, random available local port, no shell and **localhost-only** bind.
- [ ] Prove port ownership and harden child process-tree cleanup on Windows; the current launch/readiness code does **not** establish exclusive process ownership.
- [ ] Verify that the listener is the expected child process and version before passing project prompts; protect against port takeover/rebinding.
- [x] **Experimental source path:** one GUI-owned runtime lifecycle controller, authenticated readiness request, timeout, controlled stop and application shutdown.
- [x] **Experimental implementation:** reuse ForgeOS's Windows Job Object structs to attach managed llama-server with kill-on-close, and use a POSIX process group for stop/timeout cleanup.
- [ ] Eliminate the Windows Popen-to-Job assignment race via suspended process creation; verify actual child-tree termination, process ownership, model reload and reboot/lease-safe restart.
- [x] **Experimental source path:** allow manually selecting a SHA-verified GGUF and user-provided llama-server binary from the existing GUI without launching LM Studio.
- [ ] Make the binary and model installation fully turnkey, with official binary provenance and actual Windows CPU inference verified.
- [ ] Separate GPU/CPU capability detection from model selection; CPU-only must always remain usable even without a compatible GPU.
- [ ] No silent fallback to host-executed project tests, cloud inference or untrusted binaries.

**Exit:** from one ForgeOS window, select a downloaded GGUF, start managed inference, generate a valid plan/edit, stop/unload model and recover after killing the server.

## Work package C — Hugging Face model management and provenance

**Owners:** `huggingface_catalog.py`, `gguf_download.py`; add one canonical on-disk inventory; no duplicate downloader.

- [x] Read public repository metadata and GGUF file/size/hash candidates on explicit user request.
- [x] Resolve immutable repository revision for opt-in downloads; require trusted LFS hash; stream bounded chunks, reserve disk, retain partial journals, reject incompatible resumes and atomically publish verified bytes.
- [ ] Real Windows checks on LFS/Xet/CDN redirects, TLS, anti-abuse, 401/403/404/429, disk-full mid-write, concurrent downloads and partial resume across process kill.
- [ ] Confirm metadata-to-weights integrity through source revision and hash without accepting arbitrary third-party redirects, untrusted archives or symlink/path traversal.
- [ ] Show license, model author, repository, family, parameter count, quantization, context claim, chat template, tool/structured-output claims, download size and actual local verification status. Label unverified metadata as such.
- [ ] Support imported existing GGUF weights **without copying multiple large files**; choose a safe read-only reference policy and recheck hashes on use.
- [ ] Model inventory with installed, incomplete, corrupt, imported, loaded and unsupported states; no new always-running database daemon.
- [ ] Add safe remove, update, repair/reverify and cleanup workflows. Never delete user files or old model versions without explicit selection; protect active/checkpoint-referenced weights.
- [ ] Support model revision pinning, storage quotas, resume limits, deduplicated file references and readable diagnosis of failed operations.
- [ ] Test malicious Hub metadata, invalid names, bad file sizes, missing license, model-card changes and model disappearance.

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

- [ ] Configurable global memory ceiling and OS-reserve margin; include ForgeOS, runtime process tree, model mapping, KV cache, compilers, test runner and shared GPU allocations where measurable.
- [ ] Admission preflight using conservative weights + context/KV estimate + reserve + competing child-process usage; reject or downshift before Windows approaches instability.
- [ ] Lazy load one selected model; reuse warm model for adjacent tasks, idle-unload after policy timeout; bound the number of heavyweight executions.
- [ ] Native CPU-thread tuning and benchmarking (physical cores vs hyperthreads), Windows processor affinity where justified, safe worker queue limits and cancellation.
- [ ] Context pruning, source selection, summarization and prompt caches integrated with existing `context_index.py` and output pipeline (never a second context index).
- [ ] On out-of-memory, timeout or crash: freeze task state, stop/reap runtime, recheck journal/checkpoint, suggest or select a smaller **compatible** model only at a safe boundary.
- [ ] Guard repeated load/unload, rapid task switches, low disk, slow storage, CPU saturation and other concurrent builds.
- [ ] Optional on-demand resource panel in the existing GUI; no high-frequency polling or mandatory third-party monitoring agent.
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
- [ ] Add `Models` section/tab with installed/imported/available/corrupt, quantization, license, hashes, disk use, RAM estimate, suitability and runtime readiness.
- [ ] Install/start/stop/reverify/update/remove controls; protect an active model or a checkpoint reference from unsafe deletion.
- [ ] Per-role model preference, manual routing override, one-click local test prompt and benchmark result/history.
- [ ] Plain-language explanations: model too large, unavailable backend, unsupported template, missing hash, out of disk/RAM, blocked network, insufficient Windows permissions.
- [ ] Accessible keyboard navigation, screen reader labels, progress/cancel/finish affordances; all slow downloads/benchmarks off the Tk main thread.
- [ ] Minimal idle memory, no unbounded logs, no hidden background network operations; clearly state that bundled EXE **does not bundle model weights**.

**Exit:** new user can complete model discovery to local coding task without terminal commands, Docker or another GUI.

## Work package I — QA, fuzzing, security and regression gates

**Owner:** existing tests and GitHub Actions; add adversarial tests in the same test suites.

- [x] PR #51's hosted test matrix, documentation integrity and frozen EXE smoke passed on `ba985c9`.
- [ ] End-to-end real llama.cpp + LM Studio integration against pinned versions/models.
- [ ] Disrupted downloads: process kill, damaged journal, truncated weight, bogus hash, 206 mismatch, 429/500, TLS/redirect, disk full and two competing installers.
- [ ] Corrupted GGUF, unsupported quantization/model architecture, missing chat template and structured-output mismatch.
- [ ] Runtime crash, fork/process escape, port takeover, bad response, OOM, runaway context, model cancellation, rapid launch/reap and restart loops.
- [ ] Stress concurrency in model routing with tasks, downloads, tests and checkpoints while enforcing leases, CPU and memory budgets.
- [ ] Preserve existing AppContainer fail-closed test guarantees; verify frozen Windows GUI and offline manual still work.
- [ ] Security review of third-party binary supply chain, Hub metadata, imported model files, local endpoint privacy, secrets and checkpoint integrity.
- [ ] Iterate unit, integration, mutation/property/fuzz, fault-injection, benchmark and regression runs; archive exact results, failures and root causes.

**Exit:** all required gates pass at the **latest SHA**, including physical target system acceptance. A green mocked adapter test does not prove a real model works.

## Work package J — documentation, releases and continuity

**Owners:** existing `README.md`, `USER_GUIDE.md`, `MODEL_COMPATIBILITY.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `MEMORY_BUDGET.md`, `WINDOWS_BUILD.md`, `HANDOFF.md`, CI documentation gate.

- [x] Preserve accurate main-versus-feature-branch status and full handoff; previous code and docs on PR #51.
- [x] Add this requirements-to-code and acceptance-criteria tracker.
- [ ] Integrate a genuinely working in-app beginner tutorial, screenshots of a real GUI (not generated mockups), guided failure recovery and FAQ.
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
