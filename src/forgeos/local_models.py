"""Local-only OpenAI-compatible inference adapters with bounded memory use.

All endpoints are literal loopback addresses; remote/cloud API calls and redirects
are rejected. Inference binaries/model weights are managed separately.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .model_routing import (
    ALLOWED_NAME, MAX_CATALOG_BYTES, choose_installed_model,
    local_memory_settings, safe_model_file_budget, system_memory,
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 512 * 1024
BACKENDS = ("ollama", "llama.cpp", "lm-studio", "openai-local")
DEFAULT_ENDPOINTS = {
    "llama.cpp": "http://127.0.0.1:8080/v1",
    "lm-studio": "http://127.0.0.1:1234/v1",
    "openai-local": "http://127.0.0.1:8000/v1",
}


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(request: Request, *, timeout: int):
    # Explicitly disable environment proxies and cross-host redirects.
    return build_opener(ProxyHandler({}), _RejectRedirects()).open(
        request, timeout=timeout)


def validate_endpoint(endpoint: str) -> str:
    """Accept only HTTP loopback literals with the standard /v1 API root."""
    if not isinstance(endpoint, str) or len(endpoint) > 120:
        raise ValueError("Local inference endpoint must be a short URL")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError as exc:
        raise ValueError("Invalid local inference port or URL") from exc
    if (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "::1")
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or parts.path not in ("", "/", "/v1", "/v1/")
            or port is None or not 1 <= port <= 65535):
        raise ValueError(
            "Use a local-only HTTP endpoint such as http://127.0.0.1:8080/v1; "
            "remote servers and cloud APIs are not supported")
    if parts.netloc not in (f"127.0.0.1:{port}", f"[::1]:{port}"):
        raise ValueError("Only literal loopback addresses are permitted")
    return f"http://{parts.netloc}/v1"


def _read_json(request: Request, *, limit: int, timeout: int) -> object:
    with _open(request, timeout=timeout) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise RuntimeError("Local model response exceeded its memory safety limit")
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Local model endpoint returned invalid JSON") from exc


def _valid_model_name(name: str) -> bool:
    return (isinstance(name, str) and bool(ALLOWED_NAME.fullmatch(name))
            and all(segment not in ("", ".", "..") for segment in name.split("/")))


class LocalChatModel:
    """Compatible with ForgeOS's existing plan/generate_edits interface."""

    def __init__(self, *, backend: str, model: str, endpoint: str | None = None,
                 api_key: str | None = None):
        if backend not in DEFAULT_ENDPOINTS:
            raise ValueError("Unknown local inference backend")
        if not _valid_model_name(model):
            raise ValueError("Invalid local model identifier")
        if api_key is not None and (not isinstance(api_key, str)
                or not 20 <= len(api_key) <= 128
                or not all(x.isalnum() or x in "-_" for x in api_key)):
            raise ValueError("Invalid local server API key")
        self.backend = backend
        self.model = model
        self.base = validate_endpoint(endpoint or DEFAULT_ENDPOINTS[backend])
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        return headers

    def installed(self) -> list[dict]:
        req = Request(self.base + "/models", headers=self._headers())
        payload = _read_json(req, limit=MAX_CATALOG_BYTES, timeout=10)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError("Local endpoint did not expose a compatible model catalog")
        if len(payload["data"]) > 1000:
            raise RuntimeError("Local endpoint catalog is too large")
        models = []
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            name = item.get("id")
            meta = item.get("meta")
            size = item.get("size")
            if type(size) is not int and isinstance(meta, dict):
                size = meta.get("size")
            if _valid_model_name(name):
                models.append({"name": name, "size": size if type(size) is int else None})
        return models

    def _choose_model(self, prompt: str) -> str:
        if self.model != "auto":
            return self.model
        # No guessed memory sizes. Users can explicitly select an installed
        # model when the server does not report model-weight size.
        available = [item for item in self.installed()
                     if type(item["size"]) is int and item["size"] > 0]
        if not available:
            raise RuntimeError(
                "Automatic selection needs model sizes from this local server; "
                "choose a model name manually in ForgeOS")
        return choose_installed_model(
            prompt, available=available,
            memory_limit_bytes=safe_model_file_budget(system_memory()))

    def _complete(self, prompt: str, *, edits: bool = False) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Model prompt is required")
        settings = local_memory_settings()
        selected = self._choose_model(prompt)
        request_body = {
            "model": selected,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0,
            "max_tokens": (settings["num_predict"] if edits else
                           min(900, settings["num_predict"])),
        }
        encoded = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ValueError("Model request exceeds the bounded context budget")
        req = Request(
            self.base + "/chat/completions", data=encoded,
            headers={**self._headers(), "Content-Type": "application/json"},
            method="POST")
        payload = _read_json(req, limit=MAX_RESPONSE_BYTES, timeout=180)
        if not isinstance(payload, dict):
            raise RuntimeError("Local chat response must be a JSON object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RuntimeError("Local chat endpoint returned no response choices")
        first = choices[0]
        if first.get("finish_reason") == "length":
            raise RuntimeError("Local model output was truncated; use a smaller task")
        message = first.get("message")
        value = message.get("content") if isinstance(message, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("Local model returned no usable text")
        if len(value.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise RuntimeError("Local model text exceeds its memory safety limit")
        return value

    def generate_edits(self, prompt: str) -> str:
        return self._complete(prompt, edits=True)

    def plan(self, description: str) -> str:
        return self._complete(
            "You are a software planning assistant. Produce a concise, "
            "testable plan. Do not claim to have executed code. "
            "Include risks and acceptance tests.\n\nTask:\n" + description)


def create_model(*, backend: str = "ollama", model: str = "auto",
                 endpoint: str | None = None, api_key: str | None = None):
    """Preserve canonical Ollama integration while adding local adapters."""
    if backend == "ollama":
        if api_key is not None:
            raise ValueError("Ollama does not use a ForgeOS managed API key")
        if endpoint not in (None, "", "http://127.0.0.1:11434"):
            raise ValueError("Ollama uses its existing fixed local endpoint")
        if not _valid_model_name(model):
            raise ValueError("Invalid Ollama model identifier")
        from .core import Ollama
        return Ollama(model=model)
    return LocalChatModel(backend=backend, model=model,
                          endpoint=endpoint or None, api_key=api_key)
