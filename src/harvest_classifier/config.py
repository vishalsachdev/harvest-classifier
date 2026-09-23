"""Configuration.

Two rules shape this file:

1. **A fresh install classifies nothing.** The allow-list defaults to empty, so
   the classifier sends nothing anywhere until somebody names a session.
2. **Unknown keys are fatal.** A typo in a safety setting must be loud, not
   silently ignored, so both unknown sections and unknown keys raise.
"""
from __future__ import annotations

import os
import pathlib
import re
import tomllib
from dataclasses import dataclass, field, replace
from typing import Any

#: Environment variable holding the provider key, when no key file is set.
API_KEY_ENV = "TYPESAFE_API_KEY"

DEFAULT_STATUS_DIR = pathlib.Path.home() / ".claude" / "state" / "fleet"
DEFAULT_FRESH_SECONDS = 900

#: A deliberately generic default: a short lowercase handle with trailing
#: digits, which is the common shape of an institutional login. Set
#: `guard.institution_identifier` to whatever your institution actually issues.
DEFAULT_INSTITUTION_IDENTIFIER = r"(?<![A-Za-z0-9._-])[a-z]{2,8}\d{1,4}(?![A-Za-z0-9._@-])"

_SCHEMA: dict[str, dict[str, type]] = {
    "agents": {"allow": list, "deny": list, "deny_paths": list},
    "status": {"dir": str, "fresh_seconds": int},
    "guard": {"private_path_prefixes": list, "institution_identifier": str},
    "provider": {"api_key_file": str, "model": str},
    "log": {"dir": str},
}


#: Printable ASCII without space: what may safely go in an HTTP header value.
_HEADER_SAFE = re.compile(r"[\x21-\x7e]+")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    #: Empty by default. Nothing is classified until a session is named here.
    allow_list: frozenset[str] = frozenset()
    #: Checked first: a denied name can never be classified, even if it also
    #: appears in the allow-list.
    deny_list: frozenset[str] = frozenset()
    #: Directory names whose contents must never be classified, whatever a
    #: session calls itself. Ships empty.
    deny_paths: tuple[str, ...] = ()
    status_dir: pathlib.Path = DEFAULT_STATUS_DIR
    fresh_seconds: int = DEFAULT_FRESH_SECONDS
    private_path_prefixes: tuple[str, ...] = ()
    institution_identifier: str = DEFAULT_INSTITUTION_IDENTIFIER
    api_key_file: pathlib.Path | None = None
    model: str = "jev-1.13.0"
    log_dir: pathlib.Path = field(
        default_factory=lambda: pathlib.Path.cwd() / "shadow-log")

    def is_classifiable(self, agent: str | None) -> bool:
        """Fail closed. Unknown names and names needing normalisation are out.

        Comparison is case-insensitive on both lists, so a differently cased
        allow entry cannot slip past a deny entry (review, 2026-09-21).
        """
        if not isinstance(agent, str) or not agent or agent != agent.strip():
            return False
        folded = agent.casefold()
        if folded in {name.casefold() for name in self.deny_list}:
            return False
        return folded in {name.casefold() for name in self.allow_list}

    def is_classifiable_record(self, rec: dict[str, Any]) -> bool:
        """Whole-record check. Prefer this wherever a record is available.

        Resolves an opaque name from the working directory, then refuses a
        session working under a denied path whatever it reports.
        """
        from .identity import resolve_agent, under_denied_path

        if under_denied_path(rec.get("cwd"), self.deny_paths):
            return False
        return self.is_classifiable(resolve_agent(rec))

    def read_api_key(self) -> str:
        """Read the key at call time. Never stored on the instance.

        The value is validated before it is used: a key carrying whitespace or
        a control character makes the HTTP layer raise a ValueError whose text
        contains the key, and nothing upstream catches that, so the key ends up
        in a traceback (review, 2026-09-21). Error messages here never echo the
        value.
        """
        if self.api_key_file is not None:
            try:
                key = self.api_key_file.read_text().strip()
            except OSError as exc:
                raise ConfigError(f"cannot read api_key_file: {exc}") from None
            if not key:
                raise ConfigError("api_key_file is empty")
            source = "api_key_file"
        else:
            key = os.environ.get(API_KEY_ENV, "").strip()
            if not key:
                raise ConfigError(
                    f"no API key: set {API_KEY_ENV} or provider.api_key_file")
            source = API_KEY_ENV
        if not _HEADER_SAFE.fullmatch(key):
            raise ConfigError(
                f"the key from {source} contains characters that cannot go in "
                "an HTTP header (whitespace, a newline or a control "
                "character). The value is not shown.")
        return key

    def __repr__(self) -> str:  # never let a key reach a traceback or a log
        return (f"Config(allow={sorted(self.allow_list)}, "
                f"deny={sorted(self.deny_list)}, status_dir={self.status_dir}, "
                f"fresh_seconds={self.fresh_seconds}, model={self.model})")


def _expand(value: str) -> pathlib.Path:
    return pathlib.Path(value).expanduser()


def _check_names(section: str, key: str, values: Any) -> frozenset[str]:
    """Agent names must be plain, non-empty strings equal to their stripped form."""
    cleaned = []
    for value in values:
        if not isinstance(value, str):
            raise ConfigError(
                f"[{section}].{key} entries must be strings, got "
                f"{type(value).__name__}")
        if not value or value != value.strip():
            raise ConfigError(
                f"[{section}].{key} entry {value!r} must be non-empty and carry "
                "no surrounding whitespace")
        cleaned.append(value)
    return frozenset(cleaned)


def _check_schema(data: dict[str, Any]) -> None:
    unknown_sections = set(data) - set(_SCHEMA)
    if unknown_sections:
        raise ConfigError(f"unknown section(s): {sorted(unknown_sections)}")
    for section, body in data.items():
        if not isinstance(body, dict):
            raise ConfigError(f"section {section!r} must be a table")
        unknown_keys = set(body) - set(_SCHEMA[section])
        if unknown_keys:
            raise ConfigError(
                f"unknown key(s) in [{section}]: {sorted(unknown_keys)}")
        for key, value in body.items():
            expected = _SCHEMA[section][key]
            if not isinstance(value, expected) or isinstance(value, bool):
                raise ConfigError(
                    f"[{section}].{key} must be {expected.__name__}, "
                    f"got {type(value).__name__}")


def load_config(path: str | pathlib.Path | None) -> Config:
    """Load a config file, or return safe defaults when none is given."""
    config = Config()
    if path is None:
        return config

    source = pathlib.Path(path)
    if not source.is_file():
        raise ConfigError(f"config file not found: {source}")
    try:
        data = tomllib.loads(source.read_text())
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"cannot parse config: {exc}") from None

    _check_schema(data)
    agents = data.get("agents", {})
    status = data.get("status", {})
    guard = data.get("guard", {})
    provider = data.get("provider", {})
    log = data.get("log", {})

    pattern = guard.get("institution_identifier", config.institution_identifier)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ConfigError(f"guard.institution_identifier is not a regex: {exc}") from None

    return replace(
        config,
        allow_list=_check_names("agents", "allow", agents.get("allow", [])),
        deny_list=_check_names("agents", "deny", agents.get("deny", [])),
        deny_paths=tuple(agents.get("deny_paths", ())),
        status_dir=_expand(status["dir"]) if "dir" in status else config.status_dir,
        fresh_seconds=status.get("fresh_seconds", config.fresh_seconds),
        private_path_prefixes=tuple(guard.get("private_path_prefixes", ())),
        institution_identifier=pattern,
        api_key_file=_expand(provider["api_key_file"]) if "api_key_file" in provider else None,
        model=provider.get("model", config.model),
        log_dir=_expand(log["dir"]) if "dir" in log else config.log_dir,
    )
