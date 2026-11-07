import io
import json
import unittest
from unittest.mock import patch
from forgeos.model_routing import choose_installed_model, installed_models

MODELS = [
    {"name":"qwen2.5-coder:3b","size":2_000_000_000},
    {"name":"qwen2.5-coder:7b","size":5_000_000_000},
    {"name":"qwen2.5-coder:14b","size":9_000_000_000},
    {"name":"image-model:1b","size":1_000_000_000},
]

class LocalModelRoutingTests(unittest.TestCase):
    def test_short_task_uses_smallest_code_model(self):
        self.assertEqual(choose_installed_model("Fix button",available=MODELS),
                         "qwen2.5-coder:3b")

    def test_complex_task_uses_bigger_local_model(self):
        self.assertEqual(choose_installed_model(
            "Fix concurrent recovery security bugs",available=MODELS),
            "qwen2.5-coder:14b")

    def test_middle_complexity(self):
        self.assertEqual(choose_installed_model(
            "Review crash defects",available=MODELS), "qwen2.5-coder:7b")

    def test_max_size_respected(self):
        self.assertEqual(choose_installed_model(
            "Fix concurrent recovery security bugs",available=MODELS,
            max_model_bytes=6_000_000_000), "qwen2.5-coder:7b")

    def test_reject_remote_endpoint_and_bad_catalog(self):
        with self.assertRaises(ValueError):
            choose_installed_model("task",available=MODELS,endpoint="https://api.example.com")
        with self.assertRaises(ValueError):
            installed_models(endpoint="http://localhost:11434")
        with self.assertRaises(RuntimeError):
            choose_installed_model("task",available=[{"name":"bad;rm","size":2}])
        with self.assertRaises(ValueError):
            choose_installed_model("  ",available=MODELS)
        with self.assertRaises(ValueError):
            choose_installed_model("task",available=MODELS,max_model_bytes=True)

    def test_model_discovery_local_only(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
        data = json.dumps({"models":MODELS}).encode()
        with patch("forgeos.model_routing.urlopen",return_value=Response(data)) as mock:
            result = choose_installed_model("Fix a button")
        self.assertEqual(result,"qwen2.5-coder:3b")
        self.assertEqual(mock.call_args.args[0],"http://127.0.0.1:11434/api/tags")

    def test_unknown_models_can_fallback_to_installed_general_model(self):
        chosen=choose_installed_model(
            "test",available=[{"name":"gemma:2b","size":2_000_000_000}])
        self.assertEqual(chosen,"gemma:2b")

if __name__=="__main__":
    unittest.main()
