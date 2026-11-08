"""Recovery orchestrator tests; no untrusted code is executed."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from forgeos.core import MAX_CHECKPOINT_BYTES, Store
from forgeos.recovery import resume_safe


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
            size = len(payload.encode("utf-8"))
            yield {**task, "result": payload if size <= MAX_CHECKPOINT_BYTES else None,
                   "checkpoint_bytes": size}


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
            "endpoint": "http://127.0.0.1:40123",
            "managed_model_sha256": "a" * 64,
            "managed_runtime_sha256": "b" * 64,
        }

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
                ("base", "http://127.0.0.1:40124"),
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
        runner = Mock(return_value={"passed": True})
        report = resume_safe(store, self.parent, allow_host_tests=True,
                             execute=runner)
        self.assertEqual(report["resumed"], list(reversed(eligible)))
        self.assertEqual(report["skipped"], [])
        self.assertEqual(runner.call_count, 41)

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

    def test_missing_contract_is_skipped(self):
        checkpoint = {"phase": "test_pending",
                      "workspace": str(self.parent / "task-8")}
        runner = Mock()
        report = resume_safe(FakeStore([self.task(8, checkpoint)]), self.parent,
                             allow_host_tests=True, execute=runner)
        self.assertIn("contract", report["skipped"][0]["reason"].lower())
        runner.assert_not_called()
