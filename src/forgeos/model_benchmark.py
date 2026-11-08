"""One-shot managed-model baseline evidence for ForgeOS issue #55."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import uuid

from .model_routing import GIB, system_memory

SCHEMA_VERSION = 1
QUANTIZATION = re.compile(
    r"(?:^|[-_.])(Q[2-8](?:_[A-Z0-9]+)+)(?:[-_.]|$)", re.I)


def ram_tier(total_bytes: int | None) -> str:
    """Classify physical RAM using the fixed #55 matrix."""
    if total_bytes is None:
        return "unknown"
    if type(total_bytes) is not int or total_bytes <= 0:
        raise ValueError("Invalid total RAM")
    if total_bytes < 6 * GIB:
        return "4-gib-gui-investigation"
    if total_bytes < 12 * GIB:
        return "8-gib"
    if total_bytes < 24 * GIB:
        return "16-gib"
    return "32-gib"


def _quantization(filename: str) -> str | None:
    match = QUANTIZATION.search(filename)
    return match.group(1).upper() if match else None


def capture_baseline(controller) -> dict:
    """Capture one secret-free identity/resource/structured-output snapshot."""
    identity = controller.benchmark_identity()
    model = identity["model"]
    snapshot = system_memory()
    self_test = controller.inference_self_test()
    return {
        "schema": SCHEMA_VERSION,
        "kind": "managed-model-baseline",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "qualification_status": "baseline_only_not_qualified",
        "model": {
            **model,
            "quantization_from_filename": _quantization(model["file"]),
            "quantization_verified": False,
            "license_id": None,
            "license_status": "not_recorded_in_download_manifest",
        },
        "runtime": identity["runtime"],
        "settings": identity["settings"],
        "hardware": {
            "ram_tier": ram_tier(snapshot.total_bytes if snapshot else None),
            "total_ram_bytes": snapshot.total_bytes if snapshot else None,
            "available_ram_bytes_at_capture":
                snapshot.available_bytes if snapshot else None,
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "logical_cpu_count": os.cpu_count(),
        },
        "measurement": {
            "startup_elapsed_seconds": identity["startup_elapsed_seconds"],
            "self_test_elapsed_seconds": self_test["elapsed_seconds"],
            "self_test_response_bytes": self_test["response_bytes"],
            "self_test_status": self_test["status"],
            "managed_memory": self_test.get("memory"),
        },
        "limitations": [
            "Baseline evidence is not model qualification.",
            "License and quantization require pinned metadata verification.",
            "No GPU/shared-memory or tokens-per-second measurement is implied.",
        ],
    }


def write_baseline(controller, directory: str | Path | None = None) -> dict:
    """Persist a unique bounded JSON record without overwriting prior evidence."""
    record = capture_baseline(controller)
    root = (Path(directory).expanduser() if directory is not None
            else Path.home() / ".forgeos" / "benchmarks")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise OSError("Benchmark evidence directory is unsafe")
    if os.name != "nt":
        root.chmod(0o700)
    token = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / (
        f"baseline-{stamp}-{record['model']['sha256'][:12]}-{token[:8]}.json")
    temporary = root / ("." + target.name + "." + token + ".tmp")
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if len(payload.encode("utf-8")) > 64 * 1024:
        raise RuntimeError("Benchmark baseline record exceeds bounded size")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if target.exists():
            raise FileExistsError("Benchmark evidence target unexpectedly exists")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"path": str(target), "record": record}
