"""Frozen Windows GUI entry point with a headless packaging smoke check."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


ACCEPTANCE_TASKS = 520
ACCEPTANCE_RESULT = "Synthetic diagnostic text, no project data. " * 200


def seed_gui_acceptance_history(path):
    """Synthetic SQLite data for exercising real frozen Tk paging only.

    Avoid spawning processes, touching user projects, or requiring a Git repo.
    All data lives under the temporary acceptance-only database directory.
    """
    from forgeos.core import Store
    store = Store(path)
    try:
        with store.db:
            store.db.executemany(
                "INSERT INTO tasks(project,description,status,created,updated,result) "
                "VALUES(?,?,?,?,?,?)",
                ((str(path.parent), "Synthetic task " + str(i), 
                  "passed" if i % 2 else "failed", 0.0, 0.0,
                  ACCEPTANCE_RESULT + str(i))
                 for i in range(ACCEPTANCE_TASKS))
            )
        return store.counts()["total"]
    finally:
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="ForgeOS Windows desktop application")
    parser.add_argument("--smoke", action="store_true",
                        help="Check packaged imports without opening a window")
    parser.add_argument("--gui-acceptance-report", metavar="PATH",
                        help="Exercise actual Tk GUI and write a local JSON report")
    parser.add_argument("--sandbox-unittest", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--secure-sandbox-smoke", action="store_true",
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.sandbox_unittest:
        from forgeos.sandbox_runner import run_unittest
        return run_unittest()
    if args.secure_sandbox_smoke:
        from forgeos.secure_smoke import run_secure_smoke
        return run_secure_smoke()
    if args.gui_acceptance_report:
        # A temporary empty DB, no AI model loads and no network calls.
        from forgeos.gui import launch
        try:
            with tempfile.TemporaryDirectory(prefix="forgeos-gui-acceptance-") as tmp:
                db_path = Path(tmp) / "tasks.sqlite3"
                seed_gui_acceptance_history(db_path)
                result = launch(str(db_path), acceptance=True)
        except BaseException as exc:
            result = {"gui_started": False, "closed_cleanly": False,
                      "error": type(exc).__name__ + ": " + str(exc)}
        Path(args.gui_acceptance_report).write_text(
            json.dumps(result, indent=2), encoding="utf-8")
        return 0 if (result.get("gui_started")
                     and result.get("queue_initialized")
                     and result.get("closed_cleanly")
                     and result.get("paginated_history_ok")
                     and result.get("offline_manual_available")
                     and result.get("tasks_verified") == ACCEPTANCE_TASKS
                     and result.get("page_sizes") == [150, 150, 150, 70]
                     and isinstance(result.get("ram_after_gui_startup_bytes"), int)
                     and result["ram_after_gui_startup_bytes"] > 0
                     and isinstance(result.get("ram_after_paging_bytes"), int)
                     and result["ram_after_paging_bytes"] > 0
                     and result.get("rss_growth_during_paging_bytes", 64*1024*1024 + 1)
                     <= 64 * 1024 * 1024) else 12
    if args.smoke:
        from forgeos.core import Store, Ollama
        from forgeos.engine import execute_task
        from forgeos.gui import launch
        from forgeos.workspace import create_workspace
        print("ForgeOS packaged imports OK")
        return 0
    from forgeos.cli import default_db
    from forgeos.gui import launch
    launch(str(default_db()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
