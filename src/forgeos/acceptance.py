"""Bounded, explicit local acceptance evidence for ForgeOS issue #56."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import uuid

SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 128 * 1024
HASH_CHUNK_BYTES = 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _source_commit() -> str | None:
    """Return Git HEAD only when tracked checkout content matches it."""
    if getattr(sys, "frozen", False):
        return None
    source = Path(__file__).resolve()
    seen = set()
    for root in source.parents[:4]:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        key = str(resolved)
        if key in seen or not (resolved / ".git").exists():
            continue
        seen.add(key)
        try:
            result = subprocess.run(
                ["git", "-C", str(resolved), "rev-parse", "--verify", "HEAD"],
                check=False, capture_output=True, text=True, timeout=5,
                stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue
        value = result.stdout.strip().lower()
        if result.returncode == 0 and len(value) == 40:
            try:
                int(value, 16)
            except ValueError:
                continue
            try:
                clean = subprocess.run(
                    ["git", "-C", str(resolved), "diff", "--quiet",
                     "--no-ext-diff", value, "--"],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                return None
            return value if clean.returncode == 0 else None
    return None


def _embedded_build_commit() -> str | None:
    """Read the packaged immutable source SHA without trusting environment variables."""
    bundle = getattr(sys, "_MEIPASS", None)
    if not bundle:
        return None
    path = Path(bundle) / "forgeos" / "build_info.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    value = payload.get("source_commit") if isinstance(payload, dict) else None
    if not isinstance(value, str) or len(value) != 40:
        return None
    value = value.lower()
    try:
        int(value, 16)
    except ValueError:
        return None
    return value


def _frozen_identity() -> dict:
    frozen = bool(getattr(sys, "frozen", False))
    result = {"frozen_executable": frozen, "executable_sha256": None,
              "executable_bytes": None, "build_source_commit": None}
    if not frozen:
        return result
    result["build_source_commit"] = _embedded_build_commit()
    path = Path(sys.executable)
    try:
        if path.is_file() and not path.is_symlink():
            result["executable_bytes"] = path.stat().st_size
            result["executable_sha256"] = _sha256_file(path)
    except OSError:
        pass
    return result


def _host_snapshot() -> dict:
    from .model_routing import memory_report
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "processor": platform.processor() or None,
        "memory": memory_report(),
    }


def _managed_snapshot(controller) -> dict | None:
    if controller is None:
        return None
    if not controller.running():
        raise RuntimeError("Managed model controller is not running")
    identity = controller.benchmark_identity()
    self_test = controller.inference_self_test()
    try:
        memory = controller.memory_report()
    except (OSError, RuntimeError, ValueError) as exc:
        memory = {"error": type(exc).__name__ + ": " + str(exc)}
    # Explicit allowlist prevents endpoint credentials or future controller
    # fields from leaking into acceptance evidence.
    model = identity.get("model")
    runtime = identity.get("runtime")
    settings = identity.get("settings")
    if not isinstance(model, dict) or not isinstance(runtime, dict) or not isinstance(settings, dict):
        raise ValueError("Managed benchmark identity is incomplete")
    return {
        "model": {
            key: model.get(key) for key in
            ("repo", "revision", "file", "bytes", "sha256")
        },
        "runtime": {
            key: runtime.get(key) for key in ("sha256", "release_tag")
        },
        "settings": {
            key: settings.get(key) for key in
            ("context_tokens", "threads", "memory_ceiling_bytes")
        },
        "startup_elapsed_seconds": identity.get("startup_elapsed_seconds"),
        "self_test": {
            key: self_test.get(key) for key in
            ("status", "elapsed_seconds", "response_bytes", "memory")
        },
        "memory_after_self_test": memory,
    }


def collect_acceptance_snapshot(controller=None, *, physical_target: bool = False) -> dict:
    """Collect one explicit local snapshot; no project files or secrets."""
    if type(physical_target) is not bool:
        raise ValueError("physical_target must be boolean")
    frozen = _frozen_identity()
    managed = _managed_snapshot(controller)
    windows = platform.system() == "Windows"
    if physical_target and not windows:
        raise ValueError("Physical target acceptance can only be asserted on Windows")
    return {
        "schema": SCHEMA_VERSION,
        "kind": "forgeos-local-acceptance-snapshot",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "windows_host_evidence": windows,
        "physical_windows_target_asserted": windows and physical_target,
        "source_commit": _source_commit(),
        "artifact": frozen,
        "host": _host_snapshot(),
        "managed_model": managed,
        "scope": {
            "current_machine_only": True,
            "project_source_collected": False,
            "credentials_collected": False,
            "background_sampling": False,
        },
        "limitations": [
            "This snapshot is evidence from the current machine, not a release sign-off.",
            "A source commit is absent when ForgeOS is not running from a Git checkout.",
            "A frozen executable SHA-256 is absent for source-mode execution.",
            "Managed-model evidence is absent unless a verified ForgeOS-managed model is running.",
            "LM Studio/Ollama/other backend acceptance requires separate real-runtime exercises.",
        ],
    }


def write_acceptance_snapshot(controller=None, directory: str | Path | None = None,
                              *, physical_target: bool = False) -> dict:
    """Atomically write one bounded acceptance record and return path + record."""
    root = (Path(directory).expanduser() if directory is not None
            else Path.home() / ".forgeos" / "acceptance")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise OSError("Acceptance evidence directory is unsafe")
    if os.name != "nt":
        root.chmod(0o700)
    record = collect_acceptance_snapshot(
        controller, physical_target=physical_target)
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    encoded = payload.encode("utf-8")
    if len(encoded) > MAX_RECORD_BYTES:
        raise RuntimeError("Acceptance evidence record exceeds bounded size")
    token = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = root / f"acceptance-{stamp}-{token[:8]}.json"
    temporary = root / ("." + target.name + "." + token + ".tmp")
    try:
        with temporary.open("xb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {"path": str(target), "record": record}
