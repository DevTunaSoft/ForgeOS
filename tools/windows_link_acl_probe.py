"""Windows evidence probe: icacls link handling and ForgeOS snapshot copies.

This diagnostic records observed behaviour on a Windows runner. It answers
three questions:

1. Does ``icacls /grant ... /T`` change objects outside the tree when the tree
   holds a directory symbolic link, a junction or a file symbolic link? Does
   adding ``/L`` change that? Does ``/L`` still reach real children?
2. What does ``forgeos.test_runtime.create_test_snapshot`` copy for those links?
3. With the code under test, can one zero-capability AppContainer child write
   or read a decoy folder or file through a link inside the snapshot?

Everything is created below one fresh scratch directory. The probe asserts no
expected result unless ``--fail-on-escape`` is given. Run it on Windows only.

Example (PowerShell, from a checkout that contains ``src``)::

    python tools/windows_link_acl_probe.py --src src --label local --out probe.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

# An AppContainer-shaped SID that no account owns. Each icacls case gets its own
# fixed-width last subauthority so that one case cannot match another case.
SID_PREFIX = "S-1-15-2-1000000001-1000000002-1000000003-1000000004-1000000005-1000000006-"
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
CHILD_NAME = "inside.txt"
CONTROL_INDEX = 7100


def _sid(index: int) -> str:
    return f"{SID_PREFIX}{index}"


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, errors="replace",
        timeout=120, shell=False)


def _tail(result: subprocess.CompletedProcess) -> str:
    return (result.stdout + result.stderr).strip()[-600:]


def _acl(path: Path) -> str:
    result = _run(["icacls.exe", str(path)])
    return result.stdout + result.stderr


def _has_ace(text: str, sid: str) -> bool:
    return f"{sid}:(" in text


def _read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _contains(path: Path, text: str) -> bool:
    content = _read_text_or_none(path)
    return content is not None and text in content


def _describe(path: Path) -> dict:
    if not os.path.lexists(path):
        return {"exists": False}
    stat_result = os.lstat(path)
    attributes = getattr(stat_result, "st_file_attributes", 0)
    info = {
        "exists": True,
        "reparse_point": bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT),
        "reparse_tag": hex(getattr(stat_result, "st_reparse_tag", 0)),
        "os_path_islink": os.path.islink(path),
    }
    if info["os_path_islink"]:
        info["kind"] = "symlink"
        info["link_target"] = os.readlink(path)
    elif info["reparse_point"]:
        info["kind"] = "reparse-point-not-symlink (junction or other)"
    elif os.path.isdir(path):
        info["kind"] = "directory"
    else:
        info["kind"] = "file"
    return info


def _mklink(kind: str, link: Path, target: Path) -> dict:
    flags = {"dir": ["/D"], "junction": ["/J"], "file": []}[kind]
    result = _run(["cmd.exe", "/d", "/c", "mklink", *flags, str(link), str(target)])
    return {
        "created": result.returncode == 0 and os.path.lexists(link),
        "exit": result.returncode,
        "output": _tail(result),
    }


def _remove_link(path: Path) -> None:
    """Remove a link object only; never recurse into its target."""
    if not os.path.lexists(path):
        return
    stat_result = os.lstat(path)
    is_link = (os.path.islink(path)
               or getattr(stat_result, "st_file_attributes", 0)
               & FILE_ATTRIBUTE_REPARSE_POINT)
    if not is_link:
        raise OSError(f"Refusing to remove a non-link path: {path}")
    try:
        os.unlink(path)  # file symbolic link
    except OSError:
        os.rmdir(path)  # directory symbolic link or junction: the link only


def _icacls_case(root: Path, index: int, kind: str, *, recursive: bool,
                 link_flag: bool) -> dict:
    name = (f"{kind}-{'recursive' if recursive else 'direct'}"
            f"-{'with' if link_flag else 'without'}-L")
    case = root / name
    tree = case / "tree"
    real_file = tree / "real" / "real.txt"
    link = tree / ("file-link.txt" if kind == "file" else f"{kind}-link")
    sid = _sid(index)
    result = {
        "name": name, "kind": kind, "recursive": recursive,
        "link_flag": link_flag, "index": index,
    }
    try:
        real_file.parent.mkdir(parents=True)
        real_file.write_text("real", encoding="utf-8")
        if kind == "file":
            target = case / "decoy.txt"
            target.write_text("decoy", encoding="utf-8")
        else:
            target = case / "decoy-dir"
            target.mkdir()
            (target / CHILD_NAME).write_text("decoy", encoding="utf-8")
        made = _mklink(kind, link, target)
        result["link_created"] = made["created"]
        result["mklink_exit"] = made["exit"]
        if not made["created"]:
            result["skipped"] = f"mklink failed: {made['output']}"
            return result

        if recursive:
            grant_path = tree
            permission = "(OI)(CI)(M)"
        else:
            grant_path = link
            permission = "(M)" if kind == "file" else "(OI)(CI)(M)"
        result["target_preexisting_ace"] = _has_ace(_acl(target), sid)
        argv = ["icacls.exe", str(grant_path), "/grant", f"*{sid}:{permission}"]
        if recursive:
            argv.append("/T")
        if link_flag:
            argv.append("/L")
        argv.append("/Q")
        grant = _run(argv)
        result["icacls_exit"] = grant.returncode
        result["icacls_flags"] = [flag for flag in argv if flag in ("/T", "/L")]
        result["icacls_output"] = _tail(grant)
        result["target_changed"] = (
            _has_ace(_acl(target), sid) and not result["target_preexisting_ace"])
        if kind != "file":
            result["outside_child_changed"] = _has_ace(
                _acl(target / CHILD_NAME), sid)
        if recursive:
            result["real_child_granted"] = _has_ace(_acl(real_file), sid)
        return result
    except Exception as exc:  # record the case and keep the matrix going
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        try:
            _remove_link(link)  # the link object only, before any rmtree
        except Exception as exc:
            result["cleanup_error"] = f"{type(exc).__name__}: {exc}"


def _control_case(root: Path) -> dict:
    """Recursive /L grant over a tree with no links: real children must receive it."""
    case = root / "control-no-links"
    tree = case / "tree"
    real_file = tree / "real" / "real.txt"
    sid = _sid(CONTROL_INDEX)
    result = {"name": "control-no-links-recursive-with-L", "index": CONTROL_INDEX}
    try:
        real_file.parent.mkdir(parents=True)
        real_file.write_text("real", encoding="utf-8")
        argv = ["icacls.exe", str(tree), "/grant", f"*{sid}:(OI)(CI)(M)",
                "/T", "/L", "/Q"]
        grant = _run(argv)
        result["icacls_exit"] = grant.returncode
        result["icacls_output"] = _tail(grant)
        result["real_child_granted"] = _has_ace(_acl(real_file), sid)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _icacls_matrix(root: Path) -> list[dict]:
    cases = []
    index = 7001
    for kind in ("dir", "junction", "file"):
        for recursive in (True, False):
            for link_flag in (False, True):
                cases.append(_icacls_case(
                    root, index, kind, recursive=recursive,
                    link_flag=link_flag))
                index += 1
    cases.append(_control_case(root))
    return cases


def _snapshot_probe(scratch: Path) -> dict:
    from forgeos.appcontainer import run_secure_appcontainer
    from forgeos.secure_runner import _bootstrap_environment
    from forgeos.test_runtime import create_test_snapshot, remove_test_snapshot

    base = scratch / "snapshot"
    workspace = base / "workspace"
    dir_decoy = base / "dir-decoy"
    junction_decoy = base / "junction-decoy"
    file_decoy = base / "file-decoy.txt"
    links = {
        "dirlink": (workspace / "dirlink", "dir", dir_decoy),
        "junction": (workspace / "junction", "junction", junction_decoy),
        "filelink.txt": (workspace / "filelink.txt", "file", file_decoy),
    }
    report = {"links_created": {}, "snapshot_entries": {}}
    try:
        workspace.mkdir(parents=True)
        (workspace / "app.py").write_text("print('source')\n", encoding="utf-8")
        dir_decoy.mkdir()
        (dir_decoy / "d1-secret.txt").write_text("D1-SECRET", encoding="utf-8")
        junction_decoy.mkdir()
        (junction_decoy / "d2-secret.txt").write_text("D2-SECRET", encoding="utf-8")
        file_decoy.write_text("F1-ORIGINAL", encoding="utf-8")
        for name, (path, kind, target) in links.items():
            report["links_created"][name] = _mklink(kind, path, target)
        dir_acl_before = _acl(dir_decoy)
        file_acl_before = _acl(file_decoy)

        runtime = create_test_snapshot(workspace)
        for name in ("app.py", "dirlink", "junction", "filelink.txt"):
            report["snapshot_entries"][name] = _describe(runtime.path / name)
        report["junction_copy_contains_d2_secret"] = _contains(
            runtime.path / "junction" / "d2-secret.txt", "D2-SECRET")

        cmd = Path(os.environ.get("ComSpec") or (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32" / "cmd.exe"))
        # No spaces: the whole command is one argv element, read by cmd.exe.
        script = "&".join([
            "echo control>control.txt",
            "echo escaped-dir>dirlink\\escape-dir.txt",
            "echo escaped-junction>junction\\escape-junction.txt",
            "echo escaped-file>filelink.txt",
            "type dirlink\\d1-secret.txt>dirlink-read.txt",
            "type junction\\d2-secret.txt>junction-read.txt",
        ])
        env = _bootstrap_environment(runtime)
        result = run_secure_appcontainer(
            [str(cmd), "/d", "/c", script], runtime.path, env,
            executable=cmd, timeout=90)
        output = result.get("output")
        report["appcontainer_run"] = {
            "passed": result.get("passed"),
            "returncode": result.get("returncode"),
            "containment": result.get("containment"),
            "profile_deleted": result.get("profile_deleted"),
            "output_tail": output[-800:] if isinstance(output, str) else None,
        }
        report["appcontainer_outcome"] = {
            "control_written": (runtime.path / "control.txt").is_file(),
            "dir_link_write_escaped": (dir_decoy / "escape-dir.txt").exists(),
            "junction_write_escaped": (junction_decoy / "escape-junction.txt").exists(),
            "file_link_write_escaped": (
                _read_text_or_none(file_decoy) not in (None, "F1-ORIGINAL")),
            "dir_link_secret_read": _contains(
                runtime.path / "dirlink-read.txt", "D1-SECRET"),
            "junction_copy_secret_read": _contains(
                runtime.path / "junction-read.txt", "D2-SECRET"),
        }
        report["decoy_acl_unchanged_after_run"] = {
            "dir": _acl(dir_decoy) == dir_acl_before,
            "file": _acl(file_decoy) == file_acl_before,
        }
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            report["snapshot_removed"] = remove_test_snapshot(workspace)
        except Exception as exc:  # cleanup must not hide the probe result
            report["snapshot_cleanup_error"] = f"{type(exc).__name__}: {exc}"
        for path, _kind, _target in links.values():
            try:
                _remove_link(path)
            except OSError as exc:
                report.setdefault("workspace_link_cleanup_errors", []).append(
                    str(exc))
    return report


def _summarize(report: dict) -> dict:
    cases = report.get("icacls_matrix", [])
    snapshot = report.get("snapshot", {})
    outcome = snapshot.get("appcontainer_outcome", {})
    exposure_keys = (
        "dir_link_write_escaped", "junction_write_escaped",
        "file_link_write_escaped", "dir_link_secret_read",
        "junction_copy_secret_read",
    )
    with_l_real = [c["real_child_granted"] for c in cases
                   if c.get("recursive") and c.get("link_flag")
                   and "real_child_granted" in c]
    return {
        "icacls_outside_change_without_L": [
            c["name"] for c in cases
            if c.get("target_changed") and not c.get("link_flag")],
        "icacls_outside_change_with_L": [
            c["name"] for c in cases
            if c.get("target_changed") and c.get("link_flag")],
        "recursive_with_L_reaches_real_children": (
            all(with_l_real) if with_l_real else None),
        "snapshot_copy_kinds": {
            name: entry.get("kind")
            for name, entry in snapshot.get("snapshot_entries", {}).items()},
        "appcontainer_exposure_observed": any(
            outcome.get(key) for key in exposure_keys),
        "appcontainer_outcome": outcome,
        "probe_error": snapshot.get("error"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--src", default="src",
        help="Directory that contains the forgeos package to exercise")
    parser.add_argument(
        "--label", default="unlabelled",
        help="Name recorded in the report, for example main or a commit")
    parser.add_argument(
        "--out", type=Path, help="Optional path for the JSON report")
    parser.add_argument(
        "--fail-on-escape", action="store_true",
        help="Exit 1 when the AppContainer run wrote or read outside the snapshot")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("This probe needs Windows; nothing was run.", file=sys.stderr)
        return 2

    src = Path(args.src).resolve()
    sys.path.insert(0, str(src))
    scratch = Path(tempfile.mkdtemp(prefix="forgeos-link-acl-probe-"))
    report = {
        "probe": "windows-link-acl",
        "label": args.label,
        "code_under_test": str(src),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "runner_image": {
            "ImageOS": os.environ.get("ImageOS"),
            "ImageVersion": os.environ.get("ImageVersion"),
        },
    }
    try:
        report["icacls_matrix"] = _icacls_matrix(scratch)
        report["snapshot"] = _snapshot_probe(scratch)
        report["summary"] = _summarize(report)
    except Exception as exc:
        report["probe_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    text = json.dumps(report, indent=2, sort_keys=True)
    print("FORGEOS_LINK_ACL_PROBE_BEGIN")
    print(text)
    print("FORGEOS_LINK_ACL_PROBE_END")
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    exposed = report.get("summary", {}).get("appcontainer_exposure_observed")
    if args.fail_on_escape and exposed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
