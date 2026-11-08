# ForgeOS — User manual

**Audience:** first-time users, testers, and developers evaluating the current ForgeOS prototype. **Current scope:** existing local Git repositories, local Ollama AI, selected code editing/testing, diagnostics and recovery. ForgeOS does not yet provide a one-click production app builder.

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

## 2. Understand the screen

The one-window desktop prototype has **two tabs**:

| Location | What you will see | What it does |
| --- | --- | --- |
| **Development** | Project repository and **Browse** | Points ForgeOS to a local Git repository |
| **Development** | **Tell ForgeOS what to build** | Plain-English development task description |
| **Development** | Local model | Explicit Ollama model or `auto` selection from installed models |
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


### GUI task completion resilience

ForgeOS keeps the desktop queue poll scheduled even if a transient refresh,
widget, or local database read raises an exception. Worker threads also always
publish a completion event back to the Tk thread, including failures opening or
closing their worker database connection. This prevents a failed background
operation from permanently leaving task buttons disabled. If the GUI still
appears stuck, preserve the task database and workspace, restart ForgeOS, and
inspect the task status/recovery state rather than deleting files.
