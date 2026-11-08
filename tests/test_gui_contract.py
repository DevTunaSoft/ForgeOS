import unittest
from unittest.mock import Mock
from forgeos.gui import (
    PRESETS, STATUS_LABELS, _format_task, _managed_session_event,
    _retirement_allowed,
)


class DashboardContractTests(unittest.TestCase):
    def test_statuses_cover_engine_lifecycle(self):
        for status in ("queued", "planning", "planned", "editing",
                       "testing", "passed", "failed", "blocked"):
            self.assertIn(status, STATUS_LABELS)

    def test_task_row_truncates_untrusted_description(self):
        row = _format_task({"id": 3, "status": "editing", "description": "x" * 300})
        self.assertEqual(row[0], 3)
        self.assertEqual(row[1], "Editing")
        self.assertEqual(len(row[2]), 140)

    def test_brief_rows_work_in_gui_contract(self):
        self.assertEqual(_format_task(
            {"id": 9, "status": "testing", "description": "optimizing"}),
            (9, "Testing", "optimizing"))

    def test_pagination_uses_bounded_sqlite_data_not_complete_task_blobs(self):
        from pathlib import Path
        source = Path(__file__).resolve().parents[1] / "src" / "forgeos" / "gui.py"
        content = source.read_text(encoding="utf-8")
        self.assertIn("store.list(brief=True, limit=page_size", content)
        self.assertIn('page_size = 150', content)
        self.assertNotIn("store.list(brief=True)\n", content)
        self.assertIn('totals = store.counts()', content)

    def test_memory_snapshot_is_on_demand_in_existing_activity_tab(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] /
                "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('text="Show local memory snapshot"', text)
        self.assertIn('from .model_routing import memory_report', text)
        self.assertNotIn("root.after(1000, show_memory)", text)

    def test_managed_model_memory_cap_is_optional_and_not_a_second_policy(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] /
                "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('text="Max model RAM MiB"', text)
        self.assertIn("memory_limit_bytes=memory_limit", text)
        self.assertIn("Max model RAM must be at least 512 MiB", text)

    def test_managed_model_self_test_is_explicit_and_uses_existing_controller(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] /
                "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('text="Test local model"', text)
        self.assertIn("controller.inference_self_test()", text)
        self.assertIn('("smoke", controller, outcome, None)', text)
        self.assertIn('elif action == "smoke":', text)

    def test_managed_model_memory_is_explicit_one_shot_gui_action(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] /
                "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('text="Show model memory"', text)
        self.assertIn("controller.memory_report()", text)
        self.assertNotIn("root.after(1000, show_model_memory)", text)

    def test_acceptance_exercises_actual_tk_event_loop(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("root.withdraw()", source)
        self.assertIn("root.after(300, verify_gui)", source)
        self.assertIn('queue_initialized', source)
        self.assertIn("root.mainloop()", source)

    def test_frozen_acceptance_really_pages_queue_and_samples_rss(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] /
                "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("older_button.invoke()", text)
        self.assertIn("newer_button.invoke()", text)
        self.assertIn('rss_growth_during_paging_bytes', text)
        self.assertIn('paginated_history_ok', text)

    def test_canonical_user_guide_accessible_from_source(self):
        from forgeos.gui import user_guide_path
        path = user_guide_path()
        self.assertIsNotNone(path)
        self.assertEqual(path.name, "USER_GUIDE.md")
        self.assertIn("ForgeOS", path.read_text(encoding="utf-8"))

    def test_gui_uses_canonical_lease_safe_plan_and_test_actions(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("from .task_actions import plan_task, test_task", source)
        self.assertIn("plan_task(", source)
        self.assertIn("test_task(", source)
        self.assertNotIn("worker_store.update(", source)
        self.assertIn('operation in {"run", "test"}', source)

    def test_managed_llama_idle_unload_is_bounded_and_task_safe(self):
        from forgeos.gui import _managed_idle_due
        self.assertFalse(_managed_idle_due(
            last_used=None, now=400, task_busy=False,
            operation_busy=False))
        self.assertFalse(_managed_idle_due(
            last_used=100, now=399.9, task_busy=False,
            operation_busy=False))
        self.assertTrue(_managed_idle_due(
            last_used=100, now=400, task_busy=False,
            operation_busy=False))
        for task_busy, model_busy in ((True, False), (False, True),
                                      (True, True)):
            with self.subTest(task_busy=task_busy, model_busy=model_busy):
                self.assertFalse(_managed_idle_due(
                    last_used=100, now=999,
                    task_busy=task_busy, operation_busy=model_busy))
        self.assertTrue(_managed_idle_due(
            last_used=100, now=110, task_busy=False,
            operation_busy=False, idle_seconds=10))

    def test_idle_cleanup_reuses_existing_tk_event_loop(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('request_managed_stop(controller)', source)
        self.assertIn('root.after(150, poll)', source)
        self.assertIn('last_used=managed["last_used"]', source)
        self.assertNotIn('threading.Timer(', source)

    def test_crash_detection_precedes_idle_and_latches_failed_cleanup(self):
        process = Mock()
        process.running.return_value = False
        kwargs = {
            "now": 1000, "last_used": 100, "task_busy": False,
            "operation_busy": False,
        }
        self.assertEqual(_managed_session_event(process, **kwargs), "crashed")
        self.assertIsNone(_managed_session_event(None, **kwargs))
        self.assertIsNone(_managed_session_event(
            process, **{**kwargs, "task_busy": True}))
        self.assertIsNone(_managed_session_event(
            process, **{**kwargs, "operation_busy": True}))
        self.assertIsNone(_managed_session_event(
            process, **kwargs, stop_failed=True))
        process.running.return_value = True
        self.assertEqual(_managed_session_event(process, **kwargs), "idle")
        self.assertIsNone(_managed_session_event(
            process, **{**kwargs, "now": 101, "last_used": 100}))
        self.assertIsNone(_managed_session_event(
            process, **{**kwargs, "last_used": None}))

    def test_crash_cleanup_is_one_shot_and_restores_safe_default(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        for fragment in (
                'if lifecycle == "crashed":',
                'managed["unexpected_exit"] = True',
                'request_managed_stop(controller)',
                'managed["stop_failed"] = True',
                'stop_failed=managed["stop_failed"]',
                'backend.set("ollama")',
                'model.set("auto")',
                'endpoint.set("")'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)

    def test_opt_in_official_runtime_installer_preserves_single_gui(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("Install verified official Windows CPU runtime (opt-in)", source)
        self.assertIn("Install official Windows x64 CPU runtime", source)
        self.assertIn("from .runtime_installer import install_pinned_cpu_runtime", source)
        self.assertIn("controller.start_installed_cpu(", source)
        self.assertIn("Start installed CPU runtime", source)
        self.assertIn("Start manual runtime", source)
        self.assertIn('("install", (popup, install_state), details, None)', source)
        self.assertIn('textvariable=install_state', source)
        self.assertIn('controller[0].winfo_exists()', source)
        self.assertIn('elif action == "install":', source)
        self.assertIn("threading.Thread(target=worker, daemon=True).start()", source)
        # Downloading cannot happen on ordinary dashboard initialization.
        self.assertNotIn("install_pinned_cpu_runtime()",
                         source.split("    def manage_local_gguf():")[0])

    def test_retire_gui_interlock_blocks_active_ai_and_model(self):
        for task_busy, op_busy, model_present in (
                (True, False, False), (False, True, False),
                (False, False, True), (True, True, True)):
            with self.subTest(state=(task_busy, op_busy, model_present)):
                self.assertFalse(_retirement_allowed(
                    task_busy=task_busy, operation_busy=op_busy,
                    model_present=model_present))
        self.assertTrue(_retirement_allowed(
            task_busy=False, operation_busy=False, model_present=False))

    def test_retire_gui_uses_existing_picker_worker_and_bounded_queue(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("Retire selected GGUF (recoverable)", source)
        self.assertIn("from .model_retirement import retire_download", source)
        self.assertIn("retirement_events = Queue(maxsize=1)", source)
        self.assertIn('managed["busy"] = True', source)
        self.assertIn('managed["busy"] = False', source)
        self.assertIn("chooser.after(200, poll_retirement)", source)
        self.assertIn("chooser.protocol(\"WM_DELETE_WINDOW\", close_chooser)", source)
        self.assertIn('if retirement["busy"]:', source)
        self.assertIn("this DOES NOT free storage space", source)
        self.assertIn('gguf_path.set("")', source)

    def test_restore_gui_reuses_existing_retirement_journal_and_worker(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        for required in (
                'text="Restore a retired GGUF (recoverable)"',
                'from .model_retirement import list_retired',
                'from .model_retirement import restore_download',
                'restore_events = Queue(maxsize=1)',
                'chooser.after(200, poll_restore)',
                'chooser.protocol("WM_DELETE_WINDOW", close_restoration)',
                'if restore_state["busy"]:',
                'managed["busy"] = True',
                'managed["busy"] = False'):
            with self.subTest(fragment=required):
                self.assertIn(required, source)

    def test_worker_completion_and_poll_reschedule_fail_closed(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        task_start = source.split("    def start(operation):", 1)[1]
        worker = task_start.split("        def worker():", 1)[1].split(
            "        threading.Thread(target=worker", 1)[0]
        self.assertIn("finally:", worker)
        self.assertIn("events.put((task_id, operation, value))", worker)
        poll = source.split("    def poll():", 1)[1].split(
            "    def close():", 1)[0]
        self.assertIn("finally:", poll)
        self.assertIn("root.after(150, poll)", poll)

    def test_benchmark_baseline_reuses_managed_controller(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "src" / "forgeos" / "gui.py").read_text(encoding="utf-8")
        self.assertIn('text="Save benchmark baseline"', source)
        self.assertIn("from .model_benchmark import write_baseline", source)
        self.assertIn('("baseline", controller, outcome, None)', source)
        self.assertIn('action == "baseline"', source)

    def test_presets_are_unique(self):
        self.assertEqual(len(PRESETS), len(set(PRESETS)))
        self.assertIn("python-unittest", PRESETS)
