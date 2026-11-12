"""Regression coverage for lossless PDF code-line wrapping."""
import ast
import importlib.util
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "export_master_handoff.py"


class HandoffPdfWrappingTests(unittest.TestCase):
    def test_long_line_round_trip(self):
        # Parse only the helper, so this test does not require PDF dependencies.
        module = ast.parse(SOURCE.read_text(encoding="utf-8"))
        fn = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "wrap_code_line")
        isolated = ast.Module(body=[fn], type_ignores=[])
        namespace = {"list": list}
        exec(compile(isolated, str(SOURCE), "exec"), namespace)
        wrap = namespace["wrap_code_line"]
        for sample in ["", "x" * 300, "prefix " + "abcdef" * 70, "a\tb"]:
            parts = wrap(sample)
            self.assertTrue(all(len(part) <= 92 for part in parts))
            self.assertEqual("".join(parts), sample.expandtabs(2))

    def test_wrapped_blocks_are_bounded_and_lossless(self):
        module = ast.parse(SOURCE.read_text(encoding="utf-8"))
        funcs = [n for n in module.body if isinstance(n, ast.FunctionDef)
                 and n.name in {"wrap_code_line", "code_chunks"}]
        namespace = {"list": list}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), str(SOURCE), "exec"), namespace)
        lines = ["X" * 12000, "Y" * 17, "", "Z" * 400]
        chunks = namespace["code_chunks"](lines)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(1 <= len(chunk) <= 36 for chunk in chunks))
        self.assertTrue(all(len(part) <= 92 for chunk in chunks for part in chunk))
        self.assertEqual("".join(part for chunk in chunks for part in chunk),
                         "".join(line.expandtabs(2) for line in lines))
        with self.assertRaises(ValueError):
            namespace["code_chunks"](lines, max_lines=0)

    def test_no_destructive_slice_remains(self):
        text = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("[:110]", text)
        self.assertIn("for safe in code_chunks(code_lines)", text)


if __name__ == "__main__":
    unittest.main()
