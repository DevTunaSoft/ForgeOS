"""Fast, local-only readiness checks before starting a development task."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from .workspace import WorkspaceError, find_git


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    message: str


def check_readiness(*, project: str, workspace_parent: str, backend: str,
                    model: str, endpoint: str = "") -> list[Check]:
    """Check prerequisites without running user code or changing files."""
    results = []
    try:
        executable = find_git()
        results.append(Check("Git", True, f"Found Git: {executable}"))
    except WorkspaceError:
        results.append(Check("Git", False,
                             "Git not found. Install Git for Windows or GitHub Desktop, then restart ForgeOS."))

    source = Path(project).expanduser()
    if not project.strip() or not source.is_dir():
        results.append(Check("Project", False, "Select an existing project folder."))
    elif not (source / ".git").exists():
        results.append(Check("Project", False,
                             "This folder is not a Git repository. Initialize it with GitHub Desktop."))
    else:
        results.append(Check("Project", True, "Git repository folder exists."))

    parent = Path(workspace_parent).expanduser()
    if not workspace_parent.strip() or not parent.is_dir():
        results.append(Check("Workspaces", False,
                             "Select an existing workspace folder outside your project."))
    elif source.resolve() == parent.resolve() or source.resolve() in parent.resolve().parents or parent.resolve() in source.resolve().parents:
        results.append(Check("Workspaces", False,
                             "Workspace folder must be outside the project tree."))
    else:
        results.append(Check("Workspaces", True, "Workspace folder exists."))

    if backend == "ollama":
        if endpoint not in ("", "http://127.0.0.1:11434"):
            results.append(Check("Ollama", False,
                                 "Clear the endpoint field. Ollama uses http://127.0.0.1:11434."))
        else:
            try:
                request = Request("http://127.0.0.1:11434/api/tags")
                with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
                    payload = response.read(1024 * 1024 + 1)
                if len(payload) > 1024 * 1024:
                    raise ValueError("Model list is too large")
                models = json.loads(payload)["models"]
                names = {item["name"] for item in models if isinstance(item, dict) and isinstance(item.get("name"), str)}
                if model not in ("auto", "qualified-auto") and model not in names:
                    results.append(Check("Ollama", False,
                                         f"Ollama is running, but model {model!r} is not installed. Download it in Ollama."))
                elif not names:
                    results.append(Check("Ollama", False, "Ollama has no installed models."))
                else:
                    results.append(Check("Ollama", True, "Ollama responds and a model is available."))
            except (OSError, URLError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                results.append(Check("Ollama", False,
                                     "Cannot reach local Ollama. Start Ollama and verify its model installation."))
    else:
        results.append(Check("Local AI", True,
                             "Other local backend: connection is checked when the model starts."))
    return results
