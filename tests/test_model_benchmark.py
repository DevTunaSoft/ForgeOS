import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forgeos.model_benchmark import (
    SCREENING_ROLES, capture_baseline, qualification_tasks, ram_tier,
    run_qualification, score_screening_response, screening_summary,
    screening_tasks, write_baseline, write_qualification,
)
from forgeos.model_routing import GIB, MemorySnapshot


class FakeController:
    def benchmark_identity(self):
        return {
            "model": {
                "repo": "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF",
                "revision": "a" * 40,
                "file": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
                "bytes": 1_100_000_000,
                "sha256": "b" * 64,
            },
            "runtime": {"sha256": "c" * 64, "release_tag": "b12199"},
            "settings": {
                "context_tokens": 2048,
                "threads": 6,
                "memory_ceiling_bytes": 8 * GIB,
            },
            "startup_elapsed_seconds": 4.25,
        }

    def inference_self_test(self):
        return {
            "status": "passed",
            "elapsed_seconds": 0.75,
            "response_bytes": 12,
            "memory": {
                "job_memory_limit_bytes": 8 * GIB,
                "peak_job_memory_used_bytes": 2 * GIB,
                "peak_process_memory_used_bytes": 2 * GIB,
                "scope": "windows_job_committed_memory",
            },
        }


class ModelBenchmarkTests(unittest.TestCase):
    def test_screening_corpus_is_fixed_five_tasks_per_role(self):
        tasks = screening_tasks()
        self.assertEqual(len(tasks), 40)
        self.assertEqual(len({task.id for task in tasks}), 40)
        for role in SCREENING_ROLES:
            with self.subTest(role=role):
                self.assertEqual(len(screening_tasks(role)), 5)
        with self.assertRaises(ValueError):
            screening_tasks("unknown-role")

    def test_item_screening_separates_json_contract_from_objective(self):
        task = screening_tasks("planning")[0]
        passed = score_screening_response(
            task, '{"items":["checkpoint","edit","test","publish"]}')
        self.assertTrue(passed["json_contract"])
        self.assertTrue(passed["objective_pass"])
        wrong = score_screening_response(task, '{"items":["edit","test"]}')
        self.assertTrue(wrong["json_contract"])
        self.assertFalse(wrong["objective_pass"])
        bad = score_screening_response(task, "not json")
        self.assertFalse(bad["json_contract"])
        self.assertFalse(bad["objective_pass"])

    def test_edit_screening_reuses_forgeos_edit_contract(self):
        task = screening_tasks("implementation")[0]
        passed = score_screening_response(
            task, '{"files":[{"path":"solution.py",'
                  '"content":"def add(a, b):\\n    return a + b\\n"}]}')
        self.assertTrue(passed["json_contract"])
        self.assertTrue(passed["objective_pass"])
        traversal = score_screening_response(
            task, '{"files":[{"path":"../solution.py","content":"x"}]}')
        self.assertFalse(traversal["json_contract"])

    def test_screening_gate_is_four_json_and_three_objective_of_five(self):
        tasks = screening_tasks("planning")
        results = []
        for index, task in enumerate(tasks):
            results.append({
                "task_id": task.id, "role": task.role,
                "json_contract": index < 4,
                "objective_pass": index < 3,
            })
        summary = screening_summary(results)
        self.assertTrue(summary["roles"]["planning"]["screening_pass"])
        results[3]["json_contract"] = False
        self.assertFalse(
            screening_summary(results)["roles"]["planning"]["screening_pass"])
        with self.assertRaises(ValueError):
            screening_summary(results + [results[0]])

    def test_role_screening_runner_is_bounded_and_drops_raw_responses(self):
        tasks = screening_tasks("planning")
        answers = {
            task.prompt: json.dumps({"items": list(task.expected_items)})
            for task in tasks
        }

        class Controller(FakeController):
            def __init__(self):
                self.calls = []
            def benchmark_prompt(self, prompt):
                self.calls.append(prompt)
                return answers[prompt]
            def memory_report(self):
                return {"peak_job_memory_used_bytes": 123}

        controller = Controller()
        progress = []
        record = __import__(
            "forgeos.model_benchmark", fromlist=["run_screening"]
        ).run_screening(
            controller, role="planning",
            progress_callback=lambda done, total, result:
                progress.append((done, total, result["task_id"])))
        self.assertEqual(len(controller.calls), 5)
        self.assertEqual(len(progress), 5)
        self.assertTrue(record["summary"]["roles"]["planning"]["screening_pass"])
        self.assertEqual(record["qualification_status"],
                         "screening_only_not_qualified")
        serialized = json.dumps(record)
        self.assertNotIn("synthetic prompt", serialized)
        self.assertNotIn('"response":', serialized)

    def test_screening_runner_preserves_partial_cancelled_evidence(self):
        import threading
        from forgeos.model_benchmark import run_screening
        event = threading.Event()
        event.set()

        class Controller(FakeController):
            def benchmark_prompt(self, prompt):
                raise AssertionError("cancelled run must not query model")
            def memory_report(self):
                return None

        record = run_screening(
            Controller(), role="security_review", cancel_event=event)
        self.assertTrue(record["cancelled"])
        self.assertEqual(record["results"], [])
        self.assertFalse(record["summary"]["complete"])

    def test_final_qualification_corpus_has_twenty_distinct_tasks_per_role(self):
        ids = set()
        for role in SCREENING_ROLES:
            tasks = qualification_tasks(role)
            self.assertEqual(len(tasks), 20)
            self.assertEqual(len({task.id for task in tasks}), 20)
            ids.update(task.id for task in tasks)
        self.assertEqual(len(ids), 160)

    def test_final_qualification_runner_emits_route_compatible_evidence(self):
        tasks = qualification_tasks("planning")
        answers = {
            task.prompt: json.dumps({"items": list(task.expected_items)})
            for task in tasks
        }

        class Controller(FakeController):
            def benchmark_prompt(self, prompt):
                return answers[prompt]
            def memory_report(self):
                return {"peak_job_memory_used_bytes": 3 * GIB}

        record = run_qualification(Controller(), role="planning")
        self.assertEqual(record["kind"], "role-qualification")
        self.assertEqual(record["tasks"], 20)
        self.assertEqual(record["json_successes"], 20)
        self.assertEqual(record["objective_successes"], 20)
        self.assertEqual(record["runtime_failures"], 0)
        self.assertEqual(record["safety_violations"], 0)
        self.assertTrue(record["qualified"])
        self.assertEqual(record["qualification_status"], "qualified")
        self.assertEqual(len(record["evidence_sha256"]), 64)
        self.assertNotIn('"response":', json.dumps(record))

    def test_final_qualification_requires_measured_peak_memory(self):
        tasks = qualification_tasks("planning")
        answers = {
            task.prompt: json.dumps({"items": list(task.expected_items)})
            for task in tasks
        }

        class Controller(FakeController):
            def benchmark_prompt(self, prompt):
                return answers[prompt]
            def memory_report(self):
                return None

        with self.assertRaisesRegex(RuntimeError, "peak memory"):
            run_qualification(Controller(), role="planning")

    def test_unsafe_edit_response_is_counted_as_safety_violation(self):
        task = qualification_tasks("implementation")[0]
        scored = score_screening_response(
            task, '{"files":[{"path":"../solution.py","content":"x"}]}')
        self.assertTrue(scored["safety_violation"])
        self.assertEqual(scored["reason"], "unsafe_edit_manifest")

    def test_final_qualification_cancel_is_nonrouteable(self):
        import threading
        event = threading.Event()
        event.set()

        class Controller(FakeController):
            def benchmark_prompt(self, prompt):
                raise AssertionError("cancelled qualification must not query model")
            def memory_report(self):
                return {"peak_job_memory_used_bytes": 3 * GIB}

        record = run_qualification(
            Controller(), role="planning", cancel_event=event)
        self.assertTrue(record["cancelled"])
        self.assertEqual(record["kind"], "role-qualification-partial")
        self.assertEqual(record["tasks"], 0)
        self.assertEqual(record["qualification_status"],
                         "incomplete_not_routeable")
        self.assertNotIn("qualified", record)

    def test_final_qualification_round_trips_through_loader_and_router(self):
        from forgeos.model_qualification import (
            load_qualification_records, route_qualified_role,
        )
        tasks = qualification_tasks("planning")
        answers = {
            task.prompt: json.dumps({"items": list(task.expected_items)})
            for task in tasks
        }

        class Controller(FakeController):
            def benchmark_prompt(self, prompt):
                return answers[prompt]
            def memory_report(self):
                return {"peak_job_memory_used_bytes": 3 * GIB}

        with tempfile.TemporaryDirectory() as temp:
            written = write_qualification(
                Controller(), role="planning", directory=temp)
            records = load_qualification_records(temp)
            chosen = route_qualified_role(
                "planning", records, memory_budget_bytes=4 * GIB,
                backend="llama.cpp", model_sha256="b" * 64,
                runtime_sha256="c" * 64)
            self.assertEqual(len(records), 1)
            self.assertEqual(
                chosen["evidence_sha256"],
                written["record"]["evidence_sha256"])
            self.assertLess(Path(written["path"]).stat().st_size, 64 * 1024)

    def test_fixed_ram_tiers(self):
        self.assertEqual(ram_tier(4 * GIB), "4-gib-gui-investigation")
        self.assertEqual(ram_tier(8 * GIB), "8-gib")
        self.assertEqual(ram_tier(16 * GIB), "16-gib")
        self.assertEqual(ram_tier(32 * GIB), "32-gib")
        with self.assertRaises(ValueError):
            ram_tier(0)

    def test_capture_is_identity_bound_secret_free_and_not_qualification(self):
        with patch(
                "forgeos.model_benchmark.system_memory",
                return_value=MemorySnapshot(32 * GIB, 20 * GIB)):
            record = capture_baseline(FakeController())
        self.assertEqual(record["hardware"]["ram_tier"], "32-gib")
        self.assertEqual(record["model"]["quantization_from_filename"], "Q4_K_M")
        self.assertFalse(record["model"]["quantization_verified"])
        self.assertIsNone(record["model"]["license_id"])
        self.assertEqual(
            record["measurement"]["managed_memory"]
            ["peak_job_memory_used_bytes"], 2 * GIB)
        self.assertEqual(
            record["qualification_status"], "baseline_only_not_qualified")
        serialized = json.dumps(record)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("endpoint", serialized)

    def test_write_is_bounded_unique_and_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as temp, patch(
                "forgeos.model_benchmark.system_memory",
                return_value=MemorySnapshot(16 * GIB, 10 * GIB)):
            first = write_baseline(FakeController(), temp)
            second = write_baseline(FakeController(), temp)
            self.assertNotEqual(first["path"], second["path"])
            for result in (first, second):
                path = Path(result["path"])
                self.assertTrue(path.is_file())
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema"], 1)
                self.assertLess(path.stat().st_size, 64 * 1024)
            self.assertFalse(list(Path(temp).glob("*.sqlite*")))


if __name__ == "__main__":
    unittest.main()
