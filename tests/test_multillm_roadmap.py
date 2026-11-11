"""Regression contract: every multi-LLM requirements workstream stays visible.

This is a coverage/traceability check, NOT proof that feature behavior works.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MultiLLMRoadmapContractTests(unittest.TestCase):
    def test_all_ten_work_packages_and_release_gates_are_tracked(self):
        doc = (ROOT / "docs" / "MULTI_LLM_ROADMAP.md").read_text(encoding="utf-8")
        for letter in "ABCDEFGHIJ":
            with self.subTest(work_package=letter):
                self.assertIn(f"## Work package {letter} —", doc)
        for priority in ("P0", "P1", "P2", "P3"):
            self.assertIn(f"| {priority} |", doc)
        for term in ("llama.cpp", "Ollama", "LM Studio", "Hugging Face",
                     "SHA-256", "quantization", "RAM", "fault-injection",
                     "intelligent", "handoff", "Windows", "GPU"):
            with self.subTest(term=term):
                self.assertIn(term.lower(), doc.lower())
        self.assertIn("real-model", doc)
        # PR #51 was merged: historical branch status must not become a
        # permanent requirement that forces the roadmap to misstate main.
        self.assertIn("merged into `main`", doc)
        self.assertIn("historical branch evidence", doc)
        self.assertIn("no Docker", doc)

    def test_roadmap_links_from_canonical_documents(self):
        for name in ("README.md", "docs/REQUIREMENTS.md",
                     "docs/ROADMAP.md", "docs/MODEL_COMPATIBILITY.md"):
            with self.subTest(name=name):
                self.assertIn(
                    "MULTI_LLM_ROADMAP.md",
                    (ROOT / name).read_text(encoding="utf-8"))

    def test_roadmap_extends_existing_infrastructure_instead_of_duplication(self):
        source = (ROOT / "docs" / "MULTI_LLM_ROADMAP.md").read_text(encoding="utf-8")
        for filename in ("core.py", "model_routing.py", "engine.py",
                         "task_actions.py", "recovery.py", "gui.py",
                         "gguf_download.py", "huggingface_catalog.py",
                         "local_models.py", "RTK_ENHANCEMENTS.md",
                         "ENHANCEMENT_DEDUP_AUDIT.md"):
            with self.subTest(filename=filename):
                self.assertIn(filename, source)

    def test_distinguishes_ci_from_physical_windows_and_shipped_main(self):
        content = (ROOT / "docs" / "MULTI_LLM_ROADMAP.md").read_text(
            encoding="utf-8").lower()
        for term in ("draft", "merged into `main`", "historical branch evidence",
                     "physical windows", "hardware", "planned", "unverified"):
            self.assertIn(term, content)
        self.assertIn("at most one heavy resident inference model", content)


if __name__ == "__main__":
    unittest.main()
