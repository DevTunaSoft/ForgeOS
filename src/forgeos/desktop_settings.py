"""Bounded, local, nonsecret desktop preferences; no background I/O."""
import json
import os
from pathlib import Path
import tempfile

DEFAULTS = {
    "project": "", "model": "auto", "task_role": "implementation",
    "backend": "ollama", "endpoint": "", "preset": "python-unittest",
    "workspaces": "", "repair_count": 2,
}
ALLOWED_BACKENDS = ("ollama", "llama.cpp", "lm-studio", "openai-local")
ALLOWED_PRESETS = ("python-unittest", "python-pytest", "npm-test",
                   "cargo-test", "go-test", "dotnet-test")
ALLOWED_ROLES = ("implementation", "planning", "review", "testing", "debugging")


def _sanitize(values):
    if not isinstance(values, dict):
        return {}
    cleaned = {}
    for key in ("project", "model", "endpoint", "workspaces"):
        value = values.get(key)
        if isinstance(value, str) and len(value) <= 2048 and "\x00" not in value:
            cleaned[key] = value
    for key, choices in (("backend", ALLOWED_BACKENDS),
                         ("preset", ALLOWED_PRESETS)):
        if values.get(key) in choices:
            cleaned[key] = values[key]
    role = values.get("task_role")
    if isinstance(role, str) and len(role) <= 64 and "\x00" not in role:
        cleaned["task_role"] = role
    retries = values.get("repair_count")
    if type(retries) is int and 0 <= retries <= 5:
        cleaned["repair_count"] = retries
    return cleaned


def load_settings(path: Path) -> dict:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
            return {}
        return _sanitize(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeError):
        return {}


def save_settings(path: Path, values: dict) -> None:
    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or path.is_symlink():
        raise ValueError("Refusing to write desktop settings through a symlink")
    data = json.dumps(_sanitize(values), indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".desktop-settings-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
