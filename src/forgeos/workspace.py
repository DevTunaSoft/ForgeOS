"""Isolated Git workspaces for future autonomous edits.

This module never modifies the source checkout. It does not sandbox code execution:
tests/builds in a worktree can still access the host.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import threading


class WorkspaceError(RuntimeError):
    """A Git workspace operation failed."""


MAX_GIT_OUTPUT_BYTES = 1024 * 1024
DEFAULT_DIFF_PREVIEW_BYTES = 256 * 1024


@dataclass(frozen=True)
class Workspace:
    source: Path
    path: Path
    branch: str


def _github_desktop_git(local_app_data: str | Path) -> str | None:
    """Locate bundled Git across GitHub Desktop's versioned installations."""
    desktop = Path(local_app_data) / "GitHubDesktop"
    candidates = []
    for app in desktop.glob("app-*"):
        candidate = app / "resources" / "app" / "git" / "cmd" / "git.exe"
        if candidate.is_file():
            candidates.append(candidate)
    if not candidates:
        return None
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def find_git() -> str:
    """Find Git on PATH or bundled with GitHub Desktop on Windows."""
    on_path = shutil.which("git")
    if on_path:
        return on_path
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            bundled = _github_desktop_git(local)
            if bundled:
                return bundled
    raise WorkspaceError(
        "Git executable not found. Install Git for Windows or GitHub Desktop, "
        "then restart ForgeOS."
    )


def _git(cwd: Path, *args: str, timeout: int = 30,
         max_output_bytes: int = MAX_GIT_OUTPUT_BYTES,
         truncate_output: bool = False,
         truncation_marker: str = "") -> str:
    """Run Git with bounded captured output and a hard process timeout."""
    if (type(timeout) is not int or timeout < 1
            or type(max_output_bytes) is not int
            or not 1024 <= max_output_bytes <= MAX_GIT_OUTPUT_BYTES
            or type(truncate_output) is not bool
            or not isinstance(truncation_marker, str)):
        raise ValueError("Invalid Git output or timeout limit")
    executable = find_git()
    try:
        process = subprocess.Popen(
            [executable, *args], cwd=cwd, shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as exc:
        raise WorkspaceError(f"Git unavailable or timed out: {exc}") from exc
    captured = bytearray()
    overflow = threading.Event()
    read_errors = []

    def drain_output():
        try:
            while True:
                part = process.stdout.read(64 * 1024)
                if not part:
                    return
                remaining = max_output_bytes - len(captured)
                if len(part) > remaining:
                    captured.extend(part[:remaining])
                    overflow.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
                captured.extend(part)
        except OSError as exc:
            read_errors.append(exc)
            try:
                process.kill()
            except OSError:
                pass

    reader = threading.Thread(
        target=drain_output, name="forgeos-git-output", daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        reader.join()
        raise WorkspaceError(f"Git operation timed out after {timeout}s") from exc
    reader.join()
    if process.stdout is not None:
        process.stdout.close()
    if read_errors:
        raise WorkspaceError(f"Git output could not be read: {read_errors[0]}")
    if overflow.is_set():
        if not truncate_output:
            raise WorkspaceError(
                f"Git output exceeded the {max_output_bytes}-byte safety limit")
        marker = truncation_marker.encode("utf-8")
        if len(marker) >= max_output_bytes:
            raise ValueError("Git truncation marker exceeds output budget")
        del captured[max_output_bytes - len(marker):]
        captured.extend(marker)
    output = captured.decode("utf-8", errors="replace").strip()
    if returncode:
        raise WorkspaceError(
            f"Git operation failed ({returncode}): {output[-2000:]}"
        )
    return output


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


def workspace_diff(workspace: Workspace, *,
                   max_bytes: int = DEFAULT_DIFF_PREVIEW_BYTES) -> str:
    """Show a bounded tracked diff preview without changing the workspace."""
    if type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_GIT_OUTPUT_BYTES:
        raise ValueError("Diff preview limit must be 1 KiB..1 MiB")
    marker = (
        "\n[ForgeOS: diff preview truncated at its byte limit; "
        "inspect selected files for the complete patch]\n")
    return _git(
        workspace.path, "--literal-pathspecs", "diff", "--no-ext-diff",
        "--no-textconv", "--no-color", "--",
        max_output_bytes=max_bytes, truncate_output=True,
        truncation_marker=marker)


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
