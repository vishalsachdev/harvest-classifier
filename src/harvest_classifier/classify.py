"""One status record -> one shadow row. The only place the pieces meet.

Order is fixed and each step can stop the next one:
allow-list -> trust rule -> guard -> deterministic checks -> model -> policy.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from . import deterministic, guard, questions, status
from .config import Config
from .policy import recommend


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def classify(
    rec: dict[str, Any],
    send: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    config: Config | None = None,
) -> dict[str, Any] | None:
    """Return a row, or None when the record is not classifiable at all.

    `send` takes the message text and returns the provider's flat answers. It
    is injected so the whole suite runs offline; None means deterministic-only.
    """
    config = config or Config()
    agent = rec.get("agent")
    if not config.is_classifiable(agent):
        return None
    if not status.is_trustworthy(rec, config.fresh_seconds) or not status.has_message(rec):
        return None

    text = rec.get("last_assistant_text") or ""
    row: dict[str, Any] = {
        "agent": agent,
        "session_id": rec.get("session_id"),
        "updated_at": rec.get("updated_at"),
        "observed_at": _now(),
        "state": rec.get("state"),
        "event": rec.get("event"),
        "text_fingerprint": status.fingerprint(rec),
        "text_length": len(text),
        "guard_blocked": False,
        "guard_categories": [],
    }

    categories = guard.scan_text(text, config)
    flags = deterministic.check(text, rec.get("state"), rec.get("blocked_reason"))
    row["deterministic"] = flags

    if categories:
        # Fail closed: nothing is sent, and only the category names are kept.
        row["guard_blocked"] = True
        row["guard_categories"] = categories
        settled = deterministic.settles_without_a_model(flags)
        row.update({
            "recommendation": settled or "escalate_to_owner",
            "decided_by": "guard_blocked",
            "why": "guard blocked the send; deterministic flags only",
            "thresholds_provisional": True,
        })
        return row

    settled = deterministic.settles_without_a_model(flags)
    answers: dict[str, Any] | None = None
    if settled is None and send is not None:
        started = time.perf_counter()
        answers = send(questions.question_set() | {"__state__": text})
        row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        row["jev_answers"] = {k: v for k, v in answers.items()
                              if not k.startswith("_") and k != "probabilities"}
        row["jev_model"] = answers.get("_model")
        row["input_tokens"] = answers.get("_input_tokens")
        row["usd"] = answers.get("_usd")
        row["attempts"] = answers.get("_attempts")

    row.update(recommend(flags, answers))
    return row
