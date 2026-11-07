"""ForgeOS desktop dashboard. Tk calls stay exclusively on the main thread."""
from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .core import Store, Ollama, run_test
from .engine import execute_task

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


def launch(db_path):
    store = Store(db_path)
    # Interrupted tasks must not silently restart: a worktree may contain
    # generated changes that require manual inspection.
    recovered = store.recover()
    root = tk.Tk()
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
    model = tk.StringVar(value="qwen2.5-coder:3b")
    preset = tk.StringVar(value="python-unittest")
    workspaces = tk.StringVar(value=str(Path.home() / ".forgeos" / "workspaces"))
    allow_host_tests = tk.BooleanVar(value=False)
    repair_count = tk.IntVar(value=2)
    status = tk.StringVar(value="Ready")
    counts = tk.StringVar(value="")
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
    ttk.Label(first, text="Local model").pack(side="left")
    ttk.Entry(first, textvariable=model, width=23).pack(side="left", padx=(6, 18))
    ttk.Label(first, text="Test suite").pack(side="left")
    ttk.Combobox(first, textvariable=preset, values=PRESETS,
                 state="readonly", width=20).pack(side="left", padx=(6, 18))
    ttk.Label(first, text="Repair retries").pack(side="left")
    ttk.Spinbox(first, from_=0, to=5, textvariable=repair_count, width=4).pack(side="left", padx=6)
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

    actions = ttk.Frame(dashboard)
    actions.pack(fill="x")
    buttons = {}
    def add_button(key, text, callback):
        button = ttk.Button(actions, text=text, command=callback)
        button.pack(side="left", padx=(0, 8))
        buttons[key] = button

    diagnostics = tk.Text(activity, wrap="word", state="disabled")
    diagnostics.pack(fill="both", expand=True)
    ttk.Label(activity, text="Task history and test output. No background execution occurs when this window closes.",
              foreground="#52647b").pack(anchor="w", pady=6)

    def log(text):
        diagnostics.configure(state="normal")
        diagnostics.insert("end", str(text) + "\n\n")
        diagnostics.see("end")
        diagnostics.configure(state="disabled")

    def refresh():
        selection = tree.selection()
        selected_id = selection[0] if selection else None
        rows = store.list()
        tree.delete(*tree.get_children())
        for task in rows:
            tree.insert("", "end", iid=str(task["id"]), values=_format_task(task))
        if selected_id and tree.exists(selected_id):
            tree.selection_set(selected_id)
        counts.set(f"{len(rows)} tasks  •  "
                   f"{sum(t['status'] == 'passed' for t in rows)} passed  •  "
                   f"{sum(t['status'] == 'failed' for t in rows)} failed  •  "
                   f"{sum(t['status'] == 'blocked' for t in rows)} blocked")

    def selected():
        items = tree.selection()
        if not items:
            raise ValueError("Select a task in the queue first.")
        return int(items[0])

    def add():
        try:
            task_id = store.add(project.get(), description.get("1.0", "end").strip())
            refresh()
            tree.selection_set(str(task_id))
            status.set(f"Task #{task_id} queued")
            log(f"Created task #{task_id}.")
        except Exception as exc:
            messagebox.showerror("Cannot create task", str(exc))

    def start(operation):
        if busy["value"]:
            return
        try:
            task_id = selected()
            chosen_model = model.get().strip()
            chosen_preset = preset.get()
            chosen_workspace = Path(workspaces.get()).expanduser()
            chosen_retries = repair_count.get()
            chosen_host_permission = allow_host_tests.get()
            if operation == "run" and not chosen_host_permission:
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
                task = worker_store.get(task_id)
                if operation == "plan":
                    worker_store.update(task_id, "planning", increment=True)
                    value = Ollama(model=chosen_model).plan(task["description"])
                    worker_store.update(task_id, "planned", result=value)
                elif operation == "run":
                    result = execute_task(
                        worker_store, task_id, chosen_workspace,
                        preset=chosen_preset, model=Ollama(model=chosen_model),
                        max_repairs=chosen_retries,
                        allow_host_tests=chosen_host_permission,
                    )
                    value = json.dumps(result, indent=2)
                else:
                    worker_store.update(task_id, "testing", increment=True)
                    result = run_test(task["project"], chosen_preset)
                    worker_store.update(
                        task_id, "passed" if result["passed"] else "failed",
                        result=json.dumps(result),
                        error="" if result["passed"] else result["output"],
                    )
                    value = json.dumps(result, indent=2)
            except Exception as exc:
                value = f"{type(exc).__name__}: {exc}"
                # The engine manages its own blocked status and preserves the
                # workspace location; do not overwrite its recorded result.
                if operation != "run":
                    worker_store.update(task_id, "blocked", error=value)
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
        log(f"Requeued {recovered} interrupted tasks. Existing worktrees were not deleted.")
    poll()
    root.mainloop()
