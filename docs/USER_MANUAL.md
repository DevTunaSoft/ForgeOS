# ForgeOS User Manual

> **For the current ForgeOS prototype.** This manual is updated alongside the
> application. It describes what you can actually use today, not the eventual
> goal of a fully unattended application factory.
>
> **Last source review:** 2026-11-07, after the packaged Windows GUI/paging
> acceptance updates in PRs #32–#38. **Hardware acceptance is incomplete**:
> 4 GB and 8 GB are engineering targets, not supported minimum specifications.

## Contents

1. [What ForgeOS does today](#1-what-forgeos-does-today)
2. [Before you begin](#2-before-you-begin)
3. [Get ForgeOS running](#3-get-forgeos-running)
4. [Your first task in the desktop application](#4-your-first-task-in-the-desktop-application)
5. [Understand buttons and results](#5-understand-buttons-and-results)
6. [Find errors, logs, and memory information](#6-find-errors-logs-and-memory-information)
7. [Recover interrupted work](#7-recover-interrupted-work)
8. [Optional command-line reference](#8-optional-command-line-reference)
9. [Troubleshooting](#9-troubleshooting)
10. [Safety, data storage, and limitations](#10-safety-data-storage-and-limitations)
11. [New-feature documentation and release checklist](#11-new-feature-documentation-and-release-checklist)

## 1. What ForgeOS does today

ForgeOS is **one desktop interface** for keeping software-development tasks
together. You can select a local Git project, describe a change in plain
language, ask a local Ollama model to plan or generate code, run supported test
commands, inspect failures, and resume some interrupted work.

**Current working prototypes:** a Windows Tkinter GUI; task history; local
Ollama integration; AI-generated edits in separate Git worktrees; reversible
edit transactions; bounded test-and-repair attempts; selected recovery
checkpoints; SHA-256 diagnostic archives; a compact activity screen; a
resource-aware model option; and automated Windows EXE packaging and
security smoke tests.

**Not yet a completed turnkey product:** arbitrary Windows/Android/web
application building and release, GitHub management *inside* ForgeOS, an
installer with all dependencies, unattended end-to-end AI acceptance on a
real user machine, and guaranteed low-RAM hardware support. Do not treat
"Passed" on a test as proof that a generated application has been fully
released or deployed.

## 2. Before you begin

For the **Windows EXE**, you need Windows, Git, and an existing **local Git
repository** for your project. To use AI planning or code generation, also
install and run Ollama with a suitable **already downloaded** coding model.
The executable does **not** include Git, Ollama, model weights, or a preloaded
model. Nothing in ForgeOS automatically subscribes to a paid AI provider.

For the **source-code version**, you need Python 3.10 or later, Git, and
a local copy of this repository. Python package installation may retrieve
dependencies; ForgeOS AI requests themselves use only local Ollama.

You also need enough free disk space for a temporary worktree, build tools
and test results. RAM used by Ollama is **separate** from ForgeOS RAM. If your
machine has 4 GB or 8 GB, start with a small local model and modest tasks;
this has **not** yet been validated as a minimum hardware requirement.

**Important safety distinction:** The source/development version can execute
project test code on your Windows host, including when you select **Run tests
only**. The **Build / test / repair** action requires its host-test consent
checkbox; checking it does **not** magically sandbox generated code. Only
test projects you trust. Packaged Windows security isolation is narrower and
has supported-preset restrictions. See [Safety](#10-safety-data-storage-and-limitations).

### Find this guide inside the application

Once ForgeOS is open, select **Activity & diagnostics** and choose
**How to use ForgeOS**. The same guide opens in a scrollable **offline**
window. It is displayed as plain Markdown text to avoid requiring a browser
or a heavyweight rendering package. The EXE includes the guide at build time.

## 3. Get ForgeOS running

### Option A: Download the Windows executable

1. Open the [ForgeOS GitHub repository](https://github.com/DevTunaSoft/ForgeOS).
2. Choose **Actions** and then **Windows executable**.
3. Open a successful run on the **main** branch. Choose its
   **ForgeOS-Windows-EXE** artifact.
4. Extract the downloaded ZIP and locate **ForgeOS.exe**.
5. Double-click **ForgeOS.exe**. The first launch can be slower while its
   bundled files are extracted.

GitHub Actions build artifacts currently expire after **14 days**. This is a
**prototype**, not a signed production installer. If the artifact is absent,
check [Windows build instructions](WINDOWS_BUILD.md) or download source and
use Option B.

### Option B: Run from source on Windows

1. Download/clone the ForgeOS repository into a local folder.
2. Open **PowerShell** in that repository folder.
3. Install ForgeOS:

   ```powershell
   py -m pip install -e .
   ```

4. Launch the one desktop interface:

   ```powershell
   py -m forgeos gui
   ```

There is no mandatory Docker setup and no separate paid AI application.

### Optional: prepare Ollama for AI features

Install and start Ollama using its official instructions. Download a small
coding-capable model suitable for the RAM available to your computer, for
example by running an Ollama model-pull command for a model of your choice.
ForgeOS **does not pull models automatically**.

In ForgeOS, **Local model (auto = RAM-aware)** means it will look for an
already-installed model and apply conservative memory sizing. An explicit
model name overrides automatic selection. Automatic sizing is a heuristic;
it cannot promise a local model will fit in RAM or VRAM.

## 4. Your first task in the desktop application

**Step 1 — Pick the project.** On the **Development** tab, find
**Project repository**, select **Browse**, and choose a folder that already
contains a local Git repository (a `.git` directory). ForgeOS cannot yet
create an entire new project from an arbitrary empty folder in the GUI.

**Step 2 — Describe what you want.** Under **Tell ForgeOS what to build**,
type a clear, small change. For example:

> In this existing Python project, add a function that validates email
> addresses. Add unit tests for valid and invalid inputs. Do not change
> unrelated files.

**Step 3 — Choose how to run it.** Leave **Local model (auto = RAM-aware)**
set to `auto` if an appropriate local coding model is installed. Select the
matching **Test suite** (for a Python unittest project, `python-unittest`),
set **Repair retries** between 0 and 5, and choose a **Workspace folder**.
Workspaces are separate from your original Git repository.

**Step 4 — Add the task.** Select **Add task**. It appears in
**Development queue**. Choose the row before running actions.

**Step 5 — Choose an action.** For a low-risk first check, select
**Generate plan** (requires local Ollama but does not run generated tests).
To run code generation, choose **Build / test / repair** only after
understanding the host-execution warning and giving the required permission.
**Run tests only** runs your project's selected test preset; because it
executes project code, use it only on a trusted project.

**Step 6 — Review what happened.** Check the status label, open
**Activity & diagnostics**, and inspect the task's results. A successful
test status does not automatically merge code into your project or publish
an executable. Review the worktree and generated changes yourself.

### Development queue and navigation

The queue shows at most **150 tasks per page**. Use **Older tasks** and
**Newer tasks** to navigate without loading every historical diagnostic
into RAM. Historical tasks are not deleted by pagination.

## 5. Understand buttons and results

| Desktop control | What it does | What it does **not** do |
|---|---|---|
| **Add task** | Saves the plain-language request in the local task queue | Does not create a finished app |
| **Generate plan** | Asks local Ollama for a plan | Does not run tests or ship code |
| **Build / test / repair** | Attempts edits in a Git worktree, test execution and bounded repair | Does not auto-merge, commit, deploy, or guarantee success |
| **Run tests only** | Executes the chosen test preset | Is not a risk-free dry run |
| **Refresh** | Updates task history and totals | Does not restart failed work |
| **Older tasks / Newer tasks** | Changes the visible 150-task history page | Does not delete history |
| **AI efficiency and repair learning** | Shows local metrics, repair candidates and filter health | Does not automatically apply learned repairs |
| **Show local memory snapshot** | Shows current ForgeOS process memory and a system-RAM estimate | Does not measure Ollama/other build processes or GPU memory |
| **Compare selected task: original vs AI summary** | Opens a bounded preview of verified original evidence alongside the summary | Does not automatically publish or share the full log |

### What task statuses mean

| Status | Plain-language meaning |
|---|---|
| Queued | Saved but not yet run |
| Planning / Planned | Local AI planning is in progress / completed |
| Editing | ForgeOS is preparing or applying edits |
| Testing | A selected test suite is being executed |
| Passed | The recorded test attempt passed; review its scope |
| Failed | The recorded test attempt did not pass |
| Blocked | Action stopped; inspect diagnostics and recovery state |

The **Activity & diagnostics** tab keeps a bounded visible text history to
reduce memory use. Full captured diagnostic evidence, when available, is
stored separately, up to the configured archival limit.

## 6. Find errors, logs, and memory information

1. Select a task in **Development queue**.
2. Open **Activity & diagnostics**.
3. Choose **Compare selected task: original vs AI summary** to see the
   compact test summary beside the original-evidence preview.
4. Choose **AI efficiency and repair learning** to examine measured log
   reductions, trusted filter status and previously verified repair
   statistics. These are *advisory*, not self-executing commands.
5. Choose **Show local memory snapshot** for a one-time look at estimated
   RAM usage. There is no background RAM monitoring service.

If a diagnostic record provides a **64-character SHA-256 reference**, the
advanced CLI can retrieve the retained original:

```powershell
py -m forgeos diagnostics show YOUR_64_CHARACTER_REFERENCE
```

Original logs can contain sensitive data. Do not paste them into public
issues, attach them to releases, or give them to untrusted people. Logs over
the complete-evidence limit are explicitly marked; ForgeOS must not call an
incomplete capture a verified success.

### Check RAM from PowerShell

```powershell
py -m forgeos memory
```

The optional *source checkout* benchmark exercises synthetic output,
not your project or Ollama:

```powershell
py tools/memory_benchmark.py --mib 12 --cycles 3 --check
```

See [Memory budget and acceptance](MEMORY_BUDGET.md) for limits and the
difference between Python allocations, ForgeOS working-set memory, and
memory consumed by Ollama.

## 7. Recover interrupted work

If the application stops during a task, **do not delete its worktree** and
do not assume ForgeOS will continue autonomously after restart.

1. Start ForgeOS again. It marks certain interrupted editing/testing tasks
   **Blocked** for inspection, rather than silently rerunning code.
2. Look at the task's previous result and its worktree folder. Preserve
   anything you need before attempting further work.
3. From a source installation, this command inspects recovery statuses:

   ```powershell
   py -m forgeos recover
   ```

4. For a checkpoint that is *explicitly resumable*, advanced users can run:

   ```powershell
   py -m forgeos resume-safe --workspace-parent C:\forgeos-work --allow-host-tests
   ```

   **Warning:** This enables execution of project tests on your host.
   Use only a workspace containing trusted code. The command will skip
   checkpoints that lack a valid saved contract or whose paths do not match.
   It does not force recovery of every failed task.

5. If a pending edit journal requires rollback, carefully inspect the
   workspace first; then use:

   ```powershell
   py -m forgeos recover-edits C:\forgeos-work\task-1
   ```

The final command is meant for a **specific** affected task worktree, not a
random project folder. It does not guarantee that every possible interruption
is recoverable. For full recovery details see [development handoff](HANDOFF.md).

## 8. Optional command-line reference

These are advanced alternatives to using the single GUI. Run them from a
source installation after installing ForgeOS. Replace example paths and task
IDs with your own.

| Purpose | Command |
|---|---|
| Launch the GUI | `py -m forgeos gui` |
| Add a task | `py -m forgeos add C:\my-project "Fix a known bug"` |
| Show saved tasks as JSON | `py -m forgeos list` |
| Generate a local AI plan | `py -m forgeos plan 1 --model auto` |
| Run a test preset | `py -m forgeos test 1 --preset python-unittest` |
| Edit, test, repair (trusted repo only) | `py -m forgeos run 1 --workspace-parent C:\forgeos-work --model auto --allow-host-tests` |
| Resume a specific checkpoint | `py -m forgeos run 1 --workspace-parent C:\forgeos-work --model auto --allow-host-tests --resume` |
| Inspect interrupted task statuses | `py -m forgeos recover` |
| Resume eligible saved checkpoints | `py -m forgeos resume-safe --workspace-parent C:\forgeos-work --allow-host-tests` |
| Recover a pending edit journal | `py -m forgeos recover-edits C:\forgeos-work\task-1` |
| View memory | `py -m forgeos memory` |
| View diagnostics summary | `py -m forgeos diagnostics stats` |
| Restore an original log | `py -m forgeos diagnostics show YOUR_64_CHARACTER_REFERENCE` |
| Repair-learning statistics | `py -m forgeos learning stats` |
| Inspect trusted filters | `py -m forgeos filters status` |

The six currently selectable test presets are
`python-unittest`, `python-pytest`, `npm-test`, `cargo-test`,
`go-test`, and `dotnet-test`. **Availability in the menu does not
mean every preset works safely inside the frozen Windows sandbox.** Current
secure frozen support is limited; unsupported runtimes fail closed.

## 9. Troubleshooting

**ForgeOS will not open.** Confirm you have Windows for the EXE, or Python
3.10+ for the source version. For an EXE failure, check that you extracted
the downloaded ZIP. Follow the [Windows build guide](WINDOWS_BUILD.md).
A successful CI build does not prove every desktop environment is supported.

**"Project must be an existing local Git repository."** Choose the folder
of your *existing* project containing a `.git` directory. Do not point at
ForgeOS's empty workspace parent.

**"No installed local Ollama model fits..."** Start Ollama, download a
supported coding model, free system RAM, or use a smaller installed model.
Automatic routing never downloads a model or sends your project to a paid
cloud API.

**"Allow tests to run on this computer."** This is a meaningful execution
permission. Only consent when the chosen repository is trusted; generated
or third-party tests can do harmful work in source/development mode.

**Task says Blocked after a crash.** This is conservative on purpose. See
[Recover interrupted work](#7-recover-interrupted-work). Do not manually
delete journals or worktrees before inspection.

**Model responds but changes are poor.** Make the request smaller and more
specific, choose an appropriate *installed* coding model, inspect the
generated worktree, and review the test output. A correct full build is not
guaranteed.

**Tests time out or logs are incomplete.** The runner limits execution and
captured evidence. Very noisy tests may exceed the 32 MiB complete archive
limit. The incomplete-result marker is a protection, not proof that code
passed. Run narrower tests and diagnose the underlying noise.

**"Run tests only" says test executable unavailable.** The selected test
tool might not be installed (such as Node, cargo, Go, .NET or pytest), or
might be unsupported by the secure frozen environment. Do not bypass a
sandbox failure by using an untrusted project on the unrestricted host.

**RAM looks high.** The **Show local memory snapshot** button reports the
ForgeOS process, *not* Ollama's model weights, GPU allocations, or build
children. Consult [Memory budget and acceptance](MEMORY_BUDGET.md).

**No finished EXE/APK appears after "Passed."** Current tasks record code
changes and test results, not a universal release pipeline. Review outputs
and package them with the appropriate tool. Universal automatic packaging
is still under development.

## 10. Safety, data storage, and limitations

- ForgeOS task records are stored by default under
  `%USERPROFILE%\.forgeos\tasks.sqlite3` on Windows. Worktrees, model
  installations and diagnostic archives are separate resources.
- Original captured logs are stored locally by default under
  `%USERPROFILE%\.forgeos\diagnostics`. Archives are **not encrypted at
  rest** and may contain project secrets; don't share them.
- Keep passwords, tokens and sensitive personal details out of task
  descriptions, code examples and public issue reports.
- To copy the task SQLite file for a **manual backup**, first close ForgeOS
  and ensure no other task runner is writing to it. Preserve your Git
  repository and separate worktree folders too. This is not a verified
  one-click backup/restore system.
- Do not delete the workspace for an interrupted task before examining
  checkpoints and edit journals.
- Source/development-mode host execution is opt-in for autonomous builds;
  a test-only operation still executes code. A selectable test preset is
  not a security certification.
- The packaged Windows executable uses the restricted **AppContainer**
  security boundary for supported test workloads. It has been exercised in
  hosted CI, **not** guaranteed on every Windows installation or every
  workload. Unsupported frozen test environments must fail closed.
- 4 GB/8 GB system-memory claims are **targets**, not release minimums.
  Even if ForgeOS itself uses little memory, local AI and compilers may not.
- ForgeOS does not automatically publish GitHub changes, deliver
  Android APKs, or deploy applications simply because a task passed.
- Any uncertainty or failed build should appear explicitly in results;
  do not override fail-closed checks to make a task show green.

## 11. New-feature documentation and release checklist

This is a **living manual**: update it in the **same PR** as each relevant
code or GUI change. The repository's pull-request template asks for this
explicitly. The documentation-contract tests protect against accidentally
renaming controls/CLI commands without reviewing this guide.

Before calling a feature complete, verify:

- [ ] The new behavior is implemented and tested, not merely proposed.
- [ ] The relevant steps in this manual explain how to find and use it.
- [ ] Prerequisites, permissions and security implications are accurate.
- [ ] Errors, recovery actions and known limitations are explained.
- [ ] Any changed button labels, commands and paths match the actual source.
- [ ] The built-in **How to use ForgeOS** button opens the current offline
      manual in the packaged Windows executable without network access.
- [ ] Screenshots or examples (if supplied) match a tested build, not a mockup.
- [ ] README and [Windows build instructions](WINDOWS_BUILD.md) link to this
      manual rather than duplicating and eventually contradicting its steps.
- [ ] The [development handoff](HANDOFF.md) records the feature and the
      objective tests supporting it.

**Next planned manual chapters** (publish only with verified features):
model-download/onboarding wizard; true end-to-end application building;
GitHub import/PR workflows; automated ZIP/source exports; reliable backup
and restore; release/installer wizard; and fully verified low-RAM presets.
