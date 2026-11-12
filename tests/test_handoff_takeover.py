"""Takeover audit must never turn manual acceptance into a fabricated pass."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_handoff_takeover.py"
spec = importlib.util.spec_from_file_location("audit_handoff_takeover", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TakeoverAuditTests(unittest.TestCase):
    def test_missing_documents_fail(self):
        with tempfile.TemporaryDirectory() as d:
            result = module.audit(Path(d))
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any(c["missing"] for c in result["checks"] if "missing" in c))

    def test_present_documents_do_not_fabricate_manual_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for paths in module.REQUIRED.values():
                for rel in paths:
                    target = root / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("test", encoding="utf-8")
            result = module.audit(root)
            self.assertEqual(result["status"], "INCOMPLETE")
            self.assertTrue(all(c["status"] == "NOT RUN" for c in result["checks"] if c["criterion"] in module.MANUAL))


if __name__ == "__main__":
    unittest.main()
