"""ForgeOS desktop dashboard. Tk calls stay exclusively on the main thread."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from queue import Empty, Queue
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

    def browse_hub_models():
        """Opt-in read-only GGUF browser; never blocks the Tk event loop."""
        from .huggingface_catalog import search_gguf, list_gguf_files

        popup = tk.Toplevel(root)
        popup.title("ForgeOS | Hugging Face GGUF discovery")
        popup.geometry("790x540")
        popup.minsize(610, 390)
        frame = ttk.Frame(popup, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="Browse public GGUF models — discovery only. "
                        "Downloads and installation are not yet implemented."
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
        info = tk.Text(frame, height=7, wrap="word", state="disabled")
        info.pack(fill="x", pady=(8, 0))
        hub_events = Queue(maxsize=2)
        state = {"busy": False, "catalog": []}

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
            else:
                term = query.get().strip()
            state["busy"] = True
            search_btn.configure(state="disabled")
            files_btn.configure(state="disabled")

            def run_lookup():
                try:
                    data = (search_gguf(term) if action == "search"
                            else list_gguf_files(term))
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

        def poll_hub():
            try:
                action, data, error = hub_events.get_nowait()
            except Empty:
                pass
            else:
                state["busy"] = False
                search_btn.configure(state="normal")
                files_btn.configure(state="normal")
                info.configure(state="normal")
                info.delete("1.0", "end")
                if error:
                    info.insert("end", error)
                elif action == "search":
                    state["catalog"] = data
                    results.delete(*results.get_children())
                    for index, item in enumerate(data):
                        results.insert("", "end", iid=str(index), values=(
                            item["repo"], item["downloads"], item["license_tag"]))
                    info.insert(
                        "end", f"Found {len(data)} candidate repositories. "
                        "A repository listing is not a license or compatibility "
                        "verification. Select one and inspect its GGUF files.")
                else:
                    for item in data[:30]:
                        size = item["bytes"] / (1024 ** 3)
                        verified = "LFS SHA-256 provided" if item["sha256"] else "Hash unavailable"
                        info.insert("end", f'{item["path"]}: {size:.2f} GiB; {verified}\\n')
                    if not data:
                        info.insert("end", "No GGUF files found in the returned listing.")
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
            worker_store = Store(db_path)
            try:
                if operation == "plan":
                    value = plan_task(
                        worker_store, task_id,
                        model_factory=lambda: create_model(backend=chosen_backend, model=chosen_model,
                                           endpoint=chosen_endpoint))
                elif operation == "run":
                    result = execute_task(
                        worker_store, task_id, chosen_workspace,
                        preset=chosen_preset, model=create_model(backend=chosen_backend, model=chosen_model,
                                           endpoint=chosen_endpoint),
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
                worker_store.close()
            events.put((task_id, operation, value))
        threading.Thread(target=worker, daemon=True).start()

    add_button("add", "Add task", add)
    add_button("run", "Build / test / repair", lambda: start("run"))
    add_button("plan", "Generate plan", lambda: start("plan"))
    add_button("test", "Run tests only", lambda: start("test"))
    add_button("refresh", "Refresh", refresh)

    def poll():
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
        if not closing["value"]:
            root.after(150, poll)

    def close():
        if busy["value"]:
            messagebox.showinfo("Task running", "Wait for the current task to finish before closing.")
            return
        closing["value"] = True
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
