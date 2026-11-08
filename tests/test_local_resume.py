"""Extended local-backend resume contracts and fail-closed recovery tests."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from forgeos.recovery import _checkpoint_contract, resume_safe


class CheckpointStore:
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint

    def recover(self):
        return 1

    def iter_blocked_checkpoints(self):
        content = json.dumps(self.checkpoint)
        yield {"id": 2, "status": "blocked",
               "result": content, "checkpoint_bytes": len(content.encode())}


class LocalResumeTests(unittest.TestCase):
    def contract(self):
        return {
            "preset": "python-unittest",
            "max_repairs": 1,
            "model": "small-coder",
            "backend": "llama.cpp",
            "endpoint": "http://127.0.0.1:8080/v1",
        }

    def checkpoint(self, parent, contract):
        return {
            "workspace": str(parent / "task-2"),
            "phase": "test_pending",
            "contract": contract,
        }

    def test_parses_extended_and_legacy_contracts_without_change(self):
        contract = self.contract()
        self.assertEqual(_checkpoint_contract({"contract": contract}), contract)
        legacy = {k: v for k, v in contract.items()
                  if k not in ("backend", "endpoint")}
        self.assertEqual(_checkpoint_contract({"contract": legacy}), legacy)

    def test_rejects_unsafe_or_unrecognized_saved_backends(self):
        invalid = [
            {**self.contract(), "backend": "ollama"},
            {**self.contract(), "backend": "cloud"},
            {**self.contract(), "endpoint": "https://api.openai.com/v1"},
            {**self.contract(), "endpoint": "http://localhost:8080/v1"},
            {**self.contract(), "endpoint": "http://127.0.0.1:8080"},
            {**self.contract(), "endpoint": "http://127.0.0.1:8080/v1",
             "api_key": "must-not-appear"},
        ]
        for bad in invalid:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _checkpoint_contract({"contract": bad})

    def test_resumes_saved_backend_not_current_gui_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            expected = self.contract()
            store = CheckpointStore(self.checkpoint(parent, expected))
            fake_model = Mock()
            fake_model.model = expected["model"]
            fake_model.backend = expected["backend"]
            fake_model.base = expected["endpoint"]
            runner = Mock(return_value={"passed": True})
            with patch("forgeos.recovery.create_model",
                       return_value=fake_model) as factory:
                outcome = resume_safe(
                    store, parent, allow_host_tests=True, execute=runner)
            self.assertEqual(outcome["resumed"], [2])
            factory.assert_called_once_with(
                backend=expected["backend"], model=expected["model"],
                endpoint=expected["endpoint"])
            self.assertIs(runner.call_args.kwargs["model"], fake_model)
            self.assertTrue(runner.call_args.kwargs["resume"])

    def test_unsafe_checkpoint_skipped_before_factory_or_runner(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            bad = {**self.contract(), "endpoint": "http://192.168.1.2:8080/v1"}
            store = CheckpointStore(self.checkpoint(parent, bad))
            runner = Mock()
            with patch("forgeos.recovery.create_model") as factory:
                result = resume_safe(
                    store, parent, allow_host_tests=True, execute=runner)
            self.assertEqual(result["resumed"], [])
            self.assertEqual(len(result["skipped"]), 1)
            runner.assert_not_called()
            factory.assert_not_called()

    def test_backend_factory_failure_does_not_execute_any_task(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            store = CheckpointStore(self.checkpoint(parent, self.contract()))
            runner = Mock()
            with patch("forgeos.recovery.create_model",
                       side_effect=OSError("server unavailable")):
                report = resume_safe(
                    store, parent, allow_host_tests=True, execute=runner)
            self.assertEqual(report["failed"][0]["id"], 2)
            self.assertIn("server unavailable", report["failed"][0]["reason"])
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
