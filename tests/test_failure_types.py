"""Classification cannot replace test status, infer secrets, or execute content."""
import random
import unittest
from forgeos.failure_types import classify_failure

class FailureTypeTests(unittest.TestCase):
    def test_common_error_categories(self):
        cases = [
            ("ModuleNotFoundError: No module named xyz", "dependency"),
            ("SyntaxError: invalid syntax", "syntax"),
            ("AssertionError: 1 != 2", "assertion"),
            ("PermissionError: Access is denied", "permission"),
            ("TimeoutExpired 180 seconds", "timeout"),
            ("no space left on device", "resource_exhaustion"),
            ("Connection refused by server", "network"),
            ("compiler error: build failed", "build"),
            ("AppContainer security boundary blocked", "security_boundary"),
        ]
        for output, expected in cases:
            with self.subTest(output=output):
                result = classify_failure(output, passed=False)
                self.assertEqual(result["category"], expected)
                self.assertIn(expected, result["signals"])

    def test_passed_status_is_authoritative(self):
        self.assertEqual(classify_failure(
            "ModuleNotFoundError in a quoted comment", passed=True)["category"], "none")

    def test_raw_data_is_not_returned(self):
        secret = "SECRET_PASSWORD_MY_PASSWORD"
        result = classify_failure("SyntaxError " + secret, passed=False)
        self.assertNotIn(secret, repr(result))
        self.assertLessEqual(len(result["signals"]), 3)

    def test_malformed_and_random_output(self):
        self.assertEqual(classify_failure(None)["category"], "unknown")
        randomizer = random.Random(104)
        for _ in range(100):
            text = "".join(randomizer.choice("abcΩ漢字123\\n") for _ in range(250))
            result = classify_failure(text, passed=False)
            self.assertIn(result["category"], {x[0] for x in [
                ("unknown",), ("syntax",), ("assertion",), ("dependency",),
                ("permission",), ("timeout",), ("resource_exhaustion",),
                ("network",), ("build",), ("environment",), ("security_boundary",)
            ]})

    def test_long_output_preserves_final_error(self):
        output = "normal\\n" * 30000 + "\\nModuleNotFoundError: missing\\n"
        self.assertEqual(classify_failure(output, passed=False)["category"], "dependency")

if __name__ == "__main__":
    unittest.main()
