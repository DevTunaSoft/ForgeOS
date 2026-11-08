# ForgeOS cumulative engineering handoff — v0.1.0

## Current state
Initial foundation only. Python standard-library runtime, Tkinter desktop prototype, SQLite task store and recovery, Ollama planning, explicit allowlisted tests, CLI. This is **not** an autonomous software builder or production release.

## Architecture
- src/forgeos/core.py: task persistence, local Ollama planning and test runner.
- src/forgeos/cli.py: entry point and task workflows.
- src/forgeos/gui.py: Tk GUI, background worker and results display.
- tests/test_core.py: persistence, recovery, test safety and failure behavior.
- docs/REQUIREMENTS.md and docs/ROADMAP.md: cumulative mission and phases.
- .github/workflows/ci.yml: proposed Windows and Linux test execution.

## Verified local evidence before GitHub import
10 unit tests passed and Python syntax compilation passed under Linux. GitHub-hosted Windows validation is pending workflow execution. No true Windows GUI smoke test, local Ollama model run or EXE/APK build has yet been performed.

## Known limitations
- Worker thread reads Tk variables, requiring UI-thread handoff before native Windows validation.
- Host tests execute arbitrary trusted-project code without sandboxing.
- SQLite records are not encrypted, and interrupted operations restart from queued.
- No event journal, backup/restore or granular progress checkpoints.
- Ollama requests cannot yet be cancelled; no autonomous fix loop.
- No GitHub integration from the application itself.
- GitHub repository visibility may be public; never upload credentials or private data.

## Run and recover
On Windows: install Python 3.10+ and Git; from repository root run 'py -m pip install -e .' then 'py -m forgeos gui'. For tests, set PYTHONPATH=src and run 'py -m unittest discover -s tests -v'. Ollama is optional for planning. DB is ~/.forgeos/tasks.sqlite3. Use 'py -m forgeos recover' to requeue interrupted tasks. Safeguard SQLite with its backup API.

## Next autonomous work
1. Observe CI results; fix every failure and rerun.
2. Eliminate Tk cross-thread access, introduce GUI-level smoke tests and cancellation.
3. Introduce structured event journal, integrity checks and transactional backup.
4. Add safe Git worktree operations and bounded patch application.
5. Add independent test-and-repair loop with auditable evidence.
6. Build and verify a real Windows installer and update user-facing documentation.
7. Keep cumulative complete-source deliveries and comprehensive handoffs.

## Hardening round — 2026-11-07

- Corrected Tkinter cross-thread operations: GUI state captured before worker creation; worker results sent through a Queue; main GUI thread polls and updates widgets. Root.after is no longer invoked by workers.
- Added seeded malformed/Unicode task fuzzing, 75-task interruption/recovery stress, invalid test-preset fuzzing, and injected executable-missing/timeout tests.
- Pending: observe Windows/Linux CI for these changes, inspect failures, and perform real Windows GUI interaction tests. No production-ready claim.
- Known: closing during a running job remains blocked until completion; Ollama planning can take up to 180 seconds. No cancellation, sandbox or worker subprocess isolation. Test runner executes trusted repository code on the host. Full autonomous code-editing pipeline remains future work.

## Milestone: isolated Git workspaces (2026-11-07)

- Added src/forgeos/workspace.py with Git-root validation, task-derived branch names, out-of-tree worktree creation, tracked diff inspection, and refusal to remove dirty/untracked workspaces.
- Added tests/test_workspace.py: isolated edits, safe cleanup, duplicate task branches, nested/existing destination rejection, invalid IDs, untracked data protection and non-repository rejection.
- Git must be installed for worktree operations. Tests skip if Git is absent; this skip must not be confused with verified worktree behavior.
- This is NOT a sandbox: worktree code can still access host resources. No AI patch application, autonomous repair, model orchestration, or Windows GUI smoke validation yet.
- Next: verify CI, review platform behavior, add safe patch validation and execution isolation, then connect workspace orchestration to task engine.

## Autonomous task-engine prototype — 2026-11-07

- New src/forgeos/engine.py: strict JSON edit manifests, bounded files/size, traversal and symlink rejection, local Ollama generation, Git worktree editing, allowlisted test presets, up to 5 repair retries, persisted task status and retained worktree.
- CLI: `python -m forgeos run TASK_ID --workspace-parent PATH --allow-host-tests --max-repairs 2` (requires local Ollama and installed Git). No automatic commit, merge or deployment.
- New tests/test_engine.py: malicious paths, symlink escape, atomic validation, duplicate/oversized edits, successful write, end-to-end failing-first then repaired test, exhaustion, invalid model output and opt-in gating.
- CRITICAL: host tests are NOT sandboxed. Never enable --allow-host-tests for untrusted repositories or model-generated code on a sensitive host. Worktrees isolate source changes, not process privileges or network/filesystem access.
- Known: writes are not transactional across disk failure; repair model may replace existing files; Git branch names are one-shot per task; recovery currently requeues interrupted tasks but does not automatically attach to retained worktrees. No GUI run button, no autonomous commits, no actual Ollama runtime test.
- Follow-up: CI Windows/Linux validation, then process sandbox, durable execution journal and resumable workspaces before unattended operation.

## Desktop dashboard milestone — 2026-11-07

- Replaced minimal Tkinter screen with a two-tab Development / Activity & diagnostics dashboard. Added project picker, natural-language request, local model and test preset, repair retries, worktree folder, status counts and task queue.
- GUI now invokes execute_task() in a worker thread and displays JSON results; Tk operations remain on main thread through Queue polling. The user must explicitly enable host test execution because generated code is not sandboxed.
- Existing planning and tests-only actions retained; interrupted tasks are requeued without silently running them. This is not a fully unattended queue and does not provide real pause/resume/cancel yet.
- Added headless GUI contract tests. Actual interactive Windows GUI smoke testing, screen reader usability, and display scaling remain unverified.
- IMPORTANT: tests-only mode executes project code on host as before. Neither test execution path is isolated from host resources. Do not run untrusted projects on a sensitive machine.

## Windows EXE packaging milestone

- packaging/launcher.py provides GUI entry point and headless frozen --smoke mode.
- .github/workflows/windows-exe.yml runs Windows tests, PyInstaller one-file GUI build, EXE existence/size check, packaged import smoke test, and uploads a 14-day artifact.
- docs/WINDOWS_BUILD.md explains artifact retrieval, runtime prerequisites, local build, and limitations.
- Next: verify Windows packaging CI, run real Windows GUI interaction tests, and test a real Ollama-driven edit/repair loop. No claim of fully portable or production-ready app.

## Journaled edits and interrupted-task recovery (2026-11-07)

- Multi-file edits stage new files and backups in workspace/.forgeos/pending-edit with SHA-256 checksums and a journal before replacement.
- Python-level write failures attempt rollback. After a hard crash, run: python -m forgeos recover-edits PATH_TO_TASK_WORKSPACE. Recovery refuses to overwrite conflicting external edits.
- Startup recovery blocks interrupted editing/testing tasks for inspection rather than silently requeueing them. Planning tasks may requeue. Existing worktrees are preserved.
- This is not filesystem-wide atomicity, not automatic job continuation, and not a sandbox. Real Windows power-loss and interactive acceptance tests remain outstanding.

## PR #17 — conservative automatic recovery orchestration (2026-11-07)

- Added `src/forgeos/recovery.py` with `resume_safe`: first marks interrupted execution tasks blocked, then inspects only blocked tasks with a persisted `test_pending` checkpoint and workspace location matching the task-derived worktree path.
- Added CLI `python -m forgeos resume-safe --workspace-parent PATH --allow-host-tests` with optional test preset and repair budget.
- Each eligible task is passed through the existing guarded `execute_task(..., resume=True)` path, which validates Git worktree registration, branch, transaction journal and edited-file SHA-256 hashes. Per-task exceptions are recorded; other tasks continue.
- Fail-closed: without explicit `--allow-host-tests`, orchestration refuses execution before modifying the task database. This is **not sandboxed**; do not use on untrusted projects.
- Added `tests/test_recovery.py`: authorization, filtering, malformed checkpoints, task-by-task error isolation.
- Limitations: this is **explicitly invoked** recovery, not background startup execution. AI-generation and repair-pending phases are not resumable. It does not run untrusted tests in a restricted Windows environment. No actual physical Windows GUI/Ollama acceptance yet.
- Next: verify Linux/Windows CI and EXE packaging; resolve any failures; implement persisted repair-pending checkpoints and workspace-wide integrity checks; add Windows-native restricted execution before enabling unattended host tests; update GUI and full Windows acceptance.

## PR #18 — repair-pending checkpoint recovery (2026-11-07)

- Fixed a recovery data-loss gap: after a failed test, the transition back to `editing` previously cleared `tasks.result`, so interruption during AI repair generation could erase the last safe recovery context.
- The engine now persists `repair_pending` checkpoints containing workspace, branch, prior edited paths, SHA-256 hashes, next repair attempt number and bounded prior test output.
- Resume now supports both `test_pending` and `repair_pending`. Repair-pending resume reconstructs the repair prompt from the stored failure and generates the next edit without rerunning the already-failed test.
- Integrity validation remains fail-closed: workspace, branch, edited-path list, hashes and saved attempt bounds must validate before continuing.
- The recovery orchestrator now treats valid repair-pending checkpoints as eligible.
- Added interruption injection covering a crash/KeyboardInterrupt during repair generation and successful continuation at attempt 2, plus orchestrator coverage for repair-pending eligibility.
- Still not a security sandbox. Host test execution remains explicitly gated. Windows-native restricted execution and physical Windows/Ollama acceptance remain outstanding.

## PR #19 — persisted execution-contract recovery (2026-11-07)

- Resumable checkpoints now bind the task to the original execution contract: test preset, maximum repair budget, and local model identifier.
- Direct engine resume fails closed if the caller supplies different execution settings from the saved checkpoint.
- `resume-safe` now validates the saved contract and replays each task using its own preset, repair budget and Ollama model instead of applying one global default to every interrupted task.
- Optional `resume-safe --preset` and `--max-repairs` arguments are now safety overrides: if supplied, they must match the checkpoint rather than silently changing behavior.
- Legacy resumable checkpoints that predate contract persistence are deliberately skipped by automatic recovery because their original execution settings cannot be proven.
- Added tests for per-task contract replay, mismatched overrides, missing contracts and direct-engine rejection of changed settings.
- Host execution remains unsandboxed and explicitly gated; Windows-native restricted execution remains the next major security milestone.

## PR #20 — task execution leases and race-condition hardening (2026-11-07)

- Added SQLite-backed per-task execution leases with a 10-minute stale threshold, owner tokens, heartbeats, explicit release, and cleanup of implausible future timestamps.
- Added an atomic status transition that preserves persisted checkpoint/result data while preventing stale read-then-write races.
- The autonomous engine now acquires one exclusive lease per task, transitions the task atomically, refreshes the lease around long model/test calls, and releases the lease in a `finally` block so KeyboardInterrupt does not strand a live lease.
- `Store.recover()` no longer quarantines editing/testing tasks that still have a live execution lease. Stale leases are removed and their tasks can then be safely blocked for inspection.
- Duplicate execution of the same task fails closed before workspace mutation.
- Unauthorized host execution is rejected before any lease-table mutation, and leases cannot be created for nonexistent tasks.
- Added regression tests covering lease exclusion across separate SQLite connections, checkpoint-preserving atomic transitions, stale-lease reclamation, duplicate-run rejection, KeyboardInterrupt cleanup, unauthorized execution, and unknown task IDs.
- This protects task coordination and recovery correctness; it is not an OS security sandbox. Windows-native restricted execution remains the next major security milestone.

## PR #21 — Windows Job Object process-tree containment (2026-11-07)

- Added `src/forgeos/windows_containment.py`, a dependency-free ctypes wrapper around Windows Job Objects for allowlisted test execution.
- Windows test processes are created **suspended**, then assigned to the configured Job Object before the primary thread is resumed. This prevents test code from running before ForgeOS attaches the containment policy.
- Current limits: kill the full associated process tree when the Job Object closes, maximum 64 active processes, 1 GiB committed memory per process, and 2 GiB committed memory for the job.
- Timeout handling terminates the entire Job Object rather than only the direct test process. A Windows runtime test deliberately spawns a delayed child and verifies that the child cannot survive timeout cleanup to write a marker later.
- The Job Object is configured without breakaway flags so ordinary descendants remain associated with the process tree. Active-process-limit coverage verifies that excessive child creation is denied.
- Failure hardening: if Windows creates a child suspended but Job Object assignment fails, ForgeOS explicitly terminates that suspended child before closing handles.
- `core.run_test` automatically routes Windows allowlisted test presets through this containment layer. Linux behavior remains ordinary no-shell subprocess execution.
- This is **containment, not a security sandbox**. Job Objects do not remove the user's token privileges, prevent filesystem access, or block networking. Host-test execution therefore remains explicitly gated. Restricted-token / ACL / network-boundary work remains required before claiming safe unattended execution of untrusted generated code.
- Required verification: Windows 3.10/3.12 runtime tests, timeout/child cleanup, process-limit behavior, full regression suite, PyInstaller Windows EXE smoke. Physical Windows acceptance remains separate.

## PR #22 — Windows privilege-reduced child token (2026-11-07)

- Extended the Windows Job Object runner to create a restricted copy of the current primary token with `CreateRestrictedToken(DISABLE_MAX_PRIVILEGE)` and launch the suspended test process with `CreateProcessAsUserW` before Job Object assignment/resume.
- Microsoft documents `DISABLE_MAX_PRIVILEGE` as disabling every privilege except `SeChangeNotifyPrivilege`. The Windows runtime test queries the child token's `TokenPrivileges` information and requires at most one privilege to remain.
- Two more aggressive variants were tested and rejected during CI hardening:
  - adding restricting SIDs caused Python to fail DLL initialization because the second ACL access check blocked required runtime files;
  - adding `LUA_TOKEN` also caused DLL initialization failure on the GitHub Windows runner because filtered group access no longer matched the toolchain installation ACLs.
  Both failures were root-caused and the incompatible settings were removed rather than bypassed with an unrestricted fallback.
- There is **no automatic fallback** from privilege-reduced launch to unrestricted launch. If `CreateRestrictedToken` or `CreateProcessAsUserW` fails, the test run fails closed. An unrestricted Job Object mode exists only as an explicit diagnostic parameter and is not used by `core.run_test`.
- The privilege-reduced token is combined with the PR #21 Job Object process-tree/memory/process-count/timeout controls.
- This materially reduces process privileges but is still not a complete sandbox: the child retains the user's ordinary filesystem and network access, and group/SID filtering is not yet enabled. Host execution remains explicitly gated.
- Next security layers: workspace-specific ACL boundary, deny access to sensitive ForgeOS/user paths where feasible, then outbound network restriction or a stronger Windows-native isolation primitive. Each layer must preserve supported build tools and be proven on Windows CI before it becomes mandatory.

## PR #23 candidate — Windows Low-integrity workspace boundary (2026-11-07)

- Added an optional Mandatory Integrity Control capability on top of the existing privilege-reduced token and Job Object containment.
- A Low-integrity child token is produced by applying `TokenIntegrityLevel` with the Windows `WinLowLabelSid` to the already privilege-reduced primary token before process creation.
- Added `prepare_low_integrity_workspace()`, which uses the built-in Windows `icacls.exe /setintegritylevel (OI)(CI)L /T /L /Q` command with `shell=False` to label a ForgeOS-owned disposable workspace and its descendants Low integrity.
- This helper is intentionally **not** wired into every task yet. It must never be applied automatically to the user's source repository. It is designed for disposable ForgeOS task worktrees/workspaces only.
- Added a Windows runtime boundary test: the same Low-integrity child successfully writes `inside.txt` inside the Low-labeled workspace, then attempts to create a file in the normal Medium-integrity parent. The outside write must fail and the marker must remain absent.
- Low-integrity execution requires privilege-reduced execution; ForgeOS rejects the combination `low_integrity=True, restrict_privileges=False`.
- Security boundary: Windows Mandatory Integrity Control prevents Low-integrity subjects from writing to Medium-integrity objects, but this does not prevent reading Medium-integrity files and does not block network access. This is a meaningful no-write-up boundary, not a complete sandbox.
- Integration remains deliberately staged because some build tools use caches/temp directories outside the Git worktree. Before enabling Low integrity by default, ForgeOS must create dedicated writable Low-integrity temp/cache locations and redirect supported toolchains into those locations.
- Verification evidence required before merge: Linux regression tests remain green; Windows 3.10 and 3.12 must both prove the inside-write/outside-block behavior; final PR must also pass Windows EXE packaging and frozen smoke validation.

## PR #24 candidate — autonomous engine Low-integrity execution snapshots (2026-11-07)

- The autonomous Windows engine no longer runs generated project tests in the editable Git worktree. Each test attempt creates a fresh sibling execution snapshot and runs the test suite against that copy.
- The execution snapshot is labeled Windows Low integrity and the child process uses the existing privilege-reduced token plus Job Object process-tree containment. The editable worktree remains normal Medium integrity.
- Added a direct adversarial engine regression: test code running from the snapshot deliberately locates the real editable worktree and attempts to create a file there. Windows must deny that write, the test must still complete, and ForgeOS verifies the editable worktree and runtime cleanup remain unchanged.
- Snapshot mutations and build artifacts are disposable. A fresh snapshot is recreated for every test attempt so artifacts from a failed iteration cannot contaminate the next repair cycle.
- ForgeOS internal transaction metadata under `.forgeos` is excluded from the snapshot, preventing test code from directly mutating edit-recovery journals in the copied tree.
- Snapshot ownership metadata is stored **outside** the Low-integrity directory. The Low child therefore cannot delete or forge ForgeOS's ownership marker. Cleanup refuses to remove a preexisting directory without the exact marker and workspace/runtime identity.
- Common writable toolchain paths are redirected into `.forgeos-runtime` inside the disposable snapshot: TEMP/TMP, Python bytecode/cache, pip, npm, Cargo/target, Go GOPATH/GOCACHE, .NET/NuGet, Gradle, HOME and XDG cache.
- Windows process creation now supports an explicit UTF-16 environment block so those redirected paths reach the restricted child without shell invocation.
- `.env*` files are omitted from execution copies as defense-in-depth. **This is not a confidentiality sandbox:** Low integrity blocks write-up but still permits reading Medium-integrity files, including files outside the snapshot if generated code discovers them. Outbound networking is also still available.
- Direct/manual `run_test` behavior is unchanged by default. The Low-integrity disposable snapshot is used automatically only by the autonomous ForgeOS worktree engine on Windows.
- Remaining major security work: isolate read access and networking (preferably using a stronger Windows-native principal/container boundary), then validate supported Python/npm/Cargo/Go/.NET toolchains in the isolated runtime. Snapshot copy performance for very large repositories also needs profiling/optimization.

## PR #25 candidate — explicit handle inheritance and UI isolation (2026-11-07)

- Replaced broad inheritable-handle process startup with `STARTUPINFOEXW` and `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`. Only ForgeOS's intended stdin handle and combined stdout/stderr handle are eligible for inheritance by the child.
- This follows Microsoft's current process-creation security guidance: inheritable handles can expose sensitive file/socket/token resources, and processes that require inherited standard handles should use an explicit handle list rather than inheriting every inheritable parent handle.
- Added an adversarial Windows runtime test: ForgeOS deliberately opens a separate parent-only file, marks that handle inheritable, passes its numeric handle value to the child, and the child attempts `WriteFile`. The write must fail and the parent file must remain empty.
- Added all eight `JOBOBJECT_BASIC_UI_RESTRICTIONS` controls to the test Job Object: external USER handles, clipboard read, clipboard write, system-parameter changes, display settings, global atoms, desktop create/switch, and ExitWindows calls are restricted.
- ForgeOS immediately queries `JobObjectBasicUIRestrictions` after setting the policy and fails closed unless the exact requested bitmask is active.
- The Windows result records `handle_allowlist=True` and the verified UI restriction mask for diagnostics.
- Extended process startup resources are cleaned on every path, including token-creation or process-creation failure. Option/environment validation occurs before the Job Object and attribute list are allocated.
- These protections complement the existing Job Object resource limits, privilege-reduced token, Low-integrity execution snapshot, and write-back protection. They still do not block file reads or outbound networking.
- Next major isolation candidate: AppContainer/LPAC via `STARTUPINFOEX` security capabilities, because Windows can create an AppContainer token/namespace at process creation. Compatibility with host-installed Python/npm/Cargo/Go/.NET binaries and explicit snapshot ACL grants must be proven before it can replace the current boundary.

## PR #26 candidate — classic Windows AppContainer capability and isolation probe (2026-11-07)

- Added `src/forgeos/appcontainer.py` as a capability-first experiment using the stable classic AppContainer APIs from `userenv.dll`. This does **not** replace the production autonomous runner yet.
- The probe creates a unique temporary AppContainer profile, supplies `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES` with the profile SID and **zero capability SIDs**, creates the child suspended, opens the child's token from the parent, and requires `TokenIsAppContainer == 1` and a zero `TokenCapabilities` group count before resuming it.
- There is no fallback to the existing restricted-token/Low-integrity path inside the AppContainer probe. If AppContainer creation or launch fails, the probe reports unavailable rather than claiming stronger isolation.
- Windows 3.10 and 3.12 hosted-runner tests have verified basic zero-capability AppContainer process launch using the built-in `cmd.exe`.
- Added explicit filesystem-boundary probing:
  - a temporary root gets a non-inheriting AppContainer SID read/traverse ACE;
  - one child directory gets an inheritable modify ACE and Low mandatory-integrity label;
  - the AppContainer must create a file using a relative path inside that explicitly granted directory;
  - it must fail to read an ungranted sibling file one level outside the granted directory.
- Added independent network probing:
  - the zero-capability AppContainer first runs built-in `curl.exe --version` successfully, proving the client executable itself works inside the container;
  - the parent listens on a real localhost TCP port;
  - AppContainer curl then attempts the loopback URL;
  - curl must fail and the parent listener must receive no connection.
  This prevents a missing/broken curl executable from being mistaken for successful network isolation.
- Temporary profile cleanup requires `DeleteAppContainerProfile` to return a successful HRESULT. Microsoft documents that API as the profile deletion operation and notes deletion of a nonexistent profile also succeeds. `DeriveAppContainerSidFromAppContainerName` is deterministic SID derivation and is **not** a supported profile-existence check; an earlier attempt to use it as post-delete verification was removed after Windows CI exposed the semantic error.
- The probe frees every returned SID and closes process/thread/token handles before profile deletion. Cleanup failure turns the probe result into unavailable.
- Current production boundary remains PR #24/#25: disposable Low-integrity snapshots + privilege reduction + Job Object limits/UI restrictions + explicit inherited-handle allowlist. AppContainer will only become a production backend after supported test runtimes can execute inside it without granting broad host filesystem access.
- Next compatibility work: probe the actual Python test runtime under AppContainer, determine the narrow executable/library ACL grants or bundled-runtime strategy required, then repeat for npm/Cargo/Go/.NET. Do not globally ACL host toolchain installations merely to make the sandbox pass.

### PR #26 additional Python-runtime compatibility evidence

- The active GitHub-hosted CPython installation initially failed inside AppContainer with Windows status `0xC0000135` (`STATUS_DLL_NOT_FOUND`). This was treated as positive isolation evidence: AppContainer could not read the hosted Python runtime/DLL tree by default.
- The probe was hardened rather than weakening AppContainer. It temporarily grants only the unique AppContainer SID read/execute access to the specific CPython runtime targets required for startup: the interpreter, Python DLLs, standard-library/DLL directories, version zip when present, and VC runtime files when present.
- With those temporary SID-specific RX grants, isolated/no-site Python starts successfully and writes a verification file only in the explicitly granted AppContainer workspace on Windows Python 3.10 and 3.12.
- Every temporary runtime ACE is removed in a `finally` path. After revocation, the probe reruns Python and requires runtime visibility to return exactly to its pre-grant state. Cleanup failure makes the probe unavailable rather than silently continuing.
- This experiment intentionally modifies the ephemeral CI Python ACL only during the probe. Production ForgeOS should not ACL arbitrary user/global toolchains. The intended production architecture is a ForgeOS-owned/bundled runtime whose ACL can be permanently and narrowly designed for AppContainer execution.
- This establishes technical feasibility for a future Python AppContainer backend: zero network capabilities, explicit snapshot ACL only, narrow runtime RX, no broad user-file grants.

## PR #27 — production AppContainer integration for frozen Windows execution (2026-11-07)

- The frozen Windows product now routes supported autonomous tests through a zero-capability AppContainer runner instead of the prior Low-integrity-only path.
- Production Python `unittest` execution uses the frozen `ForgeOS.exe` itself as the ForgeOS-owned Python runtime. The sandbox therefore does not need RX grants to an arbitrary separately installed Python toolchain.
- The AppContainer launch combines: zero capability SIDs, disposable Low-integrity execution snapshot, Job Object process/memory limits, kill-on-close tree cleanup, Job UI restrictions, and an explicit inherited-handle allowlist.
- No network capability is granted. The temporary AppContainer profile is deleted after each execution, and explicit ACL grants to the snapshot/executable are revoked before profile teardown.
- The frozen execution path is fail-closed: if AppContainer launch fails or the preset is not yet supported by the secure runner, ForgeOS does not silently fall back to the older host runner.
- Current secure production preset scope is deliberately limited to `python-unittest`. `python-pytest`, npm, cargo, Go, and .NET remain unsupported by this new secure path until ForgeOS-owned runtime/toolchain provisioning is implemented for them.
- Source/development mode keeps the older restricted-token + Job Object + Low-integrity runner so the unfrozen source test suite can validate code before packaging. This is explicitly a development path, not a production fallback.
- The execution snapshot environment no longer copies arbitrary parent environment variables. It now uses a narrow host-variable allowlist plus ForgeOS-owned temp/cache/home paths, preventing parent API keys/tokens from being inherited by generated tests.
- Hidden frozen child mode writes unittest results to a validated file inside the disposable snapshot, avoiding reliance on console streams in the windowed PyInstaller executable while preserving failure output for AI repair.
- Windows EXE CI now runs a frozen secure-boundary smoke test before artifact upload. The smoke attempts: write outside the snapshot, inherit a parent secret, and connect to a parent localhost listener. All must be blocked.
- Added source regression coverage proving the frozen engine route cannot call the weaker runner, unsupported secure presets fail closed, sandbox output cannot escape the snapshot, and parent-secret environment inheritance is blocked.
- Remaining work after this PR: validate the frozen smoke on Windows CI, repair any PyInstaller/AppContainer-specific defects, then expand ForgeOS-owned secure runtimes to pytest/npm/cargo/Go/.NET and add deeper registry/process/IPC/reparse-point escape testing.

### PR #27 frozen AppContainer bootstrap defect and correction

- First packaged secure smoke reached the real AppContainer process-creation path and failed closed with Windows error 203 (`ERROR_ENVVAR_NOT_FOUND`) before any generated test code executed.
- Root cause was the deliberately minimal child environment omitting `LOCALAPPDATA`. On Windows AppContainer startup, `CreateProcessW` needs the caller's real LocalAppData location to resolve the temporary AppContainer package profile.
- Correction: the production secure runner injects the validated host `LOCALAPPDATA` plus a one-use `FORGEOS_BOOTSTRAP_LOCALAPPDATA=1` marker only into the AppContainer creation environment.
- The hidden frozen sandbox child requires that marker, removes it before unittest discovery, and replaces `LOCALAPPDATA` and `APPDATA` with directories owned by the disposable execution snapshot. Generated project code therefore never sees the host LocalAppData path.
- Source tests verify the bootstrap contract and scrub. The frozen secure smoke now additionally asserts the marker is absent and both AppData variables resolve inside the disposable snapshot before project tests execute.
- No fallback was enabled as part of this correction; launch failure continues to block the secure production path.


## RTK-inspired integration PR #28 (in progress, 2026-11-07)

Branch: feat/rtk-inspired-diagnostics. Base: main.
Scope tracker: docs/RTK_ENHANCEMENTS.md (52-feature program).

Code:
- diagnostics.py: atomic gzip evidence archive, SHA-256 validation,
  on-demand recall, bounded error-centric output summary, JSON/text/raw parsers.
- error_learning.py: private aggregate repair memory storing signature hashes
  and verified outcomes only. Review eligibility requires repeated success.
- optimizer_metrics.py: aggregate context byte reduction, estimated token savings,
  and fallback health counts, without storing raw output or project paths.
- core.py and windows_containment.py: full test output reaches optimizer instead of
  silent 16K-character tail truncation, preserve authoritative test outcomes.
- engine.py: record verified repair outcomes after successful retest only.
- gui.py and cli.py: one GUI efficiency display and recovery/stats commands.
- tests/test_diagnostics.py, test_error_learning.py, test_optimizer_metrics.py:
  roundtrip/fidelity/corruption/symlink/Unicode/concurrency/regression coverage.

Evidence:
- Earlier diagnostic-only commits passed Ubuntu/Windows 3.10/3.12 CI and
  Windows packaging.
- Additional learning integration revealed Windows SQLite connection leaks in
  GitHub Actions. Both learning and metrics stores now close connections with
  a contextmanager/finally. The final-head CI must be inspected before merging.
- No interactive user Windows installation has been exercised; no claim of
  production-ready autonomy. Separate AppContainer security PR #27 is still
  subject to its own acceptance gates.

Immediate next steps: verify all four CI matrix jobs and Windows packaging on
the latest head; fix root causes, run regression, audit logging privacy and
repository merge interactions with PR #27, then proceed to bounded streaming
capture, retention policies, secure model routing, and GUI diagnostics.


## RTK integration continuation: AST context / local model routing

Cumulative source branch: feat/rtk-inspired-diagnostics, draft PR #28.
Additional source modules:
- src/forgeos/context_index.py — privacy-preserving, bounded deterministic
  file inventory and Python class/function AST signatures; engine calls
  relevance selection on initial generation and all repair/resume prompts.
- src/forgeos/model_routing.py — local-only opt-in selection of installed
  Ollama models, size/complexity heuristic, no paid provider, pin before
  checkpoint. Core Ollama plan/generate and engine support model=auto.
- tests/test_context_index.py and tests/test_model_routing.py — relevance,
  output bounds, symlink avoidance, model constraints and no remote endpoint.

Root cause / resolved defects:
- Initial UI query relevance used a three-letter minimum and missed "ui".
  Corrected to two-letter tokens; later source-context CI and Windows EXE
  packaging passed on commit 48f3a348.
- SQLite connection leaks blocked Windows 3.12 test directory cleanup.
  Close connections explicitly through contextmanager/finally, checked by
  passing Linux/Windows CI and executable packaging on commit 55ec5eae.
- Repair memory originally recorded only successful repairs; a follow-up
  changed the engine to record failures of attempted repairs, with explicit
  regression assertions (latest verification may still be running).

Next action: check all tests and packaging for the current PR head, identify
any failures, fix and rerun. PR #27 (AppContainer) is independently pending.
Neither branch should be merged solely because a Windows build artifact exists.


### PR #27 AppContainer security merge reconciliation
PR #27 merged into main. This branch preserves the frozen Windows
zero-capability AppContainer path, new sandbox tests, and fail-closed policy.
Compressed output is generated only after the secure process terminates.
Use the Windows frozen smoke check to validate this joint integration.


## Phase 2 output rules implementation (CI still required)

Feature branch feat/rtk-phase2-optimization-rules, based on merged phase 1.
Added source src/forgeos/output_rules.py and tests/test_output_rules.py.
Combined with diagnostics.py after full raw-output archiving and before the
bounded AI-facing summary. The original SHA-256 evidence is recoverable.
No optimizations may alter the command, pass/fail result or exit code.
Built-in noncritical adjacent duplicate suppression; optional JSON literal
rules only after SHA-256 trust pinning; config tampering invalidates trust.
CLI: forgeos filters status; forgeos filters trust. If the summary including
recall reference costs more bytes than raw output, return raw output instead.
Tests for modified rules, error-preserving dedup, Unicode, symlink rejection,
invalid configurations, and small-output noninflation.
Block merge until Ubuntu and Windows 3.10/3.12 CI + packaged secure smoke pass.


## RTK Phase 3 — diagnostic comparison and recorded model identity (CI pending)

Source branch feat/rtk-phase3-repair-insights. New modules:
- src/forgeos/error_learning.py: consolidated deterministic, bounded advisory failure
  categories (security boundary, resource, permission, timeout, dependency,
  syntax, assertion, network, environment and build); no raw secrets returned.
- src/forgeos/insights.py: validated task result parsing and compact/raw
  comparison models, read original evidence by content-addressed SHA-256 only,
  bounded previews, rejects malformed identifiers and oversize JSON.
- src/forgeos/gui.py: explicit compare button opens two read-only side-by-side
  views within the existing ForgeOS Tkinter application. The efficiency panel
  reports read-only repair candidates, current filter trust health, and
  saved-byte metrics; no separate app is required.
- src/forgeos/engine.py: persists the exact selected local model string in
  completed and failed task results for audit.
- src/forgeos/diagnostics.py: failure classification attached alongside parsed
  test counts. It never changes real process outcomes.
- Tests: tests/test_insights.py, tests/test_failure_types.py, and
  updated tests/test_engine.py.

Important: the returned GUI evidence may contain secrets; reveal only upon
explicit action and do not export private archives to GitHub or release ZIPs.
Real Windows GUI interaction, renderer behavior, full local Ollama operations,
and comprehensive data exfiltration review remain unverified. Never describe
the integration as production-ready based only on headless package smoke.


## Enhancement-duplication checkpoint (52 outcomes, not 52 systems)

The codebase and RTK enhancement tracker were audited together on 2026-11-07.
The source-backed map is docs/ENHANCEMENT_DEDUP_AUDIT.md and covers 52/52
outcomes: 22 existing baselines, 13 extensions of those systems, 12 genuinely
new capability gaps, and 5 PR #31 items awaiting merge. These categories
describe source ownership, not a verified percentage of production readiness.
A new code subsystem must cite the relevant canonical owner and prove why
extending it would not meet the acceptance requirement. No independent GUI,
evidence store, command runner, configuration transaction engine, repair DB,
or local model router should be built for an overlapping enhancement.

During the audit the new standalone advisory failure classifier was removed
from PR #31. Its implementation now lives in the existing
src/forgeos/error_learning.py module; fingerprint() historical hashes,
category values and SQLite schema remain unchanged. Diagnostics uses the
unified owner. Added tests/test_enhancement_audit.py verifies 52/52 mapped
outcomes, real source-owner paths, shared-group alignment and no shadow
failure_types.py module. The changes must pass full CI and the Windows
frozen security smoke on the final PR head before merge.


## PR #32 low-memory integration (current development, tests not yet final)

Branch: feat/low-memory-streamed-execution. Parent main contains merged
Phase 3 and the 52-feature duplication audit. New modules are shared I/O
support (output_capture.py) and a synthetic benchmark, NOT an alternate
test runner, model service, evidence database or user application.

Code changes:
- output_capture.py: 64-KiB streamed reader, 32-MiB disk spool, 2-MiB preview,
  bounded head/tail and explicit overflow markers; native Windows outputs
  are also read via this bounded helper.
- diagnostics.py: content-addressed gzip put_stream(), SHA-verified streaming
  preview(), complete archival retained for <=32 MiB. Partial capture never
  gets silently described as full evidence.
- model_routing.py: native Windows available-memory probing and Linux
  MemAvailable/cgroup fallback; conservative file-size preflight on automatic
  model selection. Does not promise a particular model will fit VRAM.
- core.py: bounded Ollama API response and model catalog JSON size,
  memory-dependent num_ctx and keep_alive=0 on low-memory machines,
  streaming Unix process IO, brief SQLite GUI task rows.
- context_index.py and engine.py: RAM-dependent source context budget;
  captured output without a full original blocks autonomous completion.
- windows_containment.py, appcontainer.py, secure_runner.py: post-execution
  output files no longer decoded whole into Python RAM.
- gui.py and insights.py: default model auto; bounded activity text and
  verified original-evidence preview. The one GUI is retained.
- tools/memory_benchmark.py: optional standalone local synthetic output
  benchmark, reports Python traced allocation peak and time only.

Tests: test_output_capture.py, test_model_routing.py, test_core.py,
test_context_index.py, test_diagnostics.py, test_engine.py,
test_gui_contract.py, test_insights.py, test_memory_benchmark.py.
Known failures: an early test fixture emitted ~660 KiB, not the intended
>2 MiB needed to force a preview. Fixed by increasing output to ~2.8 MiB.
The early build also preceded put_stream(); subsequent commits integrated it.
Treat the latest CI head as unverified until all four Ubuntu/Windows jobs and
Windows executable including AppContainer security smoke succeed.

Update: a 500-ms native Windows temporary stdout file watchdog was added
in output_capture.py and reused by both native Windows Job Object executors.
It terminates their jobs after the 32 MiB file limit. Remaining concern:
the child-written separate frozen unittest output file is not yet watched
until process exit; this requires additional disk-pressure hardening. Real 4/8 GiB Windows GUI/inference/hardware
acceptance, peak working-set process-tree accounting and long-running
resource-pressure tests remain unverified. Output evidence is unencrypted;
never include archives in release assets. No new paid APIs or Docker.


Low-memory follow-up corrections: a Windows-only failing assertion was traced
to CRLF normalization differences between former read_text() and raw byte
decoding in the new bounded output reader. The preview layer now restores
universal newline behavior while preserving exact raw archive bytes; its
regression assertion was changed to avoid difflib recursion on huge strings.
A shared native output watchdog checks Job Object stdout file size only
twice per second, with no background agent. Invalid UTF-8 original evidence
can be SHA-verified and displayed safely with replacement markers. Tests
cover native disk limit termination, successful process exit and binary
preview behavior. The final PR head still needs independent CI proof.

## PR #33 — secure frozen unittest output disk guard

Branch: feat/secure-test-output-watchdog, based on merged PR #32 (a811b40e).
The existing output_capture.py native Windows job wait polls every 500 ms.
It now inspects the frozen unittest's dedicated .forgeos-runtime/test-output.txt
as well as inherited stdout. The monitor:
- accepts only the exact snapshot-owned output path;
- rejects symlinks, Windows reparse points/junctions and nonregular files;
- accepts a legitimately not-yet-created child file;
- checks size at each polling interval and after process exit;
- terminates the contained Job Object on unsafe path, read failure or 32 MiB
  overflow, without fallback to unrestricted host execution;
- preserves original SHA evidence when the output is within the limit.

secure_runner.py threads the fixed path through run_secure_appcontainer() into
_run_appcontainer_process(). No new sandbox, daemon, Docker or alternate GUI.
The AppContainer grants, restricted token and profile cleanup are unchanged.

Regression tests in tests/test_output_capture.py and tests/test_secure_runner.py:
oversized secondary output, symlink substitution preserving outside files,
directory link paths, output appearing mid-wait, safe output, final-poll
overflow and fail-before-launch invalid output path.

Remaining: verify latest commit on Linux/Windows 3.10/3.12 and packaged
Windows EXE AppContainer boundary smoke. Merge only after successful CI.
Physical 4/8 GiB Windows GUI/AI and long-run memory acceptance remains open.
Arbitrary other generated files still lack an aggregate disk quota.


## PR #35 — native process memory measurements (in progress)

Branch feat/native-memory-measurement based on PR #33 merge f84f0c61.
Reuses existing model_routing.py as the canonical resource-probe owner.
Windows GetProcessMemoryInfo and Linux /proc/self/status supply on-demand
ForgeOS process working-set statistics. No psutil, new daemons or polling.
CLI `python -m forgeos memory` provides a one-shot diagnostic. The existing
synthetic capture benchmark gains `--cycles 1..10`, per-cycle post-GC RSS,
peak traced Python allocations and total time. Tests verify native readings,
repeated benchmarks, bounds and invalid arguments. Unverified until complete
Linux/Windows CI, frozen executable packaging and independent Windows 4/8 GiB
hardware acceptance. This probe does not measure Ollama/model/GPU/child RAM.


## PR #36 — streamed CLI history and one shared RAM report (pending CI)

Branch feat/streamed-history-and-memory-ui from merged PR #35
(1904ebbca87d71a9337463ad3f1c8e267bd10001).
Existing Store adds iter_tasks() to yield original full JSON task rows without
materializing the entire task table; existing CLI list uses JSON streaming and
preserves historical order/content. It is not a new queue or storage backend.
The existing canonical model_routing.py owns memory_report(), reused by
CLI 'memory' and the single Tkinter GUI's on-demand Activity snapshot button.
No background agent, polling, cloud request or extra application. Tests cover
nonmaterialized CLI exports, full Unicode/output preservation, streaming
cursor early close, and one shared offline memory-report source. Full CI and
frozen AppContainer packaging/smoke must pass; 4/8 GiB physical acceptance
remains unverified.

## PR #37: frozen Tkinter GUI lifecycle acceptance (pending CI)

Branch feat/frozen-gui-startup-acceptance, based on PR #36 merged commit
2c3af3d8270a7b915ddb492bc16b41915f2be1bb.
gui.launch(db_path, acceptance=True) now initializes normal widgets with a
temporary database, withdraws the window, validates tab names and queue
initialization on the Tk main thread, processes a short event-loop cycle,
samples its own process RAM, and invokes the standard close() cleanup.
Tk construction errors explicitly close the SQLite Store.
The existing frozen executable launcher supports --gui-acceptance-report
and writes local JSON, failing with a nonzero exit on unverifiable startup.
Windows packaging CI invokes the *packaged EXE* under a 90s watchdog
and validates report fields, then still executes the existing zero-capability
AppContainer smoke with no fallback. Headless regression tests mock success
and failure without an X display; hosted Windows performs actual GUI startup.
Remaining: real 4/8 GiB physical/VM user desktop acceptance, Ollama
connectivity, edit/test/repair execution, crash recovery, memory soak,
and any required security hardening from observed runtime defects.

## PR #38: frozen GUI history paging and memory-growth acceptance (in progress)

Branch feat/frozen-gui-history-soak, based on merged PR #37
d9c085afba59f094af6a207977f4a5719ecab3c5.
The existing frozen launcher now seeds a disposable test SQLite DB with
520 synthetic completed/failed tasks (each with an approximately 8 KiB
diagnostic blob). The existing Tk GUI acceptance callback exercises
Older/Newer paging through [150,150,150,70] rows and back to first page.
It samples native process working set before and after and fails on more
than 64 MiB additional RSS. The existing CLI acceptance flags and security
smoke remain intact; no new service, daemon, model or GUI. Regression tests
verify the fixture is complete, bogus GUI pagination is rejected, and a
runaway working-set result cannot be accepted. Next: verify all 4 OS/Python
jobs and frozen Windows real Tk EXE test, repair any failures, and merge.
Hardware acceptance and Ollama end-to-end validation remain unverified.

## GitHub documentation overhaul — 2026-11-07

**Branch:** `docs/forgeos-project-guide-20261107` (base `main`). **Scope:** project presentation, comprehensive beginner guide, accurate engineering architecture, maintenance policy and automated documentation guard; no product runtime semantics intentionally changed.

### Files updated / added

- `README.md`: replaces sprawling chronological patch notes with public landing page, GitHub Actions badges, Mermaid workflow, current capability/limitation matrix, beginner start, security, local model/memory notes and link hub.
- `docs/USER_GUIDE.md`: detailed click-by-click Windows usage, practice repository, tasks, model, host tests, diagnostics, recovery, CLI commands and troubleshooting.
- `docs/ARCHITECTURE.md`: canonical modules, data flow, checkpoint process, execution trust boundaries and data-handling implications.
- `docs/ROADMAP.md`: feature maturity and verification gates instead of unqualified milestones.
- `docs/WINDOWS_BUILD.md`: actual hosted CI GUI lifecycle, 520-task paging check, secure AppContainer scope and unfinished physical acceptance.
- `docs/DOCUMENTATION_POLICY.md`: source-of-truth mapping, per-change update triggers and precise status labels.
- `CONTRIBUTING.md` and `.github/pull_request_template.md`: contributor instructions and a documentation/verification checklist.
- `tools/check_documentation.py`: offline local Markdown link/anchor check and PR source-change/documentation coverage rules.
- `tests/test_documentation.py`: unit tests for checker parsing, policy conditions and repository-local links.
- `.github/workflows/docs-sync.yml`: new PR-triggered document integrity / change coverage workflow.

### Evidence and limitations

The live GitHub repository and source tree were inspected before authoring, including README, CLI/GUI/engine and production runner, memory model and Windows workflow. The source-backed assessment identified existing AppContainer `unittest` support, real hosted Tkinter packaging acceptance and `pytest` support still open as PR #29. No new feature is claimed to be production accepted.

An independent review of Markdown links across the documentation branch found **two mismatched numbered-heading anchors**, which were corrected in the README and user guide. **GitHub CI, the complete source regression suite and the final packaged Windows validation for this documentation PR must still be checked before merge.** Do not describe those tests as passing until confirmed by their workflow runs. The proposed documentation workflow checks linked paths and anchors as well as whether core product edits are accompanied by documentation. It cannot prove textual accuracy or update the README automatically.

### Follow-up expectations

1. Open and review the documentation PR; verify GitHub Actions documentation check, Python tests and Windows build checks.
2. Correct any confirmed CI failure and rerun affected and full checks before merging.
3. Continue making doc updates **in the same PR as new features**; update GUIDE/ARCHITECTURE/WINDOWS_BUILD/MEMORY_BUDGET/ROADMAP/HANDOFF as affected.
4. Review the public repository's separate **About** description/topics; the linked GitHub connector currently exposes no repository-metadata write action.
5. Do not add an actual GUI screenshot until a current branch build is launched and captured; label mockups as mockups.
6. Preserve this handoff and the documentation gate during later feature work. Real physical Windows, Ollama and full task-run acceptance remain product blockers independent of documentation.



## Canonical user guide embedded in Windows GUI (PR #41; pending CI)

The documentation overhaul in PR #40 introduced one canonical
`docs/USER_GUIDE.md`, a pull-request checklist and offline doc integrity
checks. A concurrent older draft (#39) introduced a competing manual path,
so it must **not** be merged; its useful offline-help concept was adapted
here instead. Branch `feat/offline-user-guide-in-gui` is based on PR #40
main commit `43d5d7001214ce355436c6a23c78a9cc6be7aa72`.

The existing single Tk GUI adds an on-demand **How to use ForgeOS** control
in Activity & diagnostics. It opens the existing canonical user guide as
read-only scrollable Markdown, with no network, dedicated renderer, agent or
background memory cost. The EXE build uses one --add-data entry to bundle
`docs/USER_GUIDE.md` at the frozen resource path. The hosted Windows GUI
acceptance requires the bundled guide to be available. Source checkouts
support the project-root path even when Python imports from site-packages.
Regression tests verify the source guide, command button, packaging path,
launcher fail-closed behavior and user guide instructions. The existing
`docs-sync.yml` checks documentation-link and code/doc pairing. Next:
verify Python matrix, Windows frozen actual GUI and AppContainer smoke,
merge the clean single-guide PR, then close the superseded conflicting draft.
Physical real-user Windows and end-to-end AI acceptance remain incomplete.


## Rebuilt bundled Windows pytest AppContainer (PR #43; pending CI)

Branch `feat/secure-pytest-watchdog-rebuild` rebases the intentions of
open, stale/conflicted PR #29 onto merged `main` at
`8697b443fb65dbc013b7fa8813e8e3b741278d62`. Root cause in the old
Windows packaging smoke: pytest's logging plugin default FileHandler opens
`\\\\.\\nul`, denied by the zero-capability AppContainer. Do not weaken
sandbox permissions, disable test containment or bypass a failed smoke.

Implementation in the existing owners:
- `pyproject.toml` adds optional extra `secure-pytest` so core installs do
  not incur a mandatory pytest dependency. Windows packaging selects the extra.
- `sandbox_runner.py` adds a hidden bundled `run_pytest` mode using
  `pytest.main`. It directs normal pytest logging to an explicit fixed
  `.forgeos-runtime/pytest-log.txt` path, retains the logging plugin and
  keeps no-cache-provider and external-plugin autoload disabled.
- `secure_runner.py` selects one of two frozen test-child flags, validates
  the secondary owned log file, and passes it to the existing AppContainer
  Job Object native output watcher. No new sandbox or daemon.
- `output_capture.py` allowlists only the exact owned test-output and
  pytest-log filenames under the disposable snapshot; `appcontainer.py`
  validates and monitors a maximum of two owned files, each <=32 MiB.
- `secure_smoke.py` executes both unittest and native pytest in the
  zero-capability Windows boundary, testing snapshot writes, no inherited
  secret/bootstrap, blocked outside-file writing and localhost denial.
- Tests cover output-file link/overflow guards, helper arguments, frozen
  mode selection, snapshot environment and failure handling.

Requirements: all four foundation OS/Python jobs, documentation integrity,
packaged Windows EXE, GUI/paging/offline-help acceptance, **actual frozen
AppContainer smoke for both unittest/pytest**. Until independently passing,
this is development branch functionality, not shipped. If it passes, merge
and close old stale PR #29 without duplicate/unsafe merge. Remaining:
other framework isolation, genuine Windows/Ollama end-to-end workflow,
crash-injection, process-tree memory benchmarks and 4/8 GiB acceptance.


### PR #43 completion evidence and remaining acceptance

The corrected branch head `f496189117188b19a458fa5aa44c7a4dc4a39c1d`
passed foundation tests for Windows/Linux Python 3.10 and 3.12,
documentation integrity, frozen Windows EXE packaging, frozen Tk GUI
startup/paging/offline manual, and the actual hosted Windows secure
AppContainer smoke for BOTH unittest and pytest (run 42437425300).
Two Windows defects were fixed before passing: environment path aliases
(Windows canonical temp paths) and an unnecessary attempt to re-open
`.forgeos-runtime` with `Path.resolve(strict=True)` inside a zero-capability
child. The parent validates paths before launch and its job watchdog detects
unsafe substitution and size violations; the child now uses the pair of
parent-provided lexical paths without forbidden directory operations.
The package itself still excludes Git, Ollama models and external build
chains. Real device Ollama build/test/repair and low-RAM hardware acceptance
remain unverified. PR #29 must be closed rather than merged to avoid
reintroducing its older unguarded implementation.


## Product scope explanation added (documentation-only PR; 2026-11-07)

Branch `docs/forgeos-capabilities-and-non-goals` from `main`
`f5809c7e6c1484fb1a1f31efa02690fc2738868f`.

Added `docs/PRODUCT_VISION.md`: an extensive, public GitHub-facing explanation of
what ForgeOS is intended to become, what is actually implemented in the active
prototype, capabilities not yet delivered, explicit non-goals, user examples,
memory targets and objective release gates. Its status matrix is deliberately
separate from the canonical step-by-step `docs/USER_GUIDE.md`. Linked from the
README and roadmap; documentation policy now explicitly includes product
scope under its change triggers. The README also corrects a stale statement
about the experimental pytest feature (PR #43 is already merged; original
PR #29 remains open/unmerged). This PR changes no runtime code or security
behavior. The existing documentation link checker and Linux/Windows CI must
pass before merge; no real Windows/Ollama hardware acceptance was performed.

## Bounded checkpoint recovery scan — 2026-11-07 (review branch)

- Starting main commit: `f5809c7e6c1484fb1a1f31efa02690fc2738868f`.
- Branch: `perf/bounded-resume-checkpoints`.
- Changed `Store.iter_blocked_checkpoints()` to keyset-paginate only blocked
  checkpoint metadata (16 rows/page, 64 KiB body maximum each). SQLite excludes
  oversized payloads *before* they reach Python; completed-task diagnostics
  are not selected. Existing leases, saved execution contracts, workspace
  identity/hash validation and manual host-test opt-in remain intact.
- `resume_safe()` now skips oversized checkpoints with an explicit reason
  instead of loading or truncating them. No new resume phase or persistence
  format is introduced; existing small checkpoint JSON remains compatible.
- Tests added for >2 pages with large completed-task blobs, Unicode byte-size
  enforcement, skipped oversize checkpoint without model/runner invocation,
  and execution of 41 eligible stored checkpoints.
- Documentation updated: architecture, memory budget, user recovery guide,
  handoff. **At commit creation, tests/Windows acceptance remain unverified.**
- Next: observe PR Python 3.10/3.12 Linux/Windows CI, repair failures,
  confirm documentation checks and Windows frozen packaging remain green.
  Run physical Windows crash/restart recovery and 4/8 GiB usage tests before
  claiming hardware acceptance. Future memory hardening: stream or cap massive
  aggregated `resume-safe` reports without obscuring skipped-task reasons.

## Streamed, bounded edit transaction hardening — 2026-11-07 (review branch)

- Starting commit: `07b0ea2e0c1cd2b9e958436456a31685e4ee1511` (after merged PR #45).
- Branch: `hardening/streamed-transaction-backups`.
- `transactions.py` now preflights all target sizes/content/counts; original
  source files and recovery backup hashes use 64 KiB chunks, not
  `Path.read_bytes()` materialization. Existing inputs over 8 MiB are
  explicitly rejected while preserving original files. The 8-file/128 KB
  new-content constraints reuse the canonical engine constants.
- Journal manifests are bounded to 32 KiB and malformed/duplicate entry
  metadata is rejected before any restoration; oversized/incompatible
  journals remain on disk for manual inspection.
- Regression tests cover sparse >8 MiB files rejected pre-journal, large
  streamed backup/restore with `Path.read_bytes` disabled, oversized
  manifest and duplicate entry preservation. Previously existing rollback
  and external-conflict tests remain the regression baseline.
- At implementation/commit time: **full Python/Windows frozen tests and real
  hardware acceptance not yet verified**. Next: observe CI for 3.10/3.12
  Linux/Windows, docs, packaged Windows acceptance; resolve failures before
  merging. Follow with randomized size-boundary and interrupted-copy
  fault-injection and physical crash/restart tests.

## Streamed recovery reporting — 2026-11-07 (review branch)

- Starting main: `e977d507c2495715a69f76fdfa0dda72bb1c696d`.
- Branch: `perf/streamed-resume-report`.
- Extended the existing `resume_safe()` with validated opt-in event callbacks
  and `collect_results=False`; defaults preserve the complete report API.
  Stream errors propagate instead of being reclassified as failed model tests.
- Added `resume-safe --stream-report` to CLI, emitting one JSON event per
  recovered/skipped/failed task and one final summary. Failed tasks retain
  the nonzero exit code. The CLI no longer needs an unbounded outcome list
  in this optional mode; the checkpoint SQL scan remains bounded as before.
- Added 60-task mixed-event verification, failure/sink error tests,
  invalid-option preflight and CLI JSON-lines contract tests.
- Updated architecture, user guide and memory-budget notes.
- CI and physical Windows hardware acceptance remain unverified at this
  commit. Next: Linux/Windows Python 3.10/3.12, docs, frozen EXE acceptance,
  any root-cause fixes, then merge only on verified green checks.

## Concurrent planning safeguards — 2026-11-07 (review branch)

- Starting main commit: `fab3fbe088897b6afa616cdc9706c852e158ee5e`.
- Branch: `hardening/planning-lease-safety`.
- CLI `plan` now reuses the existing SQLite execution lease and bounded
  model runner. It accepts only queued/planned tasks, acquires a per-task
  lease, performs a lease-owner guarded transition to planning, then
  atomically stores a completed plan or blocked error while retaining
  ownership. Lease release happens in `finally`.
- `Store.transition` now optionally validates lease owner and atomically
  persists result/error, preserving original behavior for older callers.
- `Store.recover` leaves live-lease planning tasks alone; genuinely
  stale/unleased planning tasks still return to queued.
- Tests cover live planner/recover interaction, concurrent owner refusal,
  completed-task preservation, model errors and stale lease protection.
- Hosted CI and real Windows hardware acceptance are unverified at commit
  creation. Next: verify full Linux/Windows matrix, documentation and frozen
  executable, remediate any failures; physical concurrent planning and
  crash/restart acceptance remain outstanding.

## Lease-fenced autonomous task publication — 2026-11-07 (review branch)

- Starting main: `5607d869e889f0c63168dd412debde61c6212214`.
- Branch: `hardening/lease-fenced-task-publication`.
- Defect identified: `engine._execute_task()` acquired a lease but used
  unconditional `Store.update()` to publish test checkpoints, repairs, pass,
  failure and blocked state. A process waking after its lease was reclaimed
  could overwrite a new worker's data.
- Correction: reuse the existing `Store.transition()` for atomic
  status/result/error + owner-fenced publication; ensure lease identity **and
  freshness**. `Store.heartbeat_lease()` now refuses to revive a lease
  older than the default expiration or more than 60 seconds ahead.
- Engine error handling preserves the original exception but skips publication
  if the lease was lost. Added heartbeat immediately after file edits.
- New tests cover stale lease before reclaim, stale lease after replacement,
  recovery/replacement during tests, and status change during generation.
- Security limitation: no OS-level lock prevents old in-flight file writes
  during the instant a lease expires; database state fencing does not prove
  safe concurrent worktree ownership. Real Windows kill/restart and
  long-running Ollama tests remain necessary.
- At authoring: CI/Windows packaging are not yet verified. Run Linux and
  Windows 3.10/3.12 tests, documentation integrity and frozen EXE/GUI/
  AppContainer smoke before merging; inspect failures and re-run.

## Unified GUI and CLI planning/testing safeguards — 2026-11-07 (review branch)

- Starting main: `d28f224a5438972167a84e5ec0f7bf1fe0260154`.
- Branch: `hardening/unified-gui-cli-task-actions`.
- Defects: desktop planning/testing still used unconditional task updates
  without lease protection, and a packaged GUI's standalone test button
  called the direct test runner instead of the engine's AppContainer path.
  CLI `test` likewise lacked explicit host-test permission.
- Added one canonical `src/forgeos/task_actions.py` for both frontends,
  reusing the existing SQLite lease/transition and engine runtime. Planning
  is eligible only from queued/planned; direct manual testing is eligible
  from queued/planned/failed. Completed and checkpoint-blocked tasks cannot
  be overwritten by a manual test action.
- Standalone tests now require an explicit trust opt-in from GUI checkbox or
  CLI `--allow-host-tests`; they route through `engine._run_workspace_tests`
  so frozen Windows only uses permitted AppContainer presets and fail-closed
  behavior. Incomplete output cannot approve a task.
- Removed unowned GUI exception-state write that could clobber another
  worker's task. GUI worker thread continues to use a dedicated Store.
- Added tests for GUI source contract, shared planning lease behavior,
  opt-in preflight, result persistence, failed/incomplete evidence,
  protected completed tasks, live lease replacement and CLI flag routing.
- User guide, roadmap and architecture updated alongside implementation.
- CI acceptance, Windows real-machine behavior and local Ollama testing
  were **not verified** at authoring; require Linux+Windows 3.10/3.12,
  docs integrity and frozen Windows GUI/AppContainer smoke before merge.

### PR #50 CI correction (in review)

The first Linux Python 3.10/3.12 CI run exposed one regression in
`tests/test_cli_plan_lease.py`: the CLI eagerly constructed an Ollama
object for a completed/ineligible task before `plan_task()` refused it.
The intended behavior is to avoid unnecessary model construction, reduce
resource overhead and leave completed work untouched. The shared helper
now accepts a lazy model factory; CLI and GUI instantiate only *after*
eligibility checks and successful exclusive lease acquisition. Additional
tests confirm no invocation for completed tasks and exactly one model
construction under a live planning lease. The initial failure is recorded
here; full rerun/Windows packaged acceptance still required.

### PR #50 Windows CI correction (in review)

Linux tests passed after the lazy-model change, but Windows 3.12 exposed
an independent test-fixture path comparison: the temporary directory
appeared as both a Windows 8.3 short alias and its canonical long path.
`Store.add()` intentionally stores `Path.resolve()` output. The test now
asserts the exact canonical project path persisted in the task instead
of assuming the unnormalized temporary-directory spelling. No production
path transformation or security restriction was weakened. Rerun the
entire Windows/Linux suite and frozen EXE acceptance before merge.
