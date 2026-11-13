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

The desktop app also has a **Getting started & help** tab with a short first-task
workflow, shortcuts to repository selection/readiness/model discovery, and
plain-language tips for common setup and model errors. Use Tab/Shift+Tab to
move between controls.

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
forgeos model-catalog inspect Qwen/Qwen2.5-Coder-3B-Instruct-GGUF
forgeos model-download Qwen/Qwen2.5-Coder-3B-Instruct-GGUF EXACT-Q4-K-M.gguf --revision EXACT_40_DIGIT_REVISION
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

**Automatic startup recovery:** Select an existing workspace folder and enable
**Resume eligible tasks at startup and allow their tests to run on this computer**.
ForgeOS saves this permission immediately for that folder. It uses the same
checkpoint checks as manual recovery and may run eligible project tests on your
computer. Enable it only for workspaces you trust. To authorize a different
folder, turn the setting off, select the folder, and turn it back on. Check
**Activity & diagnostics** for the outcome. Checkpoints that need a running
authenticated managed model may still require manual recovery.

**GUI recovery memory limits:** The **Resume safe blocked tasks** action now
uses the same streaming recovery path automatically. The Activity tab shows
the interrupted-task count, totals of resumed/skipped/failed tasks, and only
the **12 most recent events**; older individual event messages are not kept
in the GUI report. Failure explanations are limited to 400 characters per
event, preserving the beginning and end. The task database and individual
workspaces retain their existing evidence; the GUI summary is not the full
audit log. No additional service or background polling is started.

**File transaction limits:** ForgeOS now streams backup copies instead of loading an existing source file fully into RAM. Existing files larger than 8 MiB are rejected for automated replacement, and edit journals larger than 32 KiB are preserved for manual inspection. Do not delete the journal or original workspace to bypass a safety check. These are transaction limits, separate from the 64 KiB task-checkpoint limit.

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


### Pause or resume a large GGUF download

1. In **Browse Hugging Face**, search, inspect the desired GGUF file, and choose **Download and verify**. Check license and free disk space first.
2. Watch the progress message for the **Download** phase, followed by a separate **Verify** phase. Model weights are read and hashed incrementally rather than loaded entirely into RAM.
3. Select **Pause download** to stop after the current network read and preserve the partial file and download journal. Closing the browser during a download also requests pause and waits for the worker before the dialog closes.
4. Resume by selecting the *same* public repository and GGUF file later. ForgeOS pins the concrete revision/hash from Hugging Face, requests only missing bytes when the revision is unchanged, and validates the entire result before publishing the model.
5. If the upstream revision changed, the old partial is **not** silently combined with new model weights; inspect the old directory and decide whether to keep it. Avoid manually deleting interrupted files unless you have verified they are no longer needed.

Pausing is cooperative, not instantaneous while a network socket read is blocked (up to the configured timeout). Closing the entire ForgeOS process forcibly is not equivalent to using Pause. Verification itself may also be paused before final publication. This feature has simulated transfer tests; real multi-gigabyte Windows download, pause, resume and power-loss testing remain pending.


### Check and limit model storage

Use `python -m forgeos models-storage` to report ForgeOS-managed GGUF weight bytes. The bounded offline scan includes active downloads, retired weights, resumable `.part` files and quarantined corrupt files; it does not hash model contents. Imported models referenced outside the managed model directory are not included. In the desktop **Execution** settings, choose a model store cap (0 disables the extra cap; available values are 16–512 GiB). The CLI can set a per-download cap with `python -m forgeos model-download OWNER/REPOSITORY MODEL.gguf --storage-quota-gib 64`. Quota-checked downloads serialize within one store; if a limit is reached, free space or raise it and retry the same model and revision to resume. This accounting is separate from the operating system's free-disk safety reserve. Physical quota behavior on Windows still needs acceptance testing.


### Memory warning when starting a local GGUF model

ForgeOS chooses a conservative default context based on available system RAM. If a manually entered context exceeds the current RAM profile, startup is refused rather than risking a large llama.cpp KV-cache allocation. The app also checks available RAM again just before launching the local server, because memory may have changed while verifying the model's large SHA-256. Close competing applications, select a smaller GGUF, or use a smaller context within the suggested memory profile, then retry. A passed check cannot promise there will never be a later out-of-memory failure.


### Import an existing GGUF without copying

In **Managed local GGUF**, select **Import existing GGUF without copying** and choose a local `.gguf` file. ForgeOS explicitly asks before hashing the selected file. It leaves the model bytes in place and writes a small `.forgeos.json` identity sidecar beside them; the containing directory must allow that sidecar to be created. ForgeOS rehashes the model before each managed inference session. This records integrity only: source repository, revision and license remain **unknown** unless you independently verify them. Do not treat the SHA-256 as proof that the model is trustworthy or licensed for your use.


### Recoverable GGUF retirement and restoration

When you no longer want a local downloaded GGUF in the active inventory, first stop any llama.cpp server using it. The experimental retirement feature moves the selected file and its pinned manifest into local, same-volume recovery storage. This is a reversible removal from the active list, **not disk space reclamation**. No cloud calls, inference, or deletion occur.

1. Inspect active models using: python -m forgeos models-local
2. Optionally recheck the weights with: python -m forgeos model-verify-local "ABSOLUTE_PATH.gguf"
3. Explicitly retire just one model with: python -m forgeos model-retire-local "ABSOLUTE_PATH.gguf" --confirm
4. Save the 32-character retirement ID from the output. To inspect all IDs use: python -m forgeos models-retired
5. Restore with: python -m forgeos model-restore-local "RETIREMENT_ID"

Each command accepts --directory PATH to select nondefault model storage. A retirement refuses an unfinished .part or .part.json transfer. A complete SHA-256 check occurs under the existing exclusive download lock. The recovery journal is written *before* either original file is moved, and remains available if one rename fails. Restoration rechecks the identity and SHA-256, refuses overwriting existing files, and handles either half-completed move. If models-retired says interrupted_retirement_or_restore, use its ID for a retry. If it says needs_manual_inspection, do not manually delete files; preserve them for diagnosis.

**Safety limitations:** A running external llama-server might still be reading this model, even when the CLI has no evidence of it; stop native inference first. The lock coordinates ForgeOS downloads and retire/restore, not arbitrary foreign processes. Windows junction/reparse-point races, real power-loss resilience, and active-model GUI interlocks still require real-machine acceptance.


### Recoverable model retirement from the desktop

The Managed local GGUF window's **Choose from ForgeOS downloads** picker now includes **Retire selected GGUF (recoverable)**. Select an existing downloaded GGUF and confirm the dialog. ForgeOS refuses to start this operation while an AI task, local model operation or owned llama.cpp server is active, and checks blocked task checkpoints for the selected model's SHA-256. A matching checkpoint or one that cannot be safely inspected prevents retirement. Managed llama.cpp also holds the same OS-backed lock used by download and retirement for its full server lifetime, including when another ForgeOS process owns the server. The model hash is rechecked after acquiring that lock. If shutdown cannot confirm the server exited, ForgeOS keeps the lock held. The picker remains open during verification so the result is not silently lost. Keep the recovery ID for the command-line model-restore-local operation; models-retired can list IDs again. The weights remain on disk and do not free storage. If another independently launched llama-server is running, close it manually before using this feature: ForgeOS can guard only its own managed session.

For an imported model, the same picker action removes only its path from ForgeOS's active local inventory. ForgeOS rehashes it, protects any blocked checkpoint that references its identity, and checks the managed model lock. The original GGUF and sidecar stay in place, so this does not reclaim disk space. Use **Import existing GGUF without copying** to register it again later.


### Restore a recoverably retired local model in the desktop app

Open **Managed local GGUF**, choose **Restore a retired GGUF (recoverable)** and select the appropriate retirement journal. Choose **Restore selected GGUF** and confirm. ForgeOS checks the stored SHA-256 and original manifest, refuses to overwrite an occupied destination, and puts the model back in the original revisioned folder. The model picker can then select the restored weight. Interrupted retirement or restore records can also be recovered if their journal is still valid; a record marked **needs manual inspection** is not automatically restored. Stop any externally running inference process that may use these files. This action is offline and does not delete files to reclaim storage.


**Recovery integrity:** ForgeOS refuses recovery journals shared via filesystem hard links, checks the recovery identity again after obtaining the download lock, and checks for occupied destination files before moving weights. If recovery is rejected, do not delete the recovery journal or overwrite destination files; inspect the conflict and retry only when safe. An external program that changes files concurrently is not fully isolated by the current cooperative ForgeOS lock.


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

### Task database integrity and backup (experimental)

Create a trusted folder for backups first, then use these PowerShell commands:

```powershell
py -m forgeos db-check
py -m forgeos db-backup "C:\\ForgeOS-backups\\tasks-backup.sqlite3"
```

Choose a new destination file for both backup and restore. If the file already
exists or another process creates it during the copy, ForgeOS preserves it
and exits with status 1.

Always supply a **new filename**. An existing backup is never overwritten.
ForgeOS includes committed SQLite WAL changes and checks the completed
snapshot before making it visible. No background backup process is started.
The backup is **not encrypted** and does not include external evidence archives,
Git worktrees or model weights. Do not attempt a live database overwrite to
restore. Read [database backup limitations](DATABASE_BACKUP.md) first.
