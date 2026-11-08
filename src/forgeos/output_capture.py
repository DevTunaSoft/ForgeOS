"""Memory-bounded test-output capture shared by host and Windows runners.

For outputs up to 32 MiB, the complete original can be streamed to ForgeOS's
existing content-addressed evidence store without ever loading it all in RAM.
Larger outputs are explicitly marked incomplete; autonomous repair must stop.
No command, exit code or containment permission is changed here.
"""
from __future__ import annotations

import locale
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import BinaryIO

MAX_RAW_BYTES = 32 * 1024 * 1024
MAX_VISIBLE_BYTES = 2 * 1024 * 1024
HEAD_BYTES = 256 * 1024
TAIL_BYTES = 512 * 1024
CHUNK_BYTES = 64 * 1024


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
        self._tail = bytearray()
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
            self._tail.extend(chunk)
            if len(self._tail) > TAIL_BYTES:
                del self._tail[:-TAIL_BYTES]

    def finish(self, *, encoding: str = "utf-8", store=None) -> dict:
        self.file.flush()
        if self.total <= self.cap:
            return summarize_output_file(self.file, encoding=encoding, store=store)
        self.file.seek(0)
        head = self.file.read(min(HEAD_BYTES, self._written)).decode(
            encoding, errors="replace")
        tail = bytes(self._tail).decode(encoding, errors="replace")
        omitted = max(0, self.total - min(HEAD_BYTES, self._written) - len(self._tail))
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
