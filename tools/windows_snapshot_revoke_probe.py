#!/usr/bin/env python3
"""Windows-only probe of the execution-snapshot grant cleanup path.

Question answered: after the production grant is applied to a snapshot tree and
a link is created inside it afterwards, does the cleanup revoke change any
object outside the snapshot? Three revoke strategies run on identical fixtures:

  legacy_T    _revoke_path_grant(root, SID, recursive=True), as on main
  legacy_T_L  icacls /remove:g SID /T /L (option c)
  object_L    _revoke_snapshot_grant(root, SID), the object-by-object revoke

The last section runs a real AppContainer process through run_secure_appcontainer
to see whether a contained process can create a junction inside its snapshot,
and what the cleanup then does: first with the new revoke, then with the legacy.

Everything is created under one temporary directory this script owns. The
outside objects carry a copy of the probe SID, so any removal or rewrite of
their ACL shows up in the report.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forgeos import appcontainer  # noqa: E402

PROBE_SID = ("S-1-15-2-1000000001-1000000002-1000000003-"
             "1000000004-1000000005-1000000006-1000000007")
REPARSE_POINT = appcontainer.FILE_ATTRIBUTE_REPARSE_POINT
# name -> (mklink flags, link name, target kind, how the target is written)
VARIANTS = {
    "dir_symlink_absolute": (["/D"], "linkdir", "dir", "absolute"),
    "dir_symlink_relative": (["/D"], "linkrel", "dir", "relative"),
    "junction_absolute": (["/J"], "junction", "dir", "absolute"),
    "file_symlink_absolute": ([], "linkfile.txt", "file", "absolute"),
}
MODES = ("legacy_T", "legacy_T_L", "object_L")


def run(arguments):
    result = subprocess.run(
        arguments, capture_output=True, text=True, timeout=120,
        shell=False, errors="replace")
    return result.returncode, (result.stdout + result.stderr).strip()


def acl(path, *extra) -> str:
    code, text = run(["icacls.exe", str(path), *extra])
    return text if code == 0 else f"<icacls exit {code}: {text[-200:]}>"


def has_sid(text: str) -> bool:
    return PROBE_SID.casefold() in text.casefold()


def acl_delta(before: str, after: str) -> str:
    # The ACL lines that were added and removed, so a rewrite can be read.
    old = {line.strip() for line in before.splitlines() if line.strip()}
    new = {line.strip() for line in after.splitlines() if line.strip()}
    return f"+{sorted(new - old)[:3]} -{sorted(old - new)[:3]}"[:300]


def build(root: Path, variant: str) -> dict:
    flags, link_name, kind, written = VARIANTS[variant]
    outside = root / "outside"
    outside.mkdir(parents=True)
    secret = outside / "secret.txt"
    secret.write_text("decoy-secret", encoding="ascii")
    # An unrelated holder of the SID on the outside objects.
    run(["icacls.exe", str(outside), "/grant", f"*{PROBE_SID}:(OI)(CI)(M)", "/Q"])
    run(["icacls.exe", str(secret), "/grant", f"*{PROBE_SID}:(M)", "/Q"])
    snap = root / "snap"
    snap.mkdir()
    (snap / "app.py").write_text("print('source')\n", encoding="ascii")
    # The production grant: recursive, applied before any link exists.
    code, text = run(["icacls.exe", str(snap), "/grant",
                      f"*{PROBE_SID}:(OI)(CI)(M)", "/T", "/Q"])
    if code != 0:
        raise OSError(f"production-style grant failed: {text[-300:]}")
    link = snap / link_name
    if kind == "file":
        target = str(secret)
    elif written == "relative":
        target = "..\\outside"
    else:
        target = str(outside)
    code, text = run(["cmd.exe", "/d", "/c", "mklink", *flags, str(link), target])
    if code != 0:
        raise OSError(f"mklink failed for {variant}: {text[-300:]}")
    return {"outside": outside, "secret": secret, "snap": snap, "link": link}


def revoke(mode: str, snap: Path) -> None:
    if mode == "legacy_T":
        appcontainer._revoke_path_grant(snap, PROBE_SID, recursive=True)
    elif mode == "legacy_T_L":
        appcontainer._icacls(snap, "/remove:g", f"*{PROBE_SID}", "/T", "/L", "/Q")
    else:
        appcontainer._revoke_snapshot_grant(snap, PROBE_SID)


def read_object_acl(path: Path) -> str:
    # A link's own ACL is read with /L; ordinary objects are read as they are.
    if appcontainer._is_reparse_point(os.lstat(path)):
        return acl(path, "/L")
    return acl(path)


def objects_with_sid(snap: Path) -> dict:
    found, unreadable = [], []
    for path in appcontainer._snapshot_objects(snap):
        text = read_object_acl(path)
        if text.startswith("<icacls"):
            unreadable.append(path.name)
        elif has_sid(text):
            found.append(path.name)
    return {"with_sid": found, "unreadable": unreadable}


def python_checks(base: Path, variant: str) -> dict:
    ctx = build(base / f"py-{variant}", variant)
    walked = [str(path).casefold() for path, _dirs, _files in os.walk(ctx["snap"])]
    link_prefix = str(ctx["link"]).casefold()
    listed = [Path(path) for path in appcontainer._snapshot_objects(ctx["snap"])]
    # Deletion follows the revoke: the snapshot removal must not reach outside.
    removal = build(base / f"rm-{variant}", variant)
    removal_errors = []
    shutil.rmtree(
        removal["snap"],
        onerror=lambda func, path, exc_info: removal_errors.append(
            f"{getattr(func, '__name__', func)}:{path}"))
    return {
        "os_walk_enters_link": any(
            path == link_prefix or path.startswith(link_prefix + os.sep)
            for path in walked),
        "walker_lists_outside_target": any(
            path.is_relative_to(ctx["outside"]) for path in listed),
        "walker_lists_link": ctx["link"] in listed,
        "rmtree_outside_survived": (removal["secret"].exists()
                                    and removal["outside"].exists()),
        "rmtree_errors": len(removal_errors),
    }


def probe_mode(base: Path, variant: str, mode: str) -> dict:
    ctx = build(base / f"{variant}-{mode}", variant)
    before = {name: acl(ctx[name]) for name in ("outside", "secret")}
    link_before = acl(ctx["link"], "/L")
    error = ""
    try:
        revoke(mode, ctx["snap"])
    except OSError as exc:
        error = str(exc)[-300:]
    after = {name: acl(ctx[name]) for name in ("outside", "secret")}
    return {
        "variant": variant,
        "mode": mode,
        "error": error,
        "outside_changed": before["outside"] != after["outside"],
        "secret_changed": before["secret"] != after["secret"],
        "outside_sid_removed": has_sid(before["outside"]) and not has_sid(after["outside"]),
        "secret_sid_removed": has_sid(before["secret"]) and not has_sid(after["secret"]),
        "outside_readable": not before["outside"].startswith("<icacls"),
        "link_display_ok": not link_before.startswith("<icacls"),
        "link_own_sid_before": has_sid(link_before),
        "link_own_sid_after": has_sid(acl(ctx["link"], "/L")),
        "in_tree": objects_with_sid(ctx["snap"]),
    }


def legacy_revoke(path: Path, sid_string: str, **_unused) -> None:
    appcontainer._revoke_path_grant(path, sid_string, recursive=True)


def parent_write_check(base: Path) -> dict:
    # The AppContainer grant and revoke on the snapshot's parent directory are
    # non-recursive DACL writes on that directory. Does such a write rewrite the
    # directory's children, which lie outside the snapshot?
    root = base / "parent-check"
    parent = root / "parent"
    (parent / "child").mkdir(parents=True)
    (parent / "child" / "inner.txt").write_text("x", encoding="ascii")
    (parent / "sibling.txt").write_text("y", encoding="ascii")
    objects = {
        "child": parent / "child",
        "inner": parent / "child" / "inner.txt",
        "sibling": parent / "sibling.txt",
    }
    before = {name: acl(path) for name, path in objects.items()}
    grant_code, grant_text = run(
        ["icacls.exe", str(parent), "/grant", f"*{PROBE_SID}:(RX)", "/Q"])
    after_grant = {name: acl(path) for name, path in objects.items()}
    revoke_code, revoke_text = run(
        ["icacls.exe", str(parent), "/remove:g", f"*{PROBE_SID}", "/Q"])
    after_revoke = {name: acl(path) for name, path in objects.items()}
    return {
        "grant_rc": grant_code,
        "revoke_rc": revoke_code,
        "grant_output": grant_text[-200:],
        "revoke_output": revoke_text[-200:],
        "changed_by_grant": {n: before[n] != after_grant[n] for n in objects},
        "changed_by_revoke": {n: after_grant[n] != after_revoke[n] for n in objects},
        "changed_overall": {n: before[n] != after_revoke[n] for n in objects},
        "parent_sid_after": has_sid(acl(parent)),
        "sibling_delta": acl_delta(before["sibling"], after_revoke["sibling"]),
        "child_delta": acl_delta(before["child"], after_revoke["child"]),
    }


def scale_check(base: Path, files: int = 400) -> dict:
    # Cost of the object-by-object revoke on a tree of ordinary files, with a
    # directory symlink inside it that must not be entered.
    ctx = build(base / "scale", "dir_symlink_absolute")
    bulk = ctx["snap"] / "bulk"
    bulk.mkdir()
    for index in range(files):
        (bulk / f"file-{index:04d}.txt").write_text("x", encoding="ascii")
    before = (acl(ctx["outside"]), acl(ctx["secret"]))
    objects = len(appcontainer._snapshot_objects(ctx["snap"]))
    started = time.monotonic()
    error = ""
    try:
        appcontainer._revoke_snapshot_grant(ctx["snap"], PROBE_SID)
    except OSError as exc:
        error = str(exc)[-300:]
    seconds = time.monotonic() - started
    after = (acl(ctx["outside"]), acl(ctx["secret"]))
    tree = objects_with_sid(ctx["snap"])
    return {
        "objects": objects,
        "seconds": round(seconds, 2),
        "seconds_per_object": round(seconds / max(objects, 1), 4),
        "error": error,
        "outside_changed": before != after,
        "in_tree_with_sid": tree["with_sid"],
        "unreadable": tree["unreadable"],
    }


def appcontainer_run(base: Path, label: str, *, legacy: bool,
                     target: str = "outside") -> dict:
    root = base / f"appcontainer-{label}"
    snap = root / "snap"
    (snap / "tmp").mkdir(parents=True)
    (snap / "app.py").write_text("print('source')\n", encoding="ascii")
    outside = root / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("decoy-secret", encoding="ascii")
    link = snap / "junction"
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    # A copy in a directory this probe owns. Granting and revoking the runner's
    # parent directory must not write System32's own ACL.
    runner_dir = root / "runner"
    runner_dir.mkdir()
    cmd = str(runner_dir / "cmd.exe")
    shutil.copy2(os.path.join(system_root, "System32", "cmd.exe"), cmd)
    # AppContainer process creation resolves its profile through the caller's
    # LOCALAPPDATA, as secure_runner._bootstrap_environment provides it.
    env = {
        "SystemRoot": system_root, "ComSpec": cmd,
        "TEMP": str(snap / "tmp"), "TMP": str(snap / "tmp"),
        "LOCALAPPDATA": os.environ["LOCALAPPDATA"],
    }
    before = (acl(outside), acl(secret))
    result, error = {}, ""
    # "outside" asks whether the sandbox may link to a target it cannot read.
    # "inside" asks whether it may create a junction at all, to a target it can
    # read; a denial in the outside case may come from mklink reading the target.
    target_path = outside
    if target == "inside":
        target_path = snap / "inner"
        target_path.mkdir()
    argv = [cmd, "/d", "/c", "mklink", "/J", str(link), str(target_path)]
    try:
        if legacy:
            with patch.object(appcontainer, "_revoke_snapshot_grant",
                              side_effect=legacy_revoke):
                result = appcontainer.run_secure_appcontainer(
                    argv, snap, env, executable=cmd, timeout=90)
        else:
            result = appcontainer.run_secure_appcontainer(
                argv, snap, env, executable=cmd, timeout=90)
    except Exception as exc:  # recorded for the report, never hidden
        error = f"{type(exc).__name__}: {exc}"[-400:]
    after = (acl(outside), acl(secret))
    created = os.path.lexists(link)
    return {
        "label": label,
        "target": target,
        "revoke": "legacy_T" if legacy else "object_L",
        "returncode": result.get("returncode"),
        "passed": result.get("passed"),
        "containment": result.get("containment"),
        "profile_deleted": result.get("profile_deleted"),
        "error": error,
        "junction_created": created and bool(
            os.lstat(link).st_file_attributes & REPARSE_POINT),
        "outside_changed": before[0] != after[0],
        "secret_changed": before[1] != after[1],
        "outside_delta": acl_delta(before[0], after[0]),
        "secret_delta": acl_delta(before[1], after[1]),
        "child_output": str(result.get("output", ""))[-400:],
        "outside_sid_after": has_sid(after[0]) or has_sid(after[1]),
        "in_tree": objects_with_sid(snap),
    }


def summarise(report: dict) -> list[str]:
    lines = [f"python={report['python']} platform={report['platform']} "
             f"icacls={report['icacls']}"]
    for fixture in report["fixtures"]:
        python = fixture["python"]
        lines.append(
            f"== {fixture['variant']}: os.walk_enters_link="
            f"{python['os_walk_enters_link']} walker_lists_outside="
            f"{python['walker_lists_outside_target']} walker_lists_link="
            f"{python['walker_lists_link']} rmtree_outside_survived="
            f"{python['rmtree_outside_survived']} rmtree_errors="
            f"{python['rmtree_errors']}")
        for mode in fixture["modes"]:
            tree = mode["in_tree"]
            lines.append(
                f"  {mode['mode']:<11} outside_changed={mode['outside_changed']} "
                f"secret_changed={mode['secret_changed']} "
                f"outside_readable={mode['outside_readable']} "
                f"outside_sid_removed={mode['outside_sid_removed']} "
                f"link_sid_after={mode['link_own_sid_after']} "
                f"link_display_ok={mode['link_display_ok']} "
                f"in_tree_sid={tree['with_sid']} unreadable={tree['unreadable']} "
                f"error={mode['error'][:160]!r}")
    for run_report in report["appcontainer"]:
        tree = run_report["in_tree"]
        lines.append(
            f"== AppContainer {run_report['label']} (revoke={run_report['revoke']}): "
            f"rc={run_report['returncode']} passed={run_report['passed']} "
            f"containment={run_report['containment']} "
            f"junction_created={run_report['junction_created']} "
            f"outside_changed={run_report['outside_changed']} "
            f"secret_changed={run_report['secret_changed']} "
            f"outside_delta={run_report.get('outside_delta')} "
            f"outside_sid_after={run_report['outside_sid_after']} "
            f"in_tree_sid={tree['with_sid']} unreadable={tree['unreadable']} "
            f"profile_deleted={run_report['profile_deleted']} "
            f"child_output={run_report.get('child_output', '')[-200:]!r} "
            f"error={run_report['error'][:200]!r}")
    parent = report.get("parent_write", {})
    if parent:
        lines.append(
            f"== parent_write: grant_rc={parent['grant_rc']} "
            f"revoke_rc={parent['revoke_rc']} "
            f"changed_by_grant={parent['changed_by_grant']} "
            f"changed_by_revoke={parent['changed_by_revoke']} "
            f"changed_overall={parent['changed_overall']} "
            f"parent_sid_after={parent['parent_sid_after']} "
            f"sibling_delta={parent['sibling_delta']!r} "
            f"child_delta={parent['child_delta']!r}")
    scale = report.get("scale", {})
    lines.append(
        f"== scale: objects={scale.get('objects')} seconds={scale.get('seconds')} "
        f"seconds_per_object={scale.get('seconds_per_object')} "
        f"outside_changed={scale.get('outside_changed')} "
        f"in_tree_with_sid={scale.get('in_tree_with_sid')} "
        f"unreadable={scale.get('unreadable')} error={scale.get('error', '')[:160]!r}")
    object_rows = [mode for fixture in report["fixtures"]
                   for mode in fixture["modes"] if mode["mode"] == "object_L"]
    lines.append(
        "verdict: object_L changed no outside object in any fixture="
        f"{not any(row['outside_changed'] or row['secret_changed'] for row in object_rows)}"
        "; object_L left no SID on in-tree objects="
        f"{not any(row['in_tree']['with_sid'] for row in object_rows)}"
        "; every object was readable="
        f"{not any(row['in_tree']['unreadable'] for row in object_rows)}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--report", type=Path, required=True,
                        help="JSON report path to write")
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("this probe must run on Windows")
    base = Path(tempfile.mkdtemp(prefix="forgeos-revoke-probe-"))
    report = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "icacls": shutil.which("icacls.exe"),
        "probe_sid": PROBE_SID,
        "fixtures": [],
        "appcontainer": [],
    }
    try:
        for variant in VARIANTS:
            report["fixtures"].append({
                "variant": variant,
                "python": python_checks(base, variant),
                "modes": [probe_mode(base, variant, mode) for mode in MODES],
            })
        report["appcontainer"].append(
            appcontainer_run(base, "object_L", legacy=False))
        report["appcontainer"].append(
            appcontainer_run(base, "legacy_T", legacy=True))
        report["appcontainer"].append(
            appcontainer_run(base, "inside_target", legacy=False, target="inside"))
        report["scale"] = scale_check(base)
        report["parent_write"] = parent_write_check(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\n".join(summarise(report)))
    print(f"full JSON report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
