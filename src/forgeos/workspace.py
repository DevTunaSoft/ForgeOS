"""Isolated Git workspaces for future autonomous edits.

This module never modifies the source checkout. It does not sandbox code execution:
tests/builds in a worktree can still access the host.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import tempfile


class WorkspaceError(RuntimeError):
    """A Git workspace operation failed."""


@dataclass(frozen=True)
class Workspace:
    source: Path
    path: Path
    branch: str


def _git(cwd: Path, *args: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, shell=False, text=True,
            capture_output=True, timeout=timeout, errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError(f"Git unavailable or timed out: {exc}") from exc
    if result.returncode:
        raise WorkspaceError(
            f"Git operation failed ({result.returncode}): {result.stderr[-2000:]}"
        )
    return result.stdout.strip()


def _repository_root(project: str | Path) -> Path:
    path = Path(project).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Project is not a directory")
    root = Path(_git(path, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if root != path:
        raise ValueError("Select the repository root, not a subdirectory")
    if _git(root, "rev-parse", "--is-bare-repository") != "false":
        raise ValueError("Bare repositories are unsupported")
    return root


def create_workspace(project: str | Path, task_id: int, destination: str | Path) -> Workspace:
    """Create a new branch and worktree in a *new* empty destination.

    No arbitrary branch names, Git arguments or paths are supplied by AI text.
    Dirty source checkout is permitted because the worktree uses HEAD.
    """
    if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id < 1:
        raise ValueError("task_id must be a positive integer")
    source = _repository_root(project)
    target = Path(destination).expanduser().resolve()
    if target == source or source in target.parents or target in source.parents:
        raise ValueError("Workspace must be outside the source repository tree")
    if target.exists():
        raise ValueError("Workspace destination already exists")
    if not target.parent.is_dir():
        raise ValueError("Workspace parent directory does not exist")
    branch = f"forgeos/task-{task_id}"
    # Avoid racing with another invocation for the same task.
    if _git(source, "branch", "--list", branch):
        raise ValueError("Task branch already exists")
    try:
        _git(source, "worktree", "add", "-b", branch, "--", str(target), "HEAD", timeout=90)
    except Exception:
        # Git may leave an empty directory on failure. Never delete a populated
        # directory here: it might contain user data created concurrently.
        raise
    return Workspace(source=source, path=target, branch=branch)


def workspace_diff(workspace: Workspace) -> str:
    """Show tracked changes without staging or committing anything."""
    return _git(workspace.path, "diff", "--no-ext-diff", "--")


def release_workspace(workspace: Workspace) -> None:
    """Remove only an unchanged, clean workspace; retain its task branch."""
    source = _repository_root(workspace.source)
    target = workspace.path.resolve(strict=True)
    listed = _git(source, "worktree", "list", "--porcelain")
    registered = []
    for line in listed.splitlines():
        if line.startswith("worktree "):
            # Git emits POSIX separators even on Windows. Normalize both paths
            # using the host OS before comparing; never use substring matching.
            registered.append(os.path.normcase(os.path.normpath(line[9:])))
    if os.path.normcase(os.path.normpath(str(target))) not in registered:
        raise WorkspaceError("Destination is not a registered worktree")
    if _git(target, "status", "--porcelain", "--untracked-files=all"):
        raise WorkspaceError("Workspace has changes; refusing to delete user work")
    _git(source, "worktree", "remove", "--", str(target), timeout=90)
