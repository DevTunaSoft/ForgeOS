"""Hash-pinned optional diagnostic filters; never execute rules or hide failures."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

MAX_CONFIG_BYTES = 32768
MAX_RULES = 30
IMPORTANT = re.compile(
    r"(?i)\b(?:error|warning|fail(?:ed|ure)?|traceback|exception|panic|"
    r"assert|critical|fatal|passed|skipped|tests?|timeout|denied|security)\b"
)
PRESET = re.compile(r"(?:[a-z][a-z0-9-]{0,59}|\*)\Z")
SAFE_NOISE = re.compile(r"(?i)^\s*(?:INFO\b|DEBUG\b|TRACE\b|Downloading\b|Progress\b|Fetching\b)")


def _read_safe(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Unsafe or unavailable filter configuration")
    with path.open("rb") as stream:
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("Filter configuration too large")
    return raw


def _parse(raw: bytes) -> list[dict]:
    try:
        config = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Invalid filter JSON") from exc
    if (not isinstance(config, dict) or set(config) != {"version", "rules"}
            or type(config["version"]) is not int or config["version"] != 1):
        raise ValueError("Unsupported filter configuration schema")
    rules = config["rules"]
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise ValueError("Invalid number of filter rules")
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"preset", "contains", "min_repeats"}:
            raise ValueError("Invalid filter rule")
        name, needle, count = rule["preset"], rule["contains"], rule["min_repeats"]
        if not isinstance(name, str) or not PRESET.fullmatch(name):
            raise ValueError("Invalid test preset selector")
        if (not isinstance(needle, str) or not 2 <= len(needle) <= 120
                or any(c in needle for c in ("\n", "\r", "\0"))):
            raise ValueError("Unsafe filter literal")
        if type(count) is not int or not 3 <= count <= 100:
            raise ValueError("Invalid repeated-line threshold")
    return rules


class TrustedOutputRules:
    """Trust is consent and tamper evidence, not a same-user security boundary."""

    def __init__(self, config: str | Path | None = None,
                 trust: str | Path | None = None):
        base = Path.home() / ".forgeos"
        self.config = Path(config) if config is not None else base / "output_filters.json"
        self.trust = Path(trust) if trust is not None else base / "trusted_output_filters.json"

    def _entries(self) -> dict:
        if not self.trust.exists():
            return {}
        try:
            document = json.loads(_read_safe(self.trust).decode("utf-8"))
            if isinstance(document, dict) and document.get("version") == 1:
                trusted = document.get("trusted")
                if isinstance(trusted, dict):
                    return trusted
        except (OSError, ValueError, UnicodeError):
            pass
        return {}

    def status(self) -> str:
        return self.enabled_rules()[1]

    def trust_config(self) -> str:
        raw = _read_safe(self.config)
        _parse(raw)
        digest = hashlib.sha256(raw).hexdigest()
        entries = self._entries()
        entries[str(self.config.resolve())] = digest
        self.trust.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.trust.is_symlink() or self.trust.parent.is_symlink():
            raise ValueError("Unsafe filter trust store")
        encoded = json.dumps({"version": 1, "trusted": entries},
                             sort_keys=True).encode("utf-8")
        fd, temp = tempfile.mkstemp(dir=self.trust.parent, prefix=".forgeos-")
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as destination:
                destination.write(encoded)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temp, self.trust)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return digest

    def enabled_rules(self) -> tuple[list[dict], str]:
        if not self.config.exists():
            return [], "not-configured"
        try:
            # Read once, parse those exact bytes, and validate their exact hash.
            raw = _read_safe(self.config)
            rules = _parse(raw)
            expected = self._entries().get(str(self.config.resolve()))
            if expected != hashlib.sha256(raw).hexdigest():
                return [], "untrusted-or-modified"
            return rules, "trusted"
        except (OSError, ValueError):
            return [], "invalid"


def deduplicate_output(raw: str, preset: str, rules: list[dict] | None = None
                       ) -> tuple[str, int]:
    """Deduplicate only adjacent exact, noncritical lines, retaining error counts."""
    if not isinstance(raw, str) or not isinstance(preset, str):
        raise TypeError("Text and preset must be strings")
    lines = raw.splitlines(keepends=True)
    output = []
    suppressed = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        end = i + 1
        while end < len(lines) and lines[end] == line:
            end += 1
        repetitions = end - i
        threshold = 5
        eligible = SAFE_NOISE.match(line) is not None
        for rule in rules or ():
            if rule["preset"] in ("*", preset) and rule["contains"] in line:
                eligible = True
                threshold = min(threshold, rule["min_repeats"])
        # Unknown lines might be uncategorized faults: preserve verbatim.
        if (eligible and repetitions >= threshold and line.strip()
                and not IMPORTANT.search(line)):
            output.append(line)
            output.append(f"[ForgeOS: {repetitions - 1} repeated noncritical lines omitted]\n")
            suppressed += repetitions - 1
        else:
            output.extend(lines[i:end])
        i = end
    return "".join(output), suppressed
