# ForgeOS Windows EXE — build and validation

GitHub Actions **Windows executable** builds a single-file Windows application
named **ForgeOS.exe** using Python 3.12 and PyInstaller.

## Download

Open the repository's **Actions** tab, select **Windows executable**, select
a successful run, and download the **ForgeOS-Windows-EXE** artifact. Extract
the ZIP to obtain ForgeOS.exe. Artifacts expire after 14 days.

## Run

Double-click ForgeOS.exe on Windows 11. The app opens its Tkinter dashboard.
The first startup may be slower because PyInstaller extracts bundled files.

Git is required on the computer for worktree creation, and Ollama must be
installed and running with the selected local model for AI operations. The
EXE packages the Python application, **not** Git, Ollama, or model weights.

## Verification scope

CI runs the Python regression suite, builds the frozen EXE and checks packaged
imports. It also launches the **real frozen Tkinter GUI** in a hosted Windows
runner with a hidden window and temporary synthetic task database: it checks
both tabs, queue pagination through 520 test tasks, startup/cleanup and a
one-shot memory-growth bound. A separate hosted frozen AppContainer smoke
checks security boundaries for supported test workloads.

These are stronger than import-only smoke, but **not** proof of a real
end-user Windows installation, interactive Ollama use, complete autonomous
application delivery, or validated 4 GB/8 GB hardware operation.

See the [ForgeOS User Manual](USER_MANUAL.md) for exact user-facing steps,
execution permissions and troubleshooting.

## Local build

From the repository root on Windows:

    py -3.12 -m pip install "pyinstaller>=6.0,<7" .
    py -3.12 -m PyInstaller --noconfirm --clean --onefile --windowed --name ForgeOS --paths src --add-data "docs/USER_MANUAL.md;forgeos" forgeos_build/launcher.py

The output is dist/ForgeOS.exe. Do not distribute it as production-ready
until real-device installation, AI workflow and security acceptance are
completed. **Source/development-mode test execution can run code on the host**
and should be used only on trusted projects. The packaged Windows version
uses a restricted AppContainer for supported test workloads; not every test
preset has a secure frozen execution path. Never bypass a refused sandbox
by running untrusted tests directly on the host.


## Built-in offline manual

The packaging command embeds the **single canonical**
[ForgeOS User Manual](USER_MANUAL.md) in the EXE. In the desktop app,
select **Activity & diagnostics** → **How to use ForgeOS** to read it offline
without an account, browser or network connection. The frozen GUI acceptance
run fails if the manual is missing from the packaged application.
