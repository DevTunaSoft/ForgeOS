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

CI runs the Python regression suite on Windows, builds the frozen EXE,
verifies the artifact exists, and launches it with --smoke to validate
packaged imports. This **does not** verify actual window rendering, buttons,
local model responses, security isolation, or a real application build.
Those require interactive Windows acceptance testing.

## Local build

From the repository root on Windows:

    py -3.12 -m pip install "pyinstaller>=6.0,<7" .
    py -3.12 -m PyInstaller --noconfirm --clean --onefile --windowed --name ForgeOS --paths src packaging/launcher.py

The output is dist/ForgeOS.exe. Do not distribute the build as production
ready until the desktop acceptance tests pass. Generated code and test
processes are **not sandboxed**; only run trusted projects.
