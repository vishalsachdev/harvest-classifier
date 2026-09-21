#!/usr/bin/env python3
"""EXAMPLE reference producer for harvest-classifier status files.

This is an example, not a supported part of the package. It shows one way to
produce the status files the classifier reads, using Claude Code's command
hooks. Read it, adapt it, and test it against your own setup before relying on
it. The contract it implements is documented in docs/STATUS-FILE.md.

Wire it up by pointing the SessionStart, UserPromptSubmit, Stop, Notification
and SessionEnd hooks at this script. It reads the hook payload as JSON on
stdin.

Design constraints, learned the hard way:

  * A status hook that wedges a session costs more than it can ever save, so
    every failure path exits 0 in silence, there is a time budget, and there is
    no network call and no child process.
  * `os._exit(0)` skips interpreter shutdown, which discards buffered stdout.
    Stdout from a SessionStart or UserPromptSubmit hook is injected into the
    session's context, and a status writer has no business doing that.
  * The Notification payload field is `notification_type`. Some published
    documentation calls it `type`; the real payload has no `type` key at all.
    Both are accepted below.
  * A permission request that the user DENIES fires no event, so a session can
    sit blocked without the file saying so. Freshness is what bounds that.
  * Payloads carrying `agent_id` or `agent_type` come from subagents. They are
    ignored, because a finished subagent is not an idle session and its answer
    would otherwise overwrite the parent's.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import signal
import sys
import tempfile
from datetime import datetime, timezone

STATUS_DIR = pathlib.Path(
    os.environ.get("HARVEST_STATUS_DIR",
                   pathlib.Path.home() / ".claude" / "state" / "fleet"))

#: Well below a hook budget. If anything takes longer, give up silently.
TIME_BUDGET_SECONDS = 1

DIR_MODE = 0o700
FILE_MODE = 0o600

EVENT_STATE = {
    "SessionStart": "idle",
    "UserPromptSubmit": "working",
    "Stop": "idle",
    "SessionEnd": "ended",
}

BLOCKING_NOTIFICATIONS = {
    "permission_prompt", "agent_needs_input",
    "elicitation_request", "elicitation_prompt",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


#: A file name is built from this, so it must not be able to leave the status
#: directory. "../../x" as a session id used to write outside it.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(raw: str) -> str:
    """A single path component, never empty, never `.` or `..`."""
    cleaned = _SAFE_NAME.sub("-", str(raw)).strip("-.")[:64]
    return cleaned or "unknown"


def _agent_name(payload: dict) -> str:
    """Whatever identifies this session. Adapt to your own environment."""
    for key in ("HARVEST_AGENT", "TERM_SESSION_ID"):
        value = os.environ.get(key)
        if value:
            return _safe_name(value)
    return _safe_name(payload.get("session_id", "unknown"))


def _is_subagent(payload: dict) -> bool:
    """Subagent turns are not session state.

    Checked for a non-empty value rather than mere presence: a top-level
    session whose payload carries `agent_type: null` is not a subagent, and
    treating it as one meant it never wrote a file at all.
    """
    return bool(payload.get("agent_id")) or bool(payload.get("agent_type"))


def _previous(path: pathlib.Path) -> dict:
    """The last record, or an empty one. A corrupt or non-dict file used to
    raise on every call, so that session could never update again."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _next_record(event: str, payload: dict, previous: dict) -> dict:
    """The record to write, given the event and what was there before."""
    session_id = payload.get("session_id")
    carried_text = previous.get("last_assistant_text")

    # A message belongs to one session and one turn. Carrying it into a new
    # session, or past a SessionStart, let yesterday's message pass freshness
    # under a new id and be classified again.
    if event == "SessionStart" or previous.get("session_id") != session_id:
        carried_text = None

    record = {
        "schema_version": 1,
        "agent": _agent_name(payload),
        "session_id": session_id,
        "cwd": payload.get("cwd"),
        "event": event,
        "state": previous.get("state", "idle") if previous.get("session_id") == session_id else "idle",
        "updated_at": _now(),
        "turn_started_at": previous.get("turn_started_at"),
        "last_assistant_text": carried_text,
        "blocked_reason": previous.get("blocked_reason"),
    }

    if event in EVENT_STATE:
        record["state"] = EVENT_STATE[event]
        record["blocked_reason"] = None
    if event == "UserPromptSubmit":
        record["turn_started_at"] = record["updated_at"]
        record["last_assistant_text"] = None
    if event == "Stop":
        # The Stop payload carries the final message directly in the normal
        # case; parsing the transcript is only a fallback worth adding if your
        # setup needs it.
        text = payload.get("last_assistant_message")
        record["last_assistant_text"] = text if isinstance(text, str) else None
    if event == "Notification":
        # The field is `notification_type`; `type` is accepted as an alias
        # because some published documentation names it that way.
        kind = payload.get("notification_type") or payload.get("type")
        if kind in BLOCKING_NOTIFICATIONS:
            record["state"] = "blocked"
            record["blocked_reason"] = kind
    return record


def _write_atomically(path: pathlib.Path, record: dict) -> None:
    # Only chmod a directory this hook creates. Pointing HARVEST_STATUS_DIR at
    # an existing directory used to change that directory's mode.
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, DIR_MODE)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(record, out)
        os.chmod(temp, FILE_MODE)
        os.replace(temp, path)          # atomic: a reader sees old or new
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def main() -> None:
    signal.signal(signal.SIGALRM, lambda *_: os._exit(0))
    signal.alarm(TIME_BUDGET_SECONDS)

    payload = json.loads(sys.stdin.read() or "{}")
    if not isinstance(payload, dict) or _is_subagent(payload):
        os._exit(0)

    event = payload.get("hook_event_name", "")
    path = STATUS_DIR / f"{_agent_name(payload)}.json"
    previous = _previous(path)

    if event == "Notification":
        kind = payload.get("notification_type") or payload.get("type")
        if kind not in BLOCKING_NOTIFICATIONS:
            os._exit(0)      # an ordinary notification is not a state change

    _write_atomically(path, _next_record(event, payload, previous))
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # Never let a status writer break the session it is watching.
        os._exit(0)
