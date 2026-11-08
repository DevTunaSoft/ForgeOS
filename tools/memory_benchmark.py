"""Standalone synthetic ForgeOS memory test; stdlib only, no Ollama or cloud.

Usage: python tools/memory_benchmark.py --mib 12 --check
Measures traced Python allocations, not process-tree RSS or GPU VRAM.
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc

from forgeos.diagnostics import EvidenceStore
from forgeos.model_routing import (
    local_memory_settings, process_memory, safe_model_file_budget, system_memory,
)
from forgeos.output_capture import run_streamed_process


def main(argv=None):
    parser = argparse.ArgumentParser(description="ForgeOS synthetic output memory benchmark")
    parser.add_argument("--mib", type=int, default=12)
    parser.add_argument("--cycles", type=int, default=1,
                        help="Repeat test capture 1..10 times and report residual RAM")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.mib <= 30:
        parser.error("--mib must be 1..30")
    if not 1 <= args.cycles <= 10:
        parser.error("--cycles must be 1..10")
    code = (
        "import sys\n"
        "block = b'INFO ForgeOS synthetic benchmark\\n' * 2048\n"
        f"remaining = {args.mib} * 1024 * 1024\n"
        "while remaining > 0:\n"
        "    chunk = block[:min(remaining, len(block))]\n"
        "    sys.stdout.buffer.write(chunk)\n"
        "    remaining -= len(chunk)\n"
    )
    before = process_memory()
    readings = []
    all_passed = True
    overall_peak = 0
    with tempfile.TemporaryDirectory() as temp:
        evidence = EvidenceStore(Path(temp) / "archive")
        start = time.perf_counter()
        for cycle in range(args.cycles):
            gc.collect()
            tracemalloc.start()
            tracemalloc.reset_peak()
            try:
                report = run_streamed_process(
                    [sys.executable, "-c", code], Path(temp),
                    timeout=90, store=evidence)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            all_passed = all_passed and (
                report["capture_complete"] and
                bool(report["evidence_reference"]) and
                report["returncode"] == 0 and
                peak < 32 * 1024 * 1024)
            overall_peak = max(overall_peak, peak)
            gc.collect()
            reading = process_memory()
            readings.append({
                "cycle": cycle + 1,
                "rss_bytes_after_gc": reading.rss_bytes if reading else None,
                "python_tracemalloc_peak_bytes": peak,
            })
        elapsed = round(time.perf_counter() - start, 3)
        snapshot = system_memory()
        after = process_memory()
        data = {
            "source_bytes": report["capture_source_bytes"],
            "in_memory_preview_chars": len(report["output"]),
            "complete": report["capture_complete"],
            "archive_recoverable": bool(report["evidence_reference"]),
            "test_exit_code": report["returncode"],
            "python_tracemalloc_peak_bytes": overall_peak,
            "cycles_run": args.cycles,
            "cycle_readings": readings,
            "process_rss_bytes_before": before.rss_bytes if before else None,
            "process_rss_bytes_after": after.rss_bytes if after else None,
            "process_peak_rss_bytes": after.peak_rss_bytes if after else None,
            "process_private_bytes_after": after.private_bytes if after else None,
            "elapsed_seconds": elapsed,
            "total_ram_bytes": snapshot.total_bytes if snapshot else None,
            "available_ram_bytes": snapshot.available_bytes if snapshot else None,
            "conservative_model_file_budget": safe_model_file_budget(snapshot),
            "local_model_settings": local_memory_settings(),
            "scope": "Process RSS is only this Python process; no Ollama, children or VRAM",
        }
        print(json.dumps(data, indent=2))
        if args.check and not all_passed:
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
