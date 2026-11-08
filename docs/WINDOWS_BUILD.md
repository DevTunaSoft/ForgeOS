# ForgeOS Windows EXE — build, download and acceptance

**Stage:** development prototype. The GitHub Actions workflow generates a single-file `ForgeOS.exe` using Python 3.12 and PyInstaller, but this is **not a signed production installer**.

## Download a development build

1. Go to [Actions → Windows executable](https://github.com/DevTunaSoft/ForgeOS/actions/workflows/windows-exe.yml).
2. Open a **successful** build on the intended branch. Check the run's commit if comparing changes.
3. Under **Artifacts**, download `ForgeOS-Windows-EXE`.
4. Extract the ZIP to a folder you control. Run `ForgeOS.exe` on Windows 11 in a test environment.

The artifact retention period is **14 days**, and a successful Actions run must not be confused with a published supported release.

## What is bundled

The EXE packages the Python/Tkinter ForgeOS application and an offline copy
of the single canonical [User Guide](USER_GUIDE.md). From the running
application, select **Activity & diagnostics → How to use ForgeOS**.
The guide is read only when requested and needs no browser or internet access.

The EXE packages the Python/Tkinter ForgeOS application. It does **not** package:

- Git or an existing local project repository;
- Ollama, model weights or a cloud AI service;
- Android SDK, web build toolchains, or other arbitrary project dependencies.

Git is needed for worktree operations. For AI features, Ollama must be installed and serving a suitable local model. The local endpoint is `127.0.0.1:11434`. The initial program can open without a model, but model-powered planning/editing cannot.

## What current Windows CI verifies

The `.github/workflows/windows-exe.yml` workflow on `main`:

1. Uses Python 3.12 on a GitHub-hosted Windows runner.
2. Compiles Python source and executes the regression tests.
3. Builds a one-file windowed PyInstaller executable.
4. Checks that the EXE exists and has a nontrivial size.
5. Executes a packaged `--smoke` command for frozen imports.
6. Starts actual Tkinter dashboard widgets in a headless GUI lifecycle harness, verifies tabs/queue, measures own-process RAM, runs the event loop, then closes cleanly.
7. Populates a disposable SQLite fixture with **520 synthetic tasks**, checks four GUI pages (150/150/150/70), navigates back and asserts the paging RSS growth stays within the test's 64 MiB threshold.
8. Runs the frozen AppContainer security smoke, with an outer watchdog and nonzero failure on failed containment checks.
9. Verifies the canonical offline user guide exists **inside** the packaged EXE
   during the actual frozen Tkinter GUI lifecycle smoke.

These are genuine hosted CI checks, **not** a substitute for a person launching the EXE on a target Windows laptop with a real local Ollama model and an actual project.

## Test execution trust boundary

- The **frozen Windows** workflow uses a restricted AppContainer + Job Object + disposable snapshot for its currently supported `python-unittest` test preset.
- **Unsupported frozen presets fail closed**; the program must not fall back to unrestricted host commands to make testing appear successful.
- The **source/development** workflow may run generated project tests on the host with explicit opt-in; do not test untrusted repositories there.
- Python `pytest` secure frozen support is proposed in [PR #29](https://github.com/DevTunaSoft/ForgeOS/pull/29), which is **not on `main` until merged**.
- Docker is not part of the required ForgeOS architecture.

See [architecture and security boundaries](ARCHITECTURE.md) and [user guide](USER_GUIDE.md).

## Build locally on Windows

In PowerShell from the repository root:

```powershell
py -3.12 -m pip install "pyinstaller>=6.0,<7" .
py -3.12 -m unittest discover -s tests -v
py -3.12 -m PyInstaller --noconfirm --clean --onefile --windowed --name ForgeOS --paths src --add-data "docs/USER_GUIDE.md;forgeos" forgeos_build/launcher.py
```

The result should be `dist/ForgeOS.exe` if all commands succeed. Do not claim it succeeded without examining the actual files and command exit statuses.

For a packaged GUI-only acceptance on Windows, the launcher supports:

```powershell
.\dist\ForgeOS.exe --gui-acceptance-report C:\Temp\forgeos-gui-report.json
```

Use an existing writable destination for the report. This acceptance mode uses a temporary database and does **not** run real local AI, generated code or an application build. The packaging workflow separately invokes `--secure-sandbox-smoke`; this is a security smoke mode, not a general user workflow.

## Required physical Windows acceptance before release

Record OS/hardware/Python/EXE version, actual results, diagnostics and fixes for each scenario:

1. Launch the executable interactively, verify visible controls and keyboard accessibility.
2. Choose an existing disposable repository, add a task, close/reopen ForgeOS and confirm persistence.
3. Connect to a locally installed Ollama coding model and generate a plan and bounded edit manifest.
4. Perform safe edit/test/repair and verify the resulting Git worktree, test output and original evidence.
5. Inject process termination, interrupted writes and damaged/mismatched checkpoint cases; verify recovery cannot overwrite unrelated work.
6. Confirm AppContainer restriction in a real Windows user session; verify unsupported preset behavior refuses host fallback.
7. Run representative activity/memory tests on target 4/8/16/32 GiB configurations, including Ollama and child processes, and compare results with the intended [memory budgets](MEMORY_BUDGET.md).
8. Verify safe artifact distribution, dependency instructions and release lifecycle on a clean computer.

Until the above is actually verified, ForgeOS remains **experimental** and should be used on disposable/trusted projects rather than mission-critical sources.
