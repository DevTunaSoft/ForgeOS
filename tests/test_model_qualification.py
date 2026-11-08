import copy
import unittest

from forgeos.model_qualification import (
    qualify_role_evidence, route_qualified_role,
)


def evidence(model="a", *, role="implementation", tasks=20,
             json_ok=19, objective_ok=16, size=2_000_000_000,
             peak=3_000_000_000, latency=2.0):
    return {
        "schema": 1,
        "kind": "role-qualification",
        "model_sha256": model * 64,
        "runtime_sha256": "b" * 64,
        "role": role,
        "tasks": tasks,
        "json_successes": json_ok,
        "objective_successes": objective_ok,
        "model_bytes": size,
        "peak_memory_bytes": peak,
        "median_latency_seconds": latency,
    }


class ModelQualificationTests(unittest.TestCase):
    def test_exact_final_thresholds_qualify(self):
        result = qualify_role_evidence(evidence())
        self.assertTrue(result["qualified"])
        self.assertEqual(result["json_rate"], .95)
        self.assertEqual(result["objective_rate"], .8)
        self.assertEqual(len(result["evidence_sha256"]), 64)

    def test_below_either_threshold_fails(self):
        self.assertFalse(qualify_role_evidence(
            evidence(json_ok=18))["qualified"])
        self.assertFalse(qualify_role_evidence(
            evidence(objective_ok=15))["qualified"])

    def test_screening_evidence_cannot_be_promoted(self):
        item = evidence()
        item["kind"] = "screening"
        with self.assertRaisesRegex(ValueError, "Final role qualification"):
            qualify_role_evidence(item)

    def test_requires_meaningful_final_corpus(self):
        with self.assertRaisesRegex(ValueError, "counts"):
            qualify_role_evidence(evidence(tasks=5, json_ok=5, objective_ok=5))

    def test_router_selects_smallest_qualified_model_not_fastest(self):
        small = evidence("1", size=2_000_000_000, peak=3_000_000_000, latency=8)
        large = evidence("2", size=5_000_000_000, peak=6_000_000_000, latency=1)
        chosen = route_qualified_role(
            "implementation", [large, small], memory_budget_bytes=8_000_000_000)
        self.assertEqual(chosen["model_sha256"], "1" * 64)
        self.assertEqual(chosen["selection_policy"],
                         "smallest-measured-qualified-model")

    def test_router_fails_closed_for_unqualified_or_over_budget(self):
        failed = evidence(json_ok=18)
        with self.assertRaisesRegex(RuntimeError, "No measured-qualified"):
            route_qualified_role(
                "implementation", [failed], memory_budget_bytes=8_000_000_000)
        with self.assertRaisesRegex(RuntimeError, "No measured-qualified"):
            route_qualified_role(
                "implementation", [evidence(peak=9_000_000_000)],
                memory_budget_bytes=8_000_000_000)

    def test_wrong_role_is_not_routeable(self):
        with self.assertRaises(RuntimeError):
            route_qualified_role(
                "debugging", [evidence(role="implementation")],
                memory_budget_bytes=8_000_000_000)

    def test_invalid_records_do_not_poison_valid_selection(self):
        valid = evidence()
        invalid = copy.deepcopy(valid)
        invalid["model_sha256"] = "../bad"
        chosen = route_qualified_role(
            "implementation", [invalid, valid],
            memory_budget_bytes=8_000_000_000)
        self.assertEqual(chosen["rejected_invalid_records"], 1)

    def test_evidence_digest_changes_when_identity_changes(self):
        first = qualify_role_evidence(evidence("a"))
        second = qualify_role_evidence(evidence("c"))
        self.assertNotEqual(first["evidence_sha256"], second["evidence_sha256"])


if __name__ == "__main__":
    unittest.main()
