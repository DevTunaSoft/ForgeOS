# ForgeOS — Autonomous Software Engineering Platform

**Status: v0.1.0 foundation prototype, not a complete autonomous software builder or production release.**

ForgeOS is a local-first Windows-oriented desktop application being developed to transform natural-language requirements into tested software. The long-term goal includes AI planning, safe code changes, GitHub workflows, Windows EXE / Android APK / web builds, automated tests and self-repair, recovery, and complete source and handoff exports through one beginner-friendly interface.

**Implemented in this milestone:** SQLite-backed persistent task queue, Tkinter GUI, local Ollama planning, explicit allowlisted test presets, CLI, startup task recovery and foundation tests. Autonomous code editing, EXE/APK builds, direct GitHub integration and self-repair are not yet implemented.

## Start on Windows (PowerShell)

Requirements: Python 3.10+ and Git; optional Ollama for planning.

    cd ForgeOS
    py -m pip install -e .
    py -m forgeos gui

1. Select an existing local Git repository folder.
2. Enter a task description and select Add task.
3. Optionally install Ollama and pull qwen2.5-coder:3b, then click Generate plan with Ollama.
4. Select a task and a test preset, then click Run selected test.
5. Tasks persist at ~/.forgeos/tasks.sqlite3 across restarts.

## Command line

    py -m forgeos add C:\path\to\git-repo "Fix startup error"
    py -m forgeos list
    py -m forgeos plan 1 --model qwen2.5-coder:3b
    py -m forgeos test 1 --preset python-unittest
    py -m forgeos recover

## Test ForgeOS

PowerShell:

    $env:PYTHONPATH = 'src'
    py -m unittest discover -s tests -v

## Security and limitations

This prototype runs tests directly on the host and does not sandbox project code. Use only trusted local repositories. No encryption at rest or authentication boundary is implemented. Do not put passwords or tokens in task descriptions. Ollama connection is restricted to localhost. Runtime interaction with a real Windows GUI has not yet been verified.

See docs/REQUIREMENTS.md, docs/ROADMAP.md and docs/HANDOFF.md.


### Windows EXE (prototype)

A Windows PyInstaller build workflow is available under GitHub Actions. See [Windows build instructions](docs/WINDOWS_BUILD.md). A successful build is not proof of full desktop functionality or safe unattended execution.
