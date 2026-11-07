# ForgeOS cumulative engineering handoff — v0.1.0

## Current state
Initial foundation only. Python standard-library runtime, Tkinter desktop prototype, SQLite task store and recovery, Ollama planning, explicit allowlisted tests, CLI. This is **not** an autonomous software builder or production release.

## Architecture
- src/forgeos/core.py: task persistence, local Ollama planning and test runner.
- src/forgeos/cli.py: entry point and task workflows.
- src/forgeos/gui.py: Tk GUI, background worker and results display.
- tests/test_core.py: persistence, recovery, test safety and failure behavior.
- docs/REQUIREMENTS.md and docs/ROADMAP.md: cumulative mission and phases.
- .github/workflows/ci.yml: proposed Windows and Linux test execution.

## Verified local evidence before GitHub import
10 unit tests passed and Python syntax compilation passed under Linux. GitHub-hosted Windows validation is pending workflow execution. No true Windows GUI smoke test, local Ollama model run or EXE/APK build has yet been performed.

## Known limitations
- Worker thread reads Tk variables, requiring UI-thread handoff before native Windows validation.
- Host tests execute arbitrary trusted-project code without sandboxing.
- SQLite records are not encrypted, and interrupted operations restart from queued.
- No event journal, backup/restore or granular progress checkpoints.
- Ollama requests cannot yet be cancelled; no autonomous fix loop.
- No GitHub integration from the application itself.
- GitHub repository visibility may be public; never upload credentials or private data.

## Run and recover
On Windows: install Python 3.10+ and Git; from repository root run 'py -m pip install -e .' then 'py -m forgeos gui'. For tests, set PYTHONPATH=src and run 'py -m unittest discover -s tests -v'. Ollama is optional for planning. DB is ~/.forgeos/tasks.sqlite3. Use 'py -m forgeos recover' to requeue interrupted tasks. Safeguard SQLite with its backup API.

## Next autonomous work
1. Observe CI results; fix every failure and rerun.
2. Eliminate Tk cross-thread access, introduce GUI-level smoke tests and cancellation.
3. Introduce structured event journal, integrity checks and transactional backup.
4. Add safe Git worktree operations and bounded patch application.
5. Add independent test-and-repair loop with auditable evidence.
6. Build and verify a real Windows installer and update user-facing documentation.
7. Keep cumulative complete-source deliveries and comprehensive handoffs.
