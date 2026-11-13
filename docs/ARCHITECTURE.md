# ForgeOS architecture and trust boundaries

**Document status:** source-backed overview of the current `main` prototype. It does not represent a completed production-security review.

## System overview

```mermaid
flowchart TB
    UI["Single Tkinter desktop GUI"] --> DB["SQLite task store + leases"]
    CLI["Python CLI"] --> DB
    UI --> Engine["Task engine"]
    CLI --> Engine
    Engine --> Models["Ollama + experimental loopback OpenAI-compatible adapters"]
    Engine --> Context["Bounded source metadata/context"]
    Engine --> Workspace["Git worktree per task"]
    Engine --> Tx["Validated + journaled edits"]
    Engine --> Tests["Selected test preset / runtime"]
    Tests --> Capture["Bounded capture + SHA-256 evidence"]
    Capture --> Repair["Failure summary + bounded repair retries"]
    Repair --> Engine
    Tests --> DB
    UI --> Insights["Memory snapshot / diagnostics / learning statistics"]
    Capture --> Insights
```

## Canonical modules

| Source | Responsibilities |
| --- | --- |
| `src/forgeos/gui.py` | Tkinter application, two tabs, queue paging, on-demand evidence displays, worker/thread handoff |
| `src/forgeos/cli.py` | Scriptable commands for tasks, testing, recovery, diagnostics, memory, filters and learning |
| `src/forgeos/core.py` | SQLite task state, leases, local Ollama connector, test orchestration primitives |
| `src/forgeos/engine.py` | Edit constraints, structured prompt/edit manifest, bounded local task cycle, checkpoint validation and chosen test-runtime routing |
| `src/forgeos/task_actions.py` | Canonical lease-fenced planning and manual-test operations reused by CLI and GUI; requires explicit trust opt-in for tests |
| `src/forgeos/workspace.py` | Git repository/worktree setup and ownership |
| `src/forgeos/transactions.py` | Transactional edit journal, streamed bounded original-file backups (8 MiB/file), failure/rollback/recovery boundaries |
| `src/forgeos/recovery.py` | Eligible checkpoint resumption and safe recovery workflow; bounded reason text and streaming event reporting |
| `src/forgeos/test_runtime.py` | Disposable runtime snapshots and cleanup |
| `src/forgeos/secure_runner.py`, `appcontainer.py`, `windows_containment.py`, `sandbox_runner.py` | Frozen Windows execution containment |
| `src/forgeos/diagnostics.py`, `output_capture.py`, `output_rules.py` | Evidence archive, bounded output, summaries, trusted filters |
| `src/forgeos/error_learning.py`, `optimizer_metrics.py`, `insights.py` | Test-verified repair signatures, efficiency and advisory diagnostic views |
| `src/forgeos/context_index.py` | Shared bounded project traversal, prompt metadata and on-demand Python declaration-name search; no bodies in search results |
| `src/forgeos/model_routing.py` | Installed Ollama model routing, local memory preflight and one-shot process memory report |
| `src/forgeos/local_models.py` | Experimental loopback-only chat adapter for llama.cpp, LM Studio and configurable local OpenAI-style endpoints; bounded requests/responses, no proxy/redirects |
| `src/forgeos/huggingface_catalog.py` | Opt-in public GGUF metadata, pinned commit revision, hash/size inspection |
| `src/forgeos/gguf_download.py` | Experimental pinned and SHA-256-verified resumable GGUF downloader, native per-target lock, disk budget and atomic publication; does not execute model weights |
| `forgeos_build/launcher.py` | Frozen Windows executable launcher and CI acceptance modes |
| `tests/` | Regression, recovery, security, GUI, diagnostics and memory tests |
| `.github/workflows/` | Python CI and Windows packaged smoke/acceptance |

**Do not fork duplicate implementations of these capabilities** without first checking existing ownership and the [deduplication audit](ENHANCEMENT_DEDUP_AUDIT.md).

## Multi-backend inference boundary (feature branch)

`Ollama` in `core.py` remains canonical for existing tasks. The new
`create_model()` factory returns that same client for Ollama, or a
`LocalChatModel` with the same `plan()` / `generate_edits()` interface
for supported loopback chat endpoints. The engine records non-Ollama
backend and endpoint in the task checkpoint execution contract. Legacy
Ollama contracts stay unchanged. The experimental `resume-safe` path now
reconstructs non-Ollama providers from validated backend/endpoint/model
checkpoint fields, preserving the existing lease-fencing and file-hash
checks. Real-model restart and failure-injection acceptance remains unverified.

All provider URLs must be literal IPv4/IPv6 loopback over HTTP with no
credentials, redirects or HTTP proxy. This is a network restriction, **not**
authentication of the process listening on that port. Hub metadata traffic
is explicitly opt-in HTTPS and never executes a hosted model. A server that
does not publish model sizes requires a manually selected model.

New request, catalog and response caps bound ForgeOS's own memory usage.
External LLM inference memory, context KV cache, thread count and model
loading remain outside these adapters, so there is no claimed full
resource governor or native GGUF execution. See
[MODEL_COMPATIBILITY.md](MODEL_COMPATIBILITY.md).

Managed llama.cpp startup additionally verifies that the loopback listener PID
matches ForgeOS's child before authenticated model discovery. The catalog
request checks ownership immediately before and after the HTTP exchange and
startup checks once more before reporting readiness. This narrows the
port-rebinding window; it does not bind the TCP connection atomically to a
process. Target-host Windows IP Helper access and real server identity still
need acceptance.

## Low-memory SQLite read boundary

The persistent `Store.get()` API remains available for explicit detailed history and evidence inspection, but **regular development, planning and manual test actions** use the narrower `Store.get_task_inputs()` projection. Its normal path selects only the project, status and bounded UTF-8 task description, without fetching historical result/error data. Direct resumption uses the same bounded projection but requests an existing checkpoint only when at most 64 KiB. Oversized legacy description/checkpoint values are rejected within the SQLite projection before Python materializes them; the original stored rows are not modified.

The Tkinter Live Activity Monitor uses `Store.get_status()` to select one scalar status value at its existing once-per-second cadence. It never fetches model output, error logs or checkpoints merely to update a stage label. These are **read-path optimizations**, not a guarantee that SQLite's internal page cache, the external model process or the complete application has a fixed RAM ceiling. Full process-tree Windows acceptance remains open.

## Workflow and state

1. A user adds a task referencing an existing local Git repository. The optional GUI/CLI **Plan** and **Run tests only** actions now share `task_actions.py` instead of using independent, unguarded SQLite state writes. Direct tests require explicit host-test permission and use the engine's platform-specific test containment (frozen Windows supported presets only).
2. ForgeOS persists it in SQLite; leases protect concurrent task ownership, including CLI planning and the autonomous edit/test/repair cycle. The engine now uses lease-fenced, compare-and-set updates for each checkpoint, completion, failure and blocked state; stale workers cannot publish after their lease expires or is reclaimed.
3. A local installed Ollama model provides a plan or a bounded, JSON-defined file-edit manifest.
4. ForgeOS validates file paths, extensions and total edit budget before writing; a transaction journal supports recovery.
5. Changes are written to a **separate Git worktree**, not automatically merged into the user's original branch.
6. A selected test preset executes through an environment-dependent runtime.
7. Bounded output capture feeds original evidence and a smaller AI-facing summary into a limited repair loop.
8. A result or blocked checkpoint preserves the workspace so a person can inspect it. Automatic commit/push/merge/deploy is **not** enabled.

The task engine does not hold an operating-system filesystem lock while generating/editing: a lease may expire during a long-running external call. Lease fencing prevents stale *database publications*, but a worktree touched before detecting the expired lease still requires careful inspection; recovery must not silently overwrite it.

Checkpoint recovery is deliberately narrow; file hashes, workspace ownership, branch and saved execution parameters must match. Pre-edit generation retries are bound to the original Git HEAD and require a clean worktree without a pending edit journal; generation calls are capped at three. Test-pending and repair-pending checkpoints retain their existing file-hash checks. Other interrupted phases remain ineligible. `resume-safe` reads only blocked checkpoint fields through SQLite keyset pages of 16 records; any checkpoint over 64 KiB is reported as skipped without loading its payload into Python. It does not scan completed-task test output or bypass execution leases.

## Execution security: differentiate the environments

| Environment | Present behavior | Security implication |
| --- | --- | --- |
| Source/development mode | May run project tests after explicit host-test opt-in; Windows dev mode has additional lower-integrity snapshot behavior in some paths | **Not a reliable untrusted-code sandbox.** Only run trusted projects |
| Frozen Windows EXE, `python-unittest` | Packaged in-process child is launched with AppContainer + Job Object and disposable snapshot; fail-closed on unsupported/failed isolation | Tested in hosted CI smoke, but not proven across real-world installs |
| Pytest file-logging boundary | Parent validates owned `.forgeos-runtime/pytest-log.txt`, monitors it alongside `test-output.txt` and native stdout at ~500 ms intervals (32 MiB combined); force-fail on link substitution, missing/unreadable test evidence, or disk overflow | Normal logging/caplog plugin remains enabled, third-party plugin autoload disabled; hosted CI passed, user-hardware acceptance pending |
| Frozen Windows EXE, other presets on `main` | Not all supported in the secure runner; unsupported runtimes fail closed | Do not bypass containment to make tests pass |
| Frozen Windows `python-pytest` (PR #43) | Bundles optional pytest, invokes a hidden frozen child, keeps logging inside a snapshot-owned watched file and uses the same AppContainer + Job Object | **Implemented experimental; hosted Windows unittest+pytest secure smoke passed**, physical end-user validation outstanding |

Docker is **not required**. Git worktrees are version-control isolation, not operating-system security isolation. No test runner is allowed to claim full untrusted-code containment without real runtime proof.

## Sensitive data and storage

- `~/.forgeos/tasks.sqlite3`: task descriptions, results, checkpoints and status.
- Evidence archives: original local test logs keyed by SHA-256; **not encrypted at rest**, may contain secrets.
- `~/.forgeos/output_filters.json`: optional local literal filter configuration. Content-hash trust pinning detects changes but does not protect against a malicious same-user process.
- `~/.forgeos/` may also include model/learning/metrics-related local state.
- Separate Git worktrees: generated changes and potentially sensitive project files.

Avoid writing secrets into descriptions or test output; never attach the raw evidence directory to a public GitHub issue, release or project handoff. Reliable encrypted backup/restore and user-oriented retention controls remain future work.

## Performance and memory

Memory design focuses on bounded memory in **ForgeOS itself**: paginated task list, capped GUI history, disk-spooled output with size limits, bounded context and incremental CLI listing. The RAM probe reports ForgeOS's own process working set, **not** Ollama weights, GPU RAM or child build processes. Current 4/8 GiB system targets remain unverified. See [MEMORY_BUDGET.md](MEMORY_BUDGET.md).

## Verification and shipping gates

The workflow suite includes Python regressions, Windows EXE packaging, frozen GUI startup/cleanup checks, synthetic 520-task paging acceptance and a packaged AppContainer boundary smoke. These prove specific conditions on GitHub-hosted runners; they do **not** establish a safe unattended workflow on a real Windows desktop.

Before a release claim: run end-to-end with real local model(s), edit/test/repair, restart/crash recovery, appropriate OS execution isolation, representative memory/hardware configurations, usability and safe distribution checks. Record failures and results in [HANDOFF.md](HANDOFF.md) and update the [roadmap](ROADMAP.md).

## Intended evolution

Future capabilities include beginner-friendly project creation, broad runtime/build adapters, GitHub PR workflows inside ForgeOS, multi-model agent roles, richer fuzz and fault injection, hardware acceptance and full source/handoff export. These must integrate with the canonical modules above rather than duplicate them, and must stay visibly labeled **planned/in progress** until evidence supports implementation.

## Large recovery reports

The CLI offers `resume-safe --stream-report` as an optional JSON Lines
stream. This reuses the canonical checkpoint validator/orchestrator and
avoids collecting an unbounded per-task outcome list in Python memory.
Default `resume-safe` output remains a complete backward-compatible JSON
report. Each streamed task is followed by one final count summary, and
failure exit status is retained. An interrupted process may have emitted
partial lines without a summary; never treat such output as success.


## Experimental managed GGUF and offline inventory boundaries (PR #51)

- `local_models.py` remains the common local OpenAI-compatible client,
  with an optional in-memory bearer token for a trusted local server;
  `core.Ollama` remains the canonical Ollama client.
- `managed_llama.py` is one GUI-session-owned CPU-only subprocess
  lifecycle controller; user-provided trusted executable SHA-256 and
  previously downloaded GGUF SHA-256 are checked before process launch.
  Startup/large hashing run on a background worker, with readiness
  and authenticated catalog responses crossing through the GUI event queue.
- `model_inventory.py` reads `gguf_download.py`'s existing
  pinned-revision manifests with bounded `os.scandir`. Inventory
  metadata does **not** reread full weights; explicit re-verification
  streams SHA-256 on demand and does no inference/network IO.
- `recovery.py` fails closed on the ephemeral `forgeos-managed`
  alias because a random port and transient token are not adequate
  cross-reboot model identities. Pinned binary/model contract plus
  authenticated process recreation are required for unattended resume.

**Unaccepted safety boundaries:** A Windows kill-on-close Job Object and
POSIX process-group cleanup are now implemented for the managed model's
best-effort owned process tree; they remain unaccepted until hosted and
physical Windows process-tree tests pass. Windows **suspended-process startup
before Job assignment**, OS listener-PID verification, native DLL publisher
trust and actual CPU GGUF execution,
real RAM/KV-cache budgeting and model lifecycle update/remove. They
are tracked in the detailed [multi-LLM roadmap](MULTI_LLM_ROADMAP.md).


### GGUF retirement trust boundary (experimental)

The offline model-retirement module reuses the canonical model_inventory manifest/path validation and the gguf_download per-file exclusive lock, rather than creating another model database, scheduler or daemon. The operator chooses a model and explicitly confirms. After a complete streamed hash verification under the lock, a private same-volume directory within the existing model root receives a versioned journal (capped at 4096 bytes) identifying the original revision-specific model, size and SHA-256. Weight and manifest then move in separate atomic renames. A crash between renames leaves a recoverable journal; a restore by random 32-hex ID verifies the pinned identity and bytes, checks both original and archived locations, refuses overwrite and unfinished downloads, then removes its journal only after a full verification of the restored model.

Retired weight files are preserved; this is *not* deletion or free-space recovery. Active inventory excludes the retired directory; a bounded offline retired listing is available. The lock coordinates cooperative ForgeOS downloaders, not foreign processes. File-swap/reparse and in-use native server detection are not comprehensively solved. Physical Windows acceptance is mandatory.


On POSIX systems, the retirement writer synchronizes the directory containing its durable journal before moving files, then fsyncs source/destination directories after each rename. Journal removal receives the same treatment. Windows has no equivalent portable directory fsync through Python's standard library, so NTFS power-loss resilience remains experimental and requires live recovery testing.
