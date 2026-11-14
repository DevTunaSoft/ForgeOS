"""Opt-in bounded read-only discovery of public Hugging Face GGUF catalogs.

No models are executed or downloaded here; Hub metadata is untrusted.
The Hub is contacted only after an explicit user action.
"""
from __future__ import annotations

import json
import re
import socket
import time
import unicodedata
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)

MAX_HUB_JSON_BYTES = 2 * 1024 * 1024
MAX_HUB_RESPONSE_SECONDS = 30
MAX_GGUF_BYTES = 16 * 1024 ** 3
REPO_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")
SAFE_FILE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{x}" for x in range(1, 10)),
    *(f"LPT{x}" for x in range(1, 10)),
})


COMMIT_SHA = re.compile(r"[a-fA-F0-9]{40}\Z")


class _HubRedirectHandler(HTTPRedirectHandler):
    """Allow only same-origin HTTPS redirects for public Hub metadata."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            origin = urlsplit(req.full_url)
            target = urlsplit(newurl)
            target_port = target.port
        except ValueError as exc:
            raise RuntimeError("Hugging Face returned an invalid redirect") from exc
        if (origin.scheme != "https" or origin.hostname != "huggingface.co"
                or target.scheme != "https" or target.hostname != "huggingface.co"
                or target_port not in (None, 443)
                or target.username is not None or target.password is not None):
            raise RuntimeError(
                "Hugging Face metadata redirected outside its trusted HTTPS origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_hub(request: Request, *, timeout: float):
    """Open Hub metadata without ambient proxies or cross-origin redirects."""
    opener = build_opener(ProxyHandler({}), _HubRedirectHandler())
    return opener.open(request, timeout=timeout)


def _clean_claim(value: str, limit: int) -> str:
    """Normalize untrusted card text and remove Unicode control/format chars."""
    visible = "".join(" " if unicodedata.category(char).startswith("C") else char
                      for char in value)
    return " ".join(visible.split())[:limit]


def _safe_hub_gguf_path(name: object) -> bool:
    if (not isinstance(name, str) or len(name) > 320
            or not name.lower().endswith(".gguf") or "\\" in name
            or ":" in name):
        return False
    segments = name.split("/")
    return (len(segments) <= 8 and all(
        SAFE_FILE_PART.fullmatch(part) and part not in (".", "..")
        and part.split(".", 1)[0].upper() not in WINDOWS_RESERVED_NAMES
        for part in segments))


def catalog_error_message(exc: Exception) -> str:
    """Convert common Hub HTTP/network failures to actionable UI text."""
    if isinstance(exc, HTTPError):
        try:
            if exc.code in (401, 403):
                return ("Hugging Face denied access. The repository may be private "
                        "or gated; inspect its access requirements in a browser.")
            if exc.code == 404:
                return "Hugging Face could not find this repository, file, or revision."
            if exc.code == 429:
                return "Hugging Face is rate limiting requests. Wait briefly, then retry."
            if 500 <= exc.code <= 599:
                return f"Hugging Face is temporarily unavailable (HTTP {exc.code})."
            return (f"Hugging Face returned HTTP {exc.code}. "
                    "Check the repository and retry.")
        finally:
            exc.close()
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            return ("Could not find Hugging Face on the network (DNS lookup failed). "
                    "Check internet access, then retry.")
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "The Hugging Face request timed out. Check internet access, then retry."
        if isinstance(reason, OSError):
            return "Could not connect to Hugging Face. Check network access, then retry."
        return "The Hugging Face request failed. Check network access, then retry."
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "The Hugging Face request timed out. Check internet access, then retry."
    return f"{type(exc).__name__}: {exc}"


def _hub_json_response(url: str) -> tuple[object, str | None]:
    # All URLs are constructed from a fixed HTTPS origin; no user URLs accepted.
    # Catalog requests are metadata-only. Never send authentication headers.
    req = Request(url, headers={"Accept": "application/json",
                                "User-Agent": "ForgeOS-model-catalog/0.1"})
    deadline = time.monotonic() + MAX_HUB_RESPONSE_SECONDS
    content = bytearray()
    with _open_hub(req, timeout=15) as response:
        link_header = getattr(response, "headers", {}).get("Link")
        reader = getattr(response, "read1", response.read)
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Hugging Face catalog exceeded its total response deadline")
            chunk = reader(min(8192, MAX_HUB_JSON_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_HUB_JSON_BYTES:
                raise RuntimeError(
                    "Hugging Face catalog exceeded the bounded metadata limit")
    if time.monotonic() >= deadline:
        raise TimeoutError(
            "Hugging Face catalog exceeded its total response deadline")
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Hugging Face catalog contained invalid JSON") from exc
    next_match = re.search(
        r'<([^<>]+)>\s*;\s*rel\s*=\s*"?next"?',
        link_header or "", re.IGNORECASE)
    has_next_relation = bool(re.search(
        r'\brel\s*=\s*"?next"?', link_header or "", re.IGNORECASE))
    if has_next_relation and not next_match:
        raise RuntimeError("Hugging Face returned a malformed pagination link")
    return payload, next_match.group(1) if next_match else None


def _hub_json(url: str) -> object:
    return _hub_json_response(url)[0]


def _validate_search(query: str, limit: int) -> str:
    if (not isinstance(query, str) or not 2 <= len(query.strip()) <= 64
            or any(ord(c) < 32 for c in query)):
        raise ValueError("Enter a search phrase of 2–64 characters")
    if type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("Catalog result limit must be 1–30")
    return query.strip()


def _search_next_url(url: str, query: str, limit: int) -> str:
    if not isinstance(url, str) or len(url) > 4096:
        raise RuntimeError("Hugging Face pagination link exceeded its limit")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Hugging Face returned an invalid pagination link") from exc
    params = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "https" or parsed.hostname != "huggingface.co"
            or port not in (None, 443) or parsed.username is not None
            or parsed.password is not None or parsed.path != "/api/models"
            or parsed.fragment or params.get("search") != [query]
            or params.get("limit") != [str(limit)]):
        raise RuntimeError("Hugging Face returned an untrusted pagination link")
    return url


def search_gguf_page(query: str, *, limit: int = 15,
                     next_page_url: str | None = None) -> dict:
    """Fetch one bounded Hub search page and its validated opaque continuation."""
    query = _validate_search(query, limit)
    params = urlencode({"search": query.strip(), "limit": limit,
                        "sort": "downloads", "direction": "-1"})
    url = ("https://huggingface.co/api/models?" + params if next_page_url is None
           else _search_next_url(next_page_url, query, limit))
    payload, continuation = _hub_json_response(url)
    if not isinstance(payload, list) or len(payload) > limit:
        raise RuntimeError("Unexpected Hugging Face search results")
    result = []
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("private") or entry.get("gated"):
            continue
        name = entry.get("id")
        tags = entry.get("tags") or []
        if (not isinstance(name, str) or not REPO_ID.fullmatch(name)
                or not isinstance(tags, list)):
            continue
        if not ("gguf" in name.lower() or
                any(isinstance(t, str) and "gguf" in t.lower() for t in tags)):
            continue
        license_tags = [_clean_claim(t.split(":", 1)[1], 100) for t in tags
                        if isinstance(t, str) and t.startswith("license:")]
        license_tags = [tag for tag in license_tags if tag]
        result.append({
            "repo": name, "downloads": entry.get("downloads", 0)
            if type(entry.get("downloads")) is int else 0,
            "license_tag": license_tags[0] if license_tags else "unverified",
            "verified_compatible": False,
        })
    next_page = (_search_next_url(continuation, query, limit)
                 if continuation else None)
    return {"items": result, "next_page_url": next_page}


def search_gguf(query: str, *, limit: int = 15) -> list[dict]:
    """List one page of public GGUF repositories, not verified running models."""
    return search_gguf_page(query, limit=limit)["items"]


def model_revision(repo: str) -> str:
    """Resolve a public repository to one immutable Git commit revision."""
    if not isinstance(repo, str) or not REPO_ID.fullmatch(repo):
        raise ValueError("Invalid Hugging Face repository identifier")
    url = ("https://huggingface.co/api/models/" +
           "/".join(quote(part, safe="") for part in repo.split("/")))
    item = _hub_json(url)
    if (not isinstance(item, dict) or item.get("gated") or item.get("private")
            or not isinstance(item.get("sha"), str)
            or not COMMIT_SHA.fullmatch(item["sha"])):
        raise RuntimeError("Public Hugging Face revision unavailable or gated")
    return item["sha"].lower()


_CARD_FIELDS = (
    "license", "author", "model_name", "model_type", "pipeline_tag",
    "library_name", "base_model", "language", "datasets", "tags",
    "model_size", "parameter_count", "quantization", "context_length",
    "chat_template", "tool_use", "structured_output",
)


def inspect_gguf_repo(repo: str) -> dict:
    """Return bounded Hub card claims and GGUF file metadata for a public repo.

    Card values are untrusted author-supplied claims, not compatibility,
    licensing, or safety verification.
    """
    if not isinstance(repo, str) or not REPO_ID.fullmatch(repo):
        raise ValueError("Invalid Hugging Face repository identifier")
    url = ("https://huggingface.co/api/models/" +
           "/".join(quote(part, safe="") for part in repo.split("/")) +
           "?expand=cardData,sha,tags,author")
    item = _hub_json(url)
    if not isinstance(item, dict) or item.get("gated") or item.get("private"):
        raise RuntimeError("Public Hugging Face repository metadata unavailable")
    revision = item.get("sha")
    if not isinstance(revision, str) or not COMMIT_SHA.fullmatch(revision):
        raise RuntimeError("Repository inspection could not pin an immutable revision")
    card = item.get("cardData")
    claims = {}
    if isinstance(card, dict):
        for key in _CARD_FIELDS:
            value = card.get(key)
            if isinstance(value, str):
                value = _clean_claim(value, 240)
                if value:
                    claims[key] = value
            elif type(value) is int and 0 <= value <= 10**15:
                claims[key] = str(value)
            elif isinstance(value, list):
                cleaned = [_clean_claim(v, 100) for v in value
                           if isinstance(v, str) and v.strip()]
                cleaned = [v for v in cleaned if v]
                if cleaned:
                    claims[key] = cleaned[:12]
    if "author" not in claims and isinstance(item.get("author"), str):
        author = _clean_claim(item["author"], 240)
        if author:
            claims["author"] = author
    tags = item.get("tags")
    if "license" not in claims and isinstance(tags, list):
        license_tags = [_clean_claim(t.split(":", 1)[1], 100) for t in tags
                        if isinstance(t, str) and t.startswith("license:")]
        license_tags = [tag for tag in license_tags if tag]
        if license_tags:
            claims["license"] = license_tags[0]
    files = list_gguf_files(repo, revision=revision.lower())
    return {"repo": repo, "revision": revision.lower(),
            "claims": claims, "files": files}


def list_gguf_files(repo: str, *, revision: str = "main") -> list[dict]:
    """Inspect one repository's GGUF weights and trusted LFS hashes if exposed."""
    if not isinstance(repo, str) or not REPO_ID.fullmatch(repo):
        raise ValueError("Invalid Hugging Face repository identifier")
    if not isinstance(revision, str) or (
            revision != "main" and not COMMIT_SHA.fullmatch(revision)):
        raise ValueError("File listing requires a full pinned revision")
    url = ("https://huggingface.co/api/models/" +
           "/".join(quote(part, safe="") for part in repo.split("/")) +
           "/tree/" + revision + "?recursive=true")
    payload = _hub_json(url)
    if not isinstance(payload, list) or len(payload) > 2000:
        raise RuntimeError("Unexpected or oversized Hugging Face file listing")
    output = []
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        name = item.get("path")
        size = item.get("size")
        if (not _safe_hub_gguf_path(name) or type(size) is not int
                or not 0 < size <= MAX_GGUF_BYTES):
            continue
        lfs = item.get("lfs")
        oid = lfs.get("oid") if isinstance(lfs, dict) else None
        output.append({
            "path": name, "bytes": size,
            "sha256": oid.lower() if isinstance(oid, str) and SHA256.fullmatch(oid) else None,
        })
    return output
