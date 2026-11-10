"""ForgeOS desktop dashboard. Tk calls stay exclusively on the main thread."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from queue import Empty, Full, Queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .core import Store
from .local_models import create_model
from .model_roles import MODEL_ROLES
from .engine import execute_task
from .task_actions import plan_task, test_task

PRESETS = ("python-unittest", "python-pytest", "npm-test",
           "cargo-test", "go-test", "dotnet-test")
BUSY = {"planning", "editing", "testing"}
STATUS_LABELS = {
    "queued": "Queued", "planning": "Planning", "planned": "Planned",
    "editing": "Editing", "testing": "Testing", "passed": "Passed",
    "failed": "Failed", "blocked": "Blocked",
}


def _retirement_allowed(*, task_busy: bool, operation_busy: bool,
                        model_present: bool) -> bool:
    """Only allow an opt-in model move when the owned model is inactive."""
    return not (task_busy or operation_busy or model_present)


def _format_task(task: dict) -> tuple:
    return (task["id"], STATUS_LABELS.get(task["status"], task["status"]),
            task["description"][:140])


def user_guide_path() -> Path | None:
    """Canonical local guide; no network call, no startup content loaded."""
    bundle = getattr(sys, "_MEIPASS", None)
    candidates = ([Path(bundle) / "forgeos" / "USER_GUIDE.md"] if bundle else [])
    candidates.append(
        Path(__file__).resolve().parents[2] / "docs" / "USER_GUIDE.md")
    # The CI package installation may import site-packages, while the
    # development source checkout (containing the canonical guide) is cwd.
    source_root = Path.cwd()
    if (source_root / "src" / "forgeos" / "gui.py").is_file():
        candidates.append(source_root / "docs" / "USER_GUIDE.md")
    for candidate in candidates:
        try:
            if (not candidate.is_symlink() and candidate.is_file()
                    and candidate.stat().st_size <= 256 * 1024):
                return candidate
        except OSError:
            continue
    return None


def _managed_idle_due(*, last_used, now, task_busy, operation_busy,
                      idle_seconds=300) -> bool:
    """Cheap deterministic admission for unloading the single resident model.

    Only the GUI's existing event polling invokes this; no new timer thread,
    wake-lock, periodic subprocess or background memory sampling is created.
    """
    return (last_used is not None and not task_busy and not operation_busy
            and now - last_used >= idle_seconds)


def _managed_session_event(controller, *, now, last_used, task_busy,
                           operation_busy, stop_failed=False) -> str | None:
    """Classify one managed-model lifecycle event without extra timers.

    A process crash takes priority over idle unload. Do not tamper with
    credentials or stop the server while a task/operation is running. If
    cleanup itself previously failed, latch until an explicit user retry
    to avoid spawning an infinite background stop loop.
    """
    if (controller is None or task_busy or operation_busy or stop_failed):
        return None
    if not controller.running():
        return "crashed"
    if _managed_idle_due(
            last_used=last_used, now=now, task_busy=False,
            operation_busy=False):
        return "idle"
    return None


def launch(db_path, *, acceptance: bool = False):
    """Create the single desktop GUI; optionally self-verify its real lifecycle."""
    if type(acceptance) is not bool:
        raise ValueError("acceptance must be boolean")
    started_at = time.perf_counter()
    report = {"gui_started": False, "closed_cleanly": False} if acceptance else None
    store = Store(db_path)
    # Interrupted tasks must not silently restart: a worktree may contain
    # generated changes that require manual inspection.
    recovered = store.recover()
    try:
        root = tk.Tk()
    except BaseException:
        store.close()
        raise
    root.title("ForgeOS | Autonomous Development Dashboard")
    root.geometry("1160x790")
    root.minsize(880, 620)

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure("Title.TLabel", font=("Segoe UI", 21, "bold"))
    style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
    style.configure("Treeview", rowheight=29)
    style.configure("Accent.TButton", padding=(12, 8))
    style.configure("TButton", padding=(9, 6))

    # Persist only user-selected nonsecret preferences. Never persist runtime
    # credentials, generated code, or task descriptions in this file.
    from .desktop_settings import load_settings, save_settings
    settings_path = Path.home() / ".forgeos" / "desktop-settings.json"
    saved = {} if acceptance else load_settings(settings_path)

    def setting(name, default):
        return saved.get(name, default)

    project = tk.StringVar(value=setting("project", ""))
    model = tk.StringVar(value=setting("model", "auto"))
    task_role = tk.StringVar(value=setting("task_role", "implementation"))
    backend = tk.StringVar(value=setting("backend", "ollama"))
    endpoint = tk.StringVar(value=setting("endpoint", ""))
    preset = tk.StringVar(value=setting("preset", "python-unittest"))
    workspaces = tk.StringVar(value=setting("workspaces", str(Path.home() / ".forgeos" / "workspaces")))
    allow_host_tests = tk.BooleanVar(value=False)
    repair_count = tk.IntVar(value=setting("repair_count", 2))
    status = tk.StringVar(value="Ready")
    counts = tk.StringVar(value="")
    page = {"offset": 0}
    page_size = 150
    events = Queue()
    managed_events = Queue(maxsize=2)
    recovery_events = Queue(maxsize=1)
    acceptance_events = Queue(maxsize=1)
    browser_events = Queue(maxsize=1)
    managed = {"controller": None, "busy": False, "last_used": None,
               "unexpected_exit": False, "stop_failed": False,
               "screening_cancel": None, "qualification_cancel": None}
    busy = {"value": False}
    closing = {"value": False}

    shell = ttk.Frame(root, padding=16)
    shell.pack(fill="both", expand=True)
    header = ttk.Frame(shell)
    header.pack(fill="x")
    ttk.Label(header, text="ForgeOS", style="Title.TLabel").pack(side="left")
    ttk.Label(header, textvariable=status).pack(side="right")
    ttk.Label(shell, text="Local AI development | Isolated Git workspaces | Test and repair",
              foreground="#52647b").pack(anchor="w", pady=(0, 10))

    tabs = ttk.Notebook(shell)
    tabs.pack(fill="both", expand=True)
    dashboard = ttk.Frame(tabs, padding=10)
    activity = ttk.Frame(tabs, padding=10)
    tabs.add(dashboard, text="Development")
    tabs.add(activity, text="Activity & diagnostics")

    repo_row = ttk.LabelFrame(dashboard, text="Project repository", padding=10)
    repo_row.pack(fill="x", pady=(0, 10))
    ttk.Entry(repo_row, textvariable=project).pack(side="left", fill="x", expand=True)
    ttk.Button(repo_row, text="Browse", command=lambda: project.set(
        filedialog.askdirectory(title="Select Git repository") or project.get()
    )).pack(side="left", padx=(8, 0))

    request_frame = ttk.LabelFrame(dashboard, text="Tell ForgeOS what to build", padding=10)
    request_frame.pack(fill="x", pady=(0, 10))
    description = tk.Text(request_frame, height=4, wrap="word", undo=True)
    description.pack(fill="x")

    options = ttk.LabelFrame(dashboard, text="Execution settings", padding=10)
    options.pack(fill="x", pady=(0, 10))
    first = ttk.Frame(options)
    first.pack(fill="x")
    ttk.Label(first, text="Local model (auto = RAM-aware; qualified-auto = verified)").pack(side="left")
    ttk.Entry(first, textvariable=model, width=23).pack(side="left", padx=(6, 18))
    ttk.Label(first, text="Test suite").pack(side="left")
    ttk.Combobox(first, textvariable=preset, values=PRESETS,
                 state="readonly", width=20).pack(side="left", padx=(6, 18))
    ttk.Label(first, text="Repair retries").pack(side="left")
    ttk.Spinbox(first, from_=0, to=5, textvariable=repair_count, width=4).pack(side="left", padx=6)
    ttk.Label(first, text="Task role").pack(side="left", padx=(14, 0))
    ttk.Combobox(first, textvariable=task_role, values=MODEL_ROLES,
                 state="readonly", width=20).pack(side="left", padx=6)
    provider_row = ttk.Frame(options)
    provider_row.pack(fill="x", pady=(8, 0))
    ttk.Label(provider_row, text="Inference backend").pack(side="left")
    ttk.Combobox(provider_row, textvariable=backend,
                 values=("ollama", "llama.cpp", "lm-studio", "openai-local"),
                 state="readonly", width=17).pack(side="left", padx=(6, 16))
    ttk.Label(provider_row, text="Local endpoint (optional)").pack(side="left")
    ttk.Entry(provider_row, textvariable=endpoint, width=37).pack(side="left", padx=6)
    ttk.Button(provider_row, text="Browse Hugging Face",
               command=lambda: browse_hub_models()).pack(side="left", padx=6)
    managed_row = ttk.Frame(options)
    managed_row.pack(fill="x", pady=(6, 0))
    ttk.Button(managed_row, text="Run verified local GGUF",
               command=lambda: manage_local_gguf()).pack(side="left")
    ttk.Label(
        managed_row,
        text="Experimental CPU llama.cpp: user-provided trusted executable + "
             "SHA-256, downloaded verified GGUF; unloads after 5m idle; no Docker",
        foreground="#52647b").pack(side="left", padx=(9, 0))
    second = ttk.Frame(options)
    second.pack(fill="x", pady=(8, 0))
    ttk.Label(second, text="Workspace folder").pack(side="left")
    ttk.Entry(second, textvariable=workspaces).pack(side="left", fill="x", expand=True, padx=6)
    ttk.Button(second, text="Browse", command=lambda: workspaces.set(
        filedialog.askdirectory(title="Select workspace parent") or workspaces.get()
    )).pack(side="left")
    ttk.Checkbutton(
        options,
        text="Allow tests to run on this computer (generated code is NOT sandboxed)",
        variable=allow_host_tests,
    ).pack(anchor="w", pady=(8, 0))

    # Keep the primary actions above the expanding queue so they stay visible
    # at minimum window height and at high Windows display scaling.
    actions = ttk.Frame(dashboard)
    actions.pack(fill="x", pady=(0, 6))
    # Keep experimental tools on their own row so the main task controls
    # remain reachable at 880px minimum width and Windows display scaling.
    browser_row = ttk.Frame(dashboard)
    browser_row.pack(fill="x", pady=(0, 6))
    buttons = {}
    def add_button(key, text, callback):
        button = ttk.Button(actions, text=text, command=callback)
        button.pack(side="left", padx=(0, 8))
        buttons[key] = button

    tasks_frame = ttk.LabelFrame(dashboard, text="Development queue", padding=10)
    tasks_frame.pack(fill="both", expand=True, pady=(0, 10))
    ttk.Label(tasks_frame, textvariable=counts).pack(anchor="w")
    tree_frame = ttk.Frame(tasks_frame)
    tree_frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(tree_frame, columns=("id", "status", "task"),
                        show="headings", selectmode="browse", height=7)
    for col, width, stretch in (("id", 55, False), ("status", 115, False),
                                ("task", 720, True)):
        tree.heading(col, text=col.title())
        tree.column(col, width=width, stretch=stretch)
    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    pages = ttk.Frame(tasks_frame)
    pages.pack(fill="x", pady=(5, 0))
    older_button = ttk.Button(
        pages, text="Older tasks", command=lambda: change_page(page_size))
    older_button.pack(side="right", padx=(8, 0))
    newer_button = ttk.Button(
        pages, text="Newer tasks", command=lambda: change_page(-page_size))
    newer_button.pack(side="right")


    # Lightweight, Tk-thread-only monitor; no extra polling thread or network calls.
    monitor_text = tk.StringVar(value="Idle — select and start a task.")
    ttk.Label(activity, text="Live activity monitor", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
    ttk.Label(activity, textvariable=monitor_text, wraplength=850,
              justify="left").pack(anchor="w", pady=(2, 8))
    monitor = {"task_id": None, "operation": None, "started": None,
               "last_stage": None, "last_check": 0.0}

    diagnostics = tk.Text(activity, wrap="word", state="disabled")
    diagnostics.pack(fill="both", expand=True)
    # A long autonomous session must not accumulate unlimited Tk text buffers.
    logged_chars = {"count": 0}
    max_event_chars = 12000
    max_log_chars = 64000
    ttk.Label(activity, text="Task history and test output. No background execution occurs when this window closes.",
              foreground="#52647b").pack(anchor="w", pady=6)

    def log(text):
        value = str(text)
        if len(value) > max_event_chars:
            omitted = len(value) - 10000
            value = (value[:5000] + f"\n[ForgeOS UI: {omitted} characters omitted;"
                     " use saved diagnostics or CLI for full evidence]\n" + value[-5000:])
        value += "\n\n"
        diagnostics.configure(state="normal")
        diagnostics.insert("end", value)
        logged_chars["count"] += len(value)
        if logged_chars["count"] > max_log_chars:
            excess = logged_chars["count"] - max_log_chars
            diagnostics.delete("1.0", f"1.0+{excess}c")
            logged_chars["count"] = max_log_chars
        diagnostics.see("end")
        diagnostics.configure(state="disabled")

    def show_efficiency():
        try:
            from .optimizer_metrics import OptimizationMetrics
            from .error_learning import RepairMemory
            from .output_rules import TrustedOutputRules
            memory = RepairMemory(Path(db_path).with_name("repair_memory.sqlite3"))
            metrics = OptimizationMetrics().stats()
            learning = memory.stats()
            candidates = memory.recommendations(preset=preset.get(), model=model.get())[:10]
            rules_status = TrustedOutputRules().status()
            log("Local AI efficiency\n" + json.dumps(metrics, indent=2)
                + "\nVerified repair learning\n" + json.dumps(learning, indent=2)
                + "\nRepair candidates (review only)\n"
                + json.dumps(candidates, indent=2)
                + "\nCustom output filter status: " + rules_status)
            tabs.select(activity)
        except Exception as exc:
            messagebox.showerror("Efficiency information unavailable", str(exc))

    def show_user_guide():
        # No background polling, network, separate application or heavy renderer.
        path = user_guide_path()
        if path is None:
            messagebox.showerror(
                "User guide unavailable",
                "The offline user guide is missing. See docs/USER_GUIDE.md "
                "in the ForgeOS repository.")
            return
        try:
            body = path.read_text(encoding="utf-8")
            popup = tk.Toplevel(root)
            popup.title("ForgeOS | How to use")
            popup.geometry("940x710")
            ttk.Label(
                popup,
                text="ForgeOS User Guide — setup, first task, diagnostics, recovery",
                padding=(12, 10)).pack(anchor="w")
            panel = ttk.Frame(popup, padding=(10, 0, 10, 10))
            panel.pack(fill="both", expand=True)
            viewer = tk.Text(panel, wrap="word")
            scroll = ttk.Scrollbar(panel, orient="vertical", command=viewer.yview)
            viewer.configure(yscrollcommand=scroll.set)
            viewer.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
            viewer.insert("1.0", body)
            viewer.configure(state="disabled")
        except (OSError, UnicodeError, tk.TclError) as exc:
            messagebox.showerror("Cannot display user guide", str(exc))

    ttk.Button(activity, text="How to use ForgeOS",
               command=show_user_guide).pack(anchor="w", pady=(0, 8))

    ttk.Button(activity, text="AI efficiency and repair learning",
               command=show_efficiency).pack(anchor="w", pady=(0, 8))

    def show_memory():
        # No background sampling loop, subprocess or second resource service.
        try:
            from .model_routing import memory_report
            log("ForgeOS memory snapshot (own process; excludes local AI)\n"
                + json.dumps(memory_report(), indent=2))
            tabs.select(activity)
        except Exception as exc:
            messagebox.showerror("Memory snapshot unavailable", str(exc))

    ttk.Button(activity, text="Show local memory snapshot",
               command=show_memory).pack(anchor="w", pady=(0, 8))

    def run_acceptance_snapshot():
        if busy["value"] or managed["busy"]:
            messagebox.showerror(
                "Acceptance snapshot busy",
                "Finish the current task or managed-model operation first.")
            return
        physical_target = messagebox.askyesno(
            "Physical Windows target?",
            "Is this the actual physical Windows machine you are using for "
            "ForgeOS release acceptance?\n\nChoose No for CI, a VM, or a "
            "general development computer.")
        controller = managed["controller"]
        active = controller if controller is not None and controller.running() else None
        if active is not None:
            managed["busy"] = True
            managed["last_used"] = time.monotonic()
        status.set("Collecting local acceptance evidence...")

        def worker():
            try:
                from .acceptance import write_acceptance_snapshot
                result = write_acceptance_snapshot(
                    active, physical_target=physical_target)
                acceptance_events.put((result, None, active is not None))
            except Exception as exc:
                acceptance_events.put((
                    None, f"{type(exc).__name__}: {exc}", active is not None))
        threading.Thread(target=worker, daemon=True).start()

    ttk.Button(
        activity, text="Run local acceptance snapshot",
        command=run_acceptance_snapshot).pack(anchor="w", pady=(0, 8))

    def resume_blocked_tasks():
        if busy["value"] or managed["busy"]:
            messagebox.showerror(
                "Recovery busy",
                "Finish the current task or managed-model operation first.")
            return
        if not allow_host_tests.get():
            messagebox.showerror(
                "Recovery requires test permission",
                "Enable the host test execution checkbox before recovery. "
                "Recovered tasks may execute project tests.")
            return
        try:
            chosen_workspace = Path(workspaces.get()).expanduser().resolve(strict=True)
        except OSError as exc:
            messagebox.showerror("Recovery workspace unavailable", str(exc))
            return
        manager = managed["controller"]
        active_manager = manager if manager is not None and manager.running() else None
        busy["value"] = True
        if active_manager is not None:
            managed["busy"] = True
            managed["last_used"] = time.monotonic()
        status.set("Recovering safe blocked checkpoints...")
        for button in buttons.values():
            button.configure(state="disabled")

        def worker():
            worker_store = None
            try:
                from .recovery import resume_safe
                worker_store = Store(db_path)

                def managed_factory(contract):
                    if active_manager is None:
                        raise ValueError(
                            "No authenticated managed runtime is currently active")
                    instance = create_model(
                        backend="llama.cpp", model="forgeos-managed",
                        endpoint=active_manager.endpoint,
                        api_key=active_manager.api_key)
                    instance.managed_model_sha256 = active_manager.model_sha256
                    instance.managed_runtime_sha256 = active_manager.runtime_sha256
                    return instance

                report = resume_safe(
                    worker_store, chosen_workspace,
                    allow_host_tests=True,
                    managed_model_factory=(
                        managed_factory if active_manager is not None else None))
                recovery_events.put((report, None))
            except Exception as exc:
                recovery_events.put(
                    (None, f"{type(exc).__name__}: {exc}"))
            finally:
                if worker_store is not None:
                    try:
                        worker_store.close()
                    except Exception:
                        pass

        threading.Thread(target=worker, daemon=True).start()

    ttk.Button(
        activity, text="Resume safe blocked tasks",
        command=resume_blocked_tasks).pack(anchor="w", pady=(0, 8))

    def show_comparison():
        try:
            from .insights import get_comparison
            from .diagnostics import EvidenceStore
            item = get_comparison(store.get(selected()), evidence_store=EvidenceStore())
            popup = tk.Toplevel(root)
            popup.title("ForgeOS | Original vs optimized test evidence")
            popup.geometry("1200x720")
            ttk.Label(popup, text=(
                f"Task #{item['task_id']} | {item['status']} | "
                f"Local model: {item['model']} | Failure: "
                f"{item['failure_type'].get('category', 'unknown')}"
            ), padding=(10, 8)).pack(anchor="w")
            ttk.Label(popup, text=(
                "Original evidence is shown only after you request this view. "
                "It may contain sensitive project output."
            ), padding=(10, 0)).pack(anchor="w")
            split = ttk.PanedWindow(popup, orient="horizontal")
            split.pack(fill="both", expand=True, padx=10, pady=10)
            for label, content in (("AI-optimized summary", item["compact"]),
                                   ("Original test output", item["raw"])):
                panel = ttk.LabelFrame(split, text=label, padding=4)
                editor = tk.Text(panel, wrap="none", state="normal")
                scroll = ttk.Scrollbar(panel, orient="vertical", command=editor.yview)
                editor.configure(yscrollcommand=scroll.set)
                editor.pack(side="left", fill="both", expand=True)
                scroll.pack(side="right", fill="y")
                editor.insert("1.0", content)
                editor.configure(state="disabled")
                split.add(panel, weight=1)
        except Exception as exc:
            messagebox.showerror("Cannot display diagnostic comparison", str(exc))

    ttk.Button(activity, text="Compare selected task: original vs AI summary",
               command=show_comparison).pack(anchor="w", pady=(0, 8))

    def refresh():
        selection = tree.selection()
        selected_id = selection[0] if selection else None
        totals = store.counts()
        maximum = max(0, ((totals["total"] - 1) // page_size) * page_size)
        page["offset"] = min(page["offset"], maximum)
        rows = store.list(brief=True, limit=page_size, offset=page["offset"])
        tree.delete(*tree.get_children())
        for task in rows:
            tree.insert("", "end", iid=str(task["id"]), values=_format_task(task))
        if selected_id and tree.exists(selected_id):
            tree.selection_set(selected_id)
        start = page["offset"] + 1 if rows else 0
        end = page["offset"] + len(rows)
        counts.set(
            f"Showing {start}–{end} of {totals['total']} tasks  •  "
            f"{totals['passed']} passed  •  {totals['failed']} failed  •  "
            f"{totals['blocked']} blocked")
        older_button.configure(state=(
            "normal" if end < totals["total"] else "disabled"))
        newer_button.configure(state=(
            "normal" if page["offset"] > 0 else "disabled"))

    def change_page(delta):
        page["offset"] = max(0, page["offset"] + delta)
        refresh()

    def selected():
        items = tree.selection()
        if not items:
            raise ValueError("Select a task in the queue first.")
        return int(items[0])

    def open_browser_scroll():
        """One explicit Tk action; no browser/DB worker until operator confirms."""
        if busy["value"] or managed["busy"] or closing["value"]:
            messagebox.showerror(
                "Browser action busy",
                "Finish the current operation before requesting a browser action.")
            return
        try:
            task_id = selected()
            if store.get(task_id)["status"] not in ("queued", "planned"):
                raise ValueError("Choose a queued or planned task.")
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Select a task", str(exc))
            return

        from uuid import uuid4
        popup = tk.Toplevel(root)
        popup.title("ForgeOS | Browser action approval (experimental)")
        popup.geometry("590x300")
        popup.transient(root)
        popup.grab_set()
        frame = ttk.Frame(popup, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Task #{task_id} — one public-site scroll",
                  style="Section.TLabel").pack(anchor="w")
        ttk.Label(frame, text=(
            "Only use a non-sensitive public tab paired with Browsentic. "
            "Scrolling can trigger website scripts and network requests."),
            wraplength=530, justify="left").pack(anchor="w", pady=(8, 12))
        page_url_var = tk.StringVar(value="https://example.com/")
        direction_var = tk.StringVar(value="down")
        site_row = ttk.Frame(frame)
        site_row.pack(fill="x")
        ttk.Label(site_row, text="Exact public page URL").pack(side="left")
        ttk.Entry(site_row, textvariable=page_url_var, width=42).pack(
            side="left", fill="x", expand=True, padx=(8, 0))
        direction_row = ttk.Frame(frame)
        direction_row.pack(fill="x", pady=(10, 8))
        ttk.Label(direction_row, text="One scroll direction").pack(side="left")
        ttk.Combobox(direction_row, textvariable=direction_var,
                     values=("down", "up", "top", "bottom"),
                     state="readonly", width=12).pack(side="left", padx=(8, 0))
        ttk.Label(frame, text=(
            "No clicks, typing, form submissions, AI-directed actions, "
            "or automatic retries."),
            foreground="#52647b", wraplength=530).pack(anchor="w", pady=(4, 8))
        action_key = uuid4().hex
        submitted = {"value": False}

        def approve_and_run():
            if submitted["value"] or busy["value"] or managed["busy"]:
                return
            page_url = page_url_var.get().strip()
            direction = direction_var.get()
            try:
                from .browser_observation import allowed_origin
                site = allowed_origin(page_url)
                import shutil
                if shutil.which("browsentic") is None:
                    raise ValueError(
                        "Browsentic is not installed or not available on PATH.")
                from .browser_action_gate import propose_scroll
                propose_scroll(store, task_id, action_key, site, direction,
                               tab_url=page_url)
            except (ValueError, OSError) as exc:
                messagebox.showerror("Cannot propose browser scroll",
                                     str(exc), parent=popup)
                return
            except Exception:
                # Unique action keys can never be re-proposed. Do not print SQL
                # errors or expose raw browser/DB details in crew-facing UI.
                messagebox.showerror("Browser scroll unavailable",
                                     "Could not create a one-use proposal.",
                                     parent=popup)
                return
            submitted["value"] = True
            confirmed = messagebox.askyesno(
                "Approve exactly one browser action",
                f"Task #{task_id}\n\nExact page: {page_url}\n"
                f"Action: scroll {direction} ONCE\n\n"
                "I confirm the selected browser tab is PUBLIC and contains no "
                "private data. Scrolling may trigger site scripts and network "
                "requests. The action will not be automatically retried.",
                parent=popup)
            if not confirmed:
                # Remove the declined proposal rather than growing SQLite
                # indefinitely with abandoned short-lived dialog attempts.
                from .browser_action_gate import cancel_scroll
                cancel_scroll(store, task_id, action_key, site, direction,
                              tab_url=page_url)
                popup.destroy()
                status.set("Browser scroll declined; no browser action started.")
                return
            try:
                from .browser_action_gate import approve_scroll
                approve_scroll(store, task_id, action_key, site, direction,
                               human_confirmed=True, tab_url=page_url)
            except Exception:
                messagebox.showerror(
                    "Browser approval not saved",
                    "The proposal expired or could not be approved. "
                    "No browser action was started.", parent=popup)
                popup.destroy()
                return
            popup.destroy()
            busy["value"] = True
            for button in buttons.values():
                button.configure(state="disabled")
            status.set(f"Task #{task_id}: approved browser scroll running")

            def worker():
                worker_store = None
                result_state = "unverified"
                try:
                    from .browser_action_gate import (
                        run_approved_scroll, scroll_status)
                    worker_store = Store(db_path)
                    # Never pass this observation to the GUI, logs or model.
                    run_approved_scroll(worker_store, task_id, action_key,
                                        site, direction, tab_url=page_url)
                    result_state = scroll_status(worker_store, task_id, action_key)
                except Exception:
                    if worker_store is not None:
                        try:
                            from .browser_action_gate import scroll_status
                            result_state = scroll_status(
                                worker_store, task_id, action_key) or "unverified"
                        except Exception:
                            result_state = "unverified"
                finally:
                    if worker_store is not None:
                        try:
                            worker_store.close()
                        except Exception:
                            pass
                    browser_events.put((task_id, result_state))

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(frame, text="Review and approve one scroll",
                   command=approve_and_run).pack(anchor="e", pady=(10, 0))

    def add():
        try:
            task_id = store.add(project.get(), description.get("1.0", "end").strip())
            page["offset"] = 0
            refresh()
            tree.selection_set(str(task_id))
            status.set(f"Task #{task_id} queued")
            log(f"Created task #{task_id}.")
        except Exception as exc:
            messagebox.showerror("Cannot create task", str(exc))

    def request_managed_stop(controller):
        """Stop in the background; keep Tk and active task boundaries intact."""
        managed["busy"] = True
        status.set("Stopping managed local model...")
        def worker():
            try:
                controller.stop()
                managed_events.put(("stop", controller, None, None))
            except Exception as exc:
                managed_events.put((
                    "stop", controller, None, f"{type(exc).__name__}: {exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def manage_local_gguf():
        """One-window, opt-in private llama-server lifecycle controller."""
        if managed["busy"]:
            messagebox.showinfo("Local model", "A local model operation is in progress.")
            return
        popup = tk.Toplevel(root)
        popup.title("ForgeOS | Managed local GGUF (experimental)")
        popup.geometry("980x620")
        popup.minsize(820, 540)
        panel = ttk.Frame(popup, padding=15)
        panel.pack(fill="both", expand=True)
        ttk.Label(
            panel, wraplength=820,
            text="On Windows x64, optionally install the pinned official CPU "
                 "llama.cpp release below, or use a separately verified "
                 "llama-server with its trusted SHA-256. Select a GGUF that "
                 "ForgeOS has already downloaded and verified. Starting "
                 "a model does not download or execute installer scripts.").pack(anchor="w", pady=(0, 12))
        exe_path = tk.StringVar()
        expected_hash = tk.StringVar()
        gguf_path = tk.StringVar()
        memory_cap_mib = tk.StringVar()
        screening_role = tk.StringVar(value=MODEL_ROLES[0])
        screening_status = tk.StringVar(
            value="Role screening runs five synthetic prompts; no project source is sent.")
        qualification_status = tk.StringVar(
            value="Final qualification runs 20 synthetic prompts for one role "
                  "and records measured routing evidence.")
        for title, variable, selector in (
                ("llama-server binary", exe_path,
                 lambda: filedialog.askopenfilename(
                     parent=popup, title="Select llama-server executable")),
                ("Verified .gguf weights", gguf_path,
                 lambda: filedialog.askopenfilename(
                     parent=popup, title="Select verified downloaded GGUF",
                     filetypes=[("GGUF model", "*.gguf")]))):
            line = ttk.Frame(panel)
            line.pack(fill="x", pady=(0, 7))
            ttk.Label(line, text=title, width=23).pack(side="left")
            ttk.Entry(line, textvariable=variable).pack(
                side="left", fill="x", expand=True, padx=(0, 6))
            ttk.Button(line, text="Browse", command=lambda v=variable, f=selector:
                       v.set(f() or v.get())).pack(side="left")
        hash_line = ttk.Frame(panel)
        hash_line.pack(fill="x", pady=(0, 7))
        ttk.Label(hash_line, text="Trusted binary SHA-256", width=23).pack(side="left")
        ttk.Entry(hash_line, textvariable=expected_hash).pack(
            side="left", fill="x", expand=True)
        memory_line = ttk.Frame(panel)
        memory_line.pack(fill="x", pady=(0, 9))
        ttk.Label(
            memory_line, text="Max model RAM MiB", width=23).pack(side="left")
        ttk.Entry(memory_line, textvariable=memory_cap_mib, width=16).pack(
            side="left")
        ttk.Label(
            memory_line,
            text="Optional Windows cap; blank uses automatic safe ceiling."
        ).pack(side="left", padx=8)
        install_state = tk.StringVar(
            value="Official CPU runtime installation is optional.")
        def install_official_runtime():
            """User-initiated network fetch; no installation at GUI startup."""
            if busy["value"] or managed["busy"] or managed["controller"] is not None:
                messagebox.showerror(
                    "Cannot install runtime",
                    "Finish the active task or managed model operation first.",
                    parent=popup)
                return
            if not messagebox.askyesno(
                    "Install official Windows x64 CPU runtime",
                    "Download the pinned official Windows x64 CPU llama.cpp "
                    "release from GitHub and verify the ZIP, executable and "
                    "every bundled DLL? Requires internet; installs no "
                    "system service, runs no script, uses no Docker, and "
                    "does not download model weights.", parent=popup):
                return
            managed["busy"] = True
            status.set("Downloading and verifying official CPU runtime...")
            install_state.set("Downloading and checking the official pinned CPU runtime...")
            def worker():
                try:
                    from .runtime_installer import install_pinned_cpu_runtime
                    details = install_pinned_cpu_runtime()
                    managed_events.put(
                        ("install", (popup, install_state), details, None))
                except Exception as exc:
                    managed_events.put(
                        ("install", (popup, install_state), None,
                         f"{type(exc).__name__}: {exc}"))
            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(
            panel, text="Install verified official Windows CPU runtime (opt-in)",
            command=install_official_runtime).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            panel, textvariable=install_state, wraplength=820,
            foreground="#52647b").pack(anchor="w", pady=(0, 9))

        def choose_downloaded():
            """Browse already-downloaded GGUF metadata without rehashing GBs."""
            from .model_inventory import list_downloads
            try:
                records = [
                    entry for entry in list_downloads()
                    if entry["status"] == "present_not_reverified"]
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror(
                    "Cannot list local GGUF files", str(exc), parent=popup)
                return
            if not records:
                messagebox.showinfo(
                    "No downloaded models",
                    "Download a SHA-verified GGUF using Browse Hugging Face "
                    "before starting a managed local model.", parent=popup)
                return
            chooser = tk.Toplevel(popup)
            chooser.title("ForgeOS | Downloaded GGUF weights")
            chooser.geometry("795x400")
            body = ttk.Frame(chooser, padding=12)
            body.pack(fill="both", expand=True)
            ttk.Label(
                body, wraplength=760,
                text="Metadata-only inventory (no large file is rehashed "
                     "in this view). ForgeOS verifies SHA-256 again "
                     "when you start the model.").pack(anchor="w", pady=(0, 8))
            listing = ttk.Treeview(
                body, columns=("model", "size", "source"),
                show="headings", selectmode="browse")
            for key, width in (("model", 400), ("size", 110), ("source", 210)):
                listing.heading(key, text=key.title())
                listing.column(key, width=width, stretch=key == "model")
            listing.pack(fill="both", expand=True)
            for index, entry in enumerate(records):
                listing.insert("", "end", iid=str(index),
                               values=(entry["file"],
                                       f'{entry["bytes"]/(1024**3):.2f} GiB',
                                       entry["repo"]))
            def select_local():
                chosen = listing.selection()
                if chosen:
                    gguf_path.set(records[int(chosen[0])]["path"])
                    chooser.destroy()
                else:
                    messagebox.showinfo(
                        "Select local model", "Choose a downloaded GGUF file.",
                        parent=chooser)
            controls = ttk.Frame(body)
            controls.pack(fill="x", pady=(8, 0))
            use_btn = ttk.Button(controls, text="Use selected GGUF",
                                 command=select_local)
            use_btn.pack(side="right")
            retirement_events = Queue(maxsize=1)
            retirement = {"busy": False}
            retirement_status = tk.StringVar(value="")
            ttk.Label(body, textvariable=retirement_status,
                      wraplength=750).pack(anchor="w", pady=(7, 0))

            def retire_selected():
                if retirement["busy"]:
                    return
                selection = listing.selection()
                if not selection:
                    messagebox.showinfo("Select local model",
                                        "Choose one GGUF file.", parent=chooser)
                    return
                if not _retirement_allowed(
                        task_busy=busy["value"],
                        operation_busy=managed["busy"],
                        model_present=managed["controller"] is not None):
                    messagebox.showerror(
                        "Cannot retire model",
                        "Finish the current task and stop the managed local "
                        "inference server before retiring any GGUF.",
                        parent=chooser)
                    return
                index = selection[0]
                entry = records[int(index)]
                if not messagebox.askyesno(
                        "Retire verified model",
                        "Move this GGUF and its SHA-256 manifest to recoverable "
                        "local storage? The bytes are retained on disk, and "
                        "this DOES NOT free storage space. Stop any separate "
                        "llama.cpp server using this file first. The entire "
                        "GGUF will be rehashed before the move.",
                        parent=chooser):
                    return
                retirement["busy"] = True
                managed["busy"] = True
                use_btn.configure(state="disabled")
                retire_btn.configure(state="disabled")
                retirement_status.set(
                    "Verifying model bytes and writing a recoverable "
                    "retirement journal...")
                status.set("Retiring local GGUF after full SHA-256 verification...")

                def worker():
                    try:
                        from .model_retirement import retire_download
                        outcome = retire_download(entry["path"])
                        retirement_events.put((index, outcome, None))
                    except Exception as exc:
                        retirement_events.put((
                            index, None, f"{type(exc).__name__}: {exc}"))
                threading.Thread(target=worker, daemon=True).start()
                chooser.after(200, poll_retirement)

            def poll_retirement():
                try:
                    index, outcome, error = retirement_events.get_nowait()
                except Empty:
                    if chooser.winfo_exists():
                        chooser.after(200, poll_retirement)
                    return
                retirement["busy"] = False
                managed["busy"] = False
                use_btn.configure(state="normal")
                retire_btn.configure(state="normal")
                if error is not None:
                    retirement_status.set("Retirement failed; existing "
                                          "files or recovery journal retained.")
                    status.set("Model retirement failed")
                    messagebox.showerror(
                        "Could not retire local model", error, parent=chooser)
                    return
                retirement_status.set(
                    "Model retired safely; recovery ID: " + outcome["id"])
                status.set("Local GGUF retired (recovery available)")
                if gguf_path.get() == records[int(index)]["path"]:
                    gguf_path.set("")
                listing.delete(index)
                messagebox.showinfo(
                    "Model moved to recoverable local storage",
                    "Recovery ID: " + outcome["id"] + "\n"
                    "Use models-retired and model-restore-local to restore. "
                    "The model still occupies disk space.",
                    parent=chooser)

            def close_chooser():
                if retirement["busy"]:
                    retirement_status.set(
                        "Model verification is active. The window remains "
                        "open so the recovery result cannot be lost.")
                    return
                chooser.destroy()

            chooser.protocol("WM_DELETE_WINDOW", close_chooser)
            retire_btn = ttk.Button(
                controls, text="Retire selected GGUF (recoverable)",
                command=retire_selected)
            retire_btn.pack(side="right", padx=8)

        ttk.Button(
            panel, text="Choose from ForgeOS downloads",
            command=choose_downloaded).pack(anchor="w", pady=(0, 9))
        def restore_retired():
            """Recover retired weights from the same journal, in this GUI."""
            if not _retirement_allowed(
                    task_busy=busy["value"], operation_busy=managed["busy"],
                    model_present=managed["controller"] is not None):
                messagebox.showerror(
                    "Cannot restore model",
                    "Finish the active task and stop managed inference first.",
                    parent=popup)
                return
            from .model_retirement import list_retired
            try:
                records = list_retired()
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror(
                    "Cannot list retired models", str(exc), parent=popup)
                return
            if not records:
                messagebox.showinfo(
                    "No retired models",
                    "No recoverable GGUF retirement journals were found.",
                    parent=popup)
                return
            chooser = tk.Toplevel(popup)
            chooser.title("ForgeOS | Restore retired GGUF")
            chooser.geometry("795x380")
            body = ttk.Frame(chooser, padding=12)
            body.pack(fill="both", expand=True)
            ttk.Label(
                body, wraplength=740,
                text="Restore one recoverably retired GGUF and its pinned "
                     "SHA-256 manifest. Verification reads the entire model; "
                     "no network transfer or cloud inference is used."
            ).pack(anchor="w", pady=(0, 8))
            listing = ttk.Treeview(
                body, columns=("model", "status", "recovery_id"),
                show="headings", selectmode="browse")
            for name, width in (("model", 290), ("status", 210),
                                ("recovery_id", 260)):
                listing.heading(name, text=name.replace("_", " ").title())
                listing.column(name, width=width, stretch=name == "model")
            listing.pack(fill="both", expand=True)
            for index, entry in enumerate(records):
                listing.insert("", "end", iid=str(index),
                               values=(entry.get("file", "unknown"),
                                       entry["status"], entry["id"]))
            restore_events = Queue(maxsize=1)
            restore_state = {"busy": False}
            restore_status = tk.StringVar(value="")
            ttk.Label(body, textvariable=restore_status,
                      wraplength=740).pack(anchor="w", pady=(8, 0))

            def begin_restore():
                if restore_state["busy"]:
                    return
                selection = listing.selection()
                if not selection:
                    messagebox.showinfo(
                        "Select a model", "Choose a retired model first.",
                        parent=chooser)
                    return
                selected = records[int(selection[0])]
                if selected["status"] not in (
                        "retired", "interrupted_retirement_or_restore"):
                    messagebox.showerror(
                        "Cannot restore",
                        "This journal needs manual inspection.",
                        parent=chooser)
                    return
                if not _retirement_allowed(
                        task_busy=busy["value"], operation_busy=managed["busy"],
                        model_present=managed["controller"] is not None):
                    messagebox.showerror(
                        "Model in use",
                        "Finish tasks and stop the managed server first.",
                        parent=chooser)
                    return
                if not messagebox.askyesno(
                        "Restore retired GGUF",
                        "Restore the selected verified weights to their "
                        "original revisioned path? Existing files will "
                        "never be overwritten.", parent=chooser):
                    return
                restore_state["busy"] = True
                managed["busy"] = True
                restore_btn.configure(state="disabled")
                restore_status.set(
                    "Verifying pinned bytes and restoring the model...")
                status.set("Restoring and verifying retired GGUF...")
                identifier = selected["id"]

                def worker():
                    try:
                        from .model_retirement import restore_download
                        result = restore_download(identifier)
                        restore_events.put((selection[0], result, None))
                    except Exception as exc:
                        restore_events.put((
                            selection[0], None,
                            f"{type(exc).__name__}: {exc}"))
                threading.Thread(target=worker, daemon=True).start()
                chooser.after(200, poll_restore)

            def poll_restore():
                try:
                    index, result, error = restore_events.get_nowait()
                except Empty:
                    if chooser.winfo_exists():
                        chooser.after(200, poll_restore)
                    return
                restore_state["busy"] = False
                managed["busy"] = False
                restore_btn.configure(state="normal")
                if error:
                    restore_status.set(
                        "Restore failed; original recovery journal retained.")
                    status.set("Local GGUF restore failed")
                    messagebox.showerror(
                        "Could not restore local GGUF", error, parent=chooser)
                    return
                listing.delete(index)
                restore_status.set(
                    "Restored GGUF to " + result["path"])
                status.set("Local GGUF restored and SHA-256 verified")
                messagebox.showinfo(
                    "Local model restored",
                    "Verified weight restored. Choose it from ForgeOS "
                    "downloads to run it.", parent=chooser)

            def close_restoration():
                if restore_state["busy"]:
                    restore_status.set(
                        "Restore is active. Keep this dialog open until "
                        "the recovery operation finishes.")
                    return
                chooser.destroy()
            chooser.protocol("WM_DELETE_WINDOW", close_restoration)
            restore_btn = ttk.Button(
                body, text="Restore selected GGUF",
                command=begin_restore)
            restore_btn.pack(anchor="e", pady=(8, 0))

        ttk.Button(
            panel, text="Restore a retired GGUF (recoverable)",
            command=restore_retired).pack(anchor="w", pady=(0, 8))


        ttk.Label(
            panel, wraplength=820,
            text="Do not use checksums taken solely from the same untrusted file. "
                 "A binary checksum is not a publisher signature, and adjacent DLL "
                 "dependencies remain outside this experimental verifier."
        ).pack(anchor="w", pady=(0, 12))
        buttons_row = ttk.Frame(panel)
        buttons_row.pack(fill="x")

        def do_start(*, installed=False):
            if busy["value"] or managed["busy"] or managed["controller"] is not None:
                messagebox.showerror(
                    "Cannot start model", "Complete or stop the previous task/model.",
                    parent=popup)
                return
            values = (exe_path.get().strip(), expected_hash.get().strip(),
                      gguf_path.get().strip())
            memory_text = memory_cap_mib.get().strip()
            memory_limit = None
            if memory_text:
                if not memory_text.isdecimal():
                    raise ValueError("Max model RAM must be whole MiB or blank")
                memory_mib = int(memory_text)
                if memory_mib < 512:
                    raise ValueError("Max model RAM must be at least 512 MiB")
                memory_limit = memory_mib * 1024 * 1024
            if not values[2] or (not installed and not all(values)):
                messagebox.showerror(
                    "Missing model information",
                    ("Choose a verified GGUF file." if installed else
                     "Choose the executable, trusted SHA-256 and verified GGUF."),
                    parent=popup)
                return
            managed["busy"] = True
            status.set("Verifying and starting local llama.cpp...")
            # Rehashing a large model or full binary bundle and starting the
            # server always run outside the Tk event thread.
            def worker():
                from .managed_llama import ManagedLlamaServer
                controller = ManagedLlamaServer()
                try:
                    if installed:
                        outcome = controller.start_installed_cpu(
                            model_path=values[2],
                            memory_limit_bytes=memory_limit)
                    else:
                        outcome = controller.start(
                            executable=values[0],
                            executable_sha256=values[1], model_path=values[2],
                            memory_limit_bytes=memory_limit)
                    managed_events.put(("start", controller, outcome, None))
                except Exception as exc:
                    try:
                        controller.stop()
                    except Exception as cleanup_error:
                        exc = RuntimeError(
                            f"{exc}; cleanup failed: {cleanup_error}")
                    managed_events.put(
                        ("start", None, None, f"{type(exc).__name__}: {exc}"))
            threading.Thread(target=worker, daemon=True).start()
            popup.destroy()

        def show_model_memory():
            controller = managed["controller"]
            if controller is None or not controller.running():
                messagebox.showinfo(
                    "Managed model memory", "No managed local model is running.",
                    parent=popup)
                return
            try:
                report = controller.memory_report()
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror(
                    "Managed model memory", str(exc), parent=popup)
                return
            if report["scope"] != "windows_job_committed_memory":
                messagebox.showinfo(
                    "Managed model memory",
                    "Process-tree committed-memory accounting is currently "
                    "available for the managed Windows runtime only.",
                    parent=popup)
                return
            mib = 1024 * 1024
            limit = report["job_memory_limit_bytes"]
            messagebox.showinfo(
                "Managed model memory",
                "One-shot Windows Job Object accounting\n\n"
                f"Process: {report['pid']}\n"
                f"Peak job committed memory: "
                f"{report['peak_job_memory_used_bytes']/mib:,.1f} MiB\n"
                f"Peak single-process committed memory: "
                f"{report['peak_process_memory_used_bytes']/mib:,.1f} MiB\n"
                f"Job memory ceiling: "
                f"{limit/mib:,.1f} MiB"
                if limit is not None else "Job memory ceiling: unavailable",
                parent=popup)

        def test_local_model():
            if busy["value"] or managed["busy"]:
                messagebox.showerror(
                    "Model in use",
                    "Complete the current task/model operation before testing.",
                    parent=popup)
                return
            controller = managed["controller"]
            if controller is None or not controller.running():
                messagebox.showinfo(
                    "Test local model",
                    "Start a verified managed local model first.", parent=popup)
                return
            managed["busy"] = True
            managed["last_used"] = time.monotonic()
            status.set("Testing managed local model structured output...")
            def worker():
                try:
                    outcome = controller.inference_self_test()
                    managed_events.put(("smoke", controller, outcome, None))
                except Exception as exc:
                    managed_events.put((
                        "smoke", controller, None,
                        f"{type(exc).__name__}: {exc}"))
            threading.Thread(target=worker, daemon=True).start()

        def save_benchmark_baseline():
            if busy["value"] or managed["busy"]:
                messagebox.showerror(
                    "Model in use",
                    "Complete the current task/model operation before recording.",
                    parent=popup)
                return
            controller = managed["controller"]
            if controller is None or not controller.running():
                messagebox.showinfo(
                    "Benchmark baseline",
                    "Start a verified managed local model first.", parent=popup)
                return
            managed["busy"] = True
            managed["last_used"] = time.monotonic()
            status.set("Recording managed-model benchmark baseline...")
            def worker():
                try:
                    from .model_benchmark import write_baseline
                    outcome = write_baseline(controller)
                    managed_events.put(("baseline", controller, outcome, None))
                except Exception as exc:
                    managed_events.put((
                        "baseline", controller, None,
                        f"{type(exc).__name__}: {exc}"))
            threading.Thread(target=worker, daemon=True).start()

        def run_role_screening():
            if busy["value"] or managed["busy"]:
                messagebox.showerror(
                    "Model in use",
                    "Complete the current task/model operation before screening.",
                    parent=popup)
                return
            controller = managed["controller"]
            if controller is None or not controller.running():
                messagebox.showinfo(
                    "Role screening",
                    "Start a verified managed local model first.", parent=popup)
                return
            role = screening_role.get()
            if role not in MODEL_ROLES:
                messagebox.showerror(
                    "Role screening", "Choose a valid screening role.",
                    parent=popup)
                return
            cancel_event = threading.Event()
            managed["screening_cancel"] = cancel_event
            managed["busy"] = True
            managed["last_used"] = time.monotonic()
            screening_status.set(
                "Running 0/5 for " + role.replace("_", " ") + "...")
            status.set("Screening managed model role: " + role)
            def worker():
                try:
                    from .model_benchmark import write_screening
                    def report_progress(done, total, result):
                        managed_events.put((
                            "screening-progress", controller,
                            {"done": done, "total": total,
                             "task_id": result["task_id"], "role": role},
                            None))
                    outcome = write_screening(
                        controller, role=role, cancel_event=cancel_event,
                        progress_callback=report_progress)
                    managed_events.put(
                        ("screening", controller, outcome, None))
                except Exception as exc:
                    managed_events.put((
                        "screening", controller, None,
                        f"{type(exc).__name__}: {exc}"))
            threading.Thread(target=worker, daemon=True).start()

        def cancel_role_screening():
            cancel_event = managed["screening_cancel"]
            if cancel_event is None:
                messagebox.showinfo(
                    "Role screening", "No role screening is active.",
                    parent=popup)
                return
            cancel_event.set()
            screening_status.set(
                "Cancellation requested. The current model response may "
                "finish before partial evidence is saved.")
            status.set("Role screening cancellation requested")

        def run_role_qualification():
            if busy["value"] or managed["busy"]:
                messagebox.showerror(
                    "Model in use",
                    "Complete the current task/model operation before qualification.",
                    parent=popup)
                return
            controller = managed["controller"]
            if controller is None or not controller.running():
                messagebox.showinfo(
                    "Final qualification",
                    "Start a verified managed local model first.", parent=popup)
                return
            role = screening_role.get()
            if role not in SCREENING_ROLES:
                messagebox.showerror(
                    "Final qualification", "Choose a valid role.", parent=popup)
                return
            cancel_event = threading.Event()
            managed["qualification_cancel"] = cancel_event
            managed["busy"] = True
            managed["last_used"] = time.monotonic()
            qualification_status.set(
                "Running 0/20 final qualification tasks for "
                + role.replace("_", " ") + "...")
            status.set("Final model qualification: " + role)
            def worker():
                try:
                    from .model_benchmark import write_qualification
                    def report_progress(done, total, result):
                        managed_events.put((
                            "qualification-progress", controller,
                            {"done": done, "total": total,
                             "task_id": result["task_id"], "role": role},
                            None))
                    outcome = write_qualification(
                        controller, role=role, cancel_event=cancel_event,
                        progress_callback=report_progress)
                    managed_events.put(("qualification", controller, outcome, None))
                except Exception as exc:
                    managed_events.put((
                        "qualification", controller, None,
                        f"{type(exc).__name__}: {exc}"))
            threading.Thread(target=worker, daemon=True).start()

        def cancel_role_qualification():
            cancel_event = managed["qualification_cancel"]
            if cancel_event is None:
                messagebox.showinfo(
                    "Final qualification", "No final qualification is active.",
                    parent=popup)
                return
            cancel_event.set()
            qualification_status.set(
                "Cancellation requested. The active response may finish "
                "before partial non-routeable evidence is saved.")
            status.set("Final qualification cancellation requested")

        def close_managed_dialog():
            cancel_event = managed["screening_cancel"]
            qualification_event = managed["qualification_cancel"]
            if cancel_event is not None:
                cancel_event.set()
                screening_status.set(
                    "Cancellation requested. Keep this window open until "
                    "partial screening evidence is saved.")
                return
            if qualification_event is not None:
                qualification_event.set()
                qualification_status.set(
                    "Cancellation requested. Keep this window open until "
                    "partial qualification evidence is saved.")
                return
            popup.destroy()

        def do_stop():
            if busy["value"] or managed["busy"]:
                messagebox.showerror(
                    "Model in use", "Complete the current task before stopping "
                    "the managed server.", parent=popup)
                return
            controller = managed["controller"]
            if controller is None:
                messagebox.showinfo("Local model", "No managed model is active.",
                                    parent=popup)
                return
            request_managed_stop(controller)
            popup.destroy()

        ttk.Button(
            buttons_row, text="Start installed CPU runtime",
            command=lambda: do_start(installed=True)).pack(side="left")
        ttk.Button(
            buttons_row, text="Start manual runtime",
            command=lambda: do_start(installed=False)).pack(side="left", padx=8)
        ttk.Button(
            buttons_row, text="Test local model",
            command=test_local_model).pack(side="left", padx=8)
        ttk.Button(
            buttons_row, text="Save benchmark baseline",
            command=save_benchmark_baseline).pack(side="left", padx=8)
        ttk.Button(
            buttons_row, text="Show model memory",
            command=show_model_memory).pack(side="left", padx=8)
        ttk.Button(buttons_row, text="Stop local model", command=do_stop).pack(
            side="left", padx=8)
        ttk.Label(
            buttons_row,
            text=("A model is running" if managed["controller"] is not None
                  and managed["controller"].running() else "No managed model active")
        ).pack(side="left", padx=12)

        screening_row = ttk.Frame(panel)
        screening_row.pack(fill="x", pady=(12, 0))
        ttk.Label(screening_row, text="Screening role").pack(side="left")
        ttk.Combobox(
            screening_row, textvariable=screening_role,
            values=MODEL_ROLES, state="readonly", width=23
        ).pack(side="left", padx=(7, 8))
        ttk.Button(
            screening_row, text="Run 5-task role screening",
            command=run_role_screening).pack(side="left")
        ttk.Button(
            screening_row, text="Cancel screening",
            command=cancel_role_screening).pack(side="left", padx=8)
        ttk.Label(
            panel, textvariable=screening_status, wraplength=930,
            foreground="#52647b").pack(anchor="w", pady=(6, 0))
        qualification_row = ttk.Frame(panel)
        qualification_row.pack(fill="x", pady=(10, 0))
        ttk.Button(
            qualification_row, text="Run 20-task final qualification",
            command=run_role_qualification).pack(side="left")
        ttk.Button(
            qualification_row, text="Cancel qualification",
            command=cancel_role_qualification).pack(side="left", padx=8)
        ttk.Label(
            panel, textvariable=qualification_status, wraplength=930,
            foreground="#52647b").pack(anchor="w", pady=(6, 0))
        popup.protocol("WM_DELETE_WINDOW", close_managed_dialog)

    def browse_hub_models():
        """Opt-in GGUF browser and verified downloader without Tk thread blocking."""
        from .huggingface_catalog import search_gguf, list_gguf_files

        popup = tk.Toplevel(root)
        popup.title("ForgeOS | Hugging Face GGUF catalog and downloads")
        popup.geometry("790x590")
        popup.minsize(610, 390)
        frame = ttk.Frame(popup, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="Browse public GGUF models and download verified weights. "
                        "Running downloaded models requires a local inference runtime."
        ).pack(anchor="w", pady=(0, 8))
        query = tk.StringVar(value="Qwen coder GGUF")
        search_row = ttk.Frame(frame)
        search_row.pack(fill="x", pady=(0, 8))
        ttk.Entry(search_row, textvariable=query).pack(
            side="left", fill="x", expand=True)
        results = ttk.Treeview(
            frame, columns=("repository", "downloads", "license"),
            show="headings", height=11, selectmode="browse")
        for key, width in (("repository", 430), ("downloads", 100), ("license", 150)):
            results.heading(key, text=key.title())
            results.column(key, width=width, stretch=key == "repository")
        results.pack(fill="both", expand=True)
        choose_row = ttk.Frame(frame)
        choose_row.pack(fill="x", pady=(8, 0))
        ttk.Label(choose_row, text="GGUF file").pack(side="left")
        file_choice = tk.StringVar()
        file_picker = ttk.Combobox(
            choose_row, textvariable=file_choice, values=(),
            state="readonly", width=60)
        file_picker.pack(side="left", fill="x", expand=True, padx=6)
        info = tk.Text(frame, height=7, wrap="word", state="disabled")
        info.pack(fill="x", pady=(8, 0))
        hub_events = Queue(maxsize=2)
        progress_events = Queue(maxsize=1)
        transfer_status = tk.StringVar(value="")
        ttk.Label(frame, textvariable=transfer_status, wraplength=740).pack(
            anchor="w", pady=(6, 0))
        state = {"busy": False, "catalog": [], "inspected_repo": None,
                 "action": None, "cancel": None, "close_after": False}

        def report_transfer(stage, current, total):
            # Worker threads never touch Tk. Drop stale progress events
            # instead of keeping a memory-growing GUI message backlog.
            try:
                progress_events.put_nowait((stage, current, total))
            except Full:
                try:
                    progress_events.get_nowait()
                except Empty:
                    pass
                try:
                    progress_events.put_nowait((stage, current, total))
                except Full:
                    pass

        def request_pause():
            if state["busy"] and state["action"] == "download":
                state["cancel"].set()
                pause_btn.configure(state="disabled")
                transfer_status.set(
                    "Pause requested; current network read may finish "
                    "before partial data is safely saved.")

        def close_hub():
            if state["busy"] and state["action"] == "download":
                state["close_after"] = True
                request_pause()
                return
            popup.destroy()
        popup.protocol("WM_DELETE_WINDOW", close_hub)

        def begin(action):
            if state["busy"]:
                return
            if action == "files":
                selected = results.selection()
                if not selected:
                    messagebox.showinfo("Select model", "Select a repository first.",
                                        parent=popup)
                    return
                term = state["catalog"][int(selected[0])]["repo"]
            elif action == "download":
                if not state["inspected_repo"] or not file_choice.get():
                    messagebox.showinfo(
                        "Choose a GGUF file",
                        "Inspect a repository, then select a file with SHA-256 metadata.",
                        parent=popup)
                    return
                if not messagebox.askyesno(
                        "Download GGUF to this computer",
                        "Download the selected file to the ForgeOS model folder? "
                        "Check its license and disk requirements first. "
                        "The download will not load or execute the model.",
                        parent=popup):
                    return
                term = (state["inspected_repo"], file_choice.get())
            else:
                term = query.get().strip()
            state["busy"] = True
            state["action"] = action
            state["cancel"] = threading.Event() if action == "download" else None
            transfer_status.set("")
            while not progress_events.empty():
                try:
                    progress_events.get_nowait()
                except Empty:
                    break
            pause_btn.configure(state="normal" if action == "download" else "disabled")
            search_btn.configure(state="disabled")
            files_btn.configure(state="disabled")
            download_btn.configure(state="disabled")
            info.configure(state="normal")
            info.delete("1.0", "end")
            info.insert("end",
                        "Downloading and verifying GGUF weights... "
                        "Use Pause download to retain resumable partial bytes."
                        if action == "download" else "Looking up public model metadata...")
            info.configure(state="disabled")

            def run_lookup():
                try:
                    if action == "search":
                        data = search_gguf(term)
                    elif action == "files":
                        data = list_gguf_files(term)
                    else:
                        from .gguf_download import download_gguf
                        data = download_gguf(
                            term[0], term[1], cancel_event=state["cancel"],
                            progress_callback=report_transfer)
                    hub_events.put((action, data, None))
                except Exception as exc:
                    hub_events.put((action, None, f"{type(exc).__name__}: {exc}"))
            threading.Thread(target=run_lookup, daemon=True).start()

        search_btn = ttk.Button(
            search_row, text="Search", command=lambda: begin("search"))
        search_btn.pack(side="left", padx=5)
        files_btn = ttk.Button(
            search_row, text="Inspect GGUF sizes", command=lambda: begin("files"))
        files_btn.pack(side="left")
        download_btn = ttk.Button(
            choose_row, text="Download and verify", state="disabled",
            command=lambda: begin("download"))
        download_btn.pack(side="left", padx=5)
        pause_btn = ttk.Button(
            choose_row, text="Pause download", state="disabled",
            command=request_pause)
        pause_btn.pack(side="left", padx=5)

        def poll_hub():
            latest = None
            while True:
                try:
                    latest = progress_events.get_nowait()
                except Empty:
                    break
            if latest is not None and state["busy"] and state["action"] == "download":
                stage, current, total = latest
                percent = current * 100 // total if total else 0
                transfer_status.set(
                    f"{stage.capitalize()} GGUF: {current / (1024**2):.1f} / "
                    f"{total / (1024**2):.1f} MiB ({percent}%)")
            try:
                action, data, error = hub_events.get_nowait()
            except Empty:
                pass
            else:
                state["busy"] = False
                state["action"] = None
                state["cancel"] = None
                pause_btn.configure(state="disabled")
                if state["close_after"]:
                    popup.destroy()
                    return
                search_btn.configure(state="normal")
                files_btn.configure(state="normal")
                download_btn.configure(
                    state="normal" if state["inspected_repo"]
                    and file_choice.get() else "disabled")
                info.configure(state="normal")
                info.delete("1.0", "end")
                if error:
                    info.insert("end", error)
                elif action == "search":
                    state["catalog"] = data
                    state["inspected_repo"] = None
                    file_choice.set("")
                    file_picker.configure(values=())
                    download_btn.configure(state="disabled")
                    results.delete(*results.get_children())
                    for index, item in enumerate(data):
                        results.insert("", "end", iid=str(index), values=(
                            item["repo"], item["downloads"], item["license_tag"]))
                    info.insert(
                        "end", f"Found {len(data)} candidate repositories. "
                        "A repository listing is not a license or compatibility "
                        "verification. Select one and inspect its GGUF files.")
                elif action == "files":
                    selected = results.selection()
                    state["inspected_repo"] = (
                        state["catalog"][int(selected[0])]["repo"]
                        if selected else None)
                    choices = [item["path"] for item in data if item["sha256"]]
                    file_picker.configure(values=choices)
                    file_choice.set(choices[0] if choices else "")
                    download_btn.configure(state="normal" if choices else "disabled")
                    for item in data[:30]:
                        size = item["bytes"] / (1024 ** 3)
                        verified = "LFS SHA-256 provided" if item["sha256"] else "Hash unavailable"
                        info.insert("end", f'{item["path"]}: {size:.2f} GiB; {verified}\n')
                    if not data:
                        info.insert("end", "No GGUF files found in the returned listing.")
                    elif not choices:
                        info.insert("end",
                                    "No downloadable files with trusted checksum metadata.")
                else:
                    info.insert(
                        "end", f"Model file {data['status']}: {data['path']}\n"
                               "Weight SHA-256 checked. A separate local inference "
                               "runtime is required to run this GGUF file.")
                info.configure(state="disabled")
            if popup.winfo_exists():
                popup.after(200, poll_hub)
        popup.after(200, poll_hub)

    def run_readiness_check(show_dialog=True):
        """Show actionable prerequisites without blocking the Tk event loop."""
        snapshot = dict(project=project.get().strip(),
                        workspace_parent=workspaces.get().strip(),
                        backend=backend.get(), model=model.get().strip(),
                        endpoint=endpoint.get().strip())
        status.set("Checking local prerequisites...")

        def worker():
            try:
                from .readiness import check_readiness
                checks = check_readiness(**snapshot)
                message = "\n".join(
                    ("READY: " if check.ok else "NEEDS ATTENTION: ") +
                    check.name + " — " + check.message for check in checks)
                ok = all(check.ok for check in checks)
            except Exception as exc:
                message, ok = f"Readiness check failed: {exc}", False

            def finish():
                if closing["value"]:
                    return
                status.set("Ready" if ok else "Setup needs attention")
                if show_dialog:
                    (messagebox.showinfo if ok else messagebox.showwarning)(
                        "ForgeOS readiness", message)
            root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    ttk.Button(provider_row, text="Check readiness",
               command=run_readiness_check).pack(side="left", padx=6)

    def start(operation):
        if busy["value"]:
            return
        try:
            task_id = selected()
            chosen_model = model.get().strip()
            chosen_backend = backend.get()
            chosen_endpoint = endpoint.get().strip()
            manager = managed["controller"]
            if managed["busy"]:
                raise ValueError("Managed model is starting or stopping")
            managed_key = None
            if manager is not None and chosen_endpoint == manager.endpoint:
                if (not manager.running() or chosen_backend != "llama.cpp"
                        or chosen_model not in (manager.model_name, "qualified-auto")):
                    raise ValueError("Active managed model settings do not match")
                managed_key = manager.api_key
                managed["last_used"] = time.monotonic()
            elif chosen_model == "forgeos-managed":
                raise ValueError("Start the verified managed GGUF runtime before "
                                 "using the forgeos-managed model")
            chosen_preset = preset.get()
            chosen_workspace = Path(workspaces.get()).expanduser()
            chosen_retries = repair_count.get()
            chosen_role = task_role.get()
            if chosen_role not in MODEL_ROLES:
                raise ValueError("Choose a valid task role")
            chosen_host_permission = allow_host_tests.get()
            if operation in {"run", "test"} and not chosen_host_permission:
                raise ValueError("Enable the host test execution checkbox before starting.")
            if operation == "run" and not 0 <= chosen_retries <= 5:
                raise ValueError("Repair retries must be between 0 and 5.")
            if operation == "run":
                chosen_workspace.mkdir(parents=True, exist_ok=True)
        except (ValueError, tk.TclError, OSError) as exc:
            messagebox.showerror("Cannot start", str(exc))
            return
        busy["value"] = True
        monitor.update(task_id=task_id, operation=operation,
                       started=time.monotonic(), last_stage=None, last_check=0.0)
        monitor_text.set(f"Task #{task_id} • {operation} • starting • 0s elapsed")
        status.set(f"Running {operation} for task #{task_id}")
        for button in buttons.values():
            button.configure(state="disabled")
        def worker():
            worker_store = None
            value = "RuntimeError: worker ended without a result"
            def selected_model():
                instance = create_model(
                    backend=chosen_backend, model=chosen_model,
                    endpoint=chosen_endpoint, api_key=managed_key)
                if managed_key is not None:
                    # Store both verified immutable content identities in
                    # the engine checkpoint; NEVER persist the API secret.
                    instance.managed_model_sha256 = manager.model_sha256
                    instance.managed_runtime_sha256 = manager.runtime_sha256
                return instance
            try:
                worker_store = Store(db_path)
                if operation == "plan":
                    value = plan_task(
                        worker_store, task_id,
                        model_factory=selected_model)
                elif operation == "run":
                    result = execute_task(
                        worker_store, task_id, chosen_workspace,
                        preset=chosen_preset, model=selected_model(),
                        max_repairs=chosen_retries,
                        allow_host_tests=chosen_host_permission,
                        role=chosen_role,
                    )
                    value = json.dumps(result, indent=2)
                else:
                    result = test_task(
                        worker_store, task_id, chosen_preset,
                        allow_host_tests=chosen_host_permission)
                    value = json.dumps(result, indent=2)
            except Exception as exc:
                # All operations own their state and lease. GUI errors must
                # never overwrite a task taken over by another process.
                value = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    if worker_store is not None:
                        worker_store.close()
                except Exception as close_exc:
                    value += (
                        "\nStore close failed: "
                        f"{type(close_exc).__name__}: {close_exc}")
                finally:
                    # Always notify the Tk thread, including Store/open/close
                    # failures, so busy state and buttons cannot remain locked.
                    events.put((task_id, operation, value))
        threading.Thread(target=worker, daemon=True).start()

    add_button("add", "Add task", add)
    add_button("run", "Build / test / repair", lambda: start("run"))
    add_button("plan", "Generate plan", lambda: start("plan"))
    add_button("test", "Run tests only", lambda: start("test"))
    add_button("refresh", "Refresh", refresh)
    browser_button = ttk.Button(
        browser_row, text="Browser scroll (experimental)",
        command=open_browser_scroll)
    browser_button.pack(side="left")
    buttons["browser"] = browser_button

    def poll():
        try:
            # The existing Tk poll loop updates elapsed time continuously.
            # Read SQLite at most once per second, not every 150 ms.
            if monitor["task_id"] is not None and monitor["started"] is not None:
                now = time.monotonic()
                elapsed = int(now - monitor["started"])
                if now - monitor["last_check"] >= 1.0:
                    monitor["last_check"] = now
                    try:
                        live = store.get(monitor["task_id"])
                        stage = live["status"]
                        if stage != monitor["last_stage"]:
                            monitor["last_stage"] = stage
                            log(f"Task #{monitor['task_id']} entered {stage} "
                                f"({elapsed}s elapsed)")
                    except Exception as exc:
                        stage = "status unavailable"
                        if monitor["last_stage"] != stage:
                            monitor["last_stage"] = stage
                            log(f"Activity monitor could not read task status: {exc}")
                stage = monitor["last_stage"] or "starting"
                details = {
                    "planning": "Requesting an AI plan",
                    "editing": "Preparing workspace or waiting for AI edits",
                    "testing": "Running project tests",
                    "blocked": "Stopped; inspect diagnostic output",
                    "passed": "Tests passed",
                    "failed": "Tests failed",
                }.get(stage, "Working")
                monitor_text.set(
                    f"Task #{monitor['task_id']} • {monitor['operation']} • "
                    f"{stage} • {elapsed}s elapsed — {details}")
            try:
                while True:
                    task_id, operation, value = events.get_nowait()
                    busy["value"] = False
                    elapsed = int(time.monotonic() - monitor["started"]) if (
                        monitor["task_id"] == task_id and monitor["started"] is not None) else 0
                    monitor_text.set(
                        f"Task #{task_id} • {operation} • "
                        f"{store.get(task_id)['status']} • {elapsed}s elapsed. "
                        "See diagnostic output below.")
                    monitor.update(task_id=None, started=None, last_stage=None)
                    for button in buttons.values():
                        button.configure(state="normal")
                    refresh()
                    status.set(f"Task #{task_id}: {store.get(task_id)['status']}")
                    log(f"Task #{task_id} / {operation}\n{value}")
                    tabs.select(activity)
            except Empty:
                pass
            try:
                browser_task_id, browser_state = browser_events.get_nowait()
                busy["value"] = False
                for button in buttons.values():
                    button.configure(state="normal")
                refresh()
                if browser_state == "succeeded":
                    status.set(f"Task #{browser_task_id}: one browser scroll completed")
                    log(f"Task #{browser_task_id}: approved browser scroll succeeded. "
                        "No page text or URLs were retained in the GUI log.")
                else:
                    status.set(f"Task #{browser_task_id}: browser outcome unverified")
                    log(f"Task #{browser_task_id}: browser action state {browser_state}. "
                        "Inspect the browser manually before proposing a NEW "
                        "one-use action. ForgeOS did not automatically retry.")
                tabs.select(activity)
            except Empty:
                pass
            try:
                acceptance_result, acceptance_error, used_managed = (
                    acceptance_events.get_nowait())
                if used_managed:
                    managed["busy"] = False
                    managed["last_used"] = time.monotonic()
                if acceptance_error is not None:
                    status.set("Acceptance snapshot failed")
                    log("Acceptance snapshot failed\n" + acceptance_error)
                else:
                    record = acceptance_result["record"]
                    status.set("Local acceptance snapshot saved")
                    log(
                        "Local acceptance snapshot: " + acceptance_result["path"]
                        + "\nWindows host evidence: "
                        + str(record["windows_host_evidence"])
                        + "\nPhysical target asserted: "
                        + str(record["physical_windows_target_asserted"])
                        + "\nSource commit: "
                        + str(record["source_commit"])
                        + "\nEmbedded build source commit: "
                        + str(record["artifact"]["build_source_commit"])
                        + "\nFrozen EXE SHA-256: "
                        + str(record["artifact"]["executable_sha256"])
                        + "\nManaged model evidence: "
                        + str(record["managed_model"] is not None))
                    tabs.select(activity)
            except Empty:
                pass
            try:
                report, recovery_error = recovery_events.get_nowait()
                busy["value"] = False
                managed["busy"] = False
                for button in buttons.values():
                    button.configure(state="normal")
                refresh()
                if recovery_error is not None:
                    status.set("Safe recovery failed")
                    log("Safe recovery failed\n" + recovery_error)
                else:
                    status.set("Safe recovery completed")
                    log("Safe blocked-task recovery\n"
                        + json.dumps(report, indent=2))
                tabs.select(activity)
            except Empty:
                pass
            try:
                while True:
                    action, controller, outcome, error = managed_events.get_nowait()
                    if action == "screening-progress":
                        status.set(
                            "Screening " + outcome["role"] + ": "
                            + str(outcome["done"]) + "/"
                            + str(outcome["total"]) + " complete")
                        continue
                    if action == "qualification-progress":
                        status.set(
                            "Final qualification " + outcome["role"] + ": "
                            + str(outcome["done"]) + "/"
                            + str(outcome["total"]) + " complete")
                        continue
                    managed["busy"] = False
                    if action == "screening":
                        managed["screening_cancel"] = None
                    if action == "qualification":
                        managed["qualification_cancel"] = None
                    if error:
                        if action == "stop":
                            managed["stop_failed"] = True
                        if action == "screening":
                            managed["screening_cancel"] = None
                        if action == "qualification":
                            managed["qualification_cancel"] = None
                        if action == "install" and controller[0].winfo_exists():
                            controller[1].set("CPU runtime installation failed: " + error)
                        status.set("Managed llama.cpp operation failed")
                        log("Managed local model: " + error)
                        tabs.select(activity)
                    elif action == "install":
                        if controller[0].winfo_exists():
                            controller[1].set(
                                "Verified official CPU runtime installed. "
                                "Choose a GGUF and click Start installed CPU runtime.")
                        status.set("Verified official CPU llama.cpp runtime installed")
                        log("Pinned CPU runtime installed: release " + outcome["tag"]
                            + ", ZIP SHA-256 " + outcome["asset_sha256"]
                            + ". Select a GGUF and Start installed CPU runtime.")
                    elif action == "screening":
                        managed["last_used"] = time.monotonic()
                        record = outcome["record"]
                        role = record["role_scope"]
                        role_result = record["summary"]["roles"][role]
                        if record["cancelled"]:
                            status.set(
                                "Role screening cancelled; partial evidence saved")
                        else:
                            status.set(
                                "Role screening complete: "
                                + role.replace("_", " "))
                        log(
                            "Role screening evidence: " + outcome["path"]
                            + "\nRole: " + role
                            + "\nContract successes: "
                            + str(role_result["json_successes"]) + "/5"
                            + "\nObjective successes: "
                            + str(role_result["objective_successes"]) + "/5"
                            + "\nScreening pass: "
                            + str(role_result["screening_pass"])
                            + ("\nCancelled: partial results retained."
                               if record["cancelled"] else "")
                            + "\nScreening is not final model qualification.")
                        tabs.select(activity)
                    elif action == "qualification":
                        managed["last_used"] = time.monotonic()
                        record = outcome["record"]
                        if record["cancelled"]:
                            status.set("Final qualification cancelled; partial evidence saved")
                        else:
                            status.set("Final qualification complete: " + record["role"].replace("_", " "))
                        log(
                            "Final qualification evidence: " + outcome["path"]
                            + "\nRole: " + record["role"]
                            + "\nStructured successes: " + str(record["json_successes"]) + "/" + str(record["tasks"])
                            + "\nObjective successes: " + str(record["objective_successes"]) + "/" + str(record["tasks"])
                            + "\nRuntime failures: " + str(record["runtime_failures"])
                            + "\nSafety violations: " + str(record["safety_violations"])
                            + "\nQualified: " + str(record.get("qualified", False))
                            + ("\nCancelled: partial evidence retained; routing remains disabled." if record["cancelled"] else ""))
                        tabs.select(activity)
                    elif action == "baseline":
                        managed["last_used"] = time.monotonic()
                        status.set("Managed-model benchmark baseline saved")
                        log("Benchmark baseline saved: " + outcome["path"]
                            + "\nBaseline evidence is not model qualification "
                              "and cannot enable routing.")
                        tabs.select(activity)
                    elif action == "smoke":
                        managed["last_used"] = time.monotonic()
                        status.set("Managed local model self-test passed")
                        memory = outcome.get("memory")
                        detail = (
                            "Managed local model self-test passed in "
                            + str(outcome["elapsed_seconds"]) + " seconds; "
                            + str(outcome["response_bytes"]) + " response bytes.")
                        if (isinstance(memory, dict)
                                and memory.get("peak_job_memory_used_bytes")
                                is not None):
                            detail += (
                                " Peak Windows job committed memory: "
                                + f'{memory["peak_job_memory_used_bytes"]/(1024**2):.1f}'
                                + " MiB.")
                        log(detail)
                        tabs.select(activity)
                    elif action == "start":
                        managed["controller"] = controller
                        managed["last_used"] = time.monotonic()
                        managed["unexpected_exit"] = False
                        managed["stop_failed"] = False
                        backend.set("llama.cpp")
                        model.set(outcome["model"])
                        endpoint.set(outcome["endpoint"])
                        status.set("Verified local llama.cpp model running")
                        log("Managed model ready: localhost endpoint " +
                            outcome["endpoint"] + ", model SHA-256 " +
                            outcome["sha256"] + "; process " + str(outcome["pid"]))
                    else:
                        was_crash = managed["unexpected_exit"]
                        if managed["controller"] is controller:
                            managed["controller"] = None
                            if model.get() == "forgeos-managed":
                                backend.set("ollama")
                                model.set("auto")
                                endpoint.set("")
                        managed["last_used"] = None
                        managed["unexpected_exit"] = False
                        managed["stop_failed"] = False
                        status.set(
                            "Local llama.cpp server exited unexpectedly"
                            if was_crash else "Managed local model stopped")
                        log("Managed local llama.cpp credentials cleared after "
                            + ("unexpected process exit." if was_crash
                               else "normal server shutdown."))
            except Empty:
                pass
            controller = managed["controller"]
            lifecycle = _managed_session_event(
                controller, now=time.monotonic(), last_used=managed["last_used"],
                task_busy=busy["value"], operation_busy=managed["busy"],
                stop_failed=managed["stop_failed"])
            if lifecycle == "crashed":
                managed["unexpected_exit"] = True
                request_managed_stop(controller)
                log("Managed llama.cpp process exited unexpectedly; releasing "
                    "temporary credentials and switching back to Ollama. "
                    "Inspect diagnostics before explicitly restarting the model.")
            elif lifecycle == "idle":
                # Unload the memory-heavy CPU model after five idle minutes;
                # avoid interrupting planning, edits, tests and repair.
                request_managed_stop(controller)
                log("Managed local model idle for 5 minutes: unloading to free RAM.")
        finally:
            # A transient refresh/widget/database error must not permanently
            # stop queue polling and leave the dashboard locked.
            if not closing["value"]:
                root.after(150, poll)

    def close():
        if managed["busy"]:
            messagebox.showinfo(
                "Local model operation", "The model startup or shutdown "
                "must finish before exiting.")
            return
        if busy["value"]:
            messagebox.showinfo("Task running", "Wait for the current task to finish before closing.")
            return
        closing["value"] = True
        controller = managed["controller"]
        if controller is not None:
            try:
                controller.stop()
            except Exception as exc:
                closing["value"] = False
                messagebox.showerror(
                    "Managed server did not stop", str(exc))
                return
            managed["controller"] = None
        if not acceptance:
            try:
                save_settings(settings_path, {
                    "project": project.get(), "model": model.get(),
                    "task_role": task_role.get(), "backend": backend.get(),
                    "endpoint": endpoint.get(), "preset": preset.get(),
                    "workspaces": workspaces.get(),
                    "repair_count": repair_count.get(),
                })
            except (OSError, ValueError) as exc:
                messagebox.showwarning("Settings not saved", str(exc))
        store.close()
        root.destroy()

    # Tk does not provide a native Windows right-click editing menu for
    # classic Text/ttk Entry widgets. Bind one shared menu, not one per field.
    edit_menu = tk.Menu(root, tearoff=False)
    edit_target = {"widget": None}
    for label, sequence in (("Cut", "<<Cut>>"), ("Copy", "<<Copy>>"),
                            ("Paste", "<<Paste>>"), ("Select All", "<<SelectAll>>")):
        edit_menu.add_command(label=label, command=lambda seq=sequence:
                              edit_target["widget"].event_generate(seq)
                              if edit_target["widget"] is not None else None)

    def show_edit_menu(event):
        widget = event.widget
        if not isinstance(widget, (tk.Text, tk.Entry, ttk.Entry, ttk.Spinbox,
                                   ttk.Combobox)):
            return
        edit_target["widget"] = widget
        widget.focus_set()
        readonly = str(widget.cget("state")) in ("readonly", "disabled") if "state" in widget.keys() else False
        for index in (0, 2):  # Cut and Paste must not edit read-only fields.
            edit_menu.entryconfigure(index, state="disabled" if readonly else "normal")
        try:
            edit_menu.tk_popup(event.x_root, event.y_root)
        finally:
            edit_menu.grab_release()

    def bind_edit_menus(parent):
        for child in parent.winfo_children():
            if isinstance(child, (tk.Text, tk.Entry, ttk.Entry, ttk.Spinbox,
                                  ttk.Combobox)):
                child.bind("<Button-3>", show_edit_menu, add="+")
            bind_edit_menus(child)

    bind_edit_menus(root)
    root.protocol("WM_DELETE_WINDOW", close)
    refresh()
    if recovered:
        log(f"Recovered {recovered} interrupted task statuses. Edit/test tasks are blocked for workspace inspection; worktrees were not deleted.")
    if acceptance:
        root.withdraw()
        def verify_gui():
            try:
                root.update_idletasks()
                names = [tabs.tab(tab, "text") for tab in tabs.tabs()]
                if names != ["Development", "Activity & diagnostics"]:
                    raise RuntimeError("Unexpected GUI tab configuration")
                if not tree.winfo_exists():
                    raise RuntimeError("Task queue was not constructed")
                if not buttons["browser"].winfo_exists():
                    raise RuntimeError("Manual browser approval entry was not constructed")
                if buttons["browser"].master is not browser_row:
                    raise RuntimeError("Browser action must not overcrowd primary task controls")
                if user_guide_path() is None:
                    raise RuntimeError("Offline user guide missing from executable")
                from .model_routing import process_memory
                memory = process_memory()
                if memory is None or memory.rss_bytes <= 0:
                    raise RuntimeError("GUI process working-set measurement unavailable")
                total = store.counts()["total"]
                first_size = len(tree.get_children())
                if first_size != min(page_size, total):
                    raise RuntimeError("GUI initially rendered incorrect task page")
                sizes = [first_size]
                for index in range(1, min(4, (total + page_size - 1) // page_size)):
                    older_button.invoke()
                    actual = len(tree.get_children())
                    expected = min(page_size, total - index * page_size)
                    if actual != expected or page["offset"] != index * page_size:
                        raise RuntimeError("Older task page failed verification")
                    sizes.append(actual)
                for index in range(len(sizes) - 1):
                    newer_button.invoke()
                if page["offset"] != 0 or len(tree.get_children()) != first_size:
                    raise RuntimeError("Newer task paging failed verification")
                after = process_memory()
                if after is None or after.rss_bytes <= 0:
                    raise RuntimeError("GUI post-paging working-set measurement missing")
                delta = max(0, after.rss_bytes - memory.rss_bytes)
                if delta > 64 * 1024 * 1024:
                    raise RuntimeError("GUI paging RSS growth exceeded 64 MiB")
                report.update({
                    "tasks_verified": total,
                    "page_sizes": sizes,
                    "paginated_history_ok": True,
                    "offline_user_guide_available": True,
                    "ram_after_paging_bytes": after.rss_bytes,
                    "rss_growth_during_paging_bytes": delta,
                    "gui_started": True,
                    "tabs": names,
                    "queue_initialized": True,
                    "ram_after_gui_startup_bytes": memory.rss_bytes if memory else None,
                    "startup_elapsed_seconds": round(time.perf_counter() - started_at, 3),
                    "scope": "Frozen Tk widgets and event loop; not Ollama/user hardware",
                })
            except Exception as exc:
                report["error"] = type(exc).__name__ + ": " + str(exc)
            finally:
                try:
                    close()
                except Exception as exc:
                    report["error"] = type(exc).__name__ + ": " + str(exc)
                else:
                    report["closed_cleanly"] = True
        root.after(300, verify_gui)
    if not acceptance and project.get().strip():
        root.after(400, lambda: run_readiness_check(show_dialog=False))
    poll()
    root.mainloop()
    return report if acceptance else None
