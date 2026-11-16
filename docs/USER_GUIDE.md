# ForgeOS — User manual

**Audience:** first-time users, testers, and developers evaluating the current ForgeOS prototype. **Current scope:** existing local Git repositories, local Ollama AI and experimental loopback model adapters on the feature branch, selected code editing/testing, diagnostics and recovery. ForgeOS does not yet provide a one-click production app builder.

> [!WARNING]
> **Start with a disposable test repository.** Source/development-mode test execution can run generated repository code on your Windows computer. Never check the host-test permission box for code you do not trust. Windows frozen executable isolation supports only designated presets; unsupported presets fail closed.

The packaged Windows prototype uses a restricted **AppContainer** boundary for
its designated `python-unittest` and experimental `python-pytest` test
workloads. Other frozen presets do not inherit that permission automatically
and must fail closed; running source code on the host is a different,
explicitly trusted mode. The pytest runner keeps its logging plugin but
redirects its file output to a snapshot-owned, size-monitored local file.
Third-party pytest plugin autoload is disabled for the frozen sandbox.

## How to open this guide inside ForgeOS

Once ForgeOS is open, select **Activity & diagnostics**, then choose
**How to use ForgeOS**. The exact same guide opens in a scrollable,
**offline** window. It is displayed as plain Markdown text, avoiding
a heavyweight renderer, web browser or network connection. The Windows
EXE bundles this canonical file at build time. In editable source checkouts
the guide is read from the `docs` directory.

This guide is updated **in the same pull request** as user-facing features.
See the [documentation maintenance policy](DOCUMENTATION_POLICY.md).

## 1. What to prepare

### Option 1: Windows EXE prototype

1. Open [Windows executable workflow](https://github.com/DevTunaSoft/ForgeOS/actions/workflows/windows-exe.yml).
2. Choose a **successful** run and open its **Artifacts** section.
3. Download `ForgeOS-Windows-EXE`, unzip it, and double-click `ForgeOS.exe`.
4. The EXE contains the Python desktop program but **not** Git, Ollama, a local AI model, or a complete app-building toolchain.
5. If the program fails to start, consult [Troubleshooting](#8-troubleshooting) and [Windows build details](WINDOWS_BUILD.md).

CI artifacts expire after 14 days; a downloadable artifact is not the same as a signed product release.

### Option 2: install from source

Install Python **3.10 or newer** and Git, then open Windows **PowerShell**:

```powershell
git clone https://github.com/DevTunaSoft/ForgeOS.git
cd ForgeOS
py -m pip install -e .
py -m forgeos gui
```

The terminal must stay open while the source-run GUI is active. If Python's `py` command is unavailable, use an installed Python 3 interpreter via `python -m ...`.

### Optional: install local AI

ForgeOS's current AI engine uses an **Ollama server on your own computer**. Install Ollama separately from its official distribution and install a model that fits the available RAM. A small coding model is a reasonable starting point for experimental tasks. You can verify available models in PowerShell with:

```powershell
ollama list
```

If Ollama is absent, ForgeOS can still open and manage its queue, but **Generate plan** and AI-powered editing will not function. ForgeOS does not download models automatically and does not silently switch to a paid cloud API.

### Create a safe practice repository

In PowerShell, create a throwaway Git repository to explore the interface:

```powershell
mkdir C:\forgeos-practice
cd C:\forgeos-practice
git init
'print("Hello, ForgeOS")' | Set-Content -Encoding UTF8 hello.py
git add .
git commit -m "Initial practice project"
```

If `git commit` asks for identity details, configure your own Git author name and email. A preexisting Git repository is required; ForgeOS does not currently create and configure arbitrary application projects for you.

## New: using more than one local AI runtime (experimental feature branch)

In **Development → Execution settings**, you can now select **Inference
backend**: Ollama, llama.cpp, LM Studio, or another local OpenAI-compatible
server. Leave **Local endpoint** empty for the standard port, or specify
a literal loopback URL such as `http://127.0.0.1:8080/v1`.

For manually selected external local endpoints, the model server must
already be running on your computer. Type its **exact advertised model ID**
into **Local model**. For non-Ollama servers, `auto` only works if the
server reports model sizes; otherwise select a name manually.
Use **Generate plan** first to verify that the local server responds before
trying to generate source edits. A model/server failure should block the task,
not overwrite existing project work.

Click **Browse Hugging Face** to search public GGUF repositories. Select a
result and click **Inspect GGUF sizes** to see published weight sizes and
whether Git LFS SHA-256 metadata is present. To download, choose a GGUF file with a published SHA-256 and click
**Download and verify**. ForgeOS confirms the request, checks disk space,
pins a Hugging Face revision, resumes interrupted partial files and verifies
the SHA-256 before saving under `~/.forgeos/models`. Downloaded weights are
not automatically loaded. **Experimental managed local GGUF launch** is
available in the same GUI with two options: an explicit opt-in
pinned official Windows x64 CPU runtime installer, or a user-provided
`llama-server.exe` with independently verified SHA-256. Automatic
background downloads are forbidden. Model updates/removal and real
Windows model acceptance tests are still planned. Browsing requires
internet, but ForgeOS does not use Hugging Face hosted inference.

### Optional: run your downloaded GGUF from inside ForgeOS (experimental PR #51)

This is an optional feature on the multi-LLM feature branch, **not yet
released on main**. A real Windows 11 llama.cpp session with a GGUF has
not been independently accepted; use a disposable repository first.

1. Under **Execution settings** select **Browse Hugging Face**, review a
   model's license, choose a compatible GGUF, then **Download and verify**.
   This optional internet request is separate from all local inference.
2. Open **Run verified local GGUF**. On **Windows x64**, click **Install
   verified official Windows CPU runtime (opt-in)** and **confirm** the
   one-time pinned official llama.cpp release download. ForgeOS validates
   the archive SHA-256 and checks every extracted executable and DLL. This
   step does not install a system service, use Docker, or download weights.
   Existing installations are reverified without another download.
3. The installer shows **downloading/verification progress, success or errors**
   directly in this same GGUF dialog. Once complete, choose the downloaded
   GGUF with **Choose from ForgeOS downloads**, then
   click **Start installed CPU runtime**. The local runtime archive, DLLs,
   executable and GGUF are verified again in a worker thread before launch.
   ForgeOS binds localhost, uses an ephemeral API key and CPU-only settings.
   If the model is too large for conservative available RAM, choose a
   smaller quantized GGUF.
4. **Manual alternative:** obtain a trusted `llama-server.exe` and its
   dependencies yourself, independently confirm the executable's
   64-character lowercase SHA-256, select your GGUF and click
   **Start manual runtime**. This route verifies the EXE and GGUF, but
   cannot independently attest all manually supplied DLLs.
5. Once started, ForgeOS selects backend `llama.cpp`, model
   `forgeos-managed` and the private local endpoint. Try **Generate plan**
   with a small task before enabling file-edit/test workflows.
6. Click **Stop local model**, close ForgeOS after tasks complete, or
   allow its five-minute idle unload to release model RAM. The idle
   timeout does not interrupt a currently active task.

**Security and remaining limits:** the official archive checksum is an
integrity control, not a publisher signature. Native DLL loading can still
be subject to same-user file races; Windows process creation currently has
a short job-attachment window. One local managed model runs at a time.
Session credentials are not persisted for automatic post-crash recovery.
If the local model process exits unexpectedly, ForgeOS detects this in its
existing GUI event loop **after active tasks complete**, attempts to clean
up the terminated process and ephemeral secret, then restores Ollama as
the safe default. The user must intentionally start a verified local GGUF
again; ForgeOS never silently reloads changed weights. A failed cleanup
attempt is logged and latched to avoid an endless retry loop; use
**Stop local model** to retry manually. This is fail-closed state handling,
not automatic restart after system reboot. Real model quality, startup/stop
behavior, memory and Windows 11 acceptance remain unverified. No paid API is needed for this route.
See [model compatibility and risks](MODEL_COMPATIBILITY.md).

Examples for advanced users:

```powershell
forgeos models --backend llama.cpp
forgeos model-catalog search "Qwen2.5-Coder"
forgeos model-catalog files Qwen/Qwen2.5-Coder-3B-Instruct-GGUF
forgeos model-download Qwen/Qwen2.5-Coder-3B-Instruct-GGUF EXACT-Q4-K-M.gguf
forgeos plan 1 --backend lm-studio --model your-exact-local-model-id
```

The Ollama default remains unchanged. Keep only one model loaded if RAM is
tight. A model switch is not an authorization to restart an interrupted
workflow with different weights. The experimental `resume-safe` command
reconstructs the original recorded local provider, model and endpoint
rather than replacing them with the current GUI selection. It still
requires the existing host-test permission and validates the task workspace
and hashes; real crash/restart behavior remains to be verified. Read the
[full model compatibility guide](MODEL_COMPATIBILITY.md) for limitations,
troubleshooting and a RAM-conscious model shortlist.

## 2. Understand the screen

**Low-memory task history:** The Live Activity Monitor reads only a task's status during its periodic update; it does not reload previous test logs or results. New planning, manual testing, and development runs load only the small task fields they need. When explicitly resuming a blocked development task, ForgeOS accepts only a checkpoint of at most **64 KiB**; a larger checkpoint is refused before creating a workspace or calling a model. Existing result data is retained for manual inspection rather than silently truncated. Opening **Original vs AI summary** is a deliberate evidence-inspection action and may access archived results.

**Memory-conscious input limits:** Task descriptions are limited to **16 KiB of UTF-8 text**, including text entered in the GUI or CLI. The local AI request path also rejects prompts larger than **64 KiB**, before model selection or JSON serialization. Oversized inputs are **rejected**, not silently truncated. Break large requirements into smaller, independently testable tasks and keep lengthy diagnostic files outside the task description. Legacy oversized task records are refused before ForgeOS creates a worktree or contacts the local AI model. These bounds are implemented but still require the latest Linux/Windows CI acceptance runs.

**Duplicate-task protection:** If you click **Add task** again with the same description in the same project while the original task is queued, planning, planned, editing, or testing, ForgeOS refuses to create another task and identifies the existing task number. Select that task from the queue rather than submitting the same request again. Two ForgeOS windows use one SQLite transaction to enforce this check. Completed, failed, or blocked tasks can be submitted again deliberately; an incomplete-evidence blocked task still requires manual review before deciding to retry. This guard is implemented and covered by regression tests but still needs full Linux/Windows CI acceptance.

The one-window desktop prototype has **two tabs**:

| Location | What you will see | What it does |
| --- | --- | --- |
| **Development** | Project repository and **Browse** | Points ForgeOS to a local Git repository |
| **Development** | **Tell ForgeOS what to build** | Plain-English development task description |
| **Development** | Local model | Ollama or explicit local compatible model; `auto` requires available model-size metadata |
| **Development** | Run verified local GGUF | Experimental one-GUI managed CPU llama.cpp start/stop with SHA-pinned user-provided executable and downloaded verified GGUF |
| **Development** | Test suite | Framework preset (availability depends on runtime) |
| **Development** | Repair retries | Limits how many correction attempts are allowed |
| **Development** | Workspace folder | Separate destination for temporary Git worktrees |
| **Development** | Host test execution checkbox | Required for **Run tests only** and **Build / test / repair**; enables trusted source/development-mode execution while packaged Windows uses its designated isolated test runtime |
| **Development** | Development queue | Tasks, status, and Older/Newer pagination (150 rows per page) |
| **Development** | Add task / Generate plan / Run tests only / Build / test / repair | Main workflow actions |
| **Activity & diagnostics** | Task results, original-versus-summary comparisons, repair-learning statistics, one-shot RAM snapshot | Evidence inspection and operational visibility |
| **Activity & diagnostics** | **How to use ForgeOS** | Opens this guide offline, without loading it into memory at startup |

Not every error can be fixed automatically. A failed model operation, a failed or unsupported test, a missing dependency or an unsafe edit can stop a task with a blocked/failed status.

## 3. Your first task — step by step

1. Launch ForgeOS.
2. In **Project repository**, choose **Browse** and select your practice repository.
3. In **Tell ForgeOS what to build**, enter a specific request. Example: “Make hello.py print a greeting and add a Python unittest to check the greeting.”
4. Select **Add task**. Your request appears in the development queue.
5. Click the task in the queue to select it.
6. Set **Local model** to the exact name shown by `ollama list`, or use `auto` to attempt memory-aware selection among models already installed.
7. Set **Test suite** to `python-unittest` for this example.
8. Under **Workspace folder**, choose a directory that can hold new task worktrees. The directory is separate from the source repo.
9. Select **Generate plan** if you want to see a local model's proposed approach. This may update the task's status to Planned but does not build a finished application.
10. To try **Build / test / repair** or **Run tests only**, first read the warning and enable host test execution **only if the repository is trusted**. Both actions execute project test code; the frozen Windows build uses its designated sandbox path and fails closed for unsupported presets.
11. Open **Activity & diagnostics** to see success, failure, model and test details.
12. Inspect the task's generated worktree and test evidence before copying or merging any changes into your main project.

**Important:** the label “Build / test / repair” describes an experimental edit/test loop. It is **not** a promise to compile any requested EXE/APK/web app.

## 4. What to do with the result

A successful task indicates that the chosen test operation reported success for the run; it is not proof that every feature works, that generated code is safe, or that a release has been built. Inspect the files and test coverage yourself. ForgeOS intentionally leaves the isolated Git worktree in place for review. It does not automatically commit, merge, push, deploy or publish changes.

If a task fails, examine the exit status, error summary and raw evidence when present. The local learning view tracks verified failure signatures and statistics; it does not silently run historical fixes.

## 5. Working with diagnostics

From the GUI, select a task that has run tests, then open **Activity & diagnostics**. The comparison view offers a bounded AI-facing summary alongside original evidence (when the archive is available and verified). Original output is opened explicitly and may contain private values.

From PowerShell inside the installed source project:

```powershell
py -m forgeos diagnostics stats
py -m forgeos learning stats
py -m forgeos memory
py -m forgeos filters status
```

When a result provides a 64-character SHA-256 evidence reference:

```powershell
py -m forgeos diagnostics show YOUR_64_CHARACTER_REFERENCE
```

The output may contain credentials, file paths and other sensitive information. Do **not** paste the entire raw evidence into a public issue or publish it as an Actions artifact. The evidence store is not currently encrypted at rest.

### Trusted output filters (optional)

ForgeOS allows **literal, locally pinned JSON output filters** for repeated low-value diagnostic lines. They do not execute commands or plugins. Check whether a local filter configuration is trusted:

```powershell
py -m forgeos filters status
```

Trust an intentionally reviewed local configuration only after verifying its contents:

```powershell
py -m forgeos filters trust
```

Changes to the file invalidate the previous trust hash. The default file is `~/.forgeos/output_filters.json`; see [the enhancement specification](RTK_ENHANCEMENTS.md) for its schema. Filters do not replace authoritative test outcomes.

## 6. Recovery after an interruption

ForgeOS stores task state in `~/.forgeos/tasks.sqlite3`. If it is terminated in the middle of work, it avoids silently overwriting the generated workspace. An interrupted edit/test task may be marked **Blocked** for inspection. A planning task may return to the queue.

Recommended order:

1. Close ForgeOS normally if possible. Never delete a worktree or the SQLite database just because a task is blocked.
2. Reopen the GUI, or use the CLI to inspect interrupted tasks:

```powershell
py -m forgeos recover
py -m forgeos list
```

3. Inspect the workspace and any pending edit journal before attempting a resume. To repair a pending journal in a **known, task-specific workspace**:

```powershell
py -m forgeos recover-edits C:\forgeos-work\task-1
```

4. For a **verified eligible checkpoint**, the CLI offers guarded restart with matching saved model, preset, repair budget, workspace and file hashes:

```powershell
py -m forgeos run 1 --workspace-parent C:\forgeos-work --model auto --preset python-unittest --max-repairs 2 --resume --allow-host-tests
```

5. Alternatively, to examine eligible blocked tasks collectively:

```powershell
py -m forgeos resume-safe --workspace-parent C:\forgeos-work --allow-host-tests
```

For a large recovery backlog, add `--stream-report` to this command. Each
task outcome is printed as one JSON line, followed by a final count summary;
the usual complete JSON report remains the default. Streaming does not relax
checkpoint checks, sandbox permissions, model selection or the need to inspect
a workspace before recovery.

**GUI recovery memory limits:** The **Resume safe blocked tasks** action now
uses the same streaming recovery path automatically. The Activity tab shows
the interrupted-task count, totals of resumed/skipped/failed tasks, and only
the **12 most recent events**; older individual event messages are not kept
in the GUI report. Failure explanations are limited to 400 characters per
event, preserving the beginning and end. The task database and individual
workspaces retain their existing evidence; the GUI summary is not the full
audit log. No additional service or background polling is started.

**File transaction limits:** ForgeOS now streams backup copies instead of loading an existing source file fully into RAM. Existing files larger than 8 MiB are rejected for automated replacement, and edit journals larger than 32 KiB are preserved for manual inspection. The internal `.forgeos` and `pending-edit` paths must be real directories; symlinked, Windows-reparse-point or hard-linked journal metadata fails closed. On POSIX, ForgeOS enforces mode `0700` on those internal directories and mode `0600` on manifests, backups and restore temporaries; staged replacement files remain umask-derived because they become published workspace files. Journal files and edit targets are opened through bounded, identity-checked descriptors so a substitution during a read or backup fails closed. Journal cleanup only removes the bounded allowlist of regular files created by the transaction; unexpected, linked or directory children remain in place for inspection instead of being recursively traversed. Before publishing a staged replacement, ForgeOS rechecks the real parent directory chain and target identity, then verifies both staged and published digests. Windows ACL behavior is unchanged and remains an operator review boundary; atomic publication and cleanup are not a complete concurrent-writer lock. If a committed journal remains after an interruption, ForgeOS rechecks the published file hashes before clearing it. Do not delete the journal or original workspace to bypass a safety check. These are transaction limits, separate from the 64 KiB task-checkpoint limit.

**Edit path limits:** Before any file is written, ForgeOS checks every path a local model proposes. Names that address protected locations (`.git`, `.github`, `.env*` files, dependency and virtual-environment folders, and `.forgeos`) are refused even when written in a different letter case or with a trailing dot or space that Windows would silently remove. Windows device names such as `CON`, `NUL`, `AUX`, `PRN`, `COM1` and `LPT1` are refused with or without an extension. A symlinked folder inside the project cannot be used to reach a protected location. ForgeOS refuses the whole model response, so none of its files are written, and the task is blocked with the reason. Inspect the task's error and the preserved workspace; do not weaken this check to force a file through. The same case-insensitive rule for `.env*` secret files applies to recovery manifests, source-only worktree archives and test snapshots, so `.ENV.local` and `.Env` are excluded just like `.env`.

During rollback recovery, the temporary copy used to restore an edited file is
also removed only when its file and real parent identities still match. POSIX
keeps the original temporary descriptor open through a failed replacement
decision so inode reuse cannot authorize deletion of a substitute; Windows
uses file identity without holding a handle that could block `os.replace`.
An unexpected rollback temporary remains available beside the journal for
inspection.

Recovery removes a newly created edit target, or a flat journal child, only
after rechecking the original file and real parent identities. POSIX pins the
file object through the unlink decision and Windows uses the same identity
comparison without holding a rename-blocking handle. A substituted target or
journal child is preserved and the journal remains available for inspection.

Recovery snapshots each edit target's real parent before reading it and
rechecks that parent before planning or restoring. Before the final
`pending-edit` directory is removed, ForgeOS rechecks its identity and
verifies that the path is gone; a replacement journal directory is preserved
for inspection instead of being deleted.

**Execution snapshot cleanup:** disposable test snapshots keep their ownership
marker beside, rather than inside, the runtime directory. Cleanup validates the
real parent, runtime directory and marker identities again immediately before
removal; POSIX pins the original directory/file objects, while Windows uses
identity checks without keeping handles open that could block cleanup. Marker
input is capped at 4 KiB. If a runtime, marker or parent is substituted, ForgeOS
fails closed and leaves the replacement available for inspection. These checks
are a cleanup guard, not a full concurrent-writer sandbox.

Snapshot creation also publishes its compact ownership marker exclusively with
the same 4 KiB limit. If a marker appears after the initial cleanup check,
creation fails rather than truncating that file; the marker and partial runtime
remain available for inspection.

**Expired or replaced worker:** ForgeOS now rejects a stale worker's attempt to publish a test result, checkpoint or failure after its task lease expires or is reclaimed. The workspace may still contain edits from the interrupted run. Inspect it and any journal before using guarded recovery; do not delete or automatically overwrite files just because the status says Blocked.

**Large or corrupted recovery records:** `resume-safe` skips blocked checkpoints larger than 64 KiB, with a reason in the report. It does not truncate or delete them. Review the affected workspace and journal manually; do not force a resume of unknown data. This limit applies to checkpoint recovery, not ordinary archived test output.

**Use resume commands only for trusted workspaces.** Recovery checks are strict; the CLI should reject ineligible or changed checkpoints rather than overwrite unverified edits. Actual supported resume phases are `test_pending` and `repair_pending`; not every interruption can be resumed. Supplying `auto` for resumption is subject to the engine's saved model/contract checks. If an exact resume fails, inspect the recorded checkpoint and avoid deleting any data.

## 7. Commands at a glance

| Command | Purpose |
| --- | --- |
| `py -m forgeos gui` | Open desktop interface |
| `py -m forgeos add PATH "request"` | Add task for an existing local repo |
| `py -m forgeos list` | Stream task history as JSON |
| `py -m forgeos plan ID --model auto` | Ask local model to plan a queued/planned task (exclusive lease; completed tasks cannot be overwritten) |
| `py -m forgeos test ID --preset python-unittest --allow-host-tests` | Run tests from the original trusted repository through the selected runtime. Queued/planned/failed tasks only, never overwrite passed/blocked tasks |
| `py -m forgeos run ID --workspace-parent PATH --allow-host-tests` | Prototype edit/test/repair cycle on trusted source |
| `py -m forgeos recover` | Mark interrupted states for safe inspection |
| `py -m forgeos recover-edits WORKSPACE` | Recover pending edit transaction |
| `py -m forgeos resume-safe --workspace-parent PATH --allow-host-tests` | Resume only eligible tasks; add `--stream-report` for memory-bounded JSON Lines output |
| `py -m forgeos diagnostics stats` | Output evidence statistics |
| `py -m forgeos learning stats` | Verified repair-learning statistics |
| `py -m forgeos memory` | One-time own-process memory measurement |

The CLI has a broader list of test preset names than the secure Windows packaged runner actually supports. **Preset visibility does not imply it is safe or usable in every environment.** For the packaged Windows EXE, `python-pytest` now uses the **bundled pytest module** in the same restricted AppContainer as unittest; the package is optional for source-only installations. Its logging file is redirected away from Windows `NUL` into the disposable snapshot and watched against a 32 MiB per-file limit. Other third-party pytest plugins do not autoload in that sandbox.

### Bounded task results and planning output

When you re-plan an existing planned task or rerun tests for a previously failed task, ForgeOS clears the previous result **atomically as the new attempt begins**. If the new operation fails or is blocked, the prior result does not reappear as if it belonged to the new attempt. Inspect the current task status and error; earlier outcomes are not automatically reused as acceptance evidence.

ForgeOS now limits newly stored **planning results and manual-test result records to 64 KiB of UTF-8/JSON text**, reusing its existing checkpoint storage budget. A result that exceeds the limit is **rejected rather than truncated**, and the task is marked **Blocked** for review; ForgeOS does not report it as a pass. Error messages retained in the task database are limited to the last 4,000 characters. The original full test output, when available, belongs in the existing content-addressed diagnostic evidence store, not an oversized SQLite task row. If you encounter the limit, inspect the task's diagnostics and choose a smaller, independently testable task; do not increase the limit solely to hide a failure.

These storage guards protect SQLite and the activity view; they do not establish a fixed maximum for total Python or model-process RAM usage. Full cross-platform CI and real Windows application testing are still required.

### Test runner availability and timeout safeguards

When a manual or autonomous test cannot start its runtime, or the test process exceeds its configured timeout, ForgeOS records a **failed, incomplete test result** (with the structured reason `test-runtime-unavailable` or `test-timeout`). It does **not** interpret an unavailable executable, a containment failure, or a timed-out process as an ordinary test assertion failure. Autonomous repair stops and leaves the workspace and task checkpoint for inspection; `resume-safe` will not replay that incomplete-evidence checkpoint automatically.

Check the selected test preset, installed test tools, host-execution authorization, local model readiness, and the task's diagnostics. Fix the underlying runtime issue before making a new, explicitly authorized attempt. The original Git worktree and code are not silently discarded. These safeguards are implemented, but cross-platform full-suite and real Windows acceptance remain unverified.

**Malformed test results:** ForgeOS also checks that a runner returns a real Boolean `passed` value, a text `output` field, and a Boolean `capture_complete` value when supplied. Strings such as `"false"` or `"true"` are not accepted as pass/fail evidence. Invalid results block task approval and, for autonomous runs, mark the checkpoint for manual review instead of repeatedly invoking an untrusted runner.

### Packaged Windows test evidence and disk budget

In the packaged Windows executable, a zero exit code from the secure AppContainer is **not sufficient** to approve a test. ForgeOS also requires its snapshot-owned `test-output.txt` to exist and be readable after the child exits. A missing file, a substituted symbolic link, or a read failure produces `secure-test-output-unavailable`, with `passed=False` and `capture_complete=False`. If native AppContainer stdout capture is incomplete, a complete child test log cannot override that failure. Incomplete child evidence likewise cannot report a pass.

The secure Windows output monitor now enforces **one combined 32 MiB limit** across captured native stdout, the owned test output file, and the optional pytest log. Exceeding that combined budget terminates the contained job and blocks acceptance. No additional polling loop or service was introduced; the existing output monitor checks this limit. Inspect the blocked task and reduce excessive test logging instead of increasing the limit without resource measurements. Real packaged Windows validation is still required.

After the child exits, ForgeOS opens the required `test-output.txt` through an
identity-bound descriptor and streams it through the existing bounded evidence
reader. The file must still be an unlinked regular file with the same identity
before and after opening; a hard-link, reparse point or replacement during
that last read fails closed and cannot redirect diagnostic capture.

### Automated test-output limits

**If the diagnostic archive cannot be written** (such as disk full, read-only storage, or an invalid pre-archived evidence reference), ForgeOS marks the test **not passed**, sets `capture_complete=False`, and records `evidence-archive-failed`. It retains only a bounded head/tail preview with a manual-review warning, even when the test executable exited with code 0. Do not treat the exit code alone as acceptance: free disk space or repair diagnostic storage, inspect the task, and explicitly rerun testing when safe. Automatic repair and approval remain blocked until the evidence path is healthy.

If ForgeOS reports **Incomplete test evidence requires manual review**, the task remains **blocked** and automatic recovery will deliberately skip it. This prevents repeated `resume-safe` runs from executing the same test that exceeded diagnostic limits or failed to retain its original output. The workspace and file-integrity hashes are retained for inspection, but this checkpoint is not eligible for automatic test/repair/replay. Check the task's last error and the test output settings before creating a new, explicitly authorized task; do not delete the original workspace or disable safety limits just to force it to pass.

When a source-mode test on Linux produces more than **32 MiB** of combined stdout/stderr, ForgeOS stops that test promptly rather than reading an unlimited output flood until the normal test timeout. The result is a **failed, incomplete diagnostic capture**; it is not treated as a passing test or as complete evidence for automatic repair. Output at or below the limit follows the existing disk-backed capture path. The change reuses the existing reader thread and adds no idle monitoring or extra dependency.

On POSIX source-mode runs, ForgeOS also checks whether a test left a helper process running after its parent exited. It terminates helpers remaining in that test's process group and marks the result failed with incomplete evidence, even when the parent reported exit code 0. Helpers that deliberately detach into another process group are outside this safeguard; source-mode execution is not a general-purpose sandbox.

The packaged Windows AppContainer runner enforces its separate native output-file limits. Neither output limit makes arbitrary generated tests safe to run on an unrestricted host: use a disposable project and grant host-test execution only when intended.

## 8. Troubleshooting

| Symptom | Likely cause / next action |
| --- | --- |
| GUI does not open from source | Verify Python 3.10+, successful installation, Tkinter availability and `py -m forgeos gui` from the repo root |
| `ForgeOS.exe` absent | Use a successful Windows Actions run and download its artifact before retention expires |
| “Project must be an existing local Git repository” | Select the folder containing the `.git` entry; initialize/commit the practice repository first |
| Ollama connection failure | Install/start Ollama and confirm the local server; `ollama list` should show a model |
| Auto model selection fails | No installed model fits the preflight heuristic, or the local model list is unavailable; use a smaller installed model |
| Host execution permission prompt | The checkbox is required for both manual tests and autonomous builds; CLI direct tests need `--allow-host-tests`. Only grant permission for code you trust |
| Unsupported preset in packaged Windows | Packaged secure runner supports `python-unittest` and experimental bundled `python-pytest`; npm/Go/Cargo/.NET presets remain unsupported in frozen mode. Never bypass the fail-closed restriction |
| Task is Blocked after restart | Run `recover`, inspect workspace/journal/checkpoint, then use eligible guarded recovery |
| Task is already being planned or completed | Wait for the active planner or inspect task history. GUI and CLI now share lease-fenced plan/test actions; passed/blocked work is not silently retested or overwritten |
| Diagnostic evidence cannot be opened | Archive may be missing, truncated, over size limit or fail integrity validation; do not treat an incomplete report as successful acceptance |
| App feels slow or low on memory | Choose a smaller local model, reduce context, review `memory`; telemetry covers ForgeOS only, not Ollama or compilers |

## 9. Limits to understand before relying on ForgeOS

- No general Android APK / web application production builder yet.
- No in-app GitHub authentication, pull request creation or release publishing yet.
- No signed Windows installer or complete unattended end-to-end release acceptance.
- Test containment and supported presets depend on runtime. Source execution can touch the host.
- No promise that 4/8 GiB hardware runs local models successfully.
- Local evidence is sensitive, unencrypted data; guard it accordingly.

## 10. More help

- [Main project overview](../README.md)
- [Architecture and trust boundaries](ARCHITECTURE.md)
- [Windows EXE build and acceptance](WINDOWS_BUILD.md)
- [Roadmap](ROADMAP.md)
- [Memory engineering limits](MEMORY_BUDGET.md)
- [Engineering handoff](HANDOFF.md)
- [Report a defect](https://github.com/DevTunaSoft/ForgeOS/issues)


## Experimental local model inventory and re-verification

The **Run verified local GGUF** window now offers **Choose from ForgeOS
downloads**, which lists existing GGUF files that still have a matching
pinned download manifest. The list is metadata-only and does **not**
rehash multi-gigabyte models; a full rehash is performed when the managed
server is started.

ForgeOS reads each small pinned manifest through a bounded identity check. A
replacement, Windows reparse point or oversized manifest is rejected before
its JSON can describe a model. Inventory can display a hard-linked manifest
for diagnosis, but managed inference requires the manifest to be an unlinked
regular file; model retirement applies its own explicit single-link check
before moving anything.

Advanced users can run `forgeos models-local` to list model weights and
`forgeos model-verify-local "C:\\Users\\You\\.forgeos\\models\\...\\model.gguf"`
to recheck one SHA-256 explicitly, without downloading or running anything.
Do not treat a model shown in the list as independently compatible,
licensed, executable or memory-safe. Missing/changed weights may be
displayed as unavailable and cannot be used by the managed process.


### Managed model shutdown and process cleanup (experimental)

On Windows, ForgeOS now attempts to attach its user-provided llama-server
process to a kill-on-close native Windows Job Object so child processes
terminate when the managed session ends. On POSIX, it uses a dedicated
process group. An actual Windows job lifecycle integration test is included,
but the tiny interval between process launch and Job attachment is still
not eliminated; physical Windows verification is pending.
Do not confuse model process cleanup with AppContainer isolation of
generated project tests. Close or stop a model only after the active
ForgeOS AI task completes.


### Recovering an interrupted GGUF download

Choose Browse Hugging Face and select the same repository and GGUF file. ForgeOS uses the immutable revision and SHA-256 to resume partial downloads. If the fully verified model was published but a crash prevented saving its manifest, repeating that selection checks the entire weight file and restores the missing manifest without transferring weights again.

If ForgeOS reports a conflicting manifest, an inconsistent download journal or a stray partial file, do not delete anything blindly: preserve the model directory for inspection. Correctly hashed files that are not actually GGUF are quarantined. This is experimental until tested using real multi-gigabyte files on Windows.

The small manifest and download journal are read through an identity and size
check before ForgeOS trusts their repository, revision, byte-count or digest
fields. If another process replaces, links, redirects or enlarges either
record while it is being opened, the download fails closed and leaves the
record available for inspection. This protects the recovery decision without
loading model weights into memory; it is not a lock against non-cooperating
processes.


### Pause or resume a large GGUF download

1. In **Browse Hugging Face**, search, inspect the desired GGUF file, and choose **Download and verify**. Check license and free disk space first.
2. Watch the progress message for the **Download** phase, followed by a separate **Verify** phase. Model weights are read and hashed incrementally rather than loaded entirely into RAM.
3. Select **Pause download** to stop after the current network read and preserve the partial file and download journal. Closing the browser during a download also requests pause and waits for the worker before the dialog closes.
4. Resume by selecting the *same* public repository and GGUF file later. ForgeOS pins the concrete revision/hash from Hugging Face, requests only missing bytes when the revision is unchanged, and validates the entire result before publishing the model.
5. If the upstream revision changed, the old partial is **not** silently combined with new model weights; inspect the old directory and decide whether to keep it. Avoid manually deleting interrupted files unless you have verified they are no longer needed.

Pausing is cooperative, not instantaneous while a network socket read is blocked (up to the configured timeout). Closing the entire ForgeOS process forcibly is not equivalent to using Pause. Verification itself may also be paused before final publication. This feature has simulated transfer tests; real multi-gigabyte Windows download, pause, resume and power-loss testing remain pending.


### Memory warning when starting a local GGUF model

ForgeOS chooses a conservative default context based on available system RAM. If a manually entered context exceeds the current RAM profile, startup is refused rather than risking a large llama.cpp KV-cache allocation. The app also checks available RAM again just before launching the local server, because memory may have changed while verifying the model's large SHA-256. Close competing applications, select a smaller GGUF, or use a smaller context within the suggested memory profile, then retry. A passed check cannot promise there will never be a later out-of-memory failure.


### Recoverable GGUF retirement and restoration

When you no longer want a local downloaded GGUF in the active inventory, first stop any llama.cpp server using it. The experimental CLI-only retirement feature moves the selected file and its pinned manifest into local, same-volume recovery storage. This is a reversible removal from the active list, **not disk space reclamation**. No cloud calls, inference, or deletion occur.

1. Inspect active models using: python -m forgeos models-local
2. Optionally recheck the weights with: python -m forgeos model-verify-local "ABSOLUTE_PATH.gguf"
3. Explicitly retire just one model with: python -m forgeos model-retire-local "ABSOLUTE_PATH.gguf" --confirm
4. Save the 32-character retirement ID from the output. To inspect all IDs use: python -m forgeos models-retired
5. Restore with: python -m forgeos model-restore-local "RETIREMENT_ID"

Each command accepts --directory PATH to select nondefault model storage. A retirement refuses an unfinished .part or .part.json transfer. A complete SHA-256 check occurs under the existing exclusive download lock. The recovery journal is written *before* either original file is moved, and remains available if one rename fails. Restoration rechecks the identity and SHA-256, refuses overwriting existing files, and handles either half-completed move. If models-retired says interrupted_retirement_or_restore, use its ID for a retry. If it says needs_manual_inspection, do not manually delete files; preserve them for diagnosis.

**Safety limitations:** A running external llama-server might still be reading this model, even when the CLI has no evidence of it; stop native inference first. The lock coordinates ForgeOS downloads and retire/restore, not arbitrary foreign processes. Windows junction/reparse-point races, real power-loss resilience, and active-model GUI interlocks still require real-machine acceptance.


### Recoverable model retirement from the desktop

The Managed local GGUF window's **Choose from ForgeOS downloads** picker now includes **Retire selected GGUF (recoverable)**. Select an existing downloaded GGUF and confirm the dialog. ForgeOS refuses to start this operation while an AI task, local model operation or owned llama.cpp server is active. It computes the entire file SHA-256 outside the GUI event thread, uses the existing exclusive download lock and returns a recovery ID. The picker remains open during verification so the result is not silently lost. Keep the recovery ID for the command-line model-restore-local operation; models-retired can list IDs again. The weights remain on disk and do not free storage. If another independently launched llama-server is running, close it manually before using this feature: ForgeOS can guard only its own managed session.


### Restore a recoverably retired local model in the desktop app

Open **Managed local GGUF**, choose **Restore a retired GGUF (recoverable)** and select the appropriate retirement journal. Choose **Restore selected GGUF** and confirm. ForgeOS checks the stored SHA-256 and original manifest, refuses to overwrite an occupied destination, and puts the model back in the original revisioned folder. The model picker can then select the restored weight. Interrupted retirement or restore records can also be recovered if their journal is still valid; a record marked **needs manual inspection** is not automatically restored. Stop any externally running inference process that may use these files. This action is offline and does not delete files to reclaim storage.


**Recovery integrity:** ForgeOS refuses recovery journals shared via filesystem hard links, reads the journal and saved manifest through bounded identity checks, and checks the recovery identity again after obtaining the download lock. A replacement, reparse point, hard link, malformed record or oversized record fails closed and remains available for inspection. ForgeOS also checks for occupied destination files before moving weights. If recovery is rejected, do not delete the recovery journal or overwrite destination files; inspect the conflict and retry only when safe. An external program that changes files concurrently is not fully isolated by the current cooperative ForgeOS lock.


### Managed local model memory snapshot

While a ForgeOS-managed local model is running, open **Managed local GGUF** and
choose **Show model memory**. On Windows, ForgeOS reports the Job Object memory
ceiling plus peak committed memory for the managed process tree and the peak
for a single process. This is a one-shot reading; ForgeOS does not continuously
poll the model in the background. It does not include GPU/shared graphics
memory and should not be interpreted as a complete performance benchmark.


### Test a managed local model before using it on a project

After starting a verified managed GGUF, select **Test local model** in the
Managed local GGUF window. ForgeOS sends a fixed harmless prompt containing
no project files or task text. The model must return the exact empty edit
contract `{"files":[]}` as valid JSON. ForgeOS reports pass/fail and elapsed
time in Activity & diagnostics; on Windows it also includes the available
one-shot Job Object peak-memory reading. A failed self-test does not edit a
workspace, run project tests, or silently switch to a cloud model.


### Optional managed-model RAM cap

The **Max model RAM MiB** field in Managed local GGUF is optional. Leave it
blank to use ForgeOS's automatic safe ceiling. On Windows, entering a value
of 512 MiB or greater can only reduce the automatically calculated Job Object
ceiling; it cannot grant the model more memory than ForgeOS considers safe
from the current RAM snapshot. This controls the managed llama.cpp process
tree only, not the whole computer or project test processes.


### Save a benchmark baseline before comparing local models

While a verified managed GGUF is running, open **Managed local GGUF** and choose
**Save benchmark baseline**. ForgeOS performs the harmless empty-edit JSON
self-test and saves a small JSON evidence record under
`~/.forgeos/benchmarks`. It records pinned GGUF repository/revision/file/SHA,
runtime SHA/release tag when known, context, threads, RAM tier, startup and
self-test latency, configured model-memory ceiling and available one-shot
Windows Job Object peak memory.

The record deliberately excludes the managed API key, local endpoint and
project source. It is labelled baseline-only and cannot qualify or route a
model. License and quantization remain explicitly unverified until #54 stores
trusted source metadata for those fields.


### Screening benchmark rules

ForgeOS now has a fixed local screening corpus for eight roles: planning,
implementation, review/refactor, debugging, test generation, security review,
documentation and context summarization. There are exactly five screening
tasks per role. The scorer is deterministic: it checks either an exact JSON
items schema or the existing ForgeOS edit-manifest contract plus explicit
required/forbidden markers. It does not ask another AI to judge the answer and
does not execute generated project code during screening.

A role survives screening only with at least **4/5 valid JSON/contracts** and
**3/5 objective rubric passes**. Screening is intentionally weaker than final
qualification; the router still requires the issue #55 qualification evidence
(95% structured output, 80% objective success, <=5% timeout/crash/OOM and zero
fail-open safety violations).


### Run a five-task role screening from the GUI

With a verified managed GGUF running, open **Managed local GGUF**, choose one
of the eight **Screening role** values, and select **Run 5-task role
screening**. ForgeOS sends only the fixed synthetic prompts for that role. The
screen shows progress after each prompt and keeps the model/task interlocks
active so normal project work cannot overlap the screening run.

**Cancel screening** is cooperative: the response already being generated may
finish, then ForgeOS stops before the next prompt and writes the partial
evidence file. Closing the managed-model window during screening requests the
same cancellation and keeps the window open until the partial evidence is
saved. The evidence records contract/objective results, timing, response byte
counts and error types; it does not save raw model replies or project source.
A screening pass is not final model qualification and does not enable routing.


### Qualified automatic model use

`qualified-auto` is an advanced safety mode for an already running verified
ForgeOS-managed GGUF. It does not guess that a model is good enough. ForgeOS requires
local final-qualification evidence for the task role, verifies that evidence against
the exact active GGUF and llama.cpp runtime SHA-256 identities, checks the measured
memory requirement against the current safe runtime budget, and refuses to start if
anything does not match. The current GUI uses the implementation role for build/test/
repair tasks. Final qualification records are not created by the five-task screening
button; screening remains a preliminary filter only.


### Final measured model qualification

After a managed GGUF passes the five-task role screen, the same **Managed local GGUF** window can run **Run 20-task final qualification** for the selected role. This sends only ForgeOS fixed synthetic benchmark prompts; it does not send project source. The run measures structured-output success, deterministic objective success, runtime failures, detected unsafe edit attempts, latency and managed peak memory.

A model becomes routeable for that role only after the complete run meets every gate: at least 95% structured-output success, at least 80% objective success, no more than 5% runtime failures, zero detected safety violations, exact model/runtime SHA-256 identity and measured peak memory. **Cancel qualification** preserves partial evidence, but partial evidence can never enable qualified-auto.
When ForgeOS loads saved qualification evidence, it opens each record through a
bounded identity check. A hard-linked, Windows-reparse-backed, substituted or
oversized record is ignored and cannot become the basis for autonomous routing;
the replacement is left for inspection. This is a race guard, not a lock
against an unrelated writer.


#### Interrupted qualified tasks

A qualified task checkpoint remembers the exact qualification evidence it used.
When Safe Resume is used with the same authenticated ForgeOS-managed runtime,
ForgeOS rechecks the model/runtime hashes and the saved evidence digest before
continuing. If the evidence file moved, changed, disappeared, or no longer fits the
safe memory budget, recovery stops rather than choosing another model automatically.


### Choose the task role

Execution settings now include **Task role**. The default is **implementation**. Choose planning, review/refactor, debugging, test generation, security review, documentation, or context summarization when that better describes the work. When **qualified-auto** is selected, ForgeOS requires final qualification evidence for the exact chosen role and exact active managed model/runtime identity; it does not silently substitute evidence from another role.


#### Qualified planning

When the active verified local GGUF has completed final qualification for the
`planning` role, selecting `qualified-auto` also protects **Generate plan**.
ForgeOS checks the same final evidence, exact model/runtime hashes and current
memory budget before sending the planning request. If planning evidence is not
qualified, ForgeOS stops instead of silently using another model.


Qualified-auto also requires the active local session to be authenticated by
ForgeOS. Model/runtime hashes by themselves are not sufficient. If the managed
session credential is missing, ForgeOS blocks planning or autonomous execution
before it reads qualification evidence or sends project content.


### Capture a local acceptance snapshot

Open **Activity & diagnostics** and choose **Run local acceptance snapshot**. ForgeOS asks whether this is the actual physical Windows release-acceptance machine; answer **No** for CI, a VM, or an ordinary development computer. ForgeOS writes one bounded JSON evidence file under `~/.forgeos/acceptance`. The report separately records Windows-host evidence and the operator's physical-target assertion, plus the current operating system, physical RAM snapshot, ForgeOS process memory, source Git commit when available, embedded packaged-build commit when present, and the SHA-256 of the running frozen ForgeOS executable when applicable.

If a verified managed GGUF is already running, the same button also performs the existing local-model self-test and records its exact model/runtime hashes, startup/self-test latency and managed peak-memory reading. It does **not** store the temporary API credential, project source, or raw model response.

From PowerShell, `py -m forgeos acceptance` captures the host/source or frozen-executable portion without a GUI-managed model session. A snapshot is evidence from that machine only; it does not by itself complete the Windows release gate.


### Windows GUI task buttons and mouse editing

The **Add task**, **Build / test / repair**, **Generate plan**, **Run tests only**, and **Refresh** actions appear immediately above the expanding development queue, including on smaller Windows displays. Right-click editable text fields to access **Cut**, **Copy**, **Paste**, and **Select All**; read-only fields do not permit cutting or pasting. These changes are pending physical Windows validation. Keep **Allow tests to run on this computer** unchecked until you deliberately approve executing generated code on the host.


### Slow responses from llama.cpp, LM Studio, and other local chat adapters (experimental)

The loopback OpenAI-compatible adapters now read responses in **8 KiB chunks** with the existing **8 MiB** response limit and a monotonic deadline: up to **180 seconds** for generation and **10 seconds** for local model-catalog requests. A server that keeps slowly sending response bytes cannot keep the request alive indefinitely. Individual blocking socket operations can extend the actual elapsed time beyond the nominal deadline. ForgeOS does not create an extra worker or background monitor for this check. On a timeout, inspect the active model and its context/RAM requirements; select a smaller local model when necessary rather than repeatedly launching duplicate tasks.

### Slow local AI responses (pending CI verification)

ForgeOS applies a 180-second monotonic response deadline to its built-in Ollama generator and retains an 8 MiB response-size ceiling. If a local model streams too slowly, the request fails with a deadline or socket timeout instead of waiting indefinitely. Socket reads may overshoot the exact wall-clock deadline by up to the configured per-read timeout; the lease guard still prevents stale task results from being published. This does not impose a deadline on other model backends.


### GitHub Desktop Git detection (Windows)

ForgeOS checks for `git` on PATH first. If Git is not on PATH, the Windows workspace engine also looks for GitHub Desktop's bundled `git.exe` under `%LOCALAPPDATA%\\GitHubDesktop\\app-*\\resources\\app\\git\\cmd\\git.exe`. Versioned application folders are detected automatically after GitHub Desktop updates. Restart ForgeOS after installing or updating GitHub Desktop. If the executable is missing, install Git for Windows or GitHub Desktop. This does not bypass the requirement that the selected project be an initialized Git repository.


### Saved desktop preferences

ForgeOS saves local project, workspace, model and task-role preferences in
`~/.forgeos/desktop-settings.json`. A saved value containing a NUL control
character is discarded on both save and reload; this prevents malformed
paths or identifiers from reaching the task workflow. If a preference does
not reappear after a restart, reselect it. ForgeOS does not alter the project
files or start background services as part of this validation.

At startup, ForgeOS reads this file through a bounded identity check. It only
accepts an unlinked ordinary file, rejects symlinks and Windows reparse
points, and refuses a file that changes while it is being opened or read. The
read is capped at 16 KiB. If the file is unavailable or fails these checks,
ForgeOS starts with no saved preferences; it does not apply a replacement
file's backend, model, endpoint or task-role values.

### Safe Resume: damaged or moved task workspaces

Safe Resume processes valid blocked-task checkpoints independently. If a
saved workspace path is malformed, too long, caught in a symlink loop, or
points to a symlinked task folder, ForgeOS skips that task and reports
"Workspace location mismatch" while continuing to inspect the remaining
checkpoints. It does not follow or delete the suspicious workspace and does
not start an AI model or run tests for that task.

Inspect the original task and its Git worktree before attempting another
run. Do not manually move, relink, or overwrite a worktree just to clear the
warning; preserve source and journal for diagnosis. A direct task resume
also rejects linked task workspaces. These are fail-closed recovery checks,
not proof of complete crash recovery on physical Windows machines.

### Startup readiness (experimental)

When a saved project is selected, ForgeOS performs a nonblocking, local-only startup check and displays **Ready** or **Setup needs attention** in the window status. Click **Check readiness** next to the inference backend to see specific results and corrective instructions. Checks cover executable Git discovery (including GitHub Desktop), project Git metadata, an existing workspace folder outside the project tree, and the local Ollama model catalog when Ollama is selected. The Ollama probe uses the loopback-only `/api/tags` endpoint with a three-second timeout and a one-MiB response cap. Other local backends currently report that connectivity remains unverified until task start. The check does not execute generated code, install dependencies, or bypass host-execution permissions. A green readiness status does **not** guarantee that generated code or tests will succeed.

### Context scanning during Safe Resume

If an interrupted task already has a verified test-pending checkpoint,
ForgeOS reruns its selected tests without regenerating code or building an
unneeded repository inventory for the AI. If tests fail and a repair is
still allowed, it then assembles a bounded context for that repair. When the
final permitted attempt fails, it reports failure without building an unused
next-repair prompt. Nothing is merged or published automatically.

When ForgeOS does extract Python symbols for a needed context, each selected
source file is read through the same identity and size boundary used for other
local untrusted files. The scanner accepts only a real regular file, caps the
read at 100 KiB plus one byte, and skips symbols if the file is replaced,
linked, reparse-backed or enlarged while it is being opened. The filename may
still appear in the metadata list, but source text is not copied into the
context. This is a fail-closed memory and path-race safeguard, not a lock on a
repository that another process is editing.

### Task database integrity and backup (experimental)

Create a trusted folder for backups first, then use these PowerShell commands:

```powershell
py -m forgeos db-check
py -m forgeos db-backup "C:\\ForgeOS-backups\\tasks-backup.sqlite3"
# Optional digest manifest for transfer/storage verification
py -m forgeos db-backup "C:\\ForgeOS-backups\\tasks-backup.sqlite3" --manifest "C:\\ForgeOS-backups\\tasks-backup.json"
```

Always supply a **new filename**. An existing backup is never overwritten.
ForgeOS includes committed SQLite WAL changes and checks the completed
snapshot before making it visible. No background backup process is started.
The shared publication check rejects linked or Windows-reparse staging files
and parent directories, and rechecks their identities before and after the
no-overwrite operation. This narrows path-substitution risk but is not a lock
against a non-cooperating writer or a physical power-loss guarantee.
Backup, restore, journal, recovery-bundle and worktree artifact publishers
also use identity-bound cleanup for their private temporaries. On POSIX the
original staging descriptor stays open through that decision, preventing
immediate inode reuse from authorizing deletion of a replacement; Windows
uses file identity checks without holding a handle that could block rename.
Unexpected substituted temporaries remain available for inspection.
Read-only database checks, streamed manifest hashing and restore-copy also
recheck the source regular-file identity after use; a link, reparse point or
substituted source fails closed before a destination is published. This is a
bounded check-and-use guard, not a concurrent-writer lock.
SQLite `-wal`, `-shm` and rollback-journal sidecars are checked without
following links or Windows reparse points, and pre-existing sidecar identities
are rechecked after read-only inspection or restore. An unsafe sidecar stops
the operation before a new copy is published.
The optional manifest is a bounded JSON record of the backup size and streamed
SHA-256; require it during offline inspection with
`db-restore-copy SOURCE DESTINATION --manifest MANIFEST`. It detects accidental
corruption, but is not encryption or an attacker-resistant signature.
ForgeOS reads this manifest through a bounded identity-checked descriptor. A
replacement, hard link, Windows reparse point or enlargement during the read
fails closed and remains available for inspection; this is a race guard, not
a system-wide concurrent-writer lock.
The backup is **not encrypted** and does not include external evidence archives,
Git worktrees or model weights. Do not attempt a live database overwrite to
restore. Read [database backup limitations](DATABASE_BACKUP.md) first.

For a deliberate offline replacement, stop ForgeOS and every process using its
database, then use a new rollback filename and explicit confirmation:

```powershell
py -m forgeos db-restore-activate `
  "C:\\ForgeOS-backups\\tasks-backup.sqlite3" `
  "$env:USERPROFILE\\.forgeos\\tasks.sqlite3" `
  "C:\\ForgeOS-backups\\tasks-before-activation.sqlite3" `
  --manifest "C:\\ForgeOS-backups\\tasks-backup.json" `
  --confirm
```

The command verifies the backup before moving the old active file to the
rollback path. It refuses existing rollback or activation-marker files,
symlinks, SQLite `-wal`/`-shm`/`-journal` sidecars and a missing destination
folder. On Windows an open database handle on the active or retained rollback
database causes the corresponding move or inspection to fail closed. If a later
step cannot finish, ForgeOS attempts to restore the old file; preserve the
rollback and `tasks.sqlite3.forgeos-activation.json` marker if that recovery
also fails. This is an operator-guided replacement, not automatic failover or
full power-loss recovery. Activation and recovery-bundle file moves recheck
their private source and parent identities around publication, so a path
substitution fails closed rather than deleting an unexpected file. Inspect an
interrupted marker without opening the
active Store:

```powershell
py -m forgeos db-activation-status "$env:USERPROFILE\\.forgeos\\tasks.sqlite3"
```

If the status is `pending` with phase `old_moved` or `publishing` and the
destination is absent, ForgeOS remains stopped and the retained rollback is
trusted, explicitly reconcile it with:

```powershell
py -m forgeos db-activation-rollback "$env:USERPROFILE\\.forgeos\\tasks.sqlite3" --confirm
```

Rollback refuses an occupied destination, sidecars, a remaining staged file or
an invalid marker. If a real process crash left the private staged replacement,
inspect it first. When the retained database is trusted and the staged file is
not needed, explicitly authorize its validated removal:

```powershell
py -m forgeos db-activation-rollback `
  "$env:USERPROFILE\\.forgeos\\tasks.sqlite3" --confirm --discard-staged
```

ForgeOS checks the staged file as a ForgeOS SQLite database before deleting
only that private path. It also requires the staged file and its real parent
to retain their identities through inspection, verifies the path is gone and
flushes the directory entry; a linked or substituted staged file is preserved
for inspection. On POSIX, activation keeps the original staging descriptor
open through cleanup so immediate inode reuse cannot authorize deletion of a
replacement. If activation fails before publication, its cleanup applies the
same file and parent identity check and preserves a substituted temporary.
`prepared` and `published` markers are diagnostic states; do
not force them into a rollback without inspecting the paths. If a
crash occurred after the replacement was published, the marker phase is
`published`, the active and retained rollback databases both exist, and the
staged path is absent, finalize the completed operation explicitly:

```powershell
py -m forgeos db-activation-finalize `
  "$env:USERPROFILE\\.forgeos\\tasks.sqlite3" --confirm
```

Finalization validates both databases, refuses sidecars or unexpected staging,
removes only the marker, and keeps the rollback copy. It accepts a
`publishing` or `published` marker only when the new destination is present
and staging is absent; it does not finalize a `prepared` marker or an
absent-destination rollback state.

If the marker phase is `prepared`, the active database was not moved. When the
staged replacement is not wanted, abandon that interrupted attempt explicitly:

```powershell
py -m forgeos db-activation-abandon `
  "$env:USERPROFILE\\.forgeos\\tasks.sqlite3" --confirm --discard-staged
```

Abandonment validates the active and staged databases, deletes only the staged
path and removes the prepared marker. It never moves the active database and
does not accept `old_moved` or `published` markers.

Current activation markers also bind the staged replacement and retained
rollback with streamed size/SHA-256 values. A valid-looking substituted file
is rejected when that binding is present. This detects accidental or
same-user replacement; it is not encryption or a public-key signature.
Marker reads are capped at 4 KiB and verify an unlinked regular-file identity
before and after reading. If a marker is hard-linked, reparse-backed or
substituted during a recovery command, ForgeOS stops and preserves the
interrupted state for inspection.
When ForgeOS advances a marker phase, it also rechecks the real marker,
temporary marker and parent directory before atomic replacement and verifies
the new marker afterward. Redirected parents, hard-linked markers and
unexpected staging substitutions fail closed; this is a bounded race guard,
not a complete concurrent-writer lock or physical power-loss guarantee. On
POSIX, private staging cleanup holds the original descriptor until the
identity decision; Windows avoids that handle because it can block atomic
rename and uses the existing file-identity comparison instead.
When a recovery operation clears a marker, ForgeOS rechecks the same unlinked
regular-file and parent identity, verifies that the marker path is gone, and
flushes the directory entry. A missing-marker cleanup only accepts a path that
was already absent; linked or substituted marker paths remain for inspection.

ForgeOS also keeps a bounded-field, append-only lifecycle journal in the task
database. Inspect the newest entries without loading task result blobs:

```powershell
py -m forgeos journal --limit 50
py -m forgeos journal --task-id 1 --limit 20
py -m forgeos journal-check
py -m forgeos journal-export "C:\\ForgeOS-backups\\journal.jsonl"
py -m forgeos journal-handoff "C:\\ForgeOS-backups\\task-1-handoff.jsonl" --task-id 1 --manifest "C:\\ForgeOS-backups\\task-1-handoff.json"
py -m forgeos journal-handoff-check "C:\\ForgeOS-backups\\task-1-handoff.jsonl" --manifest "C:\\ForgeOS-backups\\task-1-handoff.json" --evidence-directory "$env:USERPROFILE\\.forgeos\\diagnostics"
```

Journal rows record task, lease and recovery transitions—not full prompts,
errors or test output. Test transitions may include a bounded action, attempt,
artifact identifier and evidence digest for correlation. `journal-check` streams
semantic checks and returns a failure status with capped diagnostics if rows
refer to missing tasks or contain malformed metadata. It never repairs or
deletes records. The command is for recovery diagnosis; it does not
activate a backup or replace the running database.
`journal-export` writes one bounded event object per line in ascending event
order and refuses an existing destination. It excludes task prompts, raw
errors and test output; it is not a complete database or evidence backup.
`journal-handoff` is the task-scoped recovery form: it writes a header, the
current scalar task state, ordered lifecycle events (including validated
artifact/evidence identifiers), and a completion summary. It bounds journal
text before loading it, omits prompts, project paths, checkpoints, leases,
errors and test output, and never executes or imports actions. Run
`journal-check` first; this file is a non-executing replay plan, not a complete
database or evidence/worktree bundle.
`journal-handoff-check` verifies the handoff offline, one bounded JSONL record
at a time. It detects malformed or oversized records, record-order changes,
non-increasing event IDs, invalid references, and summary mismatches while
retaining at most 32 diagnostics. A successful check proves only internal
format consistency; it does not authenticate the file, recover missing
evidence, import journal rows, or execute any action.
Pass `--manifest` to `journal-handoff` to publish a separate bounded JSON
manifest containing the handoff byte size and streamed SHA-256, then pass the
same manifest to `journal-handoff-check` to detect transfer or storage
corruption. This digest is not a signature or encryption and must be retained
under a trusted policy if it is used as an integrity reference.
The handoff manifest is read through the same bounded identity-checked
descriptor boundary as a database-backup manifest, so a substituted or linked
file is rejected before its JSON is trusted.
Both output paths are new-file destinations. If handoff publication fails,
ForgeOS removes its temporary files and publishes neither artifact. If the
handoff is already published and a later manifest write fails, the handoff is
retained but the manifest is absent; check the handoff without `--manifest` or
retry with a new manifest path only after diagnosing the storage failure.
When `--evidence-directory` is supplied, the checker verifies each referenced
SHA-256 gzip archive by streaming and hashing its decompressed bytes under the
existing 32 MiB bound. It never creates the directory or returns the archived
text. Missing, symlinked, corrupt or oversized evidence makes the report
invalid; omit this option when the evidence store is intentionally unavailable.

For a content-free worktree integrity inventory, use a destination outside the
worktree:

```powershell
py -m forgeos worktree-manifest `
  "C:\ForgeOS-workspaces\task-1" `
  "C:\ForgeOS-backups\task-1-worktree.jsonl"
py -m forgeos worktree-manifest-check `
  "C:\ForgeOS-backups\task-1-worktree.jsonl" `
  "C:\ForgeOS-workspaces\task-1"
py -m forgeos worktree-archive `
  "C:\ForgeOS-workspaces\task-1" `
  "C:\ForgeOS-backups\task-1-worktree.zip"
py -m forgeos worktree-archive-check `
  "C:\ForgeOS-backups\task-1-worktree.zip"
py -m forgeos recovery-bundle-index `
  "C:\ForgeOS-backups\task-1-handoff.jsonl" `
  "C:\ForgeOS-backups\task-1-worktree.zip" `
  "C:\ForgeOS-backups\task-1-bundle-index.jsonl" `
  --manifest "C:\ForgeOS-backups\task-1-handoff.json" `
  --evidence-directory "$env:USERPROFILE\.forgeos\diagnostics" `
  --database "C:\ForgeOS-backups\tasks-backup.sqlite3" `
  --database-manifest "C:\ForgeOS-backups\tasks-backup.json"
py -m forgeos recovery-bundle-index-check `
  "C:\ForgeOS-backups\task-1-bundle-index.jsonl" `
  --handoff "C:\ForgeOS-backups\task-1-handoff.jsonl" `
  --worktree-archive "C:\ForgeOS-backups\task-1-worktree.zip" `
  --manifest "C:\ForgeOS-backups\task-1-handoff.json" `
  --evidence-directory "$env:USERPROFILE\.forgeos\diagnostics" `
  --database "C:\ForgeOS-backups\tasks-backup.sqlite3" `
  --database-manifest "C:\ForgeOS-backups\tasks-backup.json"
py -m forgeos recovery-bundle `
  "C:\ForgeOS-backups\task-1-handoff.jsonl" `
  "C:\ForgeOS-backups\task-1-worktree.zip" `
  "C:\ForgeOS-backups\task-1-recovery.zip" `
  --manifest "C:\ForgeOS-backups\task-1-handoff.json" `
  --evidence-directory "$env:USERPROFILE\.forgeos\diagnostics" `
  --database "C:\ForgeOS-backups\tasks-backup.sqlite3" `
  --database-manifest "C:\ForgeOS-backups\tasks-backup.json"
py -m forgeos recovery-bundle-check `
  "C:\ForgeOS-backups\task-1-recovery.zip"
py -m forgeos recovery-bundle-extract `
  "C:\ForgeOS-backups\task-1-recovery.zip" `
  "C:\ForgeOS-received\task-1"
py -m forgeos recovery-bundle-extract-check `
  "C:\ForgeOS-received\task-1"
py -m forgeos recovery-bundle-extract-resume `
  "C:\ForgeOS-backups\task-1-recovery.zip" `
  "C:\ForgeOS-received\task-1"
py -m forgeos recovery-bundle-auth `
  "C:\ForgeOS-backups\task-1-recovery.zip" `
  "C:\ForgeOS-backups\task-1-recovery.key" `
  "C:\ForgeOS-backups\task-1-recovery.auth.json"
py -m forgeos recovery-bundle-auth-check `
  "C:\ForgeOS-backups\task-1-recovery.zip" `
  "C:\ForgeOS-backups\task-1-recovery.auth.json" `
  "C:\ForgeOS-backups\task-1-recovery.key"
py -m forgeos recovery-bundle-auth-keygen `
  "C:\ForgeOS-backups\task-1-recovery.key"
```

The exporter hashes regular files in 64 KiB blocks under a 4,096-file,
64 MiB-per-file and 512 MiB-total bound. It excludes `.git`, `.forgeos`,
`.forgeos-runtime`, virtual-environment/dependency directories and `.env*`
files, and never copies source text or initializes the task database. The
checker detects changed, missing or extra regular files and rejects symlinks,
hard links, Windows reparse points and special files. Paths and hashes can
still be sensitive; this is an integrity inventory, not a complete source
archive or authenticated/encrypted bundle.

`worktree-archive` is the source-transfer form. It streams those same regular
files directly into a new ZIP and stores the JSONL integrity manifest inside
the archive; it does not load the worktree into RAM or initialize the task
database. The archive and its uncompressed source contents are each bounded
at 512 MiB, with at most 4,096 source files plus the manifest and a 4 MiB ZIP
central-directory bound. The checker validates every path and hash without
extracting files, and rejects absolute/traversal paths, noncanonical names,
encrypted entries, symlink-like metadata, oversized entries and malformed
archives. Both commands require a new destination outside the worktree.

The archive excludes `.git`, `.forgeos`, `.forgeos-runtime`, virtual-
environment/dependency directories and `.env*` files, but other source files
may contain secrets and should be reviewed before transfer. It is not a task
database backup, diagnostic evidence bundle, Git-object backup, signature or
encryption layer; it does not preserve empty directories or provide restore,
action replay or physical power-loss acceptance.

`recovery-bundle-index` is the coherence step after those separate artifacts
exist. It requires a valid handoff and source archive, optionally checks the
handoff digest manifest and a standalone `db-backup` SQLite file, and records
only bounded filenames, sizes and streamed SHA-256 values for the handoff,
archive, optional database backup and each referenced diagnostic gzip. The
database is inspected read-only and SQLite sidecars are refused.
When a handoff references evidence, `--evidence-directory` is required and
each archive is verified through the existing 32 MiB decompressed-evidence
bound. `recovery-bundle-index-check` rechecks the same paths and nested source
archive without initializing ForgeOS or extracting anything; a supplied
database backup is only inspected read-only. The index does not copy the
artifacts, authenticate them against a signer, encrypt them, activate the
database or execute a replay plan.

`recovery-bundle` is the transfer form after that validation step. It creates a
single new ZIP containing the handoff, source archive, optional manifests,
optional verified database backup, referenced evidence, and the index. It
streams inputs, refuses an existing destination, and self-checks the package
before publication. The checker uses private bounded temporary validation
copies rather than exposing an extraction directory; it also rechecks every
nested artifact and rejects unsafe or ZIP64 layouts. The package is capped at
1 GiB with 256 MiB of compressed evidence and a 512 MiB database artifact.
It is not encrypted, signed, an active-database replacement, a
restore/import/replay mechanism or a physical power-loss guarantee.

ForgeOS reads the package's 2 MiB JSONL index through an identity-checked,
bounded file descriptor. If another process replaces, redirects, hard-links
or enlarges that index while it is being opened or read, package creation or
checking fails closed and leaves the replacement for inspection. This protects
the recovery decision without loading an unbounded file into memory; it is a
race guard, not a system-wide concurrent-writer lock.

`recovery-bundle-extract` is the receiving-side materialization step. It
requires a new destination directory, verifies the package before copying,
uses private staging and bounded 64 KiB streaming writes, and writes
`recovery-bundle-extraction-state.json` before publication. The marker becomes
`complete` only after the copied handoff, source archive, optional database
backup, manifest and evidence pass the nested integrity checks. A failed or
interrupted move remains marked `failed` or otherwise incomplete; rerun against
a new destination after inspection. `recovery-bundle-extract-check` reports the
durable completion state. This step does not initialize the active Store,
start ForgeOS, execute a task or replay the handoff; a bundled database is only
checked read-only as a passive artifact.

If extraction stops after its destination marker is written, first confirm the
original extractor is no longer running. Then use
`recovery-bundle-extract-resume` with the same package and directory. It checks
the marker's package size and SHA-256, preserves only matching existing files,
refuses changed or linked files, and copies missing entries through the same
no-overwrite boundary. It marks the directory complete only after the final
index, evidence, source-archive and package checks pass. There is no automatic
retry worker; use a new destination if the existing directory is tampered or
its package identity no longer matches.

When a package contains a database backup, you can restore only that database
to a new copy without materializing the full receiving directory:

```powershell
py -m forgeos recovery-bundle-db-restore-copy `
  "C:\ForgeOS-backups\task-1-recovery.zip" `
  "C:\ForgeOS-received\tasks-restored.sqlite3"
```

This command validates the complete package, stages only the bounded database
entry privately, and reuses `db-restore-copy`'s read-only SQLite verification
and no-overwrite publication. It never replaces the active database or starts
ForgeOS. Add `--authentication AUTH_RECORD --key KEY` to require a detached
package HMAC before staging.

For deliberate offline activation from a recovery package, first exit ForgeOS
and stop every process that can open the active database. Supply a new rollback
path in the same existing directory as the active database and confirm the
operation explicitly:

```powershell
py -m forgeos recovery-bundle-db-restore-activate `
  "C:\ForgeOS-backups\task-1-recovery.zip" `
  "$env:USERPROFILE\.forgeos\tasks.sqlite3" `
  "$env:USERPROFILE\.forgeos\tasks-before-bundle-activation.sqlite3" `
  --confirm
```

This validates and stages only the bundled database, then reuses the guarded
activation boundary that retains the old active file before publishing the
replacement. Add `--authentication AUTH_RECORD --key KEY` when the package's
detached HMAC must be required. The command does not merge histories, start
ForgeOS or perform automatic failover; preserve and inspect any activation
marker left by an interrupted operation.

`recovery-bundle-auth` creates a detached authenticity record for a valid ZIP
using a local shared-secret key. The key file must be a regular, unlinked file
between 32 bytes and 4 KiB. `recovery-bundle-auth-check` requires the same key
and verifies the package filename, size, SHA-256 and HMAC-SHA-256. The key is
never placed in the record or CLI output. This is not ZIP encryption or a
public-key signature; protect and rotate the key outside ForgeOS, and treat
the package contents as potentially sensitive.

To enforce that authenticity before a transfer is checked or materialized, add
`--authentication CREDENTIAL_RECORD --key SHARED_SECRET_FILE` to
`recovery-bundle-check`, `recovery-bundle-extract`, or
`recovery-bundle-extract-resume`. Both options are required together. The gate
fails before extraction staging or resume moves, and the pair must be supplied
again for a later resume because the durable extraction marker never stores
the secret or authentication record.

If you do not already have a shared-secret file, create one with
`recovery-bundle-auth-keygen KEY_FILE`. It uses the operating system random
source, accepts 32 through 4,096 bytes, refuses an existing destination and
does not print the key. On POSIX the generated file is mode `0600`; on Windows,
review the file's account ACL and keep the key in a protected location. Do not
place the key in the recovery ZIP or publish it with the detached record.
On POSIX, ForgeOS also refuses an existing key whose group/other permission
bits are enabled; fix its mode rather than weakening the check.
