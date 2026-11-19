#!/usr/bin/env python3
"""Windows probe: does a folder grant rewrite its children, and what must it grant?

The secure runner grants the AppContainer read and execute on the snapshot's
parent folder and on the runner's folder. Those are often the user's folders,
so a grant there must leave every child exactly as it was. This probe answers
four questions on hosted Windows:

  1. Control. Does icacls /grant (non-recursive) change a child's security
     descriptor on a plain fixture? (G0+R0) If not, the other results prove
     nothing.
  2. Shipped writer. Does appcontainer._grant/_revoke_directory_read_execute,
     the writer that ships, leave the children and the folder unchanged through
     a grant and a revoke? (PROD+PROD)
  3. Alternatives, for the record. SetNamedSecurityInfoW with DACL only
     (G1+R1), with UNPROTECTED_DACL added (G2+R1), and icacls revoke on a
     folder that was never granted (none+R0).
  4. Production path. Does a real secure run beside a sibling project leave the
     parent folder, the sibling and the workspace unchanged? Does the sandboxed
     process still read, list and fail to read as expected? (PROD and F-*)

A child is compared by its full security descriptor (SDDL), so a change to the
auto-inherited flag counts even when the ACE list reads the same.

Windows only. Writes a JSON report and prints a summary. With --assert the exit
status is nonzero unless the required checks hold.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forgeos import appcontainer  # noqa: E402

USERS_SID = "S-1-5-32-545"
CHILDREN = ("child", "inner", "sibling")
DACL_SECURITY_INFORMATION = 0x00000004
UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
SE_FILE_OBJECT = 1
SDDL_REVISION_1 = 1
GRANT_ACCESS = 1
REVOKE_ACCESS = 4
NO_INHERITANCE = 0
TRUSTEE_IS_SID = 0
TRUSTEE_IS_UNKNOWN = 0
READ_EXECUTE_MASK = 0x001200A9  # icacls RX

if os.name == "nt":
    class TRUSTEE_W(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", ctypes.c_void_p),
            ("MultipleTrusteeOperation", ctypes.c_int),
            ("TrusteeForm", ctypes.c_int),
            ("TrusteeType", ctypes.c_int),
            ("ptstrName", ctypes.c_void_p),
        ]

    class EXPLICIT_ACCESS_W(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", wintypes.DWORD),
            ("grfAccessMode", ctypes.c_int),
            ("grfInheritance", wintypes.DWORD),
            ("Trustee", TRUSTEE_W),
        ]

    ADVAPI = ctypes.WinDLL("advapi32", use_last_error=True)
    KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
    ADVAPI.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    ADVAPI.GetNamedSecurityInfoW.restype = wintypes.DWORD
    ADVAPI.SetEntriesInAclW.argtypes = [
        wintypes.DWORD, ctypes.POINTER(EXPLICIT_ACCESS_W),
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    ADVAPI.SetEntriesInAclW.restype = wintypes.DWORD
    ADVAPI.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    ADVAPI.SetNamedSecurityInfoW.restype = wintypes.DWORD
    ADVAPI.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    ADVAPI.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = (
        wintypes.BOOL)
    KERNEL.LocalFree.argtypes = [ctypes.c_void_p]
    KERNEL.LocalFree.restype = ctypes.c_void_p


def _check(status: int, what: str) -> None:
    if status != 0:
        raise OSError(f"{what} failed with Win32 error {status}")


def sddl(path: Path) -> str:
    """Return the object's full DACL as SDDL, where (ID) marks inherited ACEs."""
    descriptor = ctypes.c_void_p()
    _check(ADVAPI.GetNamedSecurityInfoW(
        str(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        None, None, None, None, ctypes.byref(descriptor)),
        f"GetNamedSecurityInfoW({path})")
    try:
        text = wintypes.LPWSTR()
        length = wintypes.DWORD()
        if not ADVAPI.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, SDDL_REVISION_1, DACL_SECURITY_INFORMATION,
                ctypes.byref(text), ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            KERNEL.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        KERNEL.LocalFree(descriptor)


def _rewrite_dacl(path: Path, sid, *, grant: bool, unprotected: bool) -> None:
    """Characterization writer: SetNamedSecurityInfoW, optionally UNPROTECTED."""
    descriptor = ctypes.c_void_p()
    current = ctypes.c_void_p()
    _check(ADVAPI.GetNamedSecurityInfoW(
        str(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        None, None, ctypes.byref(current), None, ctypes.byref(descriptor)),
        f"read DACL of {path}")
    try:
        if not current.value:
            raise OSError(f"{path} has no DACL")
        entry = EXPLICIT_ACCESS_W()
        entry.grfAccessPermissions = READ_EXECUTE_MASK if grant else 0
        entry.grfAccessMode = GRANT_ACCESS if grant else REVOKE_ACCESS
        entry.grfInheritance = NO_INHERITANCE
        entry.Trustee.TrusteeForm = TRUSTEE_IS_SID
        entry.Trustee.TrusteeType = TRUSTEE_IS_UNKNOWN
        entry.Trustee.ptstrName = sid.value
        new_dacl = ctypes.c_void_p()
        _check(ADVAPI.SetEntriesInAclW(
            1, ctypes.byref(entry), current, ctypes.byref(new_dacl)),
            f"SetEntriesInAclW({path})")
        try:
            flags = DACL_SECURITY_INFORMATION
            if unprotected:
                flags |= UNPROTECTED_DACL_SECURITY_INFORMATION
            _check(ADVAPI.SetNamedSecurityInfoW(
                str(path), SE_FILE_OBJECT, flags, None, None, new_dacl, None),
                f"SetNamedSecurityInfoW({path})")
        finally:
            KERNEL.LocalFree(new_dacl)
    finally:
        KERNEL.LocalFree(descriptor)


def _icacls_grant(path: Path, sid, sid_string: str) -> None:
    appcontainer._icacls(path, "/grant", f"*{sid_string}:(RX)", "/Q")


def _icacls_revoke(path: Path, sid, sid_string: str) -> None:
    appcontainer._icacls(path, "/remove:g", f"*{sid_string}", "/Q")


def _named_grant(path: Path, sid, sid_string: str) -> None:
    _rewrite_dacl(path, sid, grant=True, unprotected=False)


def _named_revoke(path: Path, sid, sid_string: str) -> None:
    _rewrite_dacl(path, sid, grant=False, unprotected=False)


def _named_unprotected_grant(path: Path, sid, sid_string: str) -> None:
    _rewrite_dacl(path, sid, grant=True, unprotected=True)


def _shipped_grant(path: Path, sid, sid_string: str) -> None:
    appcontainer._grant_directory_read_execute(path, sid)


def _shipped_revoke(path: Path, sid, sid_string: str) -> None:
    appcontainer._revoke_directory_read_execute(path, sid)


SEQUENCES = (
    ("G0+R0", _icacls_grant, _icacls_revoke),
    ("PROD+PROD", _shipped_grant, _shipped_revoke),
    ("G1+R1", _named_grant, _named_revoke),
    ("G1+R0", _named_grant, _icacls_revoke),
    ("G2+R1", _named_unprotected_grant, _named_revoke),
    ("none+R0", None, _icacls_revoke),
    ("none+R1", None, _named_revoke),
)
FUNCTIONAL = (
    ("F-none", None, None),
    ("F-deny", None, None),
    ("F-PROD", _shipped_grant, _shipped_revoke),
)


def build_fixture(base: Path, index: int) -> dict[str, Path]:
    """The folder shape of the measured probe: plain creation, no ACL writes."""
    root = base / f"fixture-{index}"
    parent = root / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    inner = child / "inner.txt"
    inner.write_text("inner", encoding="ascii")
    sibling = parent / "sibling.txt"
    sibling.write_text("sibling", encoding="ascii")
    return {"parent": parent, "child": child,
            "inner": inner, "sibling": sibling}


def run_sequence(base: Path, index: int, name: str, sid, sid_string: str,
                 grant, revoke) -> dict:
    paths = build_fixture(base, index)
    keys = ("parent",) + CHILDREN
    row = {"sequence": name, "grant_error": "", "revoke_error": ""}
    before = {key: sddl(paths[key]) for key in keys}
    if grant is not None:
        try:
            grant(paths["parent"], sid, sid_string)
        except OSError as exc:
            row["grant_error"] = str(exc)[-300:]
    after_grant = {key: sddl(paths[key]) for key in keys}
    try:
        revoke(paths["parent"], sid, sid_string)
    except OSError as exc:
        row["revoke_error"] = str(exc)[-300:]
    after_revoke = {key: sddl(paths[key]) for key in keys}
    row["changed_by_grant"] = {
        key: before[key] != after_grant[key] for key in CHILDREN}
    row["changed_by_revoke"] = {
        key: after_grant[key] != after_revoke[key] for key in CHILDREN}
    row["changed_overall"] = {
        key: before[key] != after_revoke[key] for key in CHILDREN}
    row["parent_changed_by_grant"] = before["parent"] != after_grant["parent"]
    row["parent_restored"] = before["parent"] == after_revoke["parent"]
    row["sddl"] = {"before": before, "after_grant": after_grant,
                   "after_revoke": after_revoke}
    return row


def child_exit(sid, command: str, command_text: str, cwd: Path) -> dict:
    try:
        launched = appcontainer._launch_appcontainer_process(
            sid, [command, "/d", "/c", command_text], cwd=cwd, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": None, "error": str(exc)[-200:]}
    return {"returncode": launched["returncode"], "error": ""}


def _stage(sid, command: str, snap: Path, parent: Path, app: Path,
           sibling: Path) -> dict:
    # Output goes into the snapshot, which the child may write, so a failed
    # read or listing is the only reason for a nonzero exit.
    return {
        "read_snap": child_exit(
            sid, command, f"type {app} > {snap / 'out-app.txt'}", snap),
        "read_sibling": child_exit(
            sid, command, f"type {sibling} > {snap / 'out-sibling.txt'}",
            snap),
        "list_parent": child_exit(
            sid, command, f"dir /b {parent} > {snap / 'out-list.txt'}", snap),
    }


def functional_sequence(base: Path, index: int, name: str, sid,
                        sid_string: str, command: str, grant, revoke,
                        *, deny: bool = False) -> dict:
    root = base / f"functional-{index}"
    parent = root / "parent"
    snap = parent / "snap"
    snap.mkdir(parents=True)
    app = snap / "app.txt"
    app.write_text("app", encoding="ascii")
    sibling = parent / "sibling.txt"
    sibling.write_text("sibling", encoding="ascii")
    # The snapshot gets the same recursive modify grant the secure runner uses.
    appcontainer._icacls(
        snap, "/grant", f"*{sid_string}:(OI)(CI)(M)", "/T", "/Q")
    row = {"sequence": name, "grant_error": "", "revoke_error": "",
           "deny_error": "", "stages": {}}
    row["stages"]["initial"] = _stage(sid, command, snap, parent, app, sibling)
    if deny:
        try:
            appcontainer._icacls(
                parent, "/deny", f"*{sid_string}:(X)", "/Q")
        except OSError as exc:
            row["deny_error"] = str(exc)[-300:]
        row["stages"]["denied"] = _stage(
            sid, command, snap, parent, app, sibling)
    if grant is not None:
        try:
            grant(parent, sid, sid_string)
        except OSError as exc:
            row["grant_error"] = str(exc)[-300:]
        row["stages"]["granted"] = _stage(
            sid, command, snap, parent, app, sibling)
    if revoke is not None:
        try:
            revoke(parent, sid, sid_string)
        except OSError as exc:
            row["revoke_error"] = str(exc)[-300:]
        row["stages"]["revoked"] = _stage(
            sid, command, snap, parent, app, sibling)
    return row


def production_check(base: Path) -> dict:
    """Run the shipped secure path beside a sibling project and compare ACLs.

    This is the parent-directory check for the code that ships: the workspace's
    parent folder holds another project, and no object there may change.
    """
    from forgeos.secure_runner import _bootstrap_environment
    from forgeos.test_runtime import (
        create_windows_low_integrity_snapshot, remove_test_snapshot)

    root = base / "production"
    projects = root / "projects"
    sibling = projects / "other-project"
    sibling.mkdir(parents=True)
    (sibling / "notes.txt").write_text("keep", encoding="ascii")
    workspace = projects / "ForgeOS"
    workspace.mkdir()
    (workspace / "app.txt").write_text("workspace", encoding="ascii")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    cmd = str(bin_dir / "cmd.exe")
    shutil.copy2(
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                     "System32", "cmd.exe"), cmd)
    watched = {"projects": projects, "sibling": sibling,
               "notes": sibling / "notes.txt", "workspace": workspace}
    before = {key: sddl(path) for key, path in watched.items()}
    result = {"passed": None, "error": ""}
    try:
        runtime = create_windows_low_integrity_snapshot(workspace)
        try:
            outcome = appcontainer.run_secure_appcontainer(
                [cmd, "/c", "exit 0"], runtime.path,
                _bootstrap_environment(runtime), executable=cmd, timeout=60)
            result["passed"] = outcome["passed"]
        finally:
            remove_test_snapshot(workspace)
    except Exception as exc:  # recorded for the report, never hidden
        result["error"] = f"{type(exc).__name__}: {exc}"[-300:]
    after = {key: sddl(path) for key, path in watched.items()}
    result["changed"] = {key: before[key] != after[key] for key in watched}
    result["before"] = before
    result["after"] = after
    return result


def run_probe(base: Path) -> dict:
    report = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "icacls": shutil.which("icacls.exe"),
        "fixtures": [],
        "functional": [],
    }
    command = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    with appcontainer._temporary_probe_profile() as (_profile, sid):
        sid_string = appcontainer._sid_to_string(sid)
        for index, (name, grant, revoke) in enumerate(SEQUENCES):
            report["fixtures"].append(run_sequence(
                base, index, name, sid, sid_string, grant, revoke))
        for index, (name, grant, revoke) in enumerate(FUNCTIONAL):
            report["functional"].append(functional_sequence(
                base, 100 + index, name, sid, sid_string, command, grant,
                revoke, deny=(name == "F-deny")))
    report["production"] = production_check(base)
    return report


def _blocked(stage: dict) -> bool:
    return isinstance(stage["returncode"], int) and stage["returncode"] != 0


def _allowed(stage: dict) -> bool:
    return stage["returncode"] == 0


def acceptance(report: dict) -> dict:
    fixtures = {row["sequence"]: row for row in report["fixtures"]}
    functional = {row["sequence"]: row for row in report["functional"]}
    production = report["production"]
    control = fixtures["G0+R0"]
    shipped = fixtures["PROD+PROD"]
    none = functional["F-none"]["stages"]["initial"]
    denied = functional["F-deny"]["stages"]["denied"]
    prod = functional["F-PROD"]["stages"]
    checks = {
        "control_grant_rewrites_children": any(
            control["changed_by_grant"].values()),
        "shipped_grant_leaves_children": not any(
            shipped["changed_by_grant"].values()),
        "shipped_revoke_leaves_children": not any(
            shipped["changed_by_revoke"].values()),
        "shipped_parent_restored": shipped["parent_restored"],
        "shipped_fixture_clean": (
            not shipped["grant_error"] and not shipped["revoke_error"]),
        "production_run_passed": (
            production["passed"] is True and not production["error"]),
        "production_parent_unchanged": not production["changed"]["projects"],
        "production_sibling_unchanged": not (
            production["changed"]["sibling"]
            or production["changed"]["notes"]),
        "production_workspace_unchanged": not production["changed"][
            "workspace"],
        "functional_shipped_grant_reads_snapshot": _allowed(
            prod["granted"]["read_snap"]),
        "functional_shipped_grant_blocks_sibling": _blocked(
            prod["granted"]["read_sibling"]),
        "functional_shipped_revoke_removes_listing": _blocked(
            prod["revoked"]["list_parent"]),
        "functional_shipped_clean": not (
            functional["F-PROD"]["grant_error"]
            or functional["F-PROD"]["revoke_error"]),
    }
    named = fixtures["G1+R1"]
    info = {
        "setnamedsecurityinfo_grant_changes_children": any(
            named["changed_by_grant"].values()),
        "setnamedsecurityinfo_parent_restored": named["parent_restored"],
        "icacls_revoke_on_pristine_changes_children": any(
            fixtures["none+R0"]["changed_by_revoke"].values()),
        "traversal_read_without_parent_ace": _allowed(none["read_snap"]),
        "traversal_sibling_blocked_without_grant": _blocked(
            none["read_sibling"]),
        "traversal_read_with_deny_on_parent": _allowed(
            denied["read_snap"]),
        "listing_blocked_without_grant": _blocked(none["list_parent"]),
        "listing_allowed_with_shipped_grant": _allowed(
            prod["granted"]["list_parent"]),
        "deny_error": functional["F-deny"]["deny_error"],
        "production_error": production["error"],
    }
    return {"checks": checks, "info": info}


def summarize(report: dict, result: dict) -> list[str]:
    lines = [f"python={report['python']} platform={report['platform']} "
             f"icacls={report['icacls']}"]
    for row in report["fixtures"]:
        lines.append(
            f"FIXTURE {row['sequence']:<10} "
            f"grant_error={row['grant_error'][:160]!r} "
            f"revoke_error={row['revoke_error'][:160]!r} "
            f"changed_by_grant={row['changed_by_grant']} "
            f"changed_by_revoke={row['changed_by_revoke']} "
            f"parent_changed_by_grant={row['parent_changed_by_grant']} "
            f"parent_restored={row['parent_restored']}")
    for row in report["functional"]:
        for stage_name, stage in row["stages"].items():
            lines.append(
                f"FUNCTIONAL {row['sequence']:<7} {stage_name:<8} "
                f"read_snap={stage['read_snap']['returncode']} "
                f"read_sibling={stage['read_sibling']['returncode']} "
                f"list_parent={stage['list_parent']['returncode']} "
                f"child_error={stage['read_snap']['error'][:100]!r}")
        lines.append(
            f"FUNCTIONAL {row['sequence']:<7} errors "
            f"grant_error={row['grant_error'][:120]!r} "
            f"revoke_error={row['revoke_error'][:120]!r} "
            f"deny_error={row['deny_error'][:120]!r}")
    production = report["production"]
    lines.append(
        f"PRODUCTION passed={production['passed']} "
        f"error={production['error'][:160]!r} "
        f"changed={production['changed']}")
    for key in ("projects", "sibling"):
        lines.append(
            f"PRODUCTION sddl {key} before={production['before'][key]}")
        lines.append(
            f"PRODUCTION sddl {key} after={production['after'][key]}")
    for key, value in result["info"].items():
        lines.append(f"INFO {key}={value!r}")
    for key, value in result["checks"].items():
        lines.append(f"CHECK {key}={value}")
    lines.append(f"ACCEPTANCE={all(result['checks'].values())}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, required=True,
                        help="JSON report path to write")
    parser.add_argument("--assert", dest="assert_checks", action="store_true",
                        help="exit nonzero unless the required checks hold")
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("this probe must run on Windows")
    base = Path(tempfile.mkdtemp(prefix="forgeos-parent-acl-probe-"))
    try:
        report = run_probe(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    result = acceptance(report)
    report["acceptance"] = result
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for line in summarize(report, result):
        print(line)
    if args.assert_checks and not all(result["checks"].values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
