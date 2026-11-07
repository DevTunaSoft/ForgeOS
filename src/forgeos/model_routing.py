"""Optional, deterministic local Ollama model selection; no paid providers.

Model file size is an imperfect proxy for runtime memory and capability.
No model is downloaded or executed during selection.
"""
from __future__ import annotations
import json
import re
from urllib.request import urlopen

LOCAL_OLLAMA = "http://127.0.0.1:11434"
ALLOWED_NAME = re.compile(r"[A-Za-z0-9._:/-]{1,128}\Z")
CODE_NAMES = ("coder", "code", "devstral", "starcoder", "codestral", "qwen", "deepseek", "gpt-oss")
HARD_TASKS = {"security", "race", "concurrent", "architecture", "refactor",
              "hardening", "fuzz", "integration", "complex", "crash", "recovery"}
DEFAULT_SIZE_LIMIT = 16 * 1024 * 1024 * 1024


def installed_models(*, endpoint: str = LOCAL_OLLAMA) -> list[dict]:
    if endpoint != LOCAL_OLLAMA:
        raise ValueError("Only local Ollama model discovery is allowed")
    with urlopen(endpoint + "/api/tags", timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Invalid local model catalog")
    return payload["models"]


def choose_installed_model(task: str, *, available: list[dict] | None = None,
                           endpoint: str = LOCAL_OLLAMA,
                           max_model_bytes: int = DEFAULT_SIZE_LIMIT) -> str:
    """Select only from installed models using a stable, conservative heuristic."""
    if endpoint != LOCAL_OLLAMA:
        raise ValueError("Only local Ollama can be selected")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("Task specification is required")
    if type(max_model_bytes) is not int or max_model_bytes <= 0:
        raise ValueError("Model size limit must be positive")
    catalog = installed_models(endpoint=endpoint) if available is None else available
    if not isinstance(catalog, list):
        raise ValueError("Invalid installed model list")
    candidates = []
    for item in catalog:
        if not isinstance(item, dict):
            continue
        name, size = item.get("name"), item.get("size")
        if (not isinstance(name, str) or not ALLOWED_NAME.fullmatch(name)
                or type(size) is not int or size <= 0 or size > max_model_bytes):
            continue
        candidates.append((name, size))
    if not candidates:
        raise RuntimeError(
            "No suitable installed Ollama model within the configured size limit")
    coder = [m for m in candidates if any(term in m[0].lower() for term in CODE_NAMES)]
    if coder:
        candidates = coder
    candidates.sort(key=lambda item: (item[1], item[0].lower()))
    lowered = task.lower()
    importance = sum(bool(re.search(r"\b" + re.escape(term) + r"\b", lowered))
                     for term in HARD_TASKS)
    if importance >= 2 or len(task) >= 700:
        chosen = candidates[-1]
    elif importance >= 1 or len(task) >= 220:
        chosen = candidates[len(candidates) // 2]
    else:
        chosen = candidates[0]
    return chosen[0]
