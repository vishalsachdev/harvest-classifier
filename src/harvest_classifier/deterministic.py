"""Deterministic checks, run before any model call.

Every question a regex can settle is settled here. The model is only asked
about the remainder, which is the whole point of the design: if code can
settle it, code settles it.

Message text is **data**, never instruction. `contains_embedded_instruction`
exists so an injection attempt is visible in the row, and it changes no other
flag and no recommendation.
"""
from __future__ import annotations

import re
from typing import Any

#: A hash needs at least one a-f letter as well as a digit. Without the letter
#: requirement any 7 to 40 digit number counts, so "ticket 20260921" reads as
#: evidence of a commit. Found in review, 2026-09-21.
_COMMIT = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?=[0-9a-f]{7,40}(?![A-Za-z0-9]))"
    r"(?=[a-f0-9]*\d)(?=[a-f0-9]*[a-f])"
    r"[0-9a-f]{7,40}")
_PASSING = re.compile(r"\b(\d+)\s+(passed|tests?\s+pass(ed|ing)?)\b|\ball green\b"
                      r"|\bRan\s+\d+\s+tests?\b", re.IGNORECASE)
_FAILING = re.compile(r"\b(\d+)\s+(failed|failures?|errors?)\b|\bFAILED\b|\bAssertionError\b"
                      r"|\btraceback\b", re.IGNORECASE)
_QUESTION_END = re.compile(r"\?\s*$")
_QUESTION_ANY = re.compile(r"\?")
_NEEDS_OWNER = re.compile(
    r"\b(needs?\s+(the\s+)?owner|needs?\s+you\b|open\s+for\s+(the\s+)?owner"
    r"|your\s+call|waiting\s+on\s+you|for\s+you\s+to\s+decide)\b", re.IGNORECASE)
_PERMISSION = re.compile(r"\b(permission\s+(dialog|prompt|request)"
                         r"|needs?\s+your\s+permission|approval\s+prompt"
                         r"|awaiting\s+approval)\b", re.IGNORECASE)
#: `^[ \t]*` rather than `(^|\n)\s*`: the latter backtracks quadratically on
#: a long run of newlines (48 s on 80k of them in review).
_COMMAND_OUTPUT = re.compile(r"^[ \t]*[$>#][ \t]+\S|```", re.MULTILINE)
_EMBEDDED_INSTRUCTION = re.compile(
    r"\b(ignore (your|all|previous) (rules|instructions)"
    r"|disregard (the|your|all) (above|rules|instructions)"
    r"|you must now|new instructions?:|system prompt|mark this (as )?done"
    r"|override your)\b", re.IGNORECASE)
_DEFERRED = re.compile(r"\b(deferred|left (out|undone)|did not (do|finish)|not yet"
                       r"|still (open|to do|outstanding)|TODO|follow[- ]up needed)\b",
                       re.IGNORECASE)


def check(text: str | None, state: str | None = None,
          blocked_reason: str | None = None) -> dict[str, bool]:
    body = text or ""
    return {
        "has_commit": bool(_COMMIT.search(body)),
        "has_passing_tests": bool(_PASSING.search(body)),
        "has_failing_tests": bool(_FAILING.search(body)),
        "ends_with_question": bool(_QUESTION_END.search(body.strip())),
        "contains_question": bool(_QUESTION_ANY.search(body)),
        "needs_owner": bool(_NEEDS_OWNER.search(body)),
        "mentions_permission": bool(_PERMISSION.search(body)),
        "is_blocked": state == "blocked" and bool(blocked_reason),
        "has_command_output": bool(_COMMAND_OUTPUT.search(body)),
        "contains_embedded_instruction": bool(_EMBEDDED_INSTRUCTION.search(body)),
        "mentions_deferred_work": bool(_DEFERRED.search(body)),
    }


def settles_without_a_model(flags: dict[str, bool]) -> str | None:
    """A recommendation a regex can justify on its own, else None.

    Kept deliberately narrow: only cases where the text states the answer
    outright. Everything else is the model's remainder.
    """
    # Only the RECORD settles this. A permission mention in prose used to
    # settle `unblock` on its own, so a session whose record said
    # `state: working, blocked_reason: None` was recommended for unblocking
    # because its final message happened to discuss permission dialogs
    # (found by labelling a real row, 2026-09-23).
    #
    # The mention stays in the flags and reaches the model, which is where a
    # judgement about prose belongs: a denied permission request fires no
    # event at all, so the text is sometimes the only signal there is.
    if flags["is_blocked"]:
        return "unblock"
    if flags["needs_owner"]:
        return "escalate_to_owner"
    if flags["has_failing_tests"]:
        return "bounce_for_evidence"
    return None
