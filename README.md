# ForgeOS — Autonomous Software Engineering Platform

**Status: evolving local-first prototype. Git worktree edits, bounded test/repair loops, diagnostics, and Windows frozen AppContainer unittest isolation exist; full autonomous application delivery and interactive Windows acceptance are not yet complete.**

ForgeOS is a local-first Windows-oriented desktop application being developed to transform natural-language requirements into tested software. The long-term goal includes AI planning, safe code changes, GitHub workflows, Windows EXE / Android APK / web builds, automated tests and self-repair, recovery, and complete source and handoff exports through one beginner-friendly interface.

**Implemented prototypes:** SQLite task queue and checkpoints, one Tkinter GUI, local Ollama planning and JSON code edits, isolated Git workspaces, journaled edits, bounded test/repair loops and resume checks, SHA-256 diagnostic evidence, repair-learning statistics, local model selection, Windows EXE CI packaging, and fail-closed frozen AppContainer execution for Python unittest. GitHub management *inside ForgeOS*, Android APK/web builders, broad production preset isolation, reliable backup/restore and interactive acceptance remain unfinished.

## Start on Windows (PowerShell)

Requirements: Python 3.10+ and Git; optional Ollama for planning.

    cd ForgeOS
    py -m pip install -e .
    py -m forgeos gui

1. Select an existing local Git repository folder.
2. Enter a task description and select Add task.
3. Optionally install Ollama and pull an available local coding model. Select it under Local model or use auto routing.
4. Select a task and its test preset, then use Build / test / repair. In source development mode, host test execution requires an explicit opt-in. Frozen Windows builds require supported AppContainer isolation.
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

Source/development-mode tests may run repository code on the host only after explicit permission; do not run untrusted repositories that way. The packaged Windows executable uses a fail-closed AppContainer runner for the supported Python-unittest preset; other frozen presets need their own secure runtime implementations. This boundary has passed the hosted Windows security smoke, not every real-world Windows configuration. Evidence files may contain unencrypted secrets; do not upload them, or place passwords/tokens in task descriptions. Ollama requests remain local. Real Windows GUI/Ollama end-to-end acceptance remains outstanding.

See docs/REQUIREMENTS.md, docs/ROADMAP.md and docs/HANDOFF.md.


### Windows EXE (prototype)

A Windows PyInstaller build workflow is available under GitHub Actions. See [Windows build instructions](docs/WINDOWS_BUILD.md). A successful build is not proof of full desktop functionality or safe unattended execution.


## RTK-inspired diagnostics and repair efficiency (merged phases 1 and 2)

PRs #28 and #30 merged the first diagnostic, learning, routing and trusted-filter prototypes into main. See docs/RTK_ENHANCEMENTS.md for the 52-outcome tracker and docs/ENHANCEMENT_DEDUP_AUDIT.md for the source-backed review of duplicated/overlapping capabilities. PR #31 extends the existing learning backend and GUI; changes under review are not yet part of main.

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


## Low-memory execution (PR #32, in development)

ForgeOS is being engineered for a lean single desktop GUI, with basic
project/diagnostic workflows targeted for 4 GiB machines and conservatively
sized local AI targeted for 8 GiB and above. These are NOT verified minimum
hardware requirements. A small installed model may be necessary; heavyweight
local models will not fit every machine.

The application now streams test output into a capped temporary spool,
archives complete <=32 MiB originals using the existing SHA-256 evidence
store, and shows bounded previews. Large or unarchivable outputs cannot
automatically pass autonomous acceptance. Tk activity logs and task lists
avoid accumulating massive result blobs. RAM-aware Ollama auto-selection
reduces context and can unload idle models under memory pressure.

Run a reproducible, synthetic, local-only benchmark after installation:

    py tools/memory_benchmark.py --mib 12 --check

See docs/MEMORY_BUDGET.md for implementation limits, tests, and the real
Windows physical/VM acceptance that still must be completed. This benchmark
reports traced Python allocations, NOT total process-tree RAM or Ollama VRAM.


## Memory-bounded task history

The desktop queue is now paged in blocks of 150 tasks with **Older tasks** and
**Newer tasks** controls. Refresh retrieves only a short task summary and
SQL-level aggregate counts instead of loading and rendering every old result
or full description. This is a memory optimization, not a deletion or
retention limit: the CLI and SQLite archive still preserve prior tasks.


## Scalable task exports and memory status

The CLI `python -m forgeos list` now emits JSON one SQLite row at a time,
instead of constructing one giant Python list of every historical diagnostic
and result. The JSON format and task order remain unchanged. The desktop
Activity tab also includes an on-demand **Show local memory snapshot** button,
sharing the same process-RAM reporting code used by `python -m forgeos memory`.
It does not run a monitor in the background, and it cannot measure Ollama or
other build subprocesses; physical Windows 4/8 GiB acceptance remains required.
