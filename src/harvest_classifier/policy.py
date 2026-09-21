"""Pure policy: deterministic flags plus model answers -> a recommendation row.

Output is a **recommendation**, never an action. Nothing in this package can
prompt an agent or key a pane, and this function returns a string.

Every threshold is provisional: they are first guesses, not numbers calibrated
against what an operator actually did. `shadow-report` exists to replace
them.
"""
from __future__ import annotations

from typing import Any

DONE_CLAIMED = 0.6
EVIDENCE_FLOOR = 0.5
QUESTION_FLOOR = 0.6
OWNER_DECISION_FLOOR = 0.6
FAILURE_FLOOR = 0.6
CONFIDENCE_FLOOR = 0.5

ACTIONS = ("harvest", "unblock", "bounce_for_evidence", "escalate_to_owner", "ignore")


def recommend(flags: dict[str, bool], answers: dict[str, Any] | None) -> dict[str, Any]:
    """Deterministic checks win where they apply; the model fills the rest."""
    from .deterministic import settles_without_a_model

    settled = settles_without_a_model(flags)
    if settled is not None:
        return {"recommendation": settled, "decided_by": "deterministic",
                "why": f"deterministic flags settled it: {settled}",
                "thresholds_provisional": True}

    if not answers:
        return {"recommendation": "ignore", "decided_by": "default",
                "why": "no model answer available; failing open to no action",
                "thresholds_provisional": True}

    action = answers.get("next_action")
    confidence = answers.get("next_action_confidence") or 0.0
    why = "model verdict"

    # A message carrying an injection attempt is never harvested, whatever the
    # model chose and whatever evidence it also shows. Before this cap, an
    # injection plus any long number reached `harvest` (review, 2026-09-21).
    if flags.get("contains_embedded_instruction") and action == "harvest":
        action = "bounce_for_evidence"
        why = "harvest proposed on a message containing an embedded instruction"
        return {"recommendation": action, "decided_by": "policy",
                "why": why, "low_confidence": confidence < CONFIDENCE_FLOOR,
                "thresholds_provisional": True}

    # A claimed-done message with no evidence must never be harvested, whatever
    # the model chose: this is the failure that costs the coordinator most.
    if (action == "harvest"
            and answers.get("claims_done", 0.0) >= DONE_CLAIMED
            and answers.get("done_is_evidenced", 0.0) < EVIDENCE_FLOOR
            and not (flags["has_commit"] or flags["has_passing_tests"])):
        action, why = "bounce_for_evidence", "claims done, no evidence in message or flags"
    elif answers.get("needs_owner_decision", 0.0) >= OWNER_DECISION_FLOOR:
        action, why = "escalate_to_owner", "model reports an owner decision is needed"
    elif action == "harvest" and answers.get("reports_failure", 0.0) >= FAILURE_FLOOR:
        action, why = "bounce_for_evidence", "harvest proposed but a failure is reported"

    if action not in ACTIONS:
        return {"recommendation": "ignore", "decided_by": "model",
                "why": f"undeclared action {action!r}; failing open",
                "thresholds_provisional": True}

    low = confidence < CONFIDENCE_FLOOR
    return {
        "recommendation": action,
        "decided_by": "model",
        "why": why + (" (low confidence)" if low else ""),
        "low_confidence": low,
        "thresholds_provisional": True,
    }
