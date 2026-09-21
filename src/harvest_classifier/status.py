"""Read fleet status files and apply the trust rule.

The trust rule: a status file is authoritative only while it is **fresh**,
measured from the file's own `updated_at` rather than its mtime, and while the
session it names has not ended. Freshness comes from `updated_at` because an
mtime survives a copy or a restore that never touched the session.

**A check this package cannot make.** The producer writes one file per terminal
pane. If a pane is recycled under a new session, the file on disk may describe
a session that no longer occupies it. Confirming occupancy needs the terminal
multiplexer, which this package deliberately does not talk to. The consequence
is bounded: a recycled pane writes a new `session_id`, rows are keyed on
`session_id` plus `updated_at`, and freshness caps how stale any reading can
be. For a classifier that never acts, a misattributed row costs data quality
rather than safety. `occupancy_check` is an injection point for a caller that
can supply the answer.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat
from datetime import datetime, timezone
from typing import Any, Callable, Iterator


#: Tolerated clock skew between the producer and this reader.
MAX_CLOCK_SKEW_SECONDS = 60

#: A status file larger than this is not a status file.
MAX_STATUS_BYTES = 1_000_000


class StatusDirError(RuntimeError):
    pass


def iter_status_files(directory: pathlib.Path) -> Iterator[pathlib.Path]:
    """Regular, owner-only, reasonably sized `.json` files, and nothing else.

    The directory holds other sessions' final messages, so it is checked rather
    than trusted: a FIFO named `x.json` blocks the reader forever, a symlink
    can point anywhere, and an unbounded file exhausts memory (review,
    2026-09-21).
    """
    directory = pathlib.Path(directory)
    if not directory.is_dir():
        return
    info = os.lstat(directory)
    if info.st_uid != os.getuid():
        raise StatusDirError(
            f"{directory} is not owned by this user; refusing to read it")
    if stat.S_IMODE(info.st_mode) & 0o022:
        # Writability is the reader's concern: anyone who can write here can
        # plant a status file and choose what gets sent. Readability is the
        # producer's concern, and the contract asks it for 700.
        raise StatusDirError(
            f"{directory} has mode {stat.S_IMODE(info.st_mode):04o} and is "
            "writable by others; anyone who can write here can choose what "
            "this classifier sends")

    for path in sorted(directory.iterdir()):
        if path.suffix != ".json":
            continue
        try:
            entry = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(entry.st_mode):
            continue            # FIFOs, sockets, symlinks, directories
        if entry.st_size > MAX_STATUS_BYTES:
            continue
        yield path


def read_status(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def age_seconds(rec: dict[str, Any]) -> float | None:
    """Seconds since `updated_at`, or None if it is unusable.

    A naive stamp returns None rather than being assumed UTC: the contract
    requires an offset, and guessing turns a timezone mistake into a silently
    wrong age (review, 2026-09-21).
    """
    raw = rec.get("updated_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        return None
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def is_trustworthy(
    rec: dict[str, Any] | None,
    fresh: int = 900,
    occupancy_check: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    if not rec:
        return False
    if not isinstance(rec.get("agent", ""), str):
        return False
    state = rec.get("state")
    if state is not None and not isinstance(state, str):
        return False
    if state == "ended":
        return False
    age = age_seconds(rec)
    # A stamp in the future used to stay fresh forever. Allow only ordinary
    # clock skew (review, 2026-09-21).
    if age is None or age > fresh or age < -MAX_CLOCK_SKEW_SECONDS:
        return False
    if occupancy_check is not None and not occupancy_check(rec):
        return False
    return True


def has_message(rec: dict[str, Any] | None) -> bool:
    # The field is explicitly null on a `working` record, not merely absent,
    # and a malformed producer may write a non-string.
    text = (rec or {}).get("last_assistant_text")
    return isinstance(text, str) and bool(text.strip())


def fingerprint(rec: dict[str, Any] | None) -> str:
    """Identify a message without storing it: sha256 of the text, truncated."""
    text = (rec or {}).get("last_assistant_text") or ""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def dedupe_key(rec: dict[str, Any]) -> tuple[str, str]:
    """Must agree with `log.seen_keys` exactly, including on a null id.

    When the two disagreed, a record with no session id was never recognised
    as seen, so `--watch` re-sent it on every poll (review, 2026-09-21).
    """
    return (str(rec.get("session_id") or ""), str(rec.get("updated_at") or ""))
