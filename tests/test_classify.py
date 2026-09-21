import json
import pathlib
from datetime import datetime, timezone

from harvest_classifier.classify import classify
from harvest_classifier.config import Config

CONFIG = Config(allow_list=frozenset({"build-agent", "docs-agent"}),
                deny_list=frozenset({"orchestrator"}))

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = [json.loads(l) for l in
            (ROOT / "fixtures/final_messages_v1.jsonl").read_text().splitlines() if l.strip()]


def rec(text, agent="build-agent", state="idle", **over):
    base = {"agent": agent, "session_id": "s1", "state": state, "event": "Stop",
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "last_assistant_text": text, "blocked_reason": None}
    base.update(over)
    return base


def fake_send(answers):
    def send(_payload):
        return dict(answers)
    return send


DEFAULT = {"claims_done": 0.2, "done_is_evidenced": 0.2, "asks_a_question": 0.1,
           "needs_owner_decision": 0.1, "reports_failure": 0.1, "deferred_work": 0.1,
           "next_action": "ignore", "next_action_confidence": 0.8,
           "_model": "jev-1.13.0", "_input_tokens": 500, "_usd": 0.00002, "_attempts": 1}


def test_denied_agent_returns_no_row_at_all():
    assert classify(rec("done", agent="teaching-session"), fake_send(DEFAULT), CONFIG) is None
    assert classify(rec("done", agent="orchestrator"), fake_send(DEFAULT), CONFIG) is None


def test_unknown_agent_returns_no_row():
    assert classify(rec("done", agent="mystery"), fake_send(DEFAULT), CONFIG) is None


def test_stale_record_returns_no_row():
    assert classify(rec("done", updated_at="2020-01-01T00:00:00+00:00"),
                    fake_send(DEFAULT)) is None


def test_empty_message_returns_no_row():
    assert classify(rec("   "), fake_send(DEFAULT), CONFIG) is None


def test_row_never_contains_the_message_text():
    row = classify(rec("a very distinctive sentence"), fake_send(DEFAULT), CONFIG)
    assert "a very distinctive sentence" not in json.dumps(row)
    assert row["text_length"] == len("a very distinctive sentence")


def test_guard_hit_blocks_the_send_entirely():
    sent = []

    def spy(payload):
        sent.append(payload)
        return dict(DEFAULT)

    row = classify(rec("Dana scored 88 on the midterm"), spy, CONFIG)
    assert row["guard_blocked"] is True
    assert sent == [], "guard must prevent the call, not just label it"
    assert "grade_near_name" in row["guard_categories"]


def test_guard_row_records_categories_only():
    row = classify(rec("mail me at a@b.com"), fake_send(DEFAULT), CONFIG)
    assert row["guard_categories"] == ["email"]
    assert "a@b.com" not in json.dumps(row)


def test_deterministic_settlement_skips_the_model():
    sent = []

    def spy(payload):
        sent.append(payload)
        return dict(DEFAULT)

    row = classify(rec("Claude needs your permission to continue"), spy, CONFIG)
    assert row["recommendation"] == "unblock"
    assert row["decided_by"] == "deterministic"
    assert sent == []


def test_model_is_called_only_for_the_remainder():
    sent = []

    def spy(payload):
        sent.append(payload)
        return dict(DEFAULT, next_action="ignore")

    classify(rec("Reading the spec, a third of the way through."), spy, CONFIG)
    assert len(sent) == 1


def test_offline_with_no_sender_still_produces_a_row():
    row = classify(rec("Reading the spec."), None, CONFIG)
    assert row["recommendation"] == "ignore"
    assert row["decided_by"] == "default"


def test_embedded_instruction_cannot_produce_a_harvest():
    for f in FIXTURES:
        if "embedded_instruction" not in f["tags"]:
            continue
        row = classify(rec(f["text"]),
                       fake_send(dict(DEFAULT, next_action="harvest",
                                      claims_done=0.95, done_is_evidenced=0.05)),
                       CONFIG)
        assert row["recommendation"] != "harvest", f["id"]
        assert row["deterministic"]["contains_embedded_instruction"] is True


def test_every_fixture_produces_a_valid_row():
    for f in FIXTURES:
        row = classify(rec(f["text"]), fake_send(DEFAULT), CONFIG)
        assert row is not None and row["recommendation"] in {
            "harvest", "unblock", "bounce_for_evidence", "escalate_to_owner", "ignore"}
