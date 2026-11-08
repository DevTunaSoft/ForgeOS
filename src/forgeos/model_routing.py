"""Optional, deterministic local Ollama model selection; no paid providers.

Model file size is an imperfect proxy for runtime memory and capability.
No model is downloaded or executed during selection.
"""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import re
from typing import NamedTuple
from urllib.request import urlopen

LOCAL_OLLAMA = "http://127.0.0.1:11434"
ALLOWED_NAME = re.compile(r"[A-Za-z0-9._:/-]{1,128}\Z")
CODE_NAMES = ("coder", "code", "devstral", "starcoder", "codestral", "qwen", "deepseek", "gpt-oss")
HARD_TASKS = {"security", "race", "concurrent", "architecture", "refactor",
              "hardening", "fuzz", "integration", "complex", "crash", "recovery"}
DEFAULT_SIZE_LIMIT = 16 * 1024 * 1024 * 1024
MAX_CATALOG_BYTES = 2 * 1024 * 1024
GIB = 1024 ** 3


class MemorySnapshot(NamedTuple):
    total_bytes: int
    available_bytes: int


class ProcessMemorySnapshot(NamedTuple):
    """One-shot process working set, with optional private-commit measurement."""
    rss_bytes: int
    peak_rss_bytes: int
    private_bytes: int | None


def process_memory() -> ProcessMemorySnapshot | None:
    """Read *this* ForgeOS/Python process without agents or third-party deps.

    This does not include Ollama, test subprocesses, integrated GPU or VRAM.
    It must be called explicitly, never on a background polling loop.
    """
    if os.name == "nt":
        # PROCESS_MEMORY_COUNTERS_EX matches Psapi.h on 32-bit and 64-bit
        # Windows. Use c_size_t for all SIZE_T members.
        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]
        record = PROCESS_MEMORY_COUNTERS_EX()
        record.cb = ctypes.sizeof(record)
        try:
            kernel = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
            psapi.GetProcessMemoryInfo.restype = ctypes.c_int
            process = kernel.GetCurrentProcess()
            if psapi.GetProcessMemoryInfo(process, ctypes.byref(record), record.cb):
                return ProcessMemorySnapshot(
                    int(record.WorkingSetSize),
                    int(record.PeakWorkingSetSize),
                    int(record.PrivateUsage))
        except (AttributeError, OSError, ValueError):
            return None
        return None
    status = Path("/proc/self/status")
    if status.is_file():
        try:
            readings = {}
            with status.open("r", encoding="ascii") as stream:
                for line in stream:
                    if line.startswith(("VmRSS:", "VmHWM:")):
                        key, _, rest = line.partition(":")
                        readings[key] = int(rest.strip().split()[0]) * 1024
            current = readings.get("VmRSS")
            high = readings.get("VmHWM")
            if current is not None and high is not None:
                return ProcessMemorySnapshot(current, max(current, high), None)
        except (OSError, ValueError, IndexError):
            return None
    return None


def system_memory() -> MemorySnapshot | None:
    """Read native memory without third-party dependencies or background agents."""
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return MemorySnapshot(int(status.ullTotalPhys), int(status.ullAvailPhys))
        except (AttributeError, OSError, ValueError):
            return None
        return None
    # Linux can be memory-limited by a container. Never assume host memory
    # is the workload's available budget if a cgroup limit is smaller.
    if Path("/proc/meminfo").is_file():
        try:
            data = {}
            with Path("/proc/meminfo").open("r", encoding="ascii") as stream:
                for line in stream:
                    name, _, amount = line.partition(":")
                    if name in {"MemTotal", "MemAvailable"}:
                        data[name] = int(amount.strip().split()[0]) * 1024
            total, available = data.get("MemTotal"), data.get("MemAvailable")
            if total and available is not None:
                limit_file = Path("/sys/fs/cgroup/memory.max")
                current_file = Path("/sys/fs/cgroup/memory.current")
                try:
                    limit = limit_file.read_text(encoding="ascii").strip()
                    if limit.isdecimal() and current_file.is_file():
                        cap = int(limit)
                        consumed = int(current_file.read_text(encoding="ascii").strip())
                        if cap > 0:
                            total = min(total, cap)
                            available = min(available, max(0, cap - consumed))
                except (OSError, ValueError):
                    pass
                return MemorySnapshot(total, max(0, min(available, total)))
        except (OSError, ValueError):
            pass
    return None


def safe_model_file_budget(snapshot: MemorySnapshot | None) -> int:
    """Conservative preflight, not a guarantee about runtime/model KV-cache RAM."""
    if snapshot is None:
        # Unknown memory: prefer tiny installed models rather than guessing big.
        return 1024 ** 3
    total, free = snapshot
    if (type(total) is not int or type(free) is not int or total <= 0 or free < 0):
        raise ValueError("Invalid physical memory snapshot")
    free = min(total, free)
    reserve = max(768 * 1024 ** 2, min(2 * GIB, total // 4))
    usable = max(0, free - reserve)
    # File size underestimates quantized model overhead/KV cache; add headroom.
    return min(DEFAULT_SIZE_LIMIT, usable * 2 // 3)


def local_memory_settings() -> dict:
    """Prefer smaller context and automatic model unload on low-RAM devices."""
    snapshot = system_memory()
    total = snapshot.total_bytes if snapshot else 4 * GIB
    free = snapshot.available_bytes if snapshot else 2 * GIB
    if total <= 6 * GIB or free <= 2 * GIB:
        return {"num_ctx": 1024, "num_predict": 1024, "keep_alive": 0}
    if total <= 12 * GIB or free <= 4 * GIB:
        return {"num_ctx": 2048, "num_predict": 2048, "keep_alive": 0}
    if total <= 24 * GIB or free <= 8 * GIB:
        return {"num_ctx": 4096, "num_predict": 3072, "keep_alive": "5m"}
    return {"num_ctx": 8192, "num_predict": 4096, "keep_alive": "5m"}



def memory_report() -> dict:
    """One canonical local RAM report for CLI and GUI, on demand only."""
    system = system_memory()
    process = process_memory()
    return {
        "total_system_ram_bytes": system.total_bytes if system else None,
        "available_system_ram_bytes": system.available_bytes if system else None,
        "forgeos_process_rss_bytes": process.rss_bytes if process else None,
        "forgeos_process_peak_rss_bytes": process.peak_rss_bytes if process else None,
        "forgeos_process_private_bytes": process.private_bytes if process else None,
        "safe_local_model_file_budget_bytes": safe_model_file_budget(system),
        "local_model_settings": local_memory_settings(),
        "warning": "One-shot own-process reading; excludes Ollama/child process/GPU RAM",
    }


def installed_models(*, endpoint: str = LOCAL_OLLAMA) -> list[dict]:
    if endpoint != LOCAL_OLLAMA:
        raise ValueError("Only local Ollama model discovery is allowed")
    with urlopen(endpoint + "/api/tags", timeout=10) as response:
        raw = response.read(MAX_CATALOG_BYTES + 1)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError("Ollama model catalog exceeded memory safety limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Invalid local model catalog")
    return payload["models"]


def choose_installed_model(task: str, *, available: list[dict] | None = None,
                           endpoint: str = LOCAL_OLLAMA,
                           max_model_bytes: int = DEFAULT_SIZE_LIMIT,
                           memory_limit_bytes: int | None = None) -> str:
    """Select only from installed models using a stable, conservative heuristic."""
    if endpoint != LOCAL_OLLAMA:
        raise ValueError("Only local Ollama can be selected")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("Task specification is required")
    if type(max_model_bytes) is not int or max_model_bytes <= 0:
        raise ValueError("Model size limit must be positive")
    if memory_limit_bytes is not None:
        if type(memory_limit_bytes) is not int or memory_limit_bytes < 0:
            raise ValueError("Invalid memory budget")
        max_model_bytes = min(max_model_bytes, memory_limit_bytes)
    elif available is None:
        max_model_bytes = min(max_model_bytes, safe_model_file_budget(system_memory()))
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
            "No installed local Ollama model fits the safe memory budget; "
            "install a smaller model or free system RAM")
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
