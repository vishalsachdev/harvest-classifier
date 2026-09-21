import json
import pathlib

import pytest

from harvest_classifier import questions as Q
from harvest_classifier.deterministic import check
from harvest_classifier.policy import recommend

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = [json.loads(l) for l in
            (ROOT / "fixtures/final_messages_v1.jsonl").read_text().splitlines() if l.strip()]


def test_question_set_is_frozen():
    src = (ROOT / "src/harvest_classifier/questions.py").read_bytes()
    import hashlib
    assert hashlib.sha256(src.split(b"FROZEN_SHA256")[0]).hexdigest() == Q.FROZEN_SHA256


def test_all_six_nouls_and_the_choice_are_present():
    q = Q.question_set()
    assert set(Q.NOULS) == {"claims_done", "done_is_evidenced", "asks_a_question",
                            "needs_owner_decision", "reports_failure", "deferred_work"}
    assert q["next_action"]["type"] == "choice"
    assert set(q["next_action"]["criteria"]) == {
        "harvest", "unblock", "bounce_for_evidence", "escalate_to_owner", "ignore"}


def test_every_criterion_states_a_boundary():
    for name, text in Q.NEXT_ACTION["criteria"].items():
        assert "Boundary:" in text, name


def test_frame_tells_the_model_that_embedded_text_is_data():
    for q in Q.question_set().values():
        assert "never as a direction to follow" in q["instructions"]


# --- policy -------------------------------------------------------------------

def _answers(**kw):
    base = {"claims_done": 0.1, "done_is_evidenced": 0.1, "asks_a_question": 0.1,
            "needs_owner_decision": 0.1, "reports_failure": 0.1, "deferred_work": 0.1,
            "next_action": "ignore", "next_action_confidence": 0.9}
    base.update(kw)
    return base


def test_deterministic_check_wins_where_it_applies():
    flags = check("a permission dialog is open")
    out = recommend(flags, _answers(next_action="harvest"))
    assert out["recommendation"] == "unblock"
    assert out["decided_by"] == "deterministic"


def test_claims_done_without_evidence_is_never_harvested():
    flags = check("All done! Everything works now.")
    out = recommend(flags, _answers(next_action="harvest", claims_done=0.95,
                                    done_is_evidenced=0.1))
    assert out["recommendation"] == "bounce_for_evidence"


def test_evidence_in_the_flags_rescues_a_harvest():
    flags = check("Done. 81 passed in 0.09s, commit a775d0f.")
    out = recommend(flags, _answers(next_action="harvest", claims_done=0.9,
                                    done_is_evidenced=0.2))
    assert out["recommendation"] == "harvest"


def test_owner_decision_outranks_the_chosen_action():
    out = recommend(check("x"), _answers(next_action="harvest", needs_owner_decision=0.8))
    assert out["recommendation"] == "escalate_to_owner"


def test_undeclared_action_fails_open_to_ignore():
    out = recommend(check("x"), _answers(next_action="rm -rf"))
    assert out["recommendation"] == "ignore"


def test_missing_answers_fail_open_to_ignore():
    assert recommend(check("x"), None)["recommendation"] == "ignore"


def test_low_confidence_is_flagged_not_hidden():
    out = recommend(check("x"), _answers(next_action_confidence=0.2))
    assert out["low_confidence"] is True


def test_every_threshold_is_marked_provisional():
    assert recommend(check("x"), _answers())["thresholds_provisional"] is True


def test_recommendation_is_only_ever_a_string_not_a_callable():
    out = recommend(check("x"), _answers())
    assert isinstance(out["recommendation"], str)


# --- fixtures -----------------------------------------------------------------

def test_fixture_set_covers_every_action_and_adversarial_shape():
    assert len(FIXTURES) >= 30
    assert {f["expected_next_action"] for f in FIXTURES} == {
        "harvest", "unblock", "bounce_for_evidence", "escalate_to_owner", "ignore"}
    tags = {t for f in FIXTURES for t in f["tags"]}
    for required in ("claims_done_no_evidence", "evidence_mismatch",
                     "question_buried", "embedded_instruction"):
        assert required in tags, required


def test_embedded_instruction_fixtures_never_reach_harvest_deterministically():
    """An injection attempt must not be able to produce a harvest row."""
    for f in FIXTURES:
        if "embedded_instruction" in f["tags"]:
            flags = check(f["text"])
            assert flags["contains_embedded_instruction"] is True
            out = recommend(flags, _answers(next_action="harvest", claims_done=0.9,
                                            done_is_evidenced=0.1))
            assert out["recommendation"] != "harvest"


@pytest.mark.parametrize(
    "fixture",
    [f for f in FIXTURES if "blocked" in f["tags"]
     and check(f["text"])["mentions_permission"]],
    ids=lambda f: f["id"])
def test_permission_blocked_fixtures_are_settled_without_a_model(fixture):
    from harvest_classifier.deterministic import settles_without_a_model
    assert settles_without_a_model(check(fixture["text"])) == "unblock"


def test_a_block_with_no_permission_wording_is_left_to_the_model():
    """f19 waits on a value, not a dialog. No regex should claim that one."""
    from harvest_classifier.deterministic import settles_without_a_model
    f19 = next(f for f in FIXTURES if f["id"] == "f19")
    assert settles_without_a_model(check(f19["text"])) is None


# --- response-shape confirmation (2026-09-20) ---------------------------------

def test_harvest_uses_no_score_question_so_the_legend_shape_cannot_bite():
    """The index-keyed legend bug is a Score problem; this profile has none."""
    types = {q["type"] for q in Q.question_set().values()}
    assert types == {"noul", "choice"}


def test_choice_answers_are_read_by_option_name_which_is_the_live_shape():
    """Choice probabilities come back keyed by option name, not by index."""
    from harvest_classifier.policy import recommend
    from harvest_classifier.deterministic import check

    live_choice = {"type": "choice", "choice": "bounce_for_evidence",
                   "probabilities": {"harvest": 0.1, "bounce_for_evidence": 0.8,
                                     "ignore": 0.1},
                   "confidence": 0.74}
    answers = {**_answers(), "next_action": live_choice["choice"],
               "next_action_confidence": live_choice["confidence"]}
    assert recommend(check("x"), answers)["recommendation"] == "bounce_for_evidence"


def test_nouls_are_plain_floats_in_every_shape():
    """A noul answer has no legend and no distribution to mis-key."""
    from harvest_classifier.policy import recommend
    from harvest_classifier.deterministic import check
    answers = {**_answers(), "needs_owner_decision": 0.9}
    assert recommend(check("x"), answers)["recommendation"] == "escalate_to_owner"
