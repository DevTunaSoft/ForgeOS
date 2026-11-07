"""Opt-in Docker isolation; never silently fall back to host execution."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

PRESETS = {
    "python-unittest": ["python", "-m", "unittest", "discover"],
    "python-pytest": ["python", "-m", "pytest"],
}
IMAGE = "python:3.12-slim"


def isolated_test(project: str, preset: str, timeout: int = 180) -> dict:
    if preset not in PRESETS:
        raise ValueError("Isolated runner currently supports Python test presets only")
    root = Path(project).expanduser().resolve(strict=True)
    if not root.is_dir() or not (root / ".git").exists():
        raise ValueError("Not a local Git worktree")
    if not shutil.which("docker"):
        return {"passed": False, "returncode": None,
                "output": "Docker unavailable; refusing host execution"}
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           "--pids-limit", "128", "--memory", "512m", "--cpus", "1",
           "--user", "65534:65534",
           "--mount", f"type=bind,source={root},target=/workspace,readonly",
           "--tmpfs", "/tmp:rw,nosuid,noexec,size=64m",
           "-w", "/workspace", "-e", "PYTHONDONTWRITEBYTECODE=1",
           IMAGE, *PRESETS[preset]]
    try:
        result = subprocess.run(cmd, shell=False, capture_output=True,
                                text=True, timeout=timeout, errors="replace")
        return {"passed": result.returncode == 0, "returncode": result.returncode,
                "output": (result.stdout + "\n" + result.stderr)[-16000:]}
    except subprocess.TimeoutExpired:
        return {"passed": False, "returncode": None,
                "output": f"Isolated test timed out after {timeout}s"}
    except OSError as exc:
        return {"passed": False, "returncode": None,
                "output": f"Isolated runner unavailable: {exc}"}
