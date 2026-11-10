"""Opt-in, read-only Playwright probe. Never imported during normal ForgeOS use.

Development-only prototype: it does not attach to existing authenticated tabs,
perform actions, run under the packaged Windows EXE or bypass site restrictions.
"""
from __future__ import annotations

import argparse
from typing import Any

from .browser_observation import Observation, allowed_origin, compact_observation

# The scan limit bounds DOM traversal and the payload crossing into Python.
# Inputs and their values are deliberately never included in the snapshot.
_OBSERVE_JS = """() => {
  const rows = [];
  const walker = document.createTreeWalker(document.body || document.documentElement,
    NodeFilter.SHOW_ELEMENT);
  const kinds = new Set(['button', 'a', 'input', 'select', 'textarea',
                         'h1', 'h2', 'h3']);
  let node, visited = 0;
  while ((node = walker.nextNode()) && visited++ < 1200 && rows.length < 64) {
    const tag = node.tagName.toLowerCase();
    const role = (node.getAttribute('role') || '').toLowerCase();
    if (!kinds.has(tag) && !['button', 'link', 'tab', 'checkbox', 'radio',
                             'combobox'].includes(role)) continue;
    if (node.closest('[hidden], [aria-hidden="true"], [inert]')) continue;
    const type = (node.getAttribute('type') || '').toLowerCase();
    if (['password', 'hidden', 'file'].includes(type)) continue;
    if (node.disabled) continue;
    let kind = role || ({a:'link', h1:'heading', h2:'heading', h3:'heading'}[tag] || tag);
    if (!['button','link','input','checkbox','radio','select','textarea',
          'heading','tab','combobox'].includes(kind)) continue;
    const label = node.getAttribute('aria-label') ||
                  node.getAttribute('title') ||
                  node.getAttribute('placeholder') ||
                  ((tag === 'a' || tag === 'button' || tag[0] === 'h')
                    ? (node.textContent || '').slice(0, 160) : '');
    if (label) rows.push({kind, label: label.slice(0, 160), sensitive: false});
  }
  return rows;
}"""


def observe_page(page: Any, url: str, *, permitted_origins: frozenset[str],
                 timeout_ms: int = 10_000) -> Observation:
    """Navigate a fresh, restricted context and observe its controls once.

    The caller is responsible for request interception and closing the context.
    URL allowlisting alone is not a full network security boundary.
    """
    if type(timeout_ms) is not int or not 1000 <= timeout_ms <= 30_000:
        raise ValueError("Invalid navigation timeout")
    origin = allowed_origin(url)
    if origin not in permitted_origins:
        raise ValueError("Destination origin is not explicitly authorized")
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    if allowed_origin(page.url) not in permitted_origins:
        raise ValueError("Browser navigation left the authorized origins")
    snapshot = page.evaluate(_OBSERVE_JS)
    if not isinstance(snapshot, list):
        raise ValueError("Invalid browser observation")
    return compact_observation(snapshot)


def observe_url(url: str, *, permitted_origins: frozenset[str],
                headless: bool = True) -> Observation:
    """Launch a disposable Chromium context only for this explicit call.

    All cross-origin requests are denied by default. Navigation redirects
    may bypass a route callback; this is NOT an untrusted-URL sandbox.
    """
    origin = allowed_origin(url)
    if origin not in permitted_origins:
        raise ValueError("Destination origin is not explicitly authorized")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Optional browser tooling is missing; install forgeos[browser]") from exc
    try:
        return _probe_with_playwright(sync_playwright, url, permitted_origins, headless)
    except Exception:
        # Third-party exceptions can contain page URLs and sensitive query strings.
        raise RuntimeError("Browser probe failed; verify Playwright Chromium is installed and the site is accessible") from None


def _probe_with_playwright(sync_playwright: Any, url: str,
                           permitted_origins: frozenset[str], headless: bool) -> Observation:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            context = browser.new_context(service_workers="block", accept_downloads=False)
            try:
                # Do not load arbitrary third-party trackers, images or scripts.
                # This may break complex sites; safe failure is intentional.
                def intercept(route: Any) -> None:
                    try:
                        permitted = allowed_origin(route.request.url) in permitted_origins
                    except ValueError:
                        permitted = False
                    if permitted:
                        route.continue_()
                    else:
                        route.abort()
                context.route("**/*", intercept)
                page = context.new_page()
                return observe_page(page, url, permitted_origins=permitted_origins)
            finally:
                context.close()
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Experimental read-only browser observation")
    parser.add_argument("url", help="Public HTTPS URL to inspect; no authentication")
    parser.add_argument("--allow-origin", required=True, action="append",
                        help="Explicit HTTPS origin allowed to load; repeat if needed")
    args = parser.parse_args(argv)
    try:
        origins = frozenset(allowed_origin(value) for value in args.allow_origin)
        print(observe_url(args.url, permitted_origins=origins).text)
    except (ValueError, RuntimeError) as exc:
        parser.exit(2, f"Browser probe blocked: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
