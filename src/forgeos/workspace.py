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
import tempfile
import threading


DEFAULT_DIFF_SUMMARY_CHARS = 4000
DEFAULT_DIFF_SUMMARY_FILES = 20
DEFAULT_DIFF_NUMSTAT_BYTES = 65536
DEFAULT_DIFF_PATCH_BYTES = 16384


class WorkspaceError(RuntimeError):
    """A Git workspace operation failed."""


@dataclass(frozen=True)
class Workspace:
    source: Path
    path: Path
    branch: str


@dataclass(frozen=True)
class WorkspaceDiffSummary:
    text: str
    files_changed: int
    insertions: int
    deletions: int
    truncated: bool
    max_chars: int
    max_files: int


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


def _git(cwd: Path, *args: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            [find_git(), *args], cwd=cwd, shell=False, text=True,
            capture_output=True, timeout=timeout, errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError(f"Git unavailable or timed out: {exc}") from exc
    if result.returncode:
        raise WorkspaceError(
            f"Git operation failed ({result.returncode}): {result.stderr[-2000:]}"
        )
    return result.stdout.strip()


def _git_bounded(
    cwd: Path,
    *args: str,
    max_bytes: int,
    timeout: float = 30,
) -> tuple[str, bool]:
    """Run a read-only Git command with shell=False and a hard stdout byte cap."""
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise ValueError("max_bytes must be a positive integer")
    try:
        proc = subprocess.Popen(
            [find_git(), *args],
            cwd=cwd,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise WorkspaceError(f"Git unavailable or timed out: {exc}") from exc

    state_lock = threading.Lock()
    finished = False
    timed_out = False

    def _on_timeout() -> None:
        nonlocal timed_out
        with state_lock:
            if finished or proc.poll() is not None:
                return
            timed_out = True
            try:
                proc.kill()
            except OSError:
                pass

    stderr_tail = b""

    def _drain_stderr() -> None:
        nonlocal stderr_tail
        assert proc.stderr is not None
        buf = bytearray()
        try:
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > 4096:
                    del buf[:-4096]
        except (OSError, ValueError):
            pass
        finally:
            stderr_tail = bytes(buf)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    timer = threading.Timer(timeout, _on_timeout)
    timer.daemon = True
    timer.start()

    chunks: list[bytes] = []
    total = 0
    truncated = False
    try:
        assert proc.stdout is not None
        while total <= max_bytes:
            to_read = min(8192, max_bytes + 1 - total)
            chunk = proc.stdout.read(to_read)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > max_bytes:
            truncated = True
            with state_lock:
                if not timed_out:
                    finished = True
                try:
                    proc.kill()
                except OSError:
                    pass
        proc.wait(timeout=5)
        with state_lock:
            finished = True
        stderr_thread.join(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        with state_lock:
            finished = True
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise WorkspaceError(f"Git unavailable or timed out: {exc}") from exc
    finally:
        with state_lock:
            finished = True
        timer.cancel()
        timer.join(timeout=1)
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
        stderr_thread.join(timeout=1)

    if timed_out:
        raise WorkspaceError(f"Git unavailable or timed out after {timeout}s")
    if not truncated and proc.returncode:
        err_text = stderr_tail.decode("utf-8", errors="replace")[-2000:]
        raise WorkspaceError(f"Git operation failed ({proc.returncode}): {err_text}")

    raw = b"".join(chunks)
    if truncated:
        raw = raw[:max_bytes]
    text = raw.decode("utf-8", errors="replace").strip()
    return text, truncated


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


def _fit_bounded_summary(body: str, max_chars: int, marker: str) -> str:
    """Fit summary text and a visible truncation marker within max_chars."""
    if len(body) + len(marker) <= max_chars:
        return body + marker
    if max_chars <= len(marker):
        short_marker = "[truncated]"
        if max_chars <= len(short_marker):
            return short_marker[:max_chars]
        keep = max_chars - len(short_marker)
        prefix = body[:keep].rstrip()
        return (prefix + short_marker)[:max_chars]
    keep = max_chars - len(marker)
    prefix = body[:keep].rstrip()
    if not prefix:
        return marker.lstrip()[:max_chars]
    return prefix + marker


def workspace_diff_summary(
    workspace: Workspace,
    *,
    max_chars: int = DEFAULT_DIFF_SUMMARY_CHARS,
    max_files: int = DEFAULT_DIFF_SUMMARY_FILES,
) -> WorkspaceDiffSummary:
    """Return a concise, hard-bounded summary of tracked workspace changes.

    Preserves workspace_diff() for full output. Any truncation due to max_chars,
    max_files, or Git output byte limits is recorded in ``truncated`` and
    marked visibly in ``text`` so partial diffs are never presented as complete.
    """
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    if not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 1:
        raise ValueError("max_files must be a positive integer")

    numstat_raw, numstat_truncated = _git_bounded(
        workspace.path,
        "diff",
        "--no-ext-diff",
        "--numstat",
        "--",
        max_bytes=DEFAULT_DIFF_NUMSTAT_BYTES,
    )
    if not numstat_raw and not numstat_truncated:
        empty_text = "No tracked changes."
        if len(empty_text) <= max_chars:
            return WorkspaceDiffSummary(
                text=empty_text,
                files_changed=0,
                insertions=0,
                deletions=0,
                truncated=False,
                max_chars=max_chars,
                max_files=max_files,
            )
        return WorkspaceDiffSummary(
            text=_fit_bounded_summary(
                empty_text,
                max_chars,
                f" [truncated; max_chars={max_chars}]",
            ),
            files_changed=0,
            insertions=0,
            deletions=0,
            truncated=True,
            max_chars=max_chars,
            max_files=max_files,
        )

    lines = numstat_raw.splitlines()
    if numstat_truncated and len(lines) > 1:
        # Drop potentially partial final line after byte truncation.
        lines = lines[:-1]

    entries: list[str] = []
    insertions = 0
    deletions = 0
    for line in lines:
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added_str, deleted_str, path = parts
        if added_str == "-" or deleted_str == "-":
            entries.append(f"  {path} (binary)")
        else:
            try:
                added = int(added_str)
                deleted = int(deleted_str)
            except ValueError:
                entries.append(f"  {path}")
            else:
                insertions += added
                deletions += deleted
                entries.append(f"  {path} (+{added}/-{deleted})")

    files_changed = len(entries)
    files_truncated = files_changed > max_files or numstat_truncated
    shown_entries = entries[:max_files]
    file_count_label = f"{files_changed}+" if numstat_truncated else str(files_changed)
    header = f"Files changed: {file_count_label} (+{insertions}/-{deletions})"
    sections = [header, *shown_entries]

    patch_budget = min(DEFAULT_DIFF_PATCH_BYTES, max(max_chars * 4, 1024))
    patch_raw, patch_truncated = _git_bounded(
        workspace.path,
        "diff",
        "--no-ext-diff",
        "--unified=1",
        "--",
        max_bytes=patch_budget,
    )
    if patch_raw:
        sections.append("")
        sections.append(patch_raw)

    body = "\n".join(sections)
    truncated = files_truncated or patch_truncated or len(body) > max_chars
    if truncated:
        reasons = []
        if files_truncated:
            reasons.append(f"files {len(shown_entries)}/{file_count_label}, max_files={max_files}")
        if patch_truncated:
            reasons.append(f"patch_bytes={patch_budget}")
        if len(body) > max_chars:
            reasons.append(f"max_chars={max_chars}")
        marker = f"\n... [truncated: {'; '.join(reasons)}]"
        text = _fit_bounded_summary(body, max_chars, marker)
    else:
        text = body

    return WorkspaceDiffSummary(
        text=text,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
        truncated=truncated,
        max_chars=max_chars,
        max_files=max_files,
    )


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
