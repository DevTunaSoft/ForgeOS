"""Browser-agent prototype tests never launch a browser or make network calls."""
import importlib
import sys
from unittest import TestCase
from unittest.mock import MagicMock, Mock

from forgeos.browser_observation import allowed_origin, compact_observation
from forgeos.browser_probe import observe_page


class BrowserObservationTests(TestCase):
    def test_public_https_origin_strips_sensitive_query_and_fragment(self):
        self.assertEqual(allowed_origin("https://EXAMPLE.com/path?token=secret#abc"),
                         "https://example.com")

    def test_block_local_credentials_ip_literals_and_non_https(self):
        for url in ("http://example.com", "https://localhost/", "https://x.local/",
                    "https://127.0.0.1/", "https://[::1]/", "https://user:pass@example.com/",
                    "https://intranet/", "https://example.com:9443/", "https://example.com/x\n"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                allowed_origin(url)

    def test_compact_and_redact(self):
        entries = [
            {"kind": "button", "label": "Continue"},
            {"kind": "input", "label": "password=please-dont-log-this", "sensitive": True},
            {"kind": "link", "label": "contact bob@example.org"},
            {"kind": "heading", "label": "Authorization: verysecretsecretvalue"},
            {"kind": "button", "label": "Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ12345678901234567890"},
            {"kind": "button", "label": "Open\n Settings"},
            {"kind": "link", "label": "Authorization: Bearer topsecretvalue"},
        ]
        result = compact_observation(entries)
        self.assertEqual(result.controls, 6)
        self.assertIn("[EMAIL]", result.text)
        self.assertNotIn("bob@example.org", result.text)
        self.assertNotIn("verysecretsecretvalue", result.text)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ", result.text)
        self.assertNotIn("please-dont-log-this", result.text)
        self.assertNotIn("topsecretvalue", result.text)
        self.assertIn("Open Settings", result.text)
        self.assertIn("UNTRUSTED", result.text)

    def test_generator_is_bounded(self):
        visited = []
        def entries():
            for i in range(100000):
                visited.append(i)
                yield {"kind": "button", "label": str(i)}
        result = compact_observation(entries())
        self.assertEqual(result.controls, 48)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(visited), 49)

    def test_budget_is_bounded(self):
        result = compact_observation(({"kind": "heading", "label": "A" * 200}
                                      for _ in range(100)), max_chars=256)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.text), 256)
        self.assertTrue(result.controls >= 1)

    def test_malformed_and_private_input_values_not_exposed(self):
        result = compact_observation([None, 123, {"kind": "password", "label": "bad"},
                                      {"kind": "input", "label": "User name", "value": "TOKEN_SECRET"},
                                      {"kind": "button", "label": "Run", "id": "fake-id"}])
        self.assertNotIn("TOKEN_SECRET", result.text)
        self.assertNotIn("fake-id", result.text)
        self.assertEqual(result.controls, 2)

    def test_reject_bad_limits(self):
        for kw in ({"max_chars": 12}, {"max_chars": True}, {"max_nodes": 0},
                   {"max_nodes": 999}, {"max_nodes": False}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                compact_observation([], **kw)

    def test_probe_refuses_unauthorized_navigation_without_side_effects(self):
        page = Mock()
        with self.assertRaises(ValueError):
            observe_page(page, "https://unknown.example/a", permitted_origins=frozenset({"https://example.com"}))
        page.goto.assert_not_called()

    def test_probe_rejects_cross_origin_redirect_without_observation(self):
        page = Mock(url="https://evil.example/redirected")
        with self.assertRaises(ValueError):
            observe_page(page, "https://example.com/", permitted_origins=frozenset({"https://example.com"}))
        page.evaluate.assert_not_called()

    def test_probe_with_fake_page(self):
        page = Mock(url="https://example.com/path")
        page.evaluate.return_value = [{"kind": "button", "label": "Start"}]
        result = observe_page(page, "https://example.com/", permitted_origins=frozenset({"https://example.com"}))
        self.assertIn("Start", result.text)
        self.assertEqual(page.goto.call_count, 1)

    def test_no_browser_framework_loaded_on_import(self):
        importlib.import_module("forgeos.browser_observation")
        self.assertNotIn("playwright", sys.modules)

class BrowserProbeLifecycleTests(TestCase):
    def test_browser_is_launched_on_demand_and_closed(self):
        from forgeos.browser_probe import _probe_with_playwright
        factory = MagicMock()
        playwright = factory.return_value.__enter__.return_value
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = "https://example.com/"
        page.evaluate.return_value = [{"kind": "button", "label": "Save"}]

        result = _probe_with_playwright(factory, "https://example.com/",
                                       frozenset({"https://example.com"}), True)
        self.assertIn("Save", result.text)
        playwright.chromium.launch.assert_called_once_with(headless=True)
        browser.new_context.assert_called_once_with(service_workers="block", accept_downloads=False)
        context.close.assert_called_once()
        browser.close.assert_called_once()

        # Inspect the installed request-interception callback without making HTTP calls.
        interceptor = context.route.call_args.args[1]
        allowed = Mock()
        allowed.request.url = "https://example.com/app.js"
        interceptor(allowed)
        allowed.continue_.assert_called_once()
        blocked = Mock()
        blocked.request.url = "https://tracking.example.net/pixel"
        interceptor(blocked)
        blocked.abort.assert_called_once()
