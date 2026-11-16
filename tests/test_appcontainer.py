"""Classic Windows AppContainer capability probe tests."""
import os
from pathlib import Path
import tempfile
import unittest

from forgeos.appcontainer import (_verify_probe_marker, probe_appcontainer,
                                  probe_appcontainer_filesystem,
                                  probe_appcontainer_network,
                                  probe_appcontainer_python_runtime)


class AppContainerProbeContractTests(unittest.TestCase):
    def test_non_windows_reports_unavailable(self):
        if os.name == "nt":
            self.skipTest("non-Windows contract")
        result = probe_appcontainer()
        self.assertFalse(result["available"])
        self.assertFalse(result["verified"])


class ProbeMarkerVerificationTests(unittest.TestCase):
    """The contained child's write marker is untrusted input to the parent."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.marker = self.root / "python-created.txt"

    def test_exact_marker_is_accepted(self):
        self.marker.write_bytes(b"ok")
        _verify_probe_marker(self.marker)

    def test_oversized_marker_is_refused_before_it_is_buffered(self):
        self.marker.write_bytes(b"x" * (5 * 1024 * 1024))
        with self.assertRaisesRegex(OSError, "oversized"):
            _verify_probe_marker(self.marker)

    def test_marker_with_trailing_bytes_is_refused(self):
        self.marker.write_bytes(b"ok\n")
        with self.assertRaises(OSError):
            _verify_probe_marker(self.marker)

    def test_missing_marker_is_refused(self):
        with self.assertRaises(OSError):
            _verify_probe_marker(self.marker)

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlinks unavailable")
    def test_symlink_to_an_outside_marker_is_refused(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(b"ok")
        try:
            os.symlink(outside, self.marker)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(OSError):
            _verify_probe_marker(self.marker)


@unittest.skipUnless(os.name == "nt", "Windows-only AppContainer probe")
class WindowsAppContainerProbeTests(unittest.TestCase):
    def test_zero_capability_child_is_verified_appcontainer(self):
        result = probe_appcontainer()
        self.assertTrue(result["available"], result)
        self.assertTrue(result["verified"], result)
        self.assertTrue(result["token_is_appcontainer"])
        self.assertEqual(result["capability_count"], 0)
        self.assertFalse(result["network_capabilities_granted"])
        self.assertTrue(result["profile"].startswith("ForgeOS.Probe."))
        self.assertTrue(result["profile_deleted"])


    def test_explicit_workspace_access_and_ungranted_read_isolation(self):
        result = probe_appcontainer_filesystem()
        self.assertTrue(result["available"], result)
        self.assertTrue(result["verified"], result)
        self.assertTrue(result["workspace_write"], result)
        self.assertTrue(result["ungranted_sibling_read_blocked"], result)
        self.assertTrue(result["token_is_appcontainer"], result)
        self.assertEqual(result["capability_count"], 0, result)
        self.assertFalse(result["network_capabilities_granted"], result)
        self.assertTrue(result["profile_deleted"], result)


    def test_zero_capability_appcontainer_blocks_loopback_network(self):
        result = probe_appcontainer_network()
        self.assertTrue(result["available"], result)
        self.assertTrue(result["verified"], result)
        self.assertTrue(result["client_executable_verified"], result)
        self.assertTrue(result["loopback_connection_blocked"], result)
        self.assertTrue(result["token_is_appcontainer"], result)
        self.assertEqual(result["capability_count"], 0, result)
        self.assertFalse(result["network_capabilities_granted"], result)
        self.assertTrue(result["profile_deleted"], result)


    def test_active_python_runtime_runs_inside_appcontainer(self):
        result = probe_appcontainer_python_runtime()
        self.assertTrue(result["available"], result)
        self.assertTrue(result["verified"], result)
        self.assertTrue(result["python_startup"], result)
        self.assertTrue(result["workspace_write"], result)
        self.assertIsInstance(result["host_runtime_preexposed"], bool)
        self.assertTrue(result["runtime_acl_targets"], result)
        self.assertTrue(result["temporary_runtime_grants_revoked"], result)
        self.assertTrue(result["token_is_appcontainer"], result)
        self.assertEqual(result["capability_count"], 0, result)
        self.assertFalse(result["network_capabilities_granted"], result)
        self.assertTrue(result["profile_deleted"], result)
