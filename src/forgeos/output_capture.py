"""Memory-bounded test-output capture shared by host and Windows runners.

For outputs up to 32 MiB, the complete original can be streamed to ForgeOS's
existing content-addressed evidence store without ever loading it all in RAM.
Larger outputs are explicitly marked incomplete; autonomous repair must stop.
No command, exit code or containment permission is changed here.
"""
from __future__ import annotations

from collections import deque
import locale
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import time
from typing import BinaryIO

MAX_RAW_BYTES = 32 * 1024 * 1024
MAX_VISIBLE_BYTES = 2 * 1024 * 1024
HEAD_BYTES = 256 * 1024
TAIL_BYTES = 512 * 1024
CHUNK_BYTES = 64 * 1024


def _is_plain_path(path: Path, *, directory: bool) -> bool:
    """Reject reparse points, junctions and symlinks, without following them."""
    info = path.lstat()
    attrs = getattr(info, "st_file_attributes", 0)
    if attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return False
    return stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)


def validate_secure_output_path(root: Path, candidate: Path) -> Path:
    """Only a ForgeOS-owned, direct child output file may be monitored.

    Do not accept a caller-provided arbitrary host path or a path leading
    through a Windows junction. This executes before creating AppContainer.
    """
    from .test_runtime import RUNTIME_DIR
    root = Path(root).resolve(strict=True)
    if not isinstance(candidate, Path):
        raise ValueError("Secure output monitor requires a Path")
    # Windows Path.resolve() can canonicalize an 8.3/short parent name,
    # while the original snapshot path is still a valid spelling. Compare
    # the parent by canonical identity only after confirming the immediate
    # path segments have the exact ForgeOS-owned names and no reparse links.
    if (not candidate.is_absolute()
            or candidate.name not in {"test-output.txt", "pytest-log.txt"}
            or candidate.parent.name != RUNTIME_DIR):
        raise ValueError("Secure output monitor requires a ForgeOS-owned output path")
    if not _is_plain_path(candidate.parent.parent, directory=True):
        raise ValueError("Secure output root must be a plain directory")
    if not _is_plain_path(candidate.parent, directory=True):
        raise ValueError("Secure test output directory must be a plain directory")
    if candidate.parent.parent.resolve(strict=True) != root:
        raise ValueError("Secure output monitor requires the owned unittest output path")
    try:
        if not _is_plain_path(candidate, directory=False):
            raise ValueError("Secure test output may not be a link or special file")
    except FileNotFoundError:
        pass
    return candidate


def _watched_size(path: Path) -> int:
    """Reject link substitution, even if a child changes its output at runtime."""
    if not _is_plain_path(path.parent, directory=True):
        raise OSError("Watched output directory replaced with link or special file")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return 0  # The legitimate child has not created its file yet.
    attrs = getattr(metadata, "st_file_attributes", 0)
    if (not stat.S_ISREG(metadata.st_mode)
            or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise OSError("Watched output changed into link or special file")
    return metadata.st_size


def wait_native_output_bounded(process, job, stream, timeout: int, *,
                               kernel32, wait_timeout: int,
                               wait_object_0: int,
                               watched_files: tuple[Path, ...] = ()) -> int:
    """Check native stdout and explicit child-created file every ~500 ms.

    On any inaccessible, oversized or substituted watched output, terminate
    the contained Job Object and fail closed. Also check upon process exit,
    avoiding the old final-poll race.
    """
    if not isinstance(watched_files, tuple) or len(watched_files) > 2 or any(
            not isinstance(item, Path) for item in watched_files):
        raise ValueError("Invalid native output watch list")
    deadline = time.monotonic() + timeout

    def enforce_limit():
        try:
            size = os.fstat(stream.fileno()).st_size
            if size > MAX_RAW_BYTES:
                raise OSError("Native stdout exceeded 32 MiB disk limit")
            for candidate in watched_files:
                if _watched_size(candidate) > MAX_RAW_BYTES:
                    raise OSError("Child-generated test output exceeded 32 MiB disk limit")
        except OSError as exc:
            if not kernel32.TerminateJobObject(job, 125):
                raise OSError("Failed to terminate unsafe test output job") from exc
            kernel32.WaitForSingleObject(process, 5000)
            raise OSError("Test output disk limit/unsafe path: " + str(exc)) from exc

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            enforce_limit()
            return wait_timeout
        state = kernel32.WaitForSingleObject(
            process, min(500, max(1, int(remaining * 1000))))
        enforce_limit()
        if state != wait_timeout:
            return state
        if time.monotonic() >= deadline:
            return wait_timeout


def _sample(stream: BinaryIO, size: int, *, encoding: str) -> str:
    """Read only bounded head/tail bytes; report omission, never hide it."""
    if size <= MAX_VISIBLE_BYTES:
        stream.seek(0)
        return stream.read(size).decode(encoding, errors="replace")
    stream.seek(0)
    head = stream.read(HEAD_BYTES).decode(encoding, errors="replace")
    stream.seek(max(0, size - TAIL_BYTES))
    tail = stream.read(TAIL_BYTES).decode(encoding, errors="replace")
    omitted = size - HEAD_BYTES - TAIL_BYTES
    return (head + f"\n[ForgeOS preview: {omitted} original bytes omitted from"
            " AI context; use evidence recall when available]\n" + tail)


def summarize_output_file(stream: BinaryIO, *, encoding: str = "utf-8",
                          store=None) -> dict:
    """Bounded memory view of an existing native-process output file.

    The file may be large, but only a small preview is decoded. For a complete
    file <=32MiB, stream its original bytes into the existing evidence store.
    """
    from .diagnostics import EvidenceStore
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    if size < 0:
        raise OSError("Negative output file size")
    complete = size <= MAX_RAW_BYTES
    reference = None
    archive_error = None
    if complete and size > MAX_VISIBLE_BYTES:
        try:
            archive = store if store is not None else EvidenceStore()
            stream.seek(0)
            reference = archive.put_stream(stream)
        except (OSError, ValueError) as exc:
            archive_error = type(exc).__name__
            complete = False
    # Preserve TextIOWrapper's universal newline behavior on native Windows
    # outputs; raw archived bytes remain exact and SHA-256 verified.
    sample = _sample(stream, size, encoding=encoding).replace("\r\n", "\n")
    if size > MAX_RAW_BYTES:
        sample += (f"\n[ForgeOS warning: test output exceeded {MAX_RAW_BYTES} bytes;"
                   " full evidence unavailable, test result requires review]")
    elif archive_error:
        sample += ("\n[ForgeOS warning: original test evidence could not be"
                   " archived; test result requires review]")
    return {
        "output": sample,
        "capture_source_bytes": size,
        "capture_complete": complete,
        "output_preview_only": size > MAX_VISIBLE_BYTES,
        "evidence_reference": reference,
        "capture_error": archive_error,
    }


class BoundedSpool:
    """Disk-limited pipe sink; never retains a command's whole output in RAM."""

    def __init__(self, *, cap: int = MAX_RAW_BYTES):
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1024:
            raise ValueError("Invalid output spool limit")
        self.cap = cap
        self.total = 0
        self._written = 0
        self._tail_chunks = deque()
        self._tail_size = 0
        self.file = tempfile.TemporaryFile(mode="w+b")

    def feed(self, chunk: bytes) -> None:
        if not isinstance(chunk, bytes):
            raise TypeError("Spool accepts bytes only")
        self.total += len(chunk)
        remaining = self.cap - self._written
        if remaining > 0:
            part = chunk[:remaining]
            self.file.write(part)
            self._written += len(part)
        if self.total > self.cap:
            # Keep only a tiny deque of recent byte chunks. Removing old
            # blocks is O(1), unlike shifting a large bytearray per write.
            newest = chunk[-TAIL_BYTES:]
            self._tail_chunks.append(newest)
            self._tail_size += len(newest)
            while self._tail_size > TAIL_BYTES:
                first = self._tail_chunks[0]
                excess = self._tail_size - TAIL_BYTES
                if len(first) <= excess:
                    self._tail_chunks.popleft()
                    self._tail_size -= len(first)
                else:
                    self._tail_chunks[0] = first[excess:]
                    self._tail_size -= excess

    def finish(self, *, encoding: str = "utf-8", store=None) -> dict:
        self.file.flush()
        if self.total <= self.cap:
            return summarize_output_file(self.file, encoding=encoding, store=store)
        self.file.seek(0)
        head = self.file.read(min(HEAD_BYTES, self._written)).decode(
            encoding, errors="replace")
        tail = b"".join(self._tail_chunks).decode(encoding, errors="replace")
        omitted = max(0, self.total - min(HEAD_BYTES, self._written) - self._tail_size)
        return {
            "output": (head + f"\n[ForgeOS warning: {omitted} original output bytes omitted;"
                       " exceeded bounded disk capture; full evidence unavailable]\n" + tail),
            "capture_source_bytes": self.total,
            "capture_complete": False,
            "output_preview_only": True,
            "evidence_reference": None,
            "capture_error": "capture-limit-exceeded",
        }

    def close(self):
        self.file.close()


def run_streamed_process(argv, root: Path, timeout: int, *, env=None, store=None) -> dict:
    """Drain stdout/stderr incrementally, bounded by spool size and timeout."""
    capture = BoundedSpool()
    process = None
    reader = None
    read_error = []
    try:
        process = subprocess.Popen(
            argv, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, shell=False, env=env, bufsize=0,
        )

        def drain():
            try:
                while True:
                    block = process.stdout.read(CHUNK_BYTES)
                    if not block:
                        break
                    capture.feed(block)
            except (OSError, ValueError) as exc:
                read_error.append(exc)

        reader = threading.Thread(target=drain, name="forgeos-test-output", daemon=True)
        reader.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        reader.join(timeout=3)
        if reader.is_alive():
            process.stdout.close()
            reader.join(timeout=2)
            raise OSError("Test output stream did not close")
        if read_error:
            raise OSError(f"Test output read failed: {type(read_error[0]).__name__}")
        result = capture.finish(encoding=locale.getpreferredencoding(False) or "utf-8", store=store)
        return {
            "passed": returncode == 0,
            "returncode": returncode,
            "containment": "none",
            **result,
        }
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            if reader is not None and reader.is_alive():
                process.stdout.close()
                reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
        capture.close()
