"""Recovery orchestrator tests; no untrusted code is executed."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from forgeos.core import MAX_CHECKPOINT_BYTES, Store
from forgeos.recovery import checkpoint_references_managed_model, resume_safe


class FakeStore:
    def __init__(self, tasks):
        self.tasks = tasks
        self.recovery_calls = 0

    def recover(self):
        self.recovery_calls += 1
        return 1

    def iter_blocked_checkpoints(self):
        for task in self.tasks:
            if task["status"] != "blocked":
                continue
            payload = task["result"]
            size = (len(payload.encode("utf-8")) if isinstance(payload, str)
                    else len(payload) if payload is not None else None)
            if size is not None and size > MAX_CHECKPOINT_BYTES:
                payload = None
            yield {**task, "result": payload, "checkpoint_bytes": size}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)

    def task(self, task_id, checkpoint):
        return {"id": task_id, "status": "blocked", "result": json.dumps(checkpoint)}

    def contract(self, preset="python-unittest", max_repairs=2,
                 model="qwen2.5-coder:3b"):
        return {"preset": preset, "max_repairs": max_repairs, "model": model}


    def managed_contract(self, preset="python-unittest", max_repairs=2):
        return {
            "preset": preset, "max_repairs": max_repairs,
            "model": "forgeos-managed", "backend": "llama.cpp",
            "endpoint": "http://127.0.0.1:40123/v1",
            "managed_model_sha256": "a" * 64,
            "managed_runtime_sha256": "b" * 64,
        }

    def qualified_managed_contract(self):
        value = self.managed_contract()
        value.update(
            qualification_role="implementation",
            qualification_evidence_sha256="c" * 64,
            qualification_directory="")
        return value

    def test_retirement_protects_models_referenced_by_blocked_checkpoints(self):
        contract = self.managed_contract()
        payload = json.dumps({"phase": "test_pending", "contract": contract})
        row = {"status": "blocked", "result": payload,
               "checkpoint_bytes": len(payload.encode("utf-8"))}
        self.assertTrue(checkpoint_references_managed_model(
            FakeStore([row]), "a" * 64))
        self.assertFalse(checkpoint_references_managed_model(
            FakeStore([row]), "e" * 64))

    def test_retirement_fails_closed_when_checkpoint_identity_is_unreadable(self):
        for row in (
                {"status": "blocked",
                 "result": "x" * (MAX_CHECKPOINT_BYTES + 1)},
                {"status": "blocked", "result": b"not json",
                 "checkpoint_bytes": 8},
                {"status": "blocked", "result": b"[]",
                 "checkpoint_bytes": 2}):
            with self.subTest(row=row):
                with self.assertRaises(RuntimeError):
                    checkpoint_references_managed_model(
                        FakeStore([row]), "a" * 64)

    def test_qualified_managed_checkpoint_replays_exact_evidence(self):
        contract = self.qualified_managed_contract()
        checkpoint = {
            "phase": "test_pending",
            "workspace": str(self.parent / "task-73"),
            "contract": contract,
        }
        runner = Mock(return_value={"passed": True})
        model = type("ManagedModel", (), {
            "backend": "llama.cpp",
            "model": "forgeos-managed",
            "base": contract["endpoint"],
            "managed_model_sha256": contract["managed_model_sha256"],
            "managed_runtime_sha256": contract["managed_runtime_sha256"],
            "authenticated": True,
        })()
        report = resume_safe(
            FakeStore([self.task(73, checkpoint)]), self.parent,
            allow_host_tests=True, execute=runner,
            managed_model_factory=lambda _contract: model)
        self.assertEqual(report["resumed"], [73])
        self.assertEqual(model.model, "qualified-auto")
        kwargs = runner.call_args.kwargs
        self.assertEqual(kwargs["role"], "implementation")
        self.assertEqual(
            kwargs["qualification_evidence_sha256"], "c" * 64)
        self.assertIsNone(kwargs["qualification_directory"])
        self.assertTrue(kwargs["resume"])

    def test_qualified_managed_checkpoint_rejects_bad_evidence_digest(self):
        contract = self.qualified_managed_contract()
        contract["qualification_evidence_sha256"] = "../bad"
        checkpoint = {
            "phase": "test_pending",
            "workspace": str(self.parent / "task-74"),
            "contract": contract,
        }
        runner = Mock()
        report = resume_safe(
            FakeStore([self.task(74, checkpoint)]), self.parent,
            allow_host_tests=True, execute=runner,
            managed_model_factory=Mock())
        self.assertEqual(report["resumed"], [])
        self.assertIn("qualified-model", report["skipped"][0]["reason"])
        runner.assert_not_called()

    def test_qualification_path_resolution_failure_skips_only_damaged_task(self):
        directory = self.parent / "qualification"
        contract = self.qualified_managed_contract()
        contract["qualification_directory"] = str(directory)
        tasks = [
            self.task(1, {"phase": "test_pending",
                          "workspace": str(self.parent / "task-1"),
                          "contract": contract}),
            self.task(2, {"phase": "test_pending",
                          "workspace": str(self.parent / "task-2"),
                          "contract": self.contract()}),
        ]
        resolve = Path.resolve

        def resolve_path(path, *args, **kwargs):
            if path == directory:
                raise RuntimeError("Symlink loop")
            return resolve(path, *args, **kwargs)

        runner = Mock(return_value={"passed": True})
        with patch.object(Path, "resolve", resolve_path):
            report = resume_safe(FakeStore(tasks), self.parent,
                                 allow_host_tests=True, execute=runner)
        self.assertEqual(report["skipped"], [
            {"id": 1, "reason": "Qualification evidence directory is unavailable"}])
        self.assertEqual(report["resumed"], [2])
        runner.assert_called_once()

    def test_managed_checkpoint_requires_authenticated_runtime_factory(self):
        checkpoint = {
            "phase": "test_pending",
            "workspace": str(self.parent / "task-70"),
            "contract": self.managed_contract(),
        }
        runner = Mock()
        report = resume_safe(
            FakeStore([self.task(70, checkpoint)]), self.parent,
            allow_host_tests=True, execute=runner)
        self.assertEqual(report["resumed"], [])
        self.assertIn("authenticated", report["skipped"][0]["reason"].lower())
        runner.assert_not_called()

        sink = Mock(side_effect=BrokenPipeError("closed output"))
        with self.assertRaisesRegex(BrokenPipeError, "closed output"):
            resume_safe(FakeStore([self.task(70, checkpoint)]), self.parent,
                        allow_host_tests=True, execute=runner, on_event=sink)
        sink.assert_called_once()
        self.assertEqual(sink.call_args.args[0]["event"], "skipped")

    def test_managed_checkpoint_resumes_only_with_exact_attested_identity(self):
        contract = self.managed_contract()
        checkpoint = {
            "phase": "repair_pending",
            "workspace": str(self.parent / "task-71"),
            "contract": contract,
        }
        runner = Mock(return_value={"passed": True})
        model = type("ManagedModel", (), {
            "backend": "llama.cpp",
            "model": "forgeos-managed",
            "base": contract["endpoint"],
            "managed_model_sha256": contract["managed_model_sha256"],
            "managed_runtime_sha256": contract["managed_runtime_sha256"],
            "authenticated": True,
        })()
        factory = Mock(return_value=model)
        report = resume_safe(
            FakeStore([self.task(71, checkpoint)]), self.parent,
            allow_host_tests=True, execute=runner,
            managed_model_factory=factory)
        self.assertEqual(report["resumed"], [71])
        self.assertEqual(report["skipped"], [])
        factory.assert_called_once_with(contract)
        self.assertIs(runner.call_args.kwargs["model"], model)
        self.assertTrue(runner.call_args.kwargs["resume"])

    def test_managed_checkpoint_rejects_hash_endpoint_and_auth_mismatch(self):
        for field, bad_value in (
                ("managed_model_sha256", "c" * 64),
                ("managed_runtime_sha256", "d" * 64),
                ("base", "http://127.0.0.1:40124/v1"),
                ("authenticated", False)):
            with self.subTest(field=field):
                contract = self.managed_contract()
                checkpoint = {
                    "phase": "test_pending",
                    "workspace": str(self.parent / "task-72"),
                    "contract": contract,
                }
                values = {
                    "backend": "llama.cpp",
                    "model": "forgeos-managed",
                    "base": contract["endpoint"],
                    "managed_model_sha256": contract["managed_model_sha256"],
                    "managed_runtime_sha256": contract["managed_runtime_sha256"],
                    "authenticated": True,
                }
                values[field] = bad_value
                model = type("ManagedModel", (), values)()
                runner = Mock()
                report = resume_safe(
                    FakeStore([self.task(72, checkpoint)]), self.parent,
                    allow_host_tests=True, execute=runner,
                    managed_model_factory=lambda _contract: model)
                self.assertEqual(report["resumed"], [])
                self.assertEqual(report["failed"][0]["id"], 72)
                runner.assert_not_called()

    def test_invalid_managed_factory_rejected_before_store_mutation(self):
        store = FakeStore([])
        with self.assertRaisesRegex(ValueError, "managed_model_factory"):
            resume_safe(
                store, self.parent, allow_host_tests=True,
                managed_model_factory="not callable")
        self.assertEqual(store.recovery_calls, 0)

    def test_permission_denied_before_store_mutation(self):
        store = FakeStore([])
        with self.assertRaisesRegex(ValueError, "permission"):
            resume_safe(store, self.parent)
        self.assertEqual(store.recovery_calls, 0)

    def test_only_matching_checkpoint_resumes(self):
        valid = {"phase": "test_pending",
                 "workspace": str(self.parent / "task-1"),
                 "contract": self.contract()}
        tasks = [
            self.task(1, valid),
            self.task(2, {"phase": "repair_pending"}),
            self.task(3, {"phase": "test_pending", "workspace": "/other/task-3"}),
        ]
        runner = Mock(return_value={"passed": True})
        report = resume_safe(FakeStore(tasks), self.parent,
                             allow_host_tests=True, execute=runner)
        self.assertEqual(report["resumed"], [1])
        self.assertEqual(len(report["skipped"]), 2)
        self.assertEqual(runner.call_count, 1)
        self.assertTrue(runner.call_args.kwargs["resume"])

    def test_invalid_checkpoint_fields_do_not_abort_later_recovery(self):
        tasks = [
            self.task(1, {"phase": []}),
            self.task(2, {
                "phase": "test_pending",
                "workspace": str(self.parent / "task-2"),
                "contract": self.contract(preset={}),
            }),
            {**self.task(3, None), "result": "[" * 12000 + "]" * 12000},
            {**self.task(5, None), "result": None},
            self.task(4, {
                "phase": "test_pending",
                "workspace": str(self.parent / "task-4"),
                "contract": self.contract(),
            }),
        ]
        runner = Mock(return_value={"passed": True})
        report = resume_safe(
            FakeStore(tasks), self.parent, allow_host_tests=True, execute=runner)
        self.assertEqual(report["skipped"][:2], [
            {"id": 1, "reason": "No resumable checkpoint"},
            {"id": 2, "reason": "Invalid checkpoint test preset"},
        ])
        self.assertEqual(len(report["skipped"]), 4)
        self.assertEqual(report["skipped"][2]["id"], 3)
        self.assertIn(report["skipped"][2]["reason"],
                      ("No structured checkpoint", "No resumable checkpoint"))
        self.assertEqual(report["skipped"][3],
                         {"id": 5, "reason": "No structured checkpoint"})
        self.assertEqual(report["resumed"], [4])
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[1], 4)

    def test_bad_workspace_path_does_not_abort_later_recovery(self):
        malformed = self.task(1, {
            "phase": "test_pending",
            "workspace": str(self.parent / "task-1") + "\x00unexpected",
            "contract": self.contract(),
        })
        valid = self.task(2, {
            "phase": "test_pending",
            "workspace": str(self.parent / "task-2"),
            "contract": self.contract(),
        })
        runner = Mock(return_value={"passed": True})
        report = resume_safe(
            FakeStore([malformed, valid]), self.parent,
            allow_host_tests=True, execute=runner)
        self.assertEqual(report["resumed"], [2])
        self.assertEqual(report["skipped"], [
            {"id": 1, "reason": "Workspace location mismatch"}])
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[1], 2)

    def test_symlinked_workspace_is_not_resumed(self):
        target = self.parent / "real-workspace"
        target.mkdir()
        linked = self.parent / "task-10"
        try:
            linked.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable")
        checkpoint = {
            "phase": "test_pending",
            "workspace": str(linked),
            "contract": self.contract(),
        }
        runner = Mock()
        report = resume_safe(
            FakeStore([self.task(10, checkpoint)]), self.parent,
            allow_host_tests=True, execute=runner)
        self.assertEqual(report["resumed"], [])
        self.assertEqual(report["skipped"][0]["reason"], "Workspace location mismatch")
        runner.assert_not_called()

    def test_failure_is_isolated_per_task(self):
        tasks = [
            self.task(i, {"phase": "test_pending",
                          "workspace": str(self.parent / f"task-{i}"),
                          "contract": self.contract()})
            for i in (1, 2)
        ]
        runner = Mock(side_effect=[RuntimeError("disk error"), {"passed": True}])
        report = resume_safe(FakeStore(tasks), self.parent,
                             allow_host_tests=True, execute=runner)
        self.assertEqual(report["resumed"], [2])
        self.assertEqual(report["failed"][0]["id"], 1)

    def test_malformed_checkpoint_skipped(self):
        store = FakeStore([{"id": 1, "status": "blocked", "result": "{"}])
        runner = Mock()
        report = resume_safe(store, self.parent,
                             allow_host_tests=True, execute=runner)
        self.assertEqual(len(report["skipped"]), 1)
        runner.assert_not_called()

    def test_repair_pending_checkpoint_is_eligible(self):
        checkpoint = {"phase": "repair_pending",
                      "workspace": str(self.parent / "task-4"),
                      "contract": self.contract()}
        runner = Mock(return_value={"passed": True})
        report = resume_safe(FakeStore([self.task(4, checkpoint)]), self.parent,
                             allow_host_tests=True, execute=runner)
        self.assertEqual(report["resumed"], [4])
        self.assertEqual(report["failed"], [])
        self.assertEqual(report["skipped"], [])
        self.assertEqual(runner.call_count, 1)

    def test_replays_saved_contract_per_task(self):
        tasks = [
            self.task(5, {"phase": "test_pending",
                          "workspace": str(self.parent / "task-5"),
                          "contract": self.contract("python-pytest", 1, "model-a")}),
            self.task(6, {"phase": "repair_pending",
                          "workspace": str(self.parent / "task-6"),
                          "contract": self.contract("go-test", 3, "model-b")}),
        ]
        runner = Mock(side_effect=[{"passed": True}, {"passed": True}])
        created = []

        def model_factory(*, model):
            created.append(model)
            return type("FakeModel", (), {"model": model})()

        report = resume_safe(FakeStore(tasks), self.parent,
                             allow_host_tests=True, execute=runner,
                             model_factory=model_factory)
        self.assertEqual(report["resumed"], [5, 6])
        self.assertEqual(created, ["model-a", "model-b"])
        self.assertEqual(runner.call_args_list[0].kwargs["preset"], "python-pytest")
        self.assertEqual(runner.call_args_list[0].kwargs["max_repairs"], 1)
        self.assertEqual(runner.call_args_list[1].kwargs["preset"], "go-test")
        self.assertEqual(runner.call_args_list[1].kwargs["max_repairs"], 3)

    def test_override_mismatch_skips_without_execution(self):
        checkpoint = {"phase": "test_pending",
                      "workspace": str(self.parent / "task-7"),
                      "contract": self.contract("python-pytest", 1, "model-a")}
        runner = Mock()
        report = resume_safe(FakeStore([self.task(7, checkpoint)]), self.parent,
                             preset="python-unittest",
                             allow_host_tests=True, execute=runner)
        self.assertEqual(report["resumed"], [])
        self.assertIn("mismatch", report["skipped"][0]["reason"].lower())
        runner.assert_not_called()

    def test_oversized_checkpoint_skipped_before_model_or_runner(self):
        oversized = self.task(9, {"phase": "test_pending",
                                  "workspace": str(self.parent / "task-9"),
                                  "contract": self.contract()})
        oversized["result"] += "Z" * (MAX_CHECKPOINT_BYTES + 1)
        runner = Mock()
        factory = Mock()
        report = resume_safe(FakeStore([oversized]), self.parent,
                             allow_host_tests=True, execute=runner,
                             model_factory=factory)
        self.assertEqual(report["resumed"], [])
        self.assertIn("safety limit", report["skipped"][0]["reason"])
        runner.assert_not_called()
        factory.assert_not_called()

    def test_real_store_paginates_many_blocked_tasks_and_ignores_large_history(self):
        repo = self.parent / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        store = Store(self.parent / "tasks.sqlite3")
        self.addCleanup(store.close)
        eligible = []
        for index in range(41):
            task_id = store.add(str(repo), f"resume task {index}")
            store.update(task_id, "blocked", result=json.dumps({
                "phase": "test_pending",
                "workspace": str(self.parent / f"task-{task_id}"),
                "contract": self.contract()}))
            eligible.append(task_id)
            history_id = store.add(str(repo), f"completed task {index}")
            store.update(history_id, "passed", result="P" * (80 * 1024))
        damaged = store.add(str(repo), "damaged checkpoint encoding")
        with store.db:
            store.db.execute(
                "UPDATE tasks SET status='blocked', result=CAST(? AS TEXT) WHERE id=?",
                (b'\xff', damaged))
        with self.assertRaisesRegex(ValueError, "checkpoint is not valid UTF-8"):
            store.get_task_inputs(damaged, resume=True)
        runner = Mock(return_value={"passed": True})
        report = resume_safe(store, self.parent, allow_host_tests=True,
                             execute=runner)
        self.assertEqual(report["resumed"], list(reversed(eligible)))
        self.assertEqual(report["skipped"], [
            {"id": damaged, "reason": "No structured checkpoint"}])
        self.assertEqual(runner.call_count, 41)

    def test_large_exception_reasons_are_bounded_for_gui_and_cli(self):
        message = "initial error: " + "x" * 100000 + " / final cause"
        tasks = [
            self.task(i, {"phase": "test_pending",
                          "workspace": str(self.parent / f"task-{i}"),
                          "contract": self.contract()})
            for i in (1, 2)
        ]
        from forgeos.recovery import MAX_RECOVERY_REASON_CHARS
        for collect_results in (True, False):
            with self.subTest(collect_results=collect_results):
                events = []
                execute = Mock(side_effect=[
                    RuntimeError(message), {"passed": True}])
                report = resume_safe(
                    FakeStore(tasks), self.parent, allow_host_tests=True,
                    execute=execute, on_event=events.append,
                    collect_results=collect_results)
                self.assertEqual(execute.call_count, 2)
                self.assertEqual(len(events), 2)
                reason = events[0]["reason"]
                self.assertLessEqual(len(reason), MAX_RECOVERY_REASON_CHARS)
                self.assertTrue(reason.startswith("initial error: "))
                self.assertTrue(reason.endswith(" / final cause"))
                self.assertIn("[middle omitted]", reason)
                if collect_results:
                    self.assertEqual(report["failed"][0]["reason"], reason)
                    self.assertEqual(report["resumed"], [2])
                else:
                    self.assertEqual(report["failed"], [])
                    self.assertEqual(report["resumed"], [])
                    self.assertEqual(report["counts"],
                                     {"resumed": 1, "skipped": 0, "failed": 1})

    def test_streaming_results_keep_constant_size_and_all_event_reasons(self):
        tasks = []
        for index in range(60):
            if index % 3 == 0:
                checkpoint = {"phase": "test_pending",
                              "workspace": str(self.parent / f"task-{index}"),
                              "contract": self.contract()}
            else:
                checkpoint = {"phase": "unknown"}
            tasks.append(self.task(index, checkpoint))
        events = []
        runner = Mock(return_value={"passed": True})
        report = resume_safe(FakeStore(tasks), self.parent,
                             allow_host_tests=True, execute=runner,
                             on_event=events.append, collect_results=False)
        self.assertEqual(report["resumed"], [])
        self.assertEqual(report["skipped"], [])
        self.assertEqual(report["failed"], [])
        self.assertEqual(report["counts"],
                         {"resumed": 20, "skipped": 40, "failed": 0})
        self.assertEqual(len(events), 60)
        self.assertEqual(events[0], {"event": "resumed", "id": 0})
        self.assertEqual(events[1]["event"], "skipped")
        self.assertIn("reason", events[1])
        self.assertEqual(runner.call_count, 20)

    def test_streaming_failure_count_and_sink_errors_not_swallowed(self):
        tasks = [self.task(1, {"phase": "test_pending",
                               "workspace": str(self.parent / "task-1"),
                               "contract": self.contract()}),
                 self.task(2, {"phase": "test_pending",
                               "workspace": str(self.parent / "task-2"),
                               "contract": self.contract()})]
        runner = Mock(side_effect=[RuntimeError("test crash"), {"passed": True}])
        events = []
        report = resume_safe(FakeStore(tasks), self.parent,
                             allow_host_tests=True, execute=runner,
                             collect_results=False, on_event=events.append)
        self.assertEqual(report["counts"],
                         {"resumed": 1, "skipped": 0, "failed": 1})
        self.assertEqual(events[0],
                         {"event": "failed", "id": 1, "reason": "test crash"})
        sink = Mock(side_effect=RuntimeError("stream closed"))
        with self.assertRaisesRegex(RuntimeError, "stream closed"):
            resume_safe(FakeStore(tasks), self.parent, allow_host_tests=True,
                        execute=Mock(return_value={"passed": True}),
                        collect_results=False, on_event=sink)
        self.assertEqual(sink.call_count, 1)

    def test_invalid_stream_options_rejected_before_store_mutation(self):
        store = FakeStore([])
        with self.assertRaisesRegex(ValueError, "boolean"):
            resume_safe(store, self.parent, allow_host_tests=True,
                        collect_results=1)
        with self.assertRaisesRegex(ValueError, "callable"):
            resume_safe(store, self.parent, allow_host_tests=True,
                        on_event="not callable")
        self.assertEqual(store.recovery_calls, 0)

    def test_invalid_preset_override_rejected_before_store_mutation(self):
        store = FakeStore([])
        for preset in ([], {}, "unknown"):
            with self.subTest(preset=preset):
                with self.assertRaisesRegex(ValueError, "Invalid test preset override"):
                    resume_safe(store, self.parent, allow_host_tests=True, preset=preset)
        self.assertEqual(store.recovery_calls, 0)

    def test_manual_review_checkpoint_is_skipped_without_model_or_runner(self):
        checkpoint = {
            "phase": "manual_review_required",
            "capture_complete": False,
            "workspace": str(self.parent / "task-99"),
            "contract": self.contract(),
        }
        runner = Mock()
        factory = Mock()
        tasks = [self.task(99, checkpoint)]
        for _ in range(2):
            report = resume_safe(
                FakeStore(tasks), self.parent, allow_host_tests=True,
                execute=runner, model_factory=factory)
            self.assertEqual(report["resumed"], [])
            self.assertEqual(report["failed"], [])
            self.assertEqual(report["skipped"][0]["id"], 99)
            self.assertIn("manual review", report["skipped"][0]["reason"])
        runner.assert_not_called()
        factory.assert_not_called()

    def test_missing_contract_is_skipped(self):
        checkpoint = {"phase": "test_pending",
                      "workspace": str(self.parent / "task-8")}
        runner = Mock()
        report = resume_safe(FakeStore([self.task(8, checkpoint)]), self.parent,
                             allow_host_tests=True, execute=runner)
        self.assertIn("contract", report["skipped"][0]["reason"].lower())
        runner.assert_not_called()
