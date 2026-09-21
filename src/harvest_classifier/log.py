"""Append-only shadow log. Owner-only, never message text.

The location is configuration; the default is `./shadow-log`.
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Any, Iterator

#: Owner-only. Rows are derived from other people's sessions.
DIR_MODE = 0o700
FILE_MODE = 0o600


def ensure_dir(path: pathlib.Path) -> pathlib.Path:
    """Create the directory owner-only, but never re-chmod an existing one.

    `--log-dir .` used to set the working directory to 700 (review, 2026-09-21).
    """
    directory = pathlib.Path(path)
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, DIR_MODE)
    return directory


#: Anything not on this list is dropped before writing, so a future caller
#: cannot widen the row into carrying message text by accident.
ROW_FIELDS = frozenset({
    "agent", "session_id", "updated_at", "observed_at", "state", "event",
    "text_fingerprint", "text_length", "deterministic", "guard_blocked",
    "guard_categories", "jev_answers", "jev_model", "input_tokens", "usd",
    "latency_ms", "attempts", "recommendation", "decided_by", "why",
    "low_confidence", "thresholds_provisional", "schema",
})

#: Answer keys that may be logged, and what they may hold. The provider's
#: response is server-chosen data: before this, an arbitrary key carrying
#: arbitrary text was written straight into the log (review, 2026-09-21).
from .policy import ACTIONS  # noqa: E402  (small, and avoids a duplicate list)
from .questions import NOULS  # noqa: E402

_NUMERIC_ANSWERS = frozenset(NOULS)
_CHOICE_ANSWERS = {"next_action": frozenset(ACTIONS)}


def _clean_answers(answers: Any) -> dict[str, Any]:
    if not isinstance(answers, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in answers.items():
        if key in _NUMERIC_ANSWERS and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            out[key] = float(value)
        elif key in _CHOICE_ANSWERS and value in _CHOICE_ANSWERS[key]:
            out[key] = value
        elif key.endswith("_confidence") and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            out[key] = float(value)
    return out


def sanitise(row: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in row.items() if k in ROW_FIELDS}
    if "jev_answers" in clean:
        clean["jev_answers"] = _clean_answers(clean["jev_answers"])
    if "why" in clean:
        clean["why"] = str(clean["why"])[:200]
    clean["schema"] = 1
    return clean


def append(row: dict[str, Any], path: pathlib.Path) -> dict[str, Any]:
    target = pathlib.Path(path)
    ensure_dir(target.parent)
    clean = sanitise(row)
    # Created 0600 by os.open rather than under the umask and chmodded after
    # the first write, which left a window where it was world readable.
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(clean, sort_keys=True) + "\n")
    return clean


def read(path: pathlib.Path) -> list[dict[str, Any]]:
    target = pathlib.Path(path)
    if not target.exists():
        return []
    rows = []
    for line in target.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def seen_keys(path: pathlib.Path) -> set[tuple[str, str]]:
    """Mirror of `status.dedupe_key`; the two must not drift."""
    return {(str(r.get("session_id") or ""), str(r.get("updated_at") or ""))
            for r in read(path)}


def iter_rows(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    yield from read(path)
