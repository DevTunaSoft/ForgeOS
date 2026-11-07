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
