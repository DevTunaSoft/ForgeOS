"""Run ForgeOS's own test suite from the packaged app, inside the secure sandbox.

windows-exe.yml bundles the test modules as data. Running them uses the same
zero-capability runner as project tests, so the self-test exercises the sandbox
as well as the code. Modules that read repository files or spawn the Python
interpreter cannot run from the packaged app. The report lists each one with its
reason. They still run in source mode (CI and the PC unit suite).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from pathlib import Path

BUNDLE_NAME = "forgeos_selftest"
SELF_TEST_TIMEOUT_SECONDS = 420

EXCLUDED_MODULES = {
    "test_acceptance.py": "spawns the Python interpreter",
    "test_appcontainer.py": "runs a Python interpreter inside AppContainer",
    "test_build_info.py": "imports repository build tooling",
    "test_cloud_supervisor.py": "imports repository scripts",
    "test_secure_run_sibling_acls.py": "launches an AppContainer; nested AppContainers are not allowed",
    "test_secure_run_deep_paths.py": "measures host path limits, not product behaviour",
    "test_managed_llama.py": "spawns the Python interpreter",
    "test_memory_benchmark.py": "spawns the Python interpreter",
    "test_output_capture.py": "spawns the Python interpreter",
    "test_windows_containment.py": "spawns the Python interpreter",
    "test_ci_policy.py": "reads repository files",
    "test_documentation.py": "reads repository files",
    "test_enhancement_audit.py": "reads repository files",
    "test_handoff_check.py": "reads repository files",
    "test_handoff_pdf_wrapping.py": "reads repository files",
    "test_handoff_snapshot.py": "reads repository files",
    "test_handoff_takeover.py": "reads repository files",
    "test_multillm_roadmap.py": "reads repository files",
    "test_package_handoff.py": "reads repository files",
}

_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_OUTCOME = re.compile(r"^(OK|FAILED)(?: \((.*)\))?$", re.MULTILINE)


# A diagnostic module generated only for the packaged self-test. It always fails
# and reports which folders the sandboxed child can write, so the cause shows in
# the report's failure list and output tail.
PROBE_NAME = "test_zz_packaged_sandbox_probe.py"
_PROBE_SOURCE = '''\
import os
import tempfile
import unittest
from pathlib import Path


def _code(exc):
    return type(exc).__name__ + "/" + str(getattr(exc, "winerror", ""))


class PackagedSandboxProbe(unittest.TestCase):
    def test_report_sandbox_directories(self):
        notes = []
        for name in ("TEMP", "TMP", "HOME", "LOCALAPPDATA", "APPDATA", "cwd"):
            value = os.getcwd() if name == "cwd" else os.environ.get(name, "")
            if not value:
                notes.append(name + "=unset")
                continue
            try:
                probe = Path(value) / "forgeos-write-probe.txt"
                probe.write_text("x", encoding="ascii")
                probe.unlink()
                notes.append(name + "=file-ok")
            except OSError as exc:
                notes.append(name + "=" + _code(exc))
        try:
            folder = tempfile.mkdtemp(prefix="forgeos-probe-")
            os.rmdir(folder)
            notes.append("mkdtemp=ok")
        except OSError as exc:
            notes.append("mkdtemp=" + _code(exc))
        self.fail("PROBE " + " ".join(notes))
'''


def bundled_tests_root() -> Path:
    """Return the folder that holds the bundled test modules."""
    base = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not base:
        raise RuntimeError("The self-test runs only from the packaged ForgeOS app")
    root = Path(base) / BUNDLE_NAME
    if (root / "tests").is_dir():
        root = root / "tests"
    if not any(root.glob("test_*.py")):
        raise RuntimeError("The packaged app carries no test modules")
    return root


def parse_unittest_output(text: str) -> dict:
    """Read the unittest summary. Missing summary is never a pass."""
    ran = _RAN.findall(text)
    outcomes = _OUTCOME.findall(text)
    outcome, details = outcomes[-1] if outcomes else ("", "")
    return {
        "tests_run": int(ran[-1]) if ran else None,
        "outcome": outcome or None,
        "outcome_details": details,
    }


_SEPARATOR = "=" * 70
_SUMMARY_PREFIXES = ("Ran ", "OK", "FAILED")
_PATH = re.compile(r"[A-Za-z]:[\\/][^\s'\"]*")


def summarize_failures(text: str, group_limit: int = 25) -> dict:
    """Group failing tests by the last line of their traceback."""
    causes: dict[str, dict] = {}
    names: list[str] = []
    for block in text.split(_SEPARATOR)[1:]:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines or not lines[0].startswith(("ERROR: ", "FAIL: ")):
            continue
        name = lines[0].split(": ", 1)[1]
        names.append(name)
        body = [line for line in lines[1:]
                if not set(line.strip()) <= {"-"}
                and not line.startswith(_SUMMARY_PREFIXES)]
        cause = _PATH.sub("<path>", body[-1] if body else "unknown")[:240]
        entry = causes.setdefault(cause, {"count": 0, "example": name})
        entry["count"] += 1
    ranked = sorted(causes.items(), key=lambda item: -item[1]["count"])[:group_limit]
    return {
        "failing_tests": len(names),
        "causes": [{"count": v["count"], "cause": k, "example": v["example"]}
                   for k, v in ranked],
        "first_failing_tests": names[:40],
    }


def stage_test_modules(source: Path, target: Path) -> dict:
    """Copy the runnable test modules and their helpers into a fresh folder."""
    target.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []
    excluded: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(source)
        name = relative.as_posix()
        if len(relative.parts) == 1 and name in EXCLUDED_MODULES:
            excluded[name] = EXCLUDED_MODULES[name]
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        if len(relative.parts) == 1 and name.startswith("test_") and path.suffix == ".py":
            copied.append(name)
    return {"modules_run": copied, "modules_excluded": excluded}


def _trace_from_snapshot(runtime) -> dict:
    """Read the child's start-of-test trace through the verified output path."""
    from .output_capture import open_verified_output
    from .test_runtime import RUNTIME_DIR
    output_file = runtime.path / RUNTIME_DIR / "test-output.txt"
    try:
        with open_verified_output(runtime.path, output_file) as stream:
            text = stream.read(4_000_000).decode("utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        return {"trace_error": type(exc).__name__ + ": " + str(exc)[:200]}
    started = [line for line in text.splitlines() if line.startswith("STARTED ")]
    return {"last_started": started[-3:], "trace_tail": text[-3000:]}


def run_self_test(report_path: Path) -> int:
    """Run the bundled suite in the secure sandbox and write a JSON report."""
    from .secure_runner import run_secure_windows_test
    from .test_runtime import (
        create_windows_low_integrity_snapshot, remove_test_snapshot,
        secure_runtime_root)

    report: dict = {
        "suite": "ForgeOS packaged tests",
        "runner": "secure AppContainer",
        "passed": False,
    }
    workspace = None
    try:
        secure_root = secure_runtime_root()
        workspace = secure_root / f"self-test-{uuid.uuid4().hex}"
        report.update(stage_test_modules(bundled_tests_root(), workspace))
        (workspace / PROBE_NAME).write_text(_PROBE_SOURCE, encoding="utf-8")
        report["diagnostic_probe"] = PROBE_NAME
        runtime = create_windows_low_integrity_snapshot(
            workspace, runtime_root=secure_root)
        runtime.env["FORGEOS_SELF_TEST_TRACE"] = "1"
        try:
            try:
                result = run_secure_windows_test(
                    runtime, "python-unittest", timeout=SELF_TEST_TIMEOUT_SECONDS)
            except BaseException:
                report.update(_trace_from_snapshot(runtime))
                raise
        finally:
            remove_test_snapshot(workspace, runtime_root=secure_root)
        output = result.get("output") if isinstance(result.get("output"), str) else ""
        report.update(parse_unittest_output(output))
        report.update(summarize_failures(output))
        report["sandbox_passed"] = result.get("passed") is True
        report["passed"] = report["sandbox_passed"] and report.get("outcome") == "OK"
        report["output_tail"] = output[-4000:]
    except BaseException as exc:  # the report must record every failure
        report["error"] = type(exc).__name__ + ": " + str(exc)[:500]
    finally:
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
        try:
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError:
            pass
    return 0 if report.get("passed") is True else 1
