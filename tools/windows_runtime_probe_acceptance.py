#!/usr/bin/env python3
"""Windows-only acceptance check for the Python runtime probe's ACL changes.

Question answered: when a link is planted inside a runtime tree that
probe_appcontainer_python_runtime grants, does the probe leave every object
outside that tree with the ACL it had before? Each run plants a junction, a
directory symlink and a file symlink inside the interpreter's Lib directory,
each pointing at a fixture outside it, then runs the real probe. The planted
links are removed afterwards.

A control replays the legacy recursive grant and revoke (icacls /T) on a
separate fixture, to show whether this runner's icacls follows the same planted
junction. Without that, a clean probe result would prove little.

Every object is compared by its own `icacls` output without /T, so the check
does not depend on the code under test. Fixtures live under one temporary
directory this script owns.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forgeos import appcontainer  # noqa: E402

PROBE_SID = ("S-1-15-2-1000000001-1000000002-1000000003-"
             "1000000004-1000000005-1000000006-1000000007")
LINK_PREFIX = "forgeos-acceptance-"


def run(arguments):
    result = subprocess.run(
        arguments, capture_output=True, text=True, timeout=300,
        shell=False, errors="replace")
    return result.returncode, (result.stdout + result.stderr).strip()


def is_link(path: Path) -> bool:
    try:
        return appcontainer._is_reparse_point(os.lstat(path))
    except OSError:
        return False


def acl(path: Path) -> str:
    # A link is read as itself (/L). Reading a link without /L would print its
    # target's ACL, which would hide a change to the link object.
    arguments = ["icacls.exe", str(path)]
    if is_link(path):
        arguments.append("/L")
    code, text = run(arguments)
    return text if code == 0 else f"<icacls exit {code}: {text[-200:]}>"


def tree_acl(root: Path) -> dict:
    """ACL text of root and everything below it, entering no link."""
    return {str(path): acl(path)
            for path in appcontainer._snapshot_objects(root)}


def mklink(flag, link: Path, target: Path) -> bool:
    arguments = ["cmd.exe", "/d", "/c", "mklink"]
    if flag:
        arguments.append(flag)
    arguments += [str(link), str(target)]
    return run(arguments)[0] == 0


def remove_link(path: Path) -> None:
    """Remove a link object only. os.rmdir and os.unlink never touch a target."""
    if not is_link(path):
        raise OSError(f"Refusing to remove something that is not a link: {path}")
    try:
        os.rmdir(path)
    except OSError:
        os.unlink(path)


def remove_links_under(root: Path) -> None:
    for path in reversed(appcontainer._snapshot_objects(root)):
        if path != root and is_link(path):
            remove_link(path)


def build_fixture(root: Path) -> dict:
    outside = root / "outside"
    (outside / "sub").mkdir(parents=True)
    secret = outside / "secret.txt"
    secret.write_text("outside-secret\n", encoding="ascii")
    nested = outside / "sub" / "nested.txt"
    nested.write_text("nested\n", encoding="ascii")
    # BUILTIN\Users, by SID: an explicit inheritable ACE, so the baseline is
    # not just the default ACL.
    run(["icacls.exe", str(outside), "/grant", "*S-1-5-32-545:(OI)(CI)(R)",
         "/Q"])
    return {"outside": outside, "sub": outside / "sub", "secret": secret,
            "nested": nested}


def plant_links(lib: Path, fixture: dict) -> dict:
    """Plant a junction, a directory symlink and a file symlink in lib."""
    specs = {
        "junction": (f"{LINK_PREFIX}junction", "/J", fixture["outside"]),
        "dir_symlink": (f"{LINK_PREFIX}dirlink", "/D", fixture["outside"]),
        "file_symlink": (f"{LINK_PREFIX}filelink.txt", None,
                         fixture["secret"]),
    }
    planted = {}
    for kind, (name, flag, target) in specs.items():
        link = lib / name
        if mklink(flag, link, target) and is_link(link):
            planted[kind] = link
    return planted


def probe_pass(lib: Path, outside_paths: list, planted: dict) -> dict:
    """Run the real probe once and compare every object before and after it."""
    outside_before = {str(path): acl(path) for path in outside_paths}
    lib_before = tree_acl(lib)
    started = time.monotonic()
    probe = appcontainer.probe_appcontainer_python_runtime()
    probe_seconds = round(time.monotonic() - started, 1)
    outside_after = {str(path): acl(path) for path in outside_paths}
    lib_after = tree_acl(lib)
    links = {str(link) for link in planted.values()}
    skipped = set(probe.get("runtime_links_not_granted", []))
    changed_outside = sorted(
        key for key in outside_before
        if outside_before[key] != outside_after[key])
    changed_lib = sorted(
        key for key in set(lib_before) | set(lib_after)
        if lib_before.get(key) != lib_after.get(key))
    return {
        "probe": probe,
        "probe_seconds": probe_seconds,
        "probe_verified": probe.get("verified") is True,
        "links_skipped_by_probe": links <= skipped,
        "planted_links_intact_after_probe": all(
            is_link(link) for link in planted.values()),
        "outside_objects_checked": len(outside_before),
        "outside_changed": changed_outside,
        "lib_objects_compared": len(lib_before),
        "lib_objects_changed_count": len(changed_lib),
        "changed_lib_objects_all_planted_links": set(changed_lib) <= links,
        "lib_changed_sample": [
            {"path": key, "before": lib_before.get(key),
             "after": lib_after.get(key)}
            for key in changed_lib[:5]],
    }


def acceptance(work: Path) -> dict:
    lib = Path(sys.base_prefix) / "Lib"
    fixture = build_fixture(work / "acceptance")
    outside_paths = [fixture[key] for key in ("outside", "sub", "secret",
                                              "nested")]
    planted = plant_links(lib, fixture)
    section = {
        "lib": str(lib),
        "planted": {kind: str(link) for kind, link in planted.items()},
    }
    try:
        # The baseline is taken with the links present, so the tree listing
        # includes them and the link objects themselves are compared.
        section["with_planted_links"] = probe_pass(
            lib, outside_paths, planted)
    finally:
        for link in planted.values():
            remove_link(link)
    # The same tree with no links: any change here is the probe's own effect.
    section["without_links"] = probe_pass(lib, [], {})
    linked = section["with_planted_links"]
    section["junction_planted"] = "junction" in planted
    section["passed"] = bool(
        linked["probe_verified"] and section["junction_planted"]
        and linked["links_skipped_by_probe"]
        and linked["planted_links_intact_after_probe"]
        and not linked["outside_changed"]
        and section["without_links"]["probe_verified"])
    return section


def control(work: Path) -> dict:
    """Legacy recursive grant and revoke, then the new helpers, on fixtures."""
    base = work / "control"
    result = {}
    for name in ("legacy", "new"):
        fixture = build_fixture(base / name)
        tree = base / name / "tree"
        (tree / "pkg").mkdir(parents=True)
        (tree / "app.py").write_text("x = 1\n", encoding="ascii")
        (tree / "pkg" / "mod.py").write_text("y = 2\n", encoding="ascii")
        junction = tree / "pkg" / f"{LINK_PREFIX}junction"
        if not mklink("/J", junction, fixture["outside"]):
            raise OSError("Control junction could not be created")
        outside_paths = [fixture[key] for key in ("outside", "sub", "secret",
                                                  "nested")]
        before = {str(path): acl(path) for path in outside_paths}
        if name == "legacy":
            # The grant and revoke exactly as main had them: recursive, with /T.
            run(["icacls.exe", str(tree), "/grant",
                 f"*{PROBE_SID}:(OI)(CI)(RX)", "/T", "/Q"])
            during = {str(path): acl(path) for path in outside_paths}
            run(["icacls.exe", str(tree), "/remove:g", f"*{PROBE_SID}",
                 "/T", "/Q"])
        else:
            skipped = appcontainer._grant_path_read_execute(tree, PROBE_SID)
            during = {str(path): acl(path) for path in outside_paths}
            appcontainer._revoke_runtime_target_grant(tree, PROBE_SID)
            result["new_links_skipped"] = [str(path) for path in skipped]
        after = {str(path): acl(path) for path in outside_paths}
        tree_left = [str(path) for path in
                     appcontainer._snapshot_objects(tree)
                     if not is_link(path) and PROBE_SID in acl(path)]
        result[name] = {
            "outside_gained_probe_sid": any(
                PROBE_SID in text for text in during.values()),
            "outside_identical_after_revoke": after == before,
            "tree_objects_still_carrying_probe_sid": tree_left,
        }
        remove_links_under(tree)
    result["passed"] = bool(
        result["new"]["outside_gained_probe_sid"] is False
        and result["new"]["outside_identical_after_revoke"] is True
        and not result["new"]["tree_objects_still_carrying_probe_sid"])
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if os.name != "nt":
        raise SystemExit("The runtime probe acceptance check is Windows-only")
    report = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "runtime_targets": [str(path) for path in
                            appcontainer._python_runtime_acl_targets()],
    }
    work = Path(tempfile.mkdtemp(prefix="forgeos-runtime-acceptance-"))
    try:
        report["acceptance"] = acceptance(work)
        report["control"] = control(work)
    except Exception as exc:  # reported in the JSON, and the run fails
        report["error"] = repr(exc)
    finally:
        try:
            remove_links_under(work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    report["passed"] = bool(
        "error" not in report
        and report.get("acceptance", {}).get("passed") is True
        and report.get("control", {}).get("passed") is True)
    args.report.write_text(json.dumps(report, indent=2) + "\n",
                           encoding="utf-8")
    acc = report.get("acceptance", {})
    linked = acc.get("with_planted_links", {})
    clean = acc.get("without_links", {})
    ctl = report.get("control", {})
    print(f"Python {report['python']}: passed={report['passed']}")
    print("  with planted links: probe verified="
          f"{linked.get('probe_verified')} in {linked.get('probe_seconds')} s")
    print("    outside objects checked="
          f"{linked.get('outside_objects_checked')}; changed="
          f"{linked.get('outside_changed')}")
    print("    Lib objects compared="
          f"{linked.get('lib_objects_compared')}; changed="
          f"{linked.get('lib_objects_changed_count')}; all changed are "
          "planted links="
          f"{linked.get('changed_lib_objects_all_planted_links')}")
    print("    links skipped by probe="
          f"{linked.get('links_skipped_by_probe')}; planted links intact="
          f"{linked.get('planted_links_intact_after_probe')}")
    for sample in linked.get("lib_changed_sample", []):
        print(f"    changed object: {sample['path']}")
        print(f"      before: {sample['before']!r}")
        print(f"      after:  {sample['after']!r}")
    print("  without links: probe verified="
          f"{clean.get('probe_verified')} in {clean.get('probe_seconds')} s; "
          f"Lib objects compared={clean.get('lib_objects_compared')}; "
          f"changed={clean.get('lib_objects_changed_count')}")
    for sample in clean.get("lib_changed_sample", []):
        print(f"    changed object: {sample['path']}")
        print(f"      before: {sample['before']!r}")
        print(f"      after:  {sample['after']!r}")
    print("  control, legacy /T grant leaked to outside: "
          f"{ctl.get('legacy', {}).get('outside_gained_probe_sid')}")
    print("  control, legacy /T outside identical after revoke: "
          f"{ctl.get('legacy', {}).get('outside_identical_after_revoke')}")
    print("  control, new helpers outside gained SID: "
          f"{ctl.get('new', {}).get('outside_gained_probe_sid')}")
    if "error" in report:
        print(f"  error: {report['error']}")
    print(f"full JSON report written to {args.report}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
