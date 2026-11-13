"""Opt-in bounded read-only discovery of public Hugging Face GGUF catalogs.

No models are executed or downloaded here; Hub metadata is untrusted.
The Hub is contacted only after an explicit user action.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

MAX_HUB_JSON_BYTES = 2 * 1024 * 1024
REPO_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")


COMMIT_SHA = re.compile(r"[a-fA-F0-9]{40}\Z")


def _hub_json(url: str) -> object:
    # All URLs are constructed from a fixed HTTPS origin; no user URLs accepted.
    # Catalog requests are metadata-only. Never send authentication headers.
    req = Request(url, headers={"Accept": "application/json",
                                "User-Agent": "ForgeOS-model-catalog/0.1"})
    with urlopen(req, timeout=15) as response:
        content = response.read(MAX_HUB_JSON_BYTES + 1)
    if len(content) > MAX_HUB_JSON_BYTES:
        raise RuntimeError("Hugging Face catalog exceeded the bounded metadata limit")
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Hugging Face catalog contained invalid JSON") from exc


def search_gguf(query: str, *, limit: int = 15) -> list[dict]:
    """List public candidate GGUF repositories, not verified running models."""
    if (not isinstance(query, str) or not 2 <= len(query.strip()) <= 64
            or any(ord(c) < 32 for c in query)):
        raise ValueError("Enter a search phrase of 2–64 characters")
    if type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError("Catalog result limit must be 1–30")
    params = urlencode({"search": query.strip(), "limit": limit,
                        "sort": "downloads", "direction": "-1"})
    payload = _hub_json("https://huggingface.co/api/models?" + params)
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
        license_tags = [t.split(":", 1)[1] for t in tags
                        if isinstance(t, str) and t.startswith("license:")]
        result.append({
            "repo": name, "downloads": entry.get("downloads", 0)
            if type(entry.get("downloads")) is int else 0,
            "license_tag": license_tags[0] if license_tags else "unverified",
            "verified_compatible": False,
        })
    return result


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
                value = " ".join(value.split())[:240]
                if value:
                    claims[key] = value
            elif isinstance(value, list):
                cleaned = [" ".join(v.split())[:100] for v in value
                           if isinstance(v, str) and v.strip()]
                if cleaned:
                    claims[key] = cleaned[:12]
    if "author" not in claims and isinstance(item.get("author"), str):
        author = " ".join(item["author"].split())[:240]
        if author:
            claims["author"] = author
    tags = item.get("tags")
    if "license" not in claims and isinstance(tags, list):
        license_tags = [t.split(":", 1)[1] for t in tags
                        if isinstance(t, str) and t.startswith("license:")]
        if license_tags:
            claims["license"] = license_tags[0][:100]
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
        if (not isinstance(name, str) or not name.lower().endswith(".gguf")
                or type(size) is not int or size <= 0
                or ".." in name.split("/") or name.startswith("/")):
            continue
        lfs = item.get("lfs")
        oid = lfs.get("oid") if isinstance(lfs, dict) else None
        output.append({
            "path": name, "bytes": size,
            "sha256": oid.lower() if isinstance(oid, str) and SHA256.fullmatch(oid) else None,
        })
    return output
