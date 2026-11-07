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


## Experimental RTK-inspired diagnostics and repair efficiency (PR #28)

Development is taking place on a separate feature branch for the agreed
52-enhancement program. See docs/RTK_ENHANCEMENTS.md for the full checklist.
These capabilities are not yet part of the stable main branch.

The new test runner archives original output locally before selecting important
diagnostic lines for local AI. It includes structured/text/raw parsing fallback
and SHA-256 evidence retrieval. Learning records test-verified repair signatures
and counters only; no untrusted previous repair is automatically executed.

After installing this branch, commands are:

    py -m forgeos diagnostics stats
    py -m forgeos diagnostics show <64-char-evidence-ref>
    py -m forgeos learning stats

The Activity & diagnostics tab includes an efficiency/repair-learning view.
Output reductions are byte counts, not actual AI token counts or dollar savings.
Archived test logs are local, sensitive, unencrypted data; restrict local access.
The current 32 MiB archive limit and in-memory command capture require further
hardening before unattended or production deployment.

### Optional automatic local model selection (experimental)

Use --model auto with planning or autonomous task execution to select from
already installed Ollama models, choosing smaller coding models for simple
tasks and larger ones for more complex tasks. The choice is pinned in the task
checkpoint. The current default remains qwen2.5-coder:3b for compatibility.

    py -m forgeos plan 1 --model auto
    py -m forgeos run 1 --workspace-parent C:\forgeos-work --model auto --allow-host-tests

No models are downloaded automatically. Model size is only a rough heuristic,
not a hardware acceptance test or guarantee of adequate RAM. Only local
Ollama at 127.0.0.1:11434 is supported.


### Optional, trusted output filters (experimental)

ForgeOS compacts repeated noncritical test lines only after archiving full
original output. Custom filters are a locally pinned JSON configuration:
~/.forgeos/output_filters.json. See docs/RTK_ENHANCEMENTS.md for schema.

    py -m forgeos filters status
    py -m forgeos filters trust

Changes to the filter file invalidate its trust hash and disable the rules.
No filter code or shell commands may run. This is consent and tamper evidence,
not a same-user attacker security boundary.


### Diagnose and compare task results (experimental)

1. In the Development tab, select a task that has run tests.
2. Open Activity & diagnostics.
3. Choose "Compare selected task: original vs AI summary".
4. The left pane shows what the AI received; the right shows the original
   SHA-256 verified raw evidence, if successfully archived. Original output can
   contain sensitive data, so opening it is always an explicit user action.
5. Choose "AI efficiency and repair learning" for measured output reductions,
   candidate repair success statistics, and custom filter trust health.

The GUI records which installed local model handled a task and labels
failure categories as advisory heuristics. It does not automatically apply
learned repair candidates. Large original logs have a bounded GUI preview;
use the diagnostics CLI to retrieve the complete retained evidence.
