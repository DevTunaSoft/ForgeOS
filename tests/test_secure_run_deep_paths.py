"""Diagnostic: how a deep project tree behaves when a secure snapshot is made.

Windows only. This is evidence, not a gate: it records what happens and does
not fail on the outcome. The record shows whether snapshot creation needs
extended-length paths.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

WINDOWS = os.name == "nt"


def _extended(path: Path) -> str:
    """Return the extended-length form of an absolute Windows path."""
    text = str(path)
    return text if text.startswith("\\\\?\\") else "\\\\?\\" + text


@unittest.skipUnless(WINDOWS, "Windows long paths only")
class SecureSnapshotDeepPathDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="forgeos-deep-path-")).resolve()
        self.addCleanup(shutil.rmtree, _extended(self.tmp), True)

    def test_deep_project_snapshot_diagnostic(self):
        from forgeos.test_runtime import (
            create_windows_low_integrity_snapshot, remove_test_snapshot)

        workspace = self.tmp / "projects" / "DeepProject"
        os.makedirs(_extended(workspace))
        leaf_dir = workspace
        depth = 0
        # Nest 40-character folders until the deepest file path passes MAX_PATH.
        while len(str(leaf_dir / "leaf.txt")) <= 300:
            leaf_dir = leaf_dir / ("s" * 40)
            os.makedirs(_extended(leaf_dir))
            depth += 1
        leaf = leaf_dir / "leaf.txt"
        with open(_extended(leaf), "w", encoding="ascii") as handle:
            handle.write("deep")
        exec_root = self.tmp / "secure-runs"
        os.makedirs(_extended(exec_root))
        report = {"depth": depth, "workspace_leaf_length": len(str(leaf))}
        try:
            runtime = create_windows_low_integrity_snapshot(
                workspace, runtime_root=exec_root)
        except Exception as exc:
            report["snapshot"] = {
                "ok": False, "error": type(exc).__name__,
                "message": str(exc)[:200]}
        else:
            snapped = runtime.path / leaf.relative_to(workspace)
            report["snapshot"] = {
                "ok": True,
                "leaf_length": len(str(snapped)),
                "leaf_present": os.path.exists(_extended(snapped))}
            try:
                remove_test_snapshot(workspace, runtime_root=exec_root)
            except Exception as exc:
                report["cleanup"] = {
                    "error": type(exc).__name__, "message": str(exc)[:200]}
        print("DEEP_SNAPSHOT " + json.dumps(report, sort_keys=True))
