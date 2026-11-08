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

    project = tk.StringVar()
    model = tk.StringVar(value="auto")
    backend = tk.StringVar(value="ollama")
    endpoint = tk.StringVar(value="")
    preset = tk.StringVar(value="python-unittest")
    workspaces = tk.StringVar(value=str(Path.home() / ".forgeos" / "workspaces"))
    allow_host_tests = tk.BooleanVar(value=False)
    repair_count = tk.IntVar(value=2)
    status = tk.StringVar(value="Ready")
    counts = tk.StringVar(value="")
    page = {"offset": 0}
    page_size = 150
    events = Queue()
    managed_events = Queue(maxsize=2)
    managed = {"controller": None, "busy": False, "last_used": None,
               "unexpected_exit": False, "stop_failed": False}
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
    ttk.Label(first, text="Local model (auto = RAM-aware)").pack(side="left")
    ttk.Entry(first, textvariable=model, width=23).pack(side="left", padx=(6, 18))
    ttk.Label(first, text="Test suite").pack(side="left")
    ttk.Combobox(first, textvariable=preset, values=PRESETS,
                 state="readonly", width=20).pack(side="left", padx=(6, 18))
    ttk.Label(first, text="Repair retries").pack(side="left")
    ttk.Spinbox(first, from_=0, to=5, textvariable=repair_count, width=4).pack(side="left", padx=6)
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

    actions = ttk.Frame(dashboard)
    actions.pack(fill="x")
    buttons = {}
    def add_button(key, text, callback):
        button = ttk.Button(actions, text=text, command=callback)
        button.pack(side="left", padx=(0, 8))
        buttons[key] = button

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
        popup.geometry("875x520")
        popup.minsize(750, 465)
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
                        or chosen_model != manager.model_name):
                    raise ValueError("Active managed model settings do not match")
                managed_key = manager.api_key
                managed["last_used"] = time.monotonic()
            elif chosen_model == "forgeos-managed":
                raise ValueError("Start the verified managed GGUF runtime before "
                                 "using the forgeos-managed model")
            chosen_preset = preset.get()
            chosen_workspace = Path(workspaces.get()).expanduser()
            chosen_retries = repair_count.get()
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

    def poll():
        try:
            try:
                while True:
                    task_id, operation, value = events.get_nowait()
                    busy["value"] = False
                    for button in buttons.values():
                        button.configure(state="normal")
                    refresh()
                    status.set(f"Task #{task_id}: {store.get(task_id)['status']}")
                    log(f"Task #{task_id} / {operation}\n{value}")
                    tabs.select(activity)
            except Empty:
                pass
            try:
                while True:
                    action, controller, outcome, error = managed_events.get_nowait()
                    managed["busy"] = False
                    if error:
                        if action == "stop":
                            managed["stop_failed"] = True
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
        store.close()
        root.destroy()

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
    poll()
    root.mainloop()
    return report if acceptance else None
