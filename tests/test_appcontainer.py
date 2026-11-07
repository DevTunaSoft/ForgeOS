"""Classic Windows AppContainer capability probe tests."""
import os
import unittest

from forgeos.appcontainer import (probe_appcontainer, probe_appcontainer_filesystem,
                                  probe_appcontainer_network)


class AppContainerProbeContractTests(unittest.TestCase):
    def test_non_windows_reports_unavailable(self):
        if os.name == "nt":
            self.skipTest("non-Windows contract")
        result = probe_appcontainer()
        self.assertFalse(result["available"])
        self.assertFalse(result["verified"])


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
