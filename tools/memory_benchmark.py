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
from forgeos.model_routing import local_memory_settings, safe_model_file_budget, system_memory
from forgeos.output_capture import run_streamed_process


def main(argv=None):
    parser = argparse.ArgumentParser(description="ForgeOS synthetic output memory benchmark")
    parser.add_argument("--mib", type=int, default=12)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.mib <= 30:
        parser.error("--mib must be 1..30")
    code = (
        "import sys\n"
        "block = b'INFO ForgeOS synthetic benchmark\\n' * 2048\n"
        f"remaining = {args.mib} * 1024 * 1024\n"
        "while remaining > 0:\n"
        "    chunk = block[:min(remaining, len(block))]\n"
        "    sys.stdout.buffer.write(chunk)\n"
        "    remaining -= len(chunk)\n"
    )
    with tempfile.TemporaryDirectory() as temp:
        evidence = EvidenceStore(Path(temp) / "archive")
        gc.collect()
        tracemalloc.start()
        start = time.perf_counter()
        try:
            report = run_streamed_process(
                [sys.executable, "-c", code], Path(temp),
                timeout=90, store=evidence)
            seconds = round(time.perf_counter() - start, 3)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        memory = system_memory()
        data = {
            "source_bytes": report["capture_source_bytes"],
            "in_memory_preview_chars": len(report["output"]),
            "complete": report["capture_complete"],
            "archive_recoverable": bool(report["evidence_reference"]),
            "test_exit_code": report["returncode"],
            "python_tracemalloc_peak_bytes": peak,
            "elapsed_seconds": seconds,
            "total_ram_bytes": memory.total_bytes if memory else None,
            "available_ram_bytes": memory.available_bytes if memory else None,
            "conservative_model_file_budget": safe_model_file_budget(memory),
            "local_model_settings": local_memory_settings(),
            "scope": "Peak traced Python allocations only; no Ollama/GPU/GUI RSS",
        }
        print(json.dumps(data, indent=2))
        if args.check and (
            not report["capture_complete"] or not report["evidence_reference"]
            or report["returncode"] != 0 or peak >= 32 * 1024 * 1024
        ):
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
