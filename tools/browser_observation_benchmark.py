"""Deterministic, dependency-free browser observation memory/size benchmark.

Measures traced Python allocations for a bounded string formatter only, not
Chromium, Browsentic Bridge, Ollama, RSS, or genuine LLM token consumption.
"""
from __future__ import annotations

import argparse
import gc
import json
import tracemalloc

from forgeos.browsentic_readonly import changed_diagram, compact_diagram


def run_benchmark() -> dict[str, int | bool | str]:
    # 45 KiB of mostly blank lines previously caused a splitlines() allocation
    # proportional to page line count. Construct before enabling tracemalloc.
    pathological = "\n" * 45_000 + "heading Last line"
    common = "".join("heading Section {}\n".format(i) for i in range(22))
    before = common + "button Old\n"
    after = common + "button New\n"

    gc.collect()
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        compact = compact_diagram(pathological)
        _, formatter_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    full = compact_diagram(after)
    delta = changed_diagram(before, after)
    return {
        "formatter_peak_traced_allocations_bytes": formatter_peak,
        "pathological_input_bytes": len(pathological.encode("utf-8")),
        "pathological_output_chars": len(compact.text),
        "full_observation_chars": len(full.text),
        "changed_only_chars": len(delta.text),
        "changed_only_char_reduction_percent":
            round(100 * (1 - len(delta.text) / len(full.text)), 1),
        "only_changed_lines": (
            "+ button New" in delta.text
            and "- button Old" in delta.text
            and "Section 1\n" not in delta.text
        ),
        "scope": "Python allocations, one formatter call; not process-tree RAM or LLM tokens",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Fail if formatter resource budgets regress")
    args = parser.parse_args(argv)
    metrics = run_benchmark()
    print(json.dumps(metrics, indent=2))
    if args.check and not (
        metrics["formatter_peak_traced_allocations_bytes"] < 256 * 1024
        and metrics["pathological_output_chars"] <= 2400
        and metrics["changed_only_chars"] <= 2400
        and metrics["changed_only_char_reduction_percent"] >= 50
        and metrics["only_changed_lines"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
