#!/usr/bin/env python3
"""Windows probe: does a non-recursive AppContainer grant rewrite child ACLs?

The secure runner grants read and execute on a snapshot's parent folder and on
the runner's folder. Those folders can be a user's project folder, so a grant
there must leave every child exactly as it was. Each sequence below builds a
fresh fixture, applies a grant method and then a revoke method, and compares the
DACL SDDL of every child before and after.

The fixture gives each child an explicit copy of the parent's inheritable Users
ACE. A re-inheritance pass converts such copies into inherited ones, so the
icacls control sequence should show a change. Without that sensitivity the
unchanged-children result would prove nothing.

  G0+R0    icacls /grant and icacls /remove:g, non-recursive (control: the
           production path before this change)
  G1+R1    appcontainer._grant/_revoke_read_execute_no_propagation (candidate)
  G1+R0    candidate grant, icacls revoke
  G2+R1    SetNamedSecurityInfoW with UNPROTECTED_DACL added (characterization)
  G3+R3    SetFileSecurityW with a fresh security descriptor (characterization)
  none+R0  icacls revoke on a fixture without any grant
  none+R1  candidate revoke on a fixture without any grant

Functional sequences run real zero-capability AppContainer children: a read of
a snapshot below the parent before any parent grant, a read after the grant, a
read of an ungranted sibling, and a read after the revoke.

Windows only. Writes a JSON report and prints a summary. With --assert the exit
status is nonzero unless the acceptance checks hold.
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

if os.name == "nt":
    ADVAPI = ctypes.WinDLL("advapi32", use_last_error=True)
    KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
    ADVAPI.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    ADVAPI.GetNamedSecurityInfoW.restype = wintypes.DWORD
    ADVAPI.SetEntriesInAclW.argtypes = [
        wintypes.DWORD, ctypes.POINTER(appcontainer.EXPLICIT_ACCESS_W),
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
    ADVAPI.InitializeSecurityDescriptor.argtypes = [
        ctypes.c_void_p, wintypes.DWORD]
    ADVAPI.InitializeSecurityDescriptor.restype = wintypes.BOOL
    ADVAPI.SetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, ctypes.c_void_p, wintypes.BOOL]
    ADVAPI.SetSecurityDescriptorDacl.restype = wintypes.BOOL
    ADVAPI.SetFileSecurityW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    ADVAPI.SetFileSecurityW.restype = wintypes.BOOL
    KERNEL.LocalFree.argtypes = [ctypes.c_void_p]
    KERNEL.LocalFree.restype = ctypes.c_void_p


def _check(status: int, what: str) -> None:
    if status != 0:
        raise OSError(f"{what} failed with Win32 error {status}")


def _win_check(ok, what: str) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)


def sddl(path: Path) -> str:
    """Return the object's DACL as SDDL, where (ID) marks inherited ACEs."""
    descriptor = ctypes.c_void_p()
    _check(ADVAPI.GetNamedSecurityInfoW(
        str(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        None, None, None, None, ctypes.byref(descriptor)),
        f"GetNamedSecurityInfoW({path})")
    try:
        text = wintypes.LPWSTR()
        length = wintypes.DWORD()
        _win_check(
            ADVAPI.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, SDDL_REVISION_1, DACL_SECURITY_INFORMATION,
                ctypes.byref(text), ctypes.byref(length)),
            f"ConvertSecurityDescriptorToStringSecurityDescriptorW({path})")
        try:
            return text.value
        finally:
            KERNEL.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        KERNEL.LocalFree(descriptor)


def _rewrite_dacl(path: Path, sid, *, grant: bool, method: str) -> None:
    """Add or remove this SID's read/execute ACE with the chosen write API.

    "named" is the candidate SetNamedSecurityInfoW with DACL only.
    "named_unprotected" adds UNPROTECTED_DACL. "file_security" writes a fresh
    security descriptor with SetFileSecurityW.
    """
    descriptor = ctypes.c_void_p()
    current = ctypes.c_void_p()
    _check(ADVAPI.GetNamedSecurityInfoW(
        str(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        None, None, ctypes.byref(current), None, ctypes.byref(descriptor)),
        f"read DACL of {path}")
    try:
        if not current.value:
            raise OSError(f"{path} has no DACL")
        entry = appcontainer.EXPLICIT_ACCESS_W()
        entry.grfAccessPermissions = (
            appcontainer.READ_EXECUTE_MASK if grant else 0)
        entry.grfAccessMode = (
            appcontainer.GRANT_ACCESS if grant else appcontainer.REVOKE_ACCESS)
        entry.grfInheritance = appcontainer.NO_INHERITANCE
        entry.Trustee.TrusteeForm = appcontainer.TRUSTEE_IS_SID
        entry.Trustee.TrusteeType = appcontainer.TRUSTEE_IS_UNKNOWN
        entry.Trustee.ptstrName = sid.value
        new_dacl = ctypes.c_void_p()
        _check(ADVAPI.SetEntriesInAclW(
            1, ctypes.byref(entry), current, ctypes.byref(new_dacl)),
            f"SetEntriesInAclW({path})")
        try:
            if method == "named":
                _check(ADVAPI.SetNamedSecurityInfoW(
                    str(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                    None, None, new_dacl, None),
                    f"SetNamedSecurityInfoW({path})")
            elif method == "named_unprotected":
                _check(ADVAPI.SetNamedSecurityInfoW(
                    str(path), SE_FILE_OBJECT,
                    DACL_SECURITY_INFORMATION
                    | UNPROTECTED_DACL_SECURITY_INFORMATION,
                    None, None, new_dacl, None),
                    f"SetNamedSecurityInfoW(unprotected, {path})")
            elif method == "file_security":
                security = ctypes.create_string_buffer(256)
                security_pointer = ctypes.cast(security, ctypes.c_void_p)
                _win_check(
                    ADVAPI.InitializeSecurityDescriptor(security_pointer, 1),
                    "InitializeSecurityDescriptor")
                _win_check(
                    ADVAPI.SetSecurityDescriptorDacl(
                        security_pointer, True, new_dacl, False),
                    "SetSecurityDescriptorDacl")
                _win_check(
                    ADVAPI.SetFileSecurityW(
                        str(path), DACL_SECURITY_INFORMATION,
                        security_pointer),
                    f"SetFileSecurityW({path})")
            else:
                raise ValueError(f"unknown write method: {method}")
        finally:
            KERNEL.LocalFree(new_dacl)
    finally:
        KERNEL.LocalFree(descriptor)


def _icacls_grant(path: Path, sid, sid_string: str) -> None:
    appcontainer._icacls(path, "/grant", f"*{sid_string}:(RX)", "/Q")


def _icacls_revoke(path: Path, sid, sid_string: str) -> None:
    appcontainer._icacls(path, "/remove:g", f"*{sid_string}", "/Q")


def _candidate_grant(path: Path, sid, sid_string: str) -> None:
    appcontainer._grant_read_execute_no_propagation(path, sid)


def _candidate_revoke(path: Path, sid, sid_string: str) -> None:
    appcontainer._revoke_read_execute_no_propagation(path, sid)


def _unprotected_grant(path: Path, sid, sid_string: str) -> None:
    _rewrite_dacl(path, sid, grant=True, method="named_unprotected")


def _file_security_grant(path: Path, sid, sid_string: str) -> None:
    _rewrite_dacl(path, sid, grant=True, method="file_security")


def _file_security_revoke(path: Path, sid, sid_string: str) -> None:
    _rewrite_dacl(path, sid, grant=False, method="file_security")


SEQUENCES = (
    ("G0+R0", _icacls_grant, _icacls_revoke),
    ("G1+R1", _candidate_grant, _candidate_revoke),
    ("G1+R0", _candidate_grant, _icacls_revoke),
    ("G2+R1", _unprotected_grant, _candidate_revoke),
    ("G3+R3", _file_security_grant, _file_security_revoke),
    ("none+R0", None, _icacls_revoke),
    ("none+R1", None, _candidate_revoke),
)
FUNCTIONAL = (
    ("F-G1", _candidate_grant, _candidate_revoke),
    ("F-G3", _file_security_grant, _file_security_revoke),
)


def build_fixture(base: Path, name: str) -> dict[str, Path]:
    root = base / name
    parent = root / "parent"
    parent.mkdir(parents=True)
    # The parent gets an inheritable Users ACE before any child exists.
    appcontainer._icacls(
        parent, "/grant", f"*{USERS_SID}:(OI)(CI)(RX)", "/Q")
    child = parent / "child"
    child.mkdir()
    inner = child / "inner.txt"
    inner.write_text("inner", encoding="ascii")
    sibling = parent / "sibling.txt"
    sibling.write_text("sibling", encoding="ascii")
    # Each child then gets an explicit copy of the inherited Users ACE.
    appcontainer._icacls(
        child, "/grant", f"*{USERS_SID}:(OI)(CI)(RX)", "/Q")
    appcontainer._icacls(inner, "/grant", f"*{USERS_SID}:(RX)", "/Q")
    appcontainer._icacls(sibling, "/grant", f"*{USERS_SID}:(RX)", "/Q")
    return {"parent": parent, "child": child,
            "inner": inner, "sibling": sibling}


def run_sequence(base: Path, index: int, name: str, sid, sid_string: str,
                 grant, revoke) -> dict:
    paths = build_fixture(base, f"fixture-{index}")
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


def functional_sequence(base: Path, index: int, name: str, sid,
                        sid_string: str, command: str, grant,
                        revoke) -> dict:
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
    # Output goes into the snapshot, which the child may write, rather than to
    # NUL, so a failed read is the only reason for a nonzero exit.
    read_app = f"type {app} > {snap / 'out-app.txt'}"
    read_sibling = f"type {sibling} > {snap / 'out-sibling.txt'}"
    row = {"sequence": name, "grant_error": "", "revoke_error": ""}
    row["before_grant_read_snap"] = child_exit(sid, command, read_app, snap)
    try:
        grant(parent, sid, sid_string)
    except OSError as exc:
        row["grant_error"] = str(exc)[-300:]
    row["after_grant_read_snap"] = child_exit(sid, command, read_app, snap)
    row["after_grant_read_sibling"] = child_exit(
        sid, command, read_sibling, snap)
    try:
        revoke(parent, sid, sid_string)
    except OSError as exc:
        row["revoke_error"] = str(exc)[-300:]
    row["after_revoke_read_snap"] = child_exit(sid, command, read_app, snap)
    return row


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
                base, index, name, sid, sid_string, command, grant, revoke))
    return report


def _blocked(row: dict) -> bool:
    return isinstance(row["returncode"], int) and row["returncode"] != 0


def acceptance(report: dict) -> dict:
    fixtures = {row["sequence"]: row for row in report["fixtures"]}
    functional = {row["sequence"]: row for row in report["functional"]}
    control = fixtures["G0+R0"]
    candidate = fixtures["G1+R1"]
    grant_result = functional["F-G1"]
    checks = {
        "control_grant_rewrites_children": any(
            control["changed_by_grant"].values()),
        "candidate_grant_leaves_children": not any(
            candidate["changed_by_grant"].values()),
        "candidate_revoke_leaves_children": not any(
            candidate["changed_by_revoke"].values()),
        "candidate_parent_restored": candidate["parent_restored"],
        "candidate_fixture_clean": (
            not candidate["grant_error"] and not candidate["revoke_error"]),
        "functional_candidate_reads_snapshot": (
            grant_result["after_grant_read_snap"]["returncode"] == 0),
        "functional_candidate_blocks_sibling": _blocked(
            grant_result["after_grant_read_sibling"]),
        "functional_candidate_blocked_after_revoke": _blocked(
            grant_result["after_revoke_read_snap"]),
        "functional_candidate_clean": (
            not grant_result["grant_error"]
            and not grant_result["revoke_error"]),
    }
    info = {
        "traverse_grant_needed": _blocked(
            grant_result["before_grant_read_snap"]),
        "read_without_parent_grant": (
            grant_result["before_grant_read_snap"]["returncode"] == 0),
    }
    return {"checks": checks, "info": info}


def summarize(report: dict, result: dict) -> list[str]:
    lines = [f"python={report['python']} platform={report['platform']} "
             f"icacls={report['icacls']}"]
    for row in report["fixtures"]:
        lines.append(
            f"FIXTURE {row['sequence']:<8} "
            f"grant_error={row['grant_error'][:160]!r} "
            f"revoke_error={row['revoke_error'][:160]!r} "
            f"changed_by_grant={row['changed_by_grant']} "
            f"changed_by_revoke={row['changed_by_revoke']} "
            f"parent_changed_by_grant={row['parent_changed_by_grant']} "
            f"parent_restored={row['parent_restored']}")
    for row in report["fixtures"]:
        if row["sequence"] not in ("G0+R0", "G1+R1"):
            continue
        for stage in ("before", "after_grant", "after_revoke"):
            lines.append(
                f"SDDL {row['sequence']} {stage} "
                f"sibling={row['sddl'][stage]['sibling']} "
                f"inner={row['sddl'][stage]['inner']}")
    for row in report["functional"]:
        lines.append(
            f"FUNCTIONAL {row['sequence']:<8} "
            f"before_grant_read_snap="
            f"{row['before_grant_read_snap']['returncode']} "
            f"after_grant_read_snap="
            f"{row['after_grant_read_snap']['returncode']} "
            f"after_grant_read_sibling="
            f"{row['after_grant_read_sibling']['returncode']} "
            f"after_revoke_read_snap="
            f"{row['after_revoke_read_snap']['returncode']} "
            f"grant_error={row['grant_error'][:120]!r} "
            f"revoke_error={row['revoke_error'][:120]!r} "
            f"child_error={row['after_grant_read_snap']['error'][:120]!r}")
    for key, value in result["info"].items():
        lines.append(f"INFO {key}={value}")
    for key, value in result["checks"].items():
        lines.append(f"CHECK {key}={value}")
    lines.append(f"ACCEPTANCE={all(result['checks'].values())}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, required=True,
                        help="JSON report path to write")
    parser.add_argument("--assert", dest="assert_checks", action="store_true",
                        help="exit nonzero unless the acceptance checks hold")
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
