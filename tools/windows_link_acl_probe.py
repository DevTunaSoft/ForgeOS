#!/usr/bin/env python3
"""Windows-only evidence probe for links inside an execution snapshot.

Question answered: when a snapshot tree contains a directory symlink, a
junction or a file symlink whose target lies outside the snapshot, do the
recursive icacls operations ForgeOS uses (grant, revoke, integrity label) or
the Python copy and removal helpers reach that target, with and without /L?

Every file, directory and link is created under one temporary directory that
this script owns. The grant SID is a synthetic AppContainer-shaped SID; no
AppContainer profile is created. The report is written as JSON and printed.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE_SID = "S-1-15-2-1000000001-1000000002-1000000003-1000000004-1000000005-1000000006-1000000007"
GRANT = f"*{PROBE_SID}:(OI)(CI)(M)"
LABEL = "(OI)(CI)L"
REPARSE_POINT = 0x0400
OPERATIONS = ("grant_T", "grant_T_L", "label_T", "label_T_L",
              "revoke_T", "revoke_T_L", "direct", "direct_L")

# name -> (mklink flags, link name, target kind, how the target is written)
VARIANTS = {
    "dir_symlink_absolute": (["/D"], "linkdir", "dir", "absolute"),
    "dir_symlink_relative": (["/D"], "linkrel", "dir", "relative"),
    "junction_absolute": (["/J"], "junction", "dir", "absolute"),
    "file_symlink_absolute": ([], "linkfile.txt", "file", "absolute"),
}


def run(arguments):
    result = subprocess.run(
        arguments, capture_output=True, text=True, timeout=120,
        shell=False, errors="replace")
    return result.returncode, (result.stdout + result.stderr).strip()


def acl(path: Path) -> str:
    code, text = run(["icacls.exe", str(path)])
    return text if code == 0 else f"<icacls exit {code}: {text[-200:]}>"


def build(root: Path, spec):
    flags, link_name, kind, written = spec
    decoy = root / "decoy"
    decoy.mkdir(parents=True)
    secret = decoy / "secret.txt"
    secret.write_text("decoy-secret", encoding="ascii")
    snap = root / "snap"
    snap.mkdir()
    (snap / "app.py").write_text("print('source')\n", encoding="ascii")
    if written == "absolute":
        target = str(decoy if kind == "dir" else secret)
    else:
        target = "..\\decoy"
    link = snap / link_name
    code, text = run(["cmd.exe", "/d", "/c", "mklink", *flags, str(link), target])
    return {"decoy": decoy, "secret": secret, "snap": snap, "link": link,
            "mklink_rc": code, "mklink_output": text[-300:]}


def state(ctx):
    return {"decoy": acl(ctx["decoy"]), "secret": acl(ctx["secret"])}


def effect(before: str, after: str) -> dict:
    return {
        "changed": before != after,
        "sid_present": PROBE_SID.casefold() in after.casefold(),
        "integrity_label_present": "Mandatory Label" in after,
    }


def run_operation(root: Path, spec, operation: str) -> dict:
    ctx = build(root, spec)
    if ctx["mklink_rc"] != 0:
        return {"setup_error": ctx["mklink_output"]}
    long_flag = ["/L"] if operation.endswith("_L") else []
    if operation.startswith("revoke"):
        # Pre-grant the outside targets directly so a removal that reaches
        # them is observable as a change.
        for path in (ctx["decoy"], ctx["secret"]):
            run(["icacls.exe", str(path), "/grant", GRANT, "/Q"])
    before = state(ctx)
    if operation.startswith("grant"):
        command = ["icacls.exe", str(ctx["snap"]), "/grant", GRANT, "/T",
                   *long_flag, "/Q"]
    elif operation.startswith("label"):
        command = ["icacls.exe", str(ctx["snap"]), "/setintegritylevel", LABEL,
                   "/T", *long_flag, "/Q"]
    elif operation.startswith("revoke"):
        command = ["icacls.exe", str(ctx["snap"]), "/remove:g", f"*{PROBE_SID}",
                   "/T", *long_flag, "/Q"]
    else:
        # Direct form: the link itself is the icacls operand, no recursion.
        command = ["icacls.exe", str(ctx["link"]), "/grant", GRANT,
                   *long_flag, "/Q"]
    code, text = run(command)
    after = state(ctx)
    return {
        "rc": code,
        "output": text[-300:],
        "decoy": effect(before["decoy"], after["decoy"]),
        "secret": effect(before["secret"], after["secret"]),
        "decoy_acl_before": before["decoy"],
        "decoy_acl_after": after["decoy"],
        "secret_acl_before": before["secret"],
        "secret_acl_after": after["secret"],
    }


def python_checks(root: Path, spec) -> dict:
    checks = {}
    ctx = build(root / "copy", spec)
    link = ctx["link"]
    try:
        copied = root / "copy-out"
        shutil.copytree(ctx["snap"], copied, symlinks=True)
        in_copy = copied / link.name
        info = os.lstat(in_copy)
        is_link = os.path.islink(in_copy)
        materialised = (not is_link
                        and not info.st_file_attributes & REPARSE_POINT
                        and in_copy.exists())
        checks["copytree_symlinks_true"] = {
            "is_symlink": is_link,
            "is_junction": (os.path.isjunction(in_copy)
                            if hasattr(os.path, "isjunction") else "unavailable"),
            "reparse_point": bool(info.st_file_attributes & REPARSE_POINT),
            "readlink": os.readlink(in_copy) if is_link else None,
            "content_materialised": materialised,
        }
    except OSError as exc:
        checks["copytree_symlinks_true"] = {"error": repr(exc)}

    ctx = build(root / "scan", spec)
    with os.scandir(ctx["snap"]) as entries:
        for entry in entries:
            if entry.name == link.name:
                st = entry.stat(follow_symlinks=False)
                checks["scandir_entry"] = {
                    "is_symlink": entry.is_symlink(),
                    "is_dir_follow_symlinks_false": entry.is_dir(follow_symlinks=False),
                    "is_dir_follow_symlinks_true": entry.is_dir(),
                    "stat_is_symlink": stat.S_ISLNK(st.st_mode),
                    "reparse_point": bool(st.st_file_attributes & REPARSE_POINT),
                    "is_junction": (entry.is_junction()
                                    if hasattr(entry, "is_junction") else "unavailable"),
                }
    # Compare against the link inside the tree that os.walk actually visits.
    prefix = str(ctx["link"]).casefold()
    visited = [str(path) for path, _dirs, _files in os.walk(ctx["snap"])]
    checks["os_walk_enters_link"] = any(
        path.casefold() == prefix or path.casefold().startswith(prefix + os.sep)
        for path in visited)

    ctx = build(root / "rmtree", spec)
    errors = []
    shutil.rmtree(
        ctx["snap"],
        onerror=lambda func, path, exc_info: errors.append(
            f"{getattr(func, '__name__', func)}:{path}:{exc_info[1]!r}"))
    checks["rmtree_errors"] = errors
    checks["rmtree_outside_target_survived"] = (
        ctx["secret"].exists() and ctx["decoy"].exists())
    return checks


def probe_variant(base: Path, name: str) -> dict:
    spec = VARIANTS[name]
    result = {"variant": name, "target_kind": spec[2], "ops": {}}
    for operation in OPERATIONS:
        result["ops"][operation] = run_operation(
            base / f"{name}-{operation}", spec, operation)
    result["python"] = python_checks(base / f"{name}-python", spec)
    return result


def acl_delta(before: str, after: str) -> str:
    old = [line.strip() for line in before.splitlines() if line.strip()]
    new = [line.strip() for line in after.splitlines() if line.strip()]
    added = [line for line in new if line not in old][:3]
    removed = [line for line in old if line not in new][:3]
    return f"+{added} -{removed}"[:300]


def summarise(report: dict) -> list[str]:
    """One compact line per check, so a job-log tail carries the whole result."""
    lines = [f"python={report['python']} icacls={report['icacls']}"]
    for variant in report["variants"]:
        lines.append(f"== {variant['variant']} (target={variant['target_kind']})")
        for operation, result in variant["ops"].items():
            if "setup_error" in result:
                lines.append(f"  {operation}: setup error: {result['setup_error']}")
                continue
            cells = " ".join(
                f"{name}[changed={result[name]['changed']} "
                f"sid={result[name]['sid_present']} "
                f"label={result[name]['integrity_label_present']}]"
                for name in ("decoy", "secret"))
            deltas = [
                f"{name}_delta={acl_delta(result[name + '_acl_before'], result[name + '_acl_after'])}"
                for name in ("decoy", "secret") if result[name]["changed"]]
            line = f"  {operation:<12} rc={result['rc']} {cells} " + " ".join(deltas)
            lines.append(line.rstrip())
        python = variant["python"]
        lines.append(f"  copytree: {python.get('copytree_symlinks_true')}")
        lines.append(f"  scandir: {python.get('scandir_entry')}")
        lines.append(f"  os.walk enters link: {python.get('os_walk_enters_link')}")
        errors = python.get("rmtree_errors", [])
        lines.append(
            "  rmtree: outside_target_survived="
            f"{python.get('rmtree_outside_target_survived')} "
            f"errors={len(errors)} first_error={errors[:1]}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--report", type=Path, required=True,
                        help="JSON report path to write")
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("this probe must run on Windows")
    base = Path(tempfile.mkdtemp(prefix="forgeos-link-probe-"))
    report = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "icacls": shutil.which("icacls.exe"),
        "probe_sid": PROBE_SID,
        "variants": [],
    }
    try:
        for name in VARIANTS:
            report["variants"].append(probe_variant(base, name))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\n".join(summarise(report)))
    print(f"full JSON report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
