"""Run ForgeOS's own test suite from the packaged app, inside the secure sandbox.

windows-exe.yml bundles the test modules as data. Each module runs as its own
secure run through the same zero-capability runner that runs project tests, so a
module that hangs is recorded as a timeout and the rest still run. Modules that
read repository files, spawn the Python interpreter, or launch an AppContainer
cannot run from the packaged app. The report lists each one with its reason.
They still run in source mode (CI and the PC unit suite).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from subprocess import TimeoutExpired

BUNDLE_NAME = "forgeos_selftest"
MODULE_TIMEOUT_SECONDS = 180

EXCLUDED_MODULES = {
    "test_acceptance.py": "spawns the Python interpreter",
    "test_appcontainer.py": "runs a Python interpreter inside AppContainer",
    "test_build_info.py": "imports repository build tooling",
    "test_ci_policy.py": "reads repository files",
    "test_cloud_supervisor.py": "imports repository scripts",
    "test_documentation.py": "reads repository files",
    "test_enhancement_audit.py": "reads repository files",
    "test_handoff_check.py": "reads repository files",
    "test_handoff_pdf_wrapping.py": "reads repository files",
    "test_handoff_snapshot.py": "reads repository files",
    "test_handoff_takeover.py": "reads repository files",
    "test_managed_llama.py": "spawns the Python interpreter",
    "test_memory_benchmark.py": "spawns the Python interpreter",
    "test_multillm_roadmap.py": "reads repository files",
    "test_output_capture.py": "spawns the Python interpreter",
    "test_package_handoff.py": "reads repository files",
    "test_secure_run_deep_paths.py": "measures host path limits, not product behaviour",
    "test_secure_run_sibling_acls.py": "launches an AppContainer; nested AppContainers are not allowed",
    "test_windows_containment.py": "spawns the Python interpreter",
}

_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_OUTCOME = re.compile(r"^(OK|FAILED)(?: \((.*)\))?$", re.MULTILINE)
_STARTED = re.compile(r"STARTED (\S+)")

# A diagnostic module generated only for the packaged self-test. It always fails
# and reports, for each folder a test would use, its ACL and the file and folder
# operations the sandboxed child can perform there, so the cause shows in its
# output tail. Each check is independent, so one failure does not hide the rest.
PROBE_NAME = "test_zz_packaged_sandbox_probe.py"
PROBE_TAIL_CHARS = 16_000
_PROBE_SOURCE = '''\
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

SYSTEM = os.environ.get("SystemRoot", "C:/Windows") + "/System32/"
ICACLS = SYSTEM + "icacls.exe"
WHOAMI = SYSTEM + "whoami.exe"


def _code(exc):
    return (type(exc).__name__ + "/w" + str(getattr(exc, "winerror", "-"))
            + "/e" + str(getattr(exc, "errno", "-")))


def _op(label, action):
    try:
        action()
        return label + "=ok"
    except Exception as exc:
        return label + "=" + _code(exc)


def _run(args):
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              errors="replace", timeout=30)
        return done.stdout + done.stderr
    except Exception as exc:
        return "run-error " + _code(exc)


def _acl(label, folder):
    prefix = str(folder)
    kept = []
    for line in _run([ICACLS, prefix]).splitlines():
        if line.startswith(prefix):
            line = label + line[len(prefix):]
        line = " ".join(line.split())
        if line and "processed" not in line:
            kept.append(line)
    return "ACL " + label + ": " + " / ".join(kept)


def _identity():
    groups = _run([WHOAMI, "/groups"])
    user = _run([WHOAMI, "/user"])
    kept = [" ".join(line.split()) for line in groups.splitlines()
            if any(tag in line for tag in ("S-1-15-", "S-1-16-", "Label"))]
    kept += [" ".join(line.split()) for line in user.splitlines() if "S-1-" in line]
    if groups.startswith("run-error") or user.startswith("run-error"):
        kept.append(groups[:200] + " " + user[:200])
    return "IDENTITY " + " / ".join(kept)


def _write_and_remove(folder):
    probe = folder / "forgeos-write-probe.txt"
    probe.write_text("x", encoding="ascii")
    probe.unlink()


def _mkdir(path):
    path.mkdir()
    return path


def _inspect(label, folder):
    """Dump one folder's ACL, then try what a test does inside it."""
    notes = [_acl(label, folder)]
    notes.append(_op(label + ".mkdir_sub", (folder / "sub").mkdir))
    notes.append(_op(label + ".write",
                     lambda: (folder / "f.txt").write_text("x", encoding="ascii")))
    notes.append(_op(label + ".scandir", lambda: list(os.scandir(folder))))
    notes.append(_op(label + ".chmod", lambda: os.chmod(folder, 0o700)))
    notes.append(_op(label + ".rmtree", lambda: shutil.rmtree(folder)))
    return notes


class PackagedSandboxProbe(unittest.TestCase):
    def test_report_sandbox_directories(self):
        notes = []
        cwd = Path(os.getcwd())
        temp_value = os.environ.get("TEMP", "")
        temp = Path(temp_value) if temp_value else cwd
        for name in ("TEMP", "TMP", "HOME", "LOCALAPPDATA", "APPDATA"):
            value = os.environ.get(name, "")
            if value:
                notes.append(_op(name + ".write",
                                 lambda value=value: _write_and_remove(Path(value))))
            else:
                notes.append(name + "=unset")
        notes.append(_op("cwd.write", lambda: _write_and_remove(cwd)))
        locations = [
            ("root", lambda: _mkdir(cwd / "probe-root")),
            ("plain", lambda: _mkdir(temp / "probe-plain")),
            ("mkdtemp", lambda: Path(tempfile.mkdtemp(dir=str(temp)))),
        ]
        for label, make in locations:
            try:
                folder = make()
            except Exception as exc:
                notes.append(label + ".create=" + _code(exc))
                continue
            notes.extend(_inspect(label, folder))
        chain = [temp] + list(temp.parents)[:4]
        for name, folder in zip(("temp", "local", "appdata", "home", "runtime"), chain):
            notes.append(_acl(name, folder))
        notes.append(_acl("cwd", cwd))
        notes.append(_identity())

        def temp_cleanup():
            folder = tempfile.TemporaryDirectory(dir=str(temp))
            (Path(folder.name) / "inner").mkdir()
            folder.cleanup()

        notes.append(_op("tempdir_cleanup", temp_cleanup))
        outcome = {}

        def run_keygen():
            try:
                from forgeos.recovery_auth import generate_recovery_bundle_auth_key
                generate_recovery_bundle_auth_key(cwd / "probe-key.bin", size=48)
                outcome["keygen"] = "ok"
            except BaseException as exc:
                outcome["keygen"] = type(exc).__name__ + ":" + str(exc)[:120]

        worker = threading.Thread(target=run_keygen, daemon=True)
        worker.start()
        worker.join(20)
        notes.append("keygen=" + outcome.get("keygen", "timeout"))
        self.fail("PROBE " + " ; ".join(notes))
'''

_SEPARATOR = "=" * 70
_SUMMARY_PREFIXES = ("Ran ", "OK", "FAILED")
_PATH = re.compile(r"[A-Za-z]:[\\/][^\s'\"]*")


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
    started = _STARTED.findall(text)
    return {"last_started": started[-3:], "trace_tail": text[-1500:]}


def _run_module(runtime, module: str) -> tuple[dict, dict]:
    """Run one module in its own secure run. A failure is recorded, not raised."""
    from .secure_runner import run_secure_windows_test
    runtime.env["FORGEOS_SELF_TEST_MODULE"] = module
    try:
        result = run_secure_windows_test(
            runtime, "python-unittest", timeout=MODULE_TIMEOUT_SECONDS)
    except Exception as exc:
        entry = {
            "module": module,
            "passed": False,
            "timed_out": isinstance(exc, TimeoutExpired),
            "error": type(exc).__name__ + ": " + str(exc)[:300],
            "tests_run": None,
            "outcome": None,
        }
        entry.update(_trace_from_snapshot(runtime))
        return entry, summarize_failures("")
    output = result.get("output") if isinstance(result.get("output"), str) else ""
    parsed = parse_unittest_output(output)
    sandbox_passed = result.get("passed") is True
    entry = {
        "module": module,
        "passed": sandbox_passed and parsed["outcome"] == "OK",
        "timed_out": False,
        "tests_run": parsed["tests_run"],
        "outcome": parsed["outcome"],
        "outcome_details": parsed["outcome_details"],
        "sandbox_passed": sandbox_passed,
    }
    if module == PROBE_NAME:
        entry["output_tail"] = output[-PROBE_TAIL_CHARS:]
    elif not entry["passed"]:
        entry["output_tail"] = output[-800:]
    return entry, summarize_failures(output)


def _aggregate_causes(summaries: list[dict], limit: int = 25) -> list[dict]:
    totals: dict[str, dict] = {}
    for summary in summaries:
        for item in summary["causes"]:
            entry = totals.setdefault(
                item["cause"], {"count": 0, "example": item["example"]})
            entry["count"] += item["count"]
    ranked = sorted(totals.items(), key=lambda pair: -pair[1]["count"])[:limit]
    return [{"count": v["count"], "cause": k, "example": v["example"]}
            for k, v in ranked]


def run_self_test(report_path: Path) -> int:
    """Run each bundled module in its own secure run and write a JSON report."""
    from .test_runtime import (
        create_windows_low_integrity_snapshot, remove_test_snapshot,
        secure_runtime_root)

    report: dict = {
        "suite": "ForgeOS packaged tests",
        "runner": "secure AppContainer, one run per module",
        "passed": False,
    }
    workspace = None
    try:
        secure_root = secure_runtime_root()
        workspace = secure_root / f"self-test-{uuid.uuid4().hex}"
        staged = stage_test_modules(bundled_tests_root(), workspace)
        (workspace / PROBE_NAME).write_text(_PROBE_SOURCE, encoding="utf-8")
        report.update(staged)
        report["diagnostic_probe"] = PROBE_NAME
        runtime = create_windows_low_integrity_snapshot(
            workspace, runtime_root=secure_root)
        runtime.env["FORGEOS_SELF_TEST_TRACE"] = "1"
        try:
            results: list[dict] = []
            summaries: list[dict] = []
            for module in staged["modules_run"]:
                entry, summary = _run_module(runtime, module)
                results.append(entry)
                summaries.append(summary)
            probe_entry, _ = _run_module(runtime, PROBE_NAME)
        finally:
            remove_test_snapshot(workspace, runtime_root=secure_root)
        report["module_results"] = results
        report["modules_passed"] = sum(1 for e in results if e["passed"])
        report["modules_failed"] = [e["module"] for e in results if not e["passed"]]
        report["modules_timed_out"] = [e["module"] for e in results if e["timed_out"]]
        report["tests_run"] = sum(e["tests_run"] or 0 for e in results)
        report["failing_tests"] = sum(s["failing_tests"] for s in summaries)
        report["causes"] = _aggregate_causes(summaries)
        report["probe"] = {
            "module": PROBE_NAME,
            "output_tail": probe_entry.get("output_tail", ""),
            "error": probe_entry.get("error"),
        }
        report["passed"] = bool(results) and not report["modules_failed"]
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
